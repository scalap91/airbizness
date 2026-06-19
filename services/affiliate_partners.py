"""Registre des partenaires d'affiliation hôtels — module ② (2026-06-19).

UNE source de vérité pour le bloc « Comparer les prix » des fiches SEO.
Chaque partenaire = un deeplink de recherche DIRECT (domaine public, https),
routé via /api/affiliate-redirect qui (a) injecte l'ID d'affiliation depuis .env
et (b) logge le clic dans affiliate_clicks. Le tracking du clic marche TOUJOURS,
même sans ID.

Stratégie HYBRIDE (validée Pascal 2026-06-19) :
- ID direct présent dans .env  → le lien rapporte au taux du programme direct.
- ID direct absent             → le lien marche et le clic est tracké, mais ne
                                 rapporte pas encore (s'upgrade en ajoutant 1 var .env).
- La ligne Hotellook porte le marker TravelPayouts déjà actif → rapporte AUJOURD'HUI
  (redistribution Booking/Agoda/Expedia/Trip.com…).

L'injection d'ID se fait côté handler (routers/affiliate.py::AFFILIATE_PARAMS) :
ce fichier ne fabrique que l'URL de recherche publique, jamais d'ID en dur.
"""
from urllib.parse import quote_plus


def _q(name: str, city: str, country: str) -> str:
    return quote_plus(f"{name} {city or ''} {country or ''}".strip())


def date_params(key: str, checkin: str, checkout: str, adults: int = 2) -> str:
    """Fragment de query 'dates par défaut' + occupants, par partenaire.
    Best-effort : si un param ne colle pas, le partenaire l'ignore (l'hôtel s'affiche
    quand même) → aucun risque de lien cassé. Dates au format YYYY-MM-DD.
    Le visiteur peut changer les dates côté partenaire ; le prix affiché est le leur (live)."""
    if key == "booking":
        return f"&checkin={checkin}&checkout={checkout}&group_adults={adults}&no_rooms=1"
    if key in ("expedia", "hotels"):
        return f"&startDate={checkin}&endDate={checkout}&adults={adults}"
    if key == "agoda":
        return f"&checkIn={checkin}&checkOut={checkout}&adults={adults}"
    if key == "trip":
        return f"&checkin={checkin}&checkout={checkout}"
    if key == "hotellook":
        return f"&checkIn={checkin}&checkOut={checkout}&adults={adults}"
    return ""


def _latlong(lat, lon) -> str:
    """'{lat},{lon}' si dispo, sinon ''. Sert à épingler la recherche sur l'hôtel exact."""
    try:
        if lat is not None and lon is not None and str(lat) and str(lon):
            return f"{float(lat)},{float(lon)}"
    except (TypeError, ValueError):
        pass
    return ""


# Ordre = ordre d'affichage. provider doit être dans VALID_PROVIDERS (routers/affiliate.py).
# Booking n'est PAS ici : il a déjà son bouton "héros" dédié sur la fiche.
# Builders : (nom, ville, pays, lat, lon) → recherche par nom complet (l'hôtel sort en tête) ;
# Expedia épinglé sur le GPS via latLong quand dispo. ID hôtel exact par partenaire = payant (non câblé).
PARTNERS = [
    {"key": "agoda",     "label": "Agoda",      "color": "#ff6a00",
     "url": lambda n, c, co, lat, lon: f"https://www.agoda.com/search?q={_q(n, c, co)}"},
    {"key": "expedia",   "label": "Expedia",    "color": "#1668e3",
     "url": lambda n, c, co, lat, lon: (
         f"https://www.expedia.com/Hotel-Search?destination={_q(n, c, co)}"
         + (f"&latLong={quote_plus(_latlong(lat, lon))}" if _latlong(lat, lon) else ""))},
    {"key": "trip",      "label": "Trip.com",   "color": "#287dfa",
     "url": lambda n, c, co, lat, lon: f"https://www.trip.com/hotels/list?keyword={_q(n, c, co)}"},
    {"key": "hotels",    "label": "Hotels.com", "color": "#d32f2f",
     "url": lambda n, c, co, lat, lon: f"https://www.hotels.com/Hotel-Search?destination={_q(n, c, co)}"},
    {"key": "hotellook", "label": "Comparer tous les prix", "color": "#d4ae4a",
     "url": lambda n, c, co, lat, lon: f"https://search.hotellook.com/?destination={_q(n, c, co)}"},
]
