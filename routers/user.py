"""
User endpoints : CRUD carnet de voyageurs — Pascal 2026-05-31.

Permet à un user connecté de :
  - lister ses voyageurs enregistrés (titulaire + époux/se + enfants + amis)
  - ajouter / modifier / supprimer un voyageur
  - dans le tunnel résa, le front pourra dropdown ces voyageurs au lieu de form vide
"""
from datetime import date
from typing import Literal, Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from main import DB_CONFIG
from services.auth_token import get_current_user

# Pas de prefix /api : nginx strip déjà /api/ avant forward
router = APIRouter(prefix="/user", tags=["user"])


# ───────────────────────── Schémas ─────────────────────────

class PassengerIn(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    gender: Optional[Literal['m', 'f', 'x']] = None
    date_of_birth: Optional[date] = None
    type: Literal['adult', 'child', 'infant'] = 'adult'
    is_self: bool = False


# ───────────────────────── Endpoints ─────────────────────────

@router.get("/passengers")
def list_passengers(current_user: dict = Depends(get_current_user)):
    """Retourne tous les voyageurs enregistrés du user courant.
    Tri : is_self d'abord (le titulaire en premier), puis type, puis ordre création."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT id, first_name, last_name, gender, date_of_birth, type, is_self, created_at, updated_at
            FROM user_passengers
            WHERE user_id = %s
            ORDER BY is_self DESC, type, created_at
            """,
            (current_user["id"],)
        )
        rows = cur.fetchall()
    finally:
        cur.close(); conn.close()
    return {"passengers": [dict(r) for r in rows]}


@router.post("/passengers")
def add_passenger(req: PassengerIn, current_user: dict = Depends(get_current_user)):
    """Ajoute un voyageur au carnet. Si is_self=TRUE et un is_self existe déjà, refuse."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if req.is_self:
            cur.execute(
                "SELECT id FROM user_passengers WHERE user_id = %s AND is_self = TRUE",
                (current_user["id"],)
            )
            if cur.fetchone():
                raise HTTPException(409, "Un voyageur titulaire (is_self) existe déjà — modifier ou supprimer l'ancien d'abord")
        cur.execute(
            """
            INSERT INTO user_passengers (user_id, first_name, last_name, gender, date_of_birth, type, is_self)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, first_name, last_name, gender, date_of_birth, type, is_self, created_at
            """,
            (current_user["id"], req.first_name, req.last_name, req.gender, req.date_of_birth, req.type, req.is_self)
        )
        p = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    return dict(p)


@router.put("/passengers/{passenger_id}")
def update_passenger(passenger_id: int, req: PassengerIn, current_user: dict = Depends(get_current_user)):
    """Modifie un voyageur (vérifie ownership)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT user_id FROM user_passengers WHERE id = %s", (passenger_id,))
        row = cur.fetchone()
        if not row or row["user_id"] != current_user["id"]:
            raise HTTPException(404, "Voyageur introuvable")
        cur.execute(
            """
            UPDATE user_passengers
            SET first_name=%s, last_name=%s, gender=%s, date_of_birth=%s, type=%s, is_self=%s, updated_at=NOW()
            WHERE id = %s
            RETURNING id, first_name, last_name, gender, date_of_birth, type, is_self, updated_at
            """,
            (req.first_name, req.last_name, req.gender, req.date_of_birth, req.type, req.is_self, passenger_id)
        )
        p = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    return dict(p)


