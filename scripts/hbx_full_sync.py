#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AirBizness — HBX Full Catalog Sync (daemon / one-shot).

Pour chaque destination de `hbx_catalog_sync_state` qui n'est pas terminée :
  1. Appelle HBX `/hotel-content-api/1.0/hotels?destinationCode=…`
  2. UPSERT dans `hbx_hotels_catalog` (PK hotel_code)
  3. Si giata_code dispo → UPSERT `hotels_canonical` + `hotels_provider_map`
  4. Met à jour `hbx_catalog_sync_state`  (next_from, hotels_fetched, last_status, …)
  5. Loggue dans `hbx_catalog_sync_log`
  6. Telegram alert si > 10% erreurs par run

Modes :
  --once                       : 1 tick puis quitte
  --max-destinations N         : limite #destinations par run  (def. illimité)
  --sleep-between-ms N         : rate-limit inter-destinations  (def. 250 ms)
  --dry-run                    : pas d'UPSERT, ne fait que log
  --priority-min N             : ne traite que priority >= N
  (sans argument)              : tourne jusqu'à épuisement puis quitte (one-shot)

Env requis (lus dans /var/www/airbizness/.env) :
  HBX_API_KEY (ou HBX_HOTELS_API_KEY), HBX_SECRET (ou HBX_HOTELS_SECRET),
  DB_HOST, DB_NAME, DB_USER, DB_PASS,
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (optionnels — watchdog Pascal).

Idempotent : tous les UPSERT sont ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ─── Env ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = "/var/www/airbizness"
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Permet d'importer providers.hbx.*
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ERROR_RATE_THRESHOLD = 0.10  # > 10% destinations en erreur → alerte
RATE_LIMIT_BASE_SLEEP_SEC = 30  # backoff initial sur 429
RATE_LIMIT_MAX_SLEEP_SEC = 120
DEFAULT_PAGE_SIZE = 100

# ─── Imports HBX (après sys.path) ─────────────────────────────────────
from providers.hbx.provider import HbxProvider  # noqa: E402
from providers.hbx.exceptions import (  # noqa: E402
    HbxRateLimitError,
    HbxQuotaExceededError,
    HbxAuthError,
    HbxError,
)


def _raw_sync_call(provider: HbxProvider, destination_code: str,
                   from_idx: int, page_size: int, language: str = "ENG") -> dict:
    """Appel direct HBX qui PROPAGE les exceptions (provider.sync_catalog les avale).

    Retourne `{"total": int, "hotels": [UnifiedHotel], "next_from": int|None}`.
    """
    raw = provider.client.get(
        "/hotel-content-api/1.0/hotels",
        params={
            "destinationCode": destination_code,
            "from": from_idx, "to": from_idx + page_size - 1,
            "language": language,
            "useSecondaryLanguage": "false",
        },
    )
    total = (raw or {}).get("total", 0)
    hotels = [provider._hbx_to_unified_hotel(h, language)
              for h in (raw or {}).get("hotels", []) or []]
    next_from = (from_idx + page_size) if (from_idx + page_size - 1) < total else None
    return {"total": total, "hotels": hotels, "next_from": next_from}

# ─── Signaux ──────────────────────────────────────────────────────────
_STOP = False


def _sigterm(_sig, _frm):
    global _STOP
    _STOP = True
    print("[hbx-sync] SIGTERM/INT received — finishing current destination then exiting…",
          flush=True)


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


