"""routers/ratehawk_web.py — web endpoints du bouton « Réserver » RateHawk.

- GET  /api/ratehawk/hotel/{hid}/rates            : dispo LIVE (chambres + prix reels)
- POST /api/ratehawk/hotel/{hid}/payment-intent   : Stripe PI (capture MANUELLE) +
                                                    bookings_v2 payment_pending
- finalize_ratehawk_booking(...)                  : appele par le webhook Stripe a
                                                    l'autorisation -> book RateHawk ->
                                                    capture si OK / annule si echec.

ARGENT (doctrine Pascal) :
- Prix calcule SERVEUR (net RateHawk + marge) — jamais de montant venu du front.
- Capture MANUELLE : la carte est AUTORISEE, debitee UNIQUEMENT si la resa RateHawk
  aboutit ; sinon annulation (0 frais). Anti double-debit / anti-leurre.
- GATED : tant que Stripe est en test (sk_test) et le depot RateHawk non finance, le
  bouton « Payer » n'est pas expose au public.
- Donnees 100% reelles : tarifs de l'API RateHawk, aucun prix invente.
"""
from __future__ import annotations
import json
import logging
import time
import uuid

import stripe
from fastapi import APIRouter
from pydantic import BaseModel

from providers.ratehawk.client import RateHawkClient, RateHawkError

log = logging.getLogger("ratehawk_web")
router = APIRouter()

MARGIN_PCT = 0.15  # marge AirBizness sur le net RateHawk (provisoire, a parametrer)


