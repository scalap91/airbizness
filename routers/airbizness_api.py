"""routers/airbizness_api.py — API HTTP du provider AirBizness natif.

ROLE :
  Expose les services AirBizness-natifs (transferts publiés par les hôteliers
  revendiqués) en API HTTP, EXACTEMENT comme HBX expose son API
  `/transfer-api/1.0/`. Notre propre provider Python
  (providers/airbizness/transfers.py) consomme cette API HTTP — pas la DB
  directement. C'est le pattern provider externe interchangeable.

POURQUOI :
  - Cohérence : AirBizness est un provider parmi les autres (HBX, Duffel...)
  - Indépendance : si la DB change demain, le provider continue de marcher
  - Bonus B2B : ces endpoints peuvent être consommés par des partenaires
    externes (revendeurs AirBizness) plus tard

ENDPOINTS (alignés sur le pattern HBX /transfer-api/1.0/) :
  POST   /api/airbizness/transfers/availability       — search (renvoie offres + rate_key)
  POST   /api/airbizness/transfers/bookings           — book (avec rate_key)
  GET    /api/airbizness/transfers/bookings/{ref}     — read un booking
  DELETE /api/airbizness/transfers/bookings/{ref}     — cancel

  POST   /api/hotel-manager/transfers                 — l'hôtelier publie un transfert
  GET    /api/hotel-manager/transfers/{hotel_code}    — l'hôtelier liste ses transferts
"""
from __future__ import annotations
import logging
import os
import secrets
import string
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

logger = logging.getLogger("routers.airbizness_api")
router = APIRouter()


def _db_dsn() -> dict:
    return {
        "host":     os.getenv("DB_HOST"),
        "dbname":   os.getenv("DB_NAME"),
        "user":     os.getenv("DB_USER"),
        "password": os.getenv("DB_PASS"),
    }


def _new_ref(prefix: str = "AB-TR") -> str:
    """Génère une référence type AB-TR-XXXXXX (6 chars uppercase alphanum)."""
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"{prefix}-{suffix}"


# ════════════════════════════════════════════════════════════════════
#  API CONSOMMABLE (provider AirBizness vu de l'extérieur)
#  Pattern : aligné sur HBX /transfer-api/1.0/
# ════════════════════════════════════════════════════════════════════

class AvailabilityRequest(BaseModel):
    hotel_code: int
    passengers: int = 1
    pickup_at: Optional[str] = None  # ISO datetime, optionnel pour filtrage notice_hours


