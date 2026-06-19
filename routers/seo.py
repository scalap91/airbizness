"""
Routes SEO — migré de main.py 2026-06-01 (5e module sur 13). Pascal/orchestrateur DeepSeek.

Routes critiques pour Googlebot (66.249.x.x crawle activement) :
  GET  /h/{slug}                          → 301 vers URL canonique
  GET  /hotels/{cc}/{city}/{slug}          → page SEO hôtel SSR
  POST /leads/notify-launch                → capture lead pré-launch
  GET  /destinations/{city_slug}           → hub destination
  GET  /vols/{route_slug}                  → page SEO route aérienne
  GET  /sitemap.xml                        → sitemap dynamique
  GET  /sitemap-priority.xml               → sitemap priorité
  GET  /robots.txt                         → robots.txt

Toute régression = perte trafic SEO. Helpers exclusifs SEO migrés ici,
helpers partagés (hotel_interest_points, _hotel_seo_path, _slugify, ...)
restent dans main.py et sont importés.
"""

import json
import math as _math
import re
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, EmailStr

import psycopg2
import psycopg2.extras

from main import (
    DB_CONFIG,
    limiter,
    _hotel_seo_path,
    _city_url_slug,
    _country_iso,
    _slugify,
    _airport_info,
    _city_key,
    hotel_interest_points,
    _fmt_poi_distance_m,
    _clean_poi_name,
    _poi_dedup_key,
    CITY_CENTERS,
    CITY_AIRPORTS,
    haversine_km,
    nearby_pois,
    airports_nearby,
)
from providers.hbx.photos import extract_best_main_photo
from services.hotel_data import get_hotel_unified_data
from services.affiliate_hotellook import get_hotellook_search_url, get_provider_search_url
from routers.recherche import _vols_route_map

router = APIRouter()


# ============================================================
# MODÈLES Pydantic
# ============================================================

class NotifyLaunchRequest(BaseModel):
    email: EmailStr
    giata_code: Optional[str] = None
    source: Optional[str] = "hotel_page"

# ============================================================
# HELPERS / CONSTANTES SEO
# ============================================================
def _not_found_page(slug: str) -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Hôtel introuvable — AirBizness</title>
<meta name="robots" content="noindex">
</head><body style="background:#0f0f0f;color:#f0ece4;font-family:'DM Sans',sans-serif;text-align:center;padding:80px 24px;">
<h1>Hôtel introuvable</h1>
<p>L'établissement <code>{slug}</code> n'est pas dans notre catalog.</p>
<a href="/" style="color:#d4ae4a;">Retour à l'accueil</a></body></html>"""

def html_escape(s):
    """HTML escape rapide."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

def html_esc(s) -> str:
    """Simple HTML escape pour éviter les injections dans le SSR."""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

DESTINATIONS_CONTENT = {
    "madrid": {
        "code": "MAD",
        "name": "Madrid",
        "country": "Espagne",
        "country_code": "ES",
        "hero_image": "/images/destinations/mad.jpg",
        "tagline": "La capitale espagnole en Business Class.",
        "intro": (
            "Madrid, capitale espagnole de 3,3 millions d'habitants, conjugue tradition royale et "
            "modernité audacieuse. Plus haute capitale européenne (650 m), elle bénéficie d'un "
            "climat continental sec, d'une scène gastronomique trois fois étoilée Michelin et "
            "d'une vie nocturne légendaire. Pour un voyageur Business Class depuis Paris, "
            "Casablanca ou Dubaï, c'est l'une des destinations européennes les plus accessibles."
        ),
        "why_business": [
            ("Vol direct depuis CDG", "2h05 · 12 rotations/jour · Air France, Iberia"),
            ("Vol direct depuis CMN", "3h05 · 5 rotations/semaine · Royal Air Maroc, Air Arabia"),
            ("Vol direct depuis DXB", "8h05 · Emirates A380 Business"),
            ("Aéroport Barajas T4", "20 min en taxi du centre · Lounges Iberia Velázquez & Cibeles"),
        ],
        "quartiers": [
            {
                "nom": "Salamanca",
                "label": "Luxe & shopping",
                "desc": "Quartier élégant aux avenues haussmanniennes. Boutiques Loewe, Hermès, Carolina Herrera sur la Calle Serrano. Hôtels signature : Bless, VP Plaza España, Wellington.",
            },
            {
                "nom": "Retiro",
                "label": "Calme & patrimoine",
                "desc": "Autour du parc du Retiro et du Prado. Adresses confidentielles, ambiance résidentielle haut de gamme. Le Relais & Châteaux Orfila y est niché.",
            },
            {
                "nom": "Centro",
                "label": "Coeur historique",
                "desc": "Puerta del Sol, Plaza Mayor, Gran Vía. Centre névralgique. Le Ritz (Mandarin Oriental) et le Palace y règnent.",
            },
            {
                "nom": "Chamberí",
                "label": "Vie locale chic",
                "desc": "Quartier authentique en pleine montée. Caves à vin, bistronomie, voisins madrilènes. Idéal pour un séjour plus immersif.",
            },
        ],
        "saison": [
            ("Avril – Juin", "22-26°C · printemps lumineux · Feria de San Isidro en mai"),
            ("Sept – Octobre", "23-27°C · meilleure saison · vendanges Ribera del Duero"),
            ("Novembre – Mars", "8-15°C · ville moins peuplée · gastronomie d'hiver, jamón ibérico"),
        ],
        "lat": 40.4168,
        "lon": -3.7038,
    },
    "paris": {
        "code": "PAR",
        "name": "Paris",
        "country": "France",
        "country_code": "FR",
        "hero_image": "/images/destinations/par.jpg",
        "tagline": "L'expérience parisienne, sans compromis.",
        "intro": (
            "Paris reste la ville la plus visitée au monde et la première destination Business "
            "Class d'Europe continentale. 11,2 millions de touristes premium par an, 31 chefs "
            "trois étoiles Michelin, palaces centenaires et nouvelles adresses signature. "
            "Pour un voyageur depuis Casablanca, Dubaï, Singapour ou New York, c'est la porte "
            "d'entrée incontournable du luxe européen."
        ),
        "why_business": [
            ("Vols depuis MENA", "CMN 3h15 · TUN 2h50 · ALG 2h30 · 15+ rotations/jour"),
            ("Vols depuis Golfe", "DXB 7h05 · DOH 7h10 · AUH 7h00 · A380 Emirates/Qatar/Etihad"),
            ("Vols depuis Asie SE", "SIN 13h · BKK 12h · KUL 13h · Singapore Airlines, Thai, Cathay"),
            ("Aéroports", "CDG (T2E/T2F Business) · ORY (West Business Lounge)"),
        ],
        "quartiers": [
            {
                "nom": "Triangle d'Or (8e)",
                "label": "Palaces & Champs-Élysées",
                "desc": "George V, Plaza Athénée, Bristol, Royal Monceau. Adresse classique des voyageurs Business. Boutiques Avenue Montaigne et Faubourg Saint-Honoré.",
            },
            {
                "nom": "Saint-Germain (6e)",
                "label": "Rive Gauche raffinée",
                "desc": "Lutetia, Relais Christine, esprit littéraire et galeries d'art. Plus discret que la rive droite. Idéal pour un séjour culturel.",
            },
            {
                "nom": "Marais (3e/4e)",
                "label": "Hôtels-particuliers contemporains",
                "desc": "Maisons du Monde de l'art, Cour des Vosges, Pavillon de la Reine. Quartier patrimonial où design contemporain rencontre pierres XVIIᵉ.",
            },
            {
                "nom": "Opéra-Vendôme (1er/2e)",
                "label": "Joaillerie & shopping",
                "desc": "Ritz Paris, Mandarin Oriental, Park Hyatt Vendôme. Cœur du shopping de luxe : Cartier, Boucheron, Van Cleef, place Vendôme.",
            },
        ],
        "saison": [
            ("Mai – Juillet", "18-25°C · Roland-Garros, Fashion Week · meilleur moment"),
            ("Septembre – Octobre", "16-21°C · vendanges, FIAC, ambiance feutrée"),
            ("Novembre – Mars", "5-12°C · marchés de Noël Champs-Élysées, expositions"),
        ],
        "lat": 48.8566,
        "lon": 2.3522,
    },
    "londres": {
        "code": "LON",
        "name": "Londres",
        "country": "Royaume-Uni",
        "country_code": "GB",
        "hero_image": "/images/destinations/lon.jpg",
        "tagline": "Le standard mondial du voyage Business.",
        "intro": (
            "Londres est, avec New York et Tokyo, l'une des trois capitales financières "
            "mondiales. 7,5 millions de visiteurs business par an, 200+ hôtels 5 étoiles, "
            "70 restaurants étoilés Michelin. Pour un voyageur MENA ou Asie du Sud-Est, "
            "Londres est souvent le hub européen privilégié — accès rapide depuis Heathrow "
            "ou City Airport, services en arabe largement disponibles dans les palaces "
            "de Mayfair et Knightsbridge."
        ),
        "why_business": [
            ("Vols depuis MENA", "DXB 7h35 · DOH 6h55 · AUH 7h45 · British Airways, Emirates, Qatar"),
            ("Vols depuis Asie SE", "SIN 13h35 · BKK 11h55 · HKG 12h45 · SQ A380, BA Club Suite"),
            ("Vols depuis Europe", "CDG 1h25 · MAD 2h15 · FRA 1h45 · BA, Air France, Lufthansa"),
            ("Aéroports", "LHR T5 (BA Galleries) · LCY 30 min City · LGW · STN"),
        ],
        "quartiers": [
            {
                "nom": "Mayfair",
                "label": "Le standard du luxe londonien",
                "desc": "Claridge's, The Connaught, Brown's, Dorchester. Bond Street pour les boutiques, Mount Street pour les galeries. Adresse historique de la haute société.",
            },
            {
                "nom": "Knightsbridge",
                "label": "Shopping & ambassades",
                "desc": "Mandarin Oriental Hyde Park, Bvlgari Hotel, Berkeley. Harrods et Harvey Nichols à 5 min. Très apprécié des familles du Golfe.",
            },
            {
                "nom": "Belgravia",
                "label": "Discrétion patricienne",
                "desc": "Lanesborough, Goring, Hari. Quartier résidentiel calme des ambassades, à 10 min de Buckingham et Hyde Park. Séjour familial idéal.",
            },
            {
                "nom": "Covent Garden / Soho",
                "label": "Théâtres & vie nocturne",
                "desc": "Savoy, Ham Yard, Strand Palace. Cœur du West End : théâtres, comédies musicales, restaurants étoilés. Energie permanente.",
            },
        ],
        "saison": [
            ("Mai – Septembre", "14-22°C · Chelsea Flower Show, Wimbledon, Notting Hill Carnival"),
            ("Décembre", "5-8°C · marchés de Noël Hyde Park Winter Wonderland, illuminations Regent Street"),
            ("Mars – Avril", "8-14°C · saison shopping, Royal Academy Spring Exhibition"),
        ],
        "lat": 51.5074,
        "lon": -0.1278,
    },
}

TOP_DESTINATIONS_FOOTER = [
    ("paris", "Paris"), ("londres", "Londres"), ("new-york", "New York"),
    ("dubai", "Dubai"), ("singapore", "Singapour"), ("tokyo", "Tokyo"),
    ("geneve", "Genève"), ("zurich", "Zurich"), ("hong-kong", "Hong Kong"),
    ("shanghai", "Shanghai"), ("sydney", "Sydney"), ("sao-paulo", "São Paulo"),
    ("mumbai", "Mumbai"), ("bangkok", "Bangkok"), ("seoul", "Séoul"),
    ("berlin", "Berlin"), ("milano", "Milan"), ("amsterdam", "Amsterdam"),
    ("madrid", "Madrid"), ("lisbon", "Lisbonne"),
]

_HBX_DEST_CACHE = {"ts": 0.0, "by_slug": {}, "by_code": {}}

# Compagnies « techniques » / sandbox à ne jamais afficher comme réelles
_NON_REAL_AIRLINES = {"duffel airways", "duffel"}

def _render_top_destinations_footer_ssr() -> str:
    """Bloc SSR 'Top destinations business' (20 villes premium) — internal linking."""
    links = "".join(
        f'<a href="/destinations/{slug}" class="td-link">{html_esc(name)}</a>'
        for slug, name in TOP_DESTINATIONS_FOOTER
    )
    return f"""
