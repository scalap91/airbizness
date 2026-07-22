from fastapi import APIRouter
import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

router = APIRouter()

def _hotel_mock_enabled() -> bool:
    return (os.getenv("HOTEL_MOCK_MODE","false") or "").strip().lower() in ("1","true","yes","on")

def _duffel_status_payload():
    mode = os.getenv("DUFFEL_MODE", "test")
    dry_run = os.getenv("DUFFEL_BOOKING_DRY_RUN", "true").lower() == "true"

    token = None
    try:
        from providers.duffel import _get_duffel_token
        token = _get_duffel_token()
    except Exception as e:
        return {
            "mode": mode,
            "dry_run": dry_run,
            "token_status": f"INVALID: {e}",
            "token_prefix": None,
            "token_matches_mode": False,
            "api_reachable": False,
            "ready_for_live": False,
        }

    token_prefix = token[:15] if token else None
    token_matches_mode = bool(token and token.startswith(f"duffel_{mode}_"))

    # Test API Duffel (ping airlines list, endpoint léger)
    api_ok = False
    api_error = None
    if token:
        try:
            import urllib.request as _ur
            req = _ur.Request(
                "https://api.duffel.com/air/airlines?limit=1",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Duffel-Version": "v2",
                    "Accept": "application/json",
                },
            )
            with _ur.urlopen(req, timeout=10) as r:
                api_ok = (r.status == 200)
        except Exception as e:
            api_error = str(e)[:200]

    # Audit 2026-05-27 sev 3 #57 : enrich health avec webhook secrets et cache.
    webhook_secret_set = bool(os.getenv("DUFFEL_WEBHOOK_SECRET", ""))
    stripe_webhook_secret_set = bool(os.getenv("STRIPE_WEBHOOK_SECRET", ""))
    cache_count_24h = None
    try:
        _conn = psycopg2.connect(**DB_CONFIG)
        with _conn, _conn.cursor() as _cur:
            _cur.execute(
                "SELECT count(*) FROM flight_offers_cache WHERE created_at > NOW() - INTERVAL '24 hours'"
            )
            cache_count_24h = int(_cur.fetchone()[0] or 0)
        _conn.close()
    except Exception:
        pass

    return {
        "mode": mode,
        "dry_run": dry_run,
        "token_prefix": token_prefix,
        "token_matches_mode": token_matches_mode,
        "api_reachable": api_ok,
        "api_error": api_error,
        "ready_for_live": (mode == "live" and not dry_run and api_ok
                            and token_matches_mode and webhook_secret_set
                            and stripe_webhook_secret_set),
        "duffel_webhook_secret_set": webhook_secret_set,
        "stripe_webhook_secret_set": stripe_webhook_secret_set,
        "flight_offers_cache_24h": cache_count_24h,
    }