@router.post("/airbizness/transfers/availability")
def transfers_availability(req: AvailabilityRequest):
    """Search : renvoie les transferts disponibles pour un hôtel donné.

    Format de réponse aligné sur les autres providers (canonical Offer).
    """
    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, hotel_code, label, from_location, to_location,
                   from_lat, from_lng, to_lat, to_lng,
                   gross_price_eur, currency, vehicle_type,
                   max_passengers, max_luggage, notice_hours,
                   cancellation_policy
            FROM hotel_managed_transfers
            WHERE hotel_code = %s
              AND active = true
              AND max_passengers >= %s
            ORDER BY gross_price_eur ASC
        """, (req.hotel_code, req.passengers))
        rows = cur.fetchall()
        cur.close(); conn.close()
        offers = [
            {
                "rate_key": f"airbnz-tr-{r['id']}",
                "label": r["label"],
                "from_location": r["from_location"],
                "to_location": r["to_location"],
                "from_lat": float(r["from_lat"]) if r["from_lat"] else None,
                "from_lng": float(r["from_lng"]) if r["from_lng"] else None,
                "to_lat": float(r["to_lat"]) if r["to_lat"] else None,
                "to_lng": float(r["to_lng"]) if r["to_lng"] else None,
                "price_eur": float(r["gross_price_eur"]),
                "currency": r["currency"] or "EUR",
                "vehicle_type": r["vehicle_type"],
                "max_passengers": r["max_passengers"],
                "max_luggage": r["max_luggage"],
                "notice_hours": r["notice_hours"],
                "cancellation_policy": r["cancellation_policy"],
            }
            for r in rows
        ]
        return {
            "provider": "airbizness",
            "hotel_code": req.hotel_code,
            "offers": offers,
            "total": len(offers),
        }
    except Exception as e:
        logger.error(f"availability KO : {e}")
        raise HTTPException(500, f"availability error: {e}")


class BookingRequest(BaseModel):
    rate_key: str  # ex: 'airbnz-tr-42'
    user_email: EmailStr
    holder_name: str
    holder_surname: str
    user_phone: Optional[str] = None
    passengers_count: int = 1
    pickup_at: str  # ISO datetime
    flight_number: Optional[str] = None
    linked_hotel_booking_ref: Optional[str] = None
    stripe_payment_intent: Optional[str] = None


@router.post("/airbizness/transfers/bookings")
def transfers_book(req: BookingRequest):
    """Book : crée une réservation transfert et retourne sa référence."""
    try:
        transfer_id = int(req.rate_key.replace("airbnz-tr-", ""))
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Invalid rate_key: {req.rate_key}")

    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, net_price_eur, margin_pct, gross_price_eur, currency
            FROM hotel_managed_transfers WHERE id = %s AND active = true
        """, (transfer_id,))
        t = cur.fetchone()
        if not t:
            cur.close(); conn.close()
            raise HTTPException(404, f"Transfer {transfer_id} not found or inactive")

        ab_ref = _new_ref("AB-TR")
        cur.execute("""
            INSERT INTO transfer_bookings
              (airbizness_ref, transfer_id, linked_hotel_booking_ref,
               user_email, holder_name, holder_surname, user_phone,
               passengers_count, pickup_at, flight_number,
               net_price_eur, margin_pct, gross_price_eur, currency,
               status, stripe_payment_intent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, airbizness_ref, status
        """, (
            ab_ref, transfer_id, req.linked_hotel_booking_ref,
            req.user_email, req.holder_name, req.holder_surname, req.user_phone,
            req.passengers_count, req.pickup_at, req.flight_number,
            t["net_price_eur"], t["margin_pct"], t["gross_price_eur"], t["currency"],
            "confirmed" if req.stripe_payment_intent else "payment_pending",
            req.stripe_payment_intent,
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        logger.info(f"booking créé : {ab_ref} (transfer {transfer_id})")
        return {
            "provider": "airbizness",
            "booking_ref": ab_ref,
            "status": row["status"],
            "price_eur": float(t["gross_price_eur"]),
            "currency": t["currency"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"booking KO : {e}")
        raise HTTPException(500, f"booking error: {e}")


@router.get("/airbizness/transfers/bookings/{ref}")
def transfers_get_booking(ref: str):
    """Lecture d'une réservation transfert par sa référence."""
    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT b.*, t.label, t.from_location, t.to_location, t.vehicle_type
            FROM transfer_bookings b
            JOIN hotel_managed_transfers t ON t.id = b.transfer_id
            WHERE b.airbizness_ref = %s
        """, (ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(404, f"Booking {ref} not found")
        return {
            "booking_ref": row["airbizness_ref"],
            "status": row["status"],
            "transfer": {
                "label": row["label"],
                "from": row["from_location"],
                "to": row["to_location"],
                "vehicle_type": row["vehicle_type"],
            },
            "passenger": {
                "name": f"{row['holder_name']} {row['holder_surname']}".strip(),
                "email": row["user_email"],
                "phone": row["user_phone"],
                "count": row["passengers_count"],
            },
            "pickup_at": row["pickup_at"].isoformat() if row["pickup_at"] else None,
            "flight_number": row["flight_number"],
            "price_eur": float(row["gross_price_eur"]) if row["gross_price_eur"] else None,
            "currency": row["currency"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "linked_hotel_booking_ref": row["linked_hotel_booking_ref"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_booking KO : {e}")
        raise HTTPException(500, f"get_booking error: {e}")


@router.delete("/airbizness/transfers/bookings/{ref}")
def transfers_cancel_booking(ref: str):
    """Annulation d'une réservation transfert."""
    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor()
        cur.execute("""
            UPDATE transfer_bookings
            SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
            WHERE airbizness_ref = %s AND status NOT IN ('cancelled', 'completed')
            RETURNING airbizness_ref, status
        """, (ref,))
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(404, f"Booking {ref} not found or already cancelled/completed")
        return {"booking_ref": row[0], "status": row[1]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cancel_booking KO : {e}")
        raise HTTPException(500, f"cancel error: {e}")


# ════════════════════════════════════════════════════════════════════
#  API HÔTELIER (extranet — publier ses transferts)
# ════════════════════════════════════════════════════════════════════

class CreateTransferRequest(BaseModel):
    hotel_code: int
    label: str
    from_location: str
    to_location: str
    from_lat: Optional[float] = None
    from_lng: Optional[float] = None
    to_lat: Optional[float] = None
    to_lng: Optional[float] = None
    net_price_eur: float
    vehicle_type: Optional[str] = "berline"
    max_passengers: int = 4
    max_luggage: int = 2
    notice_hours: int = 12
    cancellation_policy: Optional[str] = None
    created_by: Optional[EmailStr] = None  # email de l'hôtelier qui publie


@router.post("/hotel-manager/transfers")
def hotel_manager_create_transfer(req: CreateTransferRequest):
    """Endpoint extranet hôtelier : publie un nouveau transfert dans le catalogue
    AirBizness pour son hôtel."""
    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO hotel_managed_transfers
              (hotel_code, label, from_location, to_location,
               from_lat, from_lng, to_lat, to_lng,
               net_price_eur, vehicle_type, max_passengers, max_luggage,
               notice_hours, cancellation_policy, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, gross_price_eur
        """, (
            req.hotel_code, req.label, req.from_location, req.to_location,
            req.from_lat, req.from_lng, req.to_lat, req.to_lng,
            req.net_price_eur, req.vehicle_type, req.max_passengers, req.max_luggage,
            req.notice_hours, req.cancellation_policy, req.created_by,
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        logger.info(f"hotel {req.hotel_code} publie transfert #{row['id']} ({row['gross_price_eur']}€)")
        return {
            "id": row["id"],
            "hotel_code": req.hotel_code,
            "net_price_eur": req.net_price_eur,
            "gross_price_eur": float(row["gross_price_eur"]),
            "margin_pct": 15.00,
            "active": True,
        }
    except Exception as e:
        logger.error(f"create_transfer KO : {e}")
        raise HTTPException(500, f"create_transfer error: {e}")


@router.get("/hotel-manager/transfers/{hotel_code}")
def hotel_manager_list_transfers(hotel_code: int):
    """Endpoint extranet hôtelier : liste les transferts publiés pour un hôtel."""
    try:
        conn = psycopg2.connect(**_db_dsn())
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, label, from_location, to_location,
                   net_price_eur, margin_pct, gross_price_eur, currency,
                   vehicle_type, max_passengers, max_luggage,
                   notice_hours, cancellation_policy, active,
                   created_at, updated_at, created_by
            FROM hotel_managed_transfers
            WHERE hotel_code = %s
            ORDER BY active DESC, created_at DESC
        """, (hotel_code,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return {
            "hotel_code": hotel_code,
            "transfers": [
                {**dict(r),
                 "net_price_eur": float(r["net_price_eur"]) if r["net_price_eur"] else None,
                 "gross_price_eur": float(r["gross_price_eur"]) if r["gross_price_eur"] else None,
                 "margin_pct": float(r["margin_pct"]) if r["margin_pct"] else None,
                 "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                 "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                 }
                for r in rows
            ],
            "total": len(rows),
        }
    except Exception as e:
        logger.error(f"list_transfers KO : {e}")
        raise HTTPException(500, f"list_transfers error: {e}")
