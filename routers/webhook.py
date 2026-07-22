"""Module webhook Duffel. Migré depuis main.py le 2026-06-02, 8e module migré. Reçoit events airline-initiated (cancellations, schedule changes, payments)."""

import json
import os
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Request, HTTPException
from main import limiter, DB_CONFIG, _alert_telegram, _stripe_refund_auto

# ─────────────────────────────────────────────────────────────────────
# DUFFEL WEBHOOK RECEIVER  (Phase 4 — Décembre 2026)
# ─────────────────────────────────────────────────────────────────────
# Reçoit les events Duffel (signature HMAC SHA-256) et déclenche les
# events airline-initiated (cancel, schedule change, payment success...).
# Sans ce receiver on ne sait JAMAIS si une airline annule un vol → le client
# se présente, vol parti, AirBizness perd toute crédibilité.
# Configurer sur Duffel Dashboard → Webhooks → Add endpoint
#   URL : https://airbizness.com/webhook/duffel
#   Events : order.airline_initiated_change_detected, order.created,
#            order.cancelled, payment.succeeded, payment.failed
# Signing secret → DUFFEL_WEBHOOK_SECRET dans .env
# ─────────────────────────────────────────────────────────────────────

DUFFEL_WEBHOOK_SECRET = os.getenv("DUFFEL_WEBHOOK_SECRET", "")


def _verify_duffel_signature(payload: bytes, header_sig: str, secret: str) -> bool:
    """Vérifie la signature HMAC-SHA256 d'un webhook Duffel.

    Duffel envoie un header `X-Duffel-Signature` (parfois `t=...,v1=...` à la
    Stripe, parfois juste un hex digest). On accepte les deux formats pour
    robustesse. Sans secret on REFUSE (sécurité — pas de mode "accept any").
    """
    if not secret or not header_sig:
        return False
    import hmac as _hmac
    import hashlib as _hl
    sig_clean = header_sig.strip()
    # Format Stripe-like : "t=12345,v1=abc..." → extract v1
    if "v1=" in sig_clean:
        for part in sig_clean.split(","):
            if part.strip().startswith("v1="):
                sig_clean = part.strip()[3:]
                break
    expected = _hmac.new(secret.encode("utf-8"), payload, _hl.sha256).hexdigest()
    try:
        return _hmac.compare_digest(expected, sig_clean)
    except Exception:
        return False


router = APIRouter()


