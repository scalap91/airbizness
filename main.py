from fastapi import FastAPI, Request, HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
import psycopg2, psycopg2.extras, os, stripe, sib_api_v3_sdk, io, json, urllib.parse, re
from sib_api_v3_sdk.rest import ApiException
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import Optional, List

load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
BREVO_KEY = os.getenv("BREVO_KEY")

# ─── Duffel mode banner (P1 #2 — 2026-05-26) ────────────────────────────────
# Affiché au démarrage pour que Pascal voie tout de suite quel mode est actif.
print(
    f"[duffel-init] mode={os.getenv('DUFFEL_MODE','test')} "
    f"dry_run={os.getenv('DUFFEL_BOOKING_DRY_RUN','true')}"
)

app = FastAPI()

# /healthz déplacé dans routers/admin_ops.py (2026-06-03)


# ═══════════════════════════════════════════════════════════════
# Airports & Cities autocomplete — 2026-05-26
# Source : OpenFlights (data/airports.json), ~6000 aéroports IATA.
# Cache mémoire au démarrage (singleton _airports_cache).
# Routes (relatives, nginx ajoute /api/ devant et strip avant proxy) :
#   GET /airports/search?q=...&limit=8   → exposé en /api/airports/search
#   GET /cities/search?q=...&limit=8     → exposé en /api/cities/search
# ═══════════════════════════════════════════════════════════════

_airports_cache: list = []
_airports_top: list = []
_TOP_POPULAR_IATA = [
    "CDG", "DXB", "JFK", "LHR", "LAX", "HND", "SIN", "FCO",
    "BCN", "MAD", "AMS", "FRA", "MUC", "ZRH", "IST", "DOH",
    "RAK", "CMN", "ORY", "GVA",
]


def _load_airports_cache() -> None:
    """Charge airports.json en mémoire (1 seule fois au démarrage)."""
    global _airports_cache, _airports_top
    if _airports_cache:
        return
    path = "/var/www/airbizness/data/airports.json"
    try:
        with open(path, encoding="utf-8") as f:
            _airports_cache = json.load(f)
    except FileNotFoundError:
        print(f"[airports] WARN: {path} introuvable, fallback liste vide")
        _airports_cache = []
        _airports_top = []
        return
    except Exception as e:
        print(f"[airports] ERROR loading {path}: {e}")
        _airports_cache = []
        _airports_top = []
        return

    # Construire la "top popular" liste : codes IATA majeurs dans l'ordre défini
    by_code = {a["code"]: a for a in _airports_cache}
    _airports_top = [by_code[c] for c in _TOP_POPULAR_IATA if c in by_code]
    print(f"[airports] {len(_airports_cache)} aéroports chargés en cache "
          f"({len(_airports_top)} top populaires)")


# Charge au démarrage du module (FastAPI startup-safe : exécuté à l'import).
_load_airports_cache()


@app.get("/airports/search")
def airports_search(q: str = "", limit: int = 8):
    """Autocomplete aéroports : matching IATA / ville / nom / pays.

    Args:
        q: chaîne de recherche (>= 2 chars conseillé, sinon retourne le top).
        limit: nombre max de résultats (8 par défaut, plafonné à 30).

    Returns:
        JSON list of {code, name, city, country, type}.
    """
    if not _airports_cache:
        _load_airports_cache()

    limit = max(1, min(int(limit or 8), 30))
    q_lower = (q or "").lower().strip()

    if not q_lower or len(q_lower) < 2:
        return _airports_top[:limit]

    matches = []
    for a in _airports_cache:
        code_l = a["code"].lower()
        city_l = a["city"].lower()
        name_l = a["name"].lower()
        country_l = a["country"].lower()
        is_top = a["code"] in _TOP_POPULAR_IATA

        score = 0
        # Code exact = ultra prioritaire, mais on booste les codes "top"
        # pour qu'un "dub" mette DXB (top) devant DUB (Dublin non-top).
        # Échelle :
        #   code exact (top)        = 110
        #   city startswith (top)   = 105   ← DXB pour "dub"
        #   code exact (non-top)    = 100   ← DUB pour "dub"
        #   code startswith (top)   = 90
        #   code startswith         = 80
        #   city == q               = 75
        #   city startswith         = 60
        #   q in city               = 40
        #   name startswith         = 30
        #   q in name               = 20
        #   country startswith      = 15
        #   q in country            =  5
        if code_l == q_lower:
            score = 110 if is_top else 100
        elif city_l.startswith(q_lower) and is_top:
            score = 105
        elif code_l.startswith(q_lower):
            score = 90 if is_top else 80
        elif city_l == q_lower:
            score = 75
        elif city_l.startswith(q_lower):
            score = 60
        elif q_lower in city_l:
            score = 40
        elif name_l.startswith(q_lower):
            score = 30
        elif q_lower in name_l:
            score = 20
        elif country_l.startswith(q_lower):
            score = 15
        elif q_lower in country_l:
            score = 5

        if score > 0:
            matches.append((score, a))

    matches.sort(key=lambda x: (-x[0], x[1]["city"].lower(), x[1]["code"]))
    return [a for _, a in matches[:limit]]


_cities_cache: list = []


def _load_cities_cache() -> list:
    """Charge 1 ligne par destination_code, libellé normalisé (Pascal 2026-05-31).

    Avant : GROUP BY (destination_code, city, country_code) → 3 lignes "PARIS"/
    "Paris"/"PARIS " visibles dans l'autocomplete. Maintenant : group sur le
    seul destination_code, libellé via INITCAP+LOWER+TRIM = "Paris" propre.
    """
    global _cities_cache
    if _cities_cache:
        return _cities_cache
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            WITH normalized AS (
                SELECT
                    destination_code,
                    INITCAP(LOWER(TRIM(city))) AS city,
                    country_code,
                    COUNT(*) AS cnt
                FROM hbx_hotels_catalog
                WHERE city IS NOT NULL AND TRIM(city) <> ''
                GROUP BY destination_code, INITCAP(LOWER(TRIM(city))), country_code
            ),
            ranked AS (
                SELECT
                    destination_code,
                    city,
                    country_code,
                    ROW_NUMBER() OVER (
                        PARTITION BY destination_code ORDER BY cnt DESC
                    ) AS rn,
                    SUM(cnt) OVER (PARTITION BY destination_code) AS total_hotels
                FROM normalized
            )
            SELECT destination_code, city, country_code,
                   total_hotels AS hotels
            FROM ranked
            WHERE rn = 1
            ORDER BY total_hotels DESC
            LIMIT 5000
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        _cities_cache = [
            {
                "destination_code": r["destination_code"],
                "city": r["city"],
                "country_code": r.get("country_code") or "",
                "hotels": int(r["hotels"]),
            }
            for r in rows
        ]
        print(f"[cities] {len(_cities_cache)} villes hôtelières chargées en cache")
    except Exception as e:
        print(f"[cities] ERROR : {e}")
        _cities_cache = []
    return _cities_cache


@app.get("/cities/search")
def cities_search(q: str = "", limit: int = 8):
    """Autocomplete villes (hôtels HBX) : matching ville / destination_code.

    Returns:
        JSON list of {destination_code, city, country_code, hotels}.
    """
    cities = _load_cities_cache()
    limit = max(1, min(int(limit or 8), 30))
    q_lower = (q or "").lower().strip()

    if not q_lower or len(q_lower) < 2:
        return cities[:limit]

    matches = []
    for c in cities:
        city_l = (c["city"] or "").lower()
        code_l = (c["destination_code"] or "").lower()
        score = 0
        if code_l == q_lower:
            score = 100
        elif city_l == q_lower:
            score = 90
        elif city_l.startswith(q_lower):
            score = 70
        elif q_lower in city_l:
            score = 40
        if score > 0:
            matches.append((score, c))

    matches.sort(key=lambda x: (-x[0], -x[1]["hotels"], x[1]["city"].lower()))
    return [c for _, c in matches[:limit]]


# /api/admin/duffel_status, /admin/duffel_status, /api/admin/seo/status,
# /admin/seo/status, /supervisor/pending déplacés dans routers/admin_ops.py (2026-06-03)




# ═══════════════════════════════════════════════════════════════════════
# Magic link — accès passwordless au compte client (mes-voyages)
# Token signé HMAC (CHATBOT_CREDENTIAL_SECRET), TTL 30j. Encode l'email.
# ═══════════════════════════════════════════════════════════════════════
def _magic_secret() -> bytes:
    return (os.getenv("CHATBOT_CREDENTIAL_SECRET") or "airbizness-magic-fallback").encode()


def sign_magic_token(email: str, ttl: int = 2592000) -> str:
    import hmac as _h, hashlib as _hl, base64 as _b64, time as _t
    payload = {"email": (email or "").lower().strip(), "exp": int(_t.time()) + ttl}
    raw = _b64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = _h.new(_magic_secret(), raw.encode(), _hl.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify_magic_token(token: str) -> Optional[str]:
    import hmac as _h, hashlib as _hl, base64 as _b64, time as _t
    try:
        raw, sig = (token or "").split(".", 1)
        exp_sig = _h.new(_magic_secret(), raw.encode(), _hl.sha256).hexdigest()[:32]
        if not _h.compare_digest(sig, exp_sig):
            return None
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(raw + pad))
        if int(payload.get("exp", 0)) < int(_t.time()):
            return None
        return payload.get("email")
    except Exception:
        return None


def magic_link_for(email: str, ref: str = "") -> str:
    # /api/... car nginx ne proxy vers le backend que sous /api/ (route /account/* directe = 404)
    base = os.getenv("ACTION_VALIDATION_BASE_URL", "https://airbizness.com")
    tok = sign_magic_token(email)
    url = f"{base}/api/account/magic?token={urllib.parse.quote(tok)}"
    if ref:
        url += f"&ref={urllib.parse.quote(ref)}"
    return url


@app.get("/account/magic")
def account_magic(token: str = "", ref: str = ""):
    from fastapi.responses import RedirectResponse
    email = verify_magic_token(token)
    if not email:
        return RedirectResponse(url="/compte.html?err=lien_invalide", status_code=302)
    q = f"?mt={urllib.parse.quote(token)}"
    if ref:
        q += f"&ref={urllib.parse.quote(ref)}"
    return RedirectResponse(url=f"/mes-voyages.html{q}", status_code=302)


# ═══════════════════════════════════════════════════════════════════════
# GOOGLE SIGN-IN (Google Identity Services) — inscription/connexion réelle.
# Le front envoie le `credential` (ID token JWT signé par Google). On le
# vérifie auprès de Google (tokeninfo), on contrôle l'audience (client_id),
# on upsert l'utilisateur en DB, et on renvoie un magic token de session.
# ═══════════════════════════════════════════════════════════════════════
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()


@app.get("/auth/config")
def auth_config():
    """Config publique pour le front (Client ID Google — PAS de secret ici)."""
    return {"google_client_id": GOOGLE_CLIENT_ID, "google_enabled": bool(GOOGLE_CLIENT_ID)}


@app.get("/config/verticals")
def config_verticals():
    """Commutateur de verticales lu par l'accueil (etude_switch_verticales.md, étape 1).
    Source = AIRBIZNESS_VERTICALS dans .env. Le front masque les onglets/nav non actifs."""
    enabled = verticals_enabled()
    return {"enabled": enabled, "default": enabled[0]}


class _GoogleAuthReq(BaseModel):
    credential: str  # ID token JWT renvoyé par Google Identity Services


def _verify_google_id_token(credential: str) -> Optional[dict]:
    """Vérifie l'ID token Google via l'endpoint tokeninfo officiel.
    Renvoie le payload (claims) si valide ET destiné à NOTRE client_id, sinon None."""
    if not credential:
        return None
    try:
        url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(credential)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            claims = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[google-auth] tokeninfo KO: {e}")
        return None
    # Contrôles de sécurité
    iss = claims.get("iss", "")
    if iss not in ("accounts.google.com", "https://accounts.google.com"):
        print(f"[google-auth] iss invalide: {iss}")
        return None
    if GOOGLE_CLIENT_ID and claims.get("aud") != GOOGLE_CLIENT_ID:
        print(f"[google-auth] audience mismatch (aud={claims.get('aud')!r})")
        return None
    if str(claims.get("email_verified", "false")).lower() not in ("true", "1"):
        print("[google-auth] email non vérifié")
        return None
    return claims


@app.post("/account/google")
def account_google(body: _GoogleAuthReq):
    """Inscription / connexion via Google. Renvoie {ok, token, user}."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google Sign-In non configuré (GOOGLE_CLIENT_ID manquant)")
    claims = _verify_google_id_token(body.credential)
    if not claims:
        raise HTTPException(401, "Token Google invalide ou non vérifié")

    email = (claims.get("email") or "").lower().strip()
    sub = claims.get("sub")
    if not email or not sub:
        raise HTTPException(400, "Profil Google incomplet")

    full_name = claims.get("name") or ""
    given = claims.get("given_name") or (full_name.split(" ")[0] if full_name else "")
    picture = claims.get("picture") or ""
    locale = claims.get("locale") or ""

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO users (email, full_name, given_name, picture_url, google_sub,
                               auth_provider, email_verified, last_login_at, locale, raw_profile)
            VALUES (%s,%s,%s,%s,%s,'google',true,now(),%s,%s::jsonb)
            ON CONFLICT (email) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                given_name = EXCLUDED.given_name,
                picture_url = EXCLUDED.picture_url,
                google_sub = COALESCE(users.google_sub, EXCLUDED.google_sub),
                last_login_at = now(),
                locale = EXCLUDED.locale,
                raw_profile = EXCLUDED.raw_profile
            RETURNING id, email, full_name, given_name, picture_url, created_at
            """,
            (email, full_name, given, picture, sub, locale,
             json.dumps({k: claims.get(k) for k in ("name", "given_name", "family_name",
                                                       "picture", "locale", "sub")})),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[google-auth] DB upsert KO: {e}")
        raise HTTPException(500, "Erreur enregistrement du compte")

    token = sign_magic_token(email)
    return {
        "ok": True,
        "token": token,
        "user": {
            "id": row["id"],
            "email": row["email"],
            "prenom": row["given_name"] or (row["full_name"] or "Voyageur").split(" ")[0],
            "full_name": row["full_name"],
            "picture": row["picture_url"],
        },
    }


