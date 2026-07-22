#!/usr/bin/env python3
"""RateHawk — harnais de certification (sandbox).

Joue les scénarios OBLIGATOIRES de la doc ETG (fundamentals/sandbox → Test cases) et
écrit un log JSON par scénario (requête partenaire + réponse ETG à chaque étape).

Chaîne : search/hp → prebook → booking/form → booking/finish → poll finish/status → order/info.
Polling RÉSILIENT : tolère les erreurs "unknown" transitoires (cas certif) et ne s'arrête
qu'à un état terminal (ok / soldout / book_limit).
"""
import os, sys, pathlib, json, uuid, time
ENV = pathlib.Path("/var/www/airbizness/.env")
for line in ENV.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
os.environ.pop("RATEHAWK_HOST", None)
sys.path.insert(0, "/var/www/airbizness")
from providers.ratehawk.client import RateHawkClient, RateHawkError

CI, CO = "2026-09-15", "2026-09-17"
OUTDIR = pathlib.Path(__file__).parent / "cert_logs"
OUTDIR.mkdir(exist_ok=True)


_ADULT_NAMES = ["Martin", "Eliot", "Peter", "Mary", "Anna", "Paul", "Sophie", "John"]
_CHILD_NAMES = ["Leo", "Emma", "Lucas", "Chloe", "Nina", "Tom"]

def room_guests(adults, child_ages):
    """Voyageurs d'une chambre : adultes {first,last} + enfants {first,last,is_child,age}.
    NB ETG : les noms n'acceptent QUE des lettres/espaces/-,.' — jamais de chiffres."""
    g = [{"first_name": _ADULT_NAMES[i % len(_ADULT_NAMES)], "last_name": "Traveler"} for i in range(adults)]
    for j, age in enumerate(child_ages):
        g.append({"first_name": _CHILD_NAMES[j % len(_CHILD_NAMES)], "last_name": "Traveler",
                  "is_child": True, "age": age})
    return g


def poll_status(client, oid, log, max_iter=40, sleep_s=5):
    """Poll finish/status. Résilient aux erreurs unknown. Retourne l'état terminal."""
    for i in range(max_iter):
        try:
            st = client.booking_status(oid)
        except RateHawkError as e:
            msg = str(e).lower()
            log.append({"step": f"status_poll[{i}]", "transient_error": str(e)})
            if "book_limit" in msg: return "book_limit", None
            if "soldout" in msg or "sold_out" in msg: return "soldout", None
            time.sleep(sleep_s); continue  # erreur unknown transitoire → on continue à poller
        top = st.get("status"); data = st.get("data") or {}
        log.append({"step": f"status_poll[{i}]", "top_status": top, "percent": data.get("percent")})
        if top == "ok" and (data.get("percent") or 0) >= 100:
            return "ok", st
        if top == "error":
            err = (st.get("error") or "").lower()
            if "book_limit" in err: return "book_limit", st
            if "soldout" in err: return "soldout", st
            return "error:" + err, st
        time.sleep(sleep_s)
    return "timeout", None


