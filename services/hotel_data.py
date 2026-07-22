"""services/hotel_data.py — Source unique de vérité par hôtel.

ROLE :
  Charge en quelques queries SQL TOUT ce qu'on sait d'un hôtel à partir de son
  slug, puis enrichit en Python (galerie classée, facilities FR, POI, aéroports).

POURQUOI :
  Avant le 2026-05-30, on avait 2 pages divergentes pour le même hôtel (page
  SEO statique + /quote.html dynamique) qui tiraient des sources différentes et
  affichaient des contenus différents. Cette fonction centralise pour que les 2
  portes d'entrée d'un même hôtel renvoient le même paquet de données.

QUI L'UTILISE :
  - main.py → handler /hotels/{cc}/{ville}/{slug} (page SEO unifiée)
  - À venir : routers/hotel.py → /api/hotels/quote (refactor Phase 5)
  - À venir : scripts/seo_auto_generator.py (validation cohérence après génération)

DÉPENDANCES :
  - helpers main.py importés en LAZY (au runtime) pour éviter les circular imports :
    DB_CONFIG, extract_best_main_photo (providers.hbx.photos), extract_facilities_fr,
    FACILITY_CATEGORY_LABELS, nearby_pois, airports_nearby, _city_key.

HISTORIQUE :
  - 2026-05-30 : Phase 1 (création dans main.py) puis Phase 4 (extraction ici).
"""
from __future__ import annotations
import json
from typing import Optional

import psycopg2
import psycopg2.extras


# ───────────────────────────── SQL ─────────────────────────────────
# Joint hotels_canonical (éditorial DeepSeek) + hbx_hotels_catalog (galerie HBX
# brute) en 1 requête. Source unique pour les 2 portes d'entrée hôtel.
_HOTEL_UNIFIED_SQL = """
    SELECT c.*,
           hc.hotel_code         AS hbx_hotel_code,
           hc.main_image_url     AS hbx_main_image,
           hc.images_count       AS hbx_images_count,
           hc.facilities_count   AS hbx_facilities_count,
           hc.destination_code   AS hbx_destination_code,
           hc.raw                AS hbx_raw,
           rh.hid                AS ratehawk_hid,
           rh.raw                AS ratehawk_raw
    FROM hotels_canonical c
    LEFT JOIN hbx_hotels_catalog hc ON hc.giata_code = c.giata_code
    LEFT JOIN hotels_provider_map rpm ON rpm.giata_code = c.giata_code AND rpm.provider = 'ratehawk'
    LEFT JOIN ratehawk_hotels_catalog rh ON rh.hid = NULLIF(rpm.provider_hotel_code, '')::bigint
    WHERE c.slug = %s
    LIMIT 1
"""

# Mapping HBX imageTypeCode → catégorie d'affichage standardisée.
# Partagé entre la page SEO et la page Quote pour garantir une galerie identique.
_HBX_IMG_CATEGORIES = {
    "HAB": "rooms",
    "RES": "restaurant",
    "BAR": "bar",
    "GEN": "general", "CON": "general", "COM": "general",
    "DEP": "outdoor", "TER": "outdoor",
}