<section class="top-destinations-ssr" style="padding:42px 24px;background:#161616;border-top:1px solid rgba(255,255,255,0.07);">
  <div style="max-width:1080px;margin:0 auto;">
    <h3 style="font-family:'DM Serif Display',serif;font-size:18px;color:#f0ece4;margin-bottom:6px;letter-spacing:-0.01em;">Top destinations <em style="color:#d4ae4a;font-style:italic;">business</em></h3>
    <p style="font-size:12.5px;color:#6a6058;margin-bottom:18px;">Nos villes signature pour le voyage premium d'affaires.</p>
    <div style="display:flex;flex-wrap:wrap;gap:10px;">
      <style>.top-destinations-ssr .td-link{{padding:8px 16px;background:#1e1e1e;border:1px solid rgba(255,255,255,0.07);border-radius:99px;color:#a09890;text-decoration:none;font-size:12.5px;transition:all .15s;}}.top-destinations-ssr .td-link:hover{{border-color:#d4ae4a;color:#d4ae4a;}}</style>
      {links}
    </div>
  </div>
</section>
"""

def city_top_seo_hotels(city: str, limit: int = 4) -> list:
    """Top hôtels d'une ville AVEC fiche éditoriale (hotel_seo_content) — pour
    le maillage interne. Retourne [{slug, name, stars, country_code, path, photo}]."""
    if not city:
        return []
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT c.slug, c.name, c.stars, c.country_code, c.best_photo_url,
                   (SELECT main_image_url FROM hbx_hotels_catalog
                    WHERE giata_code = c.giata_code LIMIT 1) AS hbx_img
            FROM hotels_canonical c
            JOIN hotel_seo_content s ON s.slug = c.slug
            WHERE c.slug IS NOT NULL AND LOWER(c.city) = LOWER(%s)
            ORDER BY c.stars DESC NULLS LAST, c.total_photos DESC NULLS LAST
            LIMIT %s
        """, (city, limit))
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        return []
    out = []
    for r in rows:
        rd = dict(r)
        slug = rd.get("slug")
        if not slug:
            continue
        out.append({
            "slug": slug,
            "name": rd.get("name") or "",
            "stars": rd.get("stars") or 0,
            "country_code": rd.get("country_code") or "",
            "path": _hotel_seo_path(rd.get("country_code"), city, slug),
            "photo": rd.get("best_photo_url") or rd.get("hbx_img") or "",
        })
    return out

def route_real_airlines(origin: str, destination: str, limit: int = 8) -> list:
    """Compagnies RÉELLES opérant la ligne, depuis les deals en cache.
    Retourne [{name, nb}] triées par nb d'offres décroissant. Filtre le sandbox."""
    if not origin or not destination:
        return []
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airline_name, COUNT(*) AS nb
            FROM deals
            WHERE origin = %s AND destination = %s
              AND airline_name IS NOT NULL AND airline_name <> ''
            GROUP BY airline_name
            ORDER BY nb DESC
        """, (origin, destination))
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        return []
    out = []
    for r in rows:
        name = (r.get("airline_name") or "").strip()
        if not name or name.lower() in _NON_REAL_AIRLINES:
            continue
        out.append({"name": name, "nb": int(r.get("nb") or 0)})
    return out[:limit]

def city_interest_points(city: str, max_count: int = 10) -> list:
    """Agrège les interestPoints de TOUS les hôtels d'une ville (data HBX réelle).
    Classe par fréquence de citation (= notoriété observée), puis distance médiane.
    Dédup par nom normalisé en gardant la graphie la plus fréquente.
    Retourne [{name, freq, min_distance_m, distance_label}] ; vide si aucune data."""
    if not city:
        return []
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT trim(both ' ' from (p->>'poiName')) AS poi,
                   (p->>'distance')::int AS dist
            FROM hotels_canonical hc
            JOIN hbx_hotels_catalog hx ON hx.giata_code = hc.giata_code
            CROSS JOIN LATERAL jsonb_array_elements(hx.raw->'interestPoints') p
            WHERE LOWER(hc.city) = LOWER(%s)
              AND hx.raw->'interestPoints' IS NOT NULL
              AND (p->>'distance') ~ '^[0-9]+$'
              AND trim(both ' ' from (p->>'poiName')) <> ''
        """, (city,))
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        return []
    # Regroupe par nom normalisé
    groups = {}
    for r in rows:
        name = _clean_poi_name(r.get("poi"))
        if not name:
            continue
        try:
            dist_m = int(r.get("dist"))
        except (TypeError, ValueError):
            continue
        if dist_m < 0:
            continue
        key = _poi_dedup_key(name)
        if not key:
            continue
        g = groups.setdefault(key, {"variants": {}, "dists": []})
        g["variants"][name] = g["variants"].get(name, 0) + 1
        g["dists"].append(dist_m)
    out = []
    for key, g in groups.items():
        # Graphie la plus fréquente comme libellé canonique
        canonical = max(g["variants"].items(), key=lambda kv: kv[1])[0]
        freq = sum(g["variants"].values())
        dists = sorted(g["dists"])
        # Distance représentative = médiane (robuste aux artefacts type 1 m / 2 m)
        med = dists[len(dists) // 2] if dists else 0
        out.append({
            "name": canonical,
            "freq": freq,
            "median_distance_m": med,
            "distance_label": _fmt_poi_distance_m(med),
        })
    # Classement : les plus cités d'abord (notoriété), puis les plus proches
    out.sort(key=lambda x: (-x["freq"], x["median_distance_m"]))
    return out[:max_count]

def _hbx_dest_cache_refresh():
    import time as _t
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT code, name, country_code, hotels_known, zones FROM hbx_destinations WHERE is_closed=false")
    rows = cur.fetchall()
    cur.close(); conn.close()
    by_slug, by_code = {}, {}
    for r in rows:
        rd = dict(r)
        c = (rd.get("code") or "").lower()
        if c and c not in by_code:
            by_code[c] = rd
        s = _slugify(rd.get("name") or "")
        if s and s not in by_slug:
            by_slug[s] = rd
    _HBX_DEST_CACHE["by_slug"] = by_slug
    _HBX_DEST_CACHE["by_code"] = by_code
    _HBX_DEST_CACHE["ts"] = _t.time()

def _hbx_dest_lookup(slug: str):
    """Résolution slug → row hbx_destinations via cache mémoire."""
    import time as _t
    if (_t.time() - _HBX_DEST_CACHE["ts"]) > 3600 or not _HBX_DEST_CACHE["by_slug"]:
        try:
            _hbx_dest_cache_refresh()
        except Exception:
            return None
    s = (slug or "").lower().strip()
    return _HBX_DEST_CACHE["by_slug"].get(s) or _HBX_DEST_CACHE["by_code"].get(s)

def _render_hotel_unified(h: dict, mode: str = "seo") -> str:
    """SSR : génère le HTML d'1 page hôtel — SOURCE UNIQUE pour les 2 portes
    d'entrée (page SEO statique + page Quote dynamique).

    `mode` :
      - "seo"      : CTA = formulaire pré-inscription (coming-soon)
      - "bookable" : CTA = formulaire dates + tunnel résa (Phase 3)

    Phase 2B (2026-05-30) — Tout le contenu est identique entre les 2 modes
    sauf le bloc CTA. Aujourd'hui défaut = "seo" car Stripe encore en TEST.
    Le jour J : env var HOTEL_PAGE_DEFAULT_MODE=bookable → bascule en 1 ligne.
    """
    import json as _json

    name = h.get("name") or "Hôtel"
    stars = h.get("stars") or 0
    stars_str = "★" * stars
    city = h.get("city") or ""
    country = h.get("country_code") or ""
    address = h.get("address") or ""
    # SEO enrichi via DeepSeek (seo_enrich_hotels_deepseek.py) si dispo, sinon fallback HBX
    seo_intro = (h.get("seo_intro_fr") or "").strip()
    seo_why = (h.get("seo_why_business_fr") or "").strip()
    seo_neighb = (h.get("seo_neighborhood_fr") or "").strip()
    desc_raw = (h.get("description_en") or h.get("description_fr") or "")[:1500]
    # La description "principale" affichée : prio SEO enrichi, fallback HBX brute
    desc = seo_intro or desc_raw
    # Photo principale : on cherche la VRAIE hero (imageTypeCode='GEN' avec order min)
    # depuis hbx_raw, sinon fallback sur best_photo_url canonical, sinon main hbx
    _raw = h.get("hbx_raw")
    if isinstance(_raw, str):
        try: _raw = json.loads(_raw)
        except Exception: _raw = None
    _raw_images = (_raw or {}).get("images") if isinstance(_raw, dict) else None
    photo = (
        extract_best_main_photo(_raw_images, provider="hbx")
        or h.get("best_photo_url")
        or h.get("hbx_main_image")
        or ""
    )
    lat = h.get("latitude")
    lng = h.get("longitude")
    giata = h.get("giata_code")
    slug = h.get("slug")
    total_photos = h.get("total_photos") or 0
    chain = h.get("chain_code") or ""

    # URL canonique SEO : /hotels/{cc}/{ville}/{slug}
    seo_path = _hotel_seo_path(country, city, slug)
    canonical_url = f"https://airbizness.com{seo_path}"

    # Mapping ville → page hub (pour cross-link interne)
    city_lower = (city or "").lower()
    hub_link = None
    hub_slug = None
    if "madrid" in city_lower:
        hub_link = ("Madrid", "/destinations/madrid")
        hub_slug = "madrid"
    elif "paris" in city_lower:
        hub_link = ("Paris", "/destinations/paris")
        hub_slug = "paris"
    elif "london" in city_lower or "londres" in city_lower:
        hub_link = ("Londres", "/destinations/londres")
        hub_slug = "londres"

    # Distances : centre-ville + aéroport principal (si lat/lng dispo + ville reconnue)
    dist_centre_km = None
    dist_airport_km = None
    airport_name = None
    if lat and lng:
        city_key = _city_key(city or country or "")
        if city_key and city_key in CITY_CENTERS:
            cy, cx, _cname = CITY_CENTERS[city_key]
            dist_centre_km = haversine_km(float(lat), float(lng), cy, cx)
        if city_key and city_key in CITY_AIRPORTS:
            ay, ax, airport_name = CITY_AIRPORTS[city_key]
            dist_airport_km = haversine_km(float(lat), float(lng), ay, ax)

    def _fmt_km(km):
        if km is None:
            return None
        if km < 1:
            return f"{int(km * 1000)} m"
        if km < 10:
            return f"{km:.1f} km".replace(".", ",")
        return f"{int(round(km))} km"

    dist_centre_str = _fmt_km(dist_centre_km)
    dist_airport_str = _fmt_km(dist_airport_km)

    # ── Breadcrumbs : "Accueil > {Ville} > Hôtels > {Nom hôtel}"
    breadcrumb_items = [("Accueil", "/")]
    if hub_link:
        breadcrumb_items.append((hub_link[0], hub_link[1]))
    breadcrumb_items.append(("Hôtels", f"/destinations/{_city_url_slug(city)}" if city else "/"))
    breadcrumb_items.append((name, None))  # dernier = page courante

    # Schema.org BreadcrumbList (rich snippet Google)
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": label,
                **({"item": f"https://airbizness.com{url}"} if url else {}),
            }
            for i, (label, url) in enumerate(breadcrumb_items)
        ],
    }

    # Schema.org pour Google
    schema = {
        "@context": "https://schema.org",
        "@type": "Hotel",
        "name": name,
        "starRating": {"@type": "Rating", "ratingValue": stars} if stars else None,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": address,
            "addressLocality": city,
            "addressCountry": country,
        },
        "image": photo or None,
        "url": canonical_url,
    }
    if lat and lng:
        schema["geo"] = {"@type": "GeoCoordinates", "latitude": lat, "longitude": lng}
    if chain:
        schema["brand"] = {"@type": "Brand", "name": chain}
    schema = {k: v for k, v in schema.items() if v is not None}

    # ── Galerie 6 catégories (rooms, general, restaurant, bar, outdoor, other)
    # Source : h["gallery"] (déjà classée par get_hotel_unified_data).
    # Affichage : onglets en haut, grille en bas. JS minimal pour basculer.
    gallery_html = ""
    _gal = h.get("gallery") or {}
    _gal_total = sum(len(v) for v in _gal.values())
    if _gal_total > 0:
        _cat_labels = {
            "general": "Général", "rooms": "Chambres", "restaurant": "Restaurant",
            "bar": "Bar", "outdoor": "Extérieur", "other": "Autres",
        }
        # On ne montre que les onglets non vides ; tab actif = 1er non vide
        _tabs_order = [c for c in ["general", "rooms", "restaurant", "bar", "outdoor", "other"]
                       if _gal.get(c)]
        _active = _tabs_order[0] if _tabs_order else "general"
        _tabs = "".join(
            f'<button class="gtab{" active" if c == _active else ""}" data-cat="{c}">'
            f'{_cat_labels[c]} <span class="gcount">{len(_gal[c])}</span></button>'
            for c in _tabs_order
        )
        _grids = "".join(
            f'<div class="ggrid{" active" if c == _active else ""}" data-cat="{c}">'
            + "".join(
                f'<div class="gthumb"><img src="{html_escape(url)}" alt="{html_escape(name)} - {_cat_labels[c]}" loading="lazy"></div>'
                for url in _gal[c][:24]
            )
            + (f'<div class="gmore">+ {len(_gal[c]) - 24} autres</div>' if len(_gal[c]) > 24 else '')
            + '</div>'
            for c in _tabs_order
        )
        gallery_html = (
            f'<div class="card"><h2>Galerie <em>photos</em> '
            f'<span style="font-size:12px;color:var(--text3);font-style:normal;">({_gal_total} photos)</span></h2>'
            f'<div class="gtabs">{_tabs}</div>'
            f'<div class="gwrap">{_grids}</div>'
            '</div>'
        )

    # ── Équipements & services classés par catégorie (FR)
    facilities_html = ""
    _fac = h.get("facilities_by_category") or {}
    if _fac:
        _fac_labels = h.get("facility_category_labels") or {}
        _fac_blocks = []
        for cat, items in _fac.items():
            if not items:
                continue
            _label = _fac_labels.get(cat, cat.capitalize())
            # items peut être une liste de strings OU de dicts {label, group_code, facility_code}
            # selon le retour d'extract_facilities_fr. On extrait toujours le label affichable.
            _items_html = "".join(
                f'<li><span class="fac-tick">✓</span>{html_escape(it.get("label") if isinstance(it, dict) else it)}</li>'
                for it in items[:30]
                if (it.get("label") if isinstance(it, dict) else it)
            )
            _more = (f'<li class="fac-more">+ {len(items) - 30} autres</li>'
                     if len(items) > 30 else '')
            _fac_blocks.append(
                f'<div class="fac-cat"><h3>{html_escape(_label)}</h3>'
                f'<ul>{_items_html}{_more}</ul></div>'
            )
        if _fac_blocks:
            facilities_html = (
                '<div class="card"><h2>Équipements &amp; <em>services</em></h2>'
                f'<div class="fac-grid">{"".join(_fac_blocks)}</div>'
                '</div>'
            )

    # ── Bloc « Lieu & environs » — carte + POI + aéroports dans UN seul container
    # ALIGNÉ sur la page quote.html (même structure, même titres, même CSS).
    # Layout : grid 2 cols, carte à gauche, liste POI+aéroports à droite.
    _pois_calc = h.get("pois_nearby") or []
    _airports = h.get("airports") or []
    # Fallback : si pas de POI calculés (ville hors CITY_POIS hardcoded), on retombe
    # sur les interestPoints bruts HBX (cas des hôtels périphériques).
    if not _pois_calc:
        _pois_calc = hotel_interest_points(h.get("hbx_raw"), max_count=8) or []
        _pois_calc = [{"name": p["name"], "category": "",
                       "distance_km": None, "distance_label": p.get("distance_label", "")}
                      for p in _pois_calc]

    location_html = ""
    if lat and lng and (_pois_calc or _airports):
        def _km_label(km):
            if km is None: return ""
            if km < 1: return f"{int(km * 1000)} m"
            if km < 10: return f"{km:.1f} km".replace(".", ",")
            return f"{int(round(km))} km"
        _poi_items = "".join(
            f'<li class="loc-item">'
            f'<span class="loc-dist">{_km_label(p.get("distance_km")) or html_escape(p.get("distance_label","") or "")}</span>'
            f'<span class="loc-name">{html_escape(p["name"])}</span>'
            f'<span class="loc-cat">{html_escape(p.get("category","") or "")}</span>'
            '</li>'
            for p in _pois_calc[:8]
        )
        _air_items = "".join(
            f'<li class="loc-item">'
            f'<span class="loc-dist">{_km_label(a.get("distance_km"))}</span>'
            f'<span class="loc-name">{html_escape(a.get("name",""))}</span>'
            f'<span class="loc-cat">{html_escape(a.get("iata",""))}</span>'
            '</li>'
            for a in _airports[:4]
        )
        _side_blocks = []
        if _poi_items:
            _side_blocks.append(
                '<div class="loc-section">'
                '<div class="loc-section-title">À voir à proximité</div>'
                f'<ul class="loc-list">{_poi_items}</ul>'
                '</div>'
            )
        if _air_items:
            _side_blocks.append(
                '<div class="loc-section">'
                '<div class="loc-section-title">Aéroports</div>'
                f'<ul class="loc-list">{_air_items}</ul>'
                '</div>'
            )
        location_html = (
            '<div class="section-title">Lieu &amp; <em>environs</em></div>'
            '<div class="location-block">'
            '<div class="location-grid">'
            '<div class="location-map-wrap">'
            '<div id="hotel-map" class="hotel-map"></div>'
            '</div>'
            '<div class="location-side">'
            + "".join(_side_blocks)
            + '</div></div></div>'
        )
    elif lat and lng:
        # Carte seule (pas de POI dispo)
        location_html = (
            '<div class="section-title">Lieu &amp; <em>environs</em></div>'
            '<div class="location-block">'
            '<div class="location-map-wrap" style="min-height:380px;">'
            '<div id="hotel-map" class="hotel-map"></div>'
            '</div></div>'
        )

    # Compat : variables historiques pour l'ancien template (vides désormais).
    nearby_html = ""
    map_html = ""

    # ── Bloc Transferts proposés par l'hôtel (provider AirBizness natif) ──
    # Lit `h["transfers"]` du carnet partagé (services/hotel_data.py).
    # Plus aucune query SQL ad-hoc ici — DRY : 1 source de vérité pour tout.
    # Mode "seo" (coming-soon) → liste info avec mention "Réservable à l'ouverture".
    # Mode "bookable" (jour J) → boutons réserver actifs (POST /api/airbizness/transfers/bookings).
    transfers_html = ""
    _transfers = h.get("transfers") or []
    if _transfers:
        _trf_cards = []
        for _t in _transfers:
            _price = float(_t["gross_price_eur"])
            if mode == "bookable":
                _cta = (f'<button class="trf-btn" onclick="bookTransfer({_t["id"]})">'
                        f'Réserver — {_price:.2f} €</button>')
            else:
                _cta = (f'<div class="trf-price">{_price:.2f} €</div>'
                        f'<div class="trf-soon">Réservable à l\'ouverture</div>')
            _spec_bits = []
            if _t.get("vehicle_type"): _spec_bits.append(_t["vehicle_type"].capitalize())
            if _t.get("max_passengers"): _spec_bits.append(f'{_t["max_passengers"]} pax')
            if _t.get("max_luggage"): _spec_bits.append(f'{_t["max_luggage"]} bagages')
            _trf_cards.append(
                '<div class="trf-card">'
                f'<div class="trf-label">{html_escape(_t["label"])}</div>'
                f'<div class="trf-route">{html_escape(_t["from_location"])} → {html_escape(_t["to_location"])}</div>'
                f'<div class="trf-spec">{" · ".join(_spec_bits)}</div>'
                + (f'<div class="trf-cancel">{html_escape(_t["cancellation_policy"])}</div>'
                   if _t.get("cancellation_policy") else '')
                + f'<div class="trf-cta">{_cta}</div>'
                '</div>'
            )
        transfers_html = (
            '<div class="card"><h2>Transferts proposés par <em>cet hôtel</em></h2>'
            f'<p>Services de transport vers/depuis l\'hôtel, organisés directement par '
            f'l\'équipe de {html_escape(name)}.</p>'
            f'<div class="trf-grid">{"".join(_trf_cards)}</div>'
            '</div>'
        )

    title = f"{name} {('★'*stars) if stars else ''} — {city or 'AirBizness'} | AirBizness Business"
    # Si SEO enrichi, on en tire une meta description premium (160 chars max)
    if seo_intro:
        meta_desc = (seo_intro[:155].rsplit(' ', 1)[0] + '…') if len(seo_intro) > 155 else seo_intro
    else:
        meta_desc = (
            f"{name} {('★'*stars + ' à ') if stars else ''}"
            f"{city}{', ' + country if country else ''}. "
            f"Réservation Business Class · tarifs négociés AirBizness."
        )[:160]

    # ── Internal linking : "Autres hôtels à {ville}" (5-10 voisins même ville)
    neighbors_html = ""
    city_slug_for_link = hub_slug or (_slugify(city) if city else "")
    if city and slug:
        try:
            _conn2 = psycopg2.connect(**DB_CONFIG)
            _cur2 = _conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _cur2.execute("""
                SELECT slug, name, stars, best_photo_url
                FROM hotels_canonical
                WHERE LOWER(city) = LOWER(%s) AND slug IS NOT NULL AND slug <> %s
                ORDER BY stars DESC NULLS LAST, total_photos DESC NULLS LAST
                LIMIT 8
            """, (city, slug))
            _neighbors = _cur2.fetchall()
            _cur2.close(); _conn2.close()
            if _neighbors:
                _items = []
                for n in _neighbors:
                    nd = dict(n)
                    nstars = "★" * (nd.get("stars") or 0)
                    nimg = nd.get("best_photo_url") or ""
                    nimg_html = (f'<div style="aspect-ratio:4/3;background:var(--bg3);overflow:hidden;border-radius:8px 8px 0 0;">'
                                 f'<img src="{html_escape(nimg)}" alt="{html_escape(nd["name"])}" loading="lazy" '
                                 f'style="width:100%;height:100%;object-fit:cover;"></div>') if nimg else ''
                    _items.append(
                        f'<a href="{html_escape(_hotel_seo_path(country, city, nd["slug"]))}" '
                        f'style="background:var(--bg);border:1px solid var(--border);border-radius:10px;'
                        f'text-decoration:none;color:inherit;overflow:hidden;display:flex;flex-direction:column;transition:border-color .2s;">'
                        f'{nimg_html}'
                        f'<div style="padding:10px 12px;">'
                        f'<div style="color:var(--gold2);font-size:11px;margin-bottom:3px;">{nstars}</div>'
                        f'<div style="font-family:DM Serif Display,serif;font-size:14px;line-height:1.25;color:var(--text);">{html_escape(nd["name"])}</div>'
                        f'</div></a>'
                    )
                _hub_link_html = (
                    f'<div style="text-align:center;margin-top:20px;">'
                    f'<a href="/destinations/{html_escape(city_slug_for_link)}" '
                    f'style="display:inline-block;padding:11px 22px;border:1px solid var(--border2);'
                    f'color:var(--gold2);text-decoration:none;border-radius:8px;font-size:13.5px;">'
                    f'Voir tous les hôtels à {html_escape(city)} →</a></div>'
                ) if city_slug_for_link else ''
                neighbors_html = (
                    f'<div class="card ab-neighbors"><h2>Autres hôtels à <em>{html_escape(city)}</em></h2>'
                    f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:14px;">'
                    + "".join(_items) +
                    f'</div>{_hub_link_html}</div>'
                )
        except Exception:
            neighbors_html = ""

    # Bloc SSR Top destinations (footer global)
    try:
        top_dest_footer_ssr = _render_top_destinations_footer_ssr()
    except Exception:
        top_dest_footer_ssr = ""

    # ── CTA conditionnel selon mode (Phase 2B) ──
    # mode="seo"      : formulaire pré-inscription email (coming-soon)
    # mode="bookable" : formulaire dates + bouton tunnel résa (Phase 3)
    if mode == "bookable":
        cta_html = (
            '<div class="cta-card">'
            '<div class="cta-tag" style="background:rgba(184,150,46,0.18);color:var(--gold2);">RÉSERVER</div>'
            '<h3>Vérifier la disponibilité</h3>'
            '<p class="sub">Sélectionnez vos dates pour voir les chambres disponibles et le prix négocié AirBizness.</p>'
            '<form class="cta-form" id="resa-form" onsubmit="goQuote(event)">'
            '<label style="font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;">Arrivée</label>'
            '<input type="date" id="r-checkin" required>'
            '<label style="font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;margin-top:4px;">Départ</label>'
            '<input type="date" id="r-checkout" required>'
            '<label style="font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;margin-top:4px;">Voyageurs</label>'
            '<input type="number" id="r-adults" value="2" min="1" max="9" required>'
            '<button type="submit">Voir les prix</button>'
            '</form>'
            '<div class="cta-note">Tarifs Business négociés · paiement sécurisé · voucher à votre nom.</div>'
            '</div>'
        )
        disclaimer_html = ''
    else:
        cta_html = (
            '<div class="cta-card">'
            '<div class="cta-tag">PRÉ-INSCRIPTION</div>'
            "<h3>Soyez prévenu(e) à l'ouverture</h3>"
            '<p class="sub">Cet hôtel sera réservable directement sur AirBizness dès juillet 2026. Inscrivez-vous pour être informé(e) en priorité.</p>'
            '<form class="cta-form" onsubmit="notifyLaunch(event)">'
            '<input type="email" id="email" placeholder="votre@email.com" required>'
            "<button type=\"submit\">M'avertir au lancement</button>"
            '</form>'
            '<div class="cta-success" id="success">Inscription enregistrée. Vous serez prévenu(e) en priorité.</div>'
            "<div class=\"cta-note\">Un email à l'ouverture. Aucun spam. Désinscription en 1 clic.</div>"
            '</div>'
        )
        disclaimer_html = (
            '<div class="disclaimer">'
            "<strong>AirBizness est en phase de pré-lancement.</strong> "
            "La réservation directe sera disponible à l'ouverture officielle prévue en juillet 2026. "
            'Les tarifs et disponibilités affichés sont indicatifs, issus de notre catalog professionnel.'
            '</div>'
        )

    # ── Widget comparateur multi-providers (Booking / Expedia / Agoda) ──
    # 3 boutons distincts en URL provider direct (search par nom hôtel + ville).
    # Hotellook gateway abandonné pour ce widget (ne respecte pas utm_source).
    hbx_code_for_widget = h.get("hbx_hotel_code") or h.get("giata_code") or ""
    comparator_widget_html = (
        '<div class="ab-comparator-widget" style="margin:32px 0;padding:24px;background:#1a1a2e;border-radius:12px;border:1px solid #d4ae4a;">'
        '<div style="text-align:center;">'
        "<p style=\"color:#fff;margin-bottom:12px;\">✉ M'avertir de l'ouverture d'AirBizness en direct sur cet hôtel</p>"
        '<form action="/api/wishlist/subscribe" method="POST" style="display:flex;gap:8px;justify-content:center;max-width:400px;margin:0 auto;flex-wrap:wrap;">'
        f'<input type="hidden" name="hotel_code" value="{html_escape(hbx_code_for_widget)}">'
        '<input type="email" name="email" placeholder="votre@email.com" required '
        'style="flex:1;min-width:200px;padding:10px 14px;border-radius:6px;border:1px solid #333;background:#0a0a14;color:#fff;">'
        '<button type="submit" style="background:#d4ae4a;color:#0a0a14;padding:10px 18px;border-radius:6px;border:none;font-weight:600;cursor:pointer;">'
        "M'avertir"
        '</button>'
        '</form>'
        '</div>'
        '</div>'
    )

    # ── Bloc partenaire Booking #1 — lien DIRECT booking.com (2026-06-19) ──
    # Monétisé par CJ Affiliate : am.js (allCJ) réécrit ce lien AU CLIC en lien affilié.
    # am.js ne peut réécrire qu'un lien pointant déjà vers l'annonceur → PAS de proxy ici.
    # Notre mesure est conservée côté client (GA4 booking_click via data-ab-partner) +
    # beacon serveur (/api/affiliate-log) pour garder Booking dans /admin-affiliate.html.
    from urllib.parse import quote_plus as _quote_plus
    _booking_query = _quote_plus(f"{name} {city or ''} {country or ''}".strip())
    _booking_hotel_code = str(hbx_code_for_widget or "").strip()
    booking_partner_url = f"https://www.booking.com/searchresults.html?ss={_booking_query}"
    booking_partner_html = (
        '<div class="ab-booking-partner" style="margin:40px 0; padding:32px; background:linear-gradient(135deg,#1a1a2e,#0a0a14); border-radius:16px; border:2px solid #d4ae4a; text-align:center;">'
        '<div style="color:#d4ae4a; font-size:14px; letter-spacing:2px; text-transform:uppercase; margin-bottom:12px;">✨ Nos partenaires</div>'
        '<h3 style="color:#fff; font-family:\'DM Serif Display\',Georgia,serif; font-size:28px; margin:8px 0 16px;">Réserver cet hôtel</h3>'
        '<p style="color:#aaa; margin-bottom:24px; max-width:500px; margin-left:auto; margin-right:auto;">Profitez des meilleurs tarifs disponibles chez Booking, partenaire de confiance d\'AirBizness.</p>'
        f'<a href="{html_escape(booking_partner_url)}" target="_blank" rel="noopener nofollow sponsored" '
        f'data-ab-partner="booking" data-ab-hotel="{html_escape(_booking_hotel_code)}" '
        'style="display:inline-block; background:#003580; color:#fff; padding:18px 48px; border-radius:8px; font-weight:600; text-decoration:none; font-size:17px; box-shadow:0 4px 16px rgba(0,53,128,0.4); transition:transform 0.2s;" '
        'onmouseover="this.style.transform=\'translateY(-2px)\'" onmouseout="this.style.transform=\'translateY(0)\'">'
        '📖 Voir cet hôtel sur Booking →'
        '</a>'
        '<div style="color:#777; font-size:13px; margin-top:16px;">Réservation sécurisée chez notre partenaire — Paiement direct sur leur site</div>'
        '</div>'
    )

    # ── Bloc « Comparer les prix » multi-partenaires (module ②, 2026-06-19) ──
    # Une ligne par OTA. Chaque lien passe par /api/affiliate-redirect (log serveur
    # affiliate_clicks + injection ID .env) et porte data-compare-partner → le listener
    # gtag (module ①) émet affiliate_click + compare_prices_click. On sait donc, par
    # partenaire, qui a cliqué — côté serveur ET GA4. Registre : services/affiliate_partners.py.
    from services.affiliate_partners import PARTNERS as _PARTNERS
    _cmp_rows = []
    for _p in _PARTNERS:
        _dest = _p["url"](name, city or "", country or "")
        _href = (
            f"/api/affiliate-redirect?provider={_p['key']}"
            f"&hotel_code={_quote_plus(str(hbx_code_for_widget or '').strip())}"
            f"&dest={_quote_plus(_dest)}"
        )
        _cmp_rows.append(
            f'<a href="{html_escape(_href)}" target="_blank" rel="noopener nofollow sponsored" '
            f'data-compare-partner="{_p["key"]}" '
            'style="display:flex;align-items:center;justify-content:space-between;gap:12px;'
            'padding:14px 18px;margin:8px 0;background:#0a0a14;border:1px solid #2a2a3e;'
            'border-radius:10px;text-decoration:none;transition:border-color .2s;" '
            'onmouseover="this.style.borderColor=\'#d4ae4a\'" onmouseout="this.style.borderColor=\'#2a2a3e\'">'
            f'<span style="display:flex;align-items:center;gap:12px;color:#fff;font-weight:600;">'
            f'<span style="width:10px;height:10px;border-radius:50%;background:{_p["color"]};"></span>'
            f'{html_escape(_p["label"])}</span>'
            '<span style="color:#d4ae4a;font-weight:600;white-space:nowrap;">Voir le prix →</span>'
            '</a>'
        )
    compare_block_html = (
        '<div class="ab-compare-block" style="margin:32px 0;padding:24px;background:linear-gradient(135deg,#1a1a2e,#0a0a14);border-radius:16px;border:1px solid #2a2a3e;">'
        '<div style="color:#d4ae4a;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;text-align:center;">Comparer les prix</div>'
        '<h3 style="color:#fff;font-family:\'DM Serif Display\',Georgia,serif;font-size:24px;margin:0 0 18px;text-align:center;">Le meilleur tarif chez nos partenaires</h3>'
        + "".join(_cmp_rows) +
        '<div style="color:#777;font-size:12px;margin-top:14px;text-align:center;">Liens partenaires — la réservation et le paiement se font sur le site choisi.</div>'
        '</div>'
    )

    # ── Leaflet assets (chargés seulement si lat/lng dispo) ──
    leaflet_head = (
        '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" '
        'integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">'
        '<script defer src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" '
        'integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>'
    ) if (lat and lng) else ''

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f0f0f">
<meta name="google-site-verification" content="mPjKOFEzqb0WNWWJc_h1pQaBW-_Vid87r5fE_EWoGBg" />
<title>{html_escape(title)}</title>
<meta name="description" content="{html_escape(meta_desc)}">
<link rel="canonical" href="{canonical_url}">

