#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AirBizness — RateHawk (ETG) Full Catalog Sync.

Tranche 1 du pipeline « pages hôtel RateHawk » (miroir de hbx_full_sync.py).

1. Demande le dump statique complet : POST /hotel/info/dump/ {inventory:'all', language}
   → renvoie l'URL d'un fichier .jsonl.zst (JSON Lines, zstd).
2. Télécharge + décompresse (zstd CLI).
3. UPSERT chaque hôtel dans `ratehawk_hotels_catalog` (PK = hid RateHawk).

⚠️ Le dump RateHawk N'A PAS de giata (contrairement à HBX). L'identité canonique
(match nom+géo avec HBX, ou clé de repli rh-<hid>) = tranche 2, PAS ici.

Modes :
  --once / (défaut)   : télécharge le dump courant et sync tout
  --limit N           : ne traite que N hôtels (tests)
  --language xx        : langue du contenu (def. en ; en sandbox, seul 'en' a des données)
  --dry-run           : parse seulement, aucun UPSERT

Env (lus dans /var/www/airbizness/.env) : RATEHAWK_KEY_ID, RATEHAWK_KEY_SECRET,
RATEHAWK_ENV, DB_HOST, DB_NAME, DB_USER, DB_PASS.

Idempotent : ON CONFLICT (hid) DO UPDATE.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile, time, urllib.request, pathlib

# ── env ────────────────────────────────────────────────────────────────────
ENV = pathlib.Path("/var/www/airbizness/.env")
for _l in ENV.read_text().splitlines():
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, "/var/www/airbizness")
import psycopg2
from psycopg2.extras import execute_batch
from providers.ratehawk.client import RateHawkClient, RateHawkError

DB = {"host": os.getenv("DB_HOST"), "dbname": os.getenv("DB_NAME"),
      "user": os.getenv("DB_USER"), "password": os.getenv("DB_PASS")}

DDL = """
CREATE TABLE IF NOT EXISTS ratehawk_hotels_catalog (
    hid            BIGINT PRIMARY KEY,
    rh_id          TEXT,
    name           TEXT,
    address        TEXT,
    postal_code    TEXT,
    country_code   TEXT,
    region_id      BIGINT,
    region_name    TEXT,
    latitude       DOUBLE PRECISION,
    longitude      DOUBLE PRECISION,
    star_rating    INTEGER,
    hotel_chain    TEXT,
    kind           TEXT,
    main_image_url TEXT,
    images_count   INTEGER,
    amenities_count INTEGER,
    raw            JSONB,
    first_seen_at  TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rh_catalog_country ON ratehawk_hotels_catalog(country_code);
CREATE INDEX IF NOT EXISTS idx_rh_catalog_region  ON ratehawk_hotels_catalog(region_id);
"""

UPSERT = """
INSERT INTO ratehawk_hotels_catalog (
    hid, rh_id, name, address, postal_code, country_code, region_id, region_name,
    latitude, longitude, star_rating, hotel_chain, kind, main_image_url,
    images_count, amenities_count, raw, first_seen_at, last_seen_at
) VALUES (
    %(hid)s, %(rh_id)s, %(name)s, %(address)s, %(postal_code)s, %(country_code)s,
    %(region_id)s, %(region_name)s, %(latitude)s, %(longitude)s, %(star_rating)s,
    %(hotel_chain)s, %(kind)s, %(main_image_url)s, %(images_count)s,
    %(amenities_count)s, %(raw)s, NOW(), NOW()
)
ON CONFLICT (hid) DO UPDATE SET
    rh_id=EXCLUDED.rh_id, name=EXCLUDED.name, address=EXCLUDED.address,
    postal_code=EXCLUDED.postal_code, country_code=EXCLUDED.country_code,
    region_id=EXCLUDED.region_id, region_name=EXCLUDED.region_name,
    latitude=COALESCE(EXCLUDED.latitude, ratehawk_hotels_catalog.latitude),
    longitude=COALESCE(EXCLUDED.longitude, ratehawk_hotels_catalog.longitude),
    star_rating=EXCLUDED.star_rating, hotel_chain=EXCLUDED.hotel_chain,
    kind=EXCLUDED.kind, main_image_url=EXCLUDED.main_image_url,
    images_count=EXCLUDED.images_count, amenities_count=EXCLUDED.amenities_count,
    raw=EXCLUDED.raw, last_seen_at=NOW()
"""


def dump_url(language: str) -> tuple[str, str]:
    c = RateHawkClient()
    r = c._request("POST", "/hotel/info/dump/", json_body={"inventory": "all", "language": language})
    d = r.get("data") or {}
    return d.get("url"), d.get("last_update")


def download_decompress(url: str) -> str:
    """Télécharge le .zst et renvoie le chemin du .jsonl décompressé."""
    tmpd = tempfile.mkdtemp(prefix="rh_dump_")
    zst = os.path.join(tmpd, "dump.jsonl.zst")
    jsonl = os.path.join(tmpd, "dump.jsonl")
    print(f"[dl] {url}", flush=True)
    urllib.request.urlretrieve(url, zst)
    print(f"[dl] {os.path.getsize(zst)//1024} KB → décompression zstd", flush=True)
    subprocess.run(["zstd", "-d", "-f", zst, "-o", jsonl], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonl


def to_row(d: dict) -> dict:
    reg = d.get("region") or {}
    imgs = d.get("images") or []
    main_img = ""
    if imgs:
        main_img = (imgs[0] or "").replace("{size}", "1024x768") if isinstance(imgs[0], str) else ""
    return {
        "hid": d.get("hid"), "rh_id": d.get("id"), "name": d.get("name"),
        "address": d.get("address"), "postal_code": d.get("postal_code"),
        "country_code": reg.get("country_code"), "region_id": reg.get("id"),
        "region_name": reg.get("name"), "latitude": d.get("latitude"),
        "longitude": d.get("longitude"), "star_rating": d.get("star_rating"),
        "hotel_chain": d.get("hotel_chain") or None, "kind": d.get("kind"),
        "main_image_url": main_img, "images_count": len(imgs),
        "amenities_count": len(d.get("amenity_groups") or []),
        "raw": json.dumps(d, ensure_ascii=False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--language", default="en")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url, last_update = dump_url(args.language)
    if not url:
        sys.exit("Pas d'URL de dump renvoyée")
    print(f"[dump] last_update={last_update}", flush=True)
    jsonl = download_decompress(url)

    if not args.dry_run:
        conn = psycopg2.connect(**DB); conn.autocommit = False
        cur = conn.cursor()
        cur.execute(DDL); conn.commit()

    batch, total, upserted = [], 0, 0
    t0 = time.time()
    with open(jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("hid") is None:
                continue
            total += 1
            batch.append(to_row(d))
            if args.limit and total >= args.limit:
                break
            if not args.dry_run and len(batch) >= 500:
                execute_batch(cur, UPSERT, batch); conn.commit()
                upserted += len(batch); batch = []
    if not args.dry_run and batch:
        execute_batch(cur, UPSERT, batch); conn.commit()
        upserted += len(batch)

    dt = time.time() - t0
    print(f"[ok] {total} hôtels parsés, {upserted} upsertés en {dt:.1f}s "
          f"({'DRY-RUN' if args.dry_run else 'ratehawk_hotels_catalog'})", flush=True)
    if not args.dry_run:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT country_code) FROM ratehawk_hotels_catalog")
        n, nc = cur.fetchone()
        print(f"[db] ratehawk_hotels_catalog = {n} hôtels, {nc} pays", flush=True)
        cur.close(); conn.close()


if __name__ == "__main__":
    main()