@app.get("/account/me")
def account_me(token: str = ""):
    """Renvoie le profil utilisateur à partir du token de session (magic token)."""
    email = verify_magic_token(token)
    if not email:
        raise HTTPException(401, "Session invalide")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, email, full_name, given_name, picture_url FROM users WHERE lower(email)=%s",
                    (email,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception:
        row = None
    if not row:
        # compte connu via token mais pas en table users (ex: magic link booking) → minimal
        return {"ok": True, "user": {"email": email, "prenom": email.split("@")[0]}}
    return {"ok": True, "user": {
        "id": row["id"], "email": row["email"],
        "prenom": row["given_name"] or (row["full_name"] or "Voyageur").split(" ")[0],
        "full_name": row["full_name"], "picture": row["picture_url"],
    }}


class _AccountBookingsReq(BaseModel):
    token: Optional[str] = None
    email: Optional[EmailStr] = None


@app.post("/account/bookings")
def account_bookings(request: Request, body: _AccountBookingsReq):
    """Liste vols + hôtels d'un client. Auth par magic token (préféré) ou email."""
    email = verify_magic_token(body.token) if body.token else None
    if not email and body.email:
        email = str(body.email).lower()
    if not email:
        raise HTTPException(401, "auth_required")
    flights, hotels = [], []
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, status, origin, destination, airline_name,
                   airline_code, departure_at, cabin_class, seat, total_eur,
                   currency, pnr FROM flight_bookings
            WHERE user_email = %s ORDER BY departure_at DESC NULLS LAST LIMIT 50
        """, (email,))
        for r in cur.fetchall():
            d = dict(r)
            if d.get("departure_at"):
                d["departure_at"] = d["departure_at"].isoformat() if hasattr(d["departure_at"], "isoformat") else str(d["departure_at"])
            if d.get("total_eur") is not None:
                d["total_eur"] = float(d["total_eur"])
            flights.append(d)
        cur.execute("""
            SELECT airbizness_ref, hbx_reference, status, hotel_name,
                   destination_name, destination_code, check_in, check_out,
                   nights, adults, rooms_count, gross_price, currency
            FROM bookings_v2 WHERE user_email = %s ORDER BY created_at DESC LIMIT 50
        """, (email,))
        for r in cur.fetchall():
            d = dict(r)
            for k in ("check_in", "check_out"):
                if d.get(k):
                    d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
            if d.get("gross_price") is not None:
                d["gross_price"] = float(d["gross_price"])
            hotels.append(d)
        cur.close(); conn.close()
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"email": email, "flights": flights, "hotels": hotels}




# ═══════════════════════════════════════════════════════════════════════
# /concierge/validate-action — L4 email click handler
# Doctrine credchain Pascal 2026-05-27 : tout pattern executor critique
# (refund, cancel, modify) exige un clic email (out-of-band) avant exec réelle.
# ═══════════════════════════════════════════════════════════════════════




limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
ALLOWED_ORIGINS = [
    "https://airbizness.com",
    "https://www.airbizness.com",
    "http://127.0.0.1:8001",
    "http://localhost:8001",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)
DB_CONFIG = {"host": os.getenv("DB_HOST"), "dbname": os.getenv("DB_NAME"), "user": os.getenv("DB_USER"), "password": os.getenv("DB_PASS")}


# ═════════════════════════════════════════════════════════════════════════
# MOCK MODE HÔTEL — Doctrine Pascal (2026-05-24)
# "Le parcours hôtel doit aboutir jusqu'au paiement même si HBX est KO."
# Quota HBX épuisé → fallback catalog DB + prix mock crédible + rate_key
# signé MOCK-HBX-<hash>. Activé via HOTEL_MOCK_MODE=true (cf .env).
# ═════════════════════════════════════════════════════════════════════════
import hashlib as _hashlib
import random as _random

def _hotel_mock_enabled() -> bool:
    return (os.getenv("HOTEL_MOCK_MODE", "false") or "").strip().lower() in ("1", "true", "yes", "on")

# ── Commutateur de verticales (étude etude_switch_verticales.md, 2026-06-17) ──
# Source unique : AIRBIZNESS_VERTICALS dans .env. Valeurs : "hotels", "flights",
# "hotels,flights". Phase 1 = hôtels seuls (licence vol pas encore obtenue).
# ÉTAPE 0 : helper seul, AUCUN effet visible. Le branchement (front/SEO/backend)
# vient aux étapes 1-3.
_VALID_VERTICALS = ("hotels", "flights")

def verticals_enabled() -> list:
    """Liste ordonnée des verticales actives. Défaut sûr = ['hotels']."""
    raw = (os.getenv("AIRBIZNESS_VERTICALS", "hotels") or "hotels").strip().lower()
    out = [v.strip() for v in raw.split(",") if v.strip() in _VALID_VERTICALS]
    return out or ["hotels"]

def vertical_default() -> str:
    """Verticale affichée par défaut sur l'accueil = la première active."""
    return verticals_enabled()[0]

def vertical_active(name: str) -> bool:
    return name in verticals_enabled()

# ── ÉTAPE 3 : garde-fou backend (etude_switch_verticales.md) ──
# Tant que la verticale "flights" n'est pas active, on coupe TOUTES les routes vol
# (recherche, deals, calendrier, offres Duffel, pages SEO /vols, réservation vol).
# Garde-fou central (un seul endroit, pas de patch éparpillé) : impossible de
# réserver/afficher un vol par une URL directe sans la licence. Préfixes 100% vol,
# aucun n'est utilisé côté hôtel.
_FLIGHT_PREFIXES = ("/vols", "/flight", "/flights", "/deals", "/duffel")

@app.middleware("http")
async def _gate_flight_routes(request, call_next):
    if not vertical_active("flights"):
        p = request.url.path
        if any(p == pre or p.startswith(pre + "/") for pre in _FLIGHT_PREFIXES):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)

def _mock_rate_key(hotel_code, check_in: str, check_out: str, guests: int,
                   board: str = "RO", room_idx: int = 0) -> str:
    raw = f"{hotel_code}|{check_in}|{check_out}|{guests}|{board}|{room_idx}".encode()
    h = _hashlib.sha1(raw).hexdigest()[:16].upper()
    return f"MOCK-HBX-{h}"

def _is_mock_rate_key(rate_key) -> bool:
    if not rate_key:
        return False
    rk = str(rate_key)
    if rk.startswith("hbx:"):
        rk = rk[4:]
    return rk.startswith("MOCK-HBX-") or rk.startswith("MOCK-")

def _nights_between(check_in: str, check_out: str) -> int:
    try:
        a = datetime.fromisoformat(str(check_in)[:10])
        b = datetime.fromisoformat(str(check_out)[:10])
        return max(1, (b - a).days)
    except Exception:
        return 1

def _mock_base_price_per_night(stars, hotel_code) -> float:
    """Déterministe par hotel_code → pas de price drift entre quote/checkrate/confirm."""
    try: s = int(stars or 3)
    except Exception: s = 3
    try: rng = _random.Random(int(hotel_code) if hotel_code else 1)
    except Exception: rng = _random.Random(1)
    return round(50.0 + s * 30.0 + rng.uniform(0, 40.0), 2)

class PaymentIntentRequest(BaseModel):
    amount: int
    currency: str = "eur"
    booking: dict = {}

class EmailRequest(BaseModel):
    to_email: str
    to_name: str
    booking_ref: str
    origin: str
    destination: str
    airline: str
    date: str
    price: float

class AlerteRequest(BaseModel):
    email: EmailStr
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(default="", max_length=3)
    max_price: int = Field(gt=0, lt=100000)

class BookingRequest(BaseModel):
    booking_ref: str = Field(min_length=4, max_length=32)
    offer_id: str = Field(min_length=4, max_length=128)
    user_email: EmailStr
    passenger_name: str = Field(default="", max_length=120)
    amount_cents: int = Field(gt=0, lt=10000000)
    currency: str = Field(default="eur", max_length=3)
    stripe_payment_intent: str = Field(default="", max_length=128)

# ──────────────────────────────────────────────────────────────────────────
# DUFFEL : refresh offer cache → live (anti-bait-and-switch)
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# FARE OPTIONS — comparatif Light/Standard/Flex (Pascal 2026-05-27, AirFrance-like)
# Pour un offer du cache : on relance un search live Duffel sur la même route,
# on filtre les offers qui partagent la même slice signature (mêmes vols/horaires
# /carrier), on garde les fare_brands distincts et on les normalise pour le front.
# ──────────────────────────────────────────────────────────────────────────

def _slice_signature(offer: dict) -> str:
    """Signature unique de la liste de segments (carrier + flight number + dep + route).

    Permet de regrouper plusieurs offers Duffel qui partagent EXACTEMENT les mêmes
    vols mais qui diffèrent par leur fare_brand / conditions / services.
    """
    parts = []
    for slc in offer.get("slices") or []:
        for seg in slc.get("segments") or []:
            carrier = (seg.get("marketing_carrier") or {}).get("iata_code", "")
            flight = seg.get("marketing_carrier_flight_number", "")
            origin = (seg.get("origin") or {}).get("iata_code", "")
            dest = (seg.get("destination") or {}).get("iata_code", "")
            dep = (seg.get("departing_at") or "")[:16]  # ignore secondes
            parts.append(f"{origin}-{dest}-{dep}-{carrier}{flight}")
    return "|".join(parts)


def _classify_fare_brand(brand_name: str) -> str:
    """Normalise un fare_brand_name compagnie (ex 'ECONOMY LIGHT', 'Flex Plus',
    'Business Saver') vers un bucket : light / standard / flex.

    Heuristique : on cherche les mots-clés dans l'ordre flex > light > standard.
    Si rien ne matche, on retombe sur 'standard'.
    """
    b = (brand_name or "").lower()
    if not b:
        return "standard"
    if any(k in b for k in ("flex", "flexi", "plus", "premium", "business", "first")):
        # Note : on garde 'flex' comme bucket même pour business/first car c'est
        # le tarif "tout inclus" du point de vue conditions (modif/remb).
        return "flex"
    if any(k in b for k in ("light", "basic", "saver", "lite", "eco light", "essential", "hand")):
        return "light"
    return "standard"


def _normalize_fare_services(offer: dict) -> dict:
    """Extrait depuis un offer Duffel les booléens/états des services pour la UI
    de comparatif. Toujours retourne le même set de clés (front rendering facile).

    Sources Duffel :
      - offer.conditions.change_before_departure / refund_before_departure
      - offer.slices[].segments[].passengers[].baggages (carry_on/checked count)
      - offer.available_services pour seat_selection / extra_bag (si présents)
    """
    cond = offer.get("conditions") or {}
    chg = cond.get("change_before_departure") or {}
    rfd = cond.get("refund_before_departure") or {}

    def _state(c: dict) -> str:
        if c.get("allowed") is True:
            try:
                pen = float(c.get("penalty_amount") or 0)
            except (TypeError, ValueError):
                pen = 0.0
            return "allowed" if pen <= 0 else "paid"
        if c.get("allowed") is False:
            return "not_allowed"
        return "unknown"

    # Bagages : on agrège sur le 1er passager du 1er segment (signature stable)
    carry_on = False
    checked = 0
    cabin_marketing = None
    try:
        seg0 = (offer.get("slices") or [{}])[0].get("segments") or [{}]
        pax0 = (seg0[0].get("passengers") or [{}])[0]
        cabin_marketing = pax0.get("cabin_class_marketing_name") or pax0.get("cabin_class")
        for b in pax0.get("baggages") or []:
            btype = b.get("type")
            qty = int(b.get("quantity") or 0)
            if btype == "carry_on" and qty > 0:
                carry_on = True
            elif btype == "checked":
                checked += qty
    except Exception:
        pass

    # Sélection siège : si available_services contient un type "seat" → payant ;
    # sinon on suppose 'paid' par défaut (rare qu'un siège soit "free" en LIGHT).
    seat_selection = "paid"
    has_seat_svc = False
    for svc in (offer.get("available_services") or []):
        if svc.get("type") == "seat":
            has_seat_svc = True
            break
    if not has_seat_svc:
        seat_selection = "unknown"

    return {
        "carry_on_bag": carry_on,
        "checked_bag_count": checked,
        "checked_bag": checked > 0,
        "modification": _state(chg),
        "modification_penalty": chg.get("penalty_amount"),
        "modification_currency": chg.get("penalty_currency"),
        "refund": _state(rfd),
        "refund_penalty": rfd.get("penalty_amount"),
        "refund_currency": rfd.get("penalty_currency"),
        "seat_selection": seat_selection,
        "cabin_class_marketing": cabin_marketing,
    }


# ═══════════════════════════════════════════════════════════════════════
# Price Calendar Bandeau ±N jours — Pascal 2026-05-27
# Endpoint : GET /flights/price-calendar?from=CDG&to=JFK&date=2026-05-28&pax=1&cabin=business&days=3
# Renvoie 2*days+1 cellules (J-days .. J+days) avec min_price par jour.
# - Lance N recherches Duffel en parallèle via asyncio.to_thread (search_offers_live est sync urllib)
# - Cache PG 4h (table flight_price_calendar_cache, clé sig from/to/date/pax/cabin)
# - Watchdog Telegram si > 50% cellules en erreur
# - Coût Duffel : 7 recherches par initial load → cache OBLIGATOIRE
# ═══════════════════════════════════════════════════════════════════════




# ─────────────────────────────────────────────────────────────────────────────
# 2026-05-27 fix Pascal — CACHE UNIFIÉ flight_offers_cache
# Source unique pour /deals (cards) et /flights/price-calendar (bandeau).
# Évite l'écart historique : calendar cachait 4h, deals tapait live → prix différents.
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_or_cache_offers(origin: str, destination: str, departure_date: str,
                            passengers: int = 1, cabin_class: str = "economy",
                            ttl_seconds: int = 14400):
    """Retourne (offers_list, min_price, offers_count) depuis cache PG ou fetch Duffel live."""
    import hashlib, json as _json
    cache_key = hashlib.sha256(
        f"{origin.upper()}|{destination.upper()}|{departure_date}|{cabin_class.lower()}|{passengers}".encode()
    ).hexdigest()
    # Cache hit
    try:
        c = psycopg2.connect(**DB_CONFIG)
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT offers, min_price, offers_count FROM flight_offers_cache "
            "WHERE cache_key=%s AND expires_at > NOW()", (cache_key,)
        )
        row = cur.fetchone()
        cur.close(); c.close()
        if row:
            return (row["offers"] or []), (float(row["min_price"]) if row["min_price"] is not None else None), row["offers_count"]
    except Exception as e:
        print(f"[foc-read] {e}")
    # Miss → live (propage DuffelHttpError au caller pour gestion 429/5xx)
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")
    from providers.duffel import search_offers_live
    offers = search_offers_live(origin=origin.upper(), destination=destination.upper(),
                                  departure_date=departure_date, passengers=passengers,
                                  cabin_class=cabin_class.lower()) or []
    try:
        offers = sorted(offers, key=lambda x: float(x.get("total_amount") or 0))
    except Exception: pass
    prices = []
    for o in offers:
        try: prices.append(float(o.get("total_amount") or 0))
        except (TypeError, ValueError): pass
    prices = [p for p in prices if p > 0]
    min_price = round(min(prices), 2) if prices else None
    offers_count = len(prices)
    # Write cache
    try:
        c2 = psycopg2.connect(**DB_CONFIG)
        cur2 = c2.cursor()
        cur2.execute(
            "INSERT INTO flight_offers_cache "
            "(cache_key, origin, destination, departure_date, cabin_class, passengers, offers, offers_count, min_price, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 second')) "
            "ON CONFLICT (cache_key) DO UPDATE SET offers=EXCLUDED.offers, offers_count=EXCLUDED.offers_count, "
            "min_price=EXCLUDED.min_price, expires_at=EXCLUDED.expires_at",
            (cache_key, origin.upper(), destination.upper(), departure_date,
             cabin_class.lower(), passengers, _json.dumps(offers), offers_count, min_price, ttl_seconds)
        )
        c2.commit(); cur2.close(); c2.close()
    except Exception as e:
        print(f"[foc-write] {e}")
    return offers, min_price, offers_count



# /stats et /home-stats déplacés dans routers/admin_ops.py (2026-06-03)


@app.post("/send-confirmation")
def send_confirmation(req: EmailRequest):
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    html = f"""<div style='font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#f0ece4;padding:32px;'>
    <h1 style='color:#d4ae4a;'>AirBizness</h1>
    <h2>Votre reservation est confirmee !</h2>
    <div style='background:#161616;border:1px solid #333;padding:20px;margin:20px 0;'>
      <p style='color:#a09890;font-size:12px;'>N DE RESERVATION</p>
      <p style='color:#d4ae4a;font-size:24px;font-weight:bold;'>{req.booking_ref}</p>
    </div>
    <p style='font-size:28px;font-weight:bold;'>{req.origin} vers {req.destination}</p>
    <p style='color:#a09890;'>{req.airline} - Classe Affaires - {req.date}</p>
    <p style='margin-top:16px;'>Prix paye : <strong>{req.price:.0f} EUR</strong></p>
    <hr style='border-color:#333;margin:20px 0;'/>
    <p style='color:#6a6058;font-size:11px;'>AirBizness - airbizness.com</p>
    </div>"""
    try:
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": req.to_email, "name": req.to_name}],
            sender={"email": "noreply@airbizness.com", "name": "AirBizness"},
            subject=f"Confirmation {req.booking_ref} - {req.origin} vers {req.destination}",
            html_content=html
        )
        api.send_transac_email(send_smtp_email)
        return {"status": "sent"}
    except ApiException as e:
        return {"status": "error", "detail": str(e)}

def _serialize_deal(d):
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif type(v).__name__ == "Decimal":
            out[k] = float(v)
        else:
            out[k] = v
    return out

@app.post("/bookings")
@limiter.limit("10/minute")
def create_booking(request: Request, req: BookingRequest):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM deals WHERE offer_id = %s LIMIT 1", (req.offer_id,))
    deal = cur.fetchone()
    if not deal:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Offer not found")
    try:
        cur.execute("""
            INSERT INTO bookings (booking_ref, offer_id, user_email, passenger_name,
                amount_cents, currency, stripe_payment_intent,
                origin, destination, airline_name, departure_at, raw_offer)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (booking_ref) DO UPDATE SET
                stripe_payment_intent = EXCLUDED.stripe_payment_intent,
                amount_cents = EXCLUDED.amount_cents
            RETURNING id, booking_ref, created_at
        """, (
            req.booking_ref, req.offer_id, req.user_email, req.passenger_name,
            req.amount_cents, req.currency, req.stripe_payment_intent or None,
            deal["origin"], deal["destination"], deal["airline_name"],
            deal["departure_at"], psycopg2.extras.Json(_serialize_deal(dict(deal))),
        ))
        row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    cur.close(); conn.close()
    return {"status": "saved", "id": row["id"], "booking_ref": row["booking_ref"]}

