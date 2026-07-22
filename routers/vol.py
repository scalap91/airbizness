"""Module VOL — offre vol (recherche / services / plan de cabine).

Provider-agnostique : interroge les providers vol allumés du catalogue et
normalise (UnifiedOffer, SeatMap). Le client cherche et choisit lui-même ;
le module ne décide rien. PAIEMENT et RÉSERVATION sont des modules à part.

NB migration : routes encore nommées /duffel/* (héritage) — à rendre
provider-agnostiques (offer_services / seat_map) quand tout le module sera sorti.
"""
import json
import psycopg2, psycopg2.extras
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from main import (limiter, DB_CONFIG, alert_conciergerie, _alert_telegram,
                  _fetch_or_cache_offers, _slice_signature,
                  _normalize_fare_services, _classify_fare_brand)
from services.api_cache import cached, cached_async  # Pascal 2026-05-31 : cache court

router = APIRouter()


@router.get("/duffel/offer_with_services/{offer_id}")
@limiter.limit("60/minute")
def duffel_offer_with_services(request: Request, offer_id: str):
    """GET /air/offers/{id}?return_available_services=true reformatté pour le front.

    Retourne les available_services Duffel regroupés par type :
      - baggage : bagages soute additionnels (svc.metadata contient maximum_weight_kg)
      - seat    : sièges achetables (parfois aussi via seat_maps endpoint)
      - cancel_for_any_reason : assurance flex

    Le front DOIT utiliser le `id` retourné (ase_xxx) quand le client choisit
    une option ; cet id sera renvoyé à create_order pour que le billet contienne
    l'option.
    """
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")
    try:
        from providers.duffel import get_offer_live
        offer = get_offer_live(offer_id, with_services=True)
    except Exception as e:
        raise HTTPException(404, f"Offer {offer_id} not found: {e}")

    services_by_type: dict[str, list[dict]] = {
        "baggage": [], "seat": [], "cancel_for_any_reason": []
    }
    for svc in offer.get("available_services", []) or []:
        svc_type = svc.get("type")
        if svc_type in services_by_type:
            try:
                amount = float(svc.get("total_amount", 0) or 0)
            except (TypeError, ValueError):
                amount = 0.0
            services_by_type[svc_type].append({
                "id": svc.get("id"),
                "type": svc_type,
                "total_amount": amount,
                "total_currency": svc.get("total_currency", "EUR"),
                "maximum_quantity": svc.get("maximum_quantity", 1),
                "segment_ids": svc.get("segment_ids", []),
                "passenger_ids": svc.get("passenger_ids", []),
                "metadata": svc.get("metadata", {}),  # type bagage etc.
            })

    return {
        "offer_id": offer_id,
        "total_amount": offer.get("total_amount"),
        "total_currency": offer.get("total_currency"),
        "slices": offer.get("slices"),
        "passengers": offer.get("passengers"),
        "available_services": services_by_type,
        "expires_at": offer.get("expires_at"),
        # Audit 2026-05-27 sev 4 #28 : flag passeport requis pour vols intl.
        # Le front l'utilise pour rendre les champs passeport obligatoires.
        "passenger_identity_documents_required": bool(
            offer.get("passenger_identity_documents_required")
        ),
    }


@router.get("/duffel/seat_maps")
@limiter.limit("60/minute")
def duffel_seat_maps(request: Request, offer_id: str):
    """Retourne le plan de cabine RÉEL Duffel (canonique SeatMap) pour un offer_id.

    Duffel indisponible ou sans cabine → cabins:[] (jamais de plan inventé).
    """
    oid = (offer_id or "").strip()
    if not oid:
        raise HTTPException(400, "offer_id requis")
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        try:
            from providers.duffel import get_seat_maps as _duffel_seat_maps  # type: ignore
        except ImportError:
            _duffel_seat_maps = None
        if _duffel_seat_maps:
            res = _duffel_seat_maps(oid)
            if res and res.get("cabins"):
                return res
    except Exception as e:
        print(f"[seat_maps] Duffel fail {oid}: {e}")
    return {"offer_id": oid, "cabins": []}


@router.get("/deals/averages")
def get_averages():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT destination,
               ROUND(AVG(price)) as avg_price,
               ROUND(MAX(price)) as max_price
        FROM deals
        GROUP BY destination
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["destination"]: int(r["avg_price"]) for r in rows}


