"""Module HOTEL — offre hôtel (recherche / fiche / disponibilité).

Provider-agnostique : interroge les providers hôtel allumés du catalogue (HBX
aujourd'hui, Ratehawk/TBO/WebBeds quand allumés) et normalise via providers.base
(UnifiedHotel / UnifiedOffer). Le client cherche et choisit lui-même ; le module
ne décide rien. RÉSERVATION et PAIEMENT sont des modules à part.

NB migration : routes encore nommées /hbx/* (héritage) — à rendre
provider-agnostiques quand tout le module sera sorti.
"""
import json
import psycopg2, psycopg2.extras
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from services.api_cache import cached  # Pascal 2026-05-31 : cache court préserve quota HBX
from main import (limiter, DB_CONFIG,
                  extract_best_main_photo, extract_gallery_photos, extract_room_photos,
                  _get_active_hotel_providers, describe_hbx_room,
                  format_cancellation_fr, board_label_fr,
                  extract_facilities_fr, FACILITY_CATEGORY_LABELS,
                  _city_key, nearby_pois, airports_nearby)

router = APIRouter()


@router.get("/hotels/search")
@cached(ttl=600, key_prefix="hotels_search", max_entries=20000)  # 10 min, LRU 20k
def v2_hotels_search(
    destination: str,
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    checkin: Optional[str] = None,
    checkout: Optional[str] = None,
    guests: Optional[int] = None,
    adults: Optional[int] = None,
    rooms: int = 1,
    stars_min: int = 3,
    budget_max: Optional[float] = None,
    limit: int = 60,
):
    """Search hôtels multi-provider avec dédup giata + best-offer-per-hotel.

    Frontend → cet endpoint → HBX + TBO + RateHawk en parallèle → 1 résultat unifié.
    Accepte les alias `checkin/checkout/adults` (curl style) en plus de `check_in/check_out/guests`.
    """
    # Aliases
    check_in = check_in or checkin
    check_out = check_out or checkout
    if guests is None:
        guests = adults if adults is not None else 2
    if not check_in or not check_out:
        return JSONResponse({"error": "missing_dates",
                              "detail": "check_in / check_out (ou checkin/checkout) requis"},
                             status_code=400)
    from providers.base import HotelQuery, search_hotels_multi

    query = HotelQuery(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        guests=guests,
        rooms=rooms,
        stars_min=stars_min,
        budget_max=budget_max,
    )
    providers = _get_active_hotel_providers()
    aggregated = search_hotels_multi(query, providers)

    top_hotels = aggregated[:limit]

    # ── Enrichissement batch galerie (Pascal 2026-05-31) ────────────────────
    # Le rendu hotels.html attend `image` + `gallery` + `images_total` pour
    # activer son carrousel multi-photos. L'agrégateur ne livre que main_photo.
    # On charge en 1 query toutes les images des hôtels affichés.
    galleries_by_code = {}
    hotel_codes = [int(h.canonical_hotel_code) for h in top_hotels
                   if h.canonical_hotel_code and str(h.canonical_hotel_code).lstrip("-").isdigit()]
    if hotel_codes:
        try:
            from providers.hbx.photos import extract_gallery_photos
            _conn = psycopg2.connect(**DB_CONFIG)
            _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _cur.execute("""
                SELECT hotel_code, raw, images_count
                FROM hbx_hotels_catalog
                WHERE hotel_code = ANY(%s::int[])
            """, (hotel_codes,))
            for row in _cur.fetchall():
                _raw = row["raw"]
                if isinstance(_raw, str):
                    try: _raw = json.loads(_raw)
                    except Exception: _raw = None
                _raw_images = (_raw or {}).get("images") if isinstance(_raw, dict) else None
                _photos = extract_gallery_photos(_raw_images, provider="hbx", max_photos=12) or []
                galleries_by_code[row["hotel_code"]] = {
                    "gallery": _photos,
                    "images_total": int(row["images_count"] or len(_photos)),
                }
            _cur.close(); _conn.close()
        except Exception as _e:
            # Fallback safe : si la query crash, on n'enrichit pas — image unique
            galleries_by_code = {}

    items = []
    for h in top_hotels:
        best = h.best_offer
        _gal_info = galleries_by_code.get(int(h.canonical_hotel_code) if str(h.canonical_hotel_code).lstrip("-").isdigit() else None, {})
        _gallery = _gal_info.get("gallery", [])
        items.append({
            "giata_code": h.giata_code,
            "hotel_code": h.canonical_hotel_code,
            "name": h.name,
            "stars": h.stars,
            "city": h.city,
            "country_code": h.country_code,
            "address": h.address,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "main_photo": h.main_photo,
            "image": h.main_photo,                    # alias compat hotels.html
            "gallery": _gallery,                       # carrousel multi-photos
            "images_total": _gal_info.get("images_total", len(_gallery)),
            "providers": h.providers,
            "best_price": h.cheapest_price,
            "best_rate_key": best.provider_offer_id if best else None,
            "best_provider": best.provider if best else None,
            "currency": best.currency if best else "EUR",
            "alternative_offers_count": max(0, len(h.offers) - 1),
            "is_mock": False,
        })

    # (RETIRÉ 2026-05-29) Plus de synthèse de faux hôtels : si HBX ne renvoie rien,
    # la liste reste vide → « aucun hôtel disponible », jamais un faux.
    is_mock_response = False

    # Lazy pricing (Pascal 2026-05-31, BUG-3) : si l'API live n'a rien renvoyé
    # (quota HBX crevé, provider down), on liste le catalog local SANS prix.
    # Le prix n'est tapé chez HBX qu'au clic sur un hôtel précis (/hotels/{code}/rooms).
    # Règle : jamais de prix engageant inventé → ces items ont best_price=None
    #         et un flag `pricing_on_demand=True` pour que le front affiche un CTA.
    fallback_used = None
    if not items:
        items = _catalog_fallback_no_price(destination, limit=limit, stars_min=stars_min)
        if items:
            fallback_used = "catalog_no_price"

    # Garde-fou backend (Pascal 2026-05-31) : signaler au front si le quota HBX
    # est crevé pour qu'il affiche le bandeau honnête sans deviner.
    if fallback_used == "catalog_no_price" and len(items) < 5:
        quota_status = "exceeded"
    elif fallback_used == "catalog_no_price":
        quota_status = "degraded"
    else:
        quota_status = "ok"

    return {
        "destination": destination,
        "check_in": check_in,
        "check_out": check_out,
        "providers_polled": [p.name for p in providers] + (["mock-catalog"] if is_mock_response else []),
        "total": len(items),
        "returned": len(items),
        "hotels": items,
        "is_mock": is_mock_response,
        "fallback_used": fallback_used,
        "quota_status": quota_status,
    }


