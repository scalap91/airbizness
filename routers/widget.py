"""
Widget vol embeddable — migré de main.py 2026-06-01 (3e module sur 13). Pascal/orchestrateur DeepSeek.

L'hôtelier copie un snippet sur son site :
  <script src="https://airbizness.com/widget/v1/airbizness.js"></script>
  <div id="airbizness-flight-widget"
       data-hotel-code="987654" data-airport="RAK"></div>

Le visiteur clique → ouvre airbizness.com/sejour.html?on_behalf_of=... avec
tous les params pré-remplis. Si la résa aboutit, commission 8% pour l'hôtel.

Endpoints :
  GET  /widget/v1/airbizness.js  → JS pur, self-contained, Cache 1h
  POST /widget/event             → beacon tracking (view / click / submit)
  GET  /widget/stats?token=...   → stats 30j pour l'extranet hôtelier
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from main import DB_CONFIG, limiter
from routers.hotelier import _validate_hotel_manager_token
import psycopg2
import psycopg2.extras
import json

router = APIRouter()


def _ensure_widget_events_table():
    """Crée la table widget_events si absente (idempotent)."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS widget_events (
                id BIGSERIAL PRIMARY KEY,
                hotel_code INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                session_id TEXT,
                page_url TEXT,
                user_agent TEXT,
                ip INET,
                destination TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_widget_events_hotel
              ON widget_events (hotel_code, created_at DESC);
        """)
        conn.commit()
        cur.close(); conn.close()
    except Exception as _e:
        # On ne casse jamais le boot si la DDL échoue
        print(f"[widget] DDL widget_events failed: {_e}")

_ensure_widget_events_table()

_WIDGET_JS = r"""(function(){
'use strict';
var API='https://airbizness.com';
var SID=(Math.random().toString(36).slice(2,12))+Date.now().toString(36).slice(-4);

function track(et, hc, extra){
  try{
    var payload=Object.assign({hotel_code:hc, event_type:et, session_id:SID,
      page_url:location.href}, extra||{});
    var body=JSON.stringify(payload);
    if(navigator.sendBeacon){
      var blob=new Blob([body],{type:'application/json'});
      navigator.sendBeacon(API+'/api/widget/event', blob);
    } else {
      fetch(API+'/api/widget/event',{method:'POST',headers:{'Content-Type':'application/json'},body:body,keepalive:true,mode:'no-cors'}).catch(function(){});
    }
  }catch(e){}
}

var CSS='\
.abz-widget{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;background:#fff;border-radius:14px;padding:22px;max-width:520px;margin:0 auto;box-shadow:0 6px 28px rgba(0,0,0,.10);border:1px solid #eee2c4;line-height:1.5;}\
.abz-widget *{box-sizing:border-box;}\
.abz-widget.abz-full{max-width:100%;}\
.abz-widget.abz-compact{max-width:460px;}\
.abz-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px;border-bottom:1px solid #f0eada;padding-bottom:10px;}\
.abz-logo{font-family:Georgia,serif;font-style:italic;font-size:18px;color:#b8962e;letter-spacing:.01em;}\
.abz-tag{font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:#999;}\
.abz-form{display:block;}\
.abz-field{margin-bottom:11px;}\
.abz-field label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#8a8a8a;margin-bottom:5px;font-weight:600;}\
.abz-field input,.abz-field select{width:100%;border:1px solid #e3dac1;border-radius:8px;padding:10px 12px;font-size:14px;font-family:inherit;color:#1a1a1a;background:#fff;outline:none;transition:border-color .15s;}\
.abz-field input:focus,.abz-field select:focus{border-color:#b8962e;}\
.abz-field input[readonly]{background:#f9f5e9;color:#7a6a3a;font-weight:600;cursor:not-allowed;}\
.abz-field-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;}\
.abz-btn{width:100%;background:#b8962e;color:#fff;border:none;padding:13px 20px;border-radius:8px;font-size:14px;font-weight:700;font-family:inherit;cursor:pointer;margin-top:8px;letter-spacing:.01em;transition:background .15s;}\
.abz-btn:hover{background:#9c7e23;}\
.abz-footer{text-align:center;font-size:10px;color:#a8a08a;margin-top:12px;letter-spacing:.5px;}\
.abz-footer a{color:#b8962e;text-decoration:none;}\
.abz-silver{border-color:#dcdcdc;}\
.abz-silver .abz-logo{color:#6e6e6e;}\
.abz-silver .abz-field input,.abz-silver .abz-field select{border-color:#dcdcdc;}\
.abz-silver .abz-field input:focus,.abz-silver .abz-field select:focus{border-color:#6e6e6e;}\
.abz-silver .abz-field input[readonly]{background:#f3f3f3;color:#4a4a4a;}\
.abz-silver .abz-btn{background:#5a5a5a;}\
.abz-silver .abz-btn:hover{background:#3a3a3a;}\
.abz-silver .abz-footer a{color:#6e6e6e;}\
.abz-dark{background:#1a1a1a;color:#f0ece4;border-color:#3a3a3a;}\
.abz-dark .abz-head{border-bottom-color:#2a2a2a;}\
.abz-dark .abz-logo{color:#d4ae4a;}\
.abz-dark .abz-tag{color:#888;}\
.abz-dark .abz-field label{color:#888;}\
.abz-dark .abz-field input,.abz-dark .abz-field select{background:#2a2a2a;border-color:#3a3a3a;color:#f0ece4;}\
.abz-dark .abz-field input:focus,.abz-dark .abz-field select:focus{border-color:#d4ae4a;}\
.abz-dark .abz-field input[readonly]{background:#222;color:#c0a868;}\
.abz-dark .abz-btn{background:#d4ae4a;color:#1a1a1a;}\
.abz-dark .abz-btn:hover{background:#c09a3a;}\
.abz-dark .abz-footer{color:#777;}\
.abz-dark .abz-footer a{color:#d4ae4a;}\
.abz-light{border-color:#e8e8e8;box-shadow:0 2px 12px rgba(0,0,0,.04);}\
.abz-light .abz-logo{color:#1a1a1a;font-weight:600;}\
.abz-light .abz-field input,.abz-light .abz-field select{border-color:#e0e0e0;}\
.abz-light .abz-field input:focus,.abz-light .abz-field select:focus{border-color:#1a1a1a;}\
.abz-light .abz-field input[readonly]{background:#f7f7f7;color:#555;}\
.abz-light .abz-btn{background:#1a1a1a;color:#fff;}\
.abz-light .abz-btn:hover{background:#000;}\
@media(max-width:480px){.abz-field-row{grid-template-columns:1fr;}}\
';

function todayISO(off){
  var d=new Date(); d.setDate(d.getDate()+(off||0));
  var m=(d.getMonth()+1).toString().padStart(2,'0');
  var day=d.getDate().toString().padStart(2,'0');
  return d.getFullYear()+'-'+m+'-'+day;
}

function escapeHtml(s){
  return String(s||'').replace(/[&<>"']/g,function(c){
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}

function renderWidget(container){
  var hotelCode=container.dataset.hotelCode||'';
  var airport=(container.dataset.airport||'').toUpperCase();
  var theme=(container.dataset.theme||'gold').toLowerCase();
  var mode=(container.dataset.mode||'compact').toLowerCase();
  var cta=container.dataset.cta||'Rechercher des vols';
  if(['gold','silver','dark','light'].indexOf(theme)<0) theme='gold';
  if(['compact','full'].indexOf(mode)<0) mode='compact';

  var dDep=todayISO(14), dRet=todayISO(17);
  container.innerHTML=''+
    '<div class="abz-widget abz-'+theme+' abz-'+mode+'">'+
      '<div class="abz-head">'+
        '<span class="abz-logo">AirBizness</span>'+
        '<span class="abz-tag">Réservez votre vol</span>'+
      '</div>'+
      '<form class="abz-form" autocomplete="off">'+
        '<div class="abz-field">'+
          '<label>Aéroport de départ</label>'+
          '<input name="origin" type="text" placeholder="CDG, ORY, LHR..." required maxlength="3" style="text-transform:uppercase;">'+
        '</div>'+
        '<div class="abz-field">'+
          '<label>Arrivée</label>'+
          '<input name="destination" type="text" value="'+airport+'" readonly>'+
        '</div>'+
        '<div class="abz-field-row">'+
          '<div class="abz-field">'+
            '<label>Aller</label>'+
            '<input name="checkin" type="date" value="'+dDep+'" required>'+
          '</div>'+
          '<div class="abz-field">'+
            '<label>Retour (optionnel)</label>'+
            '<input name="checkout" type="date" value="'+dRet+'">'+
          '</div>'+
        '</div>'+
        '<div class="abz-field-row">'+
          '<div class="abz-field">'+
            '<label>Voyageurs</label>'+
            '<select name="adults">'+
              '<option value="1">1 adulte</option>'+
              '<option value="2" selected>2 adultes</option>'+
              '<option value="3">3 adultes</option>'+
              '<option value="4">4 adultes</option>'+
            '</select>'+
          '</div>'+
          '<div class="abz-field">'+
            '<label>Classe</label>'+
            '<select name="cabin">'+
              '<option value="economy">Économique</option>'+
              '<option value="premium_economy">Premium Éco</option>'+
              '<option value="business" selected>Business</option>'+
              '<option value="first">Première</option>'+
            '</select>'+
          '</div>'+
        '</div>'+
        '<button type="submit" class="abz-btn">'+escapeHtml(cta)+' →</button>'+
        '<div class="abz-footer">Propulsé par <a href="https://airbizness.com" target="_blank" rel="noopener">AirBizness</a></div>'+
      '</form>'+
    '</div>';

  var form=container.querySelector('form');
  form.addEventListener('submit', function(e){
    e.preventDefault();
    var fd=new FormData(form);
    var origin=(fd.get('origin')||'').toString().trim().toUpperCase();
    if(!origin){return;}
    var params=new URLSearchParams();
    params.set('on_behalf_of', hotelCode);
    params.set('origin', origin);
    params.set('destination', airport);
    params.set('checkin', fd.get('checkin')||'');
    var co=fd.get('checkout');
    if(co) params.set('checkout', co);
    params.set('adults', fd.get('adults')||'2');
    params.set('cabin', fd.get('cabin')||'business');
    params.set('from_widget', '1');
    params.set('widget_session', SID);
    track('submit', hotelCode, {destination:airport});
    window.open(API+'/sejour.html?'+params.toString(), '_blank', 'noopener');
  });

  // Track impression (view)
  track('view', hotelCode, {destination:airport});
}

function injectCSS(){
  if(document.getElementById('abz-widget-css')) return;
  var st=document.createElement('style');
  st.id='abz-widget-css';
  st.textContent=CSS;
  document.head.appendChild(st);
}

function init(){
  injectCSS();
  var nodes=document.querySelectorAll('#airbizness-flight-widget, .airbizness-flight-widget');
  for(var i=0;i<nodes.length;i++){ renderWidget(nodes[i]); }
}

// Expose pour re-render (utile dans extranet preview)
window.__abzWidgetRender=function(container){ injectCSS(); renderWidget(container); };
window.__abzWidgetInit=init;

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
})();
"""


@router.get("/widget/v1/airbizness.js")
def widget_js():
    """Widget de réservation vol embeddable.
    Self-contained, aucune dépendance externe, CSS injecté."""
    return Response(
        content=_WIDGET_JS,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "X-AirBizness-Widget": "v1",
        },
    )


@router.options("/widget/event")
def widget_event_options():
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        },
    )


@router.post("/widget/event")
@limiter.limit("300/minute")
async def widget_event(request: Request):
    """Beacon tracking depuis le widget embeddé. CORS open.
    Body JSON : {hotel_code, event_type, session_id?, page_url?, destination?}.
    Anonyme par design (pas de PII, juste session_id random côté visiteur)."""
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
    }
    try:
        raw = await request.body()
        if not raw:
            return Response(status_code=204, headers=cors_headers)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return Response(status_code=204, headers=cors_headers)

        hotel_code = data.get("hotel_code")
        event_type = (data.get("event_type") or "").strip().lower()
        if not hotel_code or event_type not in ("view", "click", "submit"):
            return Response(status_code=204, headers=cors_headers)

        try:
            hotel_code_i = int(hotel_code)
        except Exception:
            return Response(status_code=204, headers=cors_headers)

        session_id = (data.get("session_id") or "")[:64]
        page_url = (data.get("page_url") or "")[:512]
        destination = (data.get("destination") or "")[:8] or None
        ua = (request.headers.get("user-agent") or "")[:255]
        client_ip = request.client.host if request.client else None

        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO widget_events
                  (hotel_code, event_type, session_id, page_url, user_agent, ip, destination)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (hotel_code_i, event_type, session_id or None, page_url or None,
                  ua or None, client_ip, destination))
            conn.commit()
            cur.close(); conn.close()
        except Exception as e:
            print(f"[widget] insert event failed: {e}")
        return Response(status_code=204, headers=cors_headers)
    except Exception as e:
        print(f"[widget] event handler error: {e}")
        return Response(status_code=204, headers=cors_headers)


@router.get("/widget/stats")
def widget_stats(token: str, period_days: int = 30):
    """Stats widget 30 derniers jours pour l'extranet hôtelier.
    Token = claim_token de l'hôtelier. Retour : impressions, clicks, submits,
    bookings_via_widget, commissions, et série quotidienne."""
    claim = _validate_hotel_manager_token(token)
    hotel_code = int(claim["hotel_code"])
    period_days = max(1, min(int(period_days or 30), 365))
    commission_rate = 0.08  # 8% commission widget (vs 5% concierge)

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    impressions = clicks = submits = 0
    bookings_count = 0
    gross_pending = 0.0
    gross_paid = 0.0
    by_day = []
    try:
        # Aggregats events
        cur.execute("""
            SELECT event_type, COUNT(*) AS c
            FROM widget_events
            WHERE hotel_code = %s
              AND created_at > NOW() - (%s || ' days')::interval
            GROUP BY event_type
        """, (hotel_code, str(period_days)))
        for r in cur.fetchall():
            et = r["event_type"]; c = int(r["c"] or 0)
            if et == "view": impressions = c
            elif et == "click": clicks = c
            elif et == "submit": submits = c

        # Série journalière
        cur.execute("""
            SELECT DATE(created_at) AS d,
                   SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END) AS views,
                   SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) AS clicks,
                   SUM(CASE WHEN event_type='submit' THEN 1 ELSE 0 END) AS submits
            FROM widget_events
            WHERE hotel_code = %s
              AND created_at > NOW() - (%s || ' days')::interval
            GROUP BY DATE(created_at)
            ORDER BY d ASC
        """, (hotel_code, str(period_days)))
        for r in cur.fetchall():
            by_day.append({
                "date": r["d"].isoformat() if r["d"] else None,
                "views": int(r["views"] or 0),
                "clicks": int(r["clicks"] or 0),
                "submits": int(r["submits"] or 0),
                "bookings": 0,
            })

        # Bookings via widget : flight_bookings ou pack_bookings avec
        # on_behalf_of=hotel_code ET from_widget=true dans raw payload.
        hc_str = str(hotel_code)
        cur.execute("""
            SELECT COALESCE(SUM(total_eur),0) AS s, COUNT(*) AS c,
                   COALESCE(SUM(CASE WHEN status='confirmed' THEN total_eur ELSE 0 END),0) AS s_conf
            FROM flight_bookings
            WHERE raw_offer->'on_behalf_of'->>'hotel_code' = %s
              AND (raw_offer->>'from_widget' = 'true' OR raw_offer->>'from_widget' = '1')
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hc_str, str(period_days)))
        row = cur.fetchone()
        bookings_count += int(row["c"] or 0)
        gross_pending += float(row["s_conf"] or 0)

        cur.execute("""
            SELECT COALESCE(SUM(total_amount),0) AS s, COUNT(*) AS c,
                   COALESCE(SUM(CASE WHEN status='confirmed' THEN total_amount ELSE 0 END),0) AS s_conf
            FROM pack_bookings
            WHERE raw_payload->>'on_behalf_of' = %s
              AND (raw_payload->>'from_widget' = 'true' OR raw_payload->>'from_widget' = '1')
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hc_str, str(period_days)))
        row = cur.fetchone()
        bookings_count += int(row["c"] or 0)
        gross_pending += float(row["s_conf"] or 0)
    finally:
        cur.close(); conn.close()

    commissions_pending = round(gross_pending * commission_rate, 2)

    return {
        "hotel_code": hotel_code,
        "period_days": period_days,
        "commission_rate": commission_rate,
        "impressions": impressions,
        "clicks": clicks,
        "submits": submits,
        "bookings_via_widget": bookings_count,
        "gross_via_widget_eur": round(gross_pending, 2),
        "commissions_pending_eur": commissions_pending,
        "commissions_paid_eur": round(gross_paid, 2),
        "by_day": by_day,
    }