<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:title" content="{html_escape(title)}">
<meta property="og:description" content="{html_escape(meta_desc)}">
<meta property="og:url" content="{canonical_url}">
{('<meta property="og:image" content="' + html_escape(photo) + '">') if photo else ''}
<meta property="og:site_name" content="AirBizness">
<meta property="og:locale" content="fr_FR">

<!-- Twitter -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html_escape(title)}">
<meta name="twitter:description" content="{html_escape(meta_desc)}">
{('<meta name="twitter:image" content="' + html_escape(photo) + '">') if photo else ''}

<!-- Schema.org Hotel pour Google -->
<script type="application/ld+json">{_json.dumps(schema, ensure_ascii=False)}</script>
<!-- Schema.org BreadcrumbList (rich snippet hiérarchique) -->
<script type="application/ld+json">{_json.dumps(breadcrumb_schema, ensure_ascii=False)}</script>

{leaflet_head}
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}}
:root{{--bg:#0f0f0f;--bg2:#161616;--bg3:#1e1e1e;--gold:#b8962e;--gold2:#d4ae4a;--gold-dim:rgba(184,150,46,0.12);--text:#f0ece4;--text2:#a09890;--text3:#6a6058;--border:rgba(255,255,255,0.07);--border2:rgba(184,150,46,0.2);--green:#4ade80;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.65;}}

.wrap{{max-width:1100px;margin:0 auto;padding:24px;}}

.hero{{position:relative;border-radius:16px;overflow:hidden;margin-bottom:28px;background:var(--bg2);min-height:420px;}}
.hero img{{width:100%;height:520px;object-fit:cover;display:block;}}
.hero-overlay{{position:absolute;inset:0;background:linear-gradient(180deg,transparent 30%,rgba(15,15,15,0.95) 100%);}}
.hero-meta{{position:absolute;left:32px;right:32px;bottom:28px;color:#fff;}}
.hero-chain{{display:inline-block;background:rgba(184,150,46,0.2);color:var(--gold2);padding:5px 12px;border-radius:99px;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;font-weight:500;margin-bottom:12px;}}
.hero-stars{{color:var(--gold2);font-size:16px;margin-bottom:8px;}}
.hero-title{{font-family:'DM Serif Display',serif;font-size:clamp(28px,5vw,46px);line-height:1.1;margin-bottom:8px;letter-spacing:-0.01em;color:#fff;}}
.hero-loc{{font-size:14px;color:rgba(255,255,255,0.8);}}
/* Breadcrumbs (sous header, au-dessus du hero) */
.breadcrumbs{{max-width:1100px;margin:0 auto;padding:18px 24px 0;}}
.breadcrumbs ol{{list-style:none;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:0;margin:0;font-size:12.5px;color:var(--text3);}}
.breadcrumbs li{{display:inline-flex;align-items:center;}}
.breadcrumbs a{{color:var(--text2);text-decoration:none;transition:color .15s;}}
.breadcrumbs a:hover{{color:var(--gold2);}}
.breadcrumbs li[aria-current="page"],.breadcrumbs span[aria-current="page"]{{color:var(--text);font-weight:500;}}
.breadcrumbs .bc-sep{{color:var(--text3);opacity:0.5;font-size:11px;}}
/* Badges sous le titre (étoiles, distance centre, distance aéroport, etc.) */
.hero-badges{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;}}
.hero-badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;background:rgba(255,255,255,0.1);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.18);border-radius:99px;font-size:11.5px;color:#fff;font-weight:500;letter-spacing:0.1px;}}
.hero-badge svg{{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;opacity:0.85;}}
.hero-badge.gold{{color:var(--gold2);border-color:rgba(184,150,46,0.35);background:rgba(184,150,46,0.12);}}

.content{{display:grid;grid-template-columns:1fr 360px;gap:28px;}}
@media(max-width:780px){{.content{{grid-template-columns:1fr;}}}}

.card{{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:18px;}}
.card h2{{font-family:'DM Serif Display',serif;font-size:22px;margin-bottom:12px;letter-spacing:-0.01em;}}
.card h2 em{{font-style:italic;color:var(--gold2);}}
.card p{{color:var(--text2);font-size:14px;line-height:1.75;}}

.meta-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;}}
.meta-item{{background:var(--bg3);padding:14px;border-radius:10px;}}
.meta-label{{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--text3);margin-bottom:4px;}}
.meta-value{{font-size:14px;color:var(--text);}}
.meta-value strong{{color:var(--gold2);font-family:'DM Serif Display',serif;font-size:18px;}}

.gallery{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px;}}
.gthumb{{aspect-ratio:1/1;background:var(--bg3);border-radius:8px;overflow:hidden;}}
.gthumb img{{width:100%;height:100%;object-fit:cover;}}
@media(max-width:560px){{.gallery{{grid-template-columns:repeat(2,1fr);}}}}

/* À proximité — POI réels (raw->interestPoints HBX) */
.poi-list{{display:flex;flex-direction:column;gap:0;}}
.poi-row{{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:11px 0;border-bottom:1px solid var(--border);}}
.poi-row:last-child{{border-bottom:none;}}
.poi-name{{font-size:14px;color:var(--text);}}
.poi-dist{{font-size:13px;color:var(--gold2);font-weight:500;white-space:nowrap;font-variant-numeric:tabular-nums;}}

/* CTA Pre-launch (sidebar) */
.cta-card{{background:linear-gradient(135deg,var(--gold-dim) 0%,var(--bg2) 80%);border:1px solid var(--border2);border-radius:14px;padding:22px;position:sticky;top:24px;}}
.cta-tag{{display:inline-block;background:rgba(74,222,128,0.15);color:var(--green);padding:4px 10px;border-radius:99px;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;font-weight:600;margin-bottom:12px;}}
.cta-card h3{{font-family:'DM Serif Display',serif;font-size:20px;line-height:1.2;margin-bottom:8px;}}
.cta-card .sub{{color:var(--text2);font-size:13px;margin-bottom:18px;line-height:1.55;}}
.cta-form{{display:flex;flex-direction:column;gap:8px;}}
.cta-form input{{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px 14px;color:var(--text);font-family:inherit;font-size:14px;outline:none;}}
.cta-form input:focus{{border-color:var(--border2);}}
.cta-form button{{background:var(--gold);color:#000;border:none;padding:13px;border-radius:8px;font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;}}
.cta-form button:hover{{background:var(--gold2);}}
.cta-note{{font-size:11px;color:var(--text3);margin-top:10px;line-height:1.5;}}
.cta-success{{display:none;padding:12px;background:rgba(74,222,128,0.12);color:var(--green);border-radius:8px;font-size:13px;text-align:center;}}

.disclaimer{{padding:16px 20px;background:var(--bg2);border:1px solid var(--border);border-radius:10px;font-size:12px;color:var(--text3);text-align:center;margin-top:24px;line-height:1.6;}}
.disclaimer strong{{color:var(--gold2);}}
.editorial-credit{{padding:12px 18px;background:transparent;border-left:2px solid var(--border2);border-radius:0;font-size:11.5px;color:var(--text3);font-style:italic;margin-top:8px;margin-bottom:24px;line-height:1.65;}}
.editorial-credit a{{color:var(--gold2);text-decoration:none;border-bottom:1px solid rgba(184,150,46,0.3);}}
.editorial-credit a:hover{{border-bottom-color:var(--gold2);}}

/* Phase 2B — Galerie 6 catégories à onglets */
.gtabs{{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 16px;}}
.gtab{{background:var(--bg3);border:1px solid var(--border);color:var(--text2);padding:8px 14px;border-radius:99px;font-family:inherit;font-size:12.5px;cursor:pointer;transition:all .15s;display:inline-flex;align-items:center;gap:6px;}}
.gtab:hover{{border-color:var(--border2);color:var(--text);}}
.gtab.active{{background:var(--gold-dim);border-color:var(--border2);color:var(--gold2);font-weight:600;}}
.gcount{{font-size:10.5px;background:rgba(255,255,255,0.08);color:inherit;padding:2px 7px;border-radius:99px;}}
.gtab.active .gcount{{background:rgba(184,150,46,0.25);}}
.gwrap{{position:relative;}}
.ggrid{{display:none;grid-template-columns:repeat(4,1fr);gap:8px;}}
.ggrid.active{{display:grid;}}
@media(max-width:560px){{.ggrid{{grid-template-columns:repeat(2,1fr);}}}}
.gmore{{grid-column:1/-1;text-align:center;padding:14px;background:var(--bg3);border-radius:8px;color:var(--text3);font-size:12.5px;}}

/* Phase 2B — Équipements & services */
.fac-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px;margin-top:14px;}}
.fac-cat h3{{font-family:'DM Serif Display',serif;font-size:15px;color:var(--gold2);margin-bottom:10px;font-style:italic;}}
.fac-cat ul{{list-style:none;padding:0;margin:0;}}
.fac-cat li{{font-size:13px;color:var(--text2);padding:5px 0;display:flex;align-items:flex-start;gap:8px;line-height:1.5;}}
.fac-tick{{color:var(--gold2);font-weight:600;flex-shrink:0;}}
.fac-more{{color:var(--text3);font-style:italic;font-size:12px;}}