@app.get("/bookings/by-email")
@limiter.limit("30/minute")
def list_bookings(request: Request, email: EmailStr):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT booking_ref, offer_id, passenger_name, amount_cents, currency, status,
               origin, destination, airline_name, departure_at, created_at, raw_offer
        FROM bookings WHERE user_email = %s ORDER BY created_at DESC LIMIT 100
    """, (email,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"bookings": [dict(r) for r in rows]}

@app.get("/og-image")
def og_image(from_: str = "CDG", to: str = "JFK", price: int = 1230, pct: int = 68, airline: str = ""):
    NAMES = {"JFK":"New York","LAX":"Los Angeles","SIN":"Singapour","NRT":"Tokyo","DXB":"Dubai","HKG":"Hong Kong","BKK":"Bangkok","GRU":"Sao Paulo","LHR":"Londres","AMS":"Amsterdam","FRA":"Francfort","MAD":"Madrid","SYD":"Sydney","DOH":"Doha","ICN":"Seoul","DEL":"Delhi","BOM":"Mumbai"}
    img_path = f"/var/www/airbizness/public/images/destinations/{to.lower()}.jpg"
    try:
        bg = Image.open(img_path).convert("RGB")
    except:
        bg = Image.new("RGB", (1200, 630), "#0f0f0f")
    bg = bg.resize((1200, 630))
    overlay = Image.new("RGBA", (1200, 630), (0, 0, 0, 150))
    bg_rgba = bg.convert("RGBA")
    bg_rgba = Image.alpha_composite(bg_rgba, overlay)
    bg = bg_rgba.convert("RGB")
    draw = ImageDraw.Draw(bg)
    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 90)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 45)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    except:
        font_big = font_med = font_sm = ImageFont.load_default()
    dest_name = NAMES.get(to, to)
    draw.text((60, 50), "AirBizness", fill="#d4ae4a", font=font_med)
    draw.text((60, 130), f"{from_}  ->  {dest_name}", fill="white", font=font_big)
    draw.text((60, 250), "Business Class", fill="white", font=font_med)
    draw.text((60, 310), "au prix d un vol Economy", fill="#d4ae4a", font=font_med)
    draw.text((60, 390), f"{price} EUR", fill="white", font=font_big)
    txt = f"ECONOMIE DE {pct} POURCENT"
    bbox = draw.textbbox((0, 0), txt, font=font_sm)
    tw = bbox[2] - bbox[0]
    draw.rectangle([60, 505, 60 + tw + 30, 570], fill="#c0392b")
    draw.text((75, 518), txt, fill="white", font=font_sm)
    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/jpeg")

@app.get("/share/{offer_id}")
def share_deal(offer_id: str):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM deals WHERE offer_id = %s LIMIT 1", (offer_id,))
    deal = cur.fetchone()
    cur.close(); conn.close()
    if not deal:
        return Response(content="Not found", status_code=404)
    NAMES = {"JFK":"New York","LAX":"Los Angeles","SIN":"Singapour","NRT":"Tokyo","DXB":"Dubai","HKG":"Hong Kong","BKK":"Bangkok","GRU":"Sao Paulo","LHR":"Londres","AMS":"Amsterdam","FRA":"Francfort","MAD":"Madrid","SYD":"Sydney","DOH":"Doha","ICN":"Seoul","DEL":"Delhi","BOM":"Mumbai","CDG":"Paris"}
    AVG = {"JFK":4700,"LAX":5200,"SIN":5800,"NRT":5500,"DXB":3800,"HKG":5600,"BKK":5000,"GRU":5200}
    price = int(deal["price"])
    avg = AVG.get(deal["destination"], 4500)
    pct = round((1 - deal["price"]/avg)*100)
    dest_name = NAMES.get(deal["destination"], deal["destination"])
    from_name = NAMES.get(deal["origin"], deal["origin"])
    og_img = f"https://airbizness.com/api/og-image?from_={deal['origin']}&to={deal['destination']}&price={price}&pct={pct}"
    og_title = f"Business Class {from_name} vers {dest_name} a {price} EUR - -{pct}% | AirBizness"
    og_desc = f"Volez en Business Class {deal['origin']} vers {dest_name} a seulement {price} EUR au lieu de {avg} EUR. Economisez {avg-price} EUR !"
    vol_url = f"https://airbizness.com/?deal={offer_id}"
    html = f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<meta property="og:type" content="website">
<meta property="og:url" content="https://airbizness.com/share/{offer_id}">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:image" content="{og_img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_img}">
<meta http-equiv="refresh" content="0;url={vol_url}">
<script>window.location.href="{vol_url}";</script>
</head><body><a href="{vol_url}">Voir le deal</a></body></html>"""
    return Response(content=html, media_type="text/html")



# ─────────────────────────────────────────────────────────────────────
# HOTELS (2026-05-22) — Hotellook curated + affiliation
# ─────────────────────────────────────────────────────────────────────

@app.get("/hotels")
@limiter.limit("60/minute")
def get_hotels(request: Request,
    destination: str = None,
    check_in: str = None,
    check_out: str = None,
    guests: int = 2,
    stars_min: int = 4,
    budget_max: float = None,
    limit: int = 30,
):
    """Recherche hôtels signature curated AirBizness.

    Pas un comparateur. Sélection éditoriale par destination, avec deeplink
    affiliation Hotellook pour la disponibilité/réservation réelle.
    """
    if not destination or not check_in or not check_out:
        return {"hotels": [], "destinations": _hotels_destinations_list()}

    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")
    from providers.base import HotelQuery
    from providers.registry import search_hotels

    q = HotelQuery(
        destination=destination, check_in=check_in, check_out=check_out,
        guests=guests, stars_min=stars_min, budget_max=budget_max,
    )
    offers = search_hotels(q)
    out = []
    for o in offers[:limit]:
        out.append({
            "provider": o.provider,
            "source": "affiliate" if o.is_affiliate else "ota",
            "offer_id": o.provider_offer_id,
            "name": o.title,
            "price_from": o.price,
            "currency": o.currency,
            "stars": (o.details or {}).get("stars"),
            "district": (o.details or {}).get("district"),
            "tag": (o.details or {}).get("tag"),
            "description": (o.details or {}).get("description"),
            "city": (o.details or {}).get("city"),
            "deeplink_url": o.deeplink_url,
        })
    return {"hotels": out, "count": len(out),
            "query": {"destination": destination, "check_in": check_in,
                       "check_out": check_out, "guests": guests}}


@app.get("/hotels/destinations")
def hotels_destinations():
    """Liste des destinations couvertes par notre curation."""
    return {"destinations": _hotels_destinations_list()}







def _hotels_destinations_list():
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hotellook import list_destinations
        return list_destinations()
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────
# TUNNEL CHECKOUT NATIVE (2026-05-23) — checkrate + Stripe + booking HBX
# Flow :
#   1. POST /hbx/booking/checkrate     → re-vérifie le tarif HBX (anti-bait-switch)
#   2. POST /hbx/booking/payment-intent → crée Stripe PaymentIntent
#   3. POST /hbx/booking/confirm       → après 3DS validé, crée booking HBX + INSERT DB
# Le visiteur reste sur airbizness.com pendant tout le tunnel.
# ─────────────────────────────────────────────────────────────────────

import uuid as _uuid


class CheckrateRequest(BaseModel):
    rate_key: str
    hotel_code: int
    check_in: str
    check_out: str
    adults: int = 2


class PaymentIntentRequest2(BaseModel):
    rate_key_verified: str
    gross_price: float
    currency: str = "EUR"
    user_email: EmailStr
    holder_name: str
    holder_surname: str
    user_phone: str = ""
    hotel_code: int
    hotel_name: str = ""
    destination_code: str = ""
    check_in: str
    check_out: str
    adults: int = 2
    rooms_count: int = 1
    remark: str = ""
    # Options hôtel seul (Pascal : "option bagage transfert assurance late chekin")
    # On garde late_checkin + special_requests + insurance + transfert
    # (pas de bagages ni modification billet — pas de vol dans ce parcours)
    late_checkin: Optional[bool] = False
    special_requests: Optional[str] = None
    insurance: Optional[bool] = False
    transfer: Optional[str] = None  # "none" | "oneway" | "roundtrip" (legacy)
    # Transfer HBX (remplace progressivement 'transfer' radio) — Pascal 2026-05-24
    transfer_rate_key: Optional[str] = None
    transfer_price: Optional[float] = 0.0
    transfer_label: Optional[str] = None
    transfer_meta: Optional[dict] = None
    # On behalf of (concierge hôtel) — Pascal 2026-05-24
    on_behalf_of: Optional[str] = None   # hotel_code (texte)
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_ref: Optional[str] = None      # n° de chambre / réf interne


# Tarifs options hôtel — source de vérité serveur (front lit ces mêmes valeurs)
HOTEL_OPTION_PRICES = {
    "late_checkin": 25,        # flat
    "insurance_per_pax": 35,   # par voyageur
    "transfer_oneway": 35,     # flat
    "transfer_roundtrip": 60,  # flat
}


def _compute_hotel_options_total(*, late_checkin: bool, insurance: bool,
                                 transfer: Optional[str], adults: int) -> int:
    """Calcule le supplément options en euros (entier). Source unique côté serveur."""
    total = 0
    if late_checkin:
        total += HOTEL_OPTION_PRICES["late_checkin"]
    if insurance:
        total += HOTEL_OPTION_PRICES["insurance_per_pax"] * max(1, int(adults or 1))
    if transfer == "oneway":
        total += HOTEL_OPTION_PRICES["transfer_oneway"]
    elif transfer == "roundtrip":
        total += HOTEL_OPTION_PRICES["transfer_roundtrip"]
    return total


class BookingConfirmRequest(BaseModel):
    airbizness_ref: str
    payment_intent_id: str


def _send_brevo_booking_confirmation(booking_row, hbx_booking):
    """Envoie l'email de confirmation Brevo (template natif AirBizness)."""
    if not BREVO_KEY:
        return
    cfg = sib_api_v3_sdk.Configuration()
    cfg.api_key["api-key"] = BREVO_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))
    nights = (booking_row["check_out"] - booking_row["check_in"]).days

    html = f"""
    <html><body style="font-family:Arial,sans-serif; color:#333; max-width:600px; margin:0 auto; padding:20px;">
      <h1 style="font-family:Georgia,serif; color:#b8962e; border-bottom:1px solid #ddd; padding-bottom:14px;">
        Confirmation AirBizness
      </h1>
      <p>Bonjour {booking_row['holder_name']},</p>
      <p>Votre réservation est confirmée.</p>
      <table style="width:100%; border-collapse:collapse; margin:24px 0;">
        <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Référence</td><td style="padding:8px; border-bottom:1px solid #eee; font-weight:bold;">{booking_row['airbizness_ref']}</td></tr>
        <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Hôtel</td><td style="padding:8px; border-bottom:1px solid #eee; font-weight:bold;">{hbx_booking['hotel_name']}</td></tr>
        <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Arrivée</td><td style="padding:8px; border-bottom:1px solid #eee;">{hbx_booking['check_in']}</td></tr>
        <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Départ</td><td style="padding:8px; border-bottom:1px solid #eee;">{hbx_booking['check_out']}</td></tr>
        <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Nuits</td><td style="padding:8px; border-bottom:1px solid #eee;">{nights}</td></tr>
        <tr><td style="padding:8px; color:#666;">Total payé</td><td style="padding:8px; font-weight:bold; color:#b8962e;">{booking_row['gross_price']}€</td></tr>
      </table>

      <div style="text-align:center;margin:24px 0;">
        <a href="https://airbizness.com/api/booking/{booking_row['airbizness_ref']}/voucher.pdf"
           style="display:inline-block;background:#b8962e;color:#000;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:600;">
          📄 Télécharger mon voucher PDF
        </a>
        <p style="color:#999;font-size:11px;margin-top:8px;">À présenter à l'arrivée de l'hôtel</p>
      </div>

      <p style="color:#666; font-size:13px;">Notre équipe est disponible pour toute question. Bon séjour.</p>
      <hr style="border:none; border-top:1px solid #ddd; margin:30px 0;">
      <p style="color:#999; font-size:11px; text-align:center;">© AirBizness — Maison de voyage</p>
    </body></html>
    """
    msg = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": booking_row["user_email"],
             "name": f"{booking_row['holder_name']} {booking_row['holder_surname']}"}],
        sender={"name": "AirBizness", "email": "no-reply@airbizness.com"},
        subject=f"Confirmation AirBizness — {hbx_booking['hotel_name']}",
        html_content=html,
    )
    api.send_transac_email(msg)


class BookingByEmailRequest(BaseModel):
    email: EmailStr




# ─── Annulation (cancel HBX + refund Stripe) ──────────────────────

class BookingCancelRequest(BaseModel):
    airbizness_ref: str
    email_confirm: EmailStr      # vérif identité minimal (email = celui de la résa)
    simulate: bool = False        # si True, on appelle HBX en mode SIMULATION (no-op)


@app.post("/hbx/booking/cancel")
@limiter.limit("10/minute")
def hbx_cancel_booking_endpoint(request: Request, body: BookingCancelRequest):
    """Annule une réservation : 1) HBX cancel  2) Stripe refund  3) UPDATE DB.

    Idempotent : si déjà cancelled, retourne le statut sans rien faire.
    """
    # 1. Récupère booking
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM bookings_v2 WHERE airbizness_ref = %s", (body.airbizness_ref,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(404, "Booking not found")

    # Vérif email (auth basique)
    if (row["user_email"] or "").lower() != body.email_confirm.lower():
        cur.close(); conn.close()
        raise HTTPException(403, "Email ne correspond pas")

    # Idempotent
    if row["status"] == "cancelled":
        cur.close(); conn.close()
        return {"airbizness_ref": body.airbizness_ref, "status": "cancelled",
                "idempotent": True, "refunded": True}

    # 2. Cancel HBX (si réservation HBX confirmée)
    hbx_cancel_result = None
    if row["hbx_reference"]:
        try:
            import sys as _sys
            if "/var/www/airbizness" not in _sys.path:
                _sys.path.insert(0, "/var/www/airbizness")
            from providers.hbx.hotels.booking import cancel_booking
            flag = "SIMULATION" if body.simulate else "CANCELLATION"
            hbx_cancel_result = cancel_booking(row["hbx_reference"], flag=flag)
        except Exception as e:
            cur.close(); conn.close()
            raise HTTPException(500, f"HBX cancel failed: {e}")

    # 3. Refund Stripe (si payment_intent existe et payment_status=succeeded)
    refund_id = None
    if row["payment_intent_id"] and row["payment_status"] == "succeeded":
        try:
            # Crée un refund full
            refund = stripe.Refund.create(
                payment_intent=row["payment_intent_id"],
                metadata={"airbizness_ref": body.airbizness_ref, "reason": "user_cancellation"},
            )
            refund_id = refund.id
        except Exception as e:
            # Pas bloquant : HBX déjà cancelled, on note l'erreur, Pascal corrige manuellement
            print(f"[cancel] Stripe refund fail: {e}")

    # 4. UPDATE bookings_v2 (persiste aussi le refund_id pour traçabilité)
    refund_amount = None
    if refund_id:
        try:
            r_obj = stripe.Refund.retrieve(refund_id)
            refund_amount = (r_obj.amount or 0) / 100.0
        except Exception:
            refund_amount = None
    try:
        with conn, conn.cursor() as cur2:
            cur2.execute("""
                UPDATE bookings_v2 SET status='cancelled', cancelled_at=NOW(),
                       refund_id=%s, refund_amount=%s,
                       refund_at = CASE WHEN %s IS NOT NULL THEN NOW() ELSE NULL END
                WHERE airbizness_ref = %s
            """, (refund_id, refund_amount, refund_id, body.airbizness_ref))
    finally:
        try: conn.close()
        except: pass

    return {
        "airbizness_ref": body.airbizness_ref,
        "status": "cancelled",
        "hbx_cancelled": bool(row["hbx_reference"]),
        "stripe_refunded": bool(refund_id),
        "refund_id": refund_id,
        "simulate": body.simulate,
    }


# CLAIM HÔTELS + HOTEL-MANAGER — déplacés dans routers/hotelier.py (2026-06-03)





def _pack_db_conn():
    return psycopg2.connect(**DB_CONFIG)











# ──────────────────────────────────────────────────────────────────────────
# MODULE RESILIENCE — routes déplacées dans routers/resilience.py (2026-06-03)
# ──────────────────────────────────────────────────────────────────────────












