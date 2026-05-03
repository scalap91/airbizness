import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import os
import time
from dotenv import load_dotenv
import re
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

load_dotenv()

DUFFEL_TOKEN = os.getenv("DUFFEL_TOKEN")
BREVO_KEY = os.getenv("BREVO_KEY")
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS")
}

CITIES = {
    "CDG":"Paris","LHR":"Londres","AMS":"Amsterdam","FRA":"Francfort",
    "MAD":"Madrid","DXB":"Dubai","SIN":"Singapour","JFK":"New York",
    "NRT":"Tokyo","HKG":"Hong Kong","SYD":"Sydney","LAX":"Los Angeles",
    "BKK":"Bangkok","GRU":"Sao Paulo","DOH":"Doha","ICN":"Seoul",
    "DEL":"Delhi","BOM":"Mumbai","CPT":"Cape Town","NBO":"Nairobi",
    "ORD":"Chicago","MIA":"Miami","SFO":"San Francisco","BOG":"Bogota"
}

# Dates de départ à scanner — J+14, J+30, J+45, J+60, J+90
DEPARTURE_OFFSETS = [7, 14, 21, 30, 45, 60, 90]

ROUTES = [
    ("CDG", "JFK"), ("CDG", "LAX"), ("CDG", "SIN"), ("CDG", "NRT"),
    ("CDG", "DXB"), ("CDG", "HKG"), ("CDG", "BKK"), ("CDG", "GRU"),
    ("CDG", "ORD"), ("CDG", "MIA"), ("CDG", "SFO"), ("CDG", "DEL"),
    ("CDG", "BOM"), ("CDG", "SYD"), ("CDG", "CPT"), ("CDG", "NBO"),
    ("LHR", "JFK"), ("LHR", "DXB"), ("LHR", "SIN"), ("LHR", "HKG"),
    ("LHR", "NRT"), ("LHR", "LAX"), ("LHR", "SYD"), ("LHR", "BKK"),
    ("AMS", "JFK"), ("AMS", "DXB"), ("AMS", "SIN"), ("AMS", "NRT"),
    ("FRA", "JFK"), ("FRA", "DXB"), ("FRA", "SIN"), ("FRA", "NRT"),
    ("MAD", "JFK"), ("MAD", "MIA"), ("MAD", "GRU"), ("MAD", "DXB"),
    ("DXB", "JFK"), ("DXB", "LAX"), ("DXB", "SIN"), ("DXB", "NRT"),
    ("DXB", "LHR"), ("DXB", "SYD"), ("DXB", "BKK"), ("DXB", "HKG"),
    ("SIN", "JFK"), ("SIN", "LAX"), ("SIN", "LHR"), ("SIN", "SYD"),
    ("JFK", "CDG"), ("JFK", "LHR"), ("JFK", "NRT"), ("JFK", "DXB"),
    ("NRT", "JFK"), ("NRT", "LAX"), ("NRT", "LHR"), ("NRT", "CDG"),
    ("HKG", "JFK"), ("HKG", "LAX"), ("HKG", "LHR"), ("HKG", "CDG"),
    ("SYD", "JFK"), ("SYD", "LAX"), ("SYD", "LHR"), ("SYD", "DXB"),
    ("LAX", "CDG"), ("LAX", "LHR"), ("LAX", "NRT"), ("LAX", "SYD"),
    ("BKK", "LHR"), ("BKK", "CDG"), ("BKK", "DXB"), ("BKK", "SYD"),
    ("DOH", "JFK"), ("DOH", "LHR"), ("DOH", "CDG"), ("DOH", "SIN"),
    ("ICN", "JFK"), ("ICN", "LAX"), ("ICN", "LHR"), ("ICN", "CDG"),
]

def parse_duration(d):
    m = re.findall(r'(\d+)H|(\d+)M', d)
    h = int(m[0][0]) if m and m[0][0] else 0
    mn = int(m[-1][1]) if m and m[-1][1] else 0
    return h * 60 + mn