/* Phase 2B — Bloc « Lieu & environs » (aligné quote.html) */
.section-title{{font-family:'DM Serif Display',serif;font-size:22px;margin:24px 0 14px;letter-spacing:-0.01em;}}
.section-title em{{font-style:italic;color:var(--gold2);}}
.location-block{{background:var(--bg2);border:1px solid var(--border);border-radius:14px;overflow:hidden;margin-bottom:18px;}}
.location-grid{{display:grid;grid-template-columns:1.4fr 1fr;gap:0;}}
@media(max-width:880px){{.location-grid{{grid-template-columns:1fr;}}}}
.location-map-wrap{{position:relative;background:var(--bg3);min-height:380px;}}
@media(max-width:880px){{.location-map-wrap{{min-height:280px;}}}}
.hotel-map{{width:100%;height:100%;min-height:380px;}}
@media(max-width:880px){{.hotel-map{{min-height:280px;}}}}
.location-side{{padding:24px 26px;display:flex;flex-direction:column;gap:24px;}}
.loc-section-title{{font-family:'JetBrains Mono','DM Sans',monospace;font-size:10.5px;font-weight:500;letter-spacing:0.16em;text-transform:uppercase;color:var(--gold2);margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid var(--border);}}
.loc-list{{list-style:none;padding:0;margin:0;}}
.loc-item{{display:grid;grid-template-columns:60px 1fr auto;gap:10px;align-items:baseline;padding:7px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,0.04);}}
.loc-item:last-child{{border-bottom:none;}}
.loc-dist{{color:var(--gold2);font-variant-numeric:tabular-nums;font-size:12.5px;font-weight:500;}}
.loc-name{{color:var(--text);line-height:1.35;}}
.loc-cat{{color:var(--text3);font-size:10.5px;text-transform:uppercase;letter-spacing:0.08em;font-family:'JetBrains Mono','DM Sans',monospace;}}
.leaflet-container{{background:#1a1a1a;font-family:'DM Sans',sans-serif;}}
.leaflet-popup-content-wrapper{{background:#161616;color:#f0ece4;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,0.6);}}
.leaflet-popup-tip{{background:#161616;}}
.leaflet-popup-content{{margin:12px 14px;font-size:13px;line-height:1.5;}}
.leaflet-control-attribution{{background:rgba(15,15,15,0.85)!important;color:var(--text3)!important;font-size:10px!important;}}
.leaflet-control-attribution a{{color:var(--gold2)!important;}}

/* Phase 2B — POI category badge */
.poi-cat{{font-size:10.5px;color:var(--text3);text-transform:uppercase;letter-spacing:0.8px;margin-left:6px;}}

/* Transferts AirBizness natif (publiés par l'hôtelier revendiqué) */
.trf-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:16px;}}
.trf-card{{background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:18px;display:flex;flex-direction:column;gap:8px;}}
.trf-label{{font-family:'DM Serif Display',serif;font-size:15px;color:var(--text);line-height:1.3;}}
.trf-route{{font-size:12.5px;color:var(--text2);line-height:1.5;}}
.trf-spec{{font-size:11.5px;color:var(--text3);text-transform:uppercase;letter-spacing:0.06em;}}
.trf-cancel{{font-size:11.5px;color:var(--text3);font-style:italic;padding-top:6px;border-top:1px dashed var(--border);margin-top:4px;}}
.trf-cta{{margin-top:auto;padding-top:10px;}}
.trf-price{{font-family:'DM Serif Display',serif;font-size:22px;color:var(--gold2);font-variant-numeric:tabular-nums;}}
.trf-soon{{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:0.1em;margin-top:2px;}}
.trf-btn{{display:inline-block;background:var(--gold);color:#000;border:none;padding:10px 18px;border-radius:8px;font-family:inherit;font-size:13px;font-weight:600;cursor:pointer;width:100%;}}
.trf-btn:hover{{background:var(--gold2);}}
</style>
<script defer src="/shared-chrome.js"></script>
</head>
<body>
<div id="ab-header"></div>
<div id="ab-bottomnav"></div>

<nav class="breadcrumbs" aria-label="Fil d'Ariane">
  <ol>{"".join(
    (f'<li><a href="{html_escape(_url)}">{html_escape(_label)}</a></li><li class="bc-sep">›</li>' if _url
     else f'<li><span aria-current="page">{html_escape(_label)}</span></li>')
    for _label, _url in breadcrumb_items
  )}</ol>
</nav>

<div class="wrap">

  <!-- HERO -->
  <div class="hero">
    {('<img src="' + html_escape(photo) + '" alt="' + html_escape(name) + '">') if photo else ''}
    <div class="hero-overlay"></div>
    <div class="hero-meta">
      {('<div class="hero-chain">' + html_escape(chain) + '</div>') if chain else ''}
      <h1 class="hero-title">{html_escape(name)}</h1>
      {('<div class="hero-stars">' + stars_str + ' ' + str(stars) + ' étoiles</div>') if stars else ''}
      <div class="hero-loc"><span style="color:var(--gold2);">•</span> {html_escape(address)}{(', ' + html_escape(str(h.get('postal_code')))) if h.get('postal_code') else ''} · {html_escape(city)}{(', ' + html_escape(country)) if country else ''}</div>
      {(
        '<div class="hero-badges">' +
        ((f'<span class="hero-badge gold"><svg viewBox="0 0 24 24"><circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 0 0-8 8c0 6 8 12 8 12s8-6 8-12a8 8 0 0 0-8-8z"/></svg>{dist_centre_str} du centre</span>') if dist_centre_str else '') +
        ((f'<span class="hero-badge"><svg viewBox="0 0 24 24"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>{dist_airport_str} {html_escape(airport_name)}</span>') if dist_airport_str and airport_name else '') +
        '</div>'
      ) if (dist_centre_str or dist_airport_str) else ''}
    </div>
  </div>

  <div class="content">
    <div class="main">

      <div class="card">
        <h2>À <em>propos</em></h2>
        <p>{html_escape(desc) if desc else 'Description bientôt disponible. Notre équipe finalise la fiche de cet établissement.'}</p>
      </div>

      {(f'<div class="card"><h2>Pourquoi cet hôtel pour un voyageur <em>Business</em></h2><p>{html_escape(seo_why)}</p></div>') if seo_why else ''}

      {(f'<div class="card"><h2>Le <em>quartier</em></h2><p>{html_escape(seo_neighb)}</p></div>') if seo_neighb else ''}

      {('<div class="editorial-credit">Description éditoriale rédigée par AirBizness · Informations factuelles (catégorie, adresse, équipements, photos) fournies par nos partenaires distributeurs hôteliers. <a href="/pour-les-hoteliers.html">Vous êtes le gestionnaire de cet établissement ?</a></div>') if seo_intro else ''}

      {(f'<div class="card"><h2>Explorer <em>{hub_link[0]}</em></h2><p>Découvrez notre sélection complète d’adresses premium à {hub_link[0]}, ainsi que notre guide des quartiers et les vols Business depuis nos principales origines voyageurs.</p><a href="{hub_link[1]}" style="display:inline-block;margin-top:14px;padding:11px 22px;border:1px solid var(--border2);color:var(--gold2);text-decoration:none;border-radius:8px;font-size:13.5px;font-weight:500;">Voir le guide {hub_link[0]} →</a></div>') if hub_link else ''}

      <div class="card">
        <h2>Informations <em>pratiques</em></h2>
        <div class="meta-grid">
          <div class="meta-item">
            <div class="meta-label">Étoiles</div>
            <div class="meta-value"><strong>{stars or '—'}</strong></div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Photos disponibles</div>
            <div class="meta-value"><strong>{total_photos}</strong></div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Ville</div>
            <div class="meta-value">{html_escape(city or '—')}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Pays</div>
            <div class="meta-value">{html_escape(country or '—')}</div>
          </div>
        </div>
      </div>

      {gallery_html}

      {facilities_html}

      {location_html}

      {transfers_html}

      <div class="card">
        <h2>L'expérience <em>AirBizness</em></h2>
        <p>Tarifs négociés en direct, paiement sécurisé chez nous, voucher à votre nom à présenter à l'hôtel. Notre équipe SAV est joignable 7j/7. Lancement officiel <strong style="color:var(--gold2);">juillet 2026</strong>.</p>
      </div>

      {booking_partner_html}

      {compare_block_html}

      {comparator_widget_html}

      {neighbors_html}

      {disclaimer_html}

    </div>

    <aside>
      {cta_html}
    </aside>
  </div>

</div>

{top_dest_footer_ssr}

<div id="ab-footer"></div>

<script>
/* AirBizness Analytics GA4 (Pascal 2026-06-19) — mesure du parcours sur la fiche hôtel.
   UN listener délégué (gtag déjà chargé via shared-chrome.js) → events :
   booking_click / affiliate_click / gallery_open / map_open / similar_hotel_click
   (compare_prices_click prêt pour le futur comparateur multi-partenaires, module 2). */
(function(){{
  var H = {{ hotel: {_json.dumps(name or '')}, city: {_json.dumps(city or '')}, hotel_code: {_json.dumps(str(hbx_code_for_widget or ''))} }};
  function abEvt(n, extra){{ try {{ if (window.gtag) window.gtag('event', n, Object.assign({{}}, H, extra || {{}})); }} catch(_e){{}} }}
  window.abEvt = abEvt;
  document.addEventListener('click', function(e){{
    var t = e.target; if (!t || !t.closest) return;
    var aff = t.closest('a[href*="affiliate-redirect"]');
    if (aff) {{ var m = (aff.getAttribute('href')||'').match(/provider=([^&]+)/); var prov = m ? m[1] : 'partner';
      abEvt('affiliate_click', {{ partner: prov }}); if (prov === 'booking') abEvt('booking_click', {{ partner: 'booking' }}); return; }}
    // Lien partenaire DIRECT (ex. Booking, réécrit par CJ am.js) : GA4 + beacon serveur (garde la trace dans le dashboard).
    var dp = t.closest('a[data-ab-partner]');
    if (dp) {{ var p = dp.getAttribute('data-ab-partner') || 'partner';
      abEvt('affiliate_click', {{ partner: p }}); if (p === 'booking') abEvt('booking_click', {{ partner: 'booking' }});
      try {{ fetch('/api/affiliate-log?provider=' + encodeURIComponent(p) + '&hotel_code=' + encodeURIComponent(dp.getAttribute('data-ab-hotel')||''), {{ method:'GET', keepalive:true, cache:'no-store' }}); }} catch(_e){{}}
      return; }}
    if (t.closest('.gtab') || t.closest('.ggrid img') || t.closest('.gallery-img')) {{ abEvt('gallery_open'); return; }}
    if (t.closest('#hotel-map')) {{ abEvt('map_open'); return; }}
    var nb = t.closest('.ab-neighbors a[href^="/hotels/"]'); if (nb) {{ abEvt('similar_hotel_click', {{ to: nb.getAttribute('href') }}); return; }}
    var cmp = t.closest('[data-compare-partner]'); if (cmp) {{ abEvt('compare_prices_click', {{ partner: cmp.getAttribute('data-compare-partner') || '' }}); return; }}
  }}, true);
}})();
async function notifyLaunch(e){{
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  if (!email) return;
  try {{
    await fetch('/api/leads/notify-launch', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, giata_code:'{giata}', source:'hotel_page'}})
    }});
  }} catch(e){{}}
  document.querySelector('.cta-form').style.display = 'none';
  document.getElementById('success').style.display = 'block';
}}

/* Phase 2B — Onglets galerie (basculer la grille visible) */
document.querySelectorAll('.gtab').forEach(function(t){{
  t.addEventListener('click', function(){{
    var cat = t.getAttribute('data-cat');
    document.querySelectorAll('.gtab').forEach(function(x){{ x.classList.remove('active'); }});
    t.classList.add('active');
    document.querySelectorAll('.ggrid').forEach(function(g){{
      g.classList.toggle('active', g.getAttribute('data-cat') === cat);
    }});
  }});
}});

/* Phase 2B — Carte Leaflet (init quand L est chargé) */
window.addEventListener('load', function(){{
  var el = document.getElementById('hotel-map');
  if (!el || typeof L === 'undefined') return;
  var lat = {lat or 0}, lng = {lng or 0};
  if (!lat || !lng) return;
  var map = L.map('hotel-map', {{scrollWheelZoom:false}}).setView([lat, lng], 14);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '© OpenStreetMap',
    maxZoom: 19
  }}).addTo(map);
  L.marker([lat, lng]).addTo(map)
    .bindPopup('<div class="pop-title">{html_escape(name)}</div>')
    .openPopup();
}});

/* Phase 2B — Tunnel résa (mode=bookable, Phase 3 le câblera vraiment) */
function goQuote(e){{
  e.preventDefault();
  var ci = document.getElementById('r-checkin').value;
  var co = document.getElementById('r-checkout').value;
  var ad = document.getElementById('r-adults').value;
  if (!ci || !co) return;
  var params = new URLSearchParams({{
    code: '{h.get("hbx_hotel_code") or ""}',
    giata: '{giata}',
    checkin: ci, checkout: co, adults: ad
  }});
  window.location.href = '/quote.html?' + params.toString();
}}
</script>
<!-- CJ Affiliate — Auto Deep Link (allCJ) + impressions/page (PID 101805872, 2026-06-19).
     Traceur tiers : chargé UNIQUEMENT après consentement cookies (RGPD). Réécrit au clic
     tout lien sortant vers un annonceur CJ (dont booking.com) en lien affilié. -->
<script>
(function(){{
  var SRC = 'https://www.anrdoezrs.net/am/101805872/include/allCJ/impressions/page/am.js';
  var loaded = false;
  function loadCJ(){{ if (loaded) return; loaded = true;
    var s = document.createElement('script'); s.src = SRC; s.async = true; document.body.appendChild(s); }}
  try {{ if (localStorage.getItem('ab_cookie_consent') === 'accepted') loadCJ(); }} catch(_e){{}}
  window.addEventListener('ab-consent', function(ev){{ if (ev && ev.detail === 'accepted') loadCJ(); }});
}})();
</script>
</body>
</html>"""


# Alias backward-compat
_render_hotel_seo_page = _render_hotel_unified

def _render_destination_hub(city_slug: str, dest: dict) -> str:
    """Génère la page HTML hub d'une destination — SSR avec données live."""
    code = dest["code"]
    name = dest["name"]
    country = dest["country"]
    country_code = dest["country_code"]

    # 1. Stats live depuis le catalog
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT COUNT(*) AS n FROM hbx_hotels_catalog
        WHERE destination_code = %s
    """, (code,))
    total_hotels = cur.fetchone()["n"]

    # 2. Top 6 hôtels signature (5*, avec photo, ranking le mieux noté)
    # On récupère `raw` pour pouvoir appliquer le helper extract_best_main_photo()
    # qui choisit la VRAIE photo principale (GEN+order min, pas une chambre ou un détail spa)
    cur.execute("""
        SELECT hc.hotel_code, hc.name, hc.category_stars, hc.city, hc.main_image_url,
               hc.description_en, hc.ranking, hc.raw,
               can.slug AS canonical_slug
        FROM hbx_hotels_catalog hc
        LEFT JOIN hotels_provider_map hpm
            ON hpm.provider='hbx' AND hpm.provider_hotel_code = hc.hotel_code::text
        LEFT JOIN hotels_canonical can ON can.giata_code = hpm.giata_code
        WHERE hc.destination_code = %s
          AND hc.category_stars >= 5
          AND hc.main_image_url IS NOT NULL
          AND hc.main_image_url <> ''
        ORDER BY hc.ranking DESC NULLS LAST, hc.hotel_code
        LIMIT 6
    """, (code,))
    top_hotels = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()

    # ── Top hotels cards : on override main_image_url avec la VRAIE photo principale
    cards = []
    for h in top_hotels:
        slug = h.get("canonical_slug") or f"hotel-{h['hotel_code']}"
        stars = "★" * (h.get("category_stars") or 0)
        # Helper : prend GEN avec order min (pas HAB chambre ni DEP équipement)
        _raw = h.get("raw")
        if isinstance(_raw, str):
            try: _raw = json.loads(_raw)
            except Exception: _raw = None
        _imgs = (_raw or {}).get("images") if isinstance(_raw, dict) else None
        img = extract_best_main_photo(_imgs, provider="hbx") or h.get("main_image_url") or ""
        href = f"/h/{slug}" if h.get("canonical_slug") else f"/quote.html?code={h['hotel_code']}"
        cards.append(
            f'<a class="hub-hotel-card" href="{href}">'
            f'  <div class="hub-hotel-img"><img src="{img}" alt="{html_esc(h["name"])}" loading="lazy"></div>'
            f'  <div class="hub-hotel-body">'
            f'    <div class="hub-hotel-stars">{stars}</div>'
            f'    <div class="hub-hotel-name">{html_esc(h["name"])}</div>'
            f'    <div class="hub-hotel-city">{html_esc(h.get("city") or "")}</div>'
            f'  </div>'
            f'</a>'
        )
    cards_html = "\n".join(cards)

    # ── Pourquoi business cards
    why_html = "\n".join(
        f'<div class="hub-why-card"><div class="hub-why-title">{html_esc(t)}</div><div class="hub-why-desc">{html_esc(d)}</div></div>'
        for (t, d) in dest["why_business"]
    )

    # ── Quartiers
    quartiers_html = "\n".join(
        f'<div class="hub-quartier-card">'
        f'  <div class="hub-quartier-label">{html_esc(q["label"])}</div>'
        f'  <h3 class="hub-quartier-name">{html_esc(q["nom"])}</h3>'
        f'  <p class="hub-quartier-desc">{html_esc(q["desc"])}</p>'
        f'</div>'
        for q in dest["quartiers"]
    )

    # ── Saisons
    saison_html = "\n".join(
        f'<tr><td class="saison-period">{html_esc(p)}</td><td class="saison-desc">{html_esc(d)}</td></tr>'
        for (p, d) in dest["saison"]
    )

    # ── « À voir à [ville] » : POI agrégés depuis les hôtels de la ville (HBX réel)
    _city_pois = city_interest_points(name, max_count=10)
    avoir_html = ""
    if _city_pois:
        _poi_items = "".join(
            f'<div class="hub-poi-item"><span class="hub-poi-name">{html_esc(p["name"])}</span>'
            + (f'<span class="hub-poi-dist">≈ {html_esc(p["distance_label"])}</span>' if p.get("distance_label") else '')
            + '</div>'
            for p in _city_pois
        )
        avoir_html = f"""
<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Sur place</div>
      <h2>À voir à <em>{html_esc(name)}</em></h2>
      <p class="hub-section-lead">Les lieux et points d'intérêt les plus fréquemment cités par nos hôtels {html_esc(name)}, avec leur distance moyenne au centre hôtelier.</p>
    </div>
    <div class="hub-poi-grid">
      {_poi_items}
    </div>
  </div>