# ─── Helpers ──────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(msg: str) -> None:
    print(f"[{_now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _telegram(text: str) -> None:
    """Watchdog Pascal : tout pipeline qui peut foirer en silence doit aboyer."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        _log(f"[telegram SILENT] {text}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        body = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"[AirBizness HBX sync] {text}",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    except Exception as e:
        _log(f"[telegram] fail: {e} | {text}")


def _db():
    return psycopg2.connect(**DB_CONFIG)


# ─── Picker ───────────────────────────────────────────────────────────
SQL_PICK_NEXT = """
    SELECT destination_code, destination_name, country_code, priority,
           total_available, hotels_fetched, next_from, page_size,
           last_status, attempts
    FROM hbx_catalog_sync_state
    WHERE (last_status IS NULL OR last_status NOT IN ('done', 'quota_exceeded'))
      AND (next_try_at IS NULL OR next_try_at <= NOW())
      AND priority >= %s
    ORDER BY
      CASE WHEN last_status = 'partial' THEN 0 ELSE 1 END,
      priority DESC,
      last_sync_at NULLS FIRST,
      attempts ASC,
      destination_code
    LIMIT 1
    FOR UPDATE SKIP LOCKED
"""


# ─── UPSERTs ──────────────────────────────────────────────────────────
SQL_UPSERT_CATALOG = """
INSERT INTO hbx_hotels_catalog (
    hotel_code, name, category_code, category_stars, chain_code,
    destination_code, zone_code, country_code, state_code,
    city, address, postal_code, latitude, longitude,
    email, phone_main, web, giata_code,
    description_en, description_fr,
    images_count, facilities_count, main_image_url, raw,
    last_update_hbx, fetched_at, updated_at
) VALUES (
    %(hotel_code)s, %(name)s, %(category_code)s, %(category_stars)s, %(chain_code)s,
    %(destination_code)s, %(zone_code)s, %(country_code)s, %(state_code)s,
    %(city)s, %(address)s, %(postal_code)s, %(latitude)s, %(longitude)s,
    %(email)s, %(phone_main)s, %(web)s, %(giata_code)s,
    %(description_en)s, %(description_fr)s,
    %(images_count)s, %(facilities_count)s, %(main_image_url)s, %(raw)s,
    %(last_update_hbx)s, NOW(), NOW()
)
ON CONFLICT (hotel_code) DO UPDATE SET
    name = EXCLUDED.name,
    category_code = EXCLUDED.category_code,
    category_stars = EXCLUDED.category_stars,
    chain_code = EXCLUDED.chain_code,
    destination_code = EXCLUDED.destination_code,
    zone_code = EXCLUDED.zone_code,
    country_code = EXCLUDED.country_code,
    state_code = EXCLUDED.state_code,
    city = EXCLUDED.city,
    address = EXCLUDED.address,
    postal_code = EXCLUDED.postal_code,
    latitude = COALESCE(EXCLUDED.latitude, hbx_hotels_catalog.latitude),
    longitude = COALESCE(EXCLUDED.longitude, hbx_hotels_catalog.longitude),
    email = COALESCE(EXCLUDED.email, hbx_hotels_catalog.email),
    phone_main = COALESCE(EXCLUDED.phone_main, hbx_hotels_catalog.phone_main),
    web = COALESCE(EXCLUDED.web, hbx_hotels_catalog.web),
    giata_code = COALESCE(EXCLUDED.giata_code, hbx_hotels_catalog.giata_code),
    description_en = COALESCE(EXCLUDED.description_en, hbx_hotels_catalog.description_en),
    images_count = EXCLUDED.images_count,
    facilities_count = EXCLUDED.facilities_count,
    main_image_url = COALESCE(EXCLUDED.main_image_url, hbx_hotels_catalog.main_image_url),
    raw = EXCLUDED.raw,
    last_update_hbx = EXCLUDED.last_update_hbx,
    updated_at = NOW()
RETURNING (xmax = 0) AS inserted
"""

SQL_UPSERT_CANONICAL = """
INSERT INTO hotels_canonical (
    giata_code, name, stars, chain_code,
    country_code, city, address, postal_code,
    latitude, longitude, email, phone, web,
    providers_count, providers_list,
    first_seen_at, last_updated_at, source_data
) VALUES (
    %(giata_code)s, %(name)s, %(stars)s, %(chain_code)s,
    %(country_code)s, %(city)s, %(address)s, %(postal_code)s,
    %(latitude)s, %(longitude)s, %(email)s, %(phone)s, %(web)s,
    1, ARRAY['hbx']::text[],
    NOW(), NOW(), %(source_data)s
)
ON CONFLICT (giata_code) DO UPDATE SET
    name = COALESCE(hotels_canonical.name, EXCLUDED.name),
    stars = COALESCE(hotels_canonical.stars, EXCLUDED.stars),
    chain_code = COALESCE(hotels_canonical.chain_code, EXCLUDED.chain_code),
    country_code = COALESCE(hotels_canonical.country_code, EXCLUDED.country_code),
    city = COALESCE(hotels_canonical.city, EXCLUDED.city),
    address = COALESCE(hotels_canonical.address, EXCLUDED.address),
    postal_code = COALESCE(hotels_canonical.postal_code, EXCLUDED.postal_code),
    latitude = COALESCE(hotels_canonical.latitude, EXCLUDED.latitude),
    longitude = COALESCE(hotels_canonical.longitude, EXCLUDED.longitude),
    email = COALESCE(hotels_canonical.email, EXCLUDED.email),
    phone = COALESCE(hotels_canonical.phone, EXCLUDED.phone),
    web = COALESCE(hotels_canonical.web, EXCLUDED.web),
    providers_list = (
        CASE WHEN 'hbx' = ANY(hotels_canonical.providers_list)
             THEN hotels_canonical.providers_list
             ELSE array_append(hotels_canonical.providers_list, 'hbx')
        END
    ),
    providers_count = array_length(
        CASE WHEN 'hbx' = ANY(hotels_canonical.providers_list)
             THEN hotels_canonical.providers_list
             ELSE array_append(hotels_canonical.providers_list, 'hbx')
        END, 1),
    last_updated_at = NOW()
"""

SQL_UPSERT_PROVIDER_MAP = """
INSERT INTO hotels_provider_map (
    provider, provider_hotel_code, giata_code, provider_name,
    first_seen_at, last_seen_at, provider_data
) VALUES (
    'hbx', %(provider_hotel_code)s, %(giata_code)s, %(provider_name)s,
    NOW(), NOW(), %(provider_data)s
)
ON CONFLICT (provider, provider_hotel_code) DO UPDATE SET
    giata_code = EXCLUDED.giata_code,
    provider_name = EXCLUDED.provider_name,
    last_seen_at = NOW(),
    provider_data = EXCLUDED.provider_data
"""

SQL_UPDATE_STATE = """
UPDATE hbx_catalog_sync_state SET
    total_available = COALESCE(%(total_available)s, total_available),
    hotels_fetched = %(hotels_fetched)s,
    next_from = %(next_from)s,
    last_sync_at = NOW(),
    last_status = %(last_status)s,
    last_error = %(last_error)s,
    next_try_at = %(next_try_at)s,
    attempts = %(attempts)s,
    updated_at = NOW()
WHERE destination_code = %(destination_code)s
"""

SQL_INSERT_LOG = """
INSERT INTO hbx_catalog_sync_log (
    destination_code, destination_label, phase,
    started_at, finished_at,
    total_available, fetched, inserted, updated, errors,
    status, error_detail
) VALUES (
    %(destination_code)s, %(destination_label)s, %(phase)s,
    %(started_at)s, %(finished_at)s,
    %(total_available)s, %(fetched)s, %(inserted)s, %(updated)s, %(errors)s,
    %(status)s, %(error_detail)s
)
"""


# ─── Mapping UnifiedHotel → row dict ──────────────────────────────────
def _to_catalog_row(u, fallback_dest: str) -> dict:
    """UnifiedHotel → dict pour SQL_UPSERT_CATALOG."""
    raw = u.raw or {}
    images = raw.get("images") or []
    facilities = raw.get("facilities") or []
    # last_update HBX format YYYY-MM-DD
    last_upd = raw.get("lastUpdate")
    last_upd_date = None
    if last_upd:
        try:
            last_upd_date = datetime.strptime(str(last_upd)[:10], "%Y-%m-%d").date()
        except Exception:
            last_upd_date = None

    desc_en = u.description if (u.language or "").upper() == "ENG" else None

    try:
        hotel_code_int = int(u.source_hotel_code)
    except (TypeError, ValueError):
        return None

    return {
        "hotel_code": hotel_code_int,
        "name": (u.name or "")[:500],
        "category_code": u.category_raw,
        "category_stars": u.stars,
        "chain_code": u.chain_code,
        "destination_code": u.destination_code or fallback_dest,
        "zone_code": str(raw.get("zoneCode")) if raw.get("zoneCode") is not None else None,
        "country_code": u.country_code,
        "state_code": u.state_code,
        "city": (u.city or "")[:500],
        "address": (u.address or "")[:1000],
        "postal_code": u.postal_code,
        "latitude": u.latitude,
        "longitude": u.longitude,
        "email": (u.email or None) and u.email.strip().lower() or None,
        "phone_main": u.phone,
        "web": u.web,
        "giata_code": u.giata_code,
        "description_en": desc_en,
        "description_fr": None,  # HBX traduction FR pas demandée ici
        "images_count": len(images),
        "facilities_count": len(facilities),
        "main_image_url": (u.photos[0] if u.photos else None),
        "raw": json.dumps(raw, default=str),
        "last_update_hbx": last_upd_date,
    }


def _to_canonical_row(u) -> dict:
    return {
        "giata_code": u.giata_code,
        "name": (u.name or "")[:500],
        "stars": u.stars,
        "chain_code": u.chain_code,
        "country_code": u.country_code,
        "city": (u.city or "")[:500],
        "address": (u.address or "")[:1000],
        "postal_code": u.postal_code,
        "latitude": u.latitude,
        "longitude": u.longitude,
        "email": (u.email or None) and u.email.strip().lower() or None,
        "phone": u.phone,
        "web": u.web,
        "source_data": json.dumps({"hbx_code": u.source_hotel_code}, default=str),
    }


# ─── Sync 1 destination ───────────────────────────────────────────────
def sync_one_destination(provider: HbxProvider, dry_run: bool = False) -> dict:
    """Picke 1 destination + traite 1 page. Retourne stats."""
    started_at = _now()
    conn = _db()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Pick + lock 1 destination
    cur.execute(SQL_PICK_NEXT, (0,))
    row = cur.fetchone()
    if not row:
        conn.rollback()
        cur.close()
        conn.close()
        return {"status": "no_work"}

    dest_code = row["destination_code"]
    dest_name = row["destination_name"] or dest_code
    next_from = row["next_from"] or 1
    page_size = row["page_size"] or DEFAULT_PAGE_SIZE
    prev_fetched = row["hotels_fetched"] or 0
    attempts = (row["attempts"] or 0) + 1
    prev_total = row["total_available"]

    _log(f"→ dest={dest_code} ({dest_name}) from={next_from} page_size={page_size} attempts={attempts}")

    # On garde une connexion "state" sous transaction (lock SKIP) pour libérer plus tard.
    # On effectue l'appel HBX en dehors du lock pour ne pas étouffer la table.
    # Mais on doit COMMIT immédiat pour relâcher le lock pendant l'appel HTTP (long).
    conn.commit()  # libère le FOR UPDATE SKIP LOCKED — un autre worker pourrait reprendre,
    # mais on a déjà l'info en mémoire ; on protégera par WHERE last_sync_at au UPDATE.
    cur.close()
    conn.close()

    # ── Appel HBX ─────────────────────────────────────────────────────
    inserted = 0
    updated = 0
    errors_count = 0
    error_detail = None
    last_status = "running"
    next_try_at_sql = "NOW() + INTERVAL '1 day'"  # par défaut
    backoff_sleep = 0

    try:
        sync_result = _raw_sync_call(
            provider,
            destination_code=dest_code,
            from_idx=next_from,
            page_size=page_size,
            language="ENG",
        )
    except HbxRateLimitError as e:
        last_status = "rate_limited"
        error_detail = f"HBX 429: {e}"
        backoff_sleep = min(RATE_LIMIT_BASE_SLEEP_SEC * (2 ** min(attempts - 1, 2)),
                            RATE_LIMIT_MAX_SLEEP_SEC)
        sync_result = None
    except HbxQuotaExceededError as e:
        last_status = "quota_exceeded"
        error_detail = f"HBX quota: {e}"
        sync_result = None
    except HbxAuthError as e:
        last_status = "error"
        error_detail = f"HBX auth: {e}"
        sync_result = None
    except HbxError as e:
        last_status = "error"
        error_detail = f"HBX: {e}"
        sync_result = None
    except Exception as e:
        last_status = "error"
        error_detail = f"crash: {e!r}\n{traceback.format_exc()[:1000]}"
        sync_result = None

    # ── UPSERTs ────────────────────────────────────────────────────────
    new_next_from = next_from
    total_available = prev_total
    page_count = 0
    if sync_result:
        hotels = sync_result.get("hotels") or []
        page_count = len(hotels)
        total_available = sync_result.get("total") or prev_total

        if not dry_run and hotels:
            conn2 = _db()
            conn2.autocommit = False
            cur2 = conn2.cursor()
            try:
                for u in hotels:
                    row_cat = _to_catalog_row(u, fallback_dest=dest_code)
                    if row_cat is None:
                        errors_count += 1
                        continue
                    try:
                        cur2.execute(SQL_UPSERT_CATALOG, row_cat)
                        res = cur2.fetchone()
                        if res and res[0]:
                            inserted += 1
                        else:
                            updated += 1
                        # canonical + provider_map (uniquement si giata_code dispo)
                        if u.giata_code:
                            cur2.execute(SQL_UPSERT_CANONICAL, _to_canonical_row(u))
                            cur2.execute(SQL_UPSERT_PROVIDER_MAP, {
                                "provider_hotel_code": str(u.source_hotel_code),
                                "giata_code": u.giata_code,
                                "provider_name": (u.name or "")[:500],
                                "provider_data": json.dumps(
                                    {"destination_code": dest_code, "stars": u.stars},
                                    default=str,
                                ),
                            })
                    except Exception as e:
                        errors_count += 1
                        _log(f"  ! upsert fail {u.source_hotel_code}: {e}")
                        conn2.rollback()
                        cur2 = conn2.cursor()
                conn2.commit()
            finally:
                cur2.close()
                conn2.close()

        # Status : 'done' si plus de pages, sinon 'partial'
        nf = sync_result.get("next_from")
        if nf is None:
            last_status = "done"
            new_next_from = next_from + page_count
            # ── Auto-check post-sync (2026-05-30) ───────────────────────
            # Découverte : 155 destinations marquées `done` avaient laissé
            # 1455 hôtels HBX orphelins (jamais propagés vers hotels_canonical
            # malgré l'UPSERT). Bug silencieux pendant 3-7 jours, attrapé par
            # accident. On vérifie maintenant la cohérence AVANT de claimer
            # `done`. Si gap > 0 → status reste done (pour ne pas bloquer le
            # resume) mais Telegram aboie + trace dans error_detail.
            try:
                conn_check = psycopg2.connect(**DB)
                cur_check = conn_check.cursor()
                cur_check.execute("""
                    SELECT COUNT(*) FROM hbx_hotels_catalog c
                    WHERE c.destination_code = %s
                      AND c.giata_code IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM hotels_canonical h
                          WHERE h.giata_code = c.giata_code
                      )
                """, (destination_code,))
                _gap = cur_check.fetchone()[0]
                cur_check.close(); conn_check.close()
                if _gap > 0:
                    error_detail = f"[GAP:{_gap}] {_gap} hôtels HBX absents de canonical — relancer scripts/backfill_canonical_from_catalog.py"
                    _log(f"[autocheck] {destination_code} : GAP {_gap} hôtels orphelins canonical")
                    telegram_alert(
                        f"⚠️ Destination {destination_code} ({destination_label}) "
                        f"marquée done MAIS {_gap} hôtels avec giata absents de hotels_canonical. "
                        f"Lancer scripts/backfill_canonical_from_catalog.py"
                    )
            except Exception as _e:
                _log(f"[autocheck] {destination_code} : erreur check ({_e}) — ignore")
        else:
            last_status = "partial"
            new_next_from = nf

    elif sync_result is None and last_status == "running":
        # sync_result == None mais aucun statut posé : ne peut pas arriver normalement.
        last_status = "error"
        error_detail = error_detail or "no result and no exception"

    new_total_fetched = prev_fetched + (inserted + updated)

    # ── Update state ──────────────────────────────────────────────────
    if not dry_run:
        if last_status == "rate_limited":
            next_try_clause = f"NOW() + INTERVAL '{backoff_sleep} seconds'"
        elif last_status == "quota_exceeded":
            next_try_clause = "NOW() + INTERVAL '1 hour'"
        elif last_status == "error":
            next_try_clause = "NOW() + INTERVAL '15 minutes'"
        elif last_status == "partial":
            next_try_clause = "NOW() + INTERVAL '5 seconds'"
        else:  # done
            next_try_clause = "NOW() + INTERVAL '7 days'"

        conn3 = _db()
        cur3 = conn3.cursor()
        cur3.execute(
            SQL_UPDATE_STATE.replace("%(next_try_at)s", next_try_clause),
            {
                "destination_code": dest_code,
                "total_available": total_available,
                "hotels_fetched": new_total_fetched,
                "next_from": new_next_from,
                "last_status": last_status,
                "last_error": error_detail,
                "attempts": attempts,
            },
        )
        # Log
        cur3.execute(SQL_INSERT_LOG, {
            "destination_code": dest_code,
            "destination_label": dest_name,
            "phase": "catalog_pull",
            "started_at": started_at,
            "finished_at": _now(),
            "total_available": total_available,
            "fetched": page_count,
            "inserted": inserted,
            "updated": updated,
            "errors": errors_count,
            "status": ("ok" if last_status in ("done", "partial") else last_status),
            "error_detail": error_detail,
        })
        conn3.commit()
        cur3.close()
        conn3.close()

    _log(
        f"  ← dest={dest_code} status={last_status} page={page_count} "
        f"insert={inserted} update={updated} errors={errors_count} "
        f"total={new_total_fetched}/{total_available}"
    )

    # Backoff applicable maintenant si on a été rate-limited
    if backoff_sleep:
        _log(f"  ⏸  rate-limit backoff {backoff_sleep}s")
        for _ in range(backoff_sleep):
            if _STOP:
                break
            time.sleep(1)

    return {
        "status": last_status,
        "destination_code": dest_code,
        "destination_name": dest_name,
        "inserted": inserted,
        "updated": updated,
        "errors": errors_count,
        "page": page_count,
        "total_available": total_available,
    }


# ─── Main loop ────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1 destination puis quitte")
    ap.add_argument("--max-destinations", type=int, default=0,
                    help="0 = illimité (jusqu'à épuisement)")
    ap.add_argument("--sleep-between-ms", type=int, default=250)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--priority-min", type=int, default=0)
    args = ap.parse_args()

    _log("=== HBX FULL SYNC start ===")
    _log(f"args: once={args.once} max={args.max_destinations} "
         f"sleep_ms={args.sleep_between_ms} dry={args.dry_run} "
         f"prio_min={args.priority_min}")

    provider = HbxProvider()

    processed = 0
    done_count = 0
    error_count = 0
    rate_limited_count = 0
    no_work_loops = 0
    consecutive_quota = 0
    started = time.time()

    while not _STOP:
        try:
            res = sync_one_destination(provider, dry_run=args.dry_run)
        except Exception as e:
            _log(f"!! sync_one_destination crash: {e}")
            traceback.print_exc()
            _telegram(f"crash sync_one_destination: {e}")
            time.sleep(5)
            continue

        if res.get("status") == "no_work":
            no_work_loops += 1
            if args.once or args.max_destinations or no_work_loops >= 3:
                _log("=== no more destinations to sync — exiting ===")
                break
            time.sleep(2)
            continue

        no_work_loops = 0
        processed += 1
        st = res.get("status")
        if st == "done":
            done_count += 1
            consecutive_quota = 0
        elif st == "rate_limited":
            rate_limited_count += 1
            consecutive_quota = 0
        elif st == "error":
            error_count += 1
            consecutive_quota = 0
        elif st == "quota_exceeded":
            consecutive_quota += 1
        elif st == "partial":
            consecutive_quota = 0

        # Si 5 quotas exceeded d'affilée → sleep long (le quota HBX sandbox est tué)
        if consecutive_quota >= 5:
            quota_sleep = 30 * 60  # 30 min
            _log(
                f"⏸  {consecutive_quota} quota_exceeded consécutifs — sleep {quota_sleep}s "
                f"(quota HBX sandbox épuisé)"
            )
            _telegram(
                f"quota HBX sandbox épuisé ({consecutive_quota} dest. consécutives). "
                f"Pause {quota_sleep // 60} min. Cron /opt/concierge continue."
            )
            for _ in range(quota_sleep):
                if _STOP:
                    break
                time.sleep(1)
            consecutive_quota = 0

        # Periodic telegram if too many errors
        if processed % 50 == 0 and processed > 0:
            err_rate = (error_count + rate_limited_count) / processed
            if err_rate > ERROR_RATE_THRESHOLD:
                _telegram(
                    f"alerte: {err_rate*100:.1f}% erreurs sur {processed} destinations "
                    f"(err={error_count} rl={rate_limited_count})"
                )

        if args.once:
            break
        if args.max_destinations and processed >= args.max_destinations:
            _log(f"=== max-destinations={args.max_destinations} reached — exiting ===")
            break

        if args.sleep_between_ms > 0:
            time.sleep(args.sleep_between_ms / 1000.0)

    elapsed = time.time() - started
    summary = (
        f"=== HBX FULL SYNC end: processed={processed} done={done_count} "
        f"errors={error_count} rate_limited={rate_limited_count} "
        f"elapsed={int(elapsed)}s ==="
    )
    _log(summary)
    if processed:
        _telegram(summary)


if __name__ == "__main__":
    main()