def calculate_score(price, duration_min, stops, ref_price=None):
    if ref_price and ref_price > 0:
        reduction = 1 - (price / ref_price)
        reduction_score = reduction * 10
    else:
        reduction_score = max(0, 10 - (price / 500))
    duration_penalty = duration_min / 300
    stop_penalty = stops * 2
    score = reduction_score - duration_penalty - stop_penalty
    return round(min(10, max(0, score)), 2)

def fetch_offers(origin, destination, departure_date, max_retries=2):
    url = "https://api.duffel.com/air/offer_requests"
    headers = {
        "Authorization": f"Bearer {DUFFEL_TOKEN}",
        "Duffel-Version": "v2",
        "Content-Type": "application/json"
    }
    payload = {"data": {
        "slices": [{"origin": origin, "destination": destination, "departure_date": departure_date}],
        "passengers": [{"type": "adult"}],
        "cabin_class": "business"
    }}
    for attempt in range(max_retries + 1):
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 201:
            return r.json()["data"]["offers"]
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", 30))
            if attempt < max_retries:
                print(f"  429 rate limit pour {origin}->{destination} - sleep {retry_after}s (tentative {attempt+1}/{max_retries})")
                time.sleep(retry_after)
                continue
            else:
                print(f"  429 persistant pour {origin}->{destination} apres {max_retries} retries - skip")
                return []
        print(f"  Erreur {r.status_code} pour {origin}->{destination} le {departure_date}")
        return []
    return []

def save_deals(offers, origin, destination):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    saved = 0
    for offer in offers[:5]:
        try:
            price = float(offer["total_amount"])
            sl = offer["slices"][0]
            seg = sl["segments"][0]
            dur = parse_duration(sl["duration"])
            stops = len(sl["segments"]) - 1
            am = seg["passengers"][0]["cabin"].get("amenities") or {}
            seat = am.get("seat", {}).get("type", "unknown")
            wifi = am.get("wifi", {}).get("available", False)
            score = calculate_score(price, dur, stops)
            # expires_at dynamique : la veille du depart (logique OTA)
            departure_dt = datetime.fromisoformat(seg["departing_at"].replace("Z", "+00:00"))
            expires_at_calc = (departure_dt - timedelta(days=1)).isoformat()
            cur.execute("""
                INSERT INTO deals (offer_id, origin, destination, airline_name, airline_code,
                    price, currency, cabin_class, departure_at, duration_minutes, stops,
                    seat_type, wifi, fare_brand, expires_at, score_deal)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (origin, destination, departure_at, price) DO UPDATE SET offer_id=EXCLUDED.offer_id, score_deal=EXCLUDED.score_deal, expires_at=EXCLUDED.expires_at
            """, (offer["id"], origin, destination,
                offer["owner"]["name"], offer["owner"]["iata_code"],
                price, offer["total_currency"], "business",
                seg["departing_at"], dur, stops, seat, wifi,
                sl.get("fare_brand_name", ""), expires_at_calc, score))
            saved += 1
        except Exception as e:
            print(f"  Erreur: {e}")
    conn.commit()
    cur.close()
    conn.close()
    return saved

def send_alerte_email(email, origin, destination, price, deal_id):
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    from_city = CITIES.get(origin, origin)
    to_city = CITIES.get(destination, destination)
    url = f"https://airbizness.com/vol.html?id={deal_id}"
    html = f"""
    <div style='font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#f0ece4;padding:32px;'>
    <h1 style='color:#d4ae4a;'>AirBizness</h1>
    <h2>Alerte prix declenchee !</h2>
    <div style='background:#161616;border:1px solid #c0392b;padding:20px;margin:20px 0;border-radius:4px;'>
      <p style='font-size:28px;font-weight:bold;'>{origin} vers {destination}</p>
      <p style='color:#a09890;'>{from_city} vers {to_city} - Business Class</p>
      <p style='font-size:48px;font-weight:bold;color:#d4ae4a;'>{int(price)} EUR</p>
    </div>
    <a href='{url}' style='display:block;padding:16px;background:#b8962e;color:#000;text-decoration:none;text-align:center;font-weight:bold;font-size:16px;border-radius:4px;margin-bottom:16px;'>Reserver maintenant</a>
    <hr style='border-color:#333;margin:20px 0;'/>
    <p style='color:#6a6058;font-size:10px;'>AirBizness - airbizness.com</p>
    </div>"""
    try:
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": email}],
            sender={"email": "noreply@airbizness.com", "name": "AirBizness"},
            subject=f"Alerte ! Business Class {from_city} vers {to_city} a {int(price)} EUR",
            html_content=html
        )
        api.send_transac_email(send_smtp_email)
        print(f"  Email alerte envoye a {email}")
        return True
    except ApiException as e:
        print(f"  Erreur email: {e}")
        return False