</section>"""

    # ── Schema.org JSON-LD
    schema = {
        "@context": "https://schema.org",
        "@type": "TouristDestination",
        "name": f"{name}, {country}",
        "description": dest["intro"][:300],
        "geo": {"@type": "GeoCoordinates", "latitude": dest["lat"], "longitude": dest["lon"]},
        "touristType": ["Business travelers", "Premium leisure"],
        "url": f"https://airbizness.com/destinations/{city_slug}",
        "containedInPlace": {"@type": "Country", "name": country, "addressCountry": country_code},
    }

    title = f"Hôtels & vols Business Class à {name} | AirBizness"
    description = f"{dest['tagline']} {total_hotels} hôtels premium sélectionnés à {name}. Vol Business + hôtel 5★ en un tunnel."

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0f0f0f">
<meta name="google-site-verification" content="mPjKOFEzqb0WNWWJc_h1pQaBW-_Vid87r5fE_EWoGBg" />
<title>{html_esc(title)}</title>
<meta name="description" content="{html_esc(description)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{html_esc(title)}">
<meta property="og:description" content="{html_esc(description)}">
<meta property="og:url" content="https://airbizness.com/destinations/{city_slug}">
<meta property="og:image" content="https://airbizness.com{dest['hero_image']}">
<link rel="canonical" href="https://airbizness.com/destinations/{city_slug}">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}}
:root{{
  --bg:#0f0f0f;--bg2:#161616;--bg3:#1e1e1e;--bg4:#252525;
  --gold:#b8962e;--gold2:#d4ae4a;--gold-dim:rgba(184,150,46,0.12);
  --text:#f0ece4;--text2:#a09890;--text3:#6a6058;
  --border:rgba(255,255,255,0.07);--border2:rgba(184,150,46,0.2);
}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.65;}}

/* Hero */
.hub-hero{{position:relative;padding:80px 24px 60px;background-size:cover;background-position:center;min-height:480px;display:flex;align-items:center;}}
.hub-hero::before{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(15,15,15,0.55) 0%,rgba(15,15,15,0.85) 100%);}}
.hub-hero-inner{{position:relative;z-index:2;max-width:880px;margin:0 auto;text-align:center;}}
.hub-hero-tag{{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,0.08);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.12);padding:7px 16px;border-radius:99px;color:#fff;font-size:11px;letter-spacing:1.8px;text-transform:uppercase;font-weight:500;margin-bottom:24px;}}
.hub-hero h1{{font-family:'DM Serif Display',serif;font-size:clamp(40px,6vw,68px);line-height:1.05;color:#fff;margin-bottom:18px;letter-spacing:-0.015em;}}
.hub-hero h1 em{{font-style:italic;color:var(--gold2);}}
.hub-hero-tagline{{font-size:clamp(17px,2vw,21px);color:rgba(255,255,255,0.92);font-weight:300;margin-bottom:32px;}}
.hub-hero-stats{{display:flex;justify-content:center;flex-wrap:wrap;gap:32px;margin-top:16px;}}
.hub-hero-stat{{text-align:center;}}
.hub-hero-stat-val{{font-family:'DM Serif Display',serif;font-size:36px;color:var(--gold2);line-height:1;}}
.hub-hero-stat-lbl{{font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.7);margin-top:6px;}}
.hub-hero-cta{{margin-top:32px;display:inline-block;padding:14px 32px;background:var(--gold);color:#000;font-family:inherit;font-size:14px;font-weight:600;border-radius:8px;text-decoration:none;letter-spacing:0.3px;transition:background .15s;}}
.hub-hero-cta:hover{{background:var(--gold2);}}

/* Sections */
.hub-section{{padding:70px 24px;border-bottom:1px solid var(--border);}}
.hub-section.alt{{background:var(--bg2);}}
.hub-section-inner{{max-width:1080px;margin:0 auto;}}
.hub-section-head{{text-align:center;margin-bottom:42px;}}
.hub-eyebrow{{display:inline-block;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--gold2);font-weight:500;margin-bottom:14px;}}
.hub-section-head h2{{font-family:'DM Serif Display',serif;font-size:clamp(28px,4vw,42px);letter-spacing:-0.01em;line-height:1.15;}}
.hub-section-head h2 em{{font-style:italic;color:var(--gold2);}}
.hub-section-lead{{color:var(--text2);font-size:15.5px;max-width:680px;margin:16px auto 0;line-height:1.7;}}

/* Intro narrative */
.hub-intro{{max-width:760px;margin:0 auto;text-align:center;font-size:17px;line-height:1.85;color:var(--text2);}}
.hub-intro p{{margin-bottom:16px;}}

/* Why business — 4 cards */
.hub-why-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;}}
@media(max-width:680px){{.hub-why-grid{{grid-template-columns:1fr;}}}}
.hub-why-card{{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:22px 24px;}}
.hub-why-title{{font-family:'DM Serif Display',serif;font-size:17px;color:var(--gold2);margin-bottom:6px;}}
.hub-why-desc{{font-size:13.5px;color:var(--text2);line-height:1.6;}}

/* Top hotels grid */
.hub-hotels-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:32px;}}
@media(max-width:880px){{.hub-hotels-grid{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:540px){{.hub-hotels-grid{{grid-template-columns:1fr;}}}}
.hub-hotel-card{{background:var(--bg);border:1px solid var(--border);border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;transition:border-color .2s,transform .2s;display:flex;flex-direction:column;}}
.hub-hotel-card:hover{{border-color:var(--border2);transform:translateY(-3px);}}
.hub-hotel-img{{aspect-ratio:4/3;background:var(--bg3);overflow:hidden;}}
.hub-hotel-img img{{width:100%;height:100%;object-fit:cover;transition:transform .5s;}}
.hub-hotel-card:hover .hub-hotel-img img{{transform:scale(1.05);}}
.hub-hotel-body{{padding:16px;}}
.hub-hotel-stars{{color:var(--gold2);font-size:12px;letter-spacing:1px;margin-bottom:6px;}}
.hub-hotel-name{{font-family:'DM Serif Display',serif;font-size:17px;color:var(--text);line-height:1.25;letter-spacing:-0.01em;margin-bottom:4px;}}
.hub-hotel-city{{font-size:12px;color:var(--text3);}}
.hub-hotels-more{{text-align:center;}}
.hub-hotels-more a{{display:inline-block;padding:13px 28px;border:1px solid var(--border2);color:var(--gold2);text-decoration:none;border-radius:8px;font-size:13.5px;font-weight:500;letter-spacing:0.3px;transition:background .15s;}}
.hub-hotels-more a:hover{{background:var(--gold-dim);}}

/* Quartiers */
.hub-quartiers-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;}}
@media(max-width:680px){{.hub-quartiers-grid{{grid-template-columns:1fr;}}}}
.hub-quartier-card{{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:24px 26px;}}
.hub-quartier-label{{font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;color:var(--gold2);margin-bottom:8px;font-weight:500;}}
.hub-quartier-name{{font-family:'DM Serif Display',serif;font-size:22px;color:var(--text);margin-bottom:12px;letter-spacing:-0.01em;}}
.hub-quartier-desc{{font-size:14px;color:var(--text2);line-height:1.7;}}

/* À voir — POI agrégés (data HBX réelle) */
.hub-poi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;max-width:960px;margin:0 auto;}}
.hub-poi-item{{display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}}
.hub-poi-name{{font-size:14.5px;color:var(--text);}}
.hub-poi-dist{{font-size:12px;color:var(--gold2);white-space:nowrap;font-variant-numeric:tabular-nums;}}

/* Saisons */
.hub-saison-table{{width:100%;max-width:780px;margin:0 auto;border-collapse:collapse;}}
.hub-saison-table td{{padding:18px 0;border-bottom:1px solid var(--border);font-size:15px;}}
.saison-period{{color:var(--gold2);font-weight:500;width:200px;}}
.saison-desc{{color:var(--text2);}}

/* CTA final */
.hub-final-cta{{padding:90px 24px;background:linear-gradient(135deg,var(--gold-dim) 0%,var(--bg) 70%);text-align:center;}}
.hub-final-cta-inner{{max-width:680px;margin:0 auto;}}
.hub-final-cta h2{{font-family:'DM Serif Display',serif;font-size:clamp(30px,4.5vw,44px);margin-bottom:14px;letter-spacing:-0.01em;}}
.hub-final-cta h2 em{{font-style:italic;color:var(--gold2);}}
.hub-final-cta p{{color:var(--text2);font-size:16px;margin-bottom:30px;}}
.hub-final-cta-btn{{display:inline-block;padding:16px 38px;background:var(--gold);color:#000;text-decoration:none;font-weight:600;font-size:15px;letter-spacing:0.3px;border-radius:9px;transition:background .15s;}}
.hub-final-cta-btn:hover{{background:var(--gold2);}}

/* Sister destinations */
.hub-sister{{padding:50px 24px;background:var(--bg2);border-top:1px solid var(--border);text-align:center;}}
.hub-sister h3{{font-family:'DM Serif Display',serif;font-size:22px;margin-bottom:18px;}}
.hub-sister-links{{display:flex;justify-content:center;gap:14px;flex-wrap:wrap;}}
.hub-sister-link{{padding:10px 22px;background:var(--bg);border:1px solid var(--border);border-radius:99px;color:var(--text);text-decoration:none;font-size:13.5px;transition:all .15s;}}
.hub-sister-link:hover{{border-color:var(--gold2);color:var(--gold2);}}
</style>
<script defer src="/shared-chrome.js"></script>
<script defer src="/cookies.js"></script>
</head>
<body>
<div id="ab-header"></div>
<div id="ab-bottomnav"></div>

<section class="hub-hero" style="background-image:url('{dest['hero_image']}');">
  <div class="hub-hero-inner">
    <div class="hub-hero-tag">Destination AirBizness · {html_esc(country)}</div>
    <h1>Hôtels & vols Business à <em>{html_esc(name)}</em></h1>
    <p class="hub-hero-tagline">{html_esc(dest['tagline'])}</p>
    <div class="hub-hero-stats">
      {('<div class="hub-hero-stat"><div class="hub-hero-stat-val">' + str(total_hotels) + '</div><div class="hub-hero-stat-lbl">Hôtels en stock</div></div>') if (total_hotels and int(total_hotels or 0) > 0) else ''}
      <div class="hub-hero-stat"><div class="hub-hero-stat-val">5★</div><div class="hub-hero-stat-lbl">Sélection premium</div></div>
      <div class="hub-hero-stat"><div class="hub-hero-stat-val">−5%</div><div class="hub-hero-stat-lbl">Pack vol + hôtel</div></div>
    </div>
    <a href="/sejour.html?destination={code}" class="hub-hero-cta">Construire mon séjour {html_esc(name)} →</a>
  </div>
</section>

<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">{html_esc(name)} en quelques mots</div>
      <h2>Pourquoi <em>{html_esc(name)}</em> en Business Class</h2>
    </div>
    <div class="hub-intro">
      <p>{html_esc(dest['intro'])}</p>
    </div>
  </div>
</section>

<section class="hub-section alt">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Accès & lounges</div>
      <h2>Vols <em>Business</em> vers {html_esc(name)}</h2>
      <p class="hub-section-lead">Toutes les rotations Business Class principales depuis nos zones d'origine voyageurs.</p>
    </div>
    <div class="hub-why-grid">
      {why_html}
    </div>
  </div>
</section>

<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Adresses signature</div>
      <h2>Top hôtels <em>5 étoiles</em> à {html_esc(name)}</h2>
      <p class="hub-section-lead">Sélection des {len(top_hotels)} adresses les mieux référencées dans notre catalogue de {total_hotels} hôtels {html_esc(name)}.</p>
    </div>

    <!-- Recherche d'un hôtel par son nom (déplacée depuis la home, 2026-05-31 Pascal)
         Filtre auto sur la ville courante {name} → résultats pertinents. -->
    <div class="dest-name-search" style="background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:12px;padding:14px 18px;margin-bottom:24px;">
      <div style="font-size:11.5px;font-weight:600;letter-spacing:.4px;color:var(--gold2);text-transform:uppercase;margin-bottom:8px;display:flex;align-items:center;gap:7px;">
        <svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.7;"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        Trouver un hôtel par son nom à {html_esc(name)}
      </div>
      <div style="position:relative;">
        <input type="text" id="dest-hotel-name-q"
               placeholder="ex. Ritz, Sofitel, Hilton…"
               autocomplete="off" spellcheck="false"
               style="width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:11px 14px;color:var(--text);font-family:inherit;font-size:14px;outline:none;" />
        <div id="dest-hotel-name-q-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;background:var(--bg2);border:1px solid var(--border);border-radius:8px;margin-top:4px;max-height:320px;overflow-y:auto;z-index:50;box-shadow:0 8px 24px rgba(0,0,0,0.4);"></div>
      </div>
    </div>
    <script>
    (function() {{
      const input = document.getElementById('dest-hotel-name-q');
      const drop = document.getElementById('dest-hotel-name-q-dropdown');
      let _t = null;
      input.addEventListener('input', () => {{
        clearTimeout(_t);
        const q = input.value.trim();
        if (q.length < 2) {{ drop.style.display = 'none'; return; }}
        _t = setTimeout(async () => {{
          try {{
            const r = await fetch('/api/hotels/autocomplete?q=' + encodeURIComponent(q) +
                                  '&city=' + encodeURIComponent({json.dumps(name)}));
            const items = await r.json();
            if (!items.length) {{
              drop.innerHTML = '<div style="padding:14px;color:var(--text3);font-size:13px;">Aucun hôtel trouvé.</div>';
              drop.style.display = 'block';
              return;
            }}
            drop.innerHTML = items.map(it => `
              <a href="${{it.url}}" style="display:block;padding:11px 14px;border-bottom:1px solid var(--border);color:var(--text);text-decoration:none;font-size:13.5px;">
                <div style="font-weight:500;">${{it.name}}</div>
                <div style="color:var(--text3);font-size:11.5px;margin-top:2px;">${{it.city}} · ${{it.country_code||''}}</div>
              </a>`).join('');
            drop.style.display = 'block';
          }} catch(e) {{ drop.style.display = 'none'; }}
        }}, 200);
      }});
      document.addEventListener('click', e => {{
        if (!e.target.closest('.dest-name-search')) drop.style.display = 'none';
      }});
    }})();
    </script>

    <div class="hub-hotels-grid">
      {cards_html}
    </div>
    <div class="hub-hotels-more">
      <a href="/hotels.html?dest={html_esc(name)}">Voir les {total_hotels} hôtels {html_esc(name)} →</a>
    </div>
  </div>
</section>

<section class="hub-section alt">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Où séjourner</div>
      <h2>Guide des <em>quartiers</em></h2>
      <p class="hub-section-lead">4 quartiers signature pour adapter votre séjour à votre style de voyage.</p>
    </div>
    <div class="hub-quartiers-grid">
      {quartiers_html}
    </div>
  </div>
</section>
{avoir_html}
<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Climat & affluence</div>
      <h2>Meilleure <em>saison</em> pour {html_esc(name)}</h2>
    </div>
    <table class="hub-saison-table">
      {saison_html}
    </table>
  </div>
</section>

<section class="hub-final-cta">
  <div class="hub-final-cta-inner">
    <h2>Votre <em>séjour {html_esc(name)}</em> en quelques clics</h2>
    <p>Vol Business + hôtel 5★ en un tunnel unique, voucher AirBizness, paiement sécurisé Stripe.</p>
    <a href="/sejour.html?destination={code}" class="hub-final-cta-btn">Construire mon séjour →</a>
  </div>
</section>

<section class="hub-sister">
  <h3>Autres destinations AirBizness</h3>
  <div class="hub-sister-links">
    <a class="hub-sister-link" href="/destinations/paris">Paris</a>
    <a class="hub-sister-link" href="/destinations/madrid">Madrid</a>
    <a class="hub-sister-link" href="/destinations/londres">Londres</a>
  </div>
</section>

{_render_top_destinations_footer_ssr()}

<div id="ab-footer"></div>
</body>
</html>"""

