#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AirBizness — RateHawk → canonical (tranche 2 du pipeline pages RateHawk).

Pour chaque hôtel de `ratehawk_hotels_catalog` :
  - MATCH avec un hôtel HBX existant dans `hotels_canonical` (nom + géo + pays,
    faute de giata commun) → on ATTACHE RateHawk à la page existante :
        providers_list += 'ratehawk'  +  ligne hotels_provider_map (giata HBX).
    → même page, même URL ; 2e source de dispo/prix + secours quota HBX.
  - PAS de match → NOUVELLE ligne canonique clé de repli `rh-<hid>` (slug généré,
    providers_list=['ratehawk']) + hotels_provider_map. → nouvelle page.

Conservateur : on ne FUSIONNE que si on est sûr (géo très proche + nom proche),
car un mauvais merge corromprait une page HBX existante. Dans le doute → nouvelle page.

Modes : --dry-run (aucune écriture) · --limit N · --country XX

Env : DB_HOST/DB_NAME/DB_USER/DB_PASS (via /var/www/airbizness/.env).
"""
from __future__ import annotations
import argparse, math, os, re, unicodedata, pathlib
from collections import defaultdict

ENV = pathlib.Path("/var/www/airbizness/.env")
for _l in ENV.read_text().splitlines():
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip())

import psycopg2
import psycopg2.extras
import difflib

DB = {"host": os.getenv("DB_HOST"), "dbname": os.getenv("DB_NAME"),
      "user": os.getenv("DB_USER"), "password": os.getenv("DB_PASS")}

# Seuils de match (conservateurs) : on n'attache à une page HBX que si on est sûr.
MAX_MERGE_KM = 0.20     # 200 m
MIN_NAME_SIM = 0.72     # ressemblance globale (jaccard tokens OU difflib)
MIN_JACCARD = 0.50      # recouvrement des tokens DISTINCTIFS (tue les faux positifs
                        # type « X Paris » ~ « Y Paris » qui ne partagent que la ville)

_STOP = {"hotel", "hotels", "the", "le", "la", "les", "de", "du", "des", "spa",
         "resort", "apartments", "apartment", "residence", "by", "inn", "suites",
         "suite", "maison", "guesthouse", "hostel", "rooms", "room", "appart", "and"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def tokens(s):
    return {t for t in norm(s).split() if t and t not in _STOP and len(t) > 1}


def name_sim(a_tok, a_key, b_tok, b_key):
    if not a_tok or not b_tok:
        return 0.0
    jac = len(a_tok & b_tok) / len(a_tok | b_tok)
    rat = difflib.SequenceMatcher(None, a_key, b_key).ratio()
    return max(jac, rat)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower().strip())
    return re.sub(r"-+", "-", s).strip("-")[:50]


def unique_slug(base, taken):
    base = base or "hotel"
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


SQL_UPDATE_PROVIDERS = """
UPDATE hotels_canonical SET
  providers_list = CASE WHEN 'ratehawk' = ANY(providers_list) THEN providers_list
                        ELSE array_append(providers_list, 'ratehawk') END,
  providers_count = array_length(CASE WHEN 'ratehawk' = ANY(providers_list) THEN providers_list
                        ELSE array_append(providers_list, 'ratehawk') END, 1),
  last_updated_at = NOW()