def _send_pack_confirmation_email(airbizness_ref: str):
    """Envoie l'email Brevo pour un pack confirmé."""
    if not BREVO_KEY:
        return
    with _pack_db_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM pack_bookings WHERE airbizness_ref=%s", (airbizness_ref,))
        row = cur.fetchone()
    if not row:
        return
    nights = (row["hotel_check_out"] - row["hotel_check_in"]).days

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
      <h1 style="font-family:Georgia,serif;color:#b8962e;border-bottom:1px solid #ddd;padding-bottom:14px;">
        Votre séjour est confirmé
      </h1>
      <p>Bonjour {row['holder_name']},</p>
      <p>Merci pour votre réservation chez AirBizness. Votre <strong>séjour pack</strong> est confirmé.</p>

      <h3 style="margin-top:28px;color:#b8962e;">Référence : {row['airbizness_ref']}</h3>

      <h3 style="margin-top:28px;">🏨 Hôtel</h3>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Établissement</td><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">{row['hotel_name']}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Référence hôtel</td><td style="padding:8px;border-bottom:1px solid #eee;">{row.get('hbx_reference','—')}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Arrivée → Départ</td><td style="padding:8px;border-bottom:1px solid #eee;">{row['hotel_check_in']} → {row['hotel_check_out']} ({nights} nuit{'s' if nights>1 else ''})</td></tr>
      </table>

      <h3 style="margin-top:28px;">✈️ Vol</h3>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Trajet</td><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">{row['flight_origin']} → {row['flight_destination']}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Départ</td><td style="padding:8px;border-bottom:1px solid #eee;">{row['flight_departure_date']}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Cabine</td><td style="padding:8px;border-bottom:1px solid #eee;">{row.get('flight_cabin','Business')}</td></tr>
      </table>
      <div style="background:#fff8e1;border:1px solid #f5d36b;border-radius:8px;padding:14px;margin-top:14px;font-size:13px;">
        <strong>Étape suivante pour votre vol :</strong> finalisez l'émission de votre billet via notre partenaire.<br>
        <a href="{row.get('flight_deeplink','')}" style="color:#b8962e;font-weight:600;">Finaliser le vol →</a>
      </div>

      <div style="margin-top:24px;padding-top:20px;border-top:2px solid #b8962e;display:flex;justify-content:space-between;">
        <span style="color:#666;">Total payé</span>
        <span style="font-family:Georgia,serif;font-size:22px;color:#b8962e;font-weight:bold;">{row['total_amount']}€</span>
      </div>

      <div style="text-align:center;margin:24px 0;">
        <a href="https://airbizness.com/api/pack/{row['airbizness_ref']}/voucher.pdf"
           style="display:inline-block;background:#b8962e;color:#000;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:600;">
          📄 Télécharger mon voucher PDF
        </a>
      </div>
      <p style="color:#666;font-size:12px;margin-top:28px;">Notre équipe reste disponible : <a href="mailto:hello@airbizness.com">hello@airbizness.com</a></p>
      <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
      <p style="color:#999;font-size:11px;text-align:center;">© AirBizness — Marketplace de voyage</p>
    </body></html>
    """
    # P0 fix D : préférer template Brevo si configuré (BREVO_TEMPLATE_PACK_CONFIRMATION)
    tpl_id = int(os.getenv("BREVO_TEMPLATE_PACK_CONFIRMATION", "0") or 0)
    params = {
        "airbizness_ref": row["airbizness_ref"],
        "holder_name": row["holder_name"],
        "hotel_name": row.get("hotel_name") or "",
        "hbx_ref": row.get("hbx_reference") or "",
        "pnr": row.get("duffel_pnr") or "",
        "check_in": str(row.get("hotel_check_in") or ""),
        "check_out": str(row.get("hotel_check_out") or ""),
        "nights": nights,
        "flight_origin": row.get("flight_origin") or "",
        "flight_destination": row.get("flight_destination") or "",
        "flight_departure_date": str(row.get("flight_departure_date") or ""),
        "flight_cabin": row.get("flight_cabin") or "Business",
        "total_amount": float(row.get("total_amount") or 0),
        "currency": row.get("currency") or "EUR",
    }
    _brevo_send_template_or_html(
        to_email=row["user_email"],
        to_name=f"{row['holder_name']} {row.get('holder_surname','')}".strip(),
        subject=f"Séjour confirmé — {row['flight_origin']} → {row['flight_destination']}",
        html_content=html,
        template_id=tpl_id,
        params=params,
    )






# ─────────────────────────────────────────────────────────────────────
# WEBHOOKS — Stripe events (2026-05-23)
# Endpoint pour Stripe : `https://airbizness.com/api/stripe-webhook`
# Configurer sur Dashboard Stripe → Webhooks → Add endpoint
# Events à écouter : payment_intent.succeeded, payment_intent.payment_failed,
#                    charge.refunded, charge.dispute.created
# ─────────────────────────────────────────────────────────────────────

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Audit 2026-05-27 sev 4 #52 : Stripe capture_method='manual' (flag bascule).
# Avec capture_method='manual', Stripe AUTORISE seulement (pas de débit) → on
# tente Duffel → succès = stripe.PaymentIntent.capture (débit réel), échec =
# stripe.PaymentIntent.cancel (autorisation annulée, 0 frais Stripe au lieu
# de 6% sur refund). À activer via STRIPE_CAPTURE_MANUAL=true dans .env.
STRIPE_CAPTURE_MANUAL = os.getenv("STRIPE_CAPTURE_MANUAL", "false").lower() == "true"


def _stripe_capture_intent(intent_id: str, ab_ref: str = "") -> dict:
    """Audit 2026-05-27 sev 4 #52 : capture une PaymentIntent (manual mode).

    Si capture_method='automatic' (mode legacy), cette fonction est idempotent
    no-op : Stripe renvoie le PI déjà succeeded. En mode manual, c'est ici
    qu'on déclenche le vrai débit côté banque, APRÈS confirmation Duffel.
    """
    try:
        pi = stripe.PaymentIntent.capture(intent_id)
        print(f"[stripe-capture] OK pi={intent_id} ab_ref={ab_ref} status={pi.status}")
        return {"ok": True, "status": pi.status, "amount": pi.amount}
    except stripe.error.InvalidRequestError as e:
        # Idempotent : déjà capturé / déjà succeeded → on retourne OK
        msg = str(e).lower()
        if "already" in msg or "succeeded" in msg or "succeed" in msg:
            print(f"[stripe-capture] idempotent (déjà capturé) pi={intent_id} ab_ref={ab_ref}")
            return {"ok": True, "idempotent": True}
        print(f"[stripe-capture] FAIL pi={intent_id} ab_ref={ab_ref}: {e}")
        return {"ok": False, "error": str(e)[:300]}
    except Exception as e:
        print(f"[stripe-capture] FAIL pi={intent_id} ab_ref={ab_ref}: {e}")
        return {"ok": False, "error": str(e)[:300]}


def _stripe_cancel_intent(intent_id: str, ab_ref: str = "",
                           reason: str = "duffel_booking_failed") -> dict:
    """Audit 2026-05-27 sev 4 #52 : annule une autorisation Stripe (manual mode).

    Préférable au refund car aucun frais Stripe : l'autorisation libère le
    montant côté banque client sans aller-retour débit/crédit visible.
    """
    try:
        pi = stripe.PaymentIntent.cancel(intent_id,
                                          cancellation_reason="requested_by_customer")
        print(f"[stripe-cancel] OK pi={intent_id} ab_ref={ab_ref} reason={reason}")
        return {"ok": True, "status": pi.status}
    except stripe.error.InvalidRequestError as e:
        msg = str(e).lower()
        # Si déjà capturé/succeeded → on bascule sur refund Stripe
        if "succeeded" in msg or "captured" in msg:
            print(f"[stripe-cancel] PI déjà capturé pi={intent_id} → fallback refund")
            try:
                return _stripe_refund_auto(intent_id, airbizness_ref=ab_ref,
                                            reason=reason, error_excerpt=str(e)[:200])
            except Exception as _e_r:
                return {"ok": False, "error": f"cancel KO ET refund KO: {_e_r}"}
        # Idempotent : déjà cancelled → OK
        if "canceled" in msg or "cancelled" in msg:
            print(f"[stripe-cancel] idempotent (déjà cancelled) pi={intent_id}")
            return {"ok": True, "idempotent": True}
        print(f"[stripe-cancel] FAIL pi={intent_id}: {e}")
        return {"ok": False, "error": str(e)[:300]}
    except Exception as e:
        print(f"[stripe-cancel] FAIL pi={intent_id}: {e}")
        return {"ok": False, "error": str(e)[:300]}


def _stripe_event_seen(event_id: str, event_type: str = "") -> bool:
    """Audit 2026-05-27 sev 3 webhook idempotency : dedup Stripe events.

    Retourne True si déjà vu (skip), False si nouveau. Crée la table à la volée.
    Best-effort : si DB down, retourne False (mieux traiter en double que pas du tout).
    """
    if not event_id:
        return False
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                      event_id TEXT PRIMARY KEY,
                      event_type TEXT,
                      received_at TIMESTAMPTZ DEFAULT NOW(),
                      processed_at TIMESTAMPTZ
                    )
                """)
                cur.execute("""
                    INSERT INTO stripe_webhook_events (event_id, event_type)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                """, (event_id, event_type[:64]))
                row = cur.fetchone()
                return row is None  # None = conflict = déjà vu
        finally:
            conn.close()
    except Exception as e:
        print(f"[stripe-dedup] ERR event_id={event_id}: {e}")
        return False


def _stripe_event_mark_processed(event_id: str) -> None:
    """Marque l'event comme traité (audit trail)."""
    if not event_id:
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE stripe_webhook_events SET processed_at=NOW()
                    WHERE event_id=%s
                """, (event_id,))
        finally:
            conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# Watchdog helpers (Telegram + Brevo) pour flight booking post-paiement
# Doctrine Pascal : tout pipeline qui peut foirer en silence doit aboyer.
# ─────────────────────────────────────────────────────────────────────
def _alert_telegram(message: str) -> None:
    """Envoie une alerte Telegram best-effort. No-op si non configuré.
    Configurer TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID dans .env pour activer.

    Verbosité (env CHATBOT_TELEGRAM_VERBOSITY, défaut 'normal'):
      - 'normal'   → tout passe.
      - 'critique' → ne laisse passer que EXECUTED / FAILED / FRAUD (mode fuzz).
      - 'silent'   → tout est swallowed (log only).
    """
    verbosity = (os.getenv("CHATBOT_TELEGRAM_VERBOSITY", "normal") or "normal").lower().strip()
    if verbosity == "silent":
        print(f"[telegram-alert SILENT mode] {message}")
        return
    if verbosity == "critique":
        m = (message or "").upper()
        keep_markers = ("EXECUTED", "FAILED", "FRAUD",
                         "🎯", "🔴", "🚨")
        if not any(k in m or k in message for k in keep_markers):
            print(f"[telegram-alert FILTERED critique] {message}")
            return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(f"[telegram-alert SILENT] {message}")
        return
    try:
        import urllib.request as _ur
        import urllib.parse as _up
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = _up.urlencode({
            "chat_id": chat_id,
            "text": f"[AirBizness] {message}",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = _ur.Request(url, data=body, method="POST")
        with _ur.urlopen(req, timeout=5) as r:
            r.read()
    except Exception as e:
        print(f"[telegram-alert] fail: {e} | msg={message}")


def _send_flight_booking_confirmation(airbizness_ref: str, duffel_order: dict) -> None:
    """Envoie l'email Brevo de confirmation vol après création Duffel order.

    Joint le lien e-ticket si dispo dans `documents`. Best-effort : ne lève pas.
    """
    if not BREVO_KEY:
        print(f"[flight-mail] BREVO_KEY absent, skip ab_ref={airbizness_ref}")
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, user_email, origin, destination,
                   airline_name, airline_code, departure_at, cabin_class,
                   total_eur, currency, passengers, pnr, booking_reference,
                   duffel_order_id
            FROM flight_bookings WHERE airbizness_ref = %s
        """, (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row or not row.get("user_email"):
            print(f"[flight-mail] booking introuvable ou email manquant ab_ref={airbizness_ref}")
            return

        booking_ref = (duffel_order or {}).get("booking_reference") or row.get("booking_reference") or row.get("pnr") or "—"
        pax_list = row.get("passengers") or []
        if isinstance(pax_list, str):
            try:
                pax_list = json.loads(pax_list)
            except Exception:
                pax_list = []
        pax_names = ", ".join(
            f"{(p.get('given_name') or p.get('firstName') or '').strip()} "
            f"{(p.get('family_name') or p.get('lastName') or '').strip()}".strip()
            for p in pax_list if isinstance(p, dict)
        ) or row.get("user_email") or ""

        dep_at = row.get("departure_at")
        dep_str = dep_at.strftime("%d/%m/%Y %H:%M") if hasattr(dep_at, "strftime") else (str(dep_at) if dep_at else "")

        documents = (duffel_order or {}).get("documents") or []
        eticket_html = ""
        for doc in documents:
            if doc.get("type") == "electronic_ticket":
                tnum = doc.get("unique_identifier") or doc.get("id") or ""
                eticket_html += f'<li>E-ticket {doc.get("passenger_id","")} : <b>{tnum}</b></li>'

        cfg = sib_api_v3_sdk.Configuration()
        cfg.api_key["api-key"] = BREVO_KEY
        api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))
        html = f"""
        <html><body style="font-family:Arial,sans-serif; color:#333; max-width:600px; margin:0 auto; padding:20px;">
          <h1 style="font-family:Georgia,serif; color:#b8962e; border-bottom:1px solid #ddd; padding-bottom:14px;">
            Votre billet d'avion AirBizness
          </h1>
          <p>Bonjour,</p>
          <p>Votre billet est émis. Voici votre référence de réservation (PNR) :</p>
          <p style="font-size:22px; font-weight:bold; color:#b8962e; letter-spacing:2px;">{booking_ref}</p>
          <table style="width:100%; border-collapse:collapse; margin:24px 0;">
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Référence AirBizness</td>
                <td style="padding:8px; border-bottom:1px solid #eee; font-weight:bold;">{row.get('airbizness_ref','')}</td></tr>
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Trajet</td>
                <td style="padding:8px; border-bottom:1px solid #eee;">{row.get('origin','')} → {row.get('destination','')}</td></tr>
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Compagnie</td>
                <td style="padding:8px; border-bottom:1px solid #eee;">{row.get('airline_name','')} ({row.get('airline_code','')})</td></tr>
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Départ</td>
                <td style="padding:8px; border-bottom:1px solid #eee;">{dep_str}</td></tr>
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Classe</td>
                <td style="padding:8px; border-bottom:1px solid #eee;">{row.get('cabin_class','economy')}</td></tr>
            <tr><td style="padding:8px; border-bottom:1px solid #eee; color:#666;">Passager(s)</td>
                <td style="padding:8px; border-bottom:1px solid #eee;">{pax_names}</td></tr>
            <tr><td style="padding:8px; color:#666;">Total payé</td>
                <td style="padding:8px; font-weight:bold; color:#b8962e;">{float(row.get('total_eur') or 0):.2f} {row.get('currency','EUR')}</td></tr>
          </table>
          {"<h3>Vos e-tickets</h3><ul>"+eticket_html+"</ul>" if eticket_html else ""}
          <p style="color:#666; font-size:13px;">Présentez votre PNR à l'aéroport ou utilisez le check-in en ligne de la compagnie. Pour toute question, écrivez à contact@airbizness.com.</p>
          <hr style="border:none; border-top:1px solid #ddd; margin:30px 0;">
          <p style="color:#999; font-size:11px; text-align:center;">© AirBizness — Maison de voyage</p>
        </body></html>
        """
        # P0 fix D : préférer template Brevo si configuré (BREVO_TEMPLATE_FLIGHT_CONFIRMATION)
        tpl_id = int(os.getenv("BREVO_TEMPLATE_FLIGHT_CONFIRMATION", "0") or 0)
        if tpl_id and tpl_id > 0:
            eticket_url = ""
            for doc in documents:
                if doc.get("type") == "electronic_ticket":
                    eticket_url = doc.get("unique_identifier") or doc.get("id") or ""
                    break
            tpl_params = {
                "passenger_name": pax_names,
                "pnr": booking_ref,
                "airbizness_ref": row.get("airbizness_ref",""),
                "origin": row.get("origin",""),
                "destination": row.get("destination",""),
                "departure_at": dep_str,
                "airline": f"{row.get('airline_name','')} ({row.get('airline_code','')})",
                "cabin_class": row.get("cabin_class","economy"),
                "eticket_url": eticket_url,
                "total_amount": float(row.get("total_eur") or 0),
                "currency": row.get("currency","EUR"),
            }
            msg = sib_api_v3_sdk.SendSmtpEmail(
                to=[{"email": row["user_email"]}],
                template_id=tpl_id,
                params=tpl_params,
            )
        else:
            msg = sib_api_v3_sdk.SendSmtpEmail(
                to=[{"email": row["user_email"]}],
                sender={"name": "AirBizness", "email": "no-reply@airbizness.com"},
                subject=f"Votre billet d'avion AirBizness — PNR {booking_ref}",
                html_content=html,
            )
        api.send_transac_email(msg)
        print(f"[flight-mail] sent to={row['user_email']} pnr={booking_ref} via_template={bool(tpl_id)}")
    except Exception as e:
        print(f"[flight-mail] fail ab_ref={airbizness_ref}: {e}")
        try:
            _alert_telegram(f"📧 Mail confirmation FAIL {airbizness_ref}: {e}")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# P0 mission 2026-05-26 — helpers refund + mails fail/hotel/activity
