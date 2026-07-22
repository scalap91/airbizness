"""routers/ratehawk_web.py — endpoints web pour le bouton « Réserver » RateHawk.

Alimente le widget de la page hôtel RateHawk : dispo LIVE (chambres + prix réels)
sur le `hid` de la page, puis lancement de la réservation (chaîne certifiée
hp → prebook → form → finish → status).

Données 100% réelles (aucun prix inventé) : tout vient de l'API RateHawk.
"""
from __future__ import annotations
import logging
from fastapi import APIRouter
from pydantic import BaseModel

from providers.ratehawk.client import RateHawkClient, RateHawkError

log = logging.getLogger("ratehawk_web")
router = APIRouter()


@router.get("/api/ratehawk/hotel/{hid}/rates")
def ratehawk_rates(hid: int, checkin: str, checkout: str, adults: int = 2,
                   currency: str = "EUR", residency: str = "gb"):
    """Disponibilité LIVE d'un hôtel RateHawk (chambres + tarifs réels)."""
    try:
        c = RateHawkClient()
        hp = c.search_hotel_page(int(hid), check_in=checkin, check_out=checkout,
                                 adults=adults, currency=currency, residency=residency)
        rates = (((hp.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [])
        rooms = []
        for r in rates[:24]:
            pt = ((r.get("payment_options") or {}).get("payment_types") or [{}])[0]
            rooms.append({
                "room_name": r.get("room_name"),
                "meal": r.get("meal"),
                "amount": pt.get("amount"),
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


class BookReq(BaseModel):
    book_hash: str
    first_name: str
    last_name: str
    email: str
    phone: str = "12124567899"


@router.post("/api/ratehawk/hotel/{hid}/book")
def ratehawk_book(hid: int, req: BookReq):
    """Lance la réservation RateHawk (prebook → form → finish). Renvoie le
    partner_order_id à suivre. ⚠️ Le paiement réel (Stripe) reste à câbler au
    lancement — en attendant, cet endpoint n'est pas exposé sur le bouton public."""
    import uuid
    try:
        c = RateHawkClient()
        pre = c.prebook(req.book_hash)
        prate = (((pre.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [{}])[0]
        p_hash = prate.get("book_hash")
        if not p_hash:
            return {"ok": False, "error": "prebook_no_hash"}
        oid = str(uuid.uuid4())
        c.booking_form(partner_order_id=oid, book_hash=p_hash, user_ip="0.0.0.0")
        pt = (prate.get("payment_options") or {}).get("payment_types", [{}])[0]
        c.booking_finish(
            partner_order_id=oid,
            rooms=[{"guests": [{"first_name": req.first_name, "last_name": req.last_name}]}],
            user={"email": req.email, "comment": "", "phone": req.phone},
            supplier_data={"first_name_original": req.first_name, "last_name_original": req.last_name,
                           "phone": req.phone, "email": req.email},
            payment_type={"type": pt.get("type", "deposit"), "amount": pt.get("amount"),
                          "currency_code": pt.get("currency_code", "EUR")})
        return {"ok": True, "partner_order_id": oid}
    except RateHawkError as e:
        return {"ok": False, "error": str(e)}
