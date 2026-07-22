#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tranche 4 (preuve) — réserver DIRECTEMENT depuis une page RateHawk créée.

Part du SLUG de la page (ex. conrad-los-angeles), lit ses données via la même
source que le rendu (get_hotel_unified_data → ratehawk_hid), puis lance la vraie
chaîne RateHawk sur ce hid : hotelpage → prebook → form → finish → status → order.

C'est le « on refait le test RateHawk DIRECT sur cette page » : la page et la
réservation partagent le même hid ; ce qui s'affiche = ce qui se réserve.

Usage : python3 scripts/ratehawk_page_booking_test.py [slug]  (def. conrad-los-angeles)
"""
import os, sys, pathlib, uuid, time, json

ENV = pathlib.Path("/var/www/airbizness/.env")
for _l in ENV.read_text().splitlines():
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip())
os.environ.pop("RATEHAWK_HOST", None)
sys.path.insert(0, "/var/www/airbizness")

from services.hotel_data import get_hotel_unified_data
from providers.ratehawk.client import RateHawkClient, RateHawkError

SLUG = sys.argv[1] if len(sys.argv) > 1 else "conrad-los-angeles"
CI, CO = "2026-09-15", "2026-09-17"


def poll(c, oid):
    for i in range(40):
        try:
            st = c.booking_status(oid)
        except RateHawkError as e:
            m = str(e).lower()
            if "book_limit" in m: return "book_limit"
            if "soldout" in m: return "soldout"
            time.sleep(5); continue
        top = st.get("status"); pc = (st.get("data") or {}).get("percent")
        print(f"    status[{i}] {top} {pc}%", flush=True)
        if top == "ok" and (pc or 0) >= 100: return "ok"
        if top == "error": return "error"
        time.sleep(5)
    return "timeout"


def main():
    # 1. La PAGE : mêmes données que le rendu
    h = get_hotel_unified_data(SLUG)
    if not h:
        sys.exit(f"page introuvable : slug={SLUG}")
    hid = h.get("ratehawk_hid")
    print(f"[page] {h.get('name')} | slug={SLUG} | giata={h.get('giata_code')} | ratehawk_hid={hid} | photos={h.get('total_photos')}")
    if not hid:
        sys.exit("cette page n'est pas servie par RateHawk (pas de ratehawk_hid)")

    c = RateHawkClient()
    # 2. Dispo RateHawk sur le hid de la page
    hp = c.search_hotel_page(int(hid), check_in=CI, check_out=CO, currency="EUR",
                             language="en", residency="gb", guests=[{"adults": 2, "children": []}])
    rates = (((hp.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [])
    print(f"[dispo] {len(rates)} tarif(s) le {CI}→{CO}")
    if not rates:
        sys.exit("aucune dispo sur ce hid pour ces dates")
    rate = rates[0]
    pt0 = ((rate.get("payment_options") or {}).get("payment_types") or [{}])[0]
    print(f"    tarif choisi : {rate.get('room_name')} — {pt0.get('amount')} {pt0.get('currency_code')}")

    # 3. Prebook → 4. form → 5. finish → 6. status → 7. order
    pre = c.prebook(rate.get("book_hash"))
    prate = (((pre.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [{}])[0]
    p_hash = prate.get("book_hash")
    oid = str(uuid.uuid4())
    c.booking_form(partner_order_id=oid, book_hash=p_hash, user_ip="82.29.0.86")
    pt = (prate.get("payment_options") or {}).get("payment_types", [{}])[0]
    payment_type = {"type": pt.get("type", "deposit"), "amount": pt.get("amount"),
                    "currency_code": pt.get("currency_code", "EUR")}
    print(f"[form] order créé — paiement {payment_type['amount']} {payment_type['currency_code']}")
    c.booking_finish(
        partner_order_id=oid,
        rooms=[{"guests": [{"first_name": "Martin", "last_name": "Traveler"},
                           {"first_name": "Sophie", "last_name": "Traveler"}]}],
        user={"email": "demo@airbizness.com", "comment": "", "phone": "12124567899"},
        supplier_data={"first_name_original": "Martin", "last_name_original": "Traveler",
                       "phone": "12124567899", "email": "demo@airbizness.com"},
        payment_type=payment_type)
    final = poll(c, oid)
    print(f"[résa] statut final = {final}")
    if final == "ok":
        for _ in range(6):
            info = c.get_order(oid)
            orders = (info.get("data") or {}).get("orders", [])
            if orders:
                o = orders[0]
                print(f"[CONFIRMÉE] order_id={o.get('order_id')} | statut={o.get('status')} | "
                      f"hôtel={(o.get('hotel_data') or {}).get('id')} | montant={o.get('amount_payable')}")
                break
            time.sleep(4)
    print(f"\n✅ BOUCLE COMPLÈTE : page '{SLUG}' (hid {hid}) → réservation RateHawk {final}")


if __name__ == "__main__":
    main()
