"""Module conciergerie — alertes, validation, actions manuelles conciergerie. Migré depuis main.py le 2026-06-02 (10e module migré)."""

import json
import os
import re
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response
from pydantic import BaseModel, EmailStr
from main import (limiter, DB_CONFIG, _alert_telegram)

router = APIRouter()

# ─── HANDLERS APPENDED BY SCRIPT BELOW ───


_BOOKING_REF_RE_MAIN = re.compile(r"\b([A-Z]{2,5}-[A-Z0-9-]{4,20})\b", re.IGNORECASE)


def _exit_to_agent_1(session_id: str) -> dict:
    """Sortie propre vers l'accueil : clôt le pattern actif + reset session AGENT_1.

    Doctrine anti-cul-de-sac : l'utilisateur peut toujours quitter un flux d'auth
    ou de pattern (siège, refund, etc.) en demandant l'accueil / annuler.
    """
    try:
        from resilience._credentials import get_active_pattern_state, update_pattern_state
        ps = get_active_pattern_state(session_id)
        if ps:
            update_pattern_state(ps["id"], current_level="REFUSED", _by="user_exit")
    except Exception:
        pass
    try:
        from resilience.concierge_session import update_session
        update_session(
            session_id, state="AGENT_1", pending_intent=None,
            authenticated_booking_ref=None, authenticated_user_email=None,
            authenticated_user_id=None,
        )
    except Exception:
        pass
    try:
        _alert_telegram(f"↩️ ChatBot retour accueil (user exit) session={session_id[:12]}…")
    except Exception:
        pass
    msg = ("Pas de souci, je vous ramène à l'accueil. Je peux vous renseigner sur nos vols, "
           "hôtels, activités et destinations. Que puis-je faire pour vous ?")
    return {
        "response": msg, "answer": msg,
        "session_id": session_id, "agent": "1", "state": "AGENT_1",
        "mode": "exit_to_agent_1", "escalate_human": False,
    }


def _credchain_validated_html(pattern_label_fr: str, booking_ref: str,
                                 success: bool, detail: str = "") -> str:
    color = "#22c55e" if success else "#ef4444"
    title = "Action exécutée" if success else "Action refusée"
    return f"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AirBizness — {title}</title>