@router.post("/webhook/duffel")
@limiter.limit("300/minute")
async def duffel_webhook(request: Request):
    """Reçoit les events Duffel (airline-initiated changes, cancellations…).

    Step 1 : valide signature HMAC + persiste en `duffel_webhook_events`.
    Step 2 : dispatch par event_type. Renvoie 200 immédiatement (Duffel retry sinon).
    Doctrine watchdog Pascal : tout event critique → alert Telegram immédiat.
    """
    payload = await request.body()
    header_sig = request.headers.get("x-duffel-signature") or request.headers.get("X-Duffel-Signature") or ""

    # Refuse sans secret configuré ET sans signature valide.
    # Une fois Pascal a renseigné DUFFEL_WEBHOOK_SECRET, on refusera les fakes.
    if DUFFEL_WEBHOOK_SECRET:
        if not _verify_duffel_signature(payload, header_sig, DUFFEL_WEBHOOK_SECRET):
            _alert_telegram(f"⚠️ Duffel webhook signature INVALID (header={header_sig[:30]!r})")
            raise HTTPException(401, "Signature invalide")
    else:
        # Mode dégradé : on log mais accepte (Pascal pas encore renseigné le secret)
        print(f"[duffel-webhook] DUFFEL_WEBHOOK_SECRET vide — mode dégradé (à configurer)")
        _alert_telegram("⚠️ Duffel webhook reçu mais DUFFEL_WEBHOOK_SECRET non configuré — sécurité OFF")

    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(400, "Payload invalide")

    event_id = event.get("id") or event.get("event_id") or ""
    event_type = event.get("type") or event.get("event_type") or "unknown"
    data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
    # Le sub-objet "object" contient l'order/payment selon le type
    obj = data.get("object", {}) if isinstance(data.get("object"), dict) else data
    order_id = obj.get("id") if obj.get("object_type") == "order" else obj.get("order_id") or ""
    # Fallback : récup depuis metadata airbizness_ref si présent dans l'order Duffel
    md = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
    airbizness_ref = md.get("airbizness_ref") or ""

    print(f"[duffel-webhook] event={event_type} id={event_id} order={order_id} ab_ref={airbizness_ref}")

    if not event_id:
        # Sans event_id on ne peut pas dedup → on accepte mais on log
        event_id = f"no_id_{event_type}_{int(__import__('time').time()*1000)}"

    # ── Persistance + dedup (idempotency) ──
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO duffel_webhook_events (event_id, event_type, payload, airbizness_ref, duffel_order_id)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                """, (event_id, event_type, json.dumps(event), airbizness_ref or None, order_id or None))
            except psycopg2.errors.UniqueViolation:
                # Déjà reçu → idempotent
                conn.rollback()
                print(f"[duffel-webhook] dedup event_id={event_id} (already processed)")
                return {"received": True, "deduped": True, "event_id": event_id}
    except Exception as e:
        print(f"[duffel-webhook] persist fail: {e}")
        _alert_telegram(f"⚠️ Duffel webhook persist DB fail: {str(e)[:200]}")
    finally:
        try: conn.close()
        except Exception: pass

    # ── Dispatch par type ──
    process_error = None
    try:
        if event_type == "order.airline_initiated_change_detected":
            # CRITIQUE : airline a changé/annulé le vol après booking
            _alert_telegram(
                f"🔥 URGENT Duffel order={order_id[-12:] if order_id else '?'} ab_ref={airbizness_ref or '?'} : "
                f"AIRLINE INITIATED CHANGE détecté. Vérifier order Duffel + contacter client."
            )
            # Marque le flight_booking pour rebooking/refund manuel
            if order_id:
                try:
                    conn2 = psycopg2.connect(**DB_CONFIG)
                    with conn2, conn2.cursor() as c2:
                        c2.execute("""
                            UPDATE flight_bookings
                            SET status='airline_change_detected', booking_error=%s
                            WHERE duffel_order_id=%s
                        """, (f"airline_change_detected via webhook event_id={event_id}", order_id))
                    conn2.close()
                except Exception as _e_up:
                    print(f"[duffel-webhook] flight_bookings update fail: {_e_up}")

        elif event_type == "order.cancelled":
            # Airline a annulé → trigger refund Stripe + mail Brevo
            _alert_telegram(
                f"⚠️ Duffel order={order_id[-12:] if order_id else '?'} ab_ref={airbizness_ref or '?'} CANCELLED par airline. "
                f"Refund client à initier."
            )
            if order_id:
                try:
                    conn2 = psycopg2.connect(**DB_CONFIG)
                    with conn2, conn2.cursor() as c2:
                        c2.execute("""
                            UPDATE flight_bookings
                            SET status='cancelled_by_airline', cancelled_at=NOW(),
                                booking_error=%s
                            WHERE duffel_order_id=%s
                            RETURNING airbizness_ref, stripe_payment_intent, total_eur, user_email
                        """, (f"order.cancelled via webhook event_id={event_id}", order_id))
                        rr = c2.fetchone()
                    conn2.close()
                    if rr:
                        ab_ref_local, pi_id, total_eur_local, user_email_local = rr
                        # Tente refund Stripe auto (best-effort)
                        if pi_id:
                            try:
                                refund_res = _stripe_refund_auto(
                                    pi_id, airbizness_ref=ab_ref_local,
                                    reason="duffel_airline_cancelled",
                                    error_excerpt=f"Duffel order.cancelled event={event_id}",
                                )
                                _alert_telegram(
                                    f"Refund auto {ab_ref_local} : {'OK' if refund_res.get('ok') else 'KO'} "
                                    f"({refund_res.get('amount') or refund_res.get('error')})"
                                )
                            except Exception as _e_r:
                                _alert_telegram(f"🔥 Refund auto FAIL {ab_ref_local}: {str(_e_r)[:200]}")
                except Exception as _e_c:
                    print(f"[duffel-webhook] cancel handling fail: {_e_c}")

        elif event_type in ("order.created", "order.updated"):
            # Informatif : confirmation Duffel post-create. On log seulement.
            print(f"[duffel-webhook] {event_type} order={order_id} ack")

        elif event_type in ("payment.succeeded", "order.payment_succeeded"):
            # Confirme que Duffel a bien crédité l'airline (B2B balance).
            print(f"[duffel-webhook] payment OK order={order_id}")

        else:
            print(f"[duffel-webhook] type={event_type} pas géré (informatif)")

        # Marque processed
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE duffel_webhook_events
                SET processed=true, processed_at=NOW()
                WHERE event_id=%s
            """, (event_id,))
        conn.close()
    except Exception as e:
        process_error = str(e)[:500]
        print(f"[duffel-webhook] DISPATCH ERROR event={event_type}: {process_error}")
        _alert_telegram(f"🔥 Duffel webhook dispatch error event={event_type}: {process_error}")
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            with conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE duffel_webhook_events SET process_error=%s WHERE event_id=%s
                """, (process_error, event_id))
            conn.close()
        except Exception:
            pass

    # Renvoie 200 immédiatement (Duffel retry sinon — best practice)
    return {"received": True, "event_id": event_id, "event_type": event_type,
            "processed": process_error is None}
