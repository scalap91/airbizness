"""
Affiliate tracking endpoints — migré de main.py 2026-06-01 (4e module sur 13). Pascal/orchestrateur DeepSeek.

Endpoints :
  GET  /track-click       → log clic affiliation puis 302 vers deeplink partenaire
  GET  /affiliate-stats   → stats dashboard admin
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from main import DB_CONFIG, limiter
import psycopg2
import psycopg2.extras

router = APIRouter()


@router.get("/track-click")
@limiter.limit("120/minute")
def track_affiliate_click(
    request: Request,
    provider: str,
    deeplink: str,
    offer_id: str = "",
    origin: str = "",
    destination: str = "",
    price: float = 0,
    currency: str = "EUR",
):
    """Log un clic affiliation puis redirige vers le deeplink partenaire.

    Le frontend pointe ses CTA "Voir l'offre" vers cet endpoint au lieu du
    deeplink direct → permet de mesurer les conversions vers TravelPayouts,
    Skyscanner, etc.
    """
    # Whitelist domaines deeplinks autorisés (anti open redirect)
    allowed_hosts = (
        "aviasales.com", "hotellook.com", "skyscanner.com",
        "booking.com", "expedia.com", "agoda.com",
        "tp.media", "tpe.travelpayouts.com",
    )
    try:
        from urllib.parse import urlparse
        host = (urlparse(deeplink).hostname or "").lower()
        if not any(host.endswith(h) for h in allowed_hosts):
            raise HTTPException(400, f"deeplink host not allowed: {host}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "invalid deeplink")

    # Log best-effort (ne bloque pas la redirection)
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO affiliate_clicks
                  (provider, offer_id, origin, destination, price, currency,
                   deeplink_url, user_ip, user_agent, referrer)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                provider, offer_id or None, origin.upper() or None,
                destination.upper() or None, price or None, currency,
                deeplink, request.client.host if request.client else None,
                request.headers.get("user-agent", "")[:300],
                request.headers.get("referer", "")[:300],
            ))
    except Exception as _e:
        print(f"[track-click] log fail (non-fatal): {_e}")

    return RedirectResponse(url=deeplink, status_code=302)


# NOTE 2026-06-19 : l'ancien GET /affiliate-stats SANS auth (param `hours`) a été
# supprimé — il masquait (route dupliquée) le vrai endpoint admin plus bas ET exposait
# les clics sans token. Le seul /affiliate-stats est désormais affiliate_stats_admin
# (require_admin_token), utilisé par public/admin-affiliate.html.


# ─────────────────────────────────────────────────────────────
# Tracking clics boutons affiliés (Booking, Aviasales, etc.)
# Ajouté 2026-06-02 — proxy /api/affiliate-redirect + stats admin.
# ─────────────────────────────────────────────────────────────
import os
import ipaddress
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from datetime import datetime, timedelta
from fastapi import Depends
from routers.schema import require_admin_token

# Détection bots (Meta/Google/Bing crawlers, outils) — 2026-06-19.
# Les crawlers suivent les liens et polluaient affiliate_clicks (198/261 = meta-externalagent).
_BOT_UA = ("bot", "crawler", "spider", "meta-external", "facebookexternal", "slurp",
           "yandex", "ahrefs", "semrush", "curl", "python-requests", "headless", "preview")
# Fragment SQL (regex) pour exclure les bots des stats historiques.
_SQL_NO_BOT = r"AND COALESCE(user_agent,'') !~* '(bot|crawler|spider|meta-external|facebookexternal|slurp|yandex|ahrefs|semrush|curl|python-requests|headless|preview)'"


def _client_ip(request):
    """Vraie IP du visiteur : 1er hop de X-Forwarded-For (posé par nginx), sinon peer TCP.
    Sans ça, request.client.host = 127.0.0.1 (nginx) pour tout le monde."""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _is_bot(request):
    ua = (request.headers.get("user-agent") or "").lower()
    return any(b in ua for b in _BOT_UA)


VALID_PROVIDERS = {"booking", "aviasales", "expedia", "agoda", "skyscanner", "hotellook", "trip", "hotels"}
VALID_HOSTS = ("booking.com", "aviasales.com", "expedia.com", "agoda.com", "skyscanner.com", "hotellook.com", "trip.com", "hotels.com", "tp.media", "tpe.travelpayouts.com",
               # Domaines de tracking CJ Affiliate (liens profonds) — PID 101805872, 2026-06-19.
               "anrdoezrs.net", "dpbolvw.net", "kqzyfj.com", "jdoqocy.com", "tkqlhce.com", "emjcd.com")

# Module ② (2026-06-19) — table d'injection d'ID d'affiliation, data-driven.
# Pour chaque provider : liste de (param d'URL, variable .env). L'ID est ajouté au
# deeplink SEULEMENT si la var .env est remplie ET si le param n'est pas déjà présent.
# Hybride : tant qu'une var est vide, le lien marche + le clic est tracké, sans rapporter.
# search.hotellook.com porte le marker TravelPayouts → rapporte aujourd'hui.
AFFILIATE_PARAMS = {
    "booking":    [("aid", "BOOKING_AID")],
    "aviasales":  [("marker", "TRAVELPAYOUTS_MARKER")],
    "hotellook":  [("marker", "TRAVELPAYOUTS_MARKER")],
    "skyscanner": [("associateid", "SKYSCANNER_AID")],
    "agoda":      [("cid", "AGODA_CID")],
    "expedia":    [("affcid", "EXPEDIA_AFFID")],
    "hotels":     [("affcid", "HOTELS_AFFID")],
    "trip":       [("Allianceid", "TRIP_ALLIANCE_ID"), ("SID", "TRIP_SID")],
}


@router.get("/affiliate-redirect")
@limiter.limit("120/minute")
async def affiliate_redirect(
    request: Request,
    provider: str,
    dest: str,
    hotel_code: str = ""
):
    if provider not in VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {provider}")

    parsed = urlparse(dest)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="dest must be https")
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in VALID_HOSTS):
        raise HTTPException(status_code=400, detail="Invalid destination host")

    final_url = dest
    query = parse_qs(parsed.query, keep_blank_values=True)
    changed = False
    for param, env_var in AFFILIATE_PARAMS.get(provider, []):
        val = (os.getenv(env_var) or "").strip()
        if val and param not in query:
            query[param] = [val]
            changed = True
    if changed:
        final_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    # On redirige tout le monde, mais on ne LOGGE pas les bots (crawlers suivent les liens).
    if not _is_bot(request):
        user_ip = _client_ip(request)
        try:
            ip_obj = ipaddress.ip_address(user_ip)
            if isinstance(ip_obj, ipaddress.IPv4Address):
                anonymized_ip = str(ipaddress.IPv4Network(f"{user_ip}/24", strict=False).network_address)
            else:
                anonymized_ip = str(ipaddress.IPv6Network(f"{user_ip}/48", strict=False).network_address)
        except ValueError:
            anonymized_ip = "0.0.0.0"
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            with conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO public.affiliate_clicks
                       (provider, hotel_code, target_url, user_ip, user_agent, referrer)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (provider, (hotel_code or None), final_url, anonymized_ip,
                     request.headers.get("user-agent", "")[:300],
                     request.headers.get("referer", "")[:300])
                )
        except Exception as _e:
            print(f"[affiliate-redirect] log fail (non-fatal): {_e}")

    return RedirectResponse(url=final_url, status_code=302)


@router.post("/affiliate-log")
@router.get("/affiliate-log")
@limiter.limit("120/minute")
async def affiliate_log(request: Request, provider: str, hotel_code: str = ""):
    """Beacon (navigator.sendBeacon) : logge un clic partenaire DIRECT (ex. Booking
    réécrit côté client par CJ am.js) SANS redirection. Garde la trace serveur dans
    affiliate_clicks → le dashboard /admin-affiliate.html reste complet. 204 No Content."""
    from fastapi import Response
    if provider not in VALID_PROVIDERS or _is_bot(request):
        return Response(status_code=204)  # silencieux ; on ne logge pas les bots
    user_ip = _client_ip(request)
    try:
        ip_obj = ipaddress.ip_address(user_ip)
        if isinstance(ip_obj, ipaddress.IPv4Address):
            anonymized_ip = str(ipaddress.IPv4Network(f"{user_ip}/24", strict=False).network_address)
        else:
            anonymized_ip = str(ipaddress.IPv6Network(f"{user_ip}/48", strict=False).network_address)
    except ValueError:
        anonymized_ip = "0.0.0.0"
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.affiliate_clicks
                   (provider, hotel_code, target_url, user_ip, user_agent, referrer)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (provider, (hotel_code or None), "direct:cj-am.js", anonymized_ip,
                 request.headers.get("user-agent", "")[:300],
                 request.headers.get("referer", "")[:300])
            )
    except Exception as _e:
        print(f"[affiliate-log] log fail (non-fatal): {_e}")
    return Response(status_code=204)


@router.get("/affiliate-stats")
async def affiliate_stats_admin(admin=Depends(require_admin_token)):
    now = datetime.utcnow()
    results = {}
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # NB : _SQL_NO_BOT exclut les crawlers (Meta/Google/Bing…) des stats → chiffres = HUMAINS.
        cur.execute(f"SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s {_SQL_NO_BOT}", (now - timedelta(hours=24),))
        results["total_clicks_24h"] = cur.fetchone()["c"]
        cur.execute(f"SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s {_SQL_NO_BOT}", (now - timedelta(days=7),))
        results["total_clicks_7d"] = cur.fetchone()["c"]
        cur.execute(f"SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s {_SQL_NO_BOT}", (now - timedelta(days=30),))
        results["total_clicks_30d"] = cur.fetchone()["c"]

        cur.execute(f"""
            SELECT provider, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s {_SQL_NO_BOT}
            GROUP BY provider
            ORDER BY clicks DESC
        """, (now - timedelta(days=30),))
        results["by_provider"] = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""
            SELECT hotel_code, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s AND hotel_code IS NOT NULL AND hotel_code <> '' {_SQL_NO_BOT}
            GROUP BY hotel_code
            ORDER BY clicks DESC
            LIMIT 20
        """, (now - timedelta(days=30),))
        results["top_hotels"] = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""
            SELECT destination, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s AND destination IS NOT NULL AND destination <> '' {_SQL_NO_BOT}
            GROUP BY destination
            ORDER BY clicks DESC
            LIMIT 20
        """, (now - timedelta(days=30),))
        results["top_destinations"] = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""
            SELECT TO_CHAR(DATE(ts), 'YYYY-MM-DD') AS date, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s {_SQL_NO_BOT}
            GROUP BY DATE(ts)
            ORDER BY DATE(ts)
        """, (now - timedelta(days=30),))
        results["by_day"] = [dict(r) for r in cur.fetchall()]

        # Quelles PAGES génèrent les clics (module ② — referrer = URL de la fiche SEO).
        cur.execute(f"""
            SELECT referrer, COUNT(*) AS clicks, COUNT(DISTINCT provider) AS partenaires
            FROM public.affiliate_clicks
            WHERE ts >= %s AND referrer IS NOT NULL AND referrer <> '' {_SQL_NO_BOT}
            GROUP BY referrer
            ORDER BY clicks DESC
            LIMIT 25
        """, (now - timedelta(days=30),))
        results["top_pages"] = [dict(r) for r in cur.fetchall()]

        # Total visiteurs uniques (IP /24 anonymisée) sur 30j, hors bots.
        cur.execute(f"SELECT COUNT(DISTINCT user_ip) AS c FROM public.affiliate_clicks WHERE ts >= %s {_SQL_NO_BOT}", (now - timedelta(days=30),))
        results["unique_visitors_30d"] = cur.fetchone()["c"]

        cur.close()
    finally:
        conn.close()

    return results
