const DEST_CONFIG = {
  JFK: { city: 'New York', img: '/images/destinations/jfk.jpg' },
  LAX: { city: 'Los Angeles', img: '/images/destinations/lax.jpg' },
  SIN: { city: 'Singapour', img: '/images/destinations/sin.jpg' },
  NRT: { city: 'Tokyo', img: '/images/destinations/nrt.jpg' },
  DXB: { city: 'Dubaï', img: '/images/destinations/dxb.jpg' },
  HKG: { city: 'Hong Kong', img: '/images/destinations/hkg.jpg' },
  BKK: { city: 'Bangkok', img: '/images/destinations/bkk.jpg' },
  GRU: { city: 'São Paulo', img: '/images/destinations/gru.jpg' },
  SYD: { city: 'Sydney', img: '/images/destinations/syd.jpg' },
  DEL: { city: 'Delhi', img: '/images/destinations/del.jpg' },
  BOM: { city: 'Mumbai', img: '/images/destinations/bom.jpg' },
  DOH: { city: 'Doha', img: '/images/destinations/doh.jpg' },
  ICN: { city: 'Séoul', img: '/images/destinations/icn.jpg' },
  LHR: { city: 'Londres', img: '/images/destinations/lhr.jpg' },
  AMS: { city: 'Amsterdam', img: '/images/destinations/jfk.jpg' },
  FRA: { city: 'Francfort', img: '/images/destinations/jfk.jpg' },
  MAD: { city: 'Madrid', img: '/images/destinations/lax.jpg' },
  CPT: { city: 'Cape Town', img: '/images/destinations/cpt.jpg' },
  NBO: { city: 'Nairobi', img: '/images/destinations/nbo.jpg' },
  MEL: { city: 'Melbourne', img: '/images/destinations/syd.jpg' },
  ORD: { city: 'Chicago', img: '/images/destinations/ord.jpg' },
  MIA: { city: 'Miami', img: '/images/destinations/mia.jpg' },
  SFO: { city: 'San Francisco', img: '/images/destinations/sfo.jpg' },
  CDG: { city: 'Paris', img: '/images/destinations/jfk.jpg' },
};

let AVG_PRICES = {
  JFK: 2000, LAX: 2300, SIN: 1700, NRT: 2000,
  DXB: 1600, HKG: 1800, BKK: 1500, GRU: 1600
};
fetch('/api/deals/averages').then(r=>r.json()).then(d=>{Object.assign(AVG_PRICES,d);}).catch(()=>{});

const CABIN_LABELS = {
  full_flat_pod: { label: '🛏 Flat bed pod', cls: 'pod' },
  full_flat: { label: '🛏 Full flat', cls: 'flat' },
  private_suite: { label: '👑 Suite privée', cls: 'suite' },
  recliner: { label: '🪑 Recliner', cls: 'rec' },
  standard: { label: '💺 Standard', cls: '' },
  unknown: { label: '✈ Business', cls: '' },
};

const CITIES = {
  CDG:'Paris',LHR:'Londres',AMS:'Amsterdam',FRA:'Francfort',MAD:'Madrid',
  DXB:'Dubai',SIN:'Singapour',JFK:'New York',NRT:'Tokyo',HKG:'Hong Kong',
  SYD:'Sydney',LAX:'Los Angeles',BKK:'Bangkok',GRU:'Sao Paulo',DOH:'Doha',
  ICN:'Seoul',DEL:'Delhi',BOM:'Mumbai',CPT:'Cape Town',NBO:'Nairobi',
  ORD:'Chicago',MIA:'Miami',SFO:'San Francisco'
};

function formatPrice(p) {
  return new Intl.NumberFormat('fr-FR').format(Math.round(p)) + ' €';
}

function formatDuration(min) {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

function getPct(price, dest) {
  const avg = AVG_PRICES[dest] || 1800;
  return Math.round((1 - price / avg) * 100);
}

function getCabinInfo(type) {
  return CABIN_LABELS[type] || CABIN_LABELS['unknown'];
}

function renderCard(deal, type) {
  const pct = getPct(deal.price, deal.destination);
  const avg = AVG_PRICES[deal.destination] || 1800;
  const dest = DEST_CONFIG[deal.destination] || { city: deal.destination };
  const cabin = getCabinInfo(deal.seat_type);
  const stops = deal.stops === 0
    ? '<span class="fc-stop fc-direct">Direct</span>'
    : `<span class="fc-stop fc-escale">${deal.stops} escale${deal.stops > 1 ? 's' : ''}</span>`;
  const pctClass = pct >= 60 ? 'exc' : 'good';
  const wifiTag = deal.wifi ? '<span class="fc-tag">Wifi</span>' : '';
  const dateStr = deal.departure_at
    ? new Date(deal.departure_at).toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'})
      + ' · ' + new Date(deal.departure_at).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})
    : '--';

  return `
  <div class="flight-card ${pctClass}" onclick="location.href='/vol.html?id=${deal.offer_id}'">
    <div class="fc-top">
      <div class="fc-route">
        <div class="fc-city">
          <div class="fc-iata">${deal.origin}</div>
          <div class="fc-name">${CITIES[deal.origin] || deal.origin}</div>
          <div class="fc-time">${dateStr}</div>
        </div>
        <div class="fc-conn">
          <div class="fc-line"></div>
          <div class="fc-dur">${formatDuration(deal.duration_minutes)}</div>
          ${stops}
        </div>
        <div class="fc-city">
          <div class="fc-iata">${deal.destination}</div>
          <div class="fc-name">${dest.city || deal.destination}</div>
        </div>
      </div>
      <div class="fc-pb">
        <div class="fc-orig">${formatPrice(avg)}</div>
        <div style="font-size:9px;color:var(--text3);margin-bottom:2px;">Tarif plein observé</div>
        <div class="fc-price">${formatPrice(deal.price)}</div>
        <div class="fc-pct ${pctClass}">— ${pct > 0 ? pct : '?'} %</div>
      </div>
    </div>
    <div class="fc-bot">
      <div class="fc-tags">
        <span class="fc-tag">${deal.airline_name}</span>
        <span class="fc-tag ${cabin.cls}">${cabin.label}</span>
        ${wifiTag}
      </div>
      <div class="fc-btns">
        <button class="btn-bell" onclick="event.stopPropagation()">🔔</button>
        <button class="btn-book" onclick="event.stopPropagation();location.href='/vol.html?id=${deal.offer_id}'">Réserver</button>
      </div>
    </div>
  </div>`;
}
