"""Module RECHERCHE — convertit une intention de recherche en query canonique.

Centralise tout ce qui était éparpillé : slug→IATA, autocomplete aéroports/villes,
normalisation date/cabin/pax. Tous les consommateurs (page SEO, chat, home,
deeplink, réservation) appellent les fonctions d'ici plutôt que de re-implémenter
leur propre conversion.

Pas de logique métier (pas de cache offres, pas de fallback prix) — juste
conversion + aiguillage vers le bon module offre (vol/hotel/activités/transferts/séjour).
"""
import psycopg2, psycopg2.extras
from typing import Optional, Tuple
from fastapi import APIRouter

router = APIRouter()

# NB : on importe DB_CONFIG / _airport_info / _slugify en LAZY (dans les fonctions)
# car main.py ré-exporte _vols_route_map d'ici → circular import si on importe au top.


def slug_to_iata_pair(slug: str) -> Optional[Tuple[str, str]]:
    """Convertit un slug SEO route ('paris-los-angeles') en pair IATA ('CDG', 'LAX').

    Retourne None si le slug n'a pas de route catalogue correspondante.
    Source : table route_stats (catalogue des routes ayant une page SEO).
    """
    if not slug:
        return None
    rmap = _build_route_map()
    return rmap.get(slug.lower().strip())


def _build_route_map() -> dict:
    """Map slug ville_pair → (origin_iata, dest_iata) depuis route_stats."""
    from main import DB_CONFIG, _airport_info, _slugify  # lazy : casse l'import circulaire
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT origin, destination FROM route_stats")
    rows = cur.fetchall()
    cur.close(); conn.close()
    m = {}
    for r in rows:
        o, d = r["origin"], r["destination"]
        oi, di = _airport_info(o), _airport_info(d)
        slug = f"{_slugify(oi.get('city') or o)}-{_slugify(di.get('city') or d)}"
        m[slug] = (o, d)
    return m


# ── Compatibilité legacy ──
# main.py et d'autres modules importent encore `_vols_route_map()` (dict complet).
# On garde l'alias jusqu'à ce que les callers passent à `slug_to_iata_pair(slug)`.
def _vols_route_map() -> dict:
    return _build_route_map()
