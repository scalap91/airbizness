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

    async function fetchResults(q) {
      if (q.length < 2) return [];
      try {
        const r = await fetch('/api/cities/search?q=' + encodeURIComponent(q) + '&limit=8');
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
