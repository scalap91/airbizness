/* ═══════════════════════════════════════════════════════════════════════
 * AirBizness — MODULE CARNET (Pascal 2026-05-31)
 *
 * Tous les composants partagés entre pages, regroupés ici.
 * Une seule inclusion par page : <script src="/js/carnets.js?v=NNNN"></script>
 *
 * Contenu :
 *   0. HELPER fmtEur            → window.fmtEur (format prix unifié, jamais d'arrondi caché)
 *   1. SECTION HOTEL CARD       → window.renderHotelCard, window.galleryNav, window.toggleFavorite
 *   2. SECTION SEARCH AUTOCOMPLETE → window.setupAirportAutocomplete, window.setupCityAutocomplete,
 *                                    window.RecentSearches, window.POPULAR_*, window.SearchOverlay
 *
 * Chaque section est isolée dans son propre IIFE (pas de collision de scope).
 * Tous les exports passent par window.*.
 * ═══════════════════════════════════════════════════════════════════════ */

/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  HELPER fmtEur (Pascal 2026-05-31, BUG-4/5)                            ║
   ║  Format prix UNIFIÉ pour TOUTES les pages.                             ║
   ║  Règle : on retranscrit l'API. Si l'API dit 1576,56 → on affiche       ║
   ║  1 576,56 €. Si elle dit 1576 → 1 576 €. JAMAIS d'arrondi silencieux.  ║
   ║  Avant : chaque page avait son .toFixed(0)/.Math.round() → 1576→1577→  ║
   ║  1576,56 selon la page (incohérence visible client).                   ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
window.fmtEur = function(v) {
  if (v == null || v === '') return '0 €';
  const n = Number(v);
  if (!isFinite(n)) return '0 €';
  // Si effectivement entier (à 0.005 près = pas de décimales significatives) → format entier
  const isInt = Math.abs(n - Math.round(n)) < 0.005;
  if (isInt) {
    return `${Math.round(n).toLocaleString('fr-FR')} €`;
  }
  // Sinon → 2 décimales fixes, format français (virgule)
  return `${n.toLocaleString('fr-FR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} €`;
};


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 1 — HOTEL CARD                                                ║
   ║  Rendu unifié cartes hôtel (carrousel, badges, favori, prix, CTA)      ║
   ║  Pages consommatrices : /hotels.html, /sejour.html (étape choix hôtel) ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
/* /js/hotel-card.js — Composant card hôtel partagé.
   ROLE : 1 SEULE source de vérité pour le rendu des cards hôtel
   (hotels.html, sejour.html, et toute future page listing).

   POURQUOI : avant ce fichier, hotels.html et sejour.html avaient chacun leur
   propre fonction de rendu (cardHtml / paintHotels). Un fix sur l'un ne se
   propageait PAS sur l'autre → duplication, dette, fix carrousel oublié dans
   sejour.html (cf. recadrage Pascal 2026-05-31).

   USAGE :
     <script src="/js/hotel-card.js?v=1"></script>
     ...
     const html = window.renderHotelCard(hotel, ctx);

   ctx attendu :
     { ci, co, adults, mode? }
     - mode = "link" (défaut) → <a href="/quote.html?..."> (hotels.html)
     - mode = "select" → <div onclick="selectHotel(idx)"> (sejour.html)

   Toutes les helpers (esc, fmtKm, distanceCenter, favoris, galerie nav)
   exposées en window.X pour rétrocompat. */
