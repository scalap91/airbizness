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


# Ordre = ordre d'affichage. provider doit être dans VALID_PROVIDERS (routers/affiliate.py).
# Booking n'est PAS ici : il a déjà son bouton "héros" dédié sur la fiche.
PARTNERS = [
    {"key": "agoda",     "label": "Agoda",      "color": "#ff6a00",
     "url": lambda n, c, co: f"https://www.agoda.com/search?q={_q(n, c, co)}"},
    {"key": "expedia",   "label": "Expedia",    "color": "#1668e3",
     "url": lambda n, c, co: f"https://www.expedia.com/Hotel-Search?destination={_q(n, c, co)}"},
    {"key": "trip",      "label": "Trip.com",   "color": "#287dfa",
     "url": lambda n, c, co: f"https://www.trip.com/hotels/list?keyword={_q(n, c, co)}"},
    {"key": "hotels",    "label": "Hotels.com", "color": "#d32f2f",
     "url": lambda n, c, co: f"https://www.hotels.com/Hotel-Search?destination={_q(n, c, co)}"},
    {"key": "hotellook", "label": "Comparer tous les prix", "color": "#d4ae4a",
     "url": lambda n, c, co: f"https://search.hotellook.com/?destination={_q(n, c, co)}"},
]