def _load_managed_transfers(hbx_hotel_code) -> list:
    """Charge les transferts AirBizness-natifs publiés par l'hôtelier revendiqué.

    Cohérent avec la doctrine du carnet partagé : la SEULE source d'info hôtel
    pour les pages. Si on changera demain le matching (par destination_code,
    par giata, etc.), 1 endroit à toucher.

    Returns liste vide si pas de hbx_hotel_code OU si l'hôtelier n'a rien publié
    OU si la table n'existe pas (= comportement honnête, pas d'erreur).
    """
    if not hbx_hotel_code:
        return []
    # Lazy import (DB_CONFIG vit dans main.py — éviter le circular)
    from main import DB_CONFIG
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, label, from_location, to_location,
                   gross_price_eur, currency, vehicle_type,
                   max_passengers, max_luggage, notice_hours,
                   cancellation_policy
            FROM hotel_managed_transfers
            WHERE hotel_code = %s AND active = true
            ORDER BY gross_price_eur ASC
            LIMIT 8
        """, (hbx_hotel_code,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        # Cast Decimal → float pour sérialisation JSON propre
        for r in rows:
            if r.get("gross_price_eur") is not None:
                r["gross_price_eur"] = float(r["gross_price_eur"])
        return rows
    except Exception:
        return []


def get_hotel_unified_data(slug: str) -> Optional[dict]:
    """Charge TOUT ce qu'on sait d'un hôtel à partir de son slug.

    Returns None si le slug n'existe pas (= page 404).

    Clés stables du dict retourné :
      - identité      : slug, name, giata_code, hbx_hotel_code, chain_code,
                        country_code, city, address, postal_code, latitude, longitude
      - éditorial     : seo_intro_fr, seo_why_business_fr, seo_neighborhood_fr,
                        description_en, description_fr, stars
      - médias        : photo_main (URL hero), gallery (dict 6 catégories),
                        total_photos
      - équipements   : facilities_by_category (dict FR), facility_category_labels
      - lieu          : pois_nearby, airports (calculés depuis lat/lng)
      - HBX brut      : hbx_destination_code (utile pour search live),
                        hbx_raw (dict parsé, pour compat _render_hotel_unified
                        qui n'a pas encore migré)
    """
    # Lazy imports pour éviter le circular import (main.py importe services/,
    # services/ importe des helpers de main.py).
    from main import (
        DB_CONFIG,
        extract_facilities_fr,
        FACILITY_CATEGORY_LABELS,
        nearby_pois,
        airports_nearby,
        _city_key,
    )
    from providers.hbx.photos import extract_best_main_photo

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(_HOTEL_UNIFIED_SQL, (slug,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None

    h = dict(row)

    # Parse raw HBX (jsonb peut arriver en str selon driver)
    _raw = h.get("hbx_raw")
    if isinstance(_raw, str):
        try: _raw = json.loads(_raw)
        except Exception: _raw = None
    raw_images = (_raw or {}).get("images") or [] if isinstance(_raw, dict) else []
    raw_facilities = (_raw or {}).get("facilities") if isinstance(_raw, dict) else None

    # ── Photo héros : la VRAIE main (imageTypeCode='GEN' + order min) > fallbacks
    h["photo_main"] = (
        extract_best_main_photo(raw_images, provider="hbx")
        or h.get("hbx_main_image")
        or h.get("best_photo_url")
        or ""
    )

    # ── Galerie classée 6 catégories, triée par order pour respecter l'ordre HBX
    gallery = {"rooms": [], "general": [], "restaurant": [], "bar": [], "outdoor": [], "other": []}
    sorted_imgs = sorted(
        [i for i in raw_images if isinstance(i, dict) and i.get("path")],
        key=lambda i: (i.get("order", 999), i.get("visualOrder", 9999)),
    )
    for img in sorted_imgs[:160]:
        url = f"https://photos.hotelbeds.com/giata/bigger/{img['path']}"
        cat = _HBX_IMG_CATEGORIES.get(img.get("imageTypeCode") or "", "other")
        gallery[cat].append(url)
    h["gallery"] = gallery
    h["total_photos"] = sum(len(v) for v in gallery.values())

    # ── Équipements classés par catégorie d'affichage (FR)
    h["facilities_by_category"] = extract_facilities_fr(raw_facilities, provider="hbx")
    h["facility_category_labels"] = FACILITY_CATEGORY_LABELS

    # ── Source RateHawk (pages RateHawk : pas de brut HBX) ──────────────────
    # Si HBX n'a fourni ni photo ni équipement, on peuple depuis le brut RateHawk
    # (mêmes clés de sortie : photo_main, gallery, total_photos, facilities_by_category).
    _rh = h.get("ratehawk_raw")
    if isinstance(_rh, str):
        try: _rh = json.loads(_rh)
        except Exception: _rh = None
    if isinstance(_rh, dict) and h["total_photos"] == 0:
        def _rh_url(u):
            return u.replace("{size}", "1024x768") if isinstance(u, str) and u else ""
        rh_imgs = [_rh_url(u) for u in (_rh.get("images") or []) if u]
        rh_gal = {"rooms": [], "general": [], "restaurant": [], "bar": [], "outdoor": [], "other": []}
        rh_gal["general"] = rh_imgs[:120]
        for rg in (_rh.get("room_groups") or []):
            for u in (rg.get("images") or [])[:6]:
                url = _rh_url(u)
                if url:
                    rh_gal["rooms"].append(url)
        h["gallery"] = rh_gal
        h["total_photos"] = sum(len(v) for v in rh_gal.values())
        if not h.get("photo_main"):
            h["photo_main"] = rh_gal["general"][0] if rh_gal["general"] else (h.get("best_photo_url") or "")
        # Équipements : amenity_groups RateHawk → {libellé groupe: [amenities]}
        rh_fac = {}
        for ag in (_rh.get("amenity_groups") or []):
            gname = (ag.get("group_name") or "").strip() or "Services"
            ams = [a for a in (ag.get("amenities") or []) if a]
            if ams:
                rh_fac[gname] = ams
        if rh_fac:
            h["facilities_by_category"] = rh_fac

    # ── POI réels + aéroports (distances calculées Haversine)
    h["pois_nearby"] = []
    h["airports"] = []
    if h.get("latitude") and h.get("longitude"):
        ck = _city_key(h.get("city") or h.get("country_code") or "")
        if ck:
            h["pois_nearby"] = nearby_pois(
                float(h["latitude"]), float(h["longitude"]), ck,
                max_count=8, max_km=8.0,
            )
            h["airports"] = airports_nearby(
                float(h["latitude"]), float(h["longitude"]), ck,
            )

    # Garde hbx_raw parsé (dict) pour compat backward avec _render_hotel_unified
    # qui n'a pas encore migré ici. À retirer quand le render aura aussi migré.
    h["hbx_raw"] = _raw

    # ── Transferts AirBizness-natifs (publiés par l'hôtelier revendiqué) ──
    # Ajout 2026-05-30 : le carnet contient TOUT, y compris ce qui vient
    # du provider AirBizness natif. Pas de query SQL dans le render → DRY.
    h["transfers"] = _load_managed_transfers(h.get("hbx_hotel_code"))

    return h
