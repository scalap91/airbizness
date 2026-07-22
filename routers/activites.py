"""
Module des activités HBX.
Migré depuis main.py le 2026-06-02, 7e module migré.
Routes pour la recherche, le paiement, la confirmation et la consultation des activités.
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
import uuid as _uuid
import json
import psycopg2
import psycopg2.extras
import stripe
from main import limiter, DB_CONFIG, STRIPE_CAPTURE_MANUAL

router = APIRouter()


class ActivityPaymentIntentRequest(BaseModel):
    rate_key: str
    gross_price: float
    currency: str = "EUR"
    user_email: EmailStr
    holder_name: str
    holder_surname: str
    user_phone: str = ""
    activity_code: str
    activity_name: str = ""
    modality_code: str = ""
    destination_code: str = ""
    operation_date: str
    adults: int = 1
    children: int = 0
    # Concierge hôtelier (résa pour client) — Pascal 2026-05-24
    on_behalf_of: Optional[str] = None
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_ref: Optional[str] = None


class ActivityConfirmRequest(BaseModel):
    airbizness_ref: str
    payment_intent_id: str


@router.get("/hbx/activities/search")
@limiter.limit("60/minute")
def search_hbx_activities(request: Request,
    destination_code: str = None,
    date_from: str = None,
    date_to: str = None,
    adults: int = 2,
    limit: int = 30,
):
    """Search activities HBX par destination + dates. Quota 5 000/jour."""
    if not destination_code or not date_from or not date_to:
        raise HTTPException(400, "destination_code, date_from et date_to requis")
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.activities.search import search_activities
        offers = search_activities(destination_code, date_from, date_to, guests=adults)
        items = []
        for o in offers[:limit]:
            d = o.details or {}
            items.append({
                "code": d.get("code"),
                "name": o.title,
                "price": o.price,
                "net_price": d.get("net_price"),
                "currency": o.currency,
                "destination_code": d.get("destination_code"),
                "country_name": d.get("country_name"),
                "duration": d.get("duration"),
                "modality_code": d.get("modality_code"),
                "rate_key": d.get("rate_key"),
                "free_cancellation": d.get("free_cancellation"),
                "operation_date": d.get("operation_date"),
                "operation_days": d.get("operation_days"),
                "content_summary": (d.get("content") or {}).get("description") if isinstance(d.get("content"), dict) else None,
            })
        return {"count": len(items), "activities": items,
                "query": {"destination_code": destination_code,
                          "date_from": date_from, "date_to": date_to,
                          "adults": adults}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/hbx/activity-booking/payment-intent")
@limiter.limit("30/minute")
def hbx_activity_payment_intent(request: Request, body: ActivityPaymentIntentRequest):
    """Crée Stripe PaymentIntent + INSERT activity_bookings_v2 status=payment_pending."""
    if body.gross_price < 1 or body.gross_price > 10000:
        raise HTTPException(400, "Prix invalide")
    airbizness_ref = f"ABA-{_uuid.uuid4().hex[:10].upper()}"

    try:
        # Audit 2026-05-27 sev 4 #52 : capture_method='manual' si flag activé.
        _pi_kwargs = {
            "amount": int(round(body.gross_price * 100)),
            "currency": body.currency.lower(),
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"airbizness_ref": airbizness_ref,
                          "activity_code": body.activity_code,
                          "user_email": body.user_email,
                          "type": "hbx_activity",
                          "capture_mode": "manual" if STRIPE_CAPTURE_MANUAL else "automatic"},
            "receipt_email": body.user_email,
            "description": f"AirBizness Activity · {body.activity_name[:50]} · {body.operation_date}",
        }
        if STRIPE_CAPTURE_MANUAL:
            _pi_kwargs["capture_method"] = "manual"
        intent = stripe.PaymentIntent.create(**_pi_kwargs)
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            on_behalf_of_payload = None
            if body.on_behalf_of:
                on_behalf_of_payload = {
                    "hotel_code": str(body.on_behalf_of),
                    "guest_name": body.guest_name or None,
                    "guest_email": body.guest_email or None,
                    "guest_ref": body.guest_ref or None,
                }
            cur.execute("""
                INSERT INTO activity_bookings_v2 (
                  airbizness_ref, status, user_email, user_phone,
                  holder_name, holder_surname,
                  activity_code, activity_name, modality_code, destination_code,
                  operation_date, adults, children,
                  gross_price, currency, rate_key,
                  payment_intent_id, payment_status, hbx_booking_raw
                ) VALUES (
                  %s, 'payment_pending', %s, %s,
                  %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s,
                  %s, 'pending', %s::jsonb
                )
            """, (
                airbizness_ref, body.user_email, body.user_phone,
                body.holder_name, body.holder_surname,
                body.activity_code, body.activity_name, body.modality_code, body.destination_code,
                body.operation_date, body.adults, body.children,
                body.gross_price, body.currency, body.rate_key,
                intent.id,
                json.dumps({"airbizness_options": {"on_behalf_of": on_behalf_of_payload}}) if on_behalf_of_payload else json.dumps({}),
            ))
    except Exception as e:
        print(f"[activity-booking] INSERT fail: {e}")

    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "airbizness_ref": airbizness_ref,
    }


@router.post("/hbx/activity-booking/confirm")
@limiter.limit("30/minute")
def hbx_activity_confirm(request: Request, body: ActivityConfirmRequest):
    """Confirme l'activité après paiement validé."""
    try:
        intent = stripe.PaymentIntent.retrieve(body.payment_intent_id)
    except Exception as e:
        raise HTTPException(400, f"Stripe retrieve fail: {e}")
    if intent.status != "succeeded":
        raise HTTPException(400, f"Paiement non confirmé (status={intent.status})")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM activity_bookings_v2 WHERE airbizness_ref = %s",
                (body.airbizness_ref,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(404, "Booking ref not found")
    if row["status"] == "confirmed":
        cur.close(); conn.close()
        return {"airbizness_ref": body.airbizness_ref,
                "hbx_reference": row["hbx_reference"],
                "status": "confirmed", "idempotent": True}
    cur.close()

    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.activities.booking import create_activity_booking
        hbx_booking = create_activity_booking(
            rate_key=row["rate_key"],
            holder_name=row["holder_name"],
            holder_surname=row["holder_surname"],
            holder_email=row["user_email"],
            holder_phone=row["user_phone"] or "",
            client_reference=row["airbizness_ref"],
        )
    except Exception as e:
        refund_id = None
        try:
            refund = stripe.Refund.create(
                payment_intent=body.payment_intent_id,
                metadata={"airbizness_ref": body.airbizness_ref,
                           "reason": "hbx_activity_failed_auto_refund"},
            )
            refund_id = refund.id
        except Exception as refund_err:
            print(f"[activity confirm] auto-refund fail: {refund_err}")
        try:
            with conn, conn.cursor() as cur2:
                cur2.execute("""
                    UPDATE activity_bookings_v2 SET status='booking_failed',
                       payment_status='succeeded', cancelled_at=NOW()
                    WHERE airbizness_ref=%s
                """, (body.airbizness_ref,))
        except Exception: pass
        raise HTTPException(500, f"HBX activity fail (refund {'OK' if refund_id else 'KO'}): {e}")

    import json as _json
    hbx_ref = hbx_booking.get("reference") or hbx_booking.get("bookings", [{}])[0].get("reference")
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE activity_bookings_v2 SET
                  status='confirmed', hbx_reference=%s,
                  payment_status='succeeded', payment_at=NOW(), confirmed_at=NOW(),
                  hbx_booking_raw=%s::jsonb
                WHERE airbizness_ref=%s
            """, (hbx_ref, _json.dumps(hbx_booking, default=str), body.airbizness_ref))
    finally:
        try: conn.close()
        except: pass

    return {
        "airbizness_ref": body.airbizness_ref,
        "hbx_reference": hbx_ref,
        "status": "confirmed",
        "activity_name": row["activity_name"],
        "operation_date": str(row["operation_date"]) if row["operation_date"] else None,
    }


@router.get("/hbx/activity-booking/{airbizness_ref}")
def hbx_get_activity_booking(request: Request, airbizness_ref: str):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, hbx_reference, status,
                   holder_name, holder_surname, user_email,
                   activity_code, activity_name, destination_code,
                   operation_date, adults, children,
                   gross_price, currency, created_at, confirmed_at
            FROM activity_bookings_v2 WHERE airbizness_ref = %s
        """, (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(404, "Booking not found")
        d = dict(row)
        for k in ("operation_date", "created_at", "confirmed_at"):
            if d.get(k):
                d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
        if d.get("gross_price") is not None:
            d["gross_price"] = float(d["gross_price"])
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