@router.get("/deals/diverse")
def get_diverse_deals(origin: str = "CDG", limit: int = 20):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (d.destination) d.*
        FROM deals d
        LEFT JOIN route_stats rs ON d.origin=rs.origin AND d.destination=rs.destination
        WHERE d.origin = %s
        AND d.expires_at > NOW()
        AND (rs.max_price IS NULL OR d.price <= rs.max_price * 0.5)
        ORDER BY d.destination, d.score_deal DESC
        LIMIT %s
    """, (origin.upper(), limit))
    deals = [dict(d) for d in cur.fetchall()]

    # Fallback si pas assez de deals locaux
    if len(deals) < 3:
        cur.execute("""
            SELECT DISTINCT ON (d.destination) d.*
            FROM deals d
            LEFT JOIN route_stats rs ON d.origin=rs.origin AND d.destination=rs.destination
            WHERE d.expires_at > NOW()
            AND (rs.max_price IS NULL OR d.price <= rs.max_price * 0.5)
            ORDER BY d.destination, d.score_deal DESC
            LIMIT %s
        """, (limit,))
        deals = [dict(d) for d in cur.fetchall()]

    cur.close()
    conn.close()
    return {"deals": deals}


@router.get("/deals/by-id")
def get_deal_by_id(offer_id: str):
    """Récupère 1 deal par offer_id.

    Source unique 2026-05-30 : cherche dans la table `deals` (historique daemon)
    PUIS dans `flight_offers_cache` (les offres live retournées par /deals datée).
    Sans ce 2e étage, le banner "offre sélectionnée" du front serait cassé pour
    toute offre venant d'une recherche datée (qui ne persiste plus dans `deals`).
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # 1) Table deals historique (daemon)
    cur.execute("SELECT * FROM deals WHERE offer_id = %s LIMIT 1", (offer_id,))
    deal = cur.fetchone()
    if deal:
        cur.close(); conn.close()
        return {"deals": [dict(deal)]}
    # 2) Fallback : scan flight_offers_cache (offers JSONB) — même source que le bandeau
    try:
        import json as _json
        cur.execute(
            "SELECT offers FROM flight_offers_cache "
            "WHERE offers @> %s::jsonb AND expires_at > NOW() LIMIT 1",
            (_json.dumps([{"id": offer_id}]),),
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return {"deals": []}
        offers_list = row["offers"] or []
        if isinstance(offers_list, str):
            offers_list = _json.loads(offers_list)
        # Trouve l'offre matching dans la liste
        offer = next((o for o in offers_list if o.get("id") == offer_id), None)
        if not offer:
            return {"deals": []}
        # Map Duffel offer → format deal compatible front (même mapping que get_deals)
        slices = offer.get("slices") or []
        seg0 = (slices[0].get("segments") or [{}])[0] if slices else {}
        segN = (slices[0].get("segments") or [{}])[-1] if slices else {}
        airline = (offer.get("owner") or {}).get("name") or seg0.get("marketing_carrier", {}).get("name", "")
        airline_code = (offer.get("owner") or {}).get("iata_code") or seg0.get("marketing_carrier", {}).get("iata_code", "")
        duration_iso = (slices[0].get("duration") if slices else "") or ""
        dur_min = 0
        try:
            import re as _re
            h = _re.search(r"(\d+)H", duration_iso); m = _re.search(r"(\d+)M", duration_iso)
            dur_min = (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
        except Exception:
            pass
        stops = max(0, len((slices[0].get("segments") if slices else []) or []) - 1) if slices else None
        deal_out = {
            "offer_id": offer.get("id"),
            "origin": (seg0.get("origin") or {}).get("iata_code") or "",
            "destination": (segN.get("destination") or {}).get("iata_code") or "",
            "airline_name": airline,
            "airline_code": airline_code,
            "price": float(offer.get("total_amount") or 0),
            "currency": offer.get("total_currency") or "EUR",
            "cabin_class": ((slices[0].get("segments") or [{}])[0].get("passengers") or [{}])[0].get("cabin_class") if slices else None,
            "departure_at": seg0.get("departing_at"),
            "arrival_at": segN.get("arriving_at"),
            "duration_minutes": dur_min,
            "stops": stops,
            "fare_brand": offer.get("fare_brand_name") or "",
            "expires_at": offer.get("expires_at"),

        }
        return {"deals": [deal_out]}
    except Exception as e:
        print(f"[deals/by-id] flight_offers_cache lookup KO: {e}")
        return {"deals": []}


@router.get("/duffel/refresh_offer/{cache_offer_id}")
@limiter.limit("30/minute")
def duffel_refresh_offer(request: Request, cache_offer_id: str,
                          passengers: int = 1, max_drift_pct: float = 30.0):
    """Reprend un offer_id du cache `deals` (potentiellement expiré 15-30min)
    et fait un nouveau search live Duffel pour obtenir un offer bookable.

    Retourne :
      - live_offer_id, live_price, currency, expires_at, passenger_ids
      - cached_price, price_drift_pct (informatif)
      - status : 'ok' si drift <= max_drift_pct, 'price_blocked' sinon
      - alternatives[] si price_blocked (top 3 offers proches du cache)
    """
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.duffel import refresh_offer_from_cache, search_offers_live

        # Charge le deal du cache
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT offer_id, origin, destination, airline_name, airline_code,
                   price, currency, cabin_class, departure_at, duration_minutes,
                   stops
            FROM deals WHERE offer_id = %s LIMIT 1
        """, (cache_offer_id,))
        deal = cur.fetchone()
        cur.close(); conn.close()
        if not deal:
            raise HTTPException(404, "offer_id introuvable dans le cache")
        deal_d = dict(deal)
        deal_d["departure_at"] = deal_d["departure_at"].isoformat() if deal_d["departure_at"] else None
        deal_d["price"] = float(deal_d["price"])

        # Refresh live
        res = refresh_offer_from_cache(deal_d, max_price_drift_pct=max_drift_pct)
        if not res:
            return {
                "status": "not_found",
                "message": "Aucun vol équivalent trouvé sur le marché. "
                            "L'offre originale n'est plus disponible.",
                "cache_offer_id": cache_offer_id,
                "alternatives": [],
            }

        # Si drift > max → marquer price_blocked et proposer alternatives
        if res["price_drifted"]:
            # Récupère top 3 offers live triées par prix pour proposer alternatives
            try:
                live_offers = search_offers_live(
                    deal_d["origin"], deal_d["destination"],
                    str(deal_d["departure_at"])[:10],
                    passengers=passengers,
                    cabin_class=deal_d.get("cabin_class") or "business",
                )
                alts = []
                for o in sorted(live_offers, key=lambda x: float(x.get("total_amount", 99999)))[:3]:
                    slices = o.get("slices", [])
                    seg = (slices[0].get("segments") or [{}])[0] if slices else {}
                    alts.append({
                        "live_offer_id": o["id"],
                        "price": float(o.get("total_amount", 0)),
                        "currency": o.get("total_currency", "EUR"),
                        "owner_name": o.get("owner", {}).get("name"),
                        "owner_code": o.get("owner", {}).get("iata_code"),
                        "departing_at": seg.get("departing_at"),
                        "arriving_at": seg.get("arriving_at"),
                        "expires_at": o.get("expires_at"),
                        "passenger_ids": [p["id"] for p in o.get("passengers", [])],
                    })
            except Exception:
                alts = []
            # Log alerte conciergerie (drift important = signal qualité)
            alert_conciergerie(
                airbizness_ref=cache_offer_id,
                severity="info",
                alert_type="price_drift_high",
                payload={"cache_price": res["cached_price"], "live_price": res["live_price"],
                          "drift_pct": res["price_drift_pct"]},
            )
            return {
                "status": "price_blocked",
                "message": f"Tarif live {res['live_price']:.0f}€ vs cache {res['cached_price']:.0f}€ "
                            f"(+{res['price_drift_pct']}%). Trop d'écart, choisissez une alternative.",
                "cached_price": res["cached_price"],
                "live_price": res["live_price"],
                "price_drift_pct": res["price_drift_pct"],
                "alternatives": alts,
            }

        # OK : drift acceptable, on retourne le live offer pour booking
        seg0 = (res["slices"][0].get("segments") or [{}])[0] if res["slices"] else {}
        return {
            "status": "ok",
            "live_offer_id": res["live_offer_id"],
            "live_price": res["live_price"],
            "currency": res["currency"],
            "cached_price": res["cached_price"],
            "price_drift_pct": res["price_drift_pct"],
            "expires_at": res["expires_at"],
            "passenger_ids": res["passenger_ids"],
            "owner_name": (res["owner"] or {}).get("name"),
            "owner_code": (res["owner"] or {}).get("iata_code"),
            "departing_at": seg0.get("departing_at"),
            "arriving_at": seg0.get("arriving_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"refresh_offer failed: {e}")


@router.get("/deals")
@limiter.limit("60/minute")
def get_deals(request: Request,
    origin: str = None,
    destination: str = None,
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    from_date: str = None,   # alias frontend rétrocompat (Pascal 2026-05-24)
    to_date: str = None,     # alias frontend rétrocompat (Pascal 2026-05-24)
    trip_type: str = "one_way",
    cabin_class: str = "economy",  # 2026-05-27 fix Pascal: filtre cabin obligatoire (cache contenait que du business)
    passengers: int = 1,             # 2026-05-27 fix Pascal: passé au fallback Duffel live
    # Audit 2026-05-27 sev 4 #8 : support child/infant côté API search.
    # Le front (index.html) passe déjà adults/children/infants en URL params.
    adults: int = 0,
    children: int = 0,
    infants: int = 0,
    limit: int = 20
):
    # Audit 2026-05-27 sev 4 #8 : si adults/children/infants fournis, override
    # le `passengers` legacy (somme des trois). Backend duffel `passenger_objs`
    # géré dans `_fetch_or_cache_offers`/`search_offers_live`.
    if adults or children or infants:
        passengers = max(1, (adults or 0) + (children or 0) + (infants or 0))
    # ── Normalize aliases (frontend envoie from_date/to_date, on accepte les 2 formes)
    if not date_from and from_date:
        date_from = from_date
    if not date_to and to_date:
        date_to = to_date

    # ────────────────────────────────────────────────────────────────────
    # SOURCE UNIQUE 2026-05-30 (fix Pascal "10000 fois quon fix sa") :
    # une recherche DATÉE (origin+destination+date|date_from) ne lit PLUS
    # la table `deals` historique. Elle appelle EXACTEMENT _fetch_or_cache_offers
    # — la même fonction que /flights/price-calendar. Donc bandeau et liste
    # voient pile la même chose : si Duffel répond 0, les deux affichent 0 ;
    # si Duffel répond N, les deux affichent N. Plus de divergence.
    # La table `deals` historique sert UNIQUEMENT aux requêtes non-datées
    # (home/marketing/alertes — pas de date ciblée).
    # ────────────────────────────────────────────────────────────────────
    deals: list = []
    is_dated_search = bool(origin and destination and (date or date_from))

    if is_dated_search:
        live_date = date or date_from
        try:
            live_offers_all, _min_price, _count = _fetch_or_cache_offers(
                origin=origin, destination=destination, departure_date=live_date,
                passengers=passengers or 1, cabin_class=cabin_class,
            )
            for o in (live_offers_all or [])[:limit]:
                slices = o.get("slices") or []
                seg0 = (slices[0].get("segments") or [{}])[0] if slices else {}
                segN = (slices[0].get("segments") or [{}])[-1] if slices else {}
                airline = (o.get("owner") or {}).get("name") or seg0.get("marketing_carrier", {}).get("name", "")
                airline_code = (o.get("owner") or {}).get("iata_code") or seg0.get("marketing_carrier", {}).get("iata_code", "")
                duration_iso = (slices[0].get("duration") if slices else "") or ""
                dur_min = 0
                try:
                    import re as _re
                    h = _re.search(r"(\d+)H", duration_iso); m = _re.search(r"(\d+)M", duration_iso)
                    dur_min = (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
                except Exception:
                    pass
                stops_live = None
                try:
                    segs0 = (slices[0].get("segments") if slices else None) or []
                    if segs0:
                        stops_live = max(0, len(segs0) - 1)
                except Exception:
                    stops_live = None
                deals.append({
                    "offer_id": o.get("id"),
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "airline_name": airline,
                    "airline_code": airline_code,
                    "price": float(o.get("total_amount") or 0),
                    "currency": o.get("total_currency") or "EUR",
                    "cabin_class": cabin_class.lower(),
                    "departure_at": seg0.get("departing_at"),
                    "arrival_at": segN.get("arriving_at"),
                    "duration_minutes": dur_min,
                    "stops": stops_live,
                    "fare_brand": o.get("fare_brand_name") or "",
                    "expires_at": o.get("expires_at"),



                })
            print(f"[deals] dated search {origin}->{destination} {live_date} cabin={cabin_class} : {len(deals)} offres (source unique _fetch_or_cache_offers)")
        except Exception as _e:
            # Si la source unique tombe, on renvoie 0 (PAS de fallback table historique
            # qui ferait diverger avec le bandeau). Le bandeau affichera aussi 0/erreur.
            print(f"[deals] source unique KO {origin}->{destination}: {_e} -> 0 offre (cohérent avec bandeau)")
    else:
        # ── Requête non-datée : table `deals` historique (home/marketing/alertes)
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = "SELECT * FROM deals WHERE 1=1"
        params: list = []
        if origin:
            query += " AND origin = %s"; params.append(origin.upper())
        if destination:
            query += " AND destination = %s"; params.append(destination.upper())
        if cabin_class:
            query += " AND cabin_class = %s"; params.append(cabin_class.lower())
        query += " AND expires_at > NOW() ORDER BY score_deal DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        deals = list(cur.fetchall())
        cur.close()
        conn.close()

    # Sérialise les deals Duffel + ajoute marker provider/source
    deals_out = []
    for d in deals:
        item = dict(d)
        item["provider"] = "duffel"
        item["source"] = "ota"           # bookable directement (pas affiliate)
        item["deeplink_url"] = None
        deals_out.append(item)

    # === PROVIDERS AFFILIÉS (2026-05-22) — TravelPayouts & Co ===
    # Best-effort : si un provider plante, on continue avec les deals DB.
    # Chaque offre affiliée a `deeplink_url` et `source='affiliate'`.
    if origin and destination:
        try:
            import sys as _sys
            if "/var/www/airbizness" not in _sys.path:
                _sys.path.insert(0, "/var/www/airbizness")
            from providers.base import FlightQuery
            from providers.registry import search_flights
            # Date à utiliser : si dispo, sinon date_from sinon J+30 par défaut
            dep = date or date_from
            if not dep:
                from datetime import datetime as _dt, timedelta as _td
                dep = (_dt.utcnow() + _td(days=30)).strftime("%Y-%m-%d")
            q = FlightQuery(
                origin=origin.upper(), destination=destination.upper(),
                departure_date=dep,
            )
            for o in search_flights(q):
                # Skip Duffel (déjà couvert par DB lookup)
                if o.provider == "duffel":
                    continue
                deals_out.append({
                    "provider": o.provider,
                    "source": "affiliate" if o.is_affiliate else "ota",
                    "offer_id": o.provider_offer_id,
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "airline_name": (o.details or {}).get("airline", ""),
                    "airline_code": (o.details or {}).get("airline", ""),
                    "price": o.price,
                    "currency": o.currency,
                    "cabin_class": None,
                    "departure_at": (o.details or {}).get("departure_at"),
                    "duration_minutes": (o.details or {}).get("duration_minutes"),
                    "stops": (o.details or {}).get("transfers", 0),
                    "deeplink_url": o.deeplink_url,
                    "_title": o.title,
                })
        except Exception as _e:
            # Log mais ne bloque jamais la réponse
            print(f"[/deals] providers merge non-fatal: {_e}")

    # Re-trie par prix croissant et applique le limit final
    deals_out.sort(key=lambda x: x.get("price") or 9999)

    # (RETIRÉ 2026-05-29) Plus de génération de faux vols. Si aucune offre réelle,
    # deals_out reste vide → le front affiche « aucun vol disponible », jamais un faux.

    # ── ROUND-TRIP (Pascal 2026-05-24) ──
    # Pour un pack vol+hôtel, on génère AUSSI la liste retour (inbound) si trip_type=round_trip.
    # Renvoyée séparément dans "inbound" pour que le frontend affiche 2 sections.
    inbound_out: list = []
    is_round_trip = (trip_type or "").lower() in ("round_trip", "roundtrip", "round-trip")
    if is_round_trip and origin and destination and date_to:
        # (RETIRÉ 2026-05-29) Plus de faux vols retour. Vraie recherche inbound à brancher.
        inbound_out = []

    return {
        "deals": deals_out[:limit],
        "inbound": inbound_out[:limit] if is_round_trip else [],
        "trip_type": "round_trip" if is_round_trip else "one_way",
        "date_from": date or date_from,
        "date_to": date_to,
    }


@router.get("/deals/calendar")
def get_calendar(origin: str = "CDG", destination: str = None):
    """Retourne les meilleurs prix par date pour afficher le calendrier"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    query = """
        SELECT DATE(departure_at) as date,
               MIN(price) as min_price,
               COUNT(*) as nb_vols
        FROM deals
        WHERE origin = %s
        AND departure_at IS NOT NULL
    """
    params = [origin.upper()]
    if destination:
        query += " AND destination = %s"
        params.append(destination.upper())
    query += " GROUP BY DATE(departure_at) ORDER BY date ASC"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"calendar": [dict(r) for r in rows]}


@router.get("/flights/price-calendar")
@limiter.limit("30/minute")
async def flight_price_calendar(
    request: Request,
    from_: str = None,
    to: str = None,
    date: str = None,
    pax: int = 1,
    cabin: str = "business",
    days: int = 3,
):
    """Bandeau prix ±days jours autour de `date` pour la route from→to.

    Params (alias frontend `from` capturé via Query alias) :
      from  : IATA origine (CDG)
      to    : IATA destination (JFK)
      date  : date de référence YYYY-MM-DD (jour sélectionné)
      pax   : nombre de passagers (1 par défaut)
      cabin : cabin_class Duffel (economy/premium_economy/business/first)
      days  : N jours de chaque côté (3 par défaut → 7 cellules)

    Note : FastAPI ne permet pas `from` comme param Python → on lit la query
    via request.query_params en bypass quand from_ est None.
    """
    import asyncio as _asyncio

    # FastAPI param shadowing : `from` est mot-clé Python, lecture brute query
    qp = request.query_params
    origin = (from_ or qp.get("from") or "").upper().strip()
    destination = (to or qp.get("to") or "").upper().strip()
    ref_date_str = (date or qp.get("date") or "").strip()
    try:
        pax_n = int(pax or qp.get("pax") or 1)
    except (TypeError, ValueError):
        pax_n = 1
    cabin_norm = (cabin or qp.get("cabin") or "business").lower().strip()
    try:
        days_n = int(days or qp.get("days") or 3)
    except (TypeError, ValueError):
        days_n = 3
    days_n = max(1, min(days_n, 5))  # clamp 1..5 (max 11 cellules)

    if not origin or not destination or not ref_date_str:
        raise HTTPException(status_code=400, detail="from, to, date required")

    try:
        ref_dt = datetime.strptime(ref_date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    # ─── 1) Build list of dates (J-days .. J+days) ────────────────
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    target_dates = []
    for delta in range(-days_n, days_n + 1):
        d = ref_dt + timedelta(days=delta)
        d_str = d.strftime("%Y-%m-%d")
        # Skip past dates (Duffel renvoie une erreur pour date passée)
        if d_str < today_str:
            target_dates.append((d_str, True))  # tagged as past
        else:
            target_dates.append((d_str, False))

    # ─── 2) Fetch all dates in parallel (asyncio.to_thread wrapping sync urllib) ──
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.duffel import search_offers_live, DuffelHttpError
    except Exception as e:
        _alert_telegram(f"price-calendar: import providers.duffel KO : {e}")
        raise HTTPException(status_code=500, detail=f"duffel import: {e}")

    # Throttle : Duffel limite à ~5 req parallèles → semaphore 3 pour rester safe
    _sem = _asyncio.Semaphore(3)

    async def _fetch_one(d_str: str, is_past: bool) -> dict:
        if is_past:
            return {"date": d_str, "min_price": None, "offers_count": 0,
                    "loading": False, "error": "past"}
        async with _sem:
            return await _fetch_one_inner(d_str)

    async def _fetch_one_inner(d_str: str) -> dict:
        try:
            # 2026-05-27 cache unifié : passe par _fetch_or_cache_offers (même source que /deals)
            offers, min_price, offers_count = await _asyncio.to_thread(
                _fetch_or_cache_offers,
                origin, destination, d_str, pax_n, cabin_norm,
            )
            if not offers or offers_count == 0:
                return {"date": d_str, "min_price": None, "offers_count": 0,
                        "loading": False, "error": "no_offers"}
            return {
                "date": d_str,
                "min_price": min_price,
                "offers_count": offers_count,
                "loading": False,
                "error": None,
            }
        except DuffelHttpError as e:
            return {"date": d_str, "min_price": None, "offers_count": 0,
                    "loading": False, "error": f"duffel_{e.status}"}
        except Exception as e:
            return {"date": d_str, "min_price": None, "offers_count": 0,
                    "loading": False, "error": str(e)[:80]}

    cells = await _asyncio.gather(*[_fetch_one(d, p) for d, p in target_dates])
    cells_list = list(cells)

    # ─── 3) Cheapest date ─────────────────────────────────────────
    valid = [c for c in cells_list if c.get("min_price")]
    cheapest_date = None
    if valid:
        cheapest_date = min(valid, key=lambda c: c["min_price"])["date"]

    # ─── 4) Watchdog Telegram si >50% en erreur ──────────────────
    errors_count = sum(1 for c in cells_list if c.get("error") and c.get("error") != "past")
    eligible = sum(1 for c in cells_list if c.get("error") != "past")
    if eligible > 0 and (errors_count / eligible) > 0.5:
        _alert_telegram(
            f"price-calendar dégradé : {errors_count}/{eligible} cellules en erreur "
            f"({origin}→{destination} {ref_date_str} pax={pax_n} cabin={cabin_norm})"
        )

    payload = {
        "ref_date": ref_date_str,
        "from": origin,
        "to": destination,
        "pax": pax_n,
        "cabin": cabin_norm,
        "currency": "EUR",
        "cells": cells_list,
        "cheapest_date": cheapest_date,
        "cached": False,
    }

    return payload


# ═══════════════════════════════════════════════════════════════════════
# Price Calendar MOIS ENTIER (popup flex-dates AirFrance) — Pascal 2026-05-27
# Endpoint : GET /flights/price-month?from=ORY&to=NCE&month=2026-07&pax=1&cabin=economy
# Renvoie tous les jours du mois avec min_price par jour (Duffel).
# - Cache PG 24h (table flight_price_calendar_cache, clé "month:from:to:Y-M:pax:cabin")
# - Semaphore(4) pour préserver Duffel
# - Watchdog Telegram si > 30% des jours en erreur (plus permissif que ±3j car 28-31 calls)
# - Timeout global 25s (sinon abort, retour partiel autorisé)
# - Skip dates passées
# - Coût Duffel : 28-31 recherches par initial load → cache OBLIGATOIRE 24h
# ═══════════════════════════════════════════════════════════════════════
@router.get("/flights/price-month")
@limiter.limit("15/minute")
async def flight_price_month(
    request: Request,
    from_: str = None,
    to: str = None,
    month: str = None,
    pax: int = 1,
    cabin: str = "economy",
):
    """Calendrier prix mois entier (popup flex-dates).

    Params :
      from  : IATA origine (CDG / ORY)
      to    : IATA destination (JFK / NCE)
      month : "YYYY-MM" (2026-07)
      pax   : nombre de passagers (1 par défaut)
      cabin : cabin_class Duffel (economy/premium_economy/business/first)
    """
    import asyncio as _asyncio
    import calendar as _calendar

    qp = request.query_params
    origin = (from_ or qp.get("from") or "").upper().strip()
    destination = (to or qp.get("to") or "").upper().strip()
    month_str = (month or qp.get("month") or "").strip()
    try:
        pax_n = int(pax or qp.get("pax") or 1)
    except (TypeError, ValueError):
        pax_n = 1
    cabin_norm = (cabin or qp.get("cabin") or "economy").lower().strip()

    if not origin or not destination or not month_str:
        raise HTTPException(status_code=400, detail="from, to, month required")

    # Parse month "YYYY-MM"
    try:
        y_str, m_str = month_str.split("-")
        year = int(y_str)
        mnum = int(m_str)
        if mnum < 1 or mnum > 12:
            raise ValueError("month out of range")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    # Refuse les mois trop loin dans le futur (>11 mois) — Duffel ne supporte pas
    today = datetime.utcnow()
    today_str = today.strftime("%Y-%m-%d")
    try:
        first_of_month = datetime(year, mnum, 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid month")
    diff_months = (year - today.year) * 12 + (mnum - today.month)
    if diff_months > 11 or diff_months < -1:
        raise HTTPException(status_code=400, detail="month out of acceptable range (-1..+11)")

    # ─── 1) Build list of dates for the month (skip past) ─────────
    last_day = _calendar.monthrange(year, mnum)[1]
    target_dates = []  # list of (date_str, is_past)
    for day in range(1, last_day + 1):
        d_str = f"{year:04d}-{mnum:02d}-{day:02d}"
        is_past = d_str < today_str
        target_dates.append((d_str, is_past))

    # ─── 3) Fetch eligible dates via Duffel ───────────────────────
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.duffel import search_offers_live, DuffelHttpError
    except Exception as e:
        _alert_telegram(f"price-month: import providers.duffel KO : {e}")
        raise HTTPException(status_code=500, detail=f"duffel import: {e}")

    # Semaphore 2 + retries jittered 429 (Duffel test mode strict).
    # Initial cache miss : 25-40s typique. Cache hit subséquent : <100ms.
    _sem = _asyncio.Semaphore(2)
    import random as _rand

    async def _fetch_one(d_str: str, is_past: bool) -> dict:
        if is_past:
            return {"date": d_str, "min_price": None, "offers_count": 0, "error": "past"}
        async with _sem:
            return await _fetch_one_inner(d_str)

    async def _fetch_one_inner(d_str: str) -> dict:
        # 2026-05-27 fix Pascal : passe par _fetch_or_cache_offers (même source que /deals + bandeau ±3j)
        # Évite l'écart historique calendrier mois vs bandeau (cache séparé → prix différents).
        # 2 tentatives sur 429 avec backoff jittered léger (0.4..0.8, 0.8..1.6)
        last_err = None
        for attempt in range(2):
            try:
                offers, min_price, offers_count = await _asyncio.to_thread(
                    _fetch_or_cache_offers,
                    origin, destination, d_str, pax_n, cabin_norm,
                )
                if not offers or offers_count == 0:
                    return {"date": d_str, "min_price": None, "offers_count": 0, "error": "no_offers"}
                return {
                    "date": d_str,
                    "min_price": min_price,
                    "offers_count": offers_count,
                    "error": None,
                }
            except DuffelHttpError as e:
                last_err = f"duffel_{e.status}"
                if e.status == 429 and attempt < 1:
                    base = 0.4 * (2 ** attempt)
                    await _asyncio.sleep(base + _rand.random() * base)
                    continue
                return {"date": d_str, "min_price": None, "offers_count": 0, "error": last_err}
            except Exception as e:
                last_err = str(e)[:80]
                return {"date": d_str, "min_price": None, "offers_count": 0, "error": last_err}
        return {"date": d_str, "min_price": None, "offers_count": 0, "error": last_err or "unknown"}

    # Timeout global 50s — avec retries 429 + semaphore(2), 31 dates peuvent prendre 30-45s.
    # (cache 24h = 1ère requête longue, suivantes instantanées <100ms)
    try:
        days_list = await _asyncio.wait_for(
            _asyncio.gather(*[_fetch_one(d, p) for d, p in target_dates]),
            timeout=50.0,
        )
        days_list = list(days_list)
    except _asyncio.TimeoutError:
        _alert_telegram(
            f"price-month timeout 45s : {origin}→{destination} {year}-{mnum:02d} "
            f"pax={pax_n} cabin={cabin_norm}"
        )
        # Fallback : on remplit tous les jours avec timeout error pour ne pas planter
        days_list = [
            {"date": d, "min_price": None, "offers_count": 0,
             "error": "past" if p else "timeout"}
            for d, p in target_dates
        ]

    # ─── 4) Cheapest date ─────────────────────────────────────────
    valid = [c for c in days_list if c.get("min_price")]
    cheapest_date = None
    cheapest_price = None
    if valid:
        best = min(valid, key=lambda c: c["min_price"])
        cheapest_date = best["date"]
        cheapest_price = best["min_price"]

    # ─── 5) Watchdog Telegram si >30% en erreur (hors past) ──────
    errors_count = sum(1 for c in days_list if c.get("error") and c.get("error") != "past")
    eligible = sum(1 for c in days_list if c.get("error") != "past")
    if eligible > 0 and (errors_count / eligible) > 0.3:
        _alert_telegram(
            f"price-month dégradé : {errors_count}/{eligible} jours en erreur "
            f"({origin}→{destination} {year}-{mnum:02d} pax={pax_n} cabin={cabin_norm})"
        )

    payload = {
        "month": f"{year:04d}-{mnum:02d}",
        "from": origin,
        "to": destination,
        "pax": pax_n,
        "cabin": cabin_norm,
        "currency": "EUR",
        "days": days_list,
        "cheapest_date": cheapest_date,
        "cheapest_price": cheapest_price,
        "cached": False,
    }

    return payload


@router.get("/flights/fare-options/{cache_offer_id}")
@limiter.limit("30/minute")
def flight_fare_options(request: Request, cache_offer_id: str,
                         passengers: int = 1, cabin_class: Optional[str] = None):
    """Retourne les fares (Light/Standard/Flex…) disponibles pour le MÊME trajet
    qu'un offer_id du cache.

    Stratégie :
      1. Charge le deal du cache (origin/dest/date/airline)
      2. Lance un search live Duffel sur la route/date
      3. Filtre les offers de la même compagnie + même heure de départ (±30min)
         pour ne garder que les fares du MÊME vol physique
      4. Groupe par fare_brand_name, garde le moins cher par brand
      5. Normalise vers {brand, offer_id, total_amount, services}
      6. Tri Light → Standard → Flex
    """
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")

    try:
        from providers.duffel import search_offers_live, get_offer_live
    except Exception as e:
        _alert_telegram(f"fare-options: import providers.duffel KO : {e}")
        raise HTTPException(500, f"providers.duffel unavailable: {e}")

    # Charge le deal de référence
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT offer_id, origin, destination, airline_code, airline_name,
                   price, currency, cabin_class, departure_at, duration_minutes,
                   stops, fare_brand
            FROM deals WHERE offer_id = %s LIMIT 1
        """, (cache_offer_id,))
        deal = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        _alert_telegram(f"fare-options DB lookup KO {cache_offer_id} : {e}")
        raise HTTPException(500, f"db lookup failed: {e}")

    if not deal:
        # Fallback : scan flight_offers_cache (offers JSONB) — même source que le bandeau
        try:
            import json as _json
            conn2 = psycopg2.connect(**DB_CONFIG)
            cur2 = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur2.execute(
                "SELECT offers FROM flight_offers_cache "
                "WHERE offers @> %s::jsonb AND expires_at > NOW() LIMIT 1",
                (_json.dumps([{"id": cache_offer_id}]),),
            )
            row2 = cur2.fetchone()
            cur2.close()
            conn2.close()
            if row2:
                offers_list = row2["offers"] or []
                if isinstance(offers_list, str):
                    offers_list = _json.loads(offers_list)
                offer = next((o for o in offers_list if o.get("id") == cache_offer_id), None)
                if offer:
                    slices = offer.get("slices") or []
                    seg0 = (slices[0].get("segments") or [{}])[0] if slices else {}
                    segN = (slices[0].get("segments") or [{}])[-1] if slices else {}
                    airline = (offer.get("owner") or {}).get("name") or seg0.get("marketing_carrier", {}).get("name", "")
                    airline_code = (offer.get("owner") or {}).get("iata_code") or seg0.get("marketing_carrier", {}).get("iata_code", "")
                    duration_iso = (slices[0].get("duration") if slices else "") or ""
                    dur_min = 0
                    try:
                        import re as _re
                        h = _re.search(r"(\d+)H", duration_iso)
                        m = _re.search(r"(\d+)M", duration_iso)
                        dur_min = (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
                    except Exception:
                        pass
                    stops = max(0, len((slices[0].get("segments") if slices else []) or []) - 1) if slices else None
                    dep_at = seg0.get("departing_at")
                    if dep_at:
                        try:
                            dep_at = datetime.strptime(dep_at[:19], '%Y-%m-%dT%H:%M:%S')
                        except (ValueError, TypeError):
                            dep_at = None
                    deal = {
                        "offer_id": offer.get("id"),
                        "origin": (seg0.get("origin") or {}).get("iata_code") or "",
                        "destination": (segN.get("destination") or {}).get("iata_code") or "",
                        "airline_code": airline_code,
                        "airline_name": airline,
                        "price": float(offer.get("total_amount") or 0),
                        "currency": offer.get("total_currency") or "EUR",
                        "cabin_class": ((slices[0].get("segments") or [{}])[0].get("passengers") or [{}])[0].get("cabin_class") if slices else None,
                        "departure_at": dep_at,
                        "duration_minutes": dur_min,
                        "stops": stops,
                        "fare_brand": offer.get("fare_brand_name") or "",
                    }
        except Exception as e:
            print(f"[fare-options] flight_offers_cache lookup KO: {e}")
    if not deal:
        raise HTTPException(404, "offer_id introuvable dans le cache")

    deal_d = dict(deal)
    dep_at = deal_d["departure_at"]
    departure_date = dep_at.isoformat()[:10] if dep_at else None
    cached_airline = deal_d.get("airline_code") or ""
    cabin = (cabin_class or deal_d.get("cabin_class") or "business").lower()

    # Search live
    try:
        # Audit 2026-05-27 sev 2 #11 + sev 3 #10 : supplier_timeout long
        # pour fare-options (le client attend déjà la page), max_connections=2.
        live_offers = search_offers_live(
            deal_d["origin"], deal_d["destination"], departure_date,
            passengers=passengers, cabin_class=cabin,
            max_connections=2, supplier_timeout_ms=25000,
        )
    except Exception as e:
        _alert_telegram(
            f"fare-options Duffel search KO {deal_d['origin']}→{deal_d['destination']} "
            f"{departure_date} : {e}"
        )
        return {
            "cache_offer_id": cache_offer_id,
            "fares": [],
            "error": "duffel_search_failed",
            "message": str(e)[:200],
        }

    if not live_offers:
        return {
            "cache_offer_id": cache_offer_id,
            "fares": [],
            "message": "Aucune offre live disponible pour ce trajet",
        }

    # Filtre : même compagnie + heure départ ±30min de la réf
    cached_hour_min = None
    if dep_at:
        try:
            cached_hour_min = dep_at.hour * 60 + dep_at.minute
        except Exception:
            cached_hour_min = None

    matched = []
    for o in live_offers:
        owner_code = (o.get("owner") or {}).get("iata_code") or ""
        slices = o.get("slices") or []
        if not slices or not (slices[0].get("segments") or []):
            continue
        seg0 = slices[0]["segments"][0]
        marketing = (seg0.get("marketing_carrier") or {}).get("iata_code") or owner_code
        if cached_airline and marketing != cached_airline:
            continue
        dep_str = seg0.get("departing_at", "")
        if cached_hour_min is not None and len(dep_str) >= 16:
            try:
                offer_hm = int(dep_str[11:13]) * 60 + int(dep_str[14:16])
            except ValueError:
                offer_hm = -999
            if abs(offer_hm - cached_hour_min) > 30:
                continue
        matched.append(o)

    # Si rien matché on relâche le filtre carrier (cas rare)
    if not matched:
        matched = live_offers

    # Groupe par fare_brand_name (case insensitive) → garde le moins cher
    by_brand: dict[str, dict] = {}
    for o in matched:
        brand_raw = (o.get("slices") or [{}])[0].get("fare_brand_name") or o.get("fare_brand_name") or ""
        brand_key = brand_raw.strip().upper() or "STANDARD"
        try:
            price = float(o.get("total_amount", 0))
        except (TypeError, ValueError):
            price = 0.0
        existing = by_brand.get(brand_key)
        if not existing or price < float(existing.get("total_amount", 0)):
            by_brand[brand_key] = {**o, "_brand_raw": brand_raw, "_brand_key": brand_key, "_price": price}

    if not by_brand:
        return {
            "cache_offer_id": cache_offer_id,
            "fares": [],
            "message": "Pas de fare alternatif détecté pour ce vol",
        }

    # Hydrate chaque fare avec les services (avec_services=True pour seat_selection)
    fares_out = []
    for brand_key, o in by_brand.items():
        # On enrichit avec available_services seulement pour le moins cher de chaque brand
        # (économie quota — pas critique : la modale donne déjà l'essentiel)
        services = _normalize_fare_services(o)
        fares_out.append({
            "offer_id": o.get("id"),
            "brand_raw": o.get("_brand_raw") or brand_key,
            "brand": o.get("_brand_raw") or brand_key,
            "brand_label": (o.get("_brand_raw") or brand_key).title() if o.get("_brand_raw") else "",
            "total_amount": f"{o.get('_price', 0):.2f}",
            "currency": o.get("total_currency", "EUR"),
            "expires_at": o.get("expires_at"),
            "services": services,
        })

    fares_out.sort(key=lambda f: float(f["total_amount"]))

    return {
        "cache_offer_id": cache_offer_id,
        "fares": fares_out,
        "count": len(fares_out),
    }


@router.get("/vols/search")
@limiter.limit("30/minute")
async def vols_search(
    request: Request,
    from_: str = None,
    to: str = None,
    date: str = None,
    pax: int = 1,
    cabin: str = "economy",
    adults: int = 0,
    children: int = 0,
    infants: int = 0,
    days: int = 7,
    limit: int = 20,
):
    """Endpoint unifié Duffel officiel : 31 dates en parallèle, réponse unique.

    Retourne cards (jour J) + calendar (31 cellules) + cheapest_date.
    TTL cache 600s, pool asyncio.Semaphore(6).
    """
    import asyncio as _asyncio

    # ── Parse params (mêmes règles que /flights/price-calendar) ──
    qp = request.query_params
    origin = (from_ or qp.get("from") or "").upper().strip()
    destination = (to or qp.get("to") or "").upper().strip()
    ref_date_str = (date or qp.get("date") or "").strip()
    try:
        pax_n = int(pax or qp.get("pax") or 1)
    except (TypeError, ValueError):
        pax_n = 1
    if adults or children or infants:
        pax_n = max(1, (adults or 0) + (children or 0) + (infants or 0))
    cabin_norm = (cabin or qp.get("cabin") or "economy").lower().strip()
    try:
        days_n = int(days or qp.get("days") or 7)
    except (TypeError, ValueError):
        days_n = 7
    days_n = max(1, min(days_n, 7))
    try:
        limit_n = int(limit or qp.get("limit") or 20)
    except (TypeError, ValueError):
        limit_n = 20

    if not origin or not destination or not ref_date_str:
        raise HTTPException(status_code=400, detail="from, to, date required")

    try:
        ref_dt = datetime.strptime(ref_date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    # ─── 1) Build list of 31 dates (J-15 .. J+15) ────────────────
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    target_dates = []
    for delta in range(-days_n, days_n + 1):
        d = ref_dt + timedelta(days=delta)
        d_str = d.strftime("%Y-%m-%d")
        if d_str < today_str:
            target_dates.append((d_str, True))  # past
        else:
            target_dates.append((d_str, False))

    # ─── 2) Fetch all dates in parallel with Semaphore(2) ──
    _sem = _asyncio.Semaphore(2)

    async def _fetch_one(d_str: str, is_past: bool) -> dict:
        if is_past:
            return {"date": d_str, "min_price": None, "offers_count": 0, "status": "past"}
        async with _sem:
            return await _fetch_one_inner(d_str)

    async def _fetch_one_inner(d_str: str) -> dict:
        try:
            offers, min_price, offers_count = await _asyncio.to_thread(
                _fetch_or_cache_offers,
                origin, destination, d_str, pax_n, cabin_norm,
                ttl_seconds=600,
            )
            if not offers or offers_count == 0:
                return {"date": d_str, "min_price": None, "offers_count": 0, "status": "no_offer"}
            return {
                "date": d_str,
                "min_price": min_price,
                "offers_count": offers_count,
                "status": "ok",
            }
        except Exception as e:
            err_str = str(e)[:80]
            if "429" in err_str or "rate_limit" in err_str.lower():
                return {"date": d_str, "min_price": None, "offers_count": 0, "status": "error", "error": "duffel_429"}
            return {"date": d_str, "min_price": None, "offers_count": 0, "status": "error", "error": err_str}

    cells = await _asyncio.gather(*[_fetch_one(d, p) for d, p in target_dates])
    cells_list = list(cells)

    # ─── 3) Cards : offres du jour J (ref_date) ──────────────────
    cards = []
    ref_cell = next((c for c in cells_list if c["date"] == ref_date_str), None)
    if ref_cell and ref_cell["status"] == "ok":
        try:
            offers_all, _, _ = await _asyncio.to_thread(
                _fetch_or_cache_offers,
                origin, destination, ref_date_str, pax_n, cabin_norm,
                ttl_seconds=600,
            )
            for o in (offers_all or [])[:limit_n]:
                slices = o.get("slices") or []
                seg0 = (slices[0].get("segments") or [{}])[0] if slices else {}
                segN = (slices[0].get("segments") or [{}])[-1] if slices else {}
                airline = (o.get("owner") or {}).get("name") or seg0.get("marketing_carrier", {}).get("name", "")
                airline_code = (o.get("owner") or {}).get("iata_code") or seg0.get("marketing_carrier", {}).get("iata_code", "")
                duration_iso = (slices[0].get("duration") if slices else "") or ""
                dur_min = 0
                try:
                    import re as _re
                    h = _re.search(r"(\d+)H", duration_iso)
                    m = _re.search(r"(\d+)M", duration_iso)
                    dur_min = (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
                except Exception:
                    pass
                stops_live = None
                try:
                    segs0 = (slices[0].get("segments") if slices else None) or []
                    if segs0:
                        stops_live = max(0, len(segs0) - 1)
                except Exception:
                    stops_live = None
                cards.append({
                    "offer_id": o.get("id"),
                    "origin": origin,
                    "destination": destination,
                    "airline_name": airline,
                    "airline_code": airline_code,
                    "price": float(o.get("total_amount") or 0),
                    "currency": o.get("total_currency") or "EUR",
                    "cabin_class": cabin_norm,
                    "departure_at": seg0.get("departing_at"),
                    "arrival_at": segN.get("arriving_at"),
                    "duration_minutes": dur_min,
                    "stops": stops_live,
                    "fare_brand": o.get("fare_brand_name") or "",
                    "expires_at": o.get("expires_at"),
                    "provider": "duffel",
                    "source": "ota",
                    "deeplink_url": None,
                })
        except Exception as e:
            print(f"[vols/search] cards fetch error: {e}")

    # ─── 4) Cheapest date ────────────────────────────────────────
    valid = [c for c in cells_list if c.get("min_price")]
    cheapest_date = None
    if valid:
        cheapest_date = min(valid, key=lambda c: c["min_price"])["date"]

    payload = {
        "ref_date": ref_date_str,
        "from": origin,
        "to": destination,
        "pax": pax_n,
        "cabin": cabin_norm,
        "currency": "EUR",
        "cards": cards,
        "calendar": cells_list,
        "cheapest_date": cheapest_date,
    }

    return payload