def _catalog_fallback_no_price(destination: str, limit: int = 60, stars_min: int = 3) -> list:
    """Charge des hôtels depuis hbx_hotels_catalog SANS prix (lazy pricing).
    Utilisé quand l'API HBX live ne renvoie rien (quota crevé, provider down).
    Le prix sera tapé chez HBX au clic sur l'hôtel (/hotels/{code}/rooms).
    """
    if not destination:
        return []
    out = []
    try:
        from providers.hbx.photos import extract_gallery_photos
        _conn = psycopg2.connect(**DB_CONFIG)
        _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Match ville par ILIKE (souple sur la casse et les accents partiels)
        _cur.execute("""
            SELECT hotel_code, name, category_stars, city, country_code,
                   address, latitude, longitude, giata_code, raw, images_count
            FROM hbx_hotels_catalog
            WHERE city ILIKE %s
              AND COALESCE(category_stars, 0) >= %s
              AND name NOT ILIKE '%%test%%'
              AND name NOT ILIKE '%%bot%%'
              AND name NOT IN ('Inventado Test', 'This Hotel Is A Testing')
            ORDER BY COALESCE(images_count, 0) DESC, hotel_code ASC
            LIMIT %s
        """, (destination, stars_min, limit))
        for row in _cur.fetchall():
            _raw = row["raw"]
            if isinstance(_raw, str):
                try: _raw = json.loads(_raw)
                except Exception: _raw = None
            _raw_images = (_raw or {}).get("images") if isinstance(_raw, dict) else None
            _photos = extract_gallery_photos(_raw_images, provider="hbx", max_photos=12) or []
            main_photo = _photos[0] if _photos else None
            out.append({
                "giata_code": row["giata_code"],
                "hotel_code": str(row["hotel_code"]),
                "name": row["name"],
                "stars": row["category_stars"],
                "city": row["city"],
                "country_code": row["country_code"],
                "address": row["address"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "main_photo": main_photo,
                "image": main_photo,
                "gallery": _photos,
                "images_total": int(row["images_count"] or len(_photos)),
                "providers": ["catalog"],
                "best_price": None,           # PAS de prix : sera tapé au clic
                "best_rate_key": None,
                "best_provider": None,
                "currency": "EUR",
                "alternative_offers_count": 0,
                "is_mock": False,
                "pricing_on_demand": True,    # signal pour le front (CTA "Voir disponibilités")
            })
        _cur.close(); _conn.close()
    except Exception:
        return []
    return out


@router.get("/hotels/quote")
def v2_hotels_quote(
    hotel_code: Optional[str] = None,
    giata_code: Optional[str] = None,
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    guests: Optional[int] = None,
    rooms: int = 1,
    rate_key: Optional[str] = None,
    provider: Optional[str] = None,
    code: Optional[str] = None,
    giata: Optional[str] = None,
    checkin: Optional[str] = None,
    checkout: Optional[str] = None,
    adults: Optional[int] = None,
):
    """Comparaison multi-provider pour 1 hôtel donné.

    Si `rate_key` est fourni (chemin propre, depuis le listing) → checkrate direct
    sans re-search : pas de divergence avec le listing, pas de rate-limit, plus
    rapide. Sinon → search général sur la destination + filtre sur hotel_code
    OU giata_code (fallback historique).

    Accepte les alias `code/giata/checkin/checkout/adults` (utilisés par le front
    et les liens partagés) en plus de `hotel_code/giata_code/check_in/check_out/guests`.
    """
    from providers.base import HotelQuery, search_hotels_multi
    hotel_code = hotel_code or code
    giata_code = giata_code or giata
    check_in   = check_in or checkin
    check_out  = check_out or checkout
    guests     = guests if guests is not None else (adults if adults is not None else 2)
    if not check_in or not check_out:
        return JSONResponse({"error": "missing_dates"}, status_code=400)

    # ── FASTPATH 2026-05-30 : si rate_key passé par le listing → checkrate direct
    # (évite la divergence listing/quote — source unique = ce rate_key précis).
    if rate_key:
        return _quote_via_checkrate(rate_key, provider or "hbx", hotel_code, check_in, check_out, guests, rooms)

    # On a besoin de la destination pour search → on la déduit du catalog
    destination = None
    catalog_hotel = None
    gallery = {"rooms": [], "general": [], "restaurant": [], "bar": [], "outdoor": [], "other": []}
    if hotel_code:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT hotel_code, giata_code, name, destination_code, city, country_code,
                   category_stars, latitude, longitude, main_image_url, address,
                   email, phone_main, web, images_count, facilities_count,
                   LEFT(description_en, 500) AS description,
                   raw
            FROM hbx_hotels_catalog WHERE hotel_code = %s
        """, (int(hotel_code),))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            row_d = dict(row)
            destination = row_d["destination_code"]
            # Parse raw (jsonb peut arriver en str selon driver)
            _raw = row_d.get("raw")
            if isinstance(_raw, str):
                try: _raw = json.loads(_raw)
                except Exception: _raw = None
            raw_images = (_raw or {}).get("images") or [] if isinstance(_raw, dict) else []

            # ── FIX : main_image_url = la VRAIE hero (imageTypeCode='GEN' + order min)
            best_main = extract_best_main_photo(raw_images, provider="hbx")
            if best_main:
                row_d["main_image_url"] = best_main

            # ── Équipements & services classés par catégorie d'affichage (FR)
            row_d["facilities_by_category"] = extract_facilities_fr(
                (_raw or {}).get("facilities") if isinstance(_raw, dict) else None,
                provider="hbx",
            )
            row_d["facility_category_labels"] = FACILITY_CATEGORY_LABELS

            # ── Lieu : POI à proximité + aéroports (distances calculées Haversine)
            row_d["pois_nearby"] = []
            row_d["airports"] = []
            if row_d.get("latitude") and row_d.get("longitude"):
                ck = _city_key(row_d.get("city") or row_d.get("country_code") or "")
                if ck:
                    row_d["pois_nearby"] = nearby_pois(
                        float(row_d["latitude"]), float(row_d["longitude"]), ck,
                        max_count=8, max_km=8.0,
                    )
                    row_d["airports"] = airports_nearby(
                        float(row_d["latitude"]), float(row_d["longitude"]), ck,
                    )

            # ── Gallery par catégorie HBX, TRIÉE par order pour respecter l'ordre officiel
            HBX_TYPE_MAP = {
                "HAB": "rooms", "RES": "restaurant", "BAR": "bar",
                "GEN": "general", "CON": "general", "COM": "general",
                "DEP": "outdoor", "TER": "outdoor",
            }
            # Trier les images par (order, visualOrder) avant de les ranger en catégories
            sorted_imgs = sorted(
                [i for i in raw_images if isinstance(i, dict) and i.get("path")],
                key=lambda i: (i.get("order", 999), i.get("visualOrder", 9999))
            )
            for img in sorted_imgs[:160]:
                url = f"https://photos.hotelbeds.com/giata/bigger/{img['path']}"
                cat = HBX_TYPE_MAP.get(img.get("imageTypeCode") or "", "other")
                gallery[cat].append(url)

            # Drop raw avant retour (trop volumineux)
            row_d.pop("raw", None)
            catalog_hotel = row_d

    if not destination:
        return JSONResponse({"error": "hotel_not_in_catalog",
                              "hint": "passe destination explicite"}, status_code=404)

    query = HotelQuery(
        destination=destination,
        check_in=check_in, check_out=check_out,
        guests=guests, rooms=rooms, stars_min=1,
    )
    providers = _get_active_hotel_providers()
    aggregated = search_hotels_multi(query, providers)

    # Filtre sur l'hôtel demandé
    target = None
    for h in aggregated:
        if giata_code and h.giata_code == str(giata_code):
            target = h; break
        if hotel_code and h.canonical_hotel_code == str(hotel_code):
            target = h; break

    if not target:
        # (RETIRÉ 2026-05-30) Plus de mock fallback : 0 offre réelle → réponse vide honnête, jamais inventé.
        return {
            "hotel": catalog_hotel,
            "gallery": gallery,
            "offers_by_provider": {},
            "best_offer": None,
            "providers_polled": [p.name for p in providers],
            "providers_with_offers": [],
            "total_offers": 0,
            "message": "Cet hôtel n'a aucune offre disponible aux dates demandées chez les providers actifs.",
        }

    offers_by_provider = {}
    for o in target.offers:
        offers_by_provider.setdefault(o.provider, []).append({
            "rate_key": o.provider_offer_id,
            "title": o.title,
            "price": o.price,
            "currency": o.currency,
            "board": o.details.get("board_name") or o.details.get("board"),
            "room_type": o.details.get("rate_class") or "",
            "cancellation": o.details.get("cancellation_policies", []),
        })
    # Tri prix asc dans chaque bucket
    for p in offers_by_provider:
        offers_by_provider[p].sort(key=lambda x: x["price"])

    return {
        "hotel": catalog_hotel or {
            "hotel_code": target.canonical_hotel_code,
            "giata_code": target.giata_code,
            "name": target.name,
            "stars": target.stars,
            "city": target.city,
            "country_code": target.country_code,
            "latitude": target.latitude,
            "longitude": target.longitude,
        },
        "gallery": gallery,
        "providers_polled": [p.name for p in providers],
        "providers_with_offers": target.providers,
        "best_offer": {
            "provider": target.best_offer.provider,
            "price": target.best_offer.price,
            "rate_key": target.best_offer.provider_offer_id,
            "currency": target.best_offer.currency,
        } if target.best_offer else None,
        "offers_by_provider": offers_by_provider,
        "total_offers": len(target.offers),
    }


@router.get("/hotels/{hotel_code}")
@limiter.limit("60/minute")
def get_hbx_hotel(request: Request, hotel_code: int, language: str = "ENG"):
    """Récupère les détails d'un hôtel HBX (pour fiche native AirBizness).

    Cache : TTL 7 jours en DB.
    """
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.hotels.content import get_hotel_details, extract_summary
        raw = get_hotel_details(hotel_code, language=language, use_cache=True)
        if not raw:
            raise HTTPException(404, f"Hotel {hotel_code} not found")
        return {"hotel": extract_summary(raw)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/hotels/{hotel_code}/availability")
@limiter.limit("60/minute")
def get_hbx_hotel_availability(request: Request,
    hotel_code: int,
    check_in: str = None,
    check_out: str = None,
    adults: int = 2,
    rooms_count: int = 1,
):
    """Disponibilités d'un hôtel pour des dates données.

    Re-appelle HBX search filtré sur 1 hôtel précis (par destination + code).
    Cache via hbx_search_cache.
    """
    if not check_in or not check_out:
        raise HTTPException(400, "check_in et check_out requis")
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.client import HbxClient
        from providers.hbx.hotels.mapper import hbx_response_to_offers

        # Payload search filtré sur 1 hôtel
        c = HbxClient(service="hotels")
        payload = {
            "stay": {"checkIn": check_in, "checkOut": check_out},
            "occupancies": [{"rooms": rooms_count, "adults": adults, "children": 0}],
            "hotels": {"hotel": [hotel_code]},
            "language": "FRA",
            "currency": "EUR",
        }
        raw = c.post("/hotel-api/1.0/hotels", json_body=payload)
        # expand_rooms=True : toutes les chambres × tous les régimes (pas seulement le best rate)
        offers = hbx_response_to_offers(raw, expand_rooms=True)

        # ── Récupère les photos HBX depuis le catalog (raw.images) pour mapping unifié
        main_photo = None
        gallery_photos: list = []
        room_photos: dict = {}
        try:
            _conn = psycopg2.connect(**DB_CONFIG)
            _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _cur.execute(
                "SELECT raw, main_image_url FROM hbx_hotels_catalog WHERE hotel_code = %s",
                (hotel_code,)
            )
            _row = _cur.fetchone()
            _cur.close(); _conn.close()
            if _row:
                _raw_cat = _row.get("raw")
                if isinstance(_raw_cat, str):
                    try: _raw_cat = json.loads(_raw_cat)
                    except Exception: _raw_cat = None
                _imgs = (_raw_cat or {}).get("images") if isinstance(_raw_cat, dict) else None
                main_photo = extract_best_main_photo(_imgs, "hbx") or _row.get("main_image_url")
                gallery_photos = extract_gallery_photos(_imgs, "hbx", max_photos=12)
                room_photos = extract_room_photos(_imgs, "hbx", max_per_room=5)
        except Exception:
            pass

        return {
            "hotel_code": hotel_code,
            "check_in": check_in, "check_out": check_out,
            "adults": adults, "rooms": rooms_count,
            "main_photo": main_photo,
            "gallery_photos": gallery_photos,
            "room_photos": room_photos,  # {room_code: [urls]}
            "rates": [{
                "rate_key": o.details.get("rate_key"),
                "price": o.price,
                "currency": o.currency,
                "room_code": o.details.get("room_code"),
                "room_name": o.details.get("room_name"),
                "board_code": o.details.get("board_code"),
                "board_name": o.details.get("board_name"),
                "rate_class": o.details.get("rate_class"),
                "cancellation_policies": o.details.get("cancellation_policies"),
                "net_price": o.details.get("net_price_hbx"),
                "adults": o.details.get("adults"),
                # Mapping direct : URL photo de cette chambre (ou null si pas dispo)
                "room_photo": (room_photos.get(o.details.get("room_code")) or [None])[0],
            } for o in offers],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/hotels/{hotel_code}/rooms")
def v2_hotel_rooms(hotel_code: int, check_in: str, check_out: str,
                    guests: int = 2, rooms: int = 1):
    """Toutes les chambres + tarifs dispo pour 1 hôtel à des dates précises.

    À utiliser sur la page détail (/quote.html) pour que l'utilisateur
    choisisse SA chambre.
    """
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")
    from providers.hbx.client import HbxClient
    from providers.hbx.hotels.mapper import hbx_response_to_offers
    from providers.hbx.hotels import cache_layer

    payload = {
        "stay": {"checkIn": check_in, "checkOut": check_out},
        "occupancies": [{"rooms": rooms, "adults": guests, "children": 0}],
        "hotels": {"hotel": [int(hotel_code)]},
    }

    cached = cache_layer.get_search_cache(payload) if hasattr(cache_layer, 'get_search_cache') else None
    raw = None
    hbx_failed = False
    if cached:
        raw = cached
    else:
        client = HbxClient(service="hotels")
        try:
            raw = client.post("/hotel-api/1.0/hotels", json_body=payload)
            try:
                if hasattr(cache_layer, 'set_search_cache'):
                    cache_layer.set_search_cache(payload, raw)
            except Exception:
                pass
        except Exception as e:
            # ── HBX 502 / quota / timeout → fallback MOCK si activé
            print(f"[v2_hotel_rooms] HBX live failed for {hotel_code}: {e}")
            hbx_failed = True
            return JSONResponse({"error": str(e), "hotel_code": hotel_code,
                                  "rooms": []}, status_code=502)

    # Expand toutes les rooms × rates
    offers = hbx_response_to_offers(raw, expand_rooms=True) if raw else []

    # ── Mapping photos depuis raw catalog HBX (room_code → photos)
    room_photos_map: dict = {}
    hotel_main_photo = None
    hotel_gallery: list = []
    try:
        _conn = psycopg2.connect(**DB_CONFIG)
        _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _cur.execute("SELECT raw, main_image_url FROM hbx_hotels_catalog WHERE hotel_code = %s",
                     (int(hotel_code),))
        _row = _cur.fetchone()
        _cur.close(); _conn.close()
        if _row:
            _raw = _row.get("raw")
            if isinstance(_raw, str):
                try: _raw = json.loads(_raw)
                except Exception: _raw = None
            _imgs = (_raw or {}).get("images") if isinstance(_raw, dict) else None
            room_photos_map = extract_room_photos(_imgs, "hbx", max_per_room=5)
            hotel_main_photo = extract_best_main_photo(_imgs, "hbx") or _row.get("main_image_url")
            hotel_gallery = extract_gallery_photos(_imgs, "hbx", max_photos=12)
    except Exception:
        pass

    # Group by room_name pour UX propre
    by_room: dict = {}
    for o in offers:
        d = o.details or {}
        room_name = d.get("room_name") or d.get("room_code") or "Chambre standard"
        room_code = d.get("room_code")
        bucket = by_room.setdefault(room_name, {
            "room_name": room_name,
            "room_code": room_code,
            # Décodage humain du room_code : DBL.DX-SU → "Chambre double Deluxe Superior"
            "room_description": describe_hbx_room(room_code, room_name),
            # Photos de cette chambre depuis HBX images (HAB tag avec roomCode)
            "photos": room_photos_map.get(room_code, []) if room_code else [],
            "main_photo": (room_photos_map.get(room_code, []) or [None])[0] if room_code else None,
            "rates": [],
            "cheapest_price": None,
        })
        cancel_info = format_cancellation_fr(d.get("cancellation_policies") or [])
        rate = {
            "rate_key": f"hbx:{o.provider_offer_id}",
            "price": o.price,
            "currency": o.currency,
            "board_code": d.get("board_code"),
            "board_name": board_label_fr(d.get("board_code") or "", d.get("board_name") or ""),
            "rate_class": d.get("rate_class"),
            "is_refundable": _is_rate_refundable(d.get("cancellation_policies") or []),
            "cancellation_policies": d.get("cancellation_policies") or [],
            "cancellation_label": cancel_info["label"],
            "cancellation_until_fr": cancel_info["until_date_fr"],
            "cancellation_is_free": cancel_info["is_free"],
            "payment_type": "Par carte",  # via Stripe (3DS supporté)
            "adults": d.get("adults"),
            "children": d.get("children"),
        }
        bucket["rates"].append(rate)
        if bucket["cheapest_price"] is None or o.price < bucket["cheapest_price"]:
            bucket["cheapest_price"] = o.price

    rooms_list = list(by_room.values())
    for r in rooms_list:
        r["rates"].sort(key=lambda x: x["price"])
    rooms_list.sort(key=lambda r: r["cheapest_price"] or 0)

    # (RETIRÉ 2026-05-29) Plus de chambres inventées : 0 chambre réelle → liste vide.

    return {
        "hotel_code": hotel_code,
        "check_in": check_in,
        "check_out": check_out,
        "guests": guests,
        "hotel_main_photo": hotel_main_photo,
        "hotel_gallery_photos": hotel_gallery,
        "rooms": rooms_list,
        "total_rooms": len(rooms_list),
        "total_rates": sum(len(r["rates"]) for r in rooms_list),
        "is_mock": False,
    }


def _is_rate_refundable(policies: list) -> bool:
    """Un rate est annulable gratuit si une policy a amount=0 OU from > maintenant+24h."""
    if not policies:
        return True  # pas de policy = annulation flexible
    import datetime as _dt
    now_24h = _dt.datetime.now() + _dt.timedelta(hours=24)
    for p in policies:
        try:
            amt = float(p.get("amount", 0) or 0)
            if amt == 0:
                return True
            from_str = p.get("from")
            if from_str:
                fr = _dt.datetime.fromisoformat(from_str.replace("Z", "+00:00").split("+")[0])
                if fr > now_24h:
                    return True
        except Exception:
            continue
    return False


def _quote_via_checkrate(rate_key: str, provider_name: str, hotel_code, check_in, check_out, guests, rooms):
    """Fastpath quote : à partir d'un rate_key déjà obtenu par le listing,
    on checkrate directement (pas de re-search → pas de divergence).
    Construit la réponse au MÊME format que la quote search-based pour que le
    front quote.html n'ait rien à changer côté rendering."""
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")

    # 1) Strip préfixe provider si présent (rate_key issu de l'aggregator : "hbx:xxx")
    native_rk = rate_key.removeprefix("hbx:") if rate_key.startswith("hbx:") else rate_key

    # 2) Checkrate
    try:
        from providers.hbx.hotels.checkrate import checkrate
        from providers.hbx import config as hbx_config
        v = checkrate(native_rk)
        pricing = hbx_config.PRICING["hotels"]
        net = v["net"]
        gross = round(net * (1 + pricing["margin_pct"]) * (1 + pricing["vat_pct"]), 2)
    except Exception as e:
        return JSONResponse({"error": f"checkrate failed: {e}"}, status_code=502)

    # 3) Catalog hotel (photos, address, POI, gallery) — même bloc qu'en mode search
    catalog_hotel = None
    gallery = {"rooms": [], "general": [], "restaurant": [], "bar": [], "outdoor": [], "other": []}
    if hotel_code:
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT hotel_code, giata_code, name, destination_code, city, country_code,
                       category_stars, latitude, longitude, main_image_url, address,
                       email, phone_main, web, images_count, facilities_count,
                       LEFT(description_en, 500) AS description, raw
                FROM hbx_hotels_catalog WHERE hotel_code = %s
            """, (int(hotel_code),))
            row = cur.fetchone()
            cur.close(); conn.close()
            if row:
                row_d = dict(row)
                _raw = row_d.get("raw")
                if isinstance(_raw, str):
                    try: _raw = json.loads(_raw)
                    except Exception: _raw = None
                raw_images = (_raw or {}).get("images") or [] if isinstance(_raw, dict) else []
                best_main = extract_best_main_photo(raw_images, provider="hbx")
                if best_main:
                    row_d["main_image_url"] = best_main
                row_d["facilities_by_category"] = extract_facilities_fr(
                    (_raw or {}).get("facilities") if isinstance(_raw, dict) else None,
                    provider="hbx",
                )
                row_d["facility_category_labels"] = FACILITY_CATEGORY_LABELS
                row_d["pois_nearby"] = []
                row_d["airports"] = []
                if row_d.get("latitude") and row_d.get("longitude"):
                    ck = _city_key(row_d.get("city") or row_d.get("country_code") or "")
                    if ck:
                        row_d["pois_nearby"] = nearby_pois(
                            float(row_d["latitude"]), float(row_d["longitude"]), ck,
                            max_count=8, max_km=8.0,
                        )
                        row_d["airports"] = airports_nearby(
                            float(row_d["latitude"]), float(row_d["longitude"]), ck,
                        )
                HBX_TYPE_MAP = {"HAB": "rooms", "RES": "restaurant", "BAR": "bar",
                                "GEN": "general", "CON": "general", "COM": "general",
                                "DEP": "outdoor", "TER": "outdoor"}
                sorted_imgs = sorted(
                    [i for i in raw_images if isinstance(i, dict) and i.get("path")],
                    key=lambda i: (i.get("order", 999), i.get("visualOrder", 9999))
                )
                for img in sorted_imgs[:160]:
                    url = f"https://photos.hotelbeds.com/giata/bigger/{img['path']}"
                    cat = HBX_TYPE_MAP.get(img.get("imageTypeCode") or "", "other")
                    gallery[cat].append(url)
                row_d.pop("raw", None)
                catalog_hotel = row_d
        except Exception as e:
            print(f"[quote-fastpath] catalog lookup KO hotel_code={hotel_code}: {e}")

    # 4) Réponse au MÊME format que la quote search-based
    offer_dict = {
        "rate_key": rate_key,
        "title": v.get("room_name") or v.get("hotel_name") or "Tarif",
        "price": gross,
        "currency": v.get("currency") or "EUR",
        "board": v.get("board_name"),
        "room_type": v.get("rate_class"),
        "cancellation": v.get("cancellation_policies", []),
    }
    return {
        "hotel": catalog_hotel or {"hotel_code": int(hotel_code) if hotel_code else None,
                                    "name": v.get("hotel_name") or "Hôtel"},
        "gallery": gallery,
        "providers_polled": [provider_name],
        "providers_with_offers": [provider_name],
        "best_offer": {
            "provider": provider_name,
            "price": gross,
            "rate_key": rate_key,
            "currency": v.get("currency") or "EUR",
        },
        "offers_by_provider": {provider_name: [offer_dict]},
        "total_offers": 1,
        "via_fastpath": True,
    }

