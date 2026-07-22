"""
Aviasales / TravelPayouts proxy — moteur de recherche vols natif AirBizness.
Réécrit pour correspondre au format Duffel de resultats.html.
Créé 2026-06-02 (mini-mission Pascal Repir).

Endpoint :
  GET /api/aviasales/search?origin=PAR&destination=JFK&date=2026-08-15&adults=1
  Accepte aussi les alias : from, to, pax pour rétro-compat.

Le token TRAVELPAYOUTS_TOKEN est lu côté serveur (jamais exposé au front).
Fallback gracieux 200 avec cards vides si la variable d'env est absente.

L'UI native (charte AirBizness or/dark) est intégrée dans le home preview
(public/home-prelaunch-preview.html), onglet "Vols". JS fetch -> cette route
-> rendu cards. Bouton "Réserver" -> deeplink Aviasales avec marker 723813.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
import os
import logging
import urllib.parse
import urllib.request
import json
import base64
from datetime import datetime, timedelta, date

router = APIRouter()
log = logging.getLogger("aviasales")

TP_ENDPOINT = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

# Dictionnaire des noms de compagnies aériennes (cosmétique pour l'affichage)
AIRLINE_NAMES = {
    "AF": "Air France",
    "KL": "KLM",
    "LH": "Lufthansa",
    "BA": "British Airways",
    "IB": "Iberia",
    "AZ": "Alitalia",
    "AY": "Finnair",
    "LO": "LOT Polish Airlines",
    "FR": "Ryanair",
    "U2": "EasyJet",
    "W4": "Wizz Air",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "EY": "Etihad Airways",
    "TK": "Turkish Airlines",
    "MS": "EgyptAir",
    "SU": "Aeroflot",
    "A3": "Aegean Airlines",
    "OS": "Austrian Airlines",
    "LX": "Swiss International Air Lines",
    "SK": "SAS",
    "TP": "TAP Air Portugal",
    "KM": "Air Malta",
    "TG": "Thai Airways",
    "JL": "Japan Airlines",
    "NH": "All Nippon Airways",
    "OZ": "Asiana Airlines",
    "CI": "China Airlines",
    "CX": "Cathay Pacific",
    "MU": "China Eastern",
    "CA": "Air China",
    "SQ": "Singapore Airlines",
    "AI": "Air India",
    "ET": "Ethiopian Airlines",
    "KE": "Korean Air",
    "BR": "EVA Air",
    "GA": "Garuda Indonesia",
    "RJ": "Royal Jordanian",
    "GF": "Gulf Air",
    "WY": "Oman Air",
    "ME": "Middle East Airlines",
    "F9": "Frontier Airlines",
    "B6": "JetBlue",
    "AA": "American Airlines",
    "UA": "United Airlines",
    "DL": "Delta Air Lines",
    "AC": "Air Canada",
    "WS": "WestJet",
    "AS": "Alaska Airlines",
    "AV": "Avianca",
    "LA": "LATAM Airlines",
    "JJ": "LATAM Brasil",
    "AR": "Aerolíneas Argentinas",
    "CM": "Copa Airlines",
}


def _compute_arrival(departure_at: str, duration_minutes: int) -> str:
    """Calcule l'heure d'arrivée à partir du départ et de la durée."""
    try:
        dep_dt = datetime.fromisoformat(departure_at)
        arr_dt = dep_dt + timedelta(minutes=duration_minutes)
        return arr_dt.isoformat()
    except Exception:
        return departure_at  # fallback


def _build_deeplink(origin: str, destination: str, departure_at: str,
                    return_at: str | None, adults: int) -> str:
    """Construit le deeplink Aviasales avec marker TravelPayouts (fallback)."""
    marker = os.getenv("TRAVELPAYOUTS_MARKER", "723813")
    try:
        dep_ddmm = departure_at[8:10] + departure_at[5:7]
    except Exception:
        dep_ddmm = ""
    ret_ddmm = ""
    if return_at:
        try:
            ret_ddmm = return_at[8:10] + return_at[5:7]
        except Exception:
            ret_ddmm = ""
    slug = f"{origin}{dep_ddmm}{destination}{ret_ddmm}{adults}"
    return f"https://www.aviasales.com/search/{slug}?marker={marker}"


def _build_calendar(ref_date: str, has_results: bool) -> list:
    """Construit le calendrier 7 cellules (J-3..J+3)."""
    try:
        ref = date.fromisoformat(ref_date)
    except ValueError:
        ref = date.today()
    today = date.today()
    calendar = []
    for offset in range(-3, 4):
        cell_date = ref + timedelta(days=offset)
        cell_date_str = cell_date.isoformat()
        if cell_date < today:
            status = "past"
            min_price = None
            offers_count = 0
        elif cell_date == ref:
            # Jour de référence : on a les résultats
            if has_results:
                status = "ok"
                min_price = None  # Sera mis à jour après
                offers_count = 0
            else:
                status = "no_offer"
                min_price = None
                offers_count = 0
        else:
            # TP ne donne pas de price calendar gratuit, on met à plat les voisines
            status = "no_offer"
            min_price = None
            offers_count = 0
        calendar.append({
            "date": cell_date_str,
            "min_price": min_price,
            "offers_count": offers_count,
            "status": status,
        })
    return calendar


@router.get("/aviasales/search")
async def aviasales_search(
    request: Request,
    origin: str = Query(None, min_length=3, max_length=3, description="Code IATA départ"),
    destination: str = Query(None, min_length=3, max_length=3, description="Code IATA arrivée"),
    date: str = Query(None, description="Date départ YYYY-MM-DD"),
    return_date: str | None = Query(None, description="Date retour YYYY-MM-DD"),
    adults: int = Query(1, ge=1, le=9),
    children: int = Query(0, ge=0, le=9),
    infants: int = Query(0, ge=0, le=9),
    cabin: str = Query("economy"),
    limit: int = Query(30, ge=1, le=30),
    # Alias pour rétro-compat avec resultats.html
    from_: str = Query(None, alias="from", min_length=3, max_length=3),
    to: str = Query(None, min_length=3, max_length=3),
    pax: int = Query(None, ge=1, le=9),
):
    """Proxy vers TravelPayouts/Aviasales prices_for_dates.

    Renvoie un JSON au format Duffel pour compatibilité avec resultats.html.
    """
    # Gestion des alias
    if origin is None and from_ is not None:
        origin = from_
    if destination is None and to is not None:
        destination = to
    if pax is not None:
        adults = pax

    # Validation des paramètres obligatoires
    if not origin or not destination or not date:
        return JSONResponse(
            status_code=200,
            content={
                "ref_date": date or "",
                "from": origin or "",
                "to": destination or "",
                "pax": adults + children + infants,
                "cabin": cabin,
                "currency": "EUR",
                "cards": [],
                "calendar": _build_calendar(date or "", False),
                "cheapest_date": None,
                "error": "missing_parameters",
            },
        )

    token = os.getenv("TRAVELPAYOUTS_TOKEN")
    if not token:
        log.warning("TRAVELPAYOUTS_TOKEN absent du .env — retour cards vides")
        return JSONResponse(
            status_code=200,
            content={
                "ref_date": date,
                "from": origin,
                "to": destination,
                "pax": adults + children + infants,
                "cabin": cabin,
                "currency": "EUR",
                "cards": [],
                "calendar": _build_calendar(date, False),
                "cheapest_date": None,
                "error": "config_missing",
            },
        )

    # Normalise codes IATA en majuscules
    origin = origin.upper().strip()
    destination = destination.upper().strip()

    # Appel à TravelPayouts pour la date J
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": date,
        "currency": "eur",
        "limit": limit,
        "sorting": "price",
        "one_way": "true" if not return_date else "false",
    }
    if return_date:
        params["return_at"] = return_date

    qs = urllib.parse.urlencode(params)
    url = f"{TP_ENDPOINT}?{qs}"

    try:
        req = urllib.request.Request(url, headers={"X-Access-Token": token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        log.error("Aviasales API call failed: %s", e)
        # Retour 200 avec cards vides pour ne pas casser le front
        return JSONResponse(
            status_code=200,
            content={
                "ref_date": date,
                "from": origin,
                "to": destination,
                "pax": adults + children + infants,
                "cabin": cabin,
                "currency": "EUR",
                "cards": [],
                "calendar": _build_calendar(date, False),
                "cheapest_date": None,
                "error": "upstream_unavailable",
            },
        )

    if not data.get("success"):
        return JSONResponse(
            status_code=200,
            content={
                "ref_date": date,
                "from": origin,
                "to": destination,
                "pax": adults + children + infants,
                "cabin": cabin,
                "currency": "EUR",
                "cards": [],
                "calendar": _build_calendar(date, False),
                "cheapest_date": None,
                "error": "no_results",
            },
        )

    raw_results = data.get("data") or []
    cards = []
    marker = os.getenv("TRAVELPAYOUTS_MARKER", "723813")
    cheapest_price = None

    for r in raw_results:
        price = r.get("price")
        if price is None:
            continue
        price = float(price)

        # Construction du deeplink
        link = r.get("link", "")
        if link and link.startswith("/"):
            if "?" in link:
                deeplink_url = f"https://www.aviasales.com{link}&marker={marker}"
            else:
                deeplink_url = f"https://www.aviasales.com{link}?marker={marker}"
        else:
            deeplink_url = _build_deeplink(origin, destination, date, return_date, adults)

        # offer_id basé sur le deeplink (base64)
        offer_id = "tp_" + base64.urlsafe_b64encode(deeplink_url.encode()).decode().rstrip("=")

        airline_code = r.get("airline", "")
        airline_name = AIRLINE_NAMES.get(airline_code, airline_code)

        departure_at = r.get("departure_at", "")
        duration_minutes = r.get("duration_to") or r.get("duration", 0)
        if duration_minutes:
            duration_minutes = int(duration_minutes)
        else:
            duration_minutes = 0

        arrival_at = _compute_arrival(departure_at, duration_minutes)

        stops = int(r.get("transfers", 0))
        fare_brand = r.get("gate", "")

        card = {
            "offer_id": offer_id,
            "origin": r.get("origin", origin),
            "destination": r.get("destination", destination),
            "airline_name": airline_name,
            "airline_code": airline_code,
            "price": price,
            "currency": data.get("currency", "eur").upper(),
            "cabin_class": cabin,
            "departure_at": departure_at,
            "arrival_at": arrival_at,
            "duration_minutes": duration_minutes,
            "stops": stops,
            "fare_brand": fare_brand,
            "expires_at": None,
            "provider": "aviasales",
            "source": "affiliate",
            "deeplink_url": deeplink_url,
        }
        cards.append(card)

        # Suivi du prix le plus bas pour le calendrier
        if cheapest_price is None or price < cheapest_price:
            cheapest_price = price

    # Construction du calendrier
    has_results = len(cards) > 0
    calendar = _build_calendar(date, has_results)

    # Mise à jour du prix pour la cellule J
    if has_results:
        for cell in calendar:
            if cell["date"] == date:
                cell["min_price"] = cheapest_price
                cell["offers_count"] = len(cards)
                cell["status"] = "ok"
                break

    # cheapest_date
    cheapest_date = date if cheapest_price is not None else None

    return {
        "ref_date": date,
        "from": origin,
        "to": destination,
        "pax": adults + children + infants,
        "cabin": cabin,
        "currency": "EUR",
        "cards": cards,
        "calendar": calendar,
        "cheapest_date": cheapest_date,
    }
