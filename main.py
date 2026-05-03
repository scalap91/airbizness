from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import psycopg2, psycopg2.extras, os, stripe, sib_api_v3_sdk, io
from sib_api_v3_sdk.rest import ApiException
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import Optional

load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
BREVO_KEY = os.getenv("BREVO_KEY")

app = FastAPI()
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
    email: str
    origin: str
    destination: str = ""
    max_price: int

@app.get("/deals")
@limiter.limit("60/minute")
def get_deals(request: Request, 
    origin: str = None,
    destination: str = None,
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    trip_type: str = "one_way",
    limit: int = 20
):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    query = "SELECT * FROM deals WHERE 1=1"
    params = []

    if origin:
        query += " AND origin = %s"
        params.append(origin.upper())
    if destination:
        query += " AND destination = %s"
        params.append(destination.upper())

    # Filtre par date exacte
    if date:
        query += " AND DATE(departure_at) = %s"
        params.append(date)
    # Filtre par plage de dates
    elif date_from and date_to:
        query += " AND DATE(departure_at) BETWEEN %s AND %s"
        params.append(date_from)
        params.append(date_to)
    elif date_from:
        query += " AND DATE(departure_at) >= %s"
        params.append(date_from)

    query += " AND expires_at > NOW() ORDER BY score_deal DESC LIMIT %s"
    params.append(limit)
    cur.execute(query, params)
    deals = cur.fetchall()
    cur.close()
    conn.close()
    return {"deals": [dict(d) for d in deals]}

@app.get("/deals/calendar")
def get_calendar(origin: str = "CDG", destination: str = None):
    """Retourne les meilleurs prix par date pour afficher le calendrier"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    query = """
        SELECT DATE(departure_at) as date,
               MIN(price) as min_price,
               COUNT(*) as nb_vols
        FROM deals
        WHERE origin = %s
        AND departure_at IS NOT NULL
    """
    params = [origin.upper()]
    if destination:
        query += " AND destination = %s"
        params.append(destination.upper())
    query += " GROUP BY DATE(departure_at) ORDER BY date ASC"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"calendar": [dict(r) for r in rows]}

@app.get("/stats")
def get_stats():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as total, MIN(price) as min_price, AVG(price) as avg_price FROM deals")
    stats = dict(cur.fetchone())
    cur.close()
    conn.close()
    return stats

@app.post("/create-payment-intent")
def create_payment_intent(req: PaymentIntentRequest):
    intent = stripe.PaymentIntent.create(amount=req.amount, currency=req.currency, metadata={"booking": str(req.booking)}, automatic_payment_methods={"enabled": True})
    return {"client_secret": intent.client_secret}

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

@app.post("/alertes")
def create_alerte(req: AlerteRequest):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO alertes (email, origin, destination, max_price)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (req.email, req.origin.upper(), req.destination.upper() if req.destination else None, req.max_price))
    alerte_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "created", "id": alerte_id}

@app.get("/alertes")
def get_alertes(email: str):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM alertes WHERE email = %s ORDER BY created_at DESC", (email,))
    alertes = [dict(a) for a in cur.fetchall()]
    cur.close()
    conn.close()
    return {"alertes": alertes}

@app.delete("/alertes/{alerte_id}")
def delete_alerte(alerte_id: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM alertes WHERE id = %s", (alerte_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "deleted"}

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
    vol_url = f"https://airbizness.com/vol.html?id={offer_id}"
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

@app.get("/deals/averages")
def get_averages():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT destination, 
               ROUND(AVG(price)) as avg_price,
               ROUND(MAX(price)) as max_price
        FROM deals 
        GROUP BY destination
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["destination"]: int(r["avg_price"]) for r in rows}

@app.get("/deals/diverse")
def get_diverse_deals(origin: str = "CDG", limit: int = 20):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (d.destination) d.*
        FROM deals d
        LEFT JOIN route_stats rs ON d.origin=rs.origin AND d.destination=rs.destination
        WHERE d.origin = %s
        AND d.expires_at > NOW()
        AND (rs.max_price IS NULL OR d.price <= rs.max_price * 0.5)
        ORDER BY d.destination, d.score_deal DESC
        LIMIT %s
    """, (origin.upper(), limit))
    deals = [dict(d) for d in cur.fetchall()]

    # Fallback si pas assez de deals locaux
    if len(deals) < 3:
        cur.execute("""
            SELECT DISTINCT ON (d.destination) d.*
            FROM deals d
            LEFT JOIN route_stats rs ON d.origin=rs.origin AND d.destination=rs.destination
            WHERE d.expires_at > NOW()
            AND (rs.max_price IS NULL OR d.price <= rs.max_price * 0.5)
            ORDER BY d.destination, d.score_deal DESC
            LIMIT %s
        """, (limit,))
        deals = [dict(d) for d in cur.fetchall()]

    cur.close()
    conn.close()
    return {"deals": deals}

@app.get("/deals/by-id")
def get_deal_by_id(offer_id: str):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM deals WHERE offer_id = %s LIMIT 1", (offer_id,))
    deal = cur.fetchone()
    cur.close(); conn.close()
    if not deal:
        return {"deals": []}
    return {"deals": [dict(deal)]}