def _seo_status_payload():
    poll_interval = int(os.getenv("SEO_POLL_INTERVAL_SEC", "900"))
    payload = {
        "city_seo_content_count": 0,
        "hotel_seo_content_count": 0,
        "city_remaining": 0,
        "hotel_remaining": 0,
        "last_run_at": None,
        "last_run_cities_generated": 0,
        "last_run_hotels_generated": 0,
        "last_run_errors": 0,
        "last_run_duration_seconds": None,
        "next_run_in_seconds": poll_interval,
        "poll_interval_seconds": poll_interval,
    }
    try:
        _conn = psycopg2.connect(**DB_CONFIG)
        with _conn, _conn.cursor() as _cur:
            _cur.execute("SELECT COUNT(*) FROM city_seo_content")
            payload["city_seo_content_count"] = int(_cur.fetchone()[0] or 0)

            _cur.execute("SELECT COUNT(*) FROM hotel_seo_content")
            payload["hotel_seo_content_count"] = int(_cur.fetchone()[0] or 0)

            _cur.execute("""
                SELECT COUNT(*) FROM hbx_destinations d
                WHERE COALESCE(d.is_closed, false) = false
                  AND d.name IS NOT NULL AND d.name <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM city_seo_content c WHERE c.destination_code = d.code
                  )
            """)
            payload["city_remaining"] = int(_cur.fetchone()[0] or 0)

            _cur.execute("""
                SELECT COUNT(*) FROM hotels_canonical h
                WHERE h.slug IS NOT NULL
                  AND h.name IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM hotel_seo_content s WHERE s.giata_code = h.giata_code
                  )
            """)
            payload["hotel_remaining"] = int(_cur.fetchone()[0] or 0)

            _cur.execute("""
                SELECT run_at, cities_generated, hotels_generated, errors, duration_seconds
                FROM seo_generation_log
                ORDER BY run_at DESC
                LIMIT 1
            """)
            r = _cur.fetchone()
            if r:
                payload["last_run_at"] = r[0].isoformat() if r[0] else None
                payload["last_run_cities_generated"] = int(r[1] or 0)
                payload["last_run_hotels_generated"] = int(r[2] or 0)
                payload["last_run_errors"] = int(r[3] or 0)
                payload["last_run_duration_seconds"] = float(r[4]) if r[4] is not None else None
                if r[0]:
                    from datetime import datetime as _dt, timezone as _tz
                    now_ts = _dt.now(_tz.utc)
                    elapsed = (now_ts - r[0]).total_seconds()
                    remaining = max(0, poll_interval - int(elapsed))
                    payload["next_run_in_seconds"] = remaining
        _conn.close()
    except Exception as e:
        payload["error"] = str(e)[:200]
    return payload

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

@router.get("/healthz")
def healthz():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}

@router.get("/api/admin/duffel_status")
def api_admin_duffel_status():
    """Endpoint admin : mode actif Duffel + dry_run + validité token + ping API."""
    return _duffel_status_payload()

@router.get("/admin/duffel_status")
def admin_duffel_status_compat():
    """Alias (nginx strip /api/) — même payload."""
    return _duffel_status_payload()

@router.get("/api/admin/seo/status")
def api_admin_seo_status():
    """Endpoint admin SEO : counts villes/hôtels générés + restants + dernier run."""
    return _seo_status_payload()

@router.get("/admin/seo/status")
def admin_seo_status_compat():
    """Alias (nginx strip /api/) — même payload."""
    return _seo_status_payload()

@router.get("/supervisor/pending")
async def supervisor_pending_proxy(session_id: str = "pascal_default"):
    """Proxy vers concierge-api /api/supervisor/pending."""
    try:
        import os as _os
        import urllib.request as _ur
        import urllib.parse as _up
        import json as _json
        base = _os.getenv("CONCIERGE_API_BASE", "http://127.0.0.1:3000")
        url = f"{base}/api/supervisor/pending?session_id={_up.quote(session_id)}"
        with _ur.urlopen(url, timeout=5) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        # Fail-soft : pas critique
        return {"messages": [], "error": str(e)}

@router.get("/stats")
def get_stats():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as total, MIN(price) as min_price, AVG(price) as avg_price FROM deals")
    stats = dict(cur.fetchone())
    cur.close()
    conn.close()
    return stats

