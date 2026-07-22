"""
Module de routage pour les transferts (HBX et API haut niveau).
Migré depuis main.py le 2026-06-01 (6e module migré).
Contient les endpoints de recherche, réservation et utilitaires transfert.
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from main import limiter, CITY_AIRPORTS_ALL

router = APIRouter()


class TransferBookRequest(BaseModel):
    rate_key: str
    holder_name: str
    holder_email: EmailStr
    holder_phone: Optional[str] = ""
    client_reference: Optional[str] = ""


# ─────────────────────────────────────────────────────────────────────
# RECHERCHE TRANSFERTS HBX (2026-05-22)
# ─────────────────────────────────────────────────────────────────────

@router.post("/hbx/transfers/search")
@limiter.limit("60/minute")
def search_hbx_transfers(request: Request, body: dict):
    """Search transferts HBX. Sandbox actuellement en HTTP 500 côté HBX (à clarifier AM)."""
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.transfers.search import search_transfers
        offers = search_transfers(
            from_type=body.get("from_type", "IATA"),
            from_code=body["from_code"],
            to_type=body.get("to_type", "IATA"),
            to_code=body["to_code"],
            outbound=body["outbound"],
            inbound=body.get("inbound"),
            adults=int(body.get("adults", 2)),
            children=int(body.get("children", 0)),
            infants=int(body.get("infants", 0)),
        )
        items = []
        for o in offers[:30]:
            d = o.details or {}
            items.append({
                "code": d.get("code"),
                "title": o.title,
                "price": o.price,
                "currency": o.currency,
                "max_pax": d.get("max_pax"),
                "vehicle": d.get("vehicle"),
                "category": d.get("category"),
            })
        return {"count": len(items), "transfers": items}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────
# API TRANSFER — Pascal 2026-05-24 : remplace mock 35/60€ par options HBX
# Wrapper haut niveau qui tente HBX et fallback mock 3-5 options.
# Documenté dans /var/www/airbizness/providers/hbx_transfer.py
# ─────────────────────────────────────────────────────────────────────

@router.get("/transfer/search")
@limiter.limit("60/minute")
def transfer_search(
    request: Request,
    from_type: str = "IATA",
    from_code: str = "",
    to_type: str = "ATLAS",
    to_code: str = "",
    outbound: str = "",
    return_: Optional[str] = None,  # alias géré ci-dessous (param 'return' réservé)
    adults: int = 2,
    children: int = 0,
    infants: int = 0,
):
    """Recherche les options de transfert.

    Query params :
        from_type / from_code  (ex 'IATA' / 'CDG' ou 'ATLAS' / '887190')
        to_type / to_code
        outbound (ISO datetime)
        return   (optionnel ISO datetime → aller-retour)
        adults, children, infants

    Retourne JSON {count, options:[{rate_key,title,vehicle,operator,price,...}],
                   from, to, outbound, return, mock_mode}.
    """
    # FastAPI ne supporte pas un paramètre nommé 'return' (mot-clé Python),
    # donc on lit aussi le query string brut.
    return_iso = return_
    raw_return = request.query_params.get("return")
    if raw_return and not return_iso:
        return_iso = raw_return
    if not from_code or not to_code or not outbound:
        raise HTTPException(400, "from_code, to_code et outbound requis")
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers import hbx_transfer
        options = hbx_transfer.search_transfers(
            from_code=from_code, to_code=to_code,
            outbound_iso=outbound, return_iso=return_iso,
            adults=int(adults), children=int(children), infants=int(infants),
            from_type=from_type, to_type=to_type,
        )
        return {
            "count": len(options),
            "options": options,
            "from": {"type": from_type, "code": from_code},
            "to": {"type": to_type, "code": to_code},
            "outbound": outbound,
            "return": return_iso,
            "mock_mode": hbx_transfer._mock_mode_active(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"transfer search error: {e}")


@router.post("/transfer/book")
@limiter.limit("30/minute")
def transfer_book(request: Request, body: TransferBookRequest):
    """Confirme une résa de transfert. Appelé interne par /pack/confirm,
    /hbx/booking/confirm, /flight/booking/confirm. Ne réalise PAS le paiement
    (le paiement transfert est inclus dans le total pack/hôtel/vol).
    """
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers import hbx_transfer
        result = hbx_transfer.book_transfer(
            rate_key=body.rate_key,
            holder_name=body.holder_name,
            holder_email=body.holder_email,
            holder_phone=body.holder_phone or "",
            client_reference=body.client_reference or "",
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"transfer book error: {e}")


@router.get("/transfer/airport-from-route")
def transfer_airport_from_route(origin: str = "", destination: str = ""):
    """Résout le couple {origin, destination} d'un itinéraire vol vers
    un from/to typé pour search transfer.

    Convention AirBizness :
      - destination du vol → arrivée → from (point de récup' transfer)
      - origin: code IATA aéroport (3 lettres) → from_type=IATA
      - destination: pareil
    """
    if not origin or not destination:
        raise HTTPException(400, "origin et destination requis")
    org = (origin or "").strip().upper()[:3]
    dst = (destination or "").strip().upper()[:3]
    return {
        # Aéroport de destination = point de pickup le plus courant
        "from_type": "IATA",
        "from_code": dst,
        "default_to_type": "GPS",  # client fournit l'adresse libre (lat/lng ou texte)
        "origin_iata": org,
        "destination_iata": dst,
    }


@router.get("/transfer/airports-near")
def transfer_airports_near(city: str = "", limit: int = 5):
    """Liste les aéroports proches d'une ville (utilisé par tunnel hôtel seul).

    Si city matche CITY_AIRPORTS_ALL → renvoie ces aéroports.
    Sinon → fallback TOP_AIRPORTS (5 plus populaires Europe + Dubaï).
    """
    city_key = (city or "").strip().upper()
    if city_key in CITY_AIRPORTS_ALL:
        aps = CITY_AIRPORTS_ALL[city_key][:limit]
        return {
            "city": city_key,
            "airports": [
                {"iata": iata, "name": name, "lat": lat, "lng": lng}
                for (lat, lng, iata, name) in aps
            ],
            "source": "local",
        }
    # Fallback
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers import hbx_transfer
        return {
            "city": city_key or None,
            "airports": hbx_transfer.TOP_AIRPORTS[:limit],
            "source": "top_popular",
        }
    except Exception as e:
        raise HTTPException(500, f"airports-near error: {e}")