@router.delete("/passengers/{passenger_id}")
def delete_passenger(passenger_id: int, current_user: dict = Depends(get_current_user)):
    """Supprime un voyageur (vérifie ownership)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM user_passengers WHERE id = %s AND user_id = %s RETURNING id",
            (passenger_id, current_user["id"])
        )
        deleted = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    if not deleted:
        raise HTTPException(404, "Voyageur introuvable")
    return {"ok": True, "deleted_id": passenger_id}


# ════════════════════════════════════════════════════════════════════════
# Historique voyages — Pascal 2026-05-31
# Lien par email (les bookings stockent user_email, pas user_id).
# ════════════════════════════════════════════════════════════════════════

@router.get("/bookings")
def list_user_bookings(current_user: dict = Depends(get_current_user)):
    """Retourne tous les bookings du user (vols + hôtels + activités, fusionnés tri par date desc).
    Lien soft par email (user_email = current_user.email)."""
    email = current_user["email"]
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    bookings = []
    try:
        # Vols
        try:
            cur.execute(
                """
                SELECT 'flight' AS type, id, offer_id AS ref, status, total_amount AS price,
                       created_at, departure_at AS travel_date,
                       origin, destination, airline_name AS provider
                FROM flight_bookings
                WHERE LOWER(user_email) = LOWER(%s)
                ORDER BY created_at DESC LIMIT 50
                """, (email,)
            )
            bookings.extend([dict(r) for r in cur.fetchall()])
        except Exception: pass
        # Hôtels
        try:
            cur.execute(
                """
                SELECT 'hotel' AS type, id, booking_ref AS ref, status, total_amount AS price,
                       created_at, check_in AS travel_date,
                       NULL AS origin, hotel_name AS destination, provider
                FROM bookings_v2
                WHERE LOWER(holder_email) = LOWER(%s)
                ORDER BY created_at DESC LIMIT 50
                """, (email,)
            )
            bookings.extend([dict(r) for r in cur.fetchall()])
        except Exception: pass
    finally:
        cur.close(); conn.close()
    # Tri global par date desc
    bookings.sort(key=lambda b: b.get("created_at") or "", reverse=True)
    return {"bookings": bookings, "count": len(bookings)}


# ════════════════════════════════════════════════════════════════════════
# Alertes prix — Pascal 2026-05-31
# Lien par email aussi (table alertes a colonne email).
# ════════════════════════════════════════════════════════════════════════

class AlertIn(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    target_price: Optional[float] = None


@router.get("/alerts")
def list_user_alerts(current_user: dict = Depends(get_current_user)):
    email = current_user["email"]
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT id, origin, destination, target_price, created_at
            FROM alertes
            WHERE LOWER(email) = LOWER(%s)
            ORDER BY created_at DESC LIMIT 100
            """, (email,)
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        cur.close(); conn.close()
    return {"alerts": rows, "count": len(rows)}


@router.delete("/alerts/{alert_id}")
def delete_user_alert(alert_id: int, current_user: dict = Depends(get_current_user)):
    email = current_user["email"]
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM alertes WHERE id = %s AND LOWER(email) = LOWER(%s) RETURNING id",
            (alert_id, email)
        )
        deleted = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    if not deleted:
        raise HTTPException(404, "Alerte introuvable")
    return {"ok": True, "deleted_id": alert_id}


# ════════════════════════════════════════════════════════════════════════
# Cart recovery N2 — TunnelState lié au user (cross-device)
# Pascal 2026-05-31
# ════════════════════════════════════════════════════════════════════════

@router.get("/tunnel-state")
def get_tunnel_state(current_user: dict = Depends(get_current_user)):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT state_json, updated_at, expires_at FROM user_tunnel_states WHERE user_id = %s AND expires_at > NOW()",
            (current_user["id"],)
        )
        row = cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row:
        return {"state": None}
    return {"state": row["state_json"], "updated_at": row["updated_at"], "expires_at": row["expires_at"]}


@router.post("/tunnel-state")
def save_tunnel_state(payload: dict, current_user: dict = Depends(get_current_user)):
    """Upsert state_json. TTL 72h."""
    import json as _json
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO user_tunnel_states (user_id, state_json, updated_at, expires_at)
            VALUES (%s, %s::jsonb, NOW(), NOW() + INTERVAL '72 hours')
            ON CONFLICT (user_id) DO UPDATE SET
              state_json = EXCLUDED.state_json,
              updated_at = NOW(),
              expires_at = NOW() + INTERVAL '72 hours'
            """,
            (current_user["id"], _json.dumps(payload))
        )
        conn.commit()
    finally:
        cur.close(); conn.close()
    return {"ok": True}


@router.delete("/tunnel-state")
def clear_tunnel_state(current_user: dict = Depends(get_current_user)):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM user_tunnel_states WHERE user_id = %s", (current_user["id"],))
        conn.commit()
    finally:
        cur.close(); conn.close()
    return {"ok": True}
