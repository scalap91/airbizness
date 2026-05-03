import subprocess
import psycopg2
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS")
}

AVG = {"JFK":4700,"DXB":3800,"SIN":5800,"NRT":5500,"BKK":5000,"LAX":5200,"HKG":5600,"GRU":5200}
NAMES = {"JFK":"New York","DXB":"Dubai","SIN":"Singapour","NRT":"Tokyo","BKK":"Bangkok","LAX":"Los Angeles","HKG":"Hong Kong","GRU":"Sao Paulo"}

def generate_video(o, d, a, p):
    avg = AVG.get(d, 4500)
    pct = round((1 - float(p)/avg) * 100)
    saved = round(avg - float(p))
    pi = int(float(p))
    ai = int(avg)
    dn = NAMES.get(d, d)
    img = f"/var/www/airbizness/public/images/destinations/{d.lower()}.jpg"
    out = f"/tmp/tiktok_{d}_{int(datetime.now().timestamp())}.mp4"
    vf = ",".join([
        "scale=1080:1920:force_original_aspect_ratio=increase",
        "crop=1080:1920",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.6:t=fill",
        "drawbox=x=0:y=0:w=iw:h=180:color=black@0.85:t=fill",
        "drawbox=x=0:y=1740:w=iw:h=180:color=black@0.85:t=fill",
        f"drawtext=text='AirBizness':fontcolor=#d4ae4a:fontsize=50:x=(w-text_w)/2:y=55:shadowcolor=black:shadowx=2:shadowy=2",
        f"drawtext=text='Business Class Deal':fontcolor=white@0.7:fontsize=26:x=(w-text_w)/2:y=115",
        f"drawtext=text='{o}  ->  {dn}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y=320:shadowcolor=black:shadowx=3:shadowy=3",
        f"drawtext=text='{a}':fontcolor=#d4ae4a:fontsize=32:x=(w-text_w)/2:y=420",
        f"drawtext=text='Business Class':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=560",
        f"drawtext=text='au prix d un vol Economy':fontcolor=#d4ae4a:fontsize=36:x=(w-text_w)/2:y=610",
        f"drawtext=text='{pi} EUR':fontcolor=white:fontsize=150:x=(w-text_w)/2:y=680:shadowcolor=black:shadowx=5:shadowy=5",
        f"drawtext=text='au lieu de {ai} EUR':fontcolor=white@0.6:fontsize=32:x=(w-text_w)/2:y=850",
        "drawbox=x=140:y=920:w=800:h=110:color=#c0392b@0.95:t=fill",
        f"drawtext=text='ECONOMIE DE {pct} POURCENT':fontcolor=white:fontsize=44:x=(w-text_w)/2:y=943:shadowcolor=black:shadowx=2:shadowy=2",
        f"drawtext=text='Vous economisez {saved} EUR':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=1060",
        "drawbox=x=140:y=1760:w=800:h=80:color=#d4ae4a@0.95:t=fill",
        f"drawtext=text='Reserver sur AirBizness.com':fontcolor=black:fontsize=30:x=(w-text_w)/2:y=1782"
    ])
    r = subprocess.run(
        ["ffmpeg","-y","-loop","1","-i",img,"-vf",vf,"-t","12","-c:v","libx264","-pix_fmt","yuv420p","-r","30",out],
        capture_output=True
    )
    if r.returncode == 0:
        print(f"OK: {out}")
        return out
    else:
        print("ERREUR:", r.stderr.decode()[-500:])
        return None

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute("SELECT origin, destination, airline_name, price FROM deals WHERE score_deal >= 6.5 ORDER BY score_deal DESC LIMIT 1")
deal = cur.fetchone()
cur.close()
conn.close()

if deal:
    o, d, a, p = deal
    generate_video(o, d, a, p)