# Doctrine Pascal : aucun paiement client ne doit jamais rester sans
# contrepartie (booking) ni notification. Tout fail → refund auto +
# mail client "Désolés, remboursé" + alert Telegram.
# ─────────────────────────────────────────────────────────────────────
def _brevo_send_template_or_html(
    *,
    to_email: str,
    to_name: str,
    subject: str,
    html_content: str,
    template_id: int = 0,
    params: dict = None,
) -> bool:
    """Envoie un mail Brevo. Préfère template_id si configuré, sinon HTML inline.
    Renvoie True si envoi OK, False sinon. Ne lève jamais."""
    if not BREVO_KEY:
        print(f"[brevo-mail] BREVO_KEY absent, skip to={to_email}")
        return False
    try:
        cfg = sib_api_v3_sdk.Configuration()
        cfg.api_key["api-key"] = BREVO_KEY
        api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))
        # Brevo refuse un "name" vide ({"code":"missing_parameter"}) → on ne le met
        # que s'il est renseigné.
        to_entry = {"email": to_email}
        if to_name:
            to_entry["name"] = to_name
        if template_id and template_id > 0:
            msg = sib_api_v3_sdk.SendSmtpEmail(
                to=[to_entry],
                template_id=int(template_id),
                params=params or {},
            )
        else:
            msg = sib_api_v3_sdk.SendSmtpEmail(
                to=[to_entry],
                # Expéditeur : doit être un sender VALIDÉ dans Brevo. Seul
                # pascal.repir@gmail.com l'est aujourd'hui → défaut. Override via
                # BREVO_SENDER_EMAIL quand no-reply@airbizness.com sera validé.
                sender={"name": "AirBizness",
                        "email": os.getenv("BREVO_SENDER_EMAIL", "pascal.repir@gmail.com")},
                subject=subject,
                html_content=html_content,
            )
        api.send_transac_email(msg)
        return True
    except Exception as e:
        print(f"[brevo-mail] fail to={to_email}: {e}")
        return False


def _send_booking_failed_mail(
    airbizness_ref: str,
    *,
    to_email: str,
    to_name: str = "",
    refund_amount: float = 0.0,
    original_amount: float = 0.0,
    reason: str = "indisponible",
) -> None:
    """Mail au client : 'Désolés, votre réservation a échoué, vous êtes remboursé'.
    Reason ∈ {'vol_indisponible','hotel_indisponible','activite_indisponible','pack_failed'}."""
    if not to_email:
        return
    tpl_id = int(os.getenv("BREVO_TEMPLATE_BOOKING_FAILED", "0") or 0)
    reason_label = {
        "vol_indisponible": "votre vol est devenu indisponible",
        "hotel_indisponible": "votre hôtel est devenu indisponible",
        "activite_indisponible": "votre activité est devenue indisponible",
        "pack_failed": "votre voyage n'a pas pu être confirmé",
    }.get(reason, "votre réservation n'a pas pu être confirmée")

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
      <h1 style="font-family:Georgia,serif;color:#b8962e;border-bottom:1px solid #ddd;padding-bottom:14px;">
        Annulation et remboursement — AirBizness
      </h1>
      <p>Bonjour{(' ' + to_name) if to_name else ''},</p>
      <p>Nous sommes désolés de vous l'annoncer : <strong>{reason_label}</strong> entre votre paiement
         et la confirmation finale auprès de notre partenaire.</p>
      <p>Votre carte a été <strong>remboursée automatiquement</strong> pour un montant de
         <strong style="color:#b8962e;">{refund_amount:.2f}€</strong>
         {f'(montant initial : {original_amount:.2f}€)' if original_amount and original_amount != refund_amount else ''}.
         Le crédit apparaîtra sous 5 à 10 jours ouvrés selon votre banque.</p>
      <p>Référence dossier : <strong>{airbizness_ref}</strong></p>
      <p>Vous pouvez bien sûr <a href="https://airbizness.com" style="color:#b8962e;">rechercher à nouveau</a>.</p>
      <p>Pour toute question : <a href="mailto:hello@airbizness.com">hello@airbizness.com</a></p>
      <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
      <p style="color:#999;font-size:11px;text-align:center;">© AirBizness — Maison de voyage</p>
    </body></html>
    """
    params = {
        "airbizness_ref": airbizness_ref,
        "holder_name": to_name,
        "reason": reason,
        "reason_label": reason_label,
        "refund_amount": f"{refund_amount:.2f}",
        "original_amount": f"{original_amount:.2f}",
    }
    ok = _brevo_send_template_or_html(
        to_email=to_email, to_name=to_name,
        subject=f"AirBizness — Annulation et remboursement ({airbizness_ref})",
        html_content=html,
        template_id=tpl_id,
        params=params,
    )
    print(f"[fail-mail] ab_ref={airbizness_ref} to={to_email} sent={ok}")


def _stripe_refund_auto(
    payment_intent_id: str,
    *,
    airbizness_ref: str,
    reason: str,
    amount_cents: int = None,
    error_excerpt: str = "",
) -> dict:
    """Tente un Stripe refund. Retourne {'ok': bool, 'refund_id': str|None, 'amount': float|None, 'error': str|None}."""
    try:
        kwargs = {
            "payment_intent": payment_intent_id,
            "reason": "requested_by_customer",
            "metadata": {
                "airbizness_ref": airbizness_ref,
                "reason": reason,
                "error": (error_excerpt or "")[:480],
            },
        }
        if amount_cents and amount_cents > 0:
            kwargs["amount"] = amount_cents
        r = stripe.Refund.create(**kwargs)
        amount_eur = (r.amount or 0) / 100.0
        return {"ok": True, "refund_id": r.id, "amount": amount_eur, "error": None}
    except Exception as e:
        print(f"[stripe-refund] FAIL pi={payment_intent_id} reason={reason}: {e}")
        return {"ok": False, "refund_id": None, "amount": None, "error": str(e)[:500]}


def _send_hotel_booking_confirmation(airbizness_ref: str, hbx_response: dict) -> None:
    """Mail confirmation hôtel via template Brevo (fallback HTML inline si tpl absent).
    Best-effort, ne lève pas."""
    if not BREVO_KEY:
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bookings_v2 WHERE airbizness_ref=%s", (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row or not row.get("user_email"):
            return
        # Délègue à l'existant pour le HTML inline (compat)
        _send_brevo_booking_confirmation(row, {
            "reference": (hbx_response or {}).get("reference") or row.get("hbx_reference") or "—",
            "hotel_name": (hbx_response or {}).get("hotel_name") or row.get("hotel_name") or "",
            "check_in": (hbx_response or {}).get("check_in") or row.get("check_in"),
            "check_out": (hbx_response or {}).get("check_out") or row.get("check_out"),
        })
    except Exception as e:
        print(f"[hotel-mail] fail ab_ref={airbizness_ref}: {e}")


def _send_activity_booking_confirmation(airbizness_ref: str, hbx_response: dict) -> None:
    """Mail confirmation activité via template Brevo (fallback HTML inline si tpl absent)."""
    if not BREVO_KEY:
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM activity_bookings_v2 WHERE airbizness_ref=%s", (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row or not row.get("user_email"):
            return
        tpl_id = int(os.getenv("BREVO_TEMPLATE_ACTIVITY_CONFIRMATION", "0") or 0)
        hbx_ref = (hbx_response or {}).get("reference") or row.get("hbx_reference") or "—"
        params = {
            "airbizness_ref": airbizness_ref,
            "hbx_ref": hbx_ref,
            "holder_name": row.get("holder_name") or "",
            "activity_name": row.get("activity_name") or "",
            "operation_date": str(row.get("operation_date") or ""),
            "adults": row.get("adults") or 1,
            "children": row.get("children") or 0,
            "total_amount": float(row.get("gross_price") or 0),
            "currency": row.get("currency") or "EUR",
        }
        html = f"""
        <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
          <h1 style="font-family:Georgia,serif;color:#b8962e;border-bottom:1px solid #ddd;padding-bottom:14px;">
            Votre activité AirBizness est confirmée
          </h1>
          <p>Bonjour {row.get('holder_name','')},</p>
          <p>Votre activité <strong>{row.get('activity_name','')}</strong> est confirmée.</p>
          <table style="width:100%;border-collapse:collapse;margin:24px 0;">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Référence AirBizness</td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">{airbizness_ref}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Référence HBX</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{hbx_ref}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Date</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{row.get('operation_date','')}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;color:#666;">Participants</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{row.get('adults',1)} adulte(s){f", {row.get('children')} enfant(s)" if row.get('children') else ''}</td></tr>
            <tr><td style="padding:8px;color:#666;">Total payé</td>
                <td style="padding:8px;font-weight:bold;color:#b8962e;">{float(row.get('gross_price') or 0):.2f} {row.get('currency','EUR')}</td></tr>
          </table>
          <p style="color:#666;font-size:13px;">Pour toute question : <a href="mailto:hello@airbizness.com">hello@airbizness.com</a></p>
          <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
          <p style="color:#999;font-size:11px;text-align:center;">© AirBizness — Maison de voyage</p>
        </body></html>
        """
        _brevo_send_template_or_html(
            to_email=row["user_email"],
            to_name=f"{row.get('holder_name','')} {row.get('holder_surname','')}".strip(),
            subject=f"Votre activité AirBizness — {hbx_ref}",
            html_content=html,
            template_id=tpl_id,
            params=params,
        )
    except Exception as e:
        print(f"[activity-mail] fail ab_ref={airbizness_ref}: {e}")





# ═════════════════════════════════════════════════════════════════════
# SEO PRE-LAUNCH (2026-05-23)
# Pages /h/{slug} SSR + sitemap.xml + robots.txt + lead capture
# ═════════════════════════════════════════════════════════════════════

_HOTEL_FETCH_SQL = """
    SELECT c.*,
           (SELECT main_image_url FROM hbx_hotels_catalog
            WHERE giata_code = c.giata_code LIMIT 1) AS hbx_main_image,
           (SELECT raw FROM hbx_hotels_catalog
            WHERE giata_code = c.giata_code LIMIT 1) AS hbx_raw
    FROM hotels_canonical c
    WHERE c.slug = %s
"""


# Source unique de vérité par hôtel : extraite vers services/hotel_data.py
# le 2026-05-30 (Phase 4 — doctrine modulaire). Le code historique vit là-bas.
from services.hotel_data import get_hotel_unified_data  # noqa: E402,F401










# Alias backward-compat : l'ancien nom continue de marcher.








# ──────────────────────────────────────────────────────────────────────────
# PAGES HUB DESTINATIONS — pillar pages SEO pour MAD/PAR/LON
# ──────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────
# GÉOLOCALISATION : centres-villes + aéroports pour calculs distances
# ──────────────────────────────────────────────────────────────────────────

import math as _math

# Centre géographique officiel de chaque ville couverte
CITY_CENTERS = {
    "PAR":      (48.8566, 2.3522,   "Paris"),
    "PARIS":    (48.8566, 2.3522,   "Paris"),
    "MAD":      (40.4168, -3.7038,  "Madrid"),
    "MADRID":   (40.4168, -3.7038,  "Madrid"),
    "LON":      (51.5074, -0.1278,  "Londres"),
    "LONDON":   (51.5074, -0.1278,  "Londres"),
    "LONDRES":  (51.5074, -0.1278,  "Londres"),
}

# Aéroport principal de chaque ville (pour badges hero compacts)
CITY_AIRPORTS = {
    "PAR":      (49.0097, 2.5479,   "Paris-CDG"),
    "PARIS":    (49.0097, 2.5479,   "Paris-CDG"),
    "MAD":      (40.4719, -3.5626,  "Madrid-Barajas"),
    "MADRID":   (40.4719, -3.5626,  "Madrid-Barajas"),
    "LON":      (51.4700, -0.4543,  "London-Heathrow"),
    "LONDON":   (51.4700, -0.4543,  "London-Heathrow"),
    "LONDRES":  (51.4700, -0.4543,  "London-Heathrow"),
}

# Aéroports secondaires (liste complète pour la section "Lieu") — lat/lng/IATA/nom
CITY_AIRPORTS_ALL = {
    "PAR":   [(49.0097, 2.5479, "CDG", "Paris-Charles de Gaulle"),
              (48.7233, 2.3795, "ORY", "Paris-Orly"),
              (49.4544, 2.1129, "BVA", "Beauvais-Tillé")],
    "MAD":   [(40.4719, -3.5626, "MAD", "Madrid-Barajas"),
              (40.7547, -4.0114, "TLM", "Madrid-Cuatro Vientos")],
    "LON":   [(51.4700, -0.4543, "LHR", "London-Heathrow"),
              (51.1537, -0.1821, "LGW", "London-Gatwick"),
              (51.5053, 0.0553,  "LCY", "London City"),
              (51.8860, 0.2389,  "STN", "London-Stansted")],
}
# Alias pour matcher city_key
CITY_AIRPORTS_ALL["PARIS"] = CITY_AIRPORTS_ALL["PAR"]
CITY_AIRPORTS_ALL["MADRID"] = CITY_AIRPORTS_ALL["MAD"]
CITY_AIRPORTS_ALL["LONDON"] = CITY_AIRPORTS_ALL["LON"]
CITY_AIRPORTS_ALL["LONDRES"] = CITY_AIRPORTS_ALL["LON"]

# Points d'intérêt principaux par ville (statique — sera enrichi via OSM Overpass plus tard)
# Format : (lat, lng, nom, catégorie)
CITY_POI = {
    "PAR": [
        (48.8584, 2.2945,  "Tour Eiffel",            "monument"),
        (48.8606, 2.3376,  "Musée du Louvre",        "musée"),
        (48.8530, 2.3499,  "Notre-Dame",             "monument"),
        (48.8738, 2.2950,  "Arc de Triomphe",        "monument"),
        (48.8867, 2.3431,  "Sacré-Cœur · Montmartre","monument"),
        (48.8462, 2.3463,  "Jardin du Luxembourg",   "parc"),
        (48.8867, 2.3174,  "Opéra Garnier",          "culture"),
        (48.8330, 2.3324,  "Catacombes",             "culture"),
    ],
    "MAD": [
        (40.4153, -3.7074, "Plaza Mayor",            "monument"),
        (40.4169, -3.7035, "Puerta del Sol",         "monument"),
        (40.4138, -3.6921, "Musée du Prado",         "musée"),
        (40.4150, -3.6845, "Parc du Retiro",         "parc"),
        (40.4530, -3.6883, "Stade Santiago Bernabéu","sport"),
        (40.4322, -3.6635, "Las Ventas (arènes)",    "monument"),
        (40.4233, -3.7117, "Gran Vía",               "shopping"),
        (40.4116, -3.6924, "Musée Thyssen",          "musée"),
    ],
    "LON": [
        (51.5014, -0.1419, "Buckingham Palace",      "monument"),
        (51.5007, -0.1246, "Big Ben · Westminster",  "monument"),
        (51.5194, -0.1270, "British Museum",         "musée"),
        (51.5055, -0.0754, "Tower Bridge",           "monument"),
        (51.5033, -0.1196, "London Eye",             "monument"),
        (51.5074, -0.1657, "Hyde Park",              "parc"),
        (51.5012, -0.1419, "Harrods",                "shopping"),
        (51.5081, -0.0759, "Tower of London",        "monument"),
    ],
}
CITY_POI["PARIS"] = CITY_POI["PAR"]
CITY_POI["MADRID"] = CITY_POI["MAD"]
CITY_POI["LONDON"] = CITY_POI["LON"]
CITY_POI["LONDRES"] = CITY_POI["LON"]


def nearby_pois(hotel_lat: float, hotel_lng: float, city_key: str,
                max_count: int = 6, max_km: float = 5.0) -> list:
    """Liste les POI à proximité de l'hôtel triés par distance croissante."""
    pois = CITY_POI.get(city_key)
    if not pois:
        return []
    with_dist = []
    for lat, lng, name, cat in pois:
        d = haversine_km(hotel_lat, hotel_lng, lat, lng)
        if d <= max_km:
            with_dist.append({
                "name": name, "category": cat, "distance_km": round(d, 2),
                "lat": lat, "lng": lng,
            })
    with_dist.sort(key=lambda p: p["distance_km"])
    return with_dist[:max_count]