<style>
body{{margin:0;background:#0a0a0a;color:#f0ece4;font-family:'DM Sans',Arial,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}}
.card{{max-width:520px;background:#141414;border:1px solid rgba(255,255,255,0.07);border-radius:14px;padding:36px 32px;text-align:center;}}
h1{{font-family:Georgia,serif;font-size:24px;color:{color};margin-bottom:14px;}}
.ref{{font-family:monospace;font-size:13px;color:#a09890;letter-spacing:0.5px;margin:10px 0 24px;}}
.detail{{font-size:13px;color:#a09890;line-height:1.6;background:#1c1c1c;padding:14px 16px;border-radius:8px;margin-top:18px;}}
.back{{display:inline-block;margin-top:24px;padding:10px 20px;background:#b8962e;color:#000;text-decoration:none;border-radius:6px;font-weight:600;font-size:13px;}}
</style></head><body><div class="card">
<h1>{title}</h1>
<p>{pattern_label_fr}</p>
<div class="ref">Dossier : <b>{booking_ref or '—'}</b></div>
<div class="detail">{detail}</div>
<a href="https://airbizness.com/" class="back">Retour airbizness.com</a>
</div></body></html>"""


class ConciergerieUpdateRequest(BaseModel):
    status: str  # 'in_progress' | 'resolved'
    resolution_note: str = ""



@router.post("/concierge/ask")
async def concierge_ask(req: Request):
    """Endpoint concierge AirBizness — délègue à le concierge (cerveau multi-tenant).

    Body JSON: {"user_ctx": {...}, "message": "...", "history": [...]}

    Court-circuit @claude / @superviseur (2026-05-25, mode C Pascal) :
      Si le message mentionne @claude, on ne réveille PAS concierge_core.
      On notifie l'endpoint /api/supervisor/notify (loggé bruyamment pour
      que Claude en session terminal voie la mention via tail -F), et on
      retourne une réponse "Claude est notifié, il va répondre". Le client
      JS poll ensuite /api/supervisor/pending pour récupérer la réponse.
    """
    # le concierge (Agent 1) RETIRÉ DÉFINITIVEMENT (Pascal 2026-07-22). Le chatbot concierge
    # public est supprimé (UI + cerveau). Endpoint neutralisé : ne réveille plus aucun agent.
    raise HTTPException(status_code=410, detail="concierge_retire")

    try:  # noqa: F841 — code mort conservé sous le 410 (retrait le concierge)
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    user_message = body.get('message') or ''
    user_ctx = body.get('user_ctx') or {}

    # ── Court-circuit @claude ──────────────────────────────────
    msg_lower = (user_message or '').lower()
    if '@claude' in msg_lower or '@superviseur' in msg_lower:
        try:
            import os as _os
            import urllib.request as _ur
            import json as _json
            session_id = user_ctx.get('session_id') or user_ctx.get('user_id') or 'pascal_default'
            payload = _json.dumps({
                "session_id": session_id,
                "user_message": user_message,
                "context": {"user_ctx": user_ctx},
            }).encode("utf-8")
            base = _os.getenv("CONCIERGE_API_BASE", "http://127.0.0.1:3000")
            req2 = _ur.Request(
                f"{base}/api/supervisor/notify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _ur.urlopen(req2, timeout=3).read()
        except Exception as _e:
            # Pas critique. Pascal : watchdog inutile ici.
            import logging as _l
            _l.getLogger("concierge").warning(f"[supervisor.notify] fail: {_e}")
        return {
            "response": "🤖 Claude est notifié. Il va répondre dans quelques instants…",
            "answer": "🤖 Claude est notifié. Il va répondre dans quelques instants…",
            "waiting_supervisor": True,
            "brain_slug": "claude-superviseur",
            "trace": {
                "brain_slug": "claude-superviseur",
                "llm_provider": "Claude Superviseur (binôme Pascal)",
                "source": "supervisor_shortcut",
                "intent": {"kind": "mention_claude", "urgency": "normal"},
                "verif": {"valid": True, "reason": "Mention détectée — concierge_core court-circuité"},
                "path": ["concierge_ask", "detect_mention", "supervisor.notify"],
                "llm_calls": 0,
            },
        }

    # ── State machine multi-agents (Agent 1 / Agent 2) ─────────
    # Doctrine Pascal 2026-05-27 : Agent 1 = chatbot public DB-only,
    # Agent 2 = compta/résa avec accès données client (authentifié).
    # Bascule via parse_and_verify_auth (booking_ref + email match).
    from resilience.concierge_session import (
        ask_concierge_agent,
        is_sensitive_request,
        is_exit_intent,
        parse_and_verify_auth,
        get_or_create_session,
        update_session,
        HANDOVER_PROMPT,
    )
    from resilience.account_agent import ask_account_agent
    import uuid as _uuid

    session_id = user_ctx.get("session_id") or body.get("session_id")
    if not session_id:
        session_id = f"sess_{_uuid.uuid4().hex[:24]}"
    history = body.get("history") or []

    try:
        session = get_or_create_session(session_id)
    except Exception as _e_sess:
        # Fail-soft : si la table n'est pas accessible, on tombe sur Agent 1 sans state
        import logging as _l
        _l.getLogger("concierge").error(f"[session] get_or_create KO: {_e_sess}")
        result = ask_concierge_agent(user_ctx=user_ctx, message=user_message, history=history)
        result["session_id"] = session_id
        result["agent"] = "1"
        return result

    state = (session.get("state") or "AGENT_1").upper()

    # ── État AGENT_1 ───────────────────────────────────────────
    if state == "AGENT_1":
        # GARDE-FOU CRITIQUE : si une action sensible est DÉJÀ en cours (pattern non
        # terminal) mais que la session est retombée en AGENT_1, l'Agent 1/LLM ne doit
        # JAMAIS répondre librement — sinon il fabrique une fausse confirmation
        # (cf. bug seat-change : « changement confirmé » alors que rien n'a été exécuté).
        # On reprend proprement le flux via ré-authentification → credchain.
        try:
            from resilience._credentials import get_active_pattern_state as _gaps
            if _gaps(session_id):
                update_session(
                    session_id, state="WAITING_AUTH",
                    pending_intent=(session.get("pending_intent") or user_message)[:1000],
                )
                try:
                    _alert_telegram(
                        f"🛡️ GARDE-FOU : action sensible en cours mais session retombée "
                        f"en AGENT_1 → reprise via auth. session={session_id[:12]}…"
                    )
                except Exception:
                    pass
                return {
                    "response": HANDOVER_PROMPT, "answer": HANDOVER_PROMPT,
                    "session_id": session_id, "agent": "1", "state": "WAITING_AUTH",
                    "mode": "resume_pending_action", "escalate_human": False,
                }
        except Exception:
            pass
        if is_sensitive_request(user_message):
            # L1 — qualification pattern + génération credential signé HMAC
            try:
                from resilience.concierge_session import qualify_pattern_l1
                qualify_pattern_l1(
                    session_id=session_id,
                    message=user_message,
                    user_id=user_ctx.get("user_id"),
                )
            except Exception as _e_l1:
                import logging as _l
                _l.getLogger("concierge").warning(f"[credchain L1] KO: {_e_l1}")
            update_session(
                session_id,
                state="WAITING_AUTH",
                pending_intent=user_message[:1000],
            )
            try:
                _alert_telegram(
                    f"🔀 ChatBot HANDOVER vers Agent 2 demandé session={session_id[:12]}… "
                    f"intent={user_message[:80]}"
                )
            except Exception:
                pass
            return {
                "response": HANDOVER_PROMPT,
                "answer": HANDOVER_PROMPT,
                "session_id": session_id,
                "agent": "1",
                "state": "WAITING_AUTH",
                "mode": "handover_request",
                "escalate_human": False,
            }
        else:
            result = ask_concierge_agent(user_ctx=user_ctx, message=user_message, history=history)
            result["session_id"] = session_id
            result["agent"] = "1"
            result["state"] = "AGENT_1"
            return result

    # ── État WAITING_AUTH ──────────────────────────────────────
    if state == "WAITING_AUTH":
        # Échappatoire : l'utilisateur veut revenir à l'accueil (jamais de cul-de-sac).
        # On ne déclenche pas si le message contient une référence de réservation.
        if is_exit_intent(user_message) and not _BOOKING_REF_RE_MAIN.search(user_message):
            return _exit_to_agent_1(session_id)
        auth_result = parse_and_verify_auth(user_message)
        if auth_result.get("success"):
            # Bascule Agent 2 avec le pending_intent
            session = update_session(
                session_id,
                state="AUTHENTICATED",
                authenticated_booking_ref=auth_result["booking_ref"],
                authenticated_user_email=auth_result["email"],
                authenticated_user_id=auth_result["user_id"],
            )
            session["session_id"] = session_id
            pending_intent = session.get("pending_intent") or user_message
            try:
                _alert_telegram(
                    f"✅ ChatBot AUTH OK session={session_id[:12]}… "
                    f"booking={auth_result['booking_ref']} email={auth_result['email']} "
                    f"→ Agent 2"
                )
            except Exception:
                pass
            # Si pattern_state L1 active → bascule en credchain L1→L2
            try:
                from resilience._credentials import get_active_pattern_state
                from resilience.account_agent import advance_pattern_state
                pstate = get_active_pattern_state(session_id)
                if pstate:
                    result = advance_pattern_state(
                        session=session, pattern_state=pstate,
                        user_message=pending_intent, history=history,
                    )
                    result["session_id"] = session_id
                    result["state"] = "AUTHENTICATED"
                    return result
            except Exception as _e_cc:
                import logging as _l
                _l.getLogger("concierge").warning(f"[credchain post-auth] KO: {_e_cc}")
            # Fallback : Agent 2 classique
            result = ask_account_agent(session=session, message=pending_intent, history=history)
            result["session_id"] = session_id
            result["state"] = "AUTHENTICATED"
            return result
        elif auth_result.get("partial"):
            return {
                "response": auth_result["message"],
                "answer": auth_result["message"],
                "session_id": session_id,
                "agent": "1",
                "state": "WAITING_AUTH",
                "mode": "auth_partial",
                "escalate_human": False,
            }
        else:
            # Mismatch / inconnu → retour AGENT_1
            update_session(
                session_id,
                state="AGENT_1",
                pending_intent=None,
                authenticated_booking_ref=None,
                authenticated_user_email=None,
                authenticated_user_id=None,
            )
            return {
                "response": auth_result["error_message"],
                "answer": auth_result["error_message"],
                "session_id": session_id,
                "agent": "1",
                "state": "AGENT_1",
                "mode": "auth_failed",
                "escalate_human": False,
            }

    # ── État AUTHENTICATED ─────────────────────────────────────
    if state == "AUTHENTICATED":
        # Échappatoire depuis n'importe quel niveau pattern (L2/L3/L4) → retour accueil.
        if is_exit_intent(user_message):
            return _exit_to_agent_1(session_id)
        session["session_id"] = session_id
        # Si pattern_state active → state machine credchain
        try:
            from resilience._credentials import get_active_pattern_state
            from resilience.account_agent import advance_pattern_state
            pstate = get_active_pattern_state(session_id)
            if pstate:
                result = advance_pattern_state(
                    session=session, pattern_state=pstate,
                    user_message=user_message, history=history,
                )
                result["session_id"] = session_id
                result["state"] = "AUTHENTICATED"
                return result
            # Pas de pattern actif mais sensitive_request → relancer L1
            from resilience.concierge_session import is_sensitive_request, qualify_pattern_l1
            if is_sensitive_request(user_message):
                qualify_pattern_l1(
                    session_id=session_id,
                    message=user_message,
                    user_id=session.get("authenticated_user_id"),
                )
                pstate2 = get_active_pattern_state(session_id)
                if pstate2:
                    result = advance_pattern_state(
                        session=session, pattern_state=pstate2,
                        user_message=user_message, history=history,
                    )
                    result["session_id"] = session_id
                    result["state"] = "AUTHENTICATED"
                    return result
        except Exception as _e_cc:
            import logging as _l
            _l.getLogger("concierge").warning(f"[credchain authenticated] KO: {_e_cc}")
        # Fallback : Agent 2 classique
        result = ask_account_agent(session=session, message=user_message, history=history)
        result["session_id"] = session_id
        result["state"] = "AUTHENTICATED"
        return result

    # ── Fallback (état inconnu) ────────────────────────────────
    update_session(session_id, state="AGENT_1")
    result = ask_concierge_agent(user_ctx=user_ctx, message=user_message, history=history)
    result["session_id"] = session_id
    result["agent"] = "1"
    result["state"] = "AGENT_1"
    return result


@router.get("/concierge/validate-action", response_class=HTMLResponse)
def concierge_validate_action(token: str = "", action: str = "confirm"):
    """Endpoint cliqué depuis l'email Brevo de validation.

    Verifie le validation_token + génère L4 credential signé + déclenche exécution.
    """
    if not token:
        return HTMLResponse(
            _credchain_validated_html("Validation", "—", False,
                                          "Lien invalide (token manquant)."),
            status_code=400,
        )
    try:
        from resilience._credentials import (
            find_state_by_validation_token, sign_credential,
            update_pattern_state, get_pattern_spec,
        )
        from resilience.account_agent import _execute_pattern_now
    except Exception as e:
        _alert_telegram(f"🔴 validate-action import KO : {e}")
        return HTMLResponse(
            _credchain_validated_html("Validation", "—", False,
                                          f"Erreur technique : {e}"),
            status_code=500,
        )

    state_row = find_state_by_validation_token(token)
    if not state_row:
        return HTMLResponse(
            _credchain_validated_html("Validation", "—", False,
                                          "Lien invalide ou expiré (token introuvable)."),
            status_code=404,
        )

    pattern_name = state_row["pattern_name"]
    spec = get_pattern_spec(pattern_name) or {}
    label = spec.get("label_fr", pattern_name)
    booking_ref = state_row.get("booking_ref")

    # Expiration check
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    exp = state_row.get("expires_at")
    if exp and exp < now:
        update_pattern_state(state_row["id"], current_level="FAILED",
                              execution_result={"reason": "expired"},
                              _by="email_click_expired")
        try:
            _alert_telegram(
                f"⏰ validate-action expiré pattern={pattern_name} ref={booking_ref}"
            )
        except Exception:
            pass
        return HTMLResponse(
            _credchain_validated_html(label, booking_ref or "—", False,
                                          "Le lien a expiré (24h dépassées)."),
            status_code=410,
        )

    if state_row["current_level"] in ("EXECUTED", "FAILED", "REFUSED"):
        return HTMLResponse(
            _credchain_validated_html(
                label, booking_ref or "—",
                state_row["current_level"] == "EXECUTED",
                f"Déjà traité (statut : {state_row['current_level']}).",
            ),
            status_code=200,
        )

    # Action = refuse → on enregistre et stop
    if action == "refuse":
        update_pattern_state(state_row["id"], current_level="REFUSED",
                              _by="email_click_refuse")
        try:
            _alert_telegram(
                f"❌ validate-action REFUSED pattern={pattern_name} ref={booking_ref}"
            )
        except Exception:
            pass
        return HTMLResponse(
            _credchain_validated_html(label, booking_ref or "—", False,
                                          "Vous avez refusé. Aucune action exécutée."),
            status_code=200,
        )

    # Action = confirm → générer L4 + exécuter
    session_id = state_row["session_id"]
    l4_payload = {
        "session_id": session_id,
        "pattern_name": pattern_name,
        "validation_token": token,
        "via": "email_click",
    }
    l4_token = sign_credential("L4", l4_payload)
    update_pattern_state(state_row["id"], current_level="L4",
                          l4_credential=l4_token,
                          _by="email_click")
    try:
        _alert_telegram(
            f"📨 L3→L4 email cliqué pattern={pattern_name} ref={booking_ref}"
        )
    except Exception:
        pass

    # Charge la session pour l'executor
    try:
        from resilience.concierge_session import get_or_create_session
        session = get_or_create_session(session_id)
    except Exception as e:
        return HTMLResponse(
            _credchain_validated_html(label, booking_ref or "—", False,
                                          f"Erreur session : {e}"),
            status_code=500,
        )

    payload = state_row.get("pattern_payload") or {}
    if isinstance(payload, str):
        try:
            import json as _json
            payload = _json.loads(payload)
        except Exception:
            payload = {}

    exec_result = _execute_pattern_now(state_row["id"], pattern_name,
                                         payload, session_id, session)
    success = exec_result.get("level") == "EXECUTED"
    detail_html = exec_result.get("response", "")[:400]
    return HTMLResponse(
        _credchain_validated_html(label, booking_ref or "—", success, detail_html),
        status_code=200,
    )


@router.get("/conciergerie/alerts")
def conciergerie_list_alerts(status: str = "open", limit: int = 100,
                              include_sandbox: bool = False):
    """Liste les alertes conciergerie. Filtre par status (open/in_progress/resolved/all)."""
    where = []
    params = []
    if status != "all":
        where.append("status = %s"); params.append(status)
    if not include_sandbox:
        where.append("is_sandbox = FALSE")
    where_clause = " AND ".join(where) if where else "1=1"

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT id, airbizness_ref, severity, alert_type, payload, status,
               resolution_note, created_at, resolved_at, is_sandbox
        FROM conciergerie_alerts
        WHERE {where_clause}
        ORDER BY
            CASE severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
            created_at DESC
        LIMIT %s
    """, params + [limit])
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["resolved_at"] = d["resolved_at"].isoformat() if d.get("resolved_at") else None
        rows.append(d)
    cur.close(); conn.close()

    # Stats agrégées
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT severity, status, COUNT(*) FROM conciergerie_alerts
        WHERE is_sandbox = FALSE OR %s = TRUE
        GROUP BY severity, status
    """, (include_sandbox,))
    stats = {}
    for sev, st, n in cur.fetchall():
        stats.setdefault(sev, {})[st] = n
    cur.close(); conn.close()

    return {"alerts": rows, "stats": stats, "total": len(rows)}


@router.post("/conciergerie/alerts/{alert_id}/update")
@limiter.limit("60/minute")
def conciergerie_update_alert(request: Request, alert_id: int,
                               body: ConciergerieUpdateRequest):
    """Met à jour le status d'une alerte conciergerie."""
    if body.status not in ("open", "in_progress", "resolved"):
        raise HTTPException(400, "status invalide")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        if body.status == "resolved":
            cur.execute("""
                UPDATE conciergerie_alerts
                SET status='resolved', resolution_note=%s, resolved_at=NOW()
                WHERE id=%s
            """, (body.resolution_note, alert_id))
        else:
            cur.execute("""
                UPDATE conciergerie_alerts SET status=%s, resolution_note=%s
                WHERE id=%s
            """, (body.status, body.resolution_note, alert_id))
        conn.commit(); cur.close(); conn.close()
        return {"id": alert_id, "status": body.status}
    except Exception as e:
        raise HTTPException(500, str(e))