# ───────────────────────── Dispo live ─────────────────────────
@router.get("/api/ratehawk/hotel/{hid}/rates")
def ratehawk_rates(hid: int, checkin: str, checkout: str, adults: int = 2,
                   currency: str = "EUR", residency: str = "gb"):
    """Disponibilite LIVE d'un hotel RateHawk (chambres + tarifs reels)."""
    try:
        c = RateHawkClient()
        hp = c.search_hotel_page(int(hid), check_in=checkin, check_out=checkout,
                                 adults=adults, currency=currency, residency=residency)
        rates = (((hp.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [])
        rooms = []
        for r in rates[:24]:
            pt = ((r.get("payment_options") or {}).get("payment_types") or [{}])[0]
            net = float(pt.get("amount") or 0)
            rooms.append({
                "room_name": r.get("room_name"),
                "meal": r.get("meal"),
                "amount": pt.get("amount"),
                "sell_amount": round(net * (1 + MARGIN_PCT), 2),
                "currency": pt.get("currency_code") or currency,
                "book_hash": r.get("book_hash"),
            })
        return {"ok": True, "hid": hid, "checkin": checkin, "checkout": checkout,
                "adults": adults, "rooms": rooms}
    except RateHawkError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        log.warning(f"[ratehawk_web] rates hid={hid} failed: {e}")
        return {"ok": False, "error": "unavailable"}


# ───────────────────────── Payment intent ─────────────────────────
class RhPIReq(BaseModel):
    book_hash: str
    checkin: str
    checkout: str
    adults: int = 2
    first_name: str
    last_name: str
    email: str
    phone: str = ""
    residency: str = "gb"


@router.post("/api/ratehawk/hotel/{hid}/payment-intent")
def ratehawk_payment_intent(hid: int, body: RhPIReq):
    """Verrouille le tarif (prebook), calcule le prix de vente SERVEUR, cree un
    Stripe PaymentIntent en CAPTURE MANUELLE, INSERT bookings_v2 payment_pending."""
    from main import DB_CONFIG
    import psycopg2
    try:
        c = RateHawkClient()
        pre = c.prebook(body.book_hash)
        prate = (((pre.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [{}])[0]
        p_hash = prate.get("book_hash")
        pt = ((prate.get("payment_options") or {}).get("payment_types") or [{}])[0]
        net = float(pt.get("amount") or 0)
        currency = (pt.get("currency_code") or "EUR")
        if not p_hash or net <= 0:
            return {"ok": False, "error": "rate_unavailable"}
    except RateHawkError as e:
        return {"ok": False, "error": str(e)}

    gross = round(net * (1 + MARGIN_PCT), 2)
    ab_ref = f"AB-RH-{uuid.uuid4().hex[:10].upper()}"

    try:
        intent = stripe.PaymentIntent.create(
            amount=int(round(gross * 100)),
            currency=currency.lower(),
            capture_method="manual",
            # Carte sans redirection off-page : le booking exige un cycle auth→capture
            # synchrone côté serveur ; on évite les moyens à redirection.
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            metadata={
                "airbizness_ref": ab_ref,
                "type": "ratehawk_hotel",
                "ratehawk_hid": str(hid),
                "user_email": body.email,
            },
            receipt_email=body.email or None,
            description=f"AirBizness · RateHawk hotel {hid} · {body.checkin}->{body.checkout}",
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"stripe: {e}"}

    rh_raw = {
        "provider": "ratehawk", "hid": hid, "book_hash": p_hash,
        "net": net, "gross": gross, "currency": currency,
        "guest": {"first_name": body.first_name, "last_name": body.last_name,
                  "email": body.email, "phone": body.phone},
        "checkin": body.checkin, "checkout": body.checkout, "adults": body.adults,
    }
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bookings_v2 (
                  airbizness_ref, status, user_email, user_phone,
                  holder_name, holder_surname, hotel_code, hotel_name,
                  check_in, check_out, adults, rooms_count,
                  net_price, gross_price, currency,
                  rate_key_verified, payment_intent_id, payment_status,
                  remark, hbx_booking_raw
                ) VALUES (
                  %s,'payment_pending',%s,%s, %s,%s,%s,%s, %s,%s,%s,1,
                  %s,%s,%s, %s,%s,'pending', %s,%s::jsonb
                )
            """, (
                ab_ref, body.email, body.phone,
                body.first_name, body.last_name, int(hid), f"RateHawk {hid}",
                body.checkin, body.checkout, body.adults,
                net, gross, currency,
                p_hash, intent.id, "reservation RateHawk (mode test)",
                json.dumps(rh_raw),
            ))
    except Exception as e:  # noqa: BLE001
        log.warning(f"[ratehawk_web] INSERT bookings_v2 fail: {e}")

    return {"ok": True, "client_secret": intent.client_secret,
            "airbizness_ref": ab_ref, "amount": gross, "currency": currency}


# ───────── Finalisation (appelee par le webhook Stripe a l'autorisation) ─────────
def _poll(c: RateHawkClient, oid: str, max_iter: int = 40) -> str:
    for _ in range(max_iter):
        try:
            st = c.booking_status(oid)
        except RateHawkError as e:
            m = str(e).lower()
            if "book_limit" in m: return "book_limit"
            if "soldout" in m: return "soldout"
            time.sleep(5); continue
        top = st.get("status"); pc = (st.get("data") or {}).get("percent")
        if top == "ok" and (pc or 0) >= 100: return "ok"
        if top == "error": return "error"
        time.sleep(5)
    return "timeout"


def finalize_ratehawk_booking(airbizness_ref: str) -> dict:
    """Webhook Stripe (autorisation OK) -> reserve chez RateHawk -> capture le
    paiement si OK, annule si echec. Idempotent."""
    from main import DB_CONFIG
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT status, payment_intent_id, hbx_booking_raw
                           FROM bookings_v2 WHERE airbizness_ref=%s""", (airbizness_ref,))
            row = cur.fetchone()
            if not row:
                return {"ok": False, "error": "ref_introuvable"}
            status, pi_id, raw = row
            if status in ("confirmed", "booking_failed"):
                return {"ok": True, "idempotent": status}
            rh = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            if rh.get("provider") != "ratehawk":
                return {"ok": False, "error": "not_ratehawk"}

            c = RateHawkClient()
            guest = rh.get("guest") or {}
            oid = airbizness_ref
            c.booking_form(partner_order_id=oid, book_hash=rh["book_hash"], user_ip="0.0.0.0")
            c.booking_finish(
                partner_order_id=oid,
                rooms=[{"guests": [{"first_name": guest.get("first_name", "Guest"),
                                    "last_name": guest.get("last_name", "Traveler")}]}],
                user={"email": guest.get("email", ""), "comment": "", "phone": guest.get("phone", "")},
                supplier_data={"first_name_original": guest.get("first_name", "Guest"),
                               "last_name_original": guest.get("last_name", "Traveler"),
                               "phone": guest.get("phone", ""), "email": guest.get("email", "")},
                payment_type={"type": "deposit", "amount": str(rh.get("net")),
                              "currency_code": rh.get("currency", "EUR")})
            final = _poll(c, oid)

            if final == "ok":
                order_id = None
                for _ in range(6):  # l'ordre met quelques secondes à être lisible
                    try:
                        info = c.get_order(oid)
                        orders = (info.get("data") or {}).get("orders", [])
                        if orders:
                            order_id = orders[0].get("order_id"); break
                    except RateHawkError:
                        pass
                    time.sleep(4)
                try:
                    stripe.PaymentIntent.capture(pi_id)
                except Exception as e:  # noqa: BLE001
                    log.error(f"[ratehawk] capture KO ref={airbizness_ref}: {e}")
                # hbx_reference = colonne « réf de réservation provider » (réutilisée
                # pour l'order_id RateHawk, comme HBX y met sa propre référence).
                cur.execute("""UPDATE bookings_v2 SET status='confirmed',
                               payment_status='succeeded', hbx_reference=%s WHERE airbizness_ref=%s""",
                            (str(order_id) if order_id else None, airbizness_ref))
                return {"ok": True, "order_id": order_id}
            else:
                try:
                    stripe.PaymentIntent.cancel(pi_id)
                except Exception as e:  # noqa: BLE001
                    log.error(f"[ratehawk] cancel KO ref={airbizness_ref}: {e}")
                cur.execute("""UPDATE bookings_v2 SET status='booking_failed',
                               payment_status='cancelled' WHERE airbizness_ref=%s""", (airbizness_ref,))
                return {"ok": False, "error": f"booking_{final}"}
    except Exception as e:  # noqa: BLE001
        log.error(f"[ratehawk] finalize KO ref={airbizness_ref}: {e}")
        return {"ok": False, "error": str(e)}