def airports_nearby(hotel_lat: float, hotel_lng: float, city_key: str) -> list:
    """Tous les aéroports de la ville avec distance."""
    aps = CITY_AIRPORTS_ALL.get(city_key)
    if not aps:
        return []
    out = []
    for lat, lng, iata, name in aps:
        d = haversine_km(hotel_lat, hotel_lng, lat, lng)
        out.append({
            "iata": iata, "name": name, "distance_km": round(d, 1),
            "lat": lat, "lng": lng,
        })
    out.sort(key=lambda a: a["distance_km"])
    return out


# ──────────────────────────────────────────────────────────────────────────
# HELPERS « À PROXIMITÉ » / « À VOIR » — 100% GROUNDED sur raw->interestPoints
# (payload HBX réel). Aucune invention : on n'affiche QUE ce qui est en base.
# ──────────────────────────────────────────────────────────────────────────

def _fmt_poi_distance_m(m) -> str:
    """Distance HBX en MÈTRES (string ou int) → libellé FR lisible.
    <1000 m → '800 m' ; sinon → '5,5 km' (arrondi à 1 décimale, virgule FR)."""
    try:
        meters = int(round(float(m)))
    except (TypeError, ValueError):
        return ""
    if meters <= 0:
        return ""
    if meters < 1000:
        return f"{meters} m"
    km = meters / 1000.0
    if km < 10:
        return f"{km:.1f} km".replace(".0 km", " km").replace(".", ",")
    return f"{int(round(km))} km"


def _clean_poi_name(name: str) -> str:
    """Nettoie un nom de POI HBX (espaces parasites, casse de fin)."""
    return (name or "").strip()


def _poi_dedup_key(name: str) -> str:
    """Clé de dédup tolérante : minuscule, sans accents/ponctuation/espaces.
    Permet de fusionner 'Eiffel Tower' / 'Tour Eiffel ' / 'Louvre Museum'/'Louvre'."""
    import unicodedata, re as _re
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    s = _re.sub(r"[^a-z0-9]+", "", s.lower())
    return s


def hotel_interest_points(raw, max_count: int = 7) -> list:
    """POI réels d'1 hôtel depuis raw->interestPoints (payload HBX).
    Retourne [{name, distance_m, distance_label}] triés par distance croissante,
    dédupliqués par nom. Liste vide si pas de data (→ on n'affiche rien)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, dict):
        return []
    pts = raw.get("interestPoints")
    if not isinstance(pts, list) or not pts:
        return []
    seen = set()
    out = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        name = _clean_poi_name(p.get("poiName"))
        if not name:
            continue
        try:
            dist_m = int(round(float(p.get("distance"))))
        except (TypeError, ValueError):
            continue
        if dist_m < 0:
            continue
        key = _poi_dedup_key(name)
        if not key or key in seen:
            continue
        label = _fmt_poi_distance_m(dist_m)
        if not label:
            continue
        seen.add(key)
        out.append({"name": name, "distance_m": dist_m, "distance_label": label})
    out.sort(key=lambda x: x["distance_m"])
    return out[:max_count]




# ──────────────────────────────────────────────────────────────────────────
# API « À proximité » pour le tunnel séjour (sejour.html) — 2026-05-29
# Renvoie les POI réels d'1 hôtel (raw->interestPoints HBX) via le helper
# hotel_interest_points(). Clé d'entrée = hotel_code HBX (= ce que sejour.html
# a dans selectedHotel.hotel_code). Liste vide si pas de data → front n'affiche rien.
# Route relative : nginx ajoute /api/ devant et strip avant proxy.
# ──────────────────────────────────────────────────────────────────────────
@app.get("/hotel/interest-points")
def api_hotel_interest_points(hotel_code: str = "", giata: str = ""):
    """POI réels d'un hôtel pour la fiche du tunnel séjour.

    Args:
        hotel_code: code hôtel HBX (clé hbx_hotels_catalog.hotel_code).
        giata: alternative — giata_code canonique (résolu via provider_map).
    Returns:
        JSON {"points": [{name, distance_m, distance_label}]} (liste vide si rien).
    """
    raw = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        hc = (hotel_code or "").strip()
        gi = (giata or "").strip()
        if hc and hc.isdigit():
            # Accès direct par hotel_code HBX (cas standard du tunnel séjour).
            cur.execute(
                "SELECT raw->'interestPoints' AS ip FROM hbx_hotels_catalog WHERE hotel_code = %s",
                (int(hc),),
            )
            row = cur.fetchone()
            if row:
                raw = {"interestPoints": row["ip"]}
        elif gi:
            # Accès par giata_code → jointure provider_map (provider='hbx').
            cur.execute(
                """
                SELECT hx.raw->'interestPoints' AS ip
                FROM hotels_provider_map hpm
                JOIN hbx_hotels_catalog hx ON hx.hotel_code::text = hpm.provider_hotel_code
                WHERE hpm.provider = 'hbx' AND hpm.giata_code = %s
                LIMIT 1
                """,
                (gi,),
            )
            row = cur.fetchone()
            if row:
                raw = {"interestPoints": row["ip"]}
        cur.close(); conn.close()
    except Exception as e:
        print(f"[interest-points] ERROR : {e}")
        return {"points": []}

    if not raw or raw.get("interestPoints") is None:
        return {"points": []}
    # Réutilise le helper grounded (tri croissant, dédup, format distance FR), max 8.
    return {"points": hotel_interest_points(raw, max_count=8)}


# ──────────────────────────────────────────────────────────────────────────
# API autocomplete hôtel PAR NOM — 2026-05-29
# Recherche d'un hôtel par son nom (et pas seulement par ville). 4217 hôtels
# avec slug dans hotels_canonical. Match ILIKE simple (pg_trgm non installé).
# Renvoie l'URL de la fiche SEO via _hotel_seo_path(). Requête paramétrée.
# Route relative : nginx ajoute /api/ devant et strip avant proxy.
# ──────────────────────────────────────────────────────────────────────────
@app.get("/hotels/autocomplete")
def api_hotels_autocomplete(q: str = "", city: str = ""):
    """Autocomplete hôtels par nom → suggestions cliquables vers la fiche.

    Args:
        q: nom (ou fragment) d'hôtel, >= 2 caractères.
        city: filtre optionnel par ville (utilisé par les pages parcours
              /destinations/{slug} pour réduire le champ aux hôtels de cette
              ville). 2026-05-31 : ajouté suite recadrage Pascal — sans filtre
              ville, "Sofitel" renvoie 50 résultats absurdes.
    Returns:
        JSON list of {name, city, url}.
    """
    term = (q or "").strip()
    if len(term) < 2:
        return []
    out = []
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        city_filter = (city or "").strip()
        if city_filter:
            cur.execute(
                """
                SELECT name, city, country_code, slug
                FROM hotels_canonical
                WHERE slug IS NOT NULL AND name ILIKE %s
                  AND LOWER(city) LIKE LOWER(%s)
                ORDER BY (CASE WHEN name ILIKE %s THEN 0 ELSE 1 END),
                         total_photos DESC NULLS LAST
                LIMIT 10
                """,
                ("%" + term + "%", "%" + city_filter + "%", term + "%"),
            )
        else:
            cur.execute(
                """
                SELECT name, city, country_code, slug
                FROM hotels_canonical
                WHERE slug IS NOT NULL AND name ILIKE %s
                ORDER BY (CASE WHEN name ILIKE %s THEN 0 ELSE 1 END),
                         total_photos DESC NULLS LAST
                LIMIT 10
                """,
                ("%" + term + "%", term + "%"),
            )
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[hotels-autocomplete] ERROR : {e}")
        return []
    for r in rows:
        out.append({
            "name": r["name"],
            "city": r.get("city") or "",
            "url": _hotel_seo_path(r.get("country_code"), r.get("city"), r["slug"]),
        })
    return out







def alert_conciergerie(airbizness_ref: str, severity: str, alert_type: str,
                        payload: dict | None = None) -> int | None:
    """Log une alerte dans conciergerie_alerts. Retourne l'id de l'alerte créée.

    severity : 'info' | 'warn' | 'critical'
    alert_type : 'booking_failed_vol' | 'booking_failed_hotel' | 'substitute_needed'
                 | 'refund_needed' | 'price_drift_high' | 'manual_action_required'
    is_sandbox : auto-détecté si payload contient {"sandbox": True}
    """
    is_sandbox = bool((payload or {}).get("sandbox"))
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO conciergerie_alerts
                (airbizness_ref, severity, alert_type, payload, status, is_sandbox)
            VALUES (%s, %s, %s, %s, 'open', %s)
            RETURNING id
        """, (airbizness_ref, severity, alert_type, json.dumps(payload or {}), is_sandbox))
        alert_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close()
        return alert_id
    except Exception as e:
        import logging
        logging.getLogger("conciergerie").error(f"alert_conciergerie failed: {e}")
        return None


# Constantes + helpers photos HBX : ont MIGRÉ dans providers/hbx/photos.py
# (carnet du chef = dans la cuisine du fournisseur, pas dans la salle main.py).
# On les ré-expose ici pour ne pas casser les callers existants ; à terme chaque
# caller doit importer directement depuis providers.hbx.photos.
from providers.hbx.photos import (  # noqa: E402
    HBX_PHOTO_BASE, HBX_GEN_CODES, HBX_ROOM_CODES, HBX_COMMON_CODES,
    extract_best_main_photo, extract_gallery_photos,
)


# ──────────────────────────────────────────────────────────────────────────
# FACILITIES / ÉQUIPEMENTS HBX → libellés FR groupés par catégorie d'affichage
# ──────────────────────────────────────────────────────────────────────────
# Mapping pragmatique des codes HBX les plus communs.
# TODO : remplacer par sync table de référence HBX /types/facilities?language=FRA
# Quand RateHawk arrive, leur API renvoie déjà des labels traduits → on adapte ici.

HBX_FACILITY_LABELS_FR: dict = {
    # (facilityGroupCode, facilityCode): (label_FR, category)
    # Group 10 — General services
    (10, 30):  ("Bar",                              "general"),
    (10, 50):  ("Restaurant",                       "general"),
    (10, 60):  ("Café",                             "general"),
    (10, 70):  ("Bagagerie",                        "general"),
    (10, 100): ("Wi-Fi gratuit",                    "populaire"),
    (10, 110): ("Internet haut débit",              "populaire"),
    (10, 124): ("Wi-Fi en chambre",                 "populaire"),
    (10, 125): ("Wi-Fi espaces communs",            "general"),
    (10, 130): ("Conciergerie",                     "populaire"),
    (10, 170): ("Service en chambre",               "general"),
    (10, 175): ("Coffre-fort",                      "general"),
    (10, 215): ("Conciergerie 24h/24",              "populaire"),
    (10, 245): ("Personnel multilingue",            "general"),
    (10, 261): ("Service de change",                "general"),
    (10, 280): ("Service de blanchisserie",         "general"),
    (10, 305): ("Centre d'affaires",                "business"),
    (10, 320): ("Réception 24h/24",                 "populaire"),
    (10, 340): ("Espace fumeurs",                   "general"),
    (10, 360): ("Salles de réunion",                "business"),
    # Group 20 — Wellness & leisure
    (20, 60):  ("Piscine extérieure",               "populaire"),
    (20, 65):  ("Piscine intérieure",               "populaire"),
    (20, 70):  ("Spa",                              "populaire"),
    (20, 80):  ("Sauna",                            "loisirs"),
    (20, 90):  ("Hammam",                           "loisirs"),
    (20, 100): ("Salle de fitness",                 "populaire"),
    (20, 110): ("Massage",                          "loisirs"),
    (20, 130): ("Centre de bien-être",              "loisirs"),
    (20, 180): ("Jardin",                           "loisirs"),
    (20, 215): ("Bain à remous",                    "loisirs"),
    # Group 30 — Sports
    (30, 30):  ("Court de tennis",                  "sports"),
    (30, 50):  ("Golf à proximité",                 "sports"),
    (30, 80):  ("Location de vélos",                "sports"),
    # Group 40 — Climate
    (40, 261): ("Air conditionné",                  "populaire"),
    (40, 285): ("Chauffage",                        "general"),
    # Group 50 — Parking
    (50, 70):  ("Parking",                          "populaire"),
    (50, 75):  ("Parking gratuit",                  "populaire"),
    (50, 76):  ("Parking couvert",                  "general"),
    (50, 245): ("Service voiturier",                "general"),
    # Group 60 — Business
    (60, 30):  ("Salle de réunion",                 "business"),
    (60, 50):  ("Centre d'affaires",                "business"),
    # Group 65 — Pets
    (65, 215): ("Animaux acceptés",                 "populaire"),
    # Group 70 — Accessibility
    (70, 30):  ("Accès handicapés",                 "accessibilite"),
    (70, 70):  ("Ascenseur",                        "general"),
    (70, 80):  ("Chambres adaptées handicap",       "accessibilite"),
    # Group 80 — Children
    (80, 30):  ("Bienvenue aux enfants",            "famille"),
    (80, 50):  ("Lits bébé sur demande",            "famille"),
    (80, 80):  ("Aire de jeux",                     "famille"),
    (80, 245): ("Service de baby-sitting",          "famille"),
}

# Labels affichage + ordre des catégories
FACILITY_CATEGORY_ORDER = ["populaire", "general", "business", "loisirs", "sports", "famille", "accessibilite"]
FACILITY_CATEGORY_LABELS = {
    "populaire":     "Populaire",
    "general":       "Général",
    "business":      "Business",
    "loisirs":       "Bien-être & loisirs",
    "sports":        "Sports",
    "famille":       "Famille",
    "accessibilite": "Accessibilité",
}


# ──────────────────────────────────────────────────────────────────────────
# HELPERS RATES : décodage room_code HBX + format annulation FR
# ──────────────────────────────────────────────────────────────────────────
# Codes HBX standards :
#   Type de chambre (préfixe) : DBL=Double, DUS=Double Use Single, TPL=Triple,
#     JSU=Junior Suite, SUI=Suite, FAM=Familiale, STD=Standard
#   Catégorie (suffixes) : ST=Standard, DX=Deluxe, SU=Superior, EX=Executive,
#     CB=Club, EJ=Executive Junior, C1/C2=Catégorie 1/2

HBX_ROOM_TYPE_FR = {
    "DBL":  "Chambre double",
    "DUS":  "Chambre double usage simple",
    "TPL":  "Chambre triple",
    "QUA":  "Chambre quadruple",
    "JSU":  "Junior Suite",
    "SUI":  "Suite",
    "STD":  "Chambre standard",
    "FAM":  "Chambre familiale",
    "SGL":  "Chambre individuelle",
    "TWN":  "Chambre twin (2 lits)",
}
# Régimes (board) HBX → FR (mapping étendu vs juste board_name brut)
HBX_BOARD_FR = {
    "RO":   "Repas non inclus",
    "SC":   "Repas non inclus",
    "ROOM ONLY": "Repas non inclus",
    "BB":   "Petit-déjeuner inclus",
    "BED AND BREAKFAST": "Petit-déjeuner inclus",
    "HB":   "Demi-pension",
    "HALF BOARD": "Demi-pension",
    "FB":   "Pension complète",
    "FULL BOARD": "Pension complète",
    "AI":   "Tout inclus",
    "ALL INCLUSIVE": "Tout inclus",
    "AIDR": "Tout inclus (avec boissons)",
}


def board_label_fr(board_code: str, board_name: str = "") -> str:
    """Traduit le régime alimentaire en FR.
    Tente board_code (RO, BB, HB, FB, AI), puis board_name (ROOM ONLY, etc.).
    Fallback : board_name original si rien trouvé."""
    if board_code and board_code.upper() in HBX_BOARD_FR:
        return HBX_BOARD_FR[board_code.upper()]
    if board_name and board_name.upper() in HBX_BOARD_FR:
        return HBX_BOARD_FR[board_name.upper()]
    return board_name or "Repas non inclus"


