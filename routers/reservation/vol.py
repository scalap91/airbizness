"""Réservation — sous-module VOL (/flight/booking/*).
Branché aux offres vol (catalogue providers via /deals). Le PAIEMENT est séparé (routers/paiement.py)."""
import json
import psycopg2, psycopg2.extras
from fastapi import APIRouter, Request, HTTPException
from main import DB_CONFIG, limiter, _alert_telegram

router = APIRouter()


@router.get("/flight/booking/{airbizness_ref}")
@limiter.limit("60/minute")
def flight_get_booking(request: Request, airbizness_ref: str):
    """Récup d'un booking pour la page de confirmation."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, offer_id, origin, destination,
                   airline_name, airline_code, departure_at, duration_minutes,
                   cabin_class, passengers, user_email, total_eur, currency,
                   status, pnr, duffel_order_id, is_mock,
                   created_at, confirmed_at, raw_offer
            FROM flight_bookings WHERE airbizness_ref = %s
        """, (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(404, "Booking not found")
        d = dict(row)
        # Sérialise datetimes / decimals
        for k in ("departure_at", "created_at", "confirmed_at"):
            if d.get(k) is not None and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()
        if d.get("total_eur") is not None:
            d["total_eur"] = float(d["total_eur"])
        # Ré-expose les options en top-level (depuis raw_offer JSONB)
        raw = d.get("raw_offer") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        opts = (raw.get("options") if isinstance(raw, dict) else None) or {}
        d["options"] = opts
        d["baggage_per_passenger"] = opts.get("baggage_per_passenger")
        d["cabin_premium"] = bool(opts.get("cabin_premium"))
        d["flex_ticket"] = bool(opts.get("flex_ticket"))
        d["insurance"] = bool(opts.get("insurance"))
        d["transfer"] = opts.get("transfer") or "none"
        d["options_total_eur"] = float(opts.get("options_total_eur") or 0)
        # Transfer HBX dynamique
        d["transfer_rate_key"] = opts.get("transfer_rate_key")
        d["transfer_price"] = float(opts.get("transfer_price_eur") or 0)
        d["transfer_label"] = opts.get("transfer_label")
        d["transfer_meta"] = opts.get("transfer_meta")
        d["transfer_booking_ref"] = (raw.get("transfer_booking_ref")
                                      if isinstance(raw, dict) else None)
        # raw_offer pas utile au front
        d.pop("raw_offer", None)
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@router.post("/flight/booking/{airbizness_ref}/sync")
@limiter.limit("20/minute")
def flight_sync_booking(request: Request, airbizness_ref: str):
    """Audit 2026-05-27 sev 3 #57 : re-sync depuis Duffel.

    GET /air/orders/{order_id} → met à jour status, documents, available_actions,
    void_window_ends_at. Utile pour récupérer e-tickets émis en différé.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT duffel_order_id, is_mock FROM flight_bookings WHERE airbizness_ref=%s",
                 (airbizness_ref,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise HTTPException(404, "Booking not found")
    if row.get("is_mock"):
        return {"airbizness_ref": airbizness_ref, "synced": False, "reason": "mock"}
    order_id = row.get("duffel_order_id")
    if not order_id:
        return {"airbizness_ref": airbizness_ref, "synced": False, "reason": "no_duffel_order"}
    try:
        from providers.duffel import get_order_live
        order = get_order_live(order_id)
    except Exception as e:
        _alert_telegram(f"flight/sync KO ab_ref={airbizness_ref} order={order_id[-12:]}: {str(e)[:200]}")
        raise HTTPException(502, f"Duffel sync failed: {str(e)[:200]}")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE flight_bookings
                SET raw_order=%s::jsonb,
                    duffel_documents=%s::jsonb,
                    pnr=COALESCE(%s, pnr),
                    booking_reference=COALESCE(%s, booking_reference)
                WHERE airbizness_ref=%s
            """, (json.dumps(order, default=str),
                  json.dumps(order.get("documents") or []),
                  order.get("booking_reference"),
                  order.get("booking_reference"),
                  airbizness_ref))
    except Exception as e:
        print(f"[flight/sync] DB update KO ab_ref={airbizness_ref}: {e}")
    return {
        "airbizness_ref": airbizness_ref,
        "synced": True,
        "duffel_order_id": order_id,
        "pnr": order.get("booking_reference"),
        "available_actions": order.get("available_actions") or [],
        "void_window_ends_at": order.get("void_window_ends_at"),
        "documents": order.get("documents") or [],
    }