(function() {
  if (window.__abHotelCardLoaded) return;
  window.__abHotelCardLoaded = true;

  // ────────── Helpers ──────────
  function esc(s) {
    return String(s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  }
  function haversineKm(lat1, lng1, lat2, lng2) {
    if (lat1==null||lng1==null||lat2==null||lng2==null) return null;
    const R = 6371, toRad = d => d*Math.PI/180;
    const dLat = toRad(lat2-lat1), dLng = toRad(lng2-lng1);
    const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLng/2)**2;
    return R * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  function fmtKm(km) {
    if (km == null) return null;
    if (km < 1) return Math.round(km*1000) + ' m';
    if (km < 10) return km.toFixed(1).replace('.', ',') + ' km';
    return Math.round(km) + ' km';
  }
  const CITY_CENTERS_LATLNG = {
    PAR:[48.8566,2.3522], PARIS:[48.8566,2.3522], CDG:[48.8566,2.3522],
    MAD:[40.4168,-3.7038], MADRID:[40.4168,-3.7038],
    LON:[51.5074,-0.1278], LONDON:[51.5074,-0.1278], LONDRES:[51.5074,-0.1278],
    BCN:[41.3851,2.1734], BARCELONA:[41.3851,2.1734],
    RAK:[31.6295,-7.9811], MARRAKECH:[31.6295,-7.9811],
  };
  function distanceCenter(h) {
    if (!h.latitude || !h.longitude) return null;
    const code = (h._destCode || h.country_code || '').toUpperCase();
    const c = CITY_CENTERS_LATLNG[code] || CITY_CENTERS_LATLNG[(h.city||'').toUpperCase()];
    if (!c) return null;
    return fmtKm(haversineKm(h.latitude, h.longitude, c[0], c[1]));
  }
  function nightsCount(ctx) {
    if (!ctx || !ctx.ci || !ctx.co) return 1;
    const a = new Date(ctx.ci), b = new Date(ctx.co);
    return Math.max(1, Math.round((b-a)/86400000));
  }

  // ────────── Favoris (localStorage) ──────────
  const FAV_KEY = 'ab_hotel_favorites';
  function getFavorites() {
    try { return JSON.parse(localStorage.getItem(FAV_KEY) || '[]'); }
    catch(e) { return []; }
  }
  function setFavorites(arr) {
    try { localStorage.setItem(FAV_KEY, JSON.stringify(arr)); } catch(e) {}
  }
  function isFavorite(code) { return getFavorites().includes(String(code)); }
  function toggleFavorite(btn, code) {
    const favs = getFavorites();
    const c = String(code);
    const idx = favs.indexOf(c);
    if (idx >= 0) { favs.splice(idx,1); btn.classList.remove('faved'); }
    else { favs.push(c); btn.classList.add('faved'); }
    setFavorites(favs);
  }

  // ────────── Galerie : navigation slides ──────────
  function galleryNav(btn, dir) {
    const cardImg = btn.closest ? btn.closest('.card-img') : btn.parentElement;
    if (!cardImg) return;
    const track = cardImg.querySelector('.gallery-track');
    if (!track) return;
    const total = parseInt(track.dataset.total||'1');
    let cur = parseInt(track.dataset.current||'0');
    cur = (cur + dir + total) % total;
    track.dataset.current = cur;
    track.style.transform = `translateX(-${cur*100}%)`;
    cardImg.querySelectorAll('.gallery-dot').forEach((d,i) =>
      d.classList.toggle('active', i === cur)
    );
  }

  // ────────── Swipe touch (mobile) ──────────
  document.addEventListener('touchstart', e => {
    const track = e.target.closest && e.target.closest('.gallery-track');
    if (!track) return;
    track._sx = e.touches[0].clientX;
    track._sy = e.touches[0].clientY;
  }, {passive:true});
  document.addEventListener('touchmove', e => {
    const track = e.target.closest && e.target.closest('.gallery-track');
    if (!track || track._sx==null) return;
    const dx = e.touches[0].clientX - track._sx;
    const dy = e.touches[0].clientY - track._sy;
    if (Math.abs(dx) > Math.abs(dy)) track._moved = true;
  }, {passive:true});
  document.addEventListener('touchend', e => {
    const track = e.target.closest && e.target.closest('.gallery-track');
    if (!track || !track._moved) { if (track) { track._sx = track._sy = null; track._moved = false; } return; }
    const dx = (e.changedTouches[0].clientX - (track._sx||0));
    if (Math.abs(dx) > 40) {
      galleryNav({closest:()=>track.parentElement}, dx > 0 ? -1 : 1);
    }
    track._sx = track._sy = null; track._moved = false;
  }, {passive:true});

  // ────────── Fonction de rendu principale ──────────
  function renderHotelCard(h, ctx) {
    ctx = ctx || {};
    const stars = '★'.repeat(h.stars || 0);
    const _rk = h.best_rate_key ? `&rate_key=${encodeURIComponent(h.best_rate_key)}` : '';
    const _pv = h.best_provider ? `&provider=${encodeURIComponent(h.best_provider)}` : '';
    const code = h.code || h.hotel_code;
    const url = `/quote.html?code=${code}${h.giata_code?`&giata=${h.giata_code}`:''}&checkin=${ctx.ci||''}&checkout=${ctx.co||''}&adults=${ctx.adults||2}${_rk}${_pv}`;

    // Galerie : multi-photos (swipe mobile + flèches desktop)
    const photos = (h.gallery && h.gallery.length) ? h.gallery
                 : (h.gallery_photos && h.gallery_photos.length) ? h.gallery_photos
                 : (h.main_photo ? [h.main_photo] : h.image ? [h.image] : []);
    const totalImgs = h.images_total || photos.length;
    let img;
    if (photos.length > 1) {
      const slides = photos.map((p, i) =>
        `<div class="gallery-slide"><img src="${esc(p)}" alt="${esc(h.name)} - photo ${i+1}" loading="lazy" decoding="async"></div>`
      ).join('');
      const dots = photos.map((_, i) =>
        `<div class="gallery-dot${i===0?' active':''}" data-dot="${i}"></div>`
      ).join('');
      const countTxt = totalImgs > photos.length ? `+${totalImgs - photos.length} photos` : `${totalImgs}`;
      img = `<div class="gallery-track" data-current="0" data-total="${photos.length}">${slides}</div>` +
            `<button class="gallery-arrow prev" type="button" aria-label="Photo précédente" onclick="event.preventDefault();event.stopPropagation();window.galleryNav(this,-1);"><svg viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"/></svg></button>` +
            `<button class="gallery-arrow next" type="button" aria-label="Photo suivante" onclick="event.preventDefault();event.stopPropagation();window.galleryNav(this,1);"><svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg></button>` +
            `<div class="gallery-dots">${dots}</div>` +
            `<div class="gallery-count"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="m21 19-7-8-9 8"/></svg>${countTxt}</div>`;
    } else {
      const single = photos[0] || '';
      img = single ? `<img src="${esc(single)}" alt="${esc(h.name)}" loading="lazy">` : '';
    }

    const isFav = isFavorite(code);

    const badges = [];
    if (h.free_cancel) badges.push(`<span class="badge badge-cancel">Annulation gratuite</span>`);
    else if (h.non_refundable) badges.push(`<span class="badge badge-nrf">Non remboursable</span>`);
    if (h.board_code === 'BB' || h.board_code === 'BREAKFAST') badges.push(`<span class="badge badge-breakfast">Petit-déj inclus</span>`);
    if ((h.stars || 0) >= 4) badges.push(`<span class="badge badge-business">Business favorite</span>`);

    const features = [];
    features.push(`<span title="Wifi gratuit">Wifi</span>`);
    if (h.board_code === 'BB') features.push(`<span>Petit-déj</span>`);
    if ((h.stars || 0) >= 4) features.push(`<span>Conciergerie</span>`);
    features.push(`<span>Paiement sécurisé</span>`);

    const nights = nightsCount(ctx);
    const totalPrice = h.best_price || h.price_from || 0;
    const pricePerNight = nights > 0 ? totalPrice / nights : totalPrice;
    // BUG-2 fix (Pascal 2026-05-31) : plus de strike/discount inventés.
    // On n'affiche un prix barré ou un % négocié QUE si le provider nous l'envoie vraiment.
    // Règle absolue : on retranscrit l'API, on n'invente pas.
    const strikeHtml = h.price_strike ? `<div class="card-price-strike">${h.price_strike}€</div>` : '';
    const discountHtml = h.discount_pct ? `<span class="card-discount">−${h.discount_pct}% négocié</span>` : '';

    // BUG-4/5 fix : fmtEur retranscrit l'API sans arrondir (1576,56 reste 1576,56).
    const priceBlock = totalPrice > 0
      ? `${strikeHtml}
         <div class="card-price-now">${window.fmtEur(totalPrice).replace(/\s?€$/, '')}<span style="font-size:18px;color:var(--text2);font-family:DM Sans;font-weight:500;"> €</span></div>
         <div class="card-price-night">${window.fmtEur(pricePerNight)}/nuit · ${nights} nuit${nights>1?'s':''} · taxes comprises</div>
         ${discountHtml}`
      : `<div class="card-price-now" style="font-size:18px;color:var(--text2);font-family:DM Sans;">Tarif à la sélection</div>
         <div class="card-price-night" style="margin-top:4px;">${nights} nuit${nights>1?'s':''}</div>`;

    // Mode : "link" (anchor) ou "select" (click handler personnalisé)
    const mode = ctx.mode || "link";
    const distLabel = distanceCenter(h) ? ` · <span class="card-dist">${distanceCenter(h)} du centre</span>` : '';
    const inner = `
      <div class="card-img">
        ${img}
        <div class="card-badges">${badges.slice(0, 3).join('')}</div>
        ${h.stars ? `<div class="card-stars">${stars}</div>` : ''}
        <button class="card-fav${isFav?' faved':''}" type="button" aria-label="${isFav?'Retirer des':'Ajouter aux'} favoris" data-fav="${code}" onclick="event.preventDefault();event.stopPropagation();window.toggleFavorite(this,'${code}');">
          <svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        </button>
      </div>
      <div class="card-body">
        <div class="card-head-row">
          <div class="card-name">${esc(h.name)}</div>
        </div>
        <div class="card-loc">${esc(h.city || '')}${h.country_code?` · ${esc(h.country_code)}`:''}${distLabel}</div>
        <div class="card-features">${features.slice(0, 3).join('')}</div>
        <div class="card-price-row">
          <div class="card-price-block">${priceBlock}</div>
          <button class="cta-btn">Voir l'offre</button>
        </div>
      </div>`;

    if (mode === "select" && typeof ctx.onClick === 'function') {
      // Mode sejour.html : selectHotel(idx) au lieu d'un href
      const idx = ctx.idx != null ? ctx.idx : '';
      return `<div class="card" data-hotel-idx="${idx}" onclick="(${ctx.onClick.toString()})(${idx})">${inner}</div>`;
    }
    return `<a href="${url}" class="card">${inner}</a>`;
  }

  // ────────── Injection CSS au load (1 seule fois) ──────────
  function injectCss() {
    if (document.getElementById('ab-hotel-card-css')) return;
    const css = `
      /* Card racine */
      .card{background:var(--bg2,#161616);border:1px solid var(--border,rgba(255,255,255,0.07));border-radius:12px;overflow:hidden;cursor:pointer;text-decoration:none;color:inherit;display:flex;flex-direction:column;transition:border-color .2s,transform .15s;position:relative;}
      .card:hover{border-color:var(--border2,rgba(184,150,46,0.2));transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,0.45);}
      .card-img{aspect-ratio:16/10;background:linear-gradient(135deg,#1a1410 0%,#1a1a1a 60%,#1a1f2a 100%);position:relative;overflow:hidden;}
      .card-img img{width:100%;height:100%;object-fit:cover;}
      /* Galerie navigable */
      .gallery-track{display:flex;width:100%;height:100%;transition:transform .35s cubic-bezier(.4,0,.2,1);will-change:transform;touch-action:pan-y pinch-zoom;}
      .gallery-slide{flex:0 0 100%;width:100%;height:100%;}
      .gallery-slide img{width:100%;height:100%;object-fit:cover;}
      .gallery-arrow{position:absolute;top:50%;transform:translateY(-50%);width:36px;height:36px;background:rgba(0,0,0,0.55);backdrop-filter:blur(6px);border:none;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:#fff;font-size:18px;z-index:3;transition:background .15s,opacity .2s;opacity:0.75;}
      .gallery-arrow.prev{left:8px;}
      .gallery-arrow.next{right:8px;}
      .gallery-arrow:hover{background:rgba(0,0,0,0.85);opacity:1;}
      .gallery-arrow svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
      @media(hover:hover) and (pointer:fine){ .card:hover .gallery-arrow{opacity:1;} }
      .gallery-dots{position:absolute;bottom:8px;left:50%;transform:translateX(-50%);display:flex;gap:5px;z-index:3;pointer-events:none;}
      .gallery-dot{width:5px;height:5px;border-radius:50%;background:rgba(255,255,255,0.45);transition:background .2s,width .2s;}
      .gallery-dot.active{background:#fff;width:14px;border-radius:99px;}
      .gallery-count{position:absolute;bottom:10px;right:10px;background:rgba(0,0,0,0.65);backdrop-filter:blur(6px);padding:4px 9px;border-radius:99px;font-size:10.5px;color:#fff;letter-spacing:0.2px;z-index:3;display:flex;align-items:center;gap:5px;}
      .gallery-count svg{width:11px;height:11px;stroke:currentColor;fill:none;stroke-width:1.8;}
      /* Favori */
      .card-fav{position:absolute;top:10px;right:10px;width:36px;height:36px;background:rgba(0,0,0,0.55);backdrop-filter:blur(6px);border:none;border-radius:50%;display:grid;place-items:center;cursor:pointer;z-index:4;color:#fff;transition:background .15s,transform .2s;-webkit-tap-highlight-color:transparent;}
      .card-fav:hover,.card-fav:active{background:rgba(0,0,0,0.78);transform:scale(1.06);}
      .card-fav svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;transition:fill .2s,stroke .2s;}
      .card-fav.faved svg{fill:#cc3a4a;stroke:#cc3a4a;}
      /* Étoiles */
      .card-stars{position:absolute;top:10px;right:56px;font-size:10px;letter-spacing:0.15em;color:var(--gold2,#d4ae4a);background:rgba(0,0,0,0.65);backdrop-filter:blur(8px);padding:3px 8px;border-radius:5px;}
      /* Badges */
      .card-badges{position:absolute;top:10px;left:10px;display:flex;flex-direction:column;gap:5px;align-items:flex-start;max-width:78%;}
      .badge{font-size:10px;font-weight:600;letter-spacing:0.3px;padding:4px 9px;border-radius:5px;backdrop-filter:blur(8px);white-space:nowrap;}
      .badge-cancel{background:rgba(74,222,128,0.18);color:#7ddc94;border:1px solid rgba(74,222,128,0.3);}
      .badge-business{background:rgba(212,174,74,0.22);color:#e6c878;border:1px solid rgba(212,174,74,0.4);}
      .badge-breakfast{background:rgba(96,165,250,0.18);color:#8fbcfa;border:1px solid rgba(96,165,250,0.3);}
      .badge-urgent{background:rgba(184,150,46,0.22);color:#d4ae4a;border:1px solid rgba(184,150,46,0.4);}
      .badge-nrf{background:rgba(220,80,80,0.18);color:#f08c8c;border:1px solid rgba(220,80,80,0.3);}
      /* Body */
      .card-body{padding:14px 16px 16px;flex:1;display:flex;flex-direction:column;gap:8px;}
      .card-head-row{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
      .card-name{font-family:'DM Serif Display',serif;font-size:16.5px;line-height:1.15;color:var(--text,#f0ece4);letter-spacing:-0.01em;flex:1;}
      .card-loc{font-size:12px;color:var(--text2,#a09890);display:flex;align-items:center;gap:5px;flex-wrap:wrap;}
      .card-loc::before{content:'•';font-size:14px;color:var(--gold2,#d4ae4a);}
      .card-dist{color:var(--gold2,#d4ae4a);font-weight:500;}
      .card-features{display:flex;gap:10px;font-size:11px;color:var(--text3,#6a6058);flex-wrap:wrap;margin-top:2px;}
      .card-features span{display:inline-flex;align-items:center;gap:4px;}
      /* Prix + CTA */
      .card-price-row{display:flex;justify-content:space-between;align-items:flex-end;margin-top:auto;padding-top:12px;border-top:1px solid var(--border,rgba(255,255,255,0.07));gap:10px;}
      .card-price-block{display:flex;flex-direction:column;align-items:flex-start;}
      .card-price-strike{font-size:12px;color:var(--text3,#6a6058);text-decoration:line-through;margin-bottom:1px;line-height:1;}
      .card-price-now{font-family:'DM Serif Display',serif;font-size:26px;color:#fff;line-height:1;letter-spacing:-0.01em;font-weight:400;}
      .card-price-night{font-size:11px;color:var(--text3,#6a6058);margin-top:3px;}
      .card-discount{display:inline-block;background:#22c55e;color:#0a1a0a;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-top:4px;letter-spacing:0.3px;}
      .cta-btn{flex-shrink:0;padding:10px 18px;background:var(--gold,#b8962e);color:#000;border:none;border-radius:7px;font-family:'DM Sans',sans-serif;font-size:12.5px;font-weight:600;letter-spacing:0.3px;cursor:pointer;text-decoration:none;transition:background .15s;white-space:nowrap;}
      .cta-btn:hover{background:var(--gold2,#d4ae4a);}
      @media(max-width:560px){
        .card-name{font-size:16px;}
        .card-price-now{font-size:24px;}
      }
    `;
    const style = document.createElement('style');
    style.id = 'ab-hotel-card-css';
    style.textContent = css;
    document.head.appendChild(style);
  }
  injectCss();

  // ────────── Expose au scope global ──────────
  window.renderHotelCard = renderHotelCard;
  window.galleryNav = galleryNav;
  window.toggleFavorite = toggleFavorite;
  window.isFavorite = isFavorite;
  window.getFavorites = getFavorites;
  // Helpers utilitaires (au cas où pages externes en ont besoin)
  window.abEsc = esc;
  window.abFmtKm = fmtKm;
  window.abHaversineKm = haversineKm;
  window.abDistanceCenter = distanceCenter;
  window.abNightsCount = nightsCount;
})();


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 2 — SEARCH AUTOCOMPLETE                                       ║
   ║  Autocomplete vols/villes, recents, populaires, grouping métro,        ║
   ║  overlay fullscreen mobile (GoVoyages-style).                          ║
   ║  Pages : /index.html, /admin-home.html, /sejour.html (search bar)      ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
/* ─────────────────────────────────────────────────────────────
 * AirBizness — Module recherche : carnet partagé (Pascal 2026-05-31)
 *
 * Source unique pour l'autocomplete vols (aéroports) + hôtels (villes).
 * Inclus par : index.html (home) + admin-home.html (preview admin).
 * Tout fix ici se propage automatiquement sur les 2 pages.
 *
 * Exports globaux :
 *   - window.RecentSearches : localStorage helper
 *   - window.POPULAR_AIRPORTS, window.POPULAR_CITIES, window.METRO_CODES
 *   - window.groupAirports(results)
 *   - window.setupAirportAutocomplete(inputId)
 *   - window.setupCityAutocomplete(inputId)
 *
 * Chaque page appelle elle-même setupAirportAutocomplete('f-from'), etc.
 * en bas de son script local — les IDs peuvent varier par page.
 * ───────────────────────────────────────────────────────────── */
(function(){
  if (window.__abSearchAutocompleteLoaded) return;
  window.__abSearchAutocompleteLoaded = true;

  const RecentSearches = {
    KEY: 'ab_recent_searches_v1',
    MAX: 5,
    save(item) {
      if (!item || !item.code) return;
      let arr = this.get();
      arr = arr.filter(x => !(x.type === item.type && x.code === item.code));
      arr.unshift({...item, ts: Date.now()});
      arr = arr.slice(0, this.MAX);
      try { localStorage.setItem(this.KEY, JSON.stringify(arr)); } catch(_){}
    },
    get() {
      try { return JSON.parse(localStorage.getItem(this.KEY) || '[]'); }
      catch(_){ return []; }
    },
    forType(type) { return this.get().filter(x => x.type === type); },
    clear() { try { localStorage.removeItem(this.KEY); } catch(_){} }
  };

  const POPULAR_AIRPORTS = [
    {region:'Europe',          code:'CDG', city:'Paris',       name:'Charles de Gaulle',  country:'France'},
    {region:'Europe',          code:'LHR', city:'London',      name:'Heathrow',           country:'United Kingdom'},
    {region:'Europe',          code:'GVA', city:'Genève',      name:'Genève Aéroport',    country:'Suisse'},
    {region:'Europe',          code:'ZRH', city:'Zurich',      name:'Zurich Airport',     country:'Suisse'},
    {region:'Europe',          code:'FRA', city:'Francfort',   name:'Frankfurt am Main',  country:'Allemagne'},
    {region:'Europe',          code:'MAD', city:'Madrid',      name:'Madrid-Barajas',     country:'Espagne'},
    {region:'Amériques',       code:'JFK', city:'New York',    name:'John F. Kennedy Intl', country:'USA'},
    {region:'Amériques',       code:'MIA', city:'Miami',       name:'Miami Intl',         country:'USA'},
    {region:'Amériques',       code:'LAX', city:'Los Angeles', name:'Los Angeles Intl',   country:'USA'},
    {region:'Asie & Pacifique',code:'NRT', city:'Tokyo',       name:'Narita Intl',        country:'Japon'},
    {region:'Asie & Pacifique',code:'SIN', city:'Singapore',   name:'Changi',             country:'Singapour'},
    {region:'Asie & Pacifique',code:'HKG', city:'Hong Kong',   name:'Hong Kong Intl',     country:'Hong Kong'},
    {region:'Moyen-Orient',    code:'DXB', city:'Dubai',       name:'Dubai Intl',         country:'EAU'},
    {region:'Moyen-Orient',    code:'DOH', city:'Doha',        name:'Hamad Intl',         country:'Qatar'},
  ];

  const POPULAR_CITIES = [
    {region:'Europe',          destination_code:'PAR', city:'Paris',       country_code:'FR'},
    {region:'Europe',          destination_code:'LON', city:'London',      country_code:'UK'},
    {region:'Europe',          destination_code:'GVA', city:'Genève',      country_code:'CH'},
    {region:'Europe',          destination_code:'ZRH', city:'Zurich',      country_code:'CH'},
    {region:'Europe',          destination_code:'FRA', city:'Francfort',   country_code:'DE'},
    {region:'Europe',          destination_code:'MAD', city:'Madrid',      country_code:'ES'},
    {region:'Europe',          destination_code:'ROM', city:'Rome',        country_code:'IT'},
    {region:'Amériques',       destination_code:'NYC', city:'New York',    country_code:'US'},
    {region:'Amériques',       destination_code:'MIA', city:'Miami',       country_code:'US'},
    {region:'Amériques',       destination_code:'LAX', city:'Los Angeles', country_code:'US'},
    {region:'Asie & Pacifique',destination_code:'TYO', city:'Tokyo',       country_code:'JP'},
    {region:'Asie & Pacifique',destination_code:'SIN', city:'Singapore',   country_code:'SG'},
    {region:'Asie & Pacifique',destination_code:'HKG', city:'Hong Kong',   country_code:'HK'},
    {region:'Moyen-Orient',    destination_code:'DXB', city:'Dubai',       country_code:'AE'},
    {region:'Moyen-Orient',    destination_code:'DOH', city:'Doha',        country_code:'QA'},
  ];

  const METRO_CODES = {
    'Paris':'PAR', 'London':'LON', 'New York':'NYC', 'Tokyo':'TYO',
    'Milan':'MIL', 'Rome':'ROM', 'Chicago':'CHI', 'Washington':'WAS',
    'Berlin':'BER', 'Stockholm':'STO', 'Moscow':'MOW', 'Shanghai':'SHA',
    'Beijing':'BJS', 'Osaka':'OSA', 'São Paulo':'SAO', 'Buenos Aires':'BUE',
    'Rio de Janeiro':'RIO', 'Toronto':'YTO', 'Montreal':'YMQ',
    'Jakarta':'JKT', 'Houston':'HOU',
  };

  const ICON_RECENT = '<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';

  function groupAirports(results) {
    const buckets = new Map();
    results.forEach(a => {
      const key = `${a.city}|${a.country}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(a);
    });
    const out = [];
    for (const [key, airports] of buckets) {
      if (airports.length >= 2) {
        const [city, country] = key.split('|');
        out.push({
          kind:'group', city, country,
          metro_code: METRO_CODES[city] || airports[0].code,
          airports
        });
      } else {
        out.push({kind:'flat', airport: airports[0]});
      }
    }
    return out;
  }

  function setupAirportAutocomplete(inputId) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(inputId + '-dropdown');
    if (!input || !dropdown) return;
    let debounceTimer = null;
    let lastQuery = '';
    let activeIdx = -1;
    let flatItems = [];
    const _acType = 'airport';

    const escH = s => String(s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

    function airportRow(a, idx, opts={}) {
      const cls = opts.sub ? 'ac-item ac-sub' : 'ac-item';
      return `<div class="${cls}" data-flat-idx="${idx}">
        <div class="ac-code">${escH(a.code)}</div>
        <div class="ac-name">
          <strong>${escH(a.city)}${opts.sub ? '' : ' ('+escH(a.code)+')'}</strong>
          <span class="ac-airport">${escH(a.name||'')}</span>
          ${!opts.sub && a.country ? '<div class="ac-country">'+escH(a.country)+'</div>' : ''}
        </div>
      </div>`;
    }

    function renderLive(results) {
      flatItems = []; activeIdx = -1;
      if (!results || !results.length) {
        dropdown.innerHTML = '<div class="ac-empty">Aucun aéroport trouvé</div>';
        showDropdown(dropdown, input);
        return;
      }
      const grouped = groupAirports(results);
      const parts = [];
      grouped.forEach(g => {
        if (g.kind === 'group') {
          flatItems.push({code:g.metro_code, city:g.city, name:'Tous les aéroports de '+g.city, country:g.country});
          const headIdx = flatItems.length - 1;
          const subHtml = g.airports.map(a => {
            flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
            return airportRow(a, flatItems.length - 1, {sub:true});
          }).join('');
          parts.push(`<div class="ac-group-head" data-flat-idx="${headIdx}">
            <div class="ac-code">${escH(g.metro_code)}</div>
            <div class="ac-name">
              <strong>${escH(g.city)}</strong>
              <span class="ac-airport">Tous les aéroports (${g.airports.length})</span>
              ${g.country ? '<div class="ac-country">'+escH(g.country)+'</div>' : ''}
            </div>
          </div>${subHtml}`);
        } else {
          const a = g.airport;
          flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
          parts.push(airportRow(a, flatItems.length - 1));
        }
      });
      dropdown.innerHTML = '<div class="ac-section">'+parts.join('')+'</div>';
      showDropdown(dropdown, input);
    }

    function renderFocus() {
      flatItems = []; activeIdx = -1;
      const recents = RecentSearches.forType('airport');
      const sections = [];
      if (recents.length) {
        const items = recents.map(r => {
          flatItems.push({code:r.code, city:r.city, name:r.name||'', country:r.country||''});
          const idx = flatItems.length - 1;
          return `<div class="ac-item" data-flat-idx="${idx}">
            <div class="ac-recent-ic">${ICON_RECENT}</div>
            <div class="ac-name">
              <strong>${escH(r.city)} (${escH(r.code)})</strong>
              <span class="ac-airport">${escH(r.name||'')}</span>
            </div>
          </div>`;
        }).join('');
        sections.push(`<div class="ac-section">
          <div class="ac-section-head">Recherches récentes
            <button type="button" class="ac-clear-recent" data-action="clear-recent">Effacer</button>
          </div>${items}</div>`);
      }
      const byRegion = {};
      POPULAR_AIRPORTS.forEach(a => {
        if (!byRegion[a.region]) byRegion[a.region] = [];
        byRegion[a.region].push(a);
      });
      Object.entries(byRegion).forEach(([region, items]) => {
        const itemsHtml = items.map(a => {
          flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
          return airportRow(a, flatItems.length - 1);
        }).join('');
        sections.push(`<div class="ac-section">
          <div class="ac-section-head">Aéroports populaires <span class="ac-region-tag">${escH(region)}</span></div>
          ${itemsHtml}</div>`);
      });
      dropdown.innerHTML = sections.join('');
      showDropdown(dropdown, input);
    }

    async function fetchResults(q) {
      try {
        const r = await fetch('/api/airports/search?q=' + encodeURIComponent(q) + '&limit=12');
        if (!r.ok) return [];
        return await r.json();
      } catch (err) { return []; }
    }

    input.addEventListener('input', () => {
      const q = input.value.trim();
      input.dataset.code = '';
      clearTimeout(debounceTimer);
      if (q.length < 2) { renderFocus(); return; }
      debounceTimer = setTimeout(async () => {
        if (q === lastQuery) return;
        lastQuery = q;
        const results = await fetchResults(q);
        renderLive(results);
      }, 200);
    });

    input.addEventListener('focus', () => {
      // Mobile : bascule en plein écran search overlay (style GoVoyages, Pascal 2026-05-31)
      if (window.matchMedia('(max-width: 880px)').matches) {
        setTimeout(() => input.blur(), 0);
        SearchOverlay.open(input, _acType);
        return;
      }
      const q = input.value.trim();
      if (q.length < 2) renderFocus();
      else if (flatItems.length) showDropdown(dropdown, input);
    });

    input.addEventListener('keydown', (e) => {
      if (dropdown.style.display === 'none') return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = Math.min(activeIdx + 1, flatItems.length - 1);
        updateActive();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = Math.max(activeIdx - 1, 0);
        updateActive();
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        selectFlat(activeIdx);
      } else if (e.key === 'Escape') {
        dropdown.style.display = 'none';
      }
    });

    function updateActive() {
      dropdown.querySelectorAll('[data-flat-idx]').forEach(el => {
        const idx = parseInt(el.dataset.flatIdx, 10);
        el.classList.toggle('ac-active', idx === activeIdx);
      });
      const el = dropdown.querySelector('[data-flat-idx].ac-active');
      if (el) el.scrollIntoView({block:'nearest'});
    }

    function selectFlat(idx) {
      const it = flatItems[idx];
      if (!it) return;
      input.value = it.city + ' (' + it.code + ')';
      input.dataset.code = it.code;
      dropdown.style.display = 'none';
    }

    dropdown.addEventListener('mousedown', (e) => {
      if (e.target.closest('[data-action="clear-recent"]')) {
        e.preventDefault();
        RecentSearches.clear();
        renderFocus();
        return;
      }
      const it = e.target.closest('[data-flat-idx]');
      if (!it) return;
      e.preventDefault();
      selectFlat(parseInt(it.dataset.flatIdx, 10));
    });

    document.addEventListener('click', (e) => {
      if (!input.parentElement.contains(e.target)) {
        dropdown.style.display = 'none';
      }
    });
  }

  function setupCityAutocomplete(inputId) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(inputId + '-dropdown');
    if (!input || !dropdown) return;
    let debounceTimer = null;
    let activeIdx = -1;
    let flatItems = [];
    const _acType = 'city';

    const escH = s => String(s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

    function cityRow(c, idx) {
      const countH = (c.hotels !== undefined && c.hotels !== null)
        ? `<span class="ac-airport">${c.hotels} hôtel${c.hotels>1?'s':''} en catalog</span>`
        : `<span class="ac-airport">Voir hôtels disponibles</span>`;
      return `<div class="ac-item" data-flat-idx="${idx}">
        <div class="ac-code">${escH(c.destination_code)}</div>
        <div class="ac-name">
          <strong>${escH(c.city)}</strong>
          ${countH}
          ${c.country_code ? '<div class="ac-country">'+escH(c.country_code)+'</div>' : ''}
        </div>
      </div>`;
    }

    function renderLive(results) {
      flatItems = []; activeIdx = -1;
      if (!results || !results.length) {
        dropdown.innerHTML = '<div class="ac-empty">Aucune ville trouvée</div>';
        showDropdown(dropdown, input);
        return;
      }
      const items = results.map(c => {
        flatItems.push({code:c.destination_code, city:c.city, country:c.country_code});
        return cityRow(c, flatItems.length - 1);
      }).join('');
      dropdown.innerHTML = '<div class="ac-section">'+items+'</div>';
      showDropdown(dropdown, input);
    }

    function renderFocus() {
      flatItems = []; activeIdx = -1;
      const recents = RecentSearches.forType('city');
      const sections = [];
      if (recents.length) {
        const items = recents.map(r => {
          flatItems.push({code:r.code, city:r.city, country:r.country||''});
          const idx = flatItems.length - 1;
          return `<div class="ac-item" data-flat-idx="${idx}">
            <div class="ac-recent-ic">${ICON_RECENT}</div>
            <div class="ac-name">
              <strong>${escH(r.city)} (${escH(r.code)})</strong>
              <span class="ac-airport">${escH(r.country||'')}</span>
            </div>
          </div>`;
        }).join('');
        sections.push(`<div class="ac-section">
          <div class="ac-section-head">Recherches récentes
            <button type="button" class="ac-clear-recent" data-action="clear-recent">Effacer</button>
          </div>${items}</div>`);
      }
      const byRegion = {};
      POPULAR_CITIES.forEach(c => {
        if (!byRegion[c.region]) byRegion[c.region] = [];
        byRegion[c.region].push(c);
      });
      Object.entries(byRegion).forEach(([region, items]) => {
        const itemsHtml = items.map(c => {
          flatItems.push({code:c.destination_code, city:c.city, country:c.country_code});
          return cityRow(c, flatItems.length - 1);
        }).join('');
        sections.push(`<div class="ac-section">
          <div class="ac-section-head">Villes prisées <span class="ac-region-tag">${escH(region)}</span></div>
          ${itemsHtml}</div>`);
      });
      dropdown.innerHTML = sections.join('');
      showDropdown(dropdown, input);
    }

    // Pascal 2026-06-01 — fix #19 : bloquer codes aéroport US (aucun hôtel US en catalogue HBX).
    // Évite que le client tape "JFK" et obtienne un mock factice.
    const US_AIRPORT_CODES = ['JFK','LAX','SFO','ORD','MIA','ATL','BOS','EWR','IAD','SEA','DFW','DEN','PHX','MCO','LGA'];
    async function fetchResults(q) {
      if (q.length < 2) return [];
      // Si le user tape un code aéroport US, renvoyer vide (UX: "Aucune ville trouvée").
      if (US_AIRPORT_CODES.includes(q.toUpperCase())) return [];
      try {
        const r = await fetch('/api/cities/search?q=' + encodeURIComponent(q) + '&limit=8');
        if (!r.ok) return [];
        const results = await r.json();
        // Filtre supplémentaire : enlever toute ligne dont le code matche un aéroport US.
        return (results || []).filter(c => !US_AIRPORT_CODES.includes((c.destination_code || '').toUpperCase()));
      } catch (err) { return []; }
    }

    input.addEventListener('input', () => {
      const q = input.value.trim();
      input.dataset.code = '';
      clearTimeout(debounceTimer);
      if (q.length < 2) { renderFocus(); return; }
      debounceTimer = setTimeout(async () => {
        const results = await fetchResults(q);
        renderLive(results);
      }, 200);
    });

    input.addEventListener('focus', () => {
      // Mobile : bascule en plein écran search overlay (style GoVoyages, Pascal 2026-05-31)
      if (window.matchMedia('(max-width: 880px)').matches) {
        setTimeout(() => input.blur(), 0);
        SearchOverlay.open(input, _acType);
        return;
      }
      const q = input.value.trim();
      if (q.length < 2) renderFocus();
      else if (flatItems.length) showDropdown(dropdown, input);
    });

    input.addEventListener('keydown', (e) => {
      if (dropdown.style.display === 'none') return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = Math.min(activeIdx + 1, flatItems.length - 1);
        updateActive();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = Math.max(activeIdx - 1, 0);
        updateActive();
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        selectFlat(activeIdx);
      } else if (e.key === 'Escape') {
        dropdown.style.display = 'none';
      }
    });

    function updateActive() {
      dropdown.querySelectorAll('[data-flat-idx]').forEach(el => {
        const idx = parseInt(el.dataset.flatIdx, 10);
        el.classList.toggle('ac-active', idx === activeIdx);
      });
      const el = dropdown.querySelector('[data-flat-idx].ac-active');
      if (el) el.scrollIntoView({block:'nearest'});
    }

    function selectFlat(idx) {
      const it = flatItems[idx];
      if (!it) return;
      input.value = it.city + ' (' + it.code + ')';
      input.dataset.code = it.code;
      dropdown.style.display = 'none';
    }

    dropdown.addEventListener('mousedown', (e) => {
      if (e.target.closest('[data-action="clear-recent"]')) {
        e.preventDefault();
        RecentSearches.clear();
        renderFocus();
        return;
      }
      const it = e.target.closest('[data-flat-idx]');
      if (!it) return;
      e.preventDefault();
      selectFlat(parseInt(it.dataset.flatIdx, 10));
    });

    document.addEventListener('click', (e) => {
      if (!input.parentElement.contains(e.target)) {
        dropdown.style.display = 'none';
      }
    });
  }

  // CSS injection (1 fois) — sections, group-head, sub-items, recent icon, etc.
  // Permet d'oublier le CSS dans les pages qui incluent ce carnet.
  function injectCss() {
    if (document.getElementById('ab-search-ac-css')) return;
    const css = `
      .ac-dropdown{max-height:420px;overscroll-behavior:contain;}
      .ac-section + .ac-section{border-top:1px solid #f0e9c8;}
      .ac-section-head{
        padding:9px 14px 6px;font-size:10.5px;font-weight:700;letter-spacing:0.5px;
        color:#7a6a2e;text-transform:uppercase;background:#fdfaf0;
        border-bottom:1px solid #f4efde;display:flex;align-items:center;
        justify-content:space-between;
      }
      .ac-section-head .ac-region-tag{
        font-weight:600;color:#a99a4a;font-size:9.5px;letter-spacing:.4px;
        text-transform:none;
      }
      .ac-clear-recent{
        background:transparent;border:none;color:#b0a26a;font-size:10px;cursor:pointer;
        padding:0;letter-spacing:.3px;text-transform:none;font-family:'Inter',sans-serif;
      }
      .ac-clear-recent:hover{color:#7a6a2e;text-decoration:underline;}
      .ac-group-head{
        padding:11px 14px 8px;cursor:pointer;display:flex;align-items:center;gap:12px;
        border-bottom:1px solid #f4efde;transition:background .12s;
        background:linear-gradient(to right, #fffdf5, #fff);
      }
      .ac-group-head:hover,.ac-group-head.ac-active{background:#fcf6e3;}
      .ac-item.ac-sub{padding-left:42px;background:#fbfaf3;}
      .ac-item.ac-sub .ac-code{font-size:10.5px;min-width:38px;padding:3px 7px;}
      .ac-item.ac-sub .ac-name strong{font-size:13px;color:#444;font-weight:500;}
      .ac-recent-ic{
        display:inline-block;width:18px;height:18px;flex-shrink:0;color:#c9a961;
      }
      .ac-recent-ic svg{width:100%;height:100%;stroke:currentColor;fill:none;stroke-width:1.8;}
      /* MOBILE : la dropdown classique reste utile en fallback, mais le focus
         déclenche en réalité l'overlay plein écran SearchOverlay (style GoVoyages). */
      @media(max-width:880px){
        .ac-dropdown{
          max-height:55vh;
          box-shadow:0 12px 32px rgba(0,0,0,0.40);
        }
        .ac-section-head{padding:8px 12px 5px;font-size:10px;}
        .ac-item.ac-sub{padding-left:34px;}
        .ac-name strong{white-space:normal;}
      }
      /* ── SearchOverlay : plein écran mobile (Pascal 2026-05-31) ── */
      #ab-search-overlay{
        position:fixed;inset:0;z-index:9999;background:#fff;
        display:none;flex-direction:column;
        font-family:'Inter',sans-serif;
      }
      #ab-search-overlay.open{display:flex;}
      .abo-header{
        flex-shrink:0;background:linear-gradient(to bottom,#1a1410 0%,#2a1f15 100%);
        padding:14px 10px;display:flex;align-items:center;gap:8px;
        box-shadow:0 4px 14px rgba(0,0,0,0.3);
        padding-top:calc(14px + env(safe-area-inset-top,0));
      }
      .abo-back{
        background:transparent;border:none;color:#d4ae4a;
        font-size:30px;line-height:1;padding:4px 10px;cursor:pointer;
        flex-shrink:0;font-family:'DM Serif Display',serif;
      }
      .abo-input{
        flex:1;background:rgba(255,255,255,0.97);
        border:1px solid rgba(184,150,46,0.3);border-radius:8px;
        padding:13px 14px;font-size:16px;color:#1a1a2e;
        font-family:inherit;outline:none;min-width:0;
      }
      .abo-input:focus{border-color:#c9a961;}
      .abo-clear{
        background:rgba(0,0,0,0.5);border:none;color:#fff;
        width:30px;height:30px;border-radius:50%;cursor:pointer;
        display:flex;align-items:center;justify-content:center;
        font-size:18px;line-height:1;flex-shrink:0;padding:0;
      }
      .abo-clear[hidden]{display:none;}
      .abo-body{
        flex:1;overflow-y:auto;overscroll-behavior:contain;
        background:#fff;-webkit-overflow-scrolling:touch;
        padding-bottom:env(safe-area-inset-bottom,0);
      }
      .abo-body .ac-section{background:#fff;}
      .abo-body .ac-section-head{background:#fafaf0;font-size:11px;padding:10px 14px 7px;}
      .abo-body .ac-item{padding:14px 16px;border-bottom:1px solid #f0e9d8;}
      .abo-body .ac-item:active,.abo-body .ac-item.ac-active{background:#fcf6e3;}
      .abo-body .ac-group-head{padding:14px 16px 10px;}
      .abo-body .ac-empty{padding:30px 20px;text-align:center;color:#999;}
    `;
    const style = document.createElement('style');
    style.id = 'ab-search-ac-css';
    style.textContent = css;
    document.head.appendChild(style);
  }
  injectCss();

  // ═════════════════════════════════════════════════════════
  // SearchOverlay : plein écran mobile (Pascal 2026-05-31)
  // Pattern GoVoyages/Booking/Airbnb : focus champ mobile → bascule fullscreen
  // dédié à la recherche. Header avec champ gros + back, body avec suggestions.
  // ═════════════════════════════════════════════════════════
  const SearchOverlay = {
    _root: null, _input: null, _body: null,
    _sourceInput: null, _type: null,
    _flatItems: [], _debounceTimer: null,

    _escH(s) { return String(s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); },

    _ensureDom() {
      if (this._root) return;
      const root = document.createElement('div');
      root.id = 'ab-search-overlay';
      root.innerHTML =
        '<div class="abo-header">' +
          '<button type="button" class="abo-back" aria-label="Retour">‹</button>' +
          '<input type="text" class="abo-input" autocomplete="off" spellcheck="false" />' +
          '<button type="button" class="abo-clear" aria-label="Effacer" hidden>×</button>' +
        '</div>' +
        '<div class="abo-body"></div>';
      document.body.appendChild(root);
      this._root  = root;
      this._input = root.querySelector('.abo-input');
      this._body  = root.querySelector('.abo-body');

      root.querySelector('.abo-back').addEventListener('click', () => this.close());
      root.querySelector('.abo-clear').addEventListener('click', () => {
        this._input.value = '';
        this._refreshClear();
        this._input.focus();
        this._render('');
      });

      this._input.addEventListener('input', () => {
        const q = this._input.value.trim();
        this._refreshClear();
        clearTimeout(this._debounceTimer);
        this._debounceTimer = setTimeout(() => this._render(q), 200);
      });

      this._body.addEventListener('click', (e) => {
        if (e.target.closest('[data-action="clear-recent"]')) {
          RecentSearches.clear();
          this._render(this._input.value.trim());
          return;
        }
        const it = e.target.closest('[data-flat-idx]');
        if (!it) return;
        const idx = parseInt(it.dataset.flatIdx, 10);
        const sel = this._flatItems[idx];
        if (sel) this._confirm(sel);
      });
    },

    _refreshClear() {
      const btn = this._root.querySelector('.abo-clear');
      if (this._input.value) btn.removeAttribute('hidden');
      else btn.setAttribute('hidden', '');
    },

    open(input, type) {
      this._ensureDom();
      this._sourceInput = input;
      this._type = type;
      this._input.placeholder = input.placeholder || '';
      this._input.value = input.value || '';
      this._refreshClear();
      this._root.classList.add('open');
      document.documentElement.style.overflow = 'hidden';
      setTimeout(() => this._input.focus(), 50);
      this._render(this._input.value.trim());
    },

    close() {
      this._root.classList.remove('open');
      document.documentElement.style.overflow = '';
      if (this._sourceInput) this._sourceInput.blur();
    },

    _confirm(sel) {
      if (!this._sourceInput || !sel) return;
      this._sourceInput.value = sel.city + ' (' + sel.code + ')';
      this._sourceInput.dataset.code = sel.code;
      this.close();
    },

    async _render(q) {
      if (this._type === 'airport') return this._renderAirports(q);
      if (this._type === 'city')    return this._renderCities(q);
    },

    _airportRow(a, idx, opts) {
      opts = opts || {};
      const escH = this._escH;
      const cls = opts.sub ? 'ac-item ac-sub' : 'ac-item';
      return '<div class="' + cls + '" data-flat-idx="' + idx + '">' +
        '<div class="ac-code">' + escH(a.code) + '</div>' +
        '<div class="ac-name">' +
          '<strong>' + escH(a.city) + (opts.sub ? '' : ' (' + escH(a.code) + ')') + '</strong>' +
          '<span class="ac-airport">' + escH(a.name || '') + '</span>' +
          (!opts.sub && a.country ? '<div class="ac-country">' + escH(a.country) + '</div>' : '') +
        '</div></div>';
    },

    _cityRow(c, idx) {
      const escH = this._escH;
      const countH = (c.hotels !== undefined && c.hotels !== null)
        ? '<span class="ac-airport">' + c.hotels + ' hôtel' + (c.hotels > 1 ? 's' : '') + ' en catalog</span>'
        : '<span class="ac-airport">Voir hôtels disponibles</span>';
      return '<div class="ac-item" data-flat-idx="' + idx + '">' +
        '<div class="ac-code">' + escH(c.destination_code) + '</div>' +
        '<div class="ac-name">' +
          '<strong>' + escH(c.city) + '</strong>' + countH +
          (c.country_code ? '<div class="ac-country">' + escH(c.country_code) + '</div>' : '') +
        '</div></div>';
    },

    async _renderAirports(q) {
      this._flatItems = [];
      const escH = this._escH;
      if (q.length >= 2) {
        try {
          const r = await fetch('/api/airports/search?q=' + encodeURIComponent(q) + '&limit=12');
          if (r.ok) {
            const results = await r.json();
            if (!results || !results.length) {
              this._body.innerHTML = '<div class="ac-empty">Aucun aéroport trouvé</div>';
              return;
            }
            const grouped = groupAirports(results);
            const parts = [];
            for (const g of grouped) {
              if (g.kind === 'group') {
                this._flatItems.push({code:g.metro_code, city:g.city, name:'Tous les aéroports de '+g.city, country:g.country});
                const headIdx = this._flatItems.length - 1;
                const subHtml = g.airports.map(a => {
                  this._flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
                  return this._airportRow(a, this._flatItems.length - 1, {sub:true});
                }).join('');
                parts.push(
                  '<div class="ac-group-head" data-flat-idx="' + headIdx + '">' +
                    '<div class="ac-code">' + escH(g.metro_code) + '</div>' +
                    '<div class="ac-name">' +
                      '<strong>' + escH(g.city) + '</strong>' +
                      '<span class="ac-airport">Tous les aéroports (' + g.airports.length + ')</span>' +
                      (g.country ? '<div class="ac-country">' + escH(g.country) + '</div>' : '') +
                    '</div></div>' + subHtml
                );
              } else {
                const a = g.airport;
                this._flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
                parts.push(this._airportRow(a, this._flatItems.length - 1));
              }
            }
            this._body.innerHTML = '<div class="ac-section">' + parts.join('') + '</div>';
            return;
          }
        } catch(_) {}
      }
      // Focus mode : recents + populaires par région
      const recents = RecentSearches.forType('airport');
      const sections = [];
      if (recents.length) {
        const items = recents.map(r => {
          this._flatItems.push({code:r.code, city:r.city, name:r.name||'', country:r.country||''});
          const idx = this._flatItems.length - 1;
          return '<div class="ac-item" data-flat-idx="' + idx + '">' +
            '<div class="ac-recent-ic">' + ICON_RECENT + '</div>' +
            '<div class="ac-name">' +
              '<strong>' + escH(r.city) + ' (' + escH(r.code) + ')</strong>' +
              '<span class="ac-airport">' + escH(r.name||'') + '</span>' +
            '</div></div>';
        }).join('');
        sections.push(
          '<div class="ac-section">' +
            '<div class="ac-section-head">Recherches récentes' +
              '<button type="button" class="ac-clear-recent" data-action="clear-recent">Effacer</button>' +
            '</div>' + items + '</div>'
        );
      }
      const byRegion = {};
      POPULAR_AIRPORTS.forEach(a => {
        if (!byRegion[a.region]) byRegion[a.region] = [];
        byRegion[a.region].push(a);
      });
      Object.entries(byRegion).forEach(([region, items]) => {
        const itemsHtml = items.map(a => {
          this._flatItems.push({code:a.code, city:a.city, name:a.name, country:a.country});
          return this._airportRow(a, this._flatItems.length - 1);
        }).join('');
        sections.push(
          '<div class="ac-section">' +
            '<div class="ac-section-head">Aéroports populaires <span class="ac-region-tag">' + escH(region) + '</span></div>' +
            itemsHtml + '</div>'
        );
      });
      this._body.innerHTML = sections.join('');
    },

    async _renderCities(q) {
      this._flatItems = [];
      const escH = this._escH;
      if (q.length >= 2) {
        try {
          const r = await fetch('/api/cities/search?q=' + encodeURIComponent(q) + '&limit=8');
          if (r.ok) {
            const results = await r.json();
            if (!results || !results.length) {
              this._body.innerHTML = '<div class="ac-empty">Aucune ville trouvée</div>';
              return;
            }
            const items = results.map(c => {
              this._flatItems.push({code:c.destination_code, city:c.city, country:c.country_code});
              return this._cityRow(c, this._flatItems.length - 1);
            }).join('');
            this._body.innerHTML = '<div class="ac-section">' + items + '</div>';
            return;
          }
        } catch(_) {}
      }
      // Focus mode
      const recents = RecentSearches.forType('city');
      const sections = [];
      if (recents.length) {
        const items = recents.map(r => {
          this._flatItems.push({code:r.code, city:r.city, country:r.country||''});
          const idx = this._flatItems.length - 1;
          return '<div class="ac-item" data-flat-idx="' + idx + '">' +
            '<div class="ac-recent-ic">' + ICON_RECENT + '</div>' +
            '<div class="ac-name">' +
              '<strong>' + escH(r.city) + ' (' + escH(r.code) + ')</strong>' +
              '<span class="ac-airport">' + escH(r.country||'') + '</span>' +
            '</div></div>';
        }).join('');
        sections.push(
          '<div class="ac-section">' +
            '<div class="ac-section-head">Recherches récentes' +
              '<button type="button" class="ac-clear-recent" data-action="clear-recent">Effacer</button>' +
            '</div>' + items + '</div>'
        );
      }
      const byRegion = {};
      POPULAR_CITIES.forEach(c => {
        if (!byRegion[c.region]) byRegion[c.region] = [];
        byRegion[c.region].push(c);
      });
      Object.entries(byRegion).forEach(([region, items]) => {
        const itemsHtml = items.map(c => {
          this._flatItems.push({code:c.destination_code, city:c.city, country:c.country_code});
          return this._cityRow(c, this._flatItems.length - 1);
        }).join('');
        sections.push(
          '<div class="ac-section">' +
            '<div class="ac-section-head">Villes prisées <span class="ac-region-tag">' + escH(region) + '</span></div>' +
            itemsHtml + '</div>'
        );
      });
      this._body.innerHTML = sections.join('');
    }
  };

  // Helper : affiche la dropdown. Sur mobile : largeur = 75% du viewport, centrée.
  function showDropdown(dropdown, input) {
    dropdown.style.display = 'block';
    if (window.matchMedia('(max-width: 880px)').matches) {
      const parentRect = input.parentElement.getBoundingClientRect();
      const targetWidth = Math.round(window.innerWidth * 0.75);
      const targetLeft  = Math.round((window.innerWidth - targetWidth) / 2);
      // left CSS = position viewport souhaitée - position parent (.ac-wrap)
      dropdown.style.left  = (targetLeft - parentRect.left) + 'px';
      dropdown.style.right = 'auto';
      dropdown.style.width = targetWidth + 'px';
    } else {
      // Desktop : reset → laisse le CSS de base (left:-12px;right:-12px)
      dropdown.style.left  = '';
      dropdown.style.right = '';
      dropdown.style.width = '';
    }
  }

  // Exports globaux (pour appel direct depuis les pages)
  window.RecentSearches = RecentSearches;
  window.POPULAR_AIRPORTS = POPULAR_AIRPORTS;
  window.POPULAR_CITIES = POPULAR_CITIES;
  window.METRO_CODES = METRO_CODES;
  window.groupAirports = groupAirports;
  window.setupAirportAutocomplete = setupAirportAutocomplete;
  window.setupCityAutocomplete = setupCityAutocomplete;
})();


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 3 — SHARED SEARCH STATE (Pascal 2026-05-31)                   ║
   ║  Mémorise origine / destination / dates cross-onglets sur la home,     ║
   ║  ET cross-pages via sessionStorage. Permet à toute page (modifier-     ║
   ║  la-recherche, recap, etc.) de lire l'état courant.                    ║
   ║  Exposé : window.SharedSearch                                          ║
   ║  Pages : /index.html (sync 4 onglets) — extensible.                    ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
(function(){
  if (window.SharedSearch) return;
  const KEY = 'ab_shared_search_v1';
  const state = {
    origin_code: '',        origin_label: '',
    dest_airport_code: '',  dest_airport_label: '',
    dest_city_code: '',     dest_city_label: '',
    check_in: '', check_out: '',
  };
  function save()  { try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch(_){} }
  function load()  { try { const j = JSON.parse(sessionStorage.getItem(KEY) || 'null'); if (j) Object.assign(state, j); } catch(_){} }
  function clear() { Object.keys(state).forEach(k => state[k] = ''); try { sessionStorage.removeItem(KEY); } catch(_){} }
  load();
  window.SharedSearch = Object.assign(state, { save, load, clear });
})();


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 4 — SEATS (Pascal 2026-05-31)                                 ║
   ║  Store partagé seat_id → designator (et prix) par leg.                 ║
   ║  Évite que les pages affichent l'ID Duffel brut "ase_0000B6qw..."      ║
   ║  Cross-pages via sessionStorage (passengers/checkout/sejour).          ║
   ║  Exposé : window.Seats { setMap, getLabel, getPrice, clear }           ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
(function(){
  if (window.Seats) return;
  const KEY = 'ab_seats_store_v1';
  const store = {
    outbound: { labels: {}, prices: {} },
    inbound:  { labels: {}, prices: {} },
  };
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(store)); } catch(_){} }
  function load() {
    try {
      const j = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (j) {
        if (j.outbound) { store.outbound.labels = j.outbound.labels || {}; store.outbound.prices = j.outbound.prices || {}; }
        if (j.inbound)  { store.inbound.labels  = j.inbound.labels  || {}; store.inbound.prices  = j.inbound.prices  || {}; }
      }
    } catch(_){}
  }
  function setMap(leg, data) {
    if (!data || (leg !== 'outbound' && leg !== 'inbound')) return;
    const labels = {}, prices = {};
    (data.cabins || []).forEach(c => (c.rows || []).forEach(row => (row.seats || []).forEach(s => {
      if (s && s.id) {
        if (s.designator) labels[s.id] = s.designator;
        if ((s.price || 0) > 0) prices[s.id] = Number(s.price);
      }
    })));
    store[leg].labels = labels;
    store[leg].prices = prices;
    save();
  }
  function getLabel(leg, seatId) {
    if (!seatId) return '';
    return (store[leg] && store[leg].labels[seatId]) || '';
  }
  function getPrice(leg, seatId) {
    if (!seatId) return 0;
    return (store[leg] && store[leg].prices[seatId]) || 0;
  }
  function clear() {
    store.outbound = { labels: {}, prices: {} };
    store.inbound  = { labels: {}, prices: {} };
    try { sessionStorage.removeItem(KEY); } catch(_){}
  }
  load();
  window.Seats = { setMap, getLabel, getPrice, clear };
})();


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 5 — TUNNEL STATE / Cart Recovery N1 (Pascal 2026-05-31)       ║
   ║  Sauvegarde du parcours en localStorage → si user abandonne et revient,║
   ║  bannière "Reprendre votre voyage" + re-fetch prix LIVE (jamais        ║
   ║  d'affichage de prix périmé).                                          ║
   ║  Exposé : window.TunnelState { save, load, clear, hasRecent, render-  ║
   ║           ResumeBanner }                                               ║
   ║  Persistance : localStorage (cross-onglet + survit refresh)            ║
   ║  TTL : 24h (au-delà, on purge — éviter polluer avec des vieux state)   ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
(function(){
  if (window.TunnelState) return;
  const KEY = 'ab_tunnel_state_v1';
  const TTL_MS = 24 * 3600 * 1000;  // 24h

  function save(partial) {
    const current = load() || {};
    const merged = {
      ...current,
      ...partial,
      _ts: Date.now(),
      _path: location.pathname,
      _url: location.href,
    };
    try { localStorage.setItem(KEY, JSON.stringify(merged)); } catch(_){}
    // N2 cross-device : si user connecté, sync en DB aussi (best-effort, silencieux)
    if (window.Auth && window.Auth.isLoggedIn()) {
      try {
        window.Auth.fetch('/api/user/tunnel-state', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(merged)
        }).catch(() => {});
      } catch(_){}
    }
    return merged;
  }
  function load() {
    try {
      const j = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (!j) return null;
      // Purge si trop vieux
      if (j._ts && (Date.now() - j._ts) > TTL_MS) {
        clear();
        return null;
      }
      return j;
    } catch(_) { return null; }
  }
  function clear() {
    try { localStorage.removeItem(KEY); } catch(_){}
  }
  function hasRecent(maxAgeMs) {
    const j = load();
    if (!j || !j._ts) return false;
    return (Date.now() - j._ts) < (maxAgeMs || TTL_MS);
  }
  function ageMinutes() {
    const j = load();
    if (!j || !j._ts) return null;
    return Math.floor((Date.now() - j._ts) / 60000);
  }

  // Rend une bannière "Reprendre votre voyage" en haut d'une page (home, etc.)
  // Le contenu est résumé à partir du state. Le bouton "Reprendre" renvoie sur _url.
  // Si l'utilisateur clique "Annuler", on clear() et on cache la bannière.
  function renderResumeBanner(containerEl) {
    if (!containerEl) return false;
    const j = load();
    if (!j || !j._url || j._url === location.href) return false;  // pas de state, ou déjà sur la page
    const age = ageMinutes();
    const ageLabel = age < 60 ? `${age} min` : age < 1440 ? `${Math.floor(age/60)} h` : `${Math.floor(age/1440)} j`;
    // Résumé concis : destination + dates si disponibles dans le state
    const ctx = j.search_context || {};
    const dest = ctx.destination_label || ctx.dest_airport_label || ctx.dest_city_label || '';
    const origin = ctx.origin_label || '';
    const route = origin && dest ? `${origin} → ${dest}` : (dest || origin || 'votre réservation');
    const step = j.step_label || j.step_actuel || 'votre parcours';
    containerEl.innerHTML = `
      <div style="background:linear-gradient(to right, rgba(184,150,46,0.18), rgba(184,150,46,0.08));border:1px solid var(--border2, rgba(184,150,46,0.4));border-radius:12px;padding:14px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:14px auto;max-width:1080px;">
        <div style="font-size:24px;line-height:1;">⏯️</div>
        <div style="flex:1;min-width:200px;">
          <div style="font-family:'DM Serif Display',serif;font-size:16px;color:var(--text,#f0ece4);margin-bottom:3px;">Reprendre votre voyage</div>
          <div style="font-size:12.5px;color:var(--text2,#a09890);">Vous étiez sur ${route} (${step}) il y a ${ageLabel}. Les prix seront actualisés.</div>
        </div>
        <button type="button" onclick="window.location.href='${j._url}'" style="padding:10px 18px;background:var(--gold,#b8962e);color:#000;border:none;border-radius:7px;font-family:'DM Sans',sans-serif;font-weight:600;font-size:13px;cursor:pointer;white-space:nowrap;">Reprendre →</button>
        <button type="button" onclick="window.TunnelState.clear(); this.parentElement.style.display='none';" style="padding:10px 14px;background:transparent;color:var(--text2,#a09890);border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:7px;font-family:'DM Sans',sans-serif;font-size:12px;cursor:pointer;white-space:nowrap;">Effacer</button>
      </div>`;
    containerEl.style.display = '';
    return true;
  }

  window.TunnelState = { save, load, clear, hasRecent, ageMinutes, renderResumeBanner };
})();


/* ╔═══════════════════════════════════════════════════════════════════════╗
   ║  SECTION 6 — AUTH (Pascal 2026-05-31)                                  ║
   ║  Client JS pour les endpoints /api/auth/* et /api/user/*               ║
   ║  Stocke JWT + user en localStorage. Helper fetch() qui injecte         ║
   ║  Authorization: Bearer automatiquement.                                ║
   ║  Exposé : window.Auth { signup, login, logout, isLoggedIn, getUser,    ║
   ║                          getToken, fetch, onChange }                   ║
   ║  Événement : 'ab-auth-change' déclenché au login/logout                ║
   ╚═══════════════════════════════════════════════════════════════════════╝ */
(function(){
  if (window.Auth) return;
  const KEY_TOKEN = 'ab_auth_token_v1';
  const KEY_USER  = 'ab_auth_user_v1';

  function _emit() {
    try { window.dispatchEvent(new CustomEvent('ab-auth-change', {detail: getUser()})); } catch(_){}
  }

  function getToken() {
    try { return localStorage.getItem(KEY_TOKEN); } catch(_) { return null; }
  }
  function getUser() {
    try { return JSON.parse(localStorage.getItem(KEY_USER) || 'null'); }
    catch(_) { return null; }
  }
  function isLoggedIn() { return !!getToken(); }

  function _persist(token, user) {
    try {
      localStorage.setItem(KEY_TOKEN, token);
      localStorage.setItem(KEY_USER, JSON.stringify(user));
    } catch(_){}
    _emit();
  }

  async function signup({email, password, first_name, last_name}) {
    const r = await fetch('/api/auth/signup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password, first_name, last_name})
    });
    const d = await r.json();
    if (!r.ok) {
      const msg = (d.detail && typeof d.detail === 'string') ? d.detail
                : (d.detail && d.detail[0] && d.detail[0].msg) ? d.detail[0].msg
                : 'Inscription échouée';
      throw new Error(msg);
    }
    _persist(d.token, d.user);
    return d;
  }

  async function login({email, password}) {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password})
    });
    const d = await r.json();
    if (!r.ok) {
      const msg = (d.detail && typeof d.detail === 'string') ? d.detail : 'Connexion échouée';
      throw new Error(msg);
    }
    _persist(d.token, d.user);
    return d;
  }

  function logout() {
    try {
      localStorage.removeItem(KEY_TOKEN);
      localStorage.removeItem(KEY_USER);
    } catch(_){}
    _emit();
  }

  // Wrapper fetch qui ajoute Authorization: Bearer <JWT> automatiquement
  async function authFetch(url, options) {
    options = options || {};
    const t = getToken();
    const headers = Object.assign({}, options.headers || {});
    if (t) headers['Authorization'] = 'Bearer ' + t;
    const r = await fetch(url, Object.assign({}, options, {headers}));
    // Si 401 → token invalide/expiré → on logout proprement
    if (r.status === 401 && t) {
      logout();
    }
    return r;
  }

  // Helper pour s'abonner aux changements auth (header peut re-render)
  function onChange(cb) {
    window.addEventListener('ab-auth-change', () => cb(getUser()));
  }

  window.Auth = {
    signup, login, logout, isLoggedIn, getUser, getToken,
    fetch: authFetch, onChange
  };
})();