HBX_ROOM_CAT_FR = {
    "ST":   "Standard",
    "DX":   "Deluxe",
    "SU":   "Superior",
    "EX":   "Executive",
    "CB":   "Club",
    "EJ":   "Executive Junior",
    "C1":   "Catégorie 1",
    "C2":   "Catégorie 2",
    "C3":   "Catégorie 3",
    "VW":   "Avec vue",
}


def describe_hbx_room(room_code: str, room_name: str = "") -> str:
    """Décode 'DBL.DX-SU' → 'Chambre double Deluxe Superior'.
    Si on ne reconnait rien, fallback sur room_name fourni par HBX."""
    if not room_code or not isinstance(room_code, str):
        return room_name or ""
    parts = room_code.replace(".", "-").split("-")
    if not parts:
        return room_name or room_code
    type_str = HBX_ROOM_TYPE_FR.get(parts[0], parts[0])
    cats = []
    for p in parts[1:]:
        lbl = HBX_ROOM_CAT_FR.get(p)
        if lbl:
            cats.append(lbl)
    if cats:
        return f"{type_str} {' '.join(cats)}"
    # Fallback : on a juste le type, ou rien
    return type_str if type_str != parts[0] else (room_name or room_code)


def format_cancellation_fr(policies: list) -> dict:
    """Retourne {label, until_date_fr, is_free}.
    - Si annulation gratuite jusqu'à une date : 'Annulation gratuite jusqu'au 26 mai'
    - Si frais : 'Annulation : 150 EUR à partir du 28 mai'
    - Si non remboursable : 'Non remboursable'
    """
    if not policies:
        return {"label": "Annulation flexible", "until_date_fr": None, "is_free": True}

    # Trie par date croissante (la 1ère policy = celle qui s'applique en premier)
    sorted_p = sorted(policies, key=lambda p: p.get("from") or "9999")
    first = sorted_p[0]
    try:
        amount = float(first.get("amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    from_str = first.get("from")

    if amount == 0:
        # Gratuit, jusqu'à la prochaine policy avec amount > 0
        next_paying = next((p for p in sorted_p[1:] if float(p.get("amount", 0) or 0) > 0), None)
        if next_paying and next_paying.get("from"):
            return {
                "label": f"0 EUR jusqu'au {_fr_date(next_paying['from'])}",
                "until_date_fr": _fr_date(next_paying["from"]),
                "is_free": True,
            }
        return {"label": "Annulation gratuite", "until_date_fr": None, "is_free": True}
    else:
        # Frais d'emblée = NRF
        return {
            "label": "Non",
            "until_date_fr": None,
            "is_free": False,
        }


_FR_MONTHS = ["janv.","févr.","mars","avr.","mai","juin","juil.","août","sept.","oct.","nov.","déc."]
def _fr_date(iso_str: str) -> str:
    """ISO 2026-05-26T00:00:00+02:00 → '26 mai 2026'"""
    try:
        import datetime as _dt
        s = iso_str.replace("Z","+00:00").split("+")[0].split("T")[0]
        dt = _dt.date.fromisoformat(s)
        return f"{dt.day} {_FR_MONTHS[dt.month - 1]}"
    except Exception:
        return iso_str[:10] if iso_str else ""


def extract_facilities_fr(raw_facilities, provider: str = "hbx") -> dict:
    """Retourne facilities classées par catégorie d'affichage en FR.
    Format : {category: [{label, group_code, facility_code}, ...], ...}
    """
    if not raw_facilities or not isinstance(raw_facilities, list):
        return {}
    if provider != "hbx":
        return {}  # TODO RateHawk + TBO

    by_cat: dict = {}
    seen: set = set()  # dédup (un même libellé peut apparaître plusieurs fois)
    for f in raw_facilities:
        if not isinstance(f, dict):
            continue
        try:
            gc = int(f.get("facilityGroupCode") or 0)
            fc = int(f.get("facilityCode") or 0)
        except (TypeError, ValueError):
            continue
        mapping = HBX_FACILITY_LABELS_FR.get((gc, fc))
        if not mapping:
            continue
        label, category = mapping
        if label in seen:
            continue
        seen.add(label)
        by_cat.setdefault(category, []).append({
            "label": label,
            "group_code": gc,
            "facility_code": fc,
        })
    # Retourne dict ordonné par FACILITY_CATEGORY_ORDER
    return {
        cat: by_cat[cat]
        for cat in FACILITY_CATEGORY_ORDER
        if cat in by_cat
    }


# extract_room_photos a migré dans providers/hbx/photos.py (cf. ré-export en haut).
from providers.hbx.photos import extract_room_photos  # noqa: E402,F811


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance grand-cercle en km entre 2 points (formule de Haversine)."""
    R = 6371.0
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi = _math.radians(lat2 - lat1)
    dlmb = _math.radians(lng2 - lng1)
    a = _math.sin(dphi/2)**2 + _math.cos(phi1)*_math.cos(phi2)*_math.sin(dlmb/2)**2
    return 2 * R * _math.asin(min(1.0, _math.sqrt(a)))


def _city_key(city_or_country: str) -> Optional[str]:
    """Normalise un nom de ville pour matcher CITY_CENTERS / CITY_AIRPORTS."""
    if not city_or_country:
        return None
    k = city_or_country.strip().upper()
    if k in CITY_CENTERS:
        return k
    # Heuristique : "MADRID, ES" → "MADRID"
    first = k.split(",")[0].strip()
    if first in CITY_CENTERS:
        return first
    return None









def _slugify(s: str) -> str:
    """Slugify : lowercase, ascii, espaces→tiret, max 50 chars."""
    if not s:
        return ""
    import unicodedata, re as _re
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    s = _re.sub(r"-+", "-", s).strip("-")
    return s[:50]


# ── Architecture URL SEO : /hotels/{cc}/{ville}/{slug} (cc = ISO alpha-2 minuscule)
_COUNTRY_ISO = {"FR": "fr", "UK": "gb", "ES": "es"}

def _country_iso(country_code) -> str:
    cc = (country_code or "").strip().upper()
    return _COUNTRY_ISO.get(cc, cc.lower() or "xx")

def _city_url_slug(city) -> str:
    import re as _re
    base = _re.sub(r"\(.*?\)", "", str(city or ""))  # retire les suffixes type "(UK)"
    return _slugify(base) or "ville"

def _hotel_seo_path(country_code, city, slug) -> str:
    return f"/hotels/{_country_iso(country_code)}/{_city_url_slug(city)}/{slug}"


# Cache mémoire pour résolution slug → hbx_destinations row (7210 rows, refresh 1h)














# ════════════════════════════════════════════════════════════════════
#  PAGES SEO VOLS — /vols/{route_slug}  (ex: /vols/paris-dubai)
#  Contenu STABLE grounded sur route_stats + cartes de vol LIVE via /deals.
# ════════════════════════════════════════════════════════════════════
def _airport_info(iata: str) -> dict:
    """Renvoie {code, name, city, country} pour un code IATA (via cache airports)."""
    if not _airports_cache:
        _load_airports_cache()
    code = (iata or "").strip().upper()
    for a in _airports_cache:
        if (a.get("code") or "").upper() == code:
            return a
    return {"code": code, "name": code, "city": code, "country": ""}


# (RETIRÉ 2026-05-30) _vols_route_map a migré dans routers.recherche.
# On le ré-expose ici sous son ancien nom pour ne pas casser les callers existants
# (vol_route_page, sitemap). À terme : remplacer chaque caller par
# `from routers.recherche import slug_to_iata_pair` (forme propre, pas un dict).
from routers.recherche import _vols_route_map  # noqa: E402










@app.get("/booking/{airbizness_ref}/voucher.pdf")
def hotel_voucher_pdf(airbizness_ref: str):
    """Génère le voucher PDF AirBizness pour 1 résa hôtel à la volée."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT b.*,
               h.name AS hotel_full_name,
               h.address AS hotel_address,
               h.city AS hotel_city
        FROM bookings_v2 b
        LEFT JOIN hbx_hotels_catalog h ON h.hotel_code = b.hotel_code
        WHERE b.airbizness_ref = %s
    """, (airbizness_ref,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from voucher import render_hotel_voucher
        pdf_bytes = render_hotel_voucher(dict(row))
    except Exception as e:
        return JSONResponse({"error": "render_failed", "detail": str(e)}, status_code=500)

    safe_ref = airbizness_ref.replace("/", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="voucher-{safe_ref}.pdf"'},
    )




# HBX catalog (cron-progress, hbx-sync/status, stats) — déplacés dans routers/hotelier.py (2026-06-03)


# ═════════════════════════════════════════════════════════════════════════
# /api/v2/ — Endpoints multi-provider unifiés (HBX + TBO + RateHawk + ...)
# Toute la stack OTA passe par providers.base.aggregator pour la dédup giata.
# ═════════════════════════════════════════════════════════════════════════

def _get_active_hotel_providers():
    """Liste les providers d'hôtels actifs (clé API présente)."""
    from providers.hbx.provider import HbxProvider
    from providers.tbo.provider import TboProvider
    from providers.ratehawk.provider import RateHawkProvider
    from providers.webbeds.provider import WebbedsProvider
    candidates = [HbxProvider(), TboProvider(), RateHawkProvider(), WebbedsProvider()]
    actives = []
    for p in candidates:
        if p.name == "hbx" or getattr(p, "enabled", False):
            actives.append(p)
    return actives


# /v2/providers/health déplacé dans routers/admin_ops.py (2026-06-03)
# (l'helper _get_active_hotel_providers reste ici car utilisé aussi par /v2/booking/checkrate)


@app.post("/v2/booking/checkrate")
def v2_booking_checkrate(payload: dict):
    """Re-verify rate before booking. Route au bon provider via préfixe rate_key."""
    from providers.base import route_rate_key
    rate_key = payload.get("rate_key", "")
    providers = _get_active_hotel_providers()
    provider = route_rate_key(rate_key, providers)
    if not provider:
        return JSONResponse({"error": "unknown_provider", "rate_key": rate_key}, status_code=400)
    verif = provider.checkrate(rate_key)
    return {
        "ok": verif.ok,
        "rate_key": verif.rate_key,
        "current_price": verif.current_price,
        "price_changed": verif.price_changed,
        "reason": verif.reason,
        "provider": provider.name,
    }


# HBX catalog (hotel/{code}, hotels) — déplacés dans routers/hotelier.py (2026-06-03)


# ════════════════════════════════════════════════════════════════════════════
# FLIGHT BOOKING V0 (2026-05-24) — Doctrine Pascal "parcours doit aboutir au
# paiement même si Duffel renvoie 0". Mock first-class : booking sandbox sans
# facturer Duffel tant que DUFFEL_BOOKING_DRY_RUN=true ou offer_id MOCK-*.
#
# Endpoints :
#   POST /flight/booking/payment-intent   → Stripe PI + insert flight_bookings
#   POST /flight/booking/confirm          → Duffel order (ou mock) + UPDATE
#   GET  /flight/booking/{ref}            → récap pour confirmation page
# ════════════════════════════════════════════════════════════════════════════

class FlightPassenger(BaseModel):
    firstName: str = Field(min_length=1, max_length=80)
    lastName: str = Field(min_length=1, max_length=80)
    # Audit 2026-05-27 critique #38 : DOB obligatoire YYYY-MM-DD strict — pas
    # de fallback "1990-01-01" côté duffel.py. Duffel utilise born_on pour la
    # tarification (adult/child/infant) + validation passeport. DOB faux = booking refusé.
    dob: str = Field(min_length=10, max_length=10)       # YYYY-MM-DD
    nationality: str = Field(default="", max_length=2)   # ISO-3166 alpha-2 (audit 2026-05-27 crit #30)
    passportNumber: str = Field(default="", max_length=20)
    # Audit 2026-05-27 : expiration passeport (requis vols intl par Duffel)
    passportExpiry: str = Field(default="", max_length=20)  # YYYY-MM-DD
    title: str = Field(default="mr", max_length=8)

    @field_validator("dob")
    @classmethod
    def _validate_dob(cls, v: str) -> str:
        import datetime as _dt
        try:
            d = _dt.date.fromisoformat(v)
        except Exception:
            raise ValueError(f"dob invalide (attendu YYYY-MM-DD): {v!r}")
        if d >= _dt.date.today():
            raise ValueError(f"dob doit être dans le passé: {v!r}")
        if d.year < 1900:
            raise ValueError(f"dob trop ancienne: {v!r}")
        return v


class FlightPaymentIntentRequest(BaseModel):
    offer_id: str = Field(min_length=4, max_length=256)
    user_email: EmailStr
    passengers: list[FlightPassenger]
    total_eur: float = Field(gt=0, lt=50000)
    currency: str = Field(default="eur", max_length=3)
    # Snapshot descriptif du vol (utile pour MOCK-* qui n'existent pas en DB).
    # On NE prend JAMAIS le prix d'ici pour la sanity-check Stripe : la source de
    # vérité reste body.total_eur (et la table `deals` si l'offer est non-mock).
    deal_snapshot: dict = Field(default_factory=dict)
    is_roundtrip: Optional[bool] = False  # Pascal 2026-05-26 align sejour
    # Options vol (calculées côté serveur, jamais confiance au client pour le prix)
    baggage_per_passenger: Optional[List[str]] = None  # ['23kg','15kg',None] — alias rétrocompat (aller)
    # ── Bagages par leg (Pascal 2026-05-26 align sejour) ──
    baggage_outbound_per_passenger: Optional[List[str]] = None
    baggage_inbound_per_passenger: Optional[List[str]] = None
    cabin_premium: Optional[bool] = False  # alias rétrocompat (= aller)
    flex_ticket: Optional[bool] = False    # alias rétrocompat (= aller)
    insurance: Optional[bool] = False      # alias rétrocompat (= aller)
    transfer: Optional[str] = None         # "none" / "oneway" / "roundtrip" (legacy)
    # Transfer HBX (legacy, alias = aller) — Pascal 2026-05-24
    transfer_rate_key: Optional[str] = None
    transfer_price: Optional[float] = 0.0
    transfer_label: Optional[str] = None
    transfer_meta: Optional[dict] = None
    # ── Options PAR LEG (Pascal 2026-05-26 align sejour allbyleg) ──
    cabin_premium_outbound: Optional[bool] = False
    cabin_premium_inbound: Optional[bool] = False
    flex_ticket_outbound: Optional[bool] = False
    flex_ticket_inbound: Optional[bool] = False
    flight_insurance_outbound: Optional[bool] = False
    flight_insurance_inbound: Optional[bool] = False
    transfer_outbound: Optional[str] = None
    transfer_outbound_rate_key: Optional[str] = None
    transfer_outbound_price: Optional[float] = 0.0
    transfer_outbound_label: Optional[str] = None
    transfer_outbound_meta: Optional[dict] = None
    transfer_outbound_address: Optional[str] = None
    transfer_inbound: Optional[str] = None
    transfer_inbound_rate_key: Optional[str] = None
    transfer_inbound_price: Optional[float] = 0.0
    transfer_inbound_label: Optional[str] = None
    transfer_inbound_meta: Optional[dict] = None
    transfer_inbound_address: Optional[str] = None
    # Sièges sélectionnés par leg : {outbound:{0:'seat_1A'}, inbound:{0:'seat_5C'}}
    selected_seats: Optional[dict] = None
    # Concierge hôtelier (résa pour client) — Pascal 2026-05-24
    on_behalf_of: Optional[str] = None
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_ref: Optional[str] = None
    # Services Duffel réels (bagages/sièges/etc.) — Pascal 2026-05-26 (P1 juridique)
    # Liste [{id:"ase_xxx", quantity:1}] — IDs récupérés via /duffel/offer_with_services/{id}.
    # Persistés dans raw_offer.options.duffel_services, consommés par webhook create_order.
    duffel_services: Optional[List[dict]] = None


class FlightBookingConfirmRequest(BaseModel):
    airbizness_ref: str
    payment_intent_id: str


def _is_mock_offer(offer_id: str) -> bool:
    return bool(offer_id) and offer_id.startswith("MOCK-")


def _load_offer_for_booking(offer_id: str) -> dict:
    """Charge un offer depuis la DB (deals) ou retourne un dict mock-friendly.

    Si l'offer commence par MOCK-*, on régénère un mock cohérent par hash, sinon
    on lit la table deals. Comme MOCK-* n'existe pas en DB, la régénération
    permet à confirm() de retrouver les détails affichables.
    """
    if _is_mock_offer(offer_id):
        # On régénère un mock générique. Les vraies données affichées côté front
        # auront été sauvegardées dans flight_bookings au moment du payment-intent.
        return {
            "offer_id": offer_id,
            "is_mock": True,
            "airline_name": "Air France",
            "airline_code": "AF",
            "origin": "CDG",
            "destination": "MAD",
            "price": 0,
            "currency": "EUR",
            "departure_at": None,
            "duration_minutes": 0,
        }
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM deals WHERE offer_id = %s LIMIT 1", (offer_id,))
    deal = cur.fetchone()
    cur.close(); conn.close()
    if not deal:
        raise HTTPException(404, "Offer not found")
    d = dict(deal)
    d["is_mock"] = False
    return d


@limiter.limit("30/minute")
def flight_confirm_booking(request: Request, body: FlightBookingConfirmRequest):
    """Appelé après que Stripe ait confirmé la CB côté front.

    - Si offer_id MOCK-* ou DUFFEL_BOOKING_DRY_RUN=true → faux PNR AB-FL-XXX (mock)
    - Sinon → vrai appel Duffel /air/orders (TODO si on retire dry-run)

    Idempotent : si la résa est déjà 'confirmed', renvoie l'état actuel.
    Pas d'email Brevo envoyé pour les mocks (doctrine Pascal : aucun email tant
    que ce n'est pas un vrai booking confirmé).
    """
    # 1) Vérifie Stripe PI
    try:
        intent = stripe.PaymentIntent.retrieve(body.payment_intent_id)
    except Exception as e:
        raise HTTPException(400, f"Stripe retrieve fail: {e}")

    if intent.status != "succeeded":
        raise HTTPException(400, f"Paiement non confirmé (status={intent.status})")

    # 2) Charge la résa
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM flight_bookings WHERE airbizness_ref = %s", (body.airbizness_ref,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(404, "Booking ref not found")
    if row["status"] == "confirmed":
        cur.close(); conn.close()
        return {
            "airbizness_ref": body.airbizness_ref,
            "pnr": row["pnr"],
            "status": "confirmed",
            "is_mock": row["is_mock"],
            "idempotent": True,
        }
    cur.close()

    is_mock = bool(row["is_mock"]) or _is_mock_offer(row["offer_id"])
    dry_run = os.environ.get("DUFFEL_BOOKING_DRY_RUN", "false").lower() == "true"

    duffel_order_id = None
    pnr = None

    if is_mock or dry_run:
        # ── MOCK PATH : pas d'appel Duffel ──
        import secrets as _s
        pnr = "AB-FL-" + _s.token_hex(3).upper()  # AB-FL-ABCDEF
        duffel_order_id = None
        is_mock = True  # force le flag (utile pour dry_run de vraies offers)
    else:
        # ── REAL PATH ──
        # Audit 2026-05-27 : le real Duffel booking est exécuté par le webhook
        # Stripe `payment_intent.succeeded` (voir main.py:~7340). Cet endpoint
        # /flight/booking/confirm devient principalement un fallback côté front
        # quand le webhook tarde (lag réseau Stripe).
        # On vérifie en DB si le webhook a déjà bookè (status='booked' ou
        # duffel_order_id présent). Sinon on alerte Telegram (silent fail) +
        # tente le refund par sécurité.
        try:
            import sys as _sys
            if "/var/www/airbizness" not in _sys.path:
                _sys.path.insert(0, "/var/www/airbizness")
            # Re-check DB : webhook a-t-il déjà booké ?
            recheck_conn = psycopg2.connect(**DB_CONFIG)
            with recheck_conn, recheck_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as rcur:
                rcur.execute("""
                    SELECT status, duffel_order_id, pnr, booking_reference
                    FROM flight_bookings WHERE airbizness_ref=%s
                """, (body.airbizness_ref,))
                rr = rcur.fetchone() or {}
            recheck_conn.close()
            if rr.get("duffel_order_id"):
                # Webhook a déjà fait son job → on renvoie l'état actuel (idempotent)
                return {
                    "airbizness_ref": body.airbizness_ref,
                    "pnr": rr.get("pnr") or rr.get("booking_reference"),
                    "duffel_order_id": rr.get("duffel_order_id"),
                    "status": rr.get("status") or "booked",
                    "is_mock": False,
                    "via": "webhook_already_processed",
                }
            # Sinon : on alerte Telegram (le webhook devrait avoir tourné) et
            # on raise pour déclencher refund défensif. Ne pas faire l'appel
            # Duffel direct ici pour éviter le double-booking via race avec
            # le webhook.
            _alert_telegram(
                f"⚠️ Vol {body.airbizness_ref} : /flight/booking/confirm appelé "
                f"sans Duffel order persisté. Webhook lag ou KO. Refund défensif "
                f"déclenché. PI={body.payment_intent_id}"
            )
            raise HTTPException(
                503,
                "Booking en attente du webhook Stripe → Duffel. "
                "Réessayez dans 30s ou contactez le support.",
            )
        except HTTPException:
            # Refund Stripe pour ne pas garder l'argent sans contrepartie
            try:
                stripe.Refund.create(
                    payment_intent=body.payment_intent_id,
                    metadata={"airbizness_ref": body.airbizness_ref,
                              "reason": "duffel_not_implemented_v0"},
                )
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise

    # 3.b) Transfer HBX best-effort booking
    transfer_booking_ref = None
    try:
        raw_offer = row.get("raw_offer") or {}
        if isinstance(raw_offer, str):
            raw_offer = json.loads(raw_offer)
        opts = (raw_offer or {}).get("options") or {}
        t_rk = opts.get("transfer_rate_key")
        if t_rk:
            from providers import hbx_transfer as _ht
            # passenger 0 = holder (cf payment-intent : passenger list)
            paxes = row.get("passengers") or []
            if isinstance(paxes, str):
                paxes = json.loads(paxes)
            holder = paxes[0] if paxes else {}
            holder_name = (
                f"{holder.get('firstName','')} {holder.get('lastName','')}".strip()
                or row.get("user_email") or "AirBizness Client"
            )
            tres = _ht.book_transfer(
                rate_key=t_rk,
                holder_name=holder_name,
                holder_email=row.get("user_email") or "",
                client_reference=body.airbizness_ref,
            )
            transfer_booking_ref = tres.get("reference")
    except Exception as e:
        print(f"[flight.confirm] transfer book best-effort fail: {e}")

    # 3) UPDATE flight_bookings → confirmed
    try:
        with conn, conn.cursor() as cur2:
            if transfer_booking_ref:
                cur2.execute("""
                    UPDATE flight_bookings SET
                      status='confirmed', pnr=%s, duffel_order_id=%s,
                      is_mock=%s, confirmed_at=NOW(),
                      raw_offer = COALESCE(raw_offer,'{}'::jsonb) || %s::jsonb
                    WHERE airbizness_ref=%s
                """, (pnr, duffel_order_id, is_mock,
                      json.dumps({"transfer_booking_ref": transfer_booking_ref}),
                      body.airbizness_ref))
            else:
                cur2.execute("""
                    UPDATE flight_bookings SET
                      status='confirmed', pnr=%s, duffel_order_id=%s,
                      is_mock=%s, confirmed_at=NOW()
                    WHERE airbizness_ref=%s
                """, (pnr, duffel_order_id, is_mock, body.airbizness_ref))
    except Exception as e:
        print(f"[flight.confirm] UPDATE fail: {e}")
    finally:
        try: conn.close()
        except: pass

    # PAS D'EMAIL pour le mock (doctrine Pascal : risque juridique)
    # Email Brevo seulement quand on aura le vrai vol Duffel + voucher PDF.

    return {
        "airbizness_ref": body.airbizness_ref,
        "pnr": pnr,
        "duffel_order_id": duffel_order_id,
        "status": "confirmed",
        "is_mock": is_mock,
        "transfer_booking_ref": transfer_booking_ref,
    }


@app.get("/flight/booking/{airbizness_ref}/cancel-preview")
@limiter.limit("20/minute")
def flight_cancel_preview(request: Request, airbizness_ref: str):
    """Audit 2026-05-27 sev 3 #64/#65/#68 : crée cancellation Duffel pending
    pour récupérer refund_amount et exposer au client AVANT confirm.

    Détecte aussi void_window_ends_at (full refund possible si encore actif).
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT duffel_order_id, is_mock, total_eur FROM flight_bookings WHERE airbizness_ref=%s",
                 (airbizness_ref,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise HTTPException(404, "Booking not found")
    if row.get("is_mock"):
        return {"airbizness_ref": airbizness_ref, "is_mock": True,
                "refund_amount": float(row.get("total_eur") or 0),
                "refund_currency": "EUR", "void_window": True}
    order_id = row.get("duffel_order_id")
    if not order_id:
        raise HTTPException(409, "No Duffel order to cancel")
    try:
        from providers.duffel import get_order_live, create_order_cancellation
        order = get_order_live(order_id)
        cancellation = create_order_cancellation(order_id)
    except Exception as e:
        _alert_telegram(f"flight/cancel-preview KO ab_ref={airbizness_ref}: {str(e)[:200]}")
        raise HTTPException(502, f"Duffel cancellation preview failed: {str(e)[:200]}")
    return {
        "airbizness_ref": airbizness_ref,
        "duffel_order_id": order_id,
        "duffel_cancellation_id": cancellation.get("id"),
        "refund_amount": float(cancellation.get("refund_amount") or 0),
        "refund_currency": cancellation.get("refund_currency") or "EUR",
        "refund_to": cancellation.get("refund_to"),
        "void_window_ends_at": order.get("void_window_ends_at"),
        "expires_at": cancellation.get("expires_at"),
    }




def _ensure_perf_indexes():
    """Audit 2026-05-27 sev 2 : DB indexes manquants sur tables critiques.
    Idempotent. Best-effort (ne casse pas le boot si DDL fail)."""
    statements = [
        # Booking lookup par référence (utilisé par webhooks, /mes-voyages, sync)
        "CREATE INDEX IF NOT EXISTS idx_flight_bookings_ab_ref ON flight_bookings (airbizness_ref)",
        "CREATE INDEX IF NOT EXISTS idx_flight_bookings_user_email ON flight_bookings (user_email)",
        "CREATE INDEX IF NOT EXISTS idx_flight_bookings_status ON flight_bookings (status)",
        "CREATE INDEX IF NOT EXISTS idx_flight_bookings_duffel_order ON flight_bookings (duffel_order_id)",
        "CREATE INDEX IF NOT EXISTS idx_flight_bookings_stripe_pi ON flight_bookings (stripe_payment_intent)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_v2_user_email ON bookings_v2 (user_email)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_v2_ab_ref ON bookings_v2 (airbizness_ref)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_v2_status ON bookings_v2 (status)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_v2_hbx_ref ON bookings_v2 (hbx_reference)",
        "CREATE INDEX IF NOT EXISTS idx_pack_bookings_ab_ref ON pack_bookings (airbizness_ref)",
        "CREATE INDEX IF NOT EXISTS idx_pack_bookings_user_email ON pack_bookings (user_email)",
        "CREATE INDEX IF NOT EXISTS idx_pack_bookings_status ON pack_bookings (status)",
        "CREATE INDEX IF NOT EXISTS idx_activity_bookings_v2_ab_ref ON activity_bookings_v2 (airbizness_ref)",
        "CREATE INDEX IF NOT EXISTS idx_activity_bookings_v2_user_email ON activity_bookings_v2 (user_email)",
        # Deals (search cache) — query par origin+destination+date
        "CREATE INDEX IF NOT EXISTS idx_deals_route ON deals (origin, destination, departure_at)",
        "CREATE INDEX IF NOT EXISTS idx_deals_expires ON deals (expires_at)",
        # Duffel webhook events — lookup par order
        "CREATE INDEX IF NOT EXISTS idx_duffel_webhook_events_order ON duffel_webhook_events (duffel_order_id)",
        # Stripe webhook events — lookup par received_at (cleanup vieux events)
        "CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_received ON stripe_webhook_events (received_at)",
    ]
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        ok_n = 0
        for stmt in statements:
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                conn.commit()
                ok_n += 1
            except Exception as _e:
                # Table peut ne pas exister (env vide), on continue
                conn.rollback()
                # Log silencieux (les "relation does not exist" sont bruyants)
                pass
        conn.close()
        print(f"[perf-idx] DDL OK {ok_n}/{len(statements)} indexes appliqués")
    except Exception as e:
        print(f"[perf-idx] DDL fail: {e}")


_ensure_perf_indexes()




# ── Modularisation (2026-05-29) — 1er module isolé : schéma technique en temps réel ──
# Accès : https://airbizness.com/api/schema-technique
from routers.schema import router as _schema_router
app.include_router(_schema_router)

# ── Modularisation — Réservation (vol/hotel) + Paiement (séparé) ──
from routers.reservation import vol as _resa_vol, hotel as _resa_hotel
from routers import paiement as _paiement
app.include_router(_resa_vol.router)
app.include_router(_resa_hotel.router)
app.include_router(_paiement.router)

# ── Modularisation — Module VOL (offre : services / plan de cabine) ──
from routers import vol as _vol
app.include_router(_vol.router)

# ── Module AirBizness API (2026-05-30) — API HTTP du provider natif (transferts hôteliers).
# Le router vit dans son module, on l'inclut ici (= 1 panneau dans le hall).
from routers import airbizness_api as _airbizness_router
app.include_router(_airbizness_router.router)

# ── Module COMPTE UTILISATEUR (Pascal 2026-05-31) — auth email/password (coexiste
# avec auth Google existante) + CRUD voyageurs enregistrés (carnet voyageurs).
from routers import auth as _auth_router
from routers import user as _user_router
app.include_router(_auth_router.router)
app.include_router(_user_router.router)


# ── Extranet hôtelier (sert hotel-manager.html, contournement de nginx qui aliase
# /hotel-manager.html vers coming-soon depuis le passage en pré-lancement 2026-05-30).
# Path /hotels/* est forwardé à FastAPI par nginx, donc /hotels/manager-extranet passe.
# URL canonique : https://airbizness.com/hotels/manager-extranet?token=<TOKEN_CLAIM>
# /hotels/manager-extranet + /hotels/admin-preview — déplacés dans routers/hotelier.py (2026-06-03)
# ── Module HOTELIER (2026-06-03) — claim / hotel-manager / extranet HTML / HBX catalog admin ──
# Doit être inclus AVANT routers.hotel.router car /hotels/manager-extranet et /hotels/admin-preview
# seraient sinon interceptés par la route paramétrique /hotels/{hotel_code} de routers.hotel.
from routers import hotelier as _hotelier
app.include_router(_hotelier.router)
# Ré-export rétrocompat : routers/widget.py importait _validate_hotel_manager_token depuis main
from routers.hotelier import _validate_hotel_manager_token  # noqa: F401, E402

# ── Modularisation — Module HOTEL (offre : recherche / fiche / dispo) ──
from routers import hotel as _hotel
app.include_router(_hotel.router)

from routers import alertes as _alertes
app.include_router(_alertes.router)

from routers import sandbox as _sandbox
app.include_router(_sandbox.router)

from routers import widget as _widget
app.include_router(_widget.router)
from routers import affiliate as _affiliate
app.include_router(_affiliate.router)

# ── Module SEO (2026-06-01) — 5e module effectif migré ──
from routers import seo as _seo
app.include_router(_seo.router)

# ── RateHawk web (2026-07-22) — dispo LIVE + réservation depuis la page hôtel ──
from routers import ratehawk_web as _ratehawk_web
app.include_router(_ratehawk_web.router)

# ── Module TRANSFERTS (2026-06-01) — 6e module effectif migré ──
from routers import transferts as _transferts
app.include_router(_transferts.router)
from routers import activites as _activites
app.include_router(_activites.router)

# ── Module WEBHOOK (2026-06-02) — 8e module effectif migré ──
from routers import webhook as _webhook
app.include_router(_webhook.router)

# ── Module PACK (2026-06-02) — 9e module effectif migré ──
from routers import pack as _pack
app.include_router(_pack.router)

from routers import conciergerie as _conciergerie
app.include_router(_conciergerie.router)

# ── Module RESILIENCE (2026-06-03) — substitutes / cushion / monitoring / idempotency ──
from routers import resilience as _resilience
app.include_router(_resilience.router)

# ── Module ADMIN_OPS (2026-06-03) — healthz / status duffel & seo / supervisor / stats / providers health ──
from routers import admin_ops as _admin_ops
app.include_router(_admin_ops.router)

# ── Module AVIASALES (2026-06-02) — proxy TravelPayouts pour moteur vols natif ──
from routers import aviasales as _aviasales
app.include_router(_aviasales.router)
from routers import tiktok as _tiktok
app.include_router(_tiktok.router)