def check_alertes():
    print("Verification des alertes...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM alertes WHERE active = TRUE AND triggered = FALSE")
    alertes = cur.fetchall()
    cur.close()
    if not alertes:
        conn.close()
        return
    for alerte in alertes:
        cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = "SELECT * FROM deals WHERE origin = %s AND price <= %s"
        params = [alerte["origin"], alerte["max_price"]]
        if alerte["destination"]:
            query += " AND destination = %s"
            params.append(alerte["destination"])
        query += " ORDER BY price ASC LIMIT 1"
        cur2.execute(query, params)
        deal = cur2.fetchone()
        cur2.close()
        if deal:
            sent = send_alerte_email(alerte["email"], deal["origin"], deal["destination"], deal["price"], deal["offer_id"])
            if sent:
                cur3 = conn.cursor()
                cur3.execute("UPDATE alertes SET triggered = TRUE, triggered_price = %s WHERE id = %s", (int(deal["price"]), alerte["id"]))
                conn.commit()
                cur3.close()
    conn.close()

CYCLE_FILE = "/var/www/airbizness/.cycle_count"

def get_and_increment_cycle():
    """Lit le compteur de cycle, l'incremente, et retourne la valeur avant incrementation."""
    try:
        with open(CYCLE_FILE, "r") as f:
            cycle = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        cycle = 0
    with open(CYCLE_FILE, "w") as f:
        f.write(str(cycle + 1))
    return cycle

def classify_routes():
    """Retourne (t1_routes, t2_routes). T1 = routes ayant deja donne un deal -50%."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT d.origin, d.destination
        FROM deals d
        JOIN route_stats rs ON d.origin=rs.origin AND d.destination=rs.destination
        WHERE d.price <= rs.max_price * 0.5
    """)
    t1_set = set(cur.fetchall())
    cur.close()
    conn.close()
    t1 = [r for r in ROUTES if r in t1_set]
    t2 = [r for r in ROUTES if r not in t1_set]
    print(f"Classification: T1={len(t1)} routes (chaque cycle), T2={len(t2)} routes (1 cycle sur 2)")
    return t1, t2

def run_fetcher():
    print(f"Fetch demarre a {datetime.now()}")
    cycle = get_and_increment_cycle()
    t1, t2 = classify_routes()
    if cycle % 2 == 0:
        active_routes = t1 + t2
        print(f"Cycle #{cycle} (pair) -> scan complet : {len(active_routes)} routes")
    else:
        active_routes = t1
        print(f"Cycle #{cycle} (impair) -> scan T1 uniquement : {len(active_routes)} routes")
    for offset in DEPARTURE_OFFSETS:
        departure = (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")
        print(f"\n=== Date : {departure} (J+{offset}) ===")
        for origin, destination in active_routes:
            print(f"  Scan {origin}->{destination}...")
            try:
                offers = fetch_offers(origin, destination, departure)
                if offers:
                    saved = save_deals(offers, origin, destination)
                    print(f"  OK {saved} deals sauves")
            except Exception as e:
                print(f"  ERREUR: {e}")
            import time
            time.sleep(5)
    check_alertes()
    print(f"\nFetch termine a {datetime.now()}")

if __name__ == "__main__":
    run_fetcher()