def run_booking(name, hid, guests_search, rooms, residency, oid_suffix="", price_inc=20):
    client = RateHawkClient()
    client.enable_trace()  # certif : capture requête partenaire + réponse ETG de CHAQUE appel
    oid = str(uuid.uuid4())
    if oid_suffix:
        oid = oid + "_" + oid_suffix  # certif : suffixe déclencheur d'erreur
    log = {"scenario": name, "partner_order_id": oid, "params": {
        "hid": hid, "residency": residency, "guests": guests_search, "rooms": rooms, "price_increase_percent": price_inc}, "steps": []}
    log["api_trace"] = client._trace  # référence : se remplit au fil des appels (tous chemins de sortie)
    try:
        # 1) hotelpage pour l'occupation demandée
        hp = client.search_hotel_page(hid, check_in=CI, check_out=CO, currency="EUR",
                                      language="en", residency=residency, guests=guests_search)
        rates = (((hp.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [])
        log["steps"].append({"step": "hotelpage", "status": hp.get("status"), "rates_found": len(rates)})
        if not rates:
            log["result"] = "NO_RATES"; return log, oid, client
        h_hash = rates[0].get("book_hash")
        # 2) prebook
        pre = client.prebook(h_hash, price_increase_percent=price_inc)
        prate = (((pre.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [{}])[0]
        p_hash = prate.get("book_hash")
        pay = ((prate.get("payment_options") or {}).get("payment_types") or [{}])[0]
        log["steps"].append({"step": "prebook", "status": pre.get("status"),
                             "changed": (pre.get("data") or {}).get("changed"),
                             "price": pay.get("amount"), "currency": pay.get("currency_code")})
        # 3) form
        form = client.booking_form(partner_order_id=oid, book_hash=p_hash, user_ip="82.29.0.86")
        fpts = (form.get("data") or {}).get("payment_types") or [pay]
        pt = fpts[0]
        payment_type = {"type": pt.get("type", "deposit"), "amount": pt.get("amount"),
                        "currency_code": pt.get("currency_code", "EUR")}
        log["steps"].append({"step": "booking_form", "status": form.get("status"),
                             "order_id": (form.get("data") or {}).get("order_id"), "payment_type": payment_type})
        # 4) finish (voyageurs réels, avec enfants/âges)
        try:
            fin = client.booking_finish(
                partner_order_id=oid, rooms=rooms,
                user={"email": "cert@airbizness.com", "comment": "", "phone": "12124567899"},
                supplier_data={"first_name_original": "Adult1", "last_name_original": "Test",
                               "phone": "12124567899", "email": "cert@airbizness.com"},
                payment_type=payment_type)
            log["steps"].append({"step": "booking_finish", "status": fin.get("status")})
        except RateHawkError as e:
            # cas certif unknown_* : erreur unknown injectée → résa EN COURS, on poll.
            # Sinon (vraie erreur de validation) → fatal, pas d'attente inutile.
            log["steps"].append({"step": "booking_finish", "transient_error": str(e)})
            if not oid_suffix:
                log["final_status"] = "finish_error:" + str(e); log["result"] = "ERROR"
                return log, oid, client
        # 5) poll résilient
        final, _ = poll_status(client, oid, log["steps"])
        log["final_status"] = final
        # 6) order info si abouti — retry : l'ordre peut mettre quelques secondes à être lisible
        if final == "ok":
            for _try in range(6):
                info = client.get_order(oid)
                orders = (info.get("data") or {}).get("orders", [])
                if orders:
                    o = orders[0]
                    log["order"] = {"order_id": o.get("order_id"), "status": o.get("status"),
                                    "hotel": (o.get("hotel_data") or {}).get("id"),
                                    "amount": o.get("amount_payable")}
                    break
                time.sleep(4)
        log["result"] = "PASS" if final in ("ok", "soldout", "book_limit") else "CHECK"
    except RateHawkError as e:
        log["steps"].append({"step": "fatal", "error": str(e)})
        log["result"] = "ERROR"
    return log, oid, client


SCENARIOS = [
    # (nom, hid, guests_search, rooms, residency, oid_suffix, price_inc)
    ("2_booking_with_children", 10004834,
     [{"adults": 2, "children": [0, 17]}],
     [{"guests": room_guests(2, [0, 17])}], "gb", "", 20),
    ("3_uzbekistan_citizenship", 10004834,
     [{"adults": 2, "children": []}],
     [{"guests": room_guests(2, [])}], "uz", "", 20),
    ("1_multiroom_mixed", 10004834,
     [{"adults": 2, "children": [3]}, {"adults": 2, "children": [1, 5, 17]}],
     [{"guests": room_guests(2, [3])}, {"guests": room_guests(2, [1, 5, 17])}], "gb", "", 20),
    ("4_price_increase_prebook", 8819557,
     [{"adults": 2, "children": []}],
     [{"guests": room_guests(2, [])}], "gb", "", 10),
    ("5_unknown_success", 10004834,
     [{"adults": 2, "children": []}],
     [{"guests": room_guests(2, [])}], "gb", "unknown_success", 20),
    ("6_unknown_soldout", 10004834,
     [{"adults": 2, "children": []}],
     [{"guests": room_guests(2, [])}], "gb", "unknown_soldout", 20),
    ("7_unknown_book_limit", 10004834,
     [{"adults": 2, "children": []}],
     [{"guests": room_guests(2, [])}], "gb", "unknown_book_limit", 20),
]

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    summary = []
    for (name, hid, gs, rooms, res, suf, pinc) in SCENARIOS:
        if only and only not in name:
            continue
        print(f"\n===== {name} =====", flush=True)
        log, oid, _ = run_booking(name, hid, gs, rooms, res, suf, pinc)
        (OUTDIR / f"{name}.json").write_text(json.dumps(log, ensure_ascii=False, indent=2))
        print(f"  result={log.get('result')} final={log.get('final_status')} order={log.get('order')}", flush=True)
        summary.append((name, log.get("result"), log.get("final_status")))
    print("\n======== RÉCAP CERTIF ========")
    for n, r, f in summary:
        print(f"  {r:6} | {f or '-':12} | {n}")