def _render_destination_hub_hbx(city_slug: str, dest_row: dict, hotels: list, country_label: str = None) -> str:
    """Page-ville SSR fallback HBX : pour les 7207 villes sans contenu hardcodé."""
    code = dest_row.get("code") or ""
    name = (dest_row.get("name") or city_slug).strip()
    # Nettoie suffixes type " - KS" ou ", DC" qu'on trouve dans certains noms HBX
    name_clean = name.split(" - ")[0].split(",")[0].strip().title() if name else city_slug.title()
    country_code = dest_row.get("country_code") or ""
    pays = country_label or country_code or ""
    nb_hotels = len(hotels)

    # Cards hôtels (top 50 avec photo)
    cards = []
    for h in hotels[:50]:
        slug = h.get("slug") or ""
        if not slug:
            continue
        stars = "★" * (h.get("stars") or 0)
        img = h.get("photo") or ""
        hname = h.get("name") or ""
        addr = h.get("address") or ""
        img_html = (f'<img src="{html_esc(img)}" alt="{html_esc(hname)}" loading="lazy" '
                    f'style="width:100%;height:100%;object-fit:cover;">') if img else ''
        cards.append(
            f'<a class="hbx-card" href="/h/{html_esc(slug)}">'
            f'<div class="hbx-card-img">{img_html}</div>'
            f'<div class="hbx-card-body">'
            f'<div class="hbx-card-stars">{stars}</div>'
            f'<div class="hbx-card-name">{html_esc(hname)}</div>'
            f'<div class="hbx-card-addr">{html_esc(addr)}</div>'
            f'</div></a>'
        )
    cards_html = "\n".join(cards) if cards else (
        '<div style="grid-column:1/-1;text-align:center;padding:40px 20px;color:#a09890;font-size:14px;">'
        f'Notre catalog d\'hôtels à {html_esc(name_clean)} arrive bientôt. '
        '<a href="/" style="color:#d4ae4a;">Inscrivez-vous</a> pour être prévenu(e).'
        '</div>'
    )

    # Intro auto
    if nb_hotels > 0:
        intro = (
            f"Découvrez notre sélection de {nb_hotels} hôtels à {name_clean}"
            + (f", {pays}" if pays else "")
            + ". Tarifs négociés AirBizness, paiement sécurisé, voucher à votre nom. "
            "Notre catalog premium couvre les adresses signature et les hôtels Business "
            "les mieux placés du centre-ville et à proximité des aéroports principaux."
        )
    else:
        intro = (
            f"{name_clean}{', ' + pays if pays else ''} fait partie de notre réseau de "
            "destinations AirBizness. Le catalog d'hôtels Business de cette ville sera ouvert "
            "très prochainement. Inscrivez-vous pour être prévenu(e) à l'ouverture."
        )

    # ── « À voir à [ville] » : POI agrégés depuis les hôtels (HBX réel)
    # On agrège par le nom de ville réel des hôtels (dest_row name peut différer).
    _city_for_poi = (hotels[0].get("city") if hotels else None) or name_clean
    _city_pois = city_interest_points(_city_for_poi, max_count=10)
    avoir_html = ""
    if _city_pois:
        _poi_items = "".join(
            f'<div class="hbx-poi-item"><span class="hbx-poi-name">{html_esc(p["name"])}</span>'
            + (f'<span class="hbx-poi-dist">≈ {html_esc(p["distance_label"])}</span>' if p.get("distance_label") else '')
            + '</div>'
            for p in _city_pois
        )
        avoir_html = f"""
<section class="hbx-section">
  <h2>À voir à <em>{html_esc(name_clean)}</em></h2>
  <p style="color:var(--text2);font-size:14.5px;margin-bottom:22px;max-width:680px;">Les lieux et points d'intérêt les plus fréquemment cités par nos hôtels à {html_esc(name_clean)}, avec leur distance moyenne au centre hôtelier.</p>
  <div class="hbx-poi-grid">
    {_poi_items}
  </div>
</section>"""

    # Schema.org TouristDestination + ItemList
    item_list = {
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "url": f"https://airbizness.com/h/{h['slug']}",
                "name": h.get("name") or "",
            }
            for i, h in enumerate(hotels[:50]) if h.get("slug")
        ],
    }
    dest_schema = {
        "@context": "https://schema.org",
        "@type": "TouristDestination",
        "name": f"{name_clean}{', ' + pays if pays else ''}",
        "description": intro[:300],
        "url": f"https://airbizness.com/destinations/{city_slug}",
        "containedInPlace": ({"@type": "Country", "addressCountry": country_code} if country_code else None),
    }
    dest_schema = {k: v for k, v in dest_schema.items() if v is not None}
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://airbizness.com/"},
            {"@type": "ListItem", "position": 2, "name": pays or "Destinations", "item": "https://airbizness.com/"},
            {"@type": "ListItem", "position": 3, "name": name_clean},
        ],
    }

    title = f"Hôtels à {name_clean}{(' — ' + pays) if pays else ''} | AirBizness Business"
    meta_desc = (
        f"Réservez parmi {nb_hotels} hôtels premium à {name_clean}"
        + (f" ({pays})" if pays else "")
        + ". Tarifs négociés AirBizness, voucher direct, lancement juillet 2026."
    )[:160] if nb_hotels else (
        f"Hôtels à {name_clean}{(' (' + pays + ')') if pays else ''}. "
        "AirBizness ouvre cette destination très prochainement."
    )[:160]

    top_dest_footer = _render_top_destinations_footer_ssr()

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f0f0f">
<meta name="google-site-verification" content="mPjKOFEzqb0WNWWJc_h1pQaBW-_Vid87r5fE_EWoGBg" />
<title>{html_esc(title)}</title>
<meta name="description" content="{html_esc(meta_desc)}">
<link rel="canonical" href="https://airbizness.com/destinations/{html_esc(city_slug)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{html_esc(title)}">
<meta property="og:description" content="{html_esc(meta_desc)}">
<meta property="og:url" content="https://airbizness.com/destinations/{html_esc(city_slug)}">
<meta property="og:site_name" content="AirBizness">
<meta property="og:locale" content="fr_FR">
<script type="application/ld+json">{json.dumps(dest_schema, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(breadcrumb_schema, ensure_ascii=False)}</script>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
:root{{--bg:#0f0f0f;--bg2:#161616;--bg3:#1e1e1e;--gold:#b8962e;--gold2:#d4ae4a;--gold-dim:rgba(184,150,46,0.12);--text:#f0ece4;--text2:#a09890;--text3:#6a6058;--border:rgba(255,255,255,0.07);--border2:rgba(184,150,46,0.2);}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.65;}}
.bc{{max-width:1080px;margin:0 auto;padding:18px 24px 0;font-size:12.5px;color:var(--text3);}}
.bc a{{color:var(--text2);text-decoration:none;}}
.bc a:hover{{color:var(--gold2);}}
.bc-sep{{opacity:0.5;margin:0 6px;}}
.hbx-hero{{padding:60px 24px 40px;background:linear-gradient(180deg,var(--bg2) 0%,var(--bg) 100%);text-align:center;}}
.hbx-hero h1{{font-family:'DM Serif Display',serif;font-size:clamp(32px,5vw,52px);line-height:1.1;margin-bottom:10px;letter-spacing:-0.015em;}}
.hbx-hero h1 em{{font-style:italic;color:var(--gold2);}}
.hbx-hero-tag{{display:inline-block;padding:6px 14px;background:var(--gold-dim);border:1px solid var(--border2);border-radius:99px;color:var(--gold2);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:18px;}}
.hbx-hero-sub{{max-width:680px;margin:14px auto 0;color:var(--text2);font-size:15.5px;line-height:1.7;}}
.hbx-section{{max-width:1080px;margin:0 auto;padding:50px 24px;}}
.hbx-section h2{{font-family:'DM Serif Display',serif;font-size:26px;margin-bottom:24px;letter-spacing:-0.01em;}}
.hbx-section h2 em{{font-style:italic;color:var(--gold2);}}
.hbx-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;}}
@media(max-width:880px){{.hbx-grid{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:560px){{.hbx-grid{{grid-template-columns:1fr;}}}}
.hbx-card{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;transition:border-color .2s,transform .2s;display:flex;flex-direction:column;}}
.hbx-card:hover{{border-color:var(--border2);transform:translateY(-3px);}}
.hbx-card-img{{aspect-ratio:4/3;background:var(--bg3);overflow:hidden;}}
.hbx-card-body{{padding:14px 16px;}}
.hbx-card-stars{{color:var(--gold2);font-size:12px;letter-spacing:1px;margin-bottom:5px;min-height:14px;}}
.hbx-card-name{{font-family:'DM Serif Display',serif;font-size:16px;color:var(--text);line-height:1.25;margin-bottom:4px;}}
.hbx-card-addr{{font-size:11.5px;color:var(--text3);line-height:1.45;}}
.hbx-cta{{padding:50px 24px;text-align:center;background:linear-gradient(135deg,var(--gold-dim) 0%,var(--bg) 70%);border-top:1px solid var(--border);}}
.hbx-cta h2{{font-family:'DM Serif Display',serif;font-size:26px;margin-bottom:10px;}}
.hbx-cta h2 em{{font-style:italic;color:var(--gold2);}}
.hbx-cta p{{color:var(--text2);max-width:560px;margin:0 auto 22px;font-size:14.5px;}}
.hbx-cta a{{display:inline-block;padding:13px 28px;background:var(--gold);color:#000;text-decoration:none;font-weight:600;font-size:14px;border-radius:8px;}}
.hbx-cta a:hover{{background:var(--gold2);}}
.hbx-back{{padding:24px;text-align:center;font-size:13px;}}
.hbx-back a{{color:var(--text2);text-decoration:none;}}
.hbx-back a:hover{{color:var(--gold2);}}
.hbx-section h2 em{{font-style:italic;color:var(--gold2);}}
.hbx-poi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;}}
.hbx-poi-item{{display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}}
.hbx-poi-name{{font-size:14.5px;color:var(--text);}}
.hbx-poi-dist{{font-size:12px;color:var(--gold2);white-space:nowrap;font-variant-numeric:tabular-nums;}}
</style>
<script defer src="/shared-chrome.js"></script>
</head>
<body>
<div id="ab-header"></div>

<nav class="bc" aria-label="Fil d'Ariane">
  <a href="/">Accueil</a><span class="bc-sep">›</span>
  {('<a href="/">' + html_esc(pays) + '</a><span class="bc-sep">›</span>') if pays else ''}
  <span aria-current="page" style="color:var(--text);">{html_esc(name_clean)}</span>
</nav>

<section class="hbx-hero">
  <div class="hbx-hero-tag">Destination AirBizness{(' · ' + html_esc(pays)) if pays else ''}</div>
  <h1>Hôtels à <em>{html_esc(name_clean)}</em></h1>
  <p class="hbx-hero-sub">{html_esc(intro)}</p>
</section>

<section class="hbx-section">
  <h2>Notre sélection <em>{html_esc(name_clean)}</em></h2>
  <div class="hbx-grid">
    {cards_html}
  </div>
</section>
{avoir_html}
<section class="hbx-cta">
  <h2>Lancement <em>juillet 2026</em></h2>
  <p>AirBizness ouvre ses réservations Business à l'été 2026. Inscrivez-vous pour être prévenu(e) en priorité.</p>
  <a href="/">M'avertir au lancement</a>
</section>

<div class="hbx-back"><a href="/">← Retour à l'accueil</a></div>

{top_dest_footer}

<div id="ab-footer"></div>
</body>
</html>"""

def _city_seo_content_to_dest_dict(row: dict) -> dict:
    """Convertit une ligne city_seo_content (JSON DeepSeek) vers le schéma dict
    attendu par _render_destination_hub (Paris/Madrid/Londres)."""
    content = row.get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = {}

    narrative = content.get("narrative") or []
    intro = " ".join(p for p in narrative if p) if narrative else ""

    why = [(w.get("title", ""), w.get("desc", "")) for w in (content.get("why_business") or [])]

    quartiers = []
    for q in (content.get("quartiers") or []):
        quartiers.append({
            "nom": q.get("name") or q.get("nom") or "",
            "label": q.get("label") or "",
            "desc": q.get("desc") or "",
        })

    saisons = [(s.get("period", ""), s.get("desc", "")) for s in (content.get("saisons") or [])]

    hero_image = row.get("hero_image") or "/images/destinations/par.jpg"

    return {
        "code": row.get("destination_code") or "",
        "name": row.get("name") or "",
        "country": row.get("country") or "",
        "country_code": row.get("country_code") or "",
        "hero_image": hero_image,
        "tagline": content.get("hero_tagline") or "",
        "intro": intro,
        "why_business": why,
        "quartiers": quartiers,
        "saison": saisons,
        "lat": (content.get("geo") or {}).get("lat") or 0.0,
        "lon": (content.get("geo") or {}).get("lon") or 0.0,
    }

def _dest_hotel_count(name: str, code: str) -> int:
    """Nb d'hôtels de notre catalogue rattachés à une destination (par code HBX ou nom de ville)."""
    try:
        _c = psycopg2.connect(**DB_CONFIG); _cu = _c.cursor()
        _cu.execute("""
            SELECT COUNT(*) FROM hotels_canonical c
            LEFT JOIN hotels_provider_map hpm ON hpm.giata_code = c.giata_code AND hpm.provider='hbx'
            LEFT JOIN hbx_hotels_catalog hc ON hc.hotel_code::text = hpm.provider_hotel_code
            WHERE c.slug IS NOT NULL AND (hc.destination_code = %s OR LOWER(c.city) = LOWER(%s))
        """, (code or "", name or ""))
        n = _cu.fetchone()[0]; _cu.close(); _c.close()
        return int(n or 0)
    except Exception:
        return 0

# ============================================================
# ROUTES SEO
# ============================================================

@router.get("/h/{slug}")
def hotel_legacy_redirect(slug: str):
    """Ancienne URL indexée → 301 PERMANENT vers la nouvelle URL canonique
    /hotels/{cc}/{ville}/{slug}. Zéro perte d'indexation : Google transfère les
    signaux au recrawl. 404 si l'hôtel n'existe pas/plus."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT city, country_code FROM hotels_canonical WHERE slug = %s", (slug,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return HTMLResponse(content=_not_found_page(slug), status_code=404)
    return RedirectResponse(
        url=_hotel_seo_path(row["country_code"], row["city"], slug), status_code=301
    )

@router.get("/hotels/{cc}/{city}/{slug}", response_class=HTMLResponse)
def hotel_seo_page(cc: str, city: str, slug: str):
    """Page SEO d'1 hôtel (SSR pour Google). URL canonique /hotels/{cc}/{ville}/{slug}.
    Self-correct : si le cc/ville de l'URL ≠ valeurs canoniques → 301 vers la bonne
    (évite tout doublon d'indexation).

    Mode pré-launch : pas de bouton Réserver, CTA "M'avertir au lancement".

    Phase 2A (2026-05-30) : chargement via get_hotel_unified_data — source unique
    partagée avec /quote.html (à venir) pour garantir un contenu identique entre
    les 2 entrées d'un même hôtel.
    """
    h = get_hotel_unified_data(slug)
    if not h:
        return HTMLResponse(content=_not_found_page(slug), status_code=404)
    canonical = _hotel_seo_path(h.get("country_code"), h.get("city"), slug)
    if f"/hotels/{cc}/{city}/{slug}" != canonical:
        return RedirectResponse(url=canonical, status_code=301)
    return HTMLResponse(content=_render_hotel_seo_page(h), status_code=200)

@router.post("/wishlist/subscribe")
@limiter.limit("20/minute")
def wishlist_subscribe(request: Request,
                       email: str = Form(...),
                       hotel_code: str = Form("")):
    """Inscription wishlist : sera notifié quand AirBizness vend cet hôtel en direct.

    Form-encoded POST (depuis le widget comparateur). 303 redirect vers la fiche
    hôtel avec query param ?wishlist=ok pour feedback visuel. Si le referer n'est
    pas une fiche hôtel, redirect home.
    """
    # Validation basique email
    _email = (email or "").strip().lower()
    if "@" not in _email or "." not in _email.split("@")[-1] or len(_email) > 255:
        return RedirectResponse(url="/?wishlist=invalid_email", status_code=303)

    _code = (hotel_code or "").strip()[:20]

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO wishlist_subscribers (hotel_code, email)
            VALUES (%s, %s)
            ON CONFLICT (hotel_code, email) DO NOTHING
        """, (_code, _email))
        conn.commit()
        cur.close(); conn.close()
    except Exception:
        # Graceful fail : ne casse pas l'UX si DB down
        pass

    # Redirect vers la fiche hôtel d'où vient la soumission (referer), sinon home
    referer = request.headers.get("referer", "") or ""
    target = "/?wishlist=ok"
    if referer.startswith("https://airbizness.com/hotels/") or referer.startswith("http://airbizness.com/hotels/"):
        # Strip query existante puis ajoute ?wishlist=ok
        _base = referer.split("?", 1)[0]
        target = f"{_base}?wishlist=ok"
    return RedirectResponse(url=target, status_code=303)


@router.post("/leads/notify-launch")
@limiter.limit("20/minute")
def leads_notify_launch(request: Request, body: NotifyLaunchRequest):
    """Capture d'un lead pré-launch (depuis page hôtel ou coming-soon)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # Récupère metadata hôtel si giata fourni
    hotel_name = city = country = None
    if body.giata_code:
        cur.execute("""SELECT name, city, country_code FROM hotels_canonical
                       WHERE giata_code=%s""", (body.giata_code,))
        row = cur.fetchone()
        if row:
            hotel_name, city, country = row
    try:
        cur.execute("""
            INSERT INTO launch_waitlist
                (email, giata_code, hotel_name, city, country_code, source, user_agent, ip)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (email, giata_code) DO NOTHING
            RETURNING id
        """, (body.email, body.giata_code, hotel_name, city, country,
              body.source, request.headers.get("user-agent"),
              request.client.host if request.client else None))
        res = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    return {"ok": True, "new": bool(res)}

@router.get("/destinations/{city_slug}", response_class=HTMLResponse)
def destination_hub_page(city_slug: str):
    """Pages hub SEO destinations : Paris/Madrid/Londres premium + city_seo_content (DeepSeek) + fallback HBX."""
    slug = city_slug.lower().strip()
    # 1) Premium hardcodé
    dest = DESTINATIONS_CONTENT.get(slug)
    if dest:
        return HTMLResponse(content=_render_destination_hub(slug, dest), status_code=200)

    # 1bis) Contenu premium DeepSeek persisté (city_seo_content)
    try:
        _conn = psycopg2.connect(**DB_CONFIG)
        _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _cur.execute("""
            SELECT city_slug, destination_code, name, country, country_code,
                   meta_title, meta_description, hero_image, content
            FROM city_seo_content
            WHERE city_slug = %s
            LIMIT 1
        """, (slug,))
        _seo_row = _cur.fetchone()
        _cur.close(); _conn.close()
    except Exception:
        _seo_row = None
    if _seo_row:
        seo_dict = _city_seo_content_to_dest_dict(dict(_seo_row))
        if seo_dict.get("name") and seo_dict.get("code"):
            _html = _render_destination_hub(slug, seo_dict)
            # Page IA sans aucun hôtel réel → noindex (évite la pénalité "scaled content")
            if _dest_hotel_count(seo_dict.get("name"), seo_dict.get("code")) == 0:
                _html = _html.replace('<meta charset="UTF-8">',
                    '<meta charset="UTF-8">\n<meta name="robots" content="noindex,follow">', 1)
            return HTMLResponse(content=_html, status_code=200)

    # 2) Fallback HBX — résolution via cache mémoire (7210 rows, neglible)
    row = _hbx_dest_lookup(slug)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if not row:
        cur.close(); conn.close()
        return HTMLResponse(content=f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Destination introuvable — AirBizness</title><meta name="robots" content="noindex">
</head><body style="background:#0f0f0f;color:#f0ece4;font-family:sans-serif;text-align:center;padding:80px 24px;">
<h1>Destination introuvable</h1><p>La destination <code>{html_esc(city_slug)}</code> n'est pas dans notre catalog.</p>
<a href="/" style="color:#d4ae4a;">Retour à l'accueil</a></body></html>""", status_code=404)

    dest_row = dict(row)
    code = dest_row.get("code") or ""
    # Recherche d'hôtels : par destination_code en priorité, fallback par LOWER(city)
    cur.execute("""
        SELECT c.slug, c.name, c.stars, c.address, c.city, c.best_photo_url,
               (SELECT main_image_url FROM hbx_hotels_catalog
                WHERE giata_code = c.giata_code LIMIT 1) AS hbx_img
        FROM hotels_canonical c
        LEFT JOIN hotels_provider_map hpm ON hpm.giata_code = c.giata_code AND hpm.provider='hbx'
        LEFT JOIN hbx_hotels_catalog hc ON hc.hotel_code::text = hpm.provider_hotel_code
        WHERE c.slug IS NOT NULL
          AND (hc.destination_code = %s OR LOWER(c.city) = LOWER(%s))
        ORDER BY c.stars DESC NULLS LAST, c.total_photos DESC NULLS LAST
        LIMIT 50
    """, (code, dest_row.get("name") or ""))
    hotel_rows = cur.fetchall()
    cur.close(); conn.close()

    hotels = []
    for h in hotel_rows:
        hd = dict(h)
        hd["photo"] = hd.get("best_photo_url") or hd.get("hbx_img") or ""
        hotels.append(hd)

    # Country label = country_code pour l'instant (on n'a pas la table countries → suffit)
    country_label = dest_row.get("country_code") or ""
    _html = _render_destination_hub_hbx(slug, dest_row, hotels, country_label=country_label)
    # Page-ville sans aucun hôtel réel → noindex (évite la pénalité "scaled content")
    if not hotels:
        _html = _html.replace('<meta charset="UTF-8">',
            '<meta charset="UTF-8">\n<meta name="robots" content="noindex,follow">', 1)
    return HTMLResponse(content=_html, status_code=200)

@router.get("/vols/{route_slug}", response_class=HTMLResponse)
def vol_route_page(route_slug: str):
    """Page SEO d'une route aérienne. Ex: /vols/paris-dubai."""
    slug = (route_slug or "").lower().strip()
    rmap = _vols_route_map()
    pair = rmap.get(slug)
    if not pair:
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Route introuvable — AirBizness</title><meta name="robots" content="noindex"></head>
<body style="background:#0f0f0f;color:#f0ece4;font-family:sans-serif;text-align:center;padding:80px 24px;">
<h1>Route introuvable</h1><p>La route <code>{html_esc(route_slug)}</code> n'est pas à notre catalogue.</p>
<a href="/" style="color:#d4ae4a;">Retour à l'accueil</a></body></html>""", status_code=404)

    origin, dest = pair
    oi, di = _airport_info(origin), _airport_info(dest)
    o_city, d_city = oi.get("city") or origin, di.get("city") or dest
    o_name, d_name = oi.get("name") or origin, di.get("name") or dest

    # (RETIRÉ 2026-05-30) Lecture route_stats + route_price_band supprimée :
    # variables avg_fr / nb_fr / _band jamais rendues dans le HTML (code mort)
    # et leurs sources (route_stats / deals via route_price_band) DIVERGENT de la
    # source unique _fetch_or_cache_offers utilisée par la liste/bandeau/calendrier.
    # On ne garde que route_real_airlines (juste les NOMS de compagnies, pas de prix
    # → pas de divergence possible).
    _airlines = route_real_airlines(origin, dest, limit=8)
    _dest_pois = city_interest_points(d_city, max_count=8)
    _dest_hotels = city_top_seo_hotels(d_city, limit=4)

    oc, dc = html_esc(o_city), html_esc(d_city)
    on, dn = html_esc(o_name), html_esc(d_name)
    title = f"Vol {o_city} → {d_city} en Business Class | AirBizness"
    description = (f"Réservez votre vol {o_city} ({origin}) → {d_city} ({dest}) en Business Class. "
                  f"Tarifs négociés AirBizness, confirmation immédiate, sans intermédiaire.")
    # FAQ : réponse compagnies grounded sur les compagnies réellement observées
    if _airlines:
        _air_names = [a["name"] for a in _airlines[:6]]
        if len(_air_names) > 1:
            _air_txt = ", ".join(_air_names[:-1]) + " et " + _air_names[-1]
        else:
            _air_txt = _air_names[0]
        _faq_air = (f"Sur la liaison {o_city} ({origin}) – {d_city} ({dest}), les compagnies "
                    f"observées dans nos offres incluent {_air_txt}.")
    else:
        _faq_air = ("Plusieurs compagnies proposent cette liaison. "
                    "Les options disponibles s'affichent en direct sur la page.")
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f"Comment réserver un vol {o_city} - {d_city} en Business Class ?",
             "acceptedAnswer": {"@type": "Answer",
                "text": f"Lancez votre recherche {o_city} ({origin}) → {d_city} ({dest}) sur AirBizness : les vols Business Class et leurs tarifs s'affichent en direct, avec confirmation immédiate à votre nom."}},
            {"@type": "Question", "name": f"Quelles compagnies opèrent la ligne {o_city} - {d_city} ?",
             "acceptedAnswer": {"@type": "Answer", "text": _faq_air}},
        ],
    }

    # ── Bloc « Compagnies & tarifs » (grounded : deals réels)
    route_info_html = ""
    _info_cards = []
    if _airlines:
        _chips = "".join(
            f'<span class="route-airline">{html_esc(a["name"])}</span>' for a in _airlines
        )
        _info_cards.append(
            f'<div class="hub-why-card" style="grid-column:1/-1;"><div class="hub-why-title">Compagnies observées sur la ligne</div>'
            f'<div class="route-airlines">{_chips}</div>'
            f'<div class="hub-why-desc" style="margin-top:10px;font-size:12px;color:var(--text3);">'
            f'Compagnies relevées dans nos offres {origin} → {dest}. La disponibilité varie selon les dates.</div></div>'
        )
    if _info_cards:
        route_info_html = f"""
<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Compagnies {oc} → {dc}</div>
      <h2>Compagnies sur la <em>ligne</em></h2>
    </div>
    <div class="hub-why-grid">
      {''.join(_info_cards)}
    </div>
  </div>
</section>"""

    # ── Bloc « À voir à [ville] » (POI agrégés des hôtels de la ville d'arrivée)
    dest_pois_html = ""
    if _dest_pois:
        _poi_chips = "".join(
            f'<div class="route-poi"><span class="route-poi-name">{html_esc(p["name"])}</span>'
            + (f'<span class="route-poi-dist">≈ {html_esc(p["distance_label"])}</span>' if p.get("distance_label") else '')
            + '</div>'
            for p in _dest_pois
        )
        dest_pois_html = f"""
<section class="hub-section alt">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Sur place</div>
      <h2>À voir à <em>{dc}</em></h2>
      <p class="hub-section-lead">Lieux et points d'intérêt les plus cités par nos hôtels {dc}, distance moyenne depuis le centre hôtelier.</p>
    </div>
    <div class="route-poi-grid">
      {_poi_chips}
    </div>
  </div>
</section>"""

    # ── Bloc « Où dormir à [ville] » (hôtels réels + liens internes /hotels/…)
    dest_hotels_html = ""
    if _dest_hotels:
        _hcards = []
        for hh in _dest_hotels:
            _hstars = "★" * (hh.get("stars") or 0)
            _himg = hh.get("photo") or ""
            _himg_html = (f'<div class="route-hotel-img"><img src="{html_esc(_himg)}" '
                          f'alt="{html_esc(hh["name"])}" loading="lazy"></div>') if _himg else ''
            _hcards.append(
                f'<a class="route-hotel-card" href="{html_esc(hh["path"])}">'
                f'{_himg_html}'
                f'<div class="route-hotel-body">'
                f'<div class="route-hotel-stars">{_hstars}</div>'
                f'<div class="route-hotel-name">{html_esc(hh["name"])}</div>'
                f'</div></a>'
            )
        dest_hotels_html = f"""
<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Vol + hôtel</div>
      <h2>Où dormir à <em>{dc}</em></h2>
      <p class="hub-section-lead">Quelques adresses de notre catalogue {dc}, réservables avec votre vol Business AirBizness.</p>
    </div>
    <div class="route-hotels-grid">
      {''.join(_hcards)}
    </div>
    <div style="text-align:center;margin-top:26px;">
      <a class="hub-sister-link" href="/destinations/{_slugify(d_city)}">Voir tous les hôtels à {dc} →</a>
    </div>
  </div>
</section>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0f0f0f">
<title>{html_esc(title)}</title>
<meta name="description" content="{html_esc(description)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{html_esc(title)}">
<meta property="og:description" content="{html_esc(description)}">
<meta property="og:url" content="https://airbizness.com/vols/{slug}">
<link rel="canonical" href="https://airbizness.com/vols/{slug}">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}}
:root{{
  --bg:#0f0f0f;--bg2:#161616;--bg3:#1e1e1e;--bg4:#252525;
  --gold:#b8962e;--gold2:#d4ae4a;--gold-dim:rgba(184,150,46,0.12);
  --text:#f0ece4;--text2:#a09890;--text3:#6a6058;
  --border:rgba(255,255,255,0.07);--border2:rgba(184,150,46,0.2);
}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.65;}}
.hub-hero{{position:relative;padding:80px 24px 60px;background:linear-gradient(135deg,#1a1408 0%,#0f0f0f 70%);min-height:420px;display:flex;align-items:center;}}
.hub-hero-inner{{position:relative;z-index:2;max-width:880px;margin:0 auto;text-align:center;}}
.hub-hero-tag{{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,0.08);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.12);padding:7px 16px;border-radius:99px;color:#fff;font-size:11px;letter-spacing:1.8px;text-transform:uppercase;font-weight:500;margin-bottom:24px;}}
.hub-hero h1{{font-family:'DM Serif Display',serif;font-size:clamp(38px,6vw,64px);line-height:1.05;color:#fff;margin-bottom:18px;letter-spacing:-0.015em;}}
.hub-hero h1 em{{font-style:italic;color:var(--gold2);}}
.hub-hero-tagline{{font-size:clamp(16px,2vw,20px);color:rgba(255,255,255,0.92);font-weight:300;margin-bottom:32px;}}
.hub-hero-stats{{display:flex;justify-content:center;flex-wrap:wrap;gap:32px;margin-top:16px;}}
.hub-hero-stat{{text-align:center;}}
.hub-hero-stat-val{{font-family:'DM Serif Display',serif;font-size:36px;color:var(--gold2);line-height:1;}}
.hub-hero-stat-lbl{{font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.7);margin-top:6px;}}
.hub-section{{padding:70px 24px;border-bottom:1px solid var(--border);}}
.hub-section.alt{{background:var(--bg2);}}
.hub-section-inner{{max-width:1080px;margin:0 auto;}}
.hub-section-head{{text-align:center;margin-bottom:42px;}}
.hub-eyebrow{{display:inline-block;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--gold2);font-weight:500;margin-bottom:14px;}}
.hub-section-head h2{{font-family:'DM Serif Display',serif;font-size:clamp(28px,4vw,42px);letter-spacing:-0.01em;line-height:1.15;}}
.hub-section-head h2 em{{font-style:italic;color:var(--gold2);}}
.hub-section-lead{{color:var(--text2);font-size:15.5px;max-width:680px;margin:16px auto 0;line-height:1.7;}}
.hub-intro{{max-width:760px;margin:0 auto;text-align:center;font-size:17px;line-height:1.85;color:var(--text2);}}
.hub-intro p{{margin-bottom:16px;}} .hub-intro strong{{color:var(--text);}}
/* Cartes vol — RENDU IDENTIQUE a /resultats.html (.flight-card / .fc-*) */
.fcards{{max-width:920px;margin:0 auto;}}
.flight-card{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;margin-bottom:10px;overflow:hidden;transition:border-color .2s;}}
.flight-card:hover{{border-color:var(--border2);}}
.flight-card.exc{{border-left:3px solid #c0392b;}}
.flight-card.good{{border-left:3px solid var(--gold);}}
.fc-date-strip{{padding:9px 20px;background:var(--bg3);border-bottom:1px solid var(--border);font-size:11.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--gold2);font-weight:500;}}
.fc-main{{display:grid;grid-template-columns:140px 1fr 180px;gap:18px;padding:18px 20px;align-items:center;}}
.fc-airline{{display:flex;flex-direction:column;gap:4px;}}
.fc-airline-name{{font-size:13px;font-weight:600;color:var(--text);}}
.fc-airline-code{{font-size:10px;color:var(--text3);letter-spacing:1px;}}
.fc-route{{display:flex;align-items:center;gap:14px;}}
.fc-pt{{display:flex;flex-direction:column;align-items:center;min-width:62px;}}
.fc-time{{font-family:'DM Serif Display',serif;font-size:24px;line-height:1;color:var(--text);}}
.fc-iata{{font-size:11px;color:var(--text3);margin-top:4px;letter-spacing:1px;}}
.fc-mid{{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:90px;}}
.fc-dur{{font-size:10px;color:var(--text3);letter-spacing:0.5px;}}
.fc-line{{width:100%;height:1px;background:var(--border);position:relative;}}
.fc-line::after{{content:'';position:absolute;left:50%;top:-3px;transform:translateX(-50%);width:6px;height:6px;border-radius:50%;background:var(--gold2);}}
.fc-stop{{font-size:10px;font-weight:500;}} .fc-direct{{color:var(--gold2);}} .fc-escale{{color:var(--text3);}}
.fc-pb{{display:flex;flex-direction:column;align-items:flex-end;gap:8px;}}
.fc-orig{{font-size:11px;color:var(--text3);text-decoration:line-through;}}
.fc-price{{font-family:'DM Serif Display',serif;font-size:30px;line-height:1;color:var(--text);}}
.fc-pct{{font-size:10px;font-weight:600;letter-spacing:0.5px;padding:2px 8px;border-radius:2px;}}
.fc-pct.good{{background:var(--gold-dim);color:var(--gold2);border:1px solid var(--border2);}}
.fc-select{{padding:10px 18px;background:var(--gold);border:none;color:#000;font-family:'DM Sans',sans-serif;font-size:13px;font-weight:600;cursor:pointer;border-radius:6px;transition:background .15s;white-space:nowrap;text-decoration:none;display:inline-block;}}
.fc-select:hover{{background:var(--gold2);}}
.fc-bot{{display:flex;justify-content:space-between;align-items:center;padding:10px 20px;background:var(--bg3);border-top:1px solid var(--border);gap:12px;flex-wrap:wrap;}}
.fc-tags{{display:flex;gap:6px;flex-wrap:wrap;}}
.fc-tag{{font-size:10px;color:var(--text3);padding:3px 8px;background:var(--bg4);border:1px solid var(--border);border-radius:2px;}}
.fc-tag.suite{{color:var(--gold2);border-color:var(--border2);background:var(--gold-dim);}}
.fc-tag.pod{{color:#7ba7e0;border-color:rgba(123,167,224,.2);background:rgba(123,167,224,.06);}}
.fc-tag.flat{{color:#b07ed4;border-color:rgba(176,126,212,.2);background:rgba(176,126,212,.06);}}
.fc-detail{{font-size:11px;color:var(--text3);text-decoration:none;}} .fc-detail:hover{{color:var(--gold2);}}
#cards-loading{{color:var(--text3);font-size:14px;padding:24px;text-align:center;}}
.live-tag{{font-size:11px;color:#4ade80;margin-left:8px;font-weight:500;letter-spacing:0.5px;}}
@media(max-width:640px){{.fc-main{{grid-template-columns:1fr;gap:14px;text-align:center;}}.fc-airline,.fc-route{{justify-content:center;}}.fc-pb{{align-items:center;}}}}
.hub-why-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;}}
@media(max-width:680px){{.hub-why-grid{{grid-template-columns:1fr;}}}}
.hub-why-card{{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:22px 24px;}}
.hub-why-title{{font-family:'DM Serif Display',serif;font-size:17px;color:var(--gold2);margin-bottom:6px;}}
.hub-why-desc{{font-size:13.5px;color:var(--text2);line-height:1.6;}}
/* Compagnies (chips), POI ville, hôtels (liens internes) — data réelle */
.route-airlines{{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px;}}
.route-airline{{font-size:12.5px;color:var(--text);background:var(--bg3);border:1px solid var(--border);border-radius:99px;padding:6px 14px;}}
.route-poi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;max-width:900px;margin:0 auto;}}
.route-poi{{display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:13px 16px;}}
.route-poi-name{{font-size:14px;color:var(--text);}}
.route-poi-dist{{font-size:12px;color:var(--gold2);white-space:nowrap;font-variant-numeric:tabular-nums;}}
.route-hotels-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;}}
@media(max-width:880px){{.route-hotels-grid{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:520px){{.route-hotels-grid{{grid-template-columns:1fr;}}}}
.route-hotel-card{{background:var(--bg);border:1px solid var(--border);border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;transition:border-color .2s,transform .2s;display:flex;flex-direction:column;}}
.route-hotel-card:hover{{border-color:var(--border2);transform:translateY(-3px);}}
.route-hotel-img{{aspect-ratio:4/3;background:var(--bg3);overflow:hidden;}}
.route-hotel-img img{{width:100%;height:100%;object-fit:cover;}}
.route-hotel-body{{padding:14px 16px;}}
.route-hotel-stars{{color:var(--gold2);font-size:12px;letter-spacing:1px;margin-bottom:5px;min-height:14px;}}
.route-hotel-name{{font-family:'DM Serif Display',serif;font-size:15.5px;color:var(--text);line-height:1.25;}}
.hub-final-cta{{padding:90px 24px;background:linear-gradient(135deg,var(--gold-dim) 0%,var(--bg) 70%);text-align:center;}}
.hub-final-cta-inner{{max-width:680px;margin:0 auto;}}
.hub-final-cta h2{{font-family:'DM Serif Display',serif;font-size:clamp(30px,4.5vw,44px);margin-bottom:14px;letter-spacing:-0.01em;}}
.hub-final-cta h2 em{{font-style:italic;color:var(--gold2);}}
.hub-final-cta p{{color:var(--text2);font-size:16px;margin-bottom:30px;}}
.hub-final-cta-btn{{display:inline-block;padding:16px 38px;background:var(--gold);color:#000;text-decoration:none;font-weight:600;font-size:15px;letter-spacing:0.3px;border-radius:9px;transition:background .15s;}}
.hub-final-cta-btn:hover{{background:var(--gold2);}}
.hub-sister{{padding:50px 24px;background:var(--bg2);border-top:1px solid var(--border);text-align:center;}}
.hub-sister h3{{font-family:'DM Serif Display',serif;font-size:22px;margin-bottom:18px;}}
.hub-sister-links{{display:flex;justify-content:center;gap:14px;flex-wrap:wrap;}}
.hub-sister-link{{padding:10px 22px;background:var(--bg);border:1px solid var(--border);border-radius:99px;color:var(--text);text-decoration:none;font-size:13.5px;transition:all .15s;}}
.hub-sister-link:hover{{border-color:var(--gold2);color:var(--gold2);}}
</style>
<script defer src="/shared-chrome.js"></script>
<script defer src="/cookies.js"></script>
</head>
<body>
<div id="ab-header"></div>
<div id="ab-bottomnav"></div>

<section class="hub-hero">
  <div class="hub-hero-inner">
    <div class="hub-hero-tag">Vol Business AirBizness</div>
    <h1>Vol {oc} → <em>{dc}</em></h1>
    <p class="hub-hero-tagline">Business Class, tarifs négociés. Confirmation immédiate, sans intermédiaire.</p>
    <div class="hub-hero-stats">
      <div class="hub-hero-stat"><div class="hub-hero-stat-val">Direct</div><div class="hub-hero-stat-lbl">Sans intermédiaire</div></div>
      <div class="hub-hero-stat"><div class="hub-hero-stat-val">Immédiate</div><div class="hub-hero-stat-lbl">Confirmation</div></div>
      <div class="hub-hero-stat"><div class="hub-hero-stat-val">Business</div><div class="hub-hero-stat-lbl">Classe privilégiée</div></div>
    </div>
  </div>
</section>

<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">Vols disponibles <span class="live-tag">● prix en direct</span></div>
      <h2>Vols {oc} → <em>{dc}</em></h2>
      <p class="hub-section-lead">Offres réelles récupérées en direct — cliquez pour réserver, confirmation immédiate à votre nom.</p>
    </div>
    <div class="fcards" id="cards"><div id="cards-loading">Chargement des vols en direct…</div></div>
  </div>
</section>

<section class="hub-section alt">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">{oc} → {dc} en quelques mots</div>
      <h2>Voyager de {oc} à <em>{dc}</em> en classe affaires</h2>
    </div>
    <div class="hub-intro">
      <p>La liaison <strong>{oc} – {dc}</strong> fait partie des routes proposées par AirBizness en Business Class. Au départ de <strong>{on} ({origin})</strong> vers <strong>{dn} ({dest})</strong>, nous négocions directement les tarifs auprès des compagnies, sans intermédiaire. Les vols disponibles et leurs prix s'affichent en direct ci-dessous.</p>
    </div>
  </div>
</section>
{route_info_html}
{dest_pois_html}
{dest_hotels_html}
<section class="hub-section">
  <div class="hub-section-inner">
    <div class="hub-section-head">
      <div class="hub-eyebrow">L'avantage AirBizness</div>
      <h2>Pourquoi réserver ce <em>vol</em> avec nous</h2>
    </div>
    <div class="hub-why-grid">
      <div class="hub-why-card"><div class="hub-why-title">Tarifs négociés</div><div class="hub-why-desc">Prix direct AirBizness, sans surcoût d'intermédiaire.</div></div>
      <div class="hub-why-card"><div class="hub-why-title">Business Class</div><div class="hub-why-desc">Cabines premium privilégiées pour le voyageur d'affaires.</div></div>
      <div class="hub-why-card"><div class="hub-why-title">Confirmation immédiate</div><div class="hub-why-desc">Billet émis à votre nom, reçu sous quelques secondes.</div></div>
      <div class="hub-why-card"><div class="hub-why-title">Sans intermédiaire</div><div class="hub-why-desc">Réservation directe, support 24/7.</div></div>
    </div>
  </div>
</section>

<section class="hub-final-cta">
  <div class="hub-final-cta-inner">
    <h2>Votre vol <em>{oc} → {dc}</em> en quelques clics</h2>
    <p>Business Class au meilleur tarif négocié, voucher AirBizness, paiement sécurisé Stripe.</p>
    <a href="/?origin={origin}&destination={dest}" class="hub-final-cta-btn">Rechercher mon vol →</a>
  </div>
</section>

<section class="hub-sister">
  <h3>Autres destinations AirBizness</h3>
  <div class="hub-sister-links">
    <a class="hub-sister-link" href="/destinations/paris">Paris</a>
    <a class="hub-sister-link" href="/destinations/madrid">Madrid</a>
    <a class="hub-sister-link" href="/destinations/londres">Londres</a>
  </div>
</section>

<div id="ab-footer"></div>

<script>
// Cartes vol = MEME rendu que /resultats.html (.flight-card). 2 offres teaser.
// Clic -> atterrit sur /resultats.html avec la recherche PRE-REMPLIE (from/to/cabin).
const ORIGIN="{origin}", DEST="{dest}";
const SEARCH_DATE=new Date(Date.now()+42*864e5).toISOString().slice(0,10);
const RESULTS_URL=`/resultats.html?from=${{ORIGIN}}&to=${{DEST}}&date=${{SEARCH_DATE}}&trip=one_way&adults=1&children=0&infants=0&cabin=business&pax=1`;
const CABIN_LABELS={{private_suite:{{l:'Suite privée',c:'suite'}},full_flat_pod:{{l:'Flat Bed Pod',c:'pod'}},full_flat:{{l:'Full Flat',c:'flat'}},recliner:{{l:'Recliner',c:'rec'}},standard:{{l:'Standard',c:''}},unknown:{{l:'Business',c:''}}}};
const FR_DAYS=['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi'];
const FR_MONTHS=['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'];
function fmtPrice(p){{return new Intl.NumberFormat('fr-FR').format(Math.round(p))+' €';}}
function fmtDur(m){{const h=Math.floor(m/60),mn=m%60;return mn?h+'h '+mn:h+'h';}}
function fmtTime(iso){{if(!iso)return '--:--';return new Date(iso).toLocaleTimeString('fr-FR',{{hour:'2-digit',minute:'2-digit'}});}}
function arrTime(iso,dur){{if(!iso)return '--:--';return new Date(new Date(iso).getTime()+dur*60000).toLocaleTimeString('fr-FR',{{hour:'2-digit',minute:'2-digit'}});}}
async function loadCards(){{
  const box=document.getElementById('cards');
  try{{
    // 2026-05-29 fix Pascal: la page promet du Business → on DEMANDE du business
    // (le défaut backend est economy). Si 0 offre business sur la route, fallback
    // honnête en économie + flag pour labelliser franchement les cartes.
    let cabinUsed='business';
    let r=await fetch(`/api/deals?origin=${{ORIGIN}}&destination=${{DEST}}&date=${{SEARCH_DATE}}&cabin_class=business&limit=40`);
    let data=await r.json();
    let offers=(data.deals||data.offers||(Array.isArray(data)?data:[]));
    if(!offers.length){{
      // Pas de business dispo → on retombe sur l'éco mais on le DIT (jamais faire passer l'éco pour du business).
      cabinUsed='economy';
      r=await fetch(`/api/deals?origin=${{ORIGIN}}&destination=${{DEST}}&date=${{SEARCH_DATE}}&cabin_class=economy&limit=40`);
      data=await r.json();
      offers=(data.deals||data.offers||(Array.isArray(data)?data:[]));
    }}
    const seen=new Set(); const uniq=[];
    for(const o of offers){{
      // Filtre client défensif : on ne garde que la cabine demandée si le champ est renseigné.
      if(o.cabin_class && o.cabin_class.toLowerCase()!==cabinUsed)continue;
      const k=(o.airline_code||o.airline_name||'')+'|'+(o.departure_at||'').slice(0,16)+'|'+(o.stops==null?'?':o.stops);
      if(seen.has(k))continue; seen.add(k); uniq.push(o);
    }}
    uniq.sort((a,b)=>(a.price||1e9)-(b.price||1e9));
    offers=uniq.slice(0,2);
    if(!offers.length){{box.innerHTML='<div id="cards-loading">Offres bientôt disponibles sur cette ligne — <a href="'+RESULTS_URL+'" style="color:var(--gold2)">lancer une recherche</a></div>';return;}}
    if(cabinUsed==='economy'){{
      box.innerHTML='<div style="text-align:center;color:var(--text2);font-size:13px;margin-bottom:14px;padding:10px 16px;background:var(--bg3);border:1px solid var(--border);border-radius:8px;">Aucune offre Business disponible en direct sur cette ligne pour le moment — affichage des meilleurs tarifs en <strong style="color:var(--text)">classe économique</strong>.</div>';
    }}else{{
      box.innerHTML='';
    }}
    offers.forEach(o=>{{
      // 2026-05-29 fix Pascal: "Vol direct" UNIQUEMENT si stops===0 confirmé (number).
      // stops null/undefined => inconnu => "Voir détail" (jamais prétendre direct).
      const _stops=(o.stops===null||o.stops===undefined)?null:Number(o.stops);
      const isDirect=(_stops===0);
      const stopsLabel=isDirect?'Vol direct'
                      :(_stops===null||isNaN(_stops))?'Voir détail'
                      :(_stops+' escale'+(_stops>1?'s':''));
      // Durée : on n'affiche un chiffre que si duration_minutes est fiable (>0).
      // On NE déduit PLUS la durée de l'écart d'horloge départ→arrivée (faux pour les escales).
      const durMin=Number(o.duration_minutes||0);
      const hasDur=durMin>0;
      const cab=cabinUsed==='economy'?{{l:'Économique',c:''}}:(CABIN_LABELS[o.seat_type]||CABIN_LABELS.unknown);
      const offerId=o.offer_id||o.id||'';
      // L'URL de l'offre porte la cabine réellement affichée (business si dispo, sinon éco)
      // pour que /resultats.html charge la bonne classe et retrouve l'offre.
      const offerBase=RESULTS_URL.replace(/cabin=business/,'cabin='+cabinUsed);
      const offerUrl=offerId?`${{offerBase}}&offer_id=${{encodeURIComponent(offerId)}}`:offerBase;
      const dep=o.departure_at?new Date(o.departure_at):null;
      const dateHeader=(dep&&!isNaN(dep.getTime()))?`<div class="fc-date-strip">${{FR_DAYS[dep.getDay()]}} ${{dep.getDate()}} ${{FR_MONTHS[dep.getMonth()]}} ${{dep.getFullYear()}}</div>`:'';
      box.insertAdjacentHTML('beforeend',`
        <div class="flight-card good">
          ${{dateHeader}}
          <div class="fc-main">
            <div class="fc-airline">
              <div class="fc-airline-name">${{o.airline_name||'Compagnie'}}</div>
              <div class="fc-airline-code">${{o.airline_code||''}}</div>
            </div>
            <div class="fc-route">
              <div class="fc-pt"><div class="fc-time">${{fmtTime(o.departure_at)}}</div><div class="fc-iata">${{ORIGIN}}</div></div>
              <div class="fc-mid">
                <div class="fc-dur">${{hasDur?fmtDur(durMin):''}}</div>
                <div class="fc-line"></div>
                <div class="fc-stop ${{isDirect?'fc-direct':'fc-escale'}}">${{stopsLabel}}</div>
              </div>
              <div class="fc-pt"><div class="fc-time">${{hasDur?arrTime(o.departure_at,durMin):(o.arrival_at?fmtTime(o.arrival_at):'--:--')}}</div><div class="fc-iata">${{DEST}}</div></div>
            </div>
            <div class="fc-pb">
              <div class="fc-price">${{o.price?fmtPrice(o.price):'—'}}</div>
              <a class="fc-select" href="${{offerUrl}}">Voir cette offre</a>
            </div>
          </div>
          <div class="fc-bot">
            <div class="fc-tags">
              ${{cab&&cab.l?`<div class="fc-tag ${{cab.c}}">${{cab.l}}</div>`:''}}
              ${{o.wifi?'<div class="fc-tag">Wifi</div>':''}}
              ${{o.fare_brand?'<div class="fc-tag">'+o.fare_brand+'</div>':''}}
            </div>
            <a class="fc-detail" href="${{RESULTS_URL}}">Voir tous les vols ${{ORIGIN}} vers ${{DEST}}</a>
          </div>
        </div>`);
    }});
    box.insertAdjacentHTML('beforeend',`<div style="text-align:center;margin-top:18px"><a class="hub-final-cta-btn" style="padding:13px 30px;font-size:14px" href="${{RESULTS_URL}}">Voir tous les vols ${{ORIGIN}} vers ${{DEST}}</a></div>`);
  }}catch(e){{box.innerHTML='<div id="cards-loading">Vols momentanément indisponibles — <a href="'+RESULTS_URL+'" style="color:var(--gold2)">lancer une recherche</a></div>';}}
}}
loadCards();
</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)

@router.get("/sitemap.xml")
def sitemap_xml():
    """Sitemap dynamique : statiques + 3 villes premium + ~4200 hôtels + ~7210 villes HBX."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT slug, city, country_code, last_updated_at FROM hotels_canonical
        WHERE slug IS NOT NULL ORDER BY giata_code LIMIT 50000
    """)
    hotel_rows = cur.fetchall()
    cur.execute("""
        SELECT code, name, last_synced_at FROM hbx_destinations
        WHERE is_closed = false
        ORDER BY code LIMIT 50000
    """)
    dest_rows = cur.fetchall()
    # On n'indexe QUE les destinations adossées à du vrai stock (anti scaled-content abuse).
    # Filtre par destination_code RÉEL uniquement (le matching par nom créait des homonymes US).
    cur.execute("""
        SELECT DISTINCT hc.destination_code FROM hotels_canonical c
        JOIN hotels_provider_map hpm ON hpm.giata_code = c.giata_code AND hpm.provider='hbx'
        JOIN hbx_hotels_catalog hc ON hc.hotel_code::text = hpm.provider_hotel_code
        WHERE c.slug IS NOT NULL AND hc.destination_code IS NOT NULL
    """)
    hotel_dest_codes = {r[0] for r in cur.fetchall() if r[0]}
    cur.close(); conn.close()

    base = "https://airbizness.com"
    today = datetime.utcnow().date().isoformat()
    items = [
        f"  <url><loc>{base}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>",
        f"  <url><loc>{base}/pour-les-hoteliers.html</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>",
        f"  <url><loc>{base}/destinations/paris</loc><changefreq>weekly</changefreq><priority>0.95</priority></url>",
        f"  <url><loc>{base}/destinations/madrid</loc><changefreq>weekly</changefreq><priority>0.95</priority></url>",
        f"  <url><loc>{base}/destinations/londres</loc><changefreq>weekly</changefreq><priority>0.95</priority></url>",
    ]
    # Hôtels — nouvelles URLs canoniques /hotels/{cc}/{ville}/{slug}
    # 2026-05-30 (boost SEO) : passage priority 0.7→0.9 + changefreq monthly→weekly.
    # Demandé par Pascal après audit GSC : requêtes longue-traîne hôtels position ~15,
    # le sitemap est notre vrai canal de propagation vers Google → on remonte la priorité.
    for slug, city, country_code, last_upd in hotel_rows:
        last = last_upd.date().isoformat() if last_upd else today
        loc = base + _hotel_seo_path(country_code, city, slug)
        items.append(
            f"  <url><loc>{loc}</loc><lastmod>{last}</lastmod>"
            f"<changefreq>weekly</changefreq><priority>0.9</priority></url>"
        )
    # Villes HBX (skip celles déjà présentes en premium : paris/madrid/londres/london)
    skip_slugs = {"paris", "madrid", "londres", "london"}
    seen_slugs = set()
    for code, name, last_syn in dest_rows:
        # Filtre inventaire STRICT : on n'expose une destination QUE si son destination_code
        # HBX a du vrai stock chez nous. On NE matche PLUS par nom de ville : le matching par
        # nom créait des faux positifs (homonymes US : Paris-TX, London-KY, Richmond-VA…) qui
        # affichaient en réalité les hôtels de Paris-FR / Londres-UK → pollution SEO.
        if code not in hotel_dest_codes:
            continue
        # Préfère le slug du name (lisible) sinon code lowercase
        s = _slugify(name) if name else (code or "").lower()
        if not s or s in skip_slugs or s in seen_slugs:
            continue
        seen_slugs.add(s)
        last = last_syn.date().isoformat() if last_syn else today
        items.append(
            f"  <url><loc>{base}/destinations/{s}</loc><lastmod>{last}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )
    # 2026-05-29 fix Pascal: pages SEO vol /vols/{slug} (~80 routes) absentes du sitemap.
    # Sans elles Google ne découvre pas les pages vol → SEO vol invisible.
    # Étape 2 (etude_switch_verticales.md) : on ne propose les /vols/ à Google QUE si la
    # verticale "flights" est active (sinon = proposer des vols non vendus → pages creuses).
    try:
        from main import vertical_active  # lazy : évite l'import circulaire main↔routers
        flights_on = vertical_active("flights")
    except Exception:
        flights_on = True  # défaut non destructif si le helper est indisponible
    if flights_on:
        try:
            for vslug in sorted(_vols_route_map().keys()):
                if not vslug:
                    continue
                items.append(
                    f"  <url><loc>{base}/vols/{vslug}</loc><lastmod>{today}</lastmod>"
                    f"<changefreq>weekly</changefreq><priority>0.8</priority></url>"
                )
        except Exception as _e:
            print(f"[sitemap] vols routes non-fatal: {_e}")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items) + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")

@router.get("/sitemap-priority.xml")
def sitemap_priority_xml():
    """Sitemap secondaire pour les URLs flaggées par GSC "Explorée, actuellement non indexée".
    Priority=1.0, changefreq=daily → signal max à Google pour re-crawl prioritaire."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT url_path FROM seo_priority_urls ORDER BY slug")
    rows = cur.fetchall()
    cur.close(); conn.close()
    base = "https://airbizness.com"
    today = datetime.utcnow().date().isoformat()
    items = []
    for (path,) in rows:
        items.append(
            f"  <url><loc>{base}{path}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>daily</changefreq><priority>1.0</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items) + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")