@router.get("/home-stats")
def home_stats():
    """Vrais compteurs catalogue (pour le bandeau défilant de la home). LIVE DB."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM hotels_canonical WHERE slug IS NOT NULL")
        hotels = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT lower(city)) FROM hotels_canonical WHERE city IS NOT NULL")
        cities = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT country_code) FROM hotels_canonical WHERE country_code IS NOT NULL")
        countries = cur.fetchone()[0]
        # Top villes RÉELLES (par nombre d'hôtels) pour le ticker — plus de hardcode.
        cur.execute("""
            SELECT initcap(min(btrim(city))) AS c, COUNT(*) AS n
            FROM hotels_canonical
            WHERE slug IS NOT NULL AND btrim(city) <> ''
            GROUP BY lower(btrim(city))
            ORDER BY n DESC
            LIMIT 5
        """)
        top_cities = [r[0] for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()
    return {"hotels": hotels, "cities": cities, "countries": countries, "top_cities": top_cities}

@router.get("/v2/providers/health")
def v2_providers_health():
    """État de santé de chaque provider activé.

    Health honnête : on tente une VRAIE search (PAR, J+30 → J+33, 1 adulte)
    pour HBX, plutôt qu'un ping /status qui peut être OK même quand
    le quota search est épuisé. Pour Duffel, on tente un offer_request test.
    """
    from datetime import datetime as _dt, timedelta as _td
    from providers.base import HotelQuery
    providers = _get_active_hotel_providers()
    today = _dt.utcnow().date()
    test_ci = (today + _td(days=30)).isoformat()
    test_co = (today + _td(days=33)).isoformat()

    out = []
    for p in providers:
        info = {
            "name": p.name,
            "is_affiliate": p.is_affiliate,
            "sandbox": getattr(p, "sandbox", False),
            "has_content": p.has_content,
            "has_booking": p.has_booking,
            "coverage": p.coverage,
        }
        # Health par provider — pour HBX on fait une VRAIE search test
        if p.name == "hbx":
            try:
                offers = p.search(HotelQuery(
                    destination="PAR", check_in=test_ci, check_out=test_co,
                    guests=1, rooms=1, stars_min=1,
                ))
                if offers:
                    info["health"] = {
                        "ok": True, "search_test_offers": len(offers),
                        "destination_test": "PAR",
                    }
                    info["healthy"] = True
                else:
                    info["health"] = {
                        "ok": False,
                        "reason": "HBX quota or search empty",
                        "search_test_offers": 0,
                        "destination_test": "PAR",
                    }
                    info["healthy"] = False
            except Exception as e:
                info["health"] = {
                    "ok": False,
                    "reason": f"HBX search failed: {e}",
                    "destination_test": "PAR",
                }
                info["healthy"] = False
        else:
            try:
                h = p.health()
                info["health"] = h
                info["healthy"] = bool(h.get("ok"))
            except Exception as e:
                info["health"] = {"ok": False, "reason": str(e)}
                info["healthy"] = False

        out.append(info)

    # Health Duffel (vol) : VRAIE offer_request test CDG→ORY J+30, plus honnête
    # qu'un ping /airlines qui peut être OK même si offer_request fail.
    try:
        from providers.duffel import search_offers_live as _duffel_search
        from datetime import datetime as _dt2, timedelta as _td2
        td = (_dt2.utcnow().date() + _td2(days=30)).isoformat()
        duffel_h: dict
        try:
            offers = _duffel_search(origin="CDG", destination="ORY",
                                    departure_date=td, passengers=1) or []
            ok = len(offers) > 0
            duffel_h = {
                "ok": ok,
                "search_test_offers": len(offers),
                "route_test": "CDG-ORY",
                "reason": None if ok else "Duffel search returned 0 offers",
            }
        except TypeError:
            duffel_h = {"ok": False, "reason": "duffel search signature mismatch"}
        except Exception as ie:
            duffel_h = {"ok": False, "reason": f"duffel search error: {ie}"}
        out.append({
            "name": "duffel",
            "is_affiliate": False,
            "sandbox": True,
            "has_content": False,
            "has_booking": True,
            "coverage": ["global"],
            "health": duffel_h,
            "healthy": bool(duffel_h.get("ok")),
        })
    except Exception as e:
        out.append({
            "name": "duffel",
            "is_affiliate": False,
            "sandbox": True,
            "has_content": False,
            "has_booking": True,
            "coverage": ["global"],
            "health": {"ok": False, "reason": f"duffel module load failed: {e}"},
            "healthy": False,
        })

    # Flag global mock — utile pour la console admin
    return {
        "providers": out,
        "hotel_mock_mode": _hotel_mock_enabled(),
        "duffel_booking_dry_run": (os.getenv("DUFFEL_BOOKING_DRY_RUN", "") or "").lower() in ("1","true","yes","on"),
    }
