"""
Alertes prix endpoints — migré de main.py 2026-06-01 (1er module d'une vague de 13). Pascal/orchestrateur DeepSeek.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field
from main import DB_CONFIG, limiter
import psycopg2
import psycopg2.extras

router = APIRouter()

class AlerteRequest(BaseModel):
    email: EmailStr
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(default="", max_length=3)
    max_price: int = Field(gt=0, lt=100000)

@router.post("/alertes")
@limiter.limit("5/minute")
def create_alerte(request: Request, req: AlerteRequest):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO alertes (email, origin, destination, max_price)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (req.email, req.origin.upper(), req.destination.upper() if req.destination else None, req.max_price))
    alerte_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "created", "id": alerte_id}

@router.get("/alertes")
def get_alertes(email: str):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM alertes WHERE email = %s ORDER BY created_at DESC", (email,))
    alertes = [dict(a) for a in cur.fetchall()]
    cur.close()
    conn.close()
    return {"alertes": alertes}

@router.delete("/alertes/{alerte_id}")
def delete_alerte(alerte_id: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM alertes WHERE id = %s", (alerte_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "deleted"}