WHERE giata_code = %s
"""

SQL_INSERT_CANONICAL = """
INSERT INTO hotels_canonical (
  giata_code, name, stars, chain_code, country_code, city, address, postal_code,
  latitude, longitude, providers_count, providers_list,
  first_seen_at, last_updated_at, slug, best_photo_url, total_photos
) VALUES (
  %(giata_code)s, %(name)s, %(stars)s, %(chain_code)s, %(country_code)s, %(city)s,
  %(address)s, %(postal_code)s, %(latitude)s, %(longitude)s, 1,
  ARRAY['ratehawk']::text[], NOW(), NOW(), %(slug)s, %(best_photo_url)s, %(total_photos)s
) ON CONFLICT (giata_code) DO NOTHING
"""

SQL_UPSERT_PROVIDER_MAP = """
INSERT INTO hotels_provider_map (
  provider, provider_hotel_code, giata_code, provider_name, first_seen_at, last_seen_at, provider_data
) VALUES ('ratehawk', %(hid)s, %(giata_code)s, %(name)s, NOW(), NOW(), %(provider_data)s)
ON CONFLICT (provider, provider_hotel_code) DO UPDATE SET
  giata_code = EXCLUDED.giata_code, last_seen_at = NOW()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--country", default=None)
    args = ap.parse_args()

    conn = psycopg2.connect(**DB); conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. HBX canonical (candidats de match) indexés par pays, avec géo
    cur.execute("""SELECT giata_code, name, city, country_code, latitude, longitude, address
                   FROM hotels_canonical
                   WHERE latitude IS NOT NULL AND longitude IS NOT NULL""")
    hbx_by_country = defaultdict(list)
    for r in cur.fetchall():
        r = dict(r)
        r["_tok"] = tokens(r["name"]); r["_key"] = " ".join(sorted(r["_tok"]))
        hbx_by_country[r["country_code"]].append(r)

    # slugs déjà pris
    cur.execute("SELECT slug FROM hotels_canonical WHERE slug IS NOT NULL")
    taken = {r["slug"] for r in cur.fetchall()}

    # 2. hôtels RateHawk à traiter
    q = "SELECT hid, rh_id, name, address, postal_code, country_code, region_name, latitude, longitude, star_rating, hotel_chain, main_image_url, images_count FROM ratehawk_hotels_catalog"
    params = []
    if args.country:
        q += " WHERE country_code = %s"; params.append(args.country)
    cur.execute(q, params)
    rh = [dict(r) for r in cur.fetchall()]

    import json
    matched = created = skipped = 0
    for i, h in enumerate(rh):
        if args.limit and i >= args.limit:
            break
        hid = h["hid"]
        pdata = json.dumps({"hid": hid, "rh_id": h["rh_id"], "name": h["name"]}, ensure_ascii=False)
        # ── recherche d'un match HBX (même pays, géo proche, nom proche) ──
        best = None; best_km = 999
        if h["latitude"] and h["longitude"]:
            htok = tokens(h["name"]); hkey = " ".join(sorted(htok))
            for cand in hbx_by_country.get(h["country_code"], []):
                if abs(cand["latitude"] - h["latitude"]) > 0.004 or abs(cand["longitude"] - h["longitude"]) > 0.006:
                    continue
                km = haversine(h["latitude"], h["longitude"], cand["latitude"], cand["longitude"])
                if km > MAX_MERGE_KM:
                    continue
                jac = len(htok & cand["_tok"]) / len(htok | cand["_tok"]) if (htok and cand["_tok"]) else 0
                if jac < MIN_JACCARD or name_sim(htok, hkey, cand["_tok"], cand["_key"]) < MIN_NAME_SIM:
                    continue
                if km < best_km:
                    best_km = km; best = cand

        if best:  # ── CAS 1 : attacher à la page HBX existante ──
            if not args.dry_run:
                cur.execute(SQL_UPDATE_PROVIDERS, (best["giata_code"],))
                cur.execute(SQL_UPSERT_PROVIDER_MAP, {"hid": hid, "giata_code": best["giata_code"], "name": h["name"], "provider_data": pdata})
            matched += 1
        else:      # ── CAS 2 : nouvelle page canonique rh-<hid> ──
            giata = f"rh-{hid}"
            slug = unique_slug(slugify(h["name"]), taken); taken.add(slug)
            if not args.dry_run:
                cur.execute(SQL_INSERT_CANONICAL, {
                    "giata_code": giata, "name": h["name"], "stars": h["star_rating"],
                    "chain_code": h["hotel_chain"], "country_code": h["country_code"],
                    "city": h["region_name"], "address": h["address"], "postal_code": h["postal_code"],
                    "latitude": h["latitude"], "longitude": h["longitude"],
                    "slug": slug, "best_photo_url": h["main_image_url"], "total_photos": h["images_count"],
                })
                cur.execute(SQL_UPSERT_PROVIDER_MAP, {"hid": hid, "giata_code": giata, "name": h["name"], "provider_data": pdata})
            created += 1
        if not args.dry_run and (i + 1) % 200 == 0:
            conn.commit()

    if not args.dry_run:
        conn.commit()
    print(f"[tranche2] RateHawk traités={matched+created+skipped} | "
          f"attachés à page HBX={matched} | nouvelles pages rh-={created} "
          f"{'(DRY-RUN)' if args.dry_run else ''}")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
