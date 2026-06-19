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


@router.get("/affiliate-stats")
def affiliate_stats(hours: int = 24):
    """Stats clics affiliation pour le dashboard admin."""
    if hours < 1 or hours > 720:
        raise HTTPException(400, "hours must be 1-720")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT provider, COUNT(*) AS clicks,
               SUM(price) AS total_price,
               COUNT(DISTINCT user_ip) AS unique_visitors
        FROM affiliate_clicks
        WHERE ts > NOW() - INTERVAL '{int(hours)} hours'
        GROUP BY provider ORDER BY clicks DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {
        "hours": hours,
        "by_provider": [dict(r) for r in rows],
        "total_clicks": sum(r["clicks"] for r in rows),
    }


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

VALID_PROVIDERS = {"booking", "aviasales", "expedia", "agoda", "skyscanner", "hotellook", "trip", "hotels"}
VALID_HOSTS = ("booking.com", "aviasales.com", "expedia.com", "agoda.com", "skyscanner.com", "hotellook.com", "trip.com", "hotels.com", "tp.media", "tpe.travelpayouts.com")

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

    user_ip = request.client.host if request.client else "0.0.0.0"
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


@router.get("/affiliate-stats")
async def affiliate_stats_admin(admin=Depends(require_admin_token)):
    now = datetime.utcnow()
    results = {}
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s", (now - timedelta(hours=24),))
        results["total_clicks_24h"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s", (now - timedelta(days=7),))
        results["total_clicks_7d"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM public.affiliate_clicks WHERE ts >= %s", (now - timedelta(days=30),))
        results["total_clicks_30d"] = cur.fetchone()["c"]

        cur.execute("""
            SELECT provider, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s
            GROUP BY provider
            ORDER BY clicks DESC
        """, (now - timedelta(days=30),))
        results["by_provider"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT hotel_code, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s AND hotel_code IS NOT NULL AND hotel_code <> ''
            GROUP BY hotel_code
            ORDER BY clicks DESC
            LIMIT 20
        """, (now - timedelta(days=30),))
        results["top_hotels"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT destination, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s AND destination IS NOT NULL AND destination <> ''
            GROUP BY destination
            ORDER BY clicks DESC
            LIMIT 20
        """, (now - timedelta(days=30),))
        results["top_destinations"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT TO_CHAR(DATE(ts), 'YYYY-MM-DD') AS date, COUNT(*) AS clicks
            FROM public.affiliate_clicks
            WHERE ts >= %s
            GROUP BY DATE(ts)
            ORDER BY DATE(ts)
        """, (now - timedelta(days=30),))
        results["by_day"] = [dict(r) for r in cur.fetchall()]

        cur.close()
    finally:
        conn.close()

    return results