@router.get("/robots.txt")
def robots_txt():
    """robots.txt — autorise /h/* et la home coming-soon, interdit le reste."""
    body = """User-agent: *
Allow: /
Allow: /hotels/
Allow: /h/
Allow: /destinations/
Allow: /pour-les-hoteliers.html
Allow: /coming-soon.html
Allow: /images/
Allow: /css/
Allow: /js/
Allow: /fonts/

Disallow: /hotels.html
Disallow: /checkout.html
Disallow: /pack-checkout.html
Disallow: /quote.html
Disallow: /hotel-preview.html
Disallow: /catalog.html
Disallow: /admin-*
Disallow: /claim.html
Disallow: /hotel-manager.html
Disallow: /mes-voyages.html
Disallow: /mes-alertes.html
Disallow: /api/
Disallow: /vol.html
Disallow: /flight-passengers.html
Disallow: /flight-checkout.html
Disallow: /flight-confirmation.html
Disallow: /pack-confirmation.html
Disallow: /hotel-confirmation.html
Disallow: /activity-checkout.html
Disallow: /activity-confirmation.html
Disallow: /bizzi-chat.html
Disallow: /compte.html
Disallow: /confirmation.html

Sitemap: https://airbizness.com/sitemap.xml
Sitemap: https://airbizness.com/sitemap-priority.xml
"""
    return Response(content=body, media_type="text/plain")


# ============================================================
# SEO Combinator — comparatifs hôtels (Phase 1 : 10 pages pilotes Paris)
# ============================================================
@router.get("/hotels-vs/{slug_a}/{slug_b}", response_class=HTMLResponse)
def hotel_comparator_page(slug_a: str, slug_b: str):
    if slug_a == slug_b:
        return HTMLResponse(_not_found_page(f"{slug_a}-vs-{slug_b}"), status_code=404)
    from services.seo_combinator import ComparatorTemplate
    comparator = ComparatorTemplate()
    result = comparator.render(slug_a, slug_b)
    if result[0] is None:
        return HTMLResponse(_not_found_page(f"{slug_a}-vs-{slug_b}"), status_code=404)
    html, _ = result
    return HTMLResponse(html, status_code=200, headers={"Cache-Control": "public, max-age=3600"})
