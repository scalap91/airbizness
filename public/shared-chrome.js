/* AirBizness — Header, bottom-nav mobile et footer partagés.
   Inclure sur chaque page :
     <script defer src="/shared-chrome.js"></script>
     <div id="ab-header"></div>   (placé en haut)
     <div id="ab-bottomnav"></div> (mobile bottom)
     <div id="ab-footer"></div>   (placé en bas)
   Le JS injecte le CSS scoped + le HTML.
*/
(function(){
  'use strict';
  if (window.__abChromeLoaded) return;
  window.__abChromeLoaded = true;

  // Google Analytics (GA4) — chargé sur toutes les pages incluant shared-chrome.js
  if (!window.__abGA) {
    window.__abGA = true;
    var _ga = document.createElement('script');
    _ga.async = true;
    _ga.src = 'https://www.googletagmanager.com/gtag/js?id=G-J6GDD5N054';
    (document.head || document.documentElement).appendChild(_ga);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function(){ dataLayer.push(arguments); };
    gtag('js', new Date());
    gtag('config', 'G-J6GDD5N054');
  }

  var STYLE_ID = 'ab-shared-chrome-style';
  if (!document.getElementById(STYLE_ID)) {
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '.ab-header{position:fixed;top:0;left:0;right:0;z-index:90;display:flex;align-items:center;justify-content:space-between;padding:0 24px;height:60px;background:rgba(15,15,15,0.85);backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,0.07);}',
      '.ab-header .ab-logo{display:flex;align-items:center;gap:12px;text-decoration:none;color:#f0ece4;}',
      // Monogramme italique calligraphique style Vogue/Tiffany (or sur noir)
      '.ab-header .ab-logo-mark{width:46px;height:34px;display:grid;place-items:center;flex-shrink:0;color:#d4ae4a;}',
      '.ab-header .ab-logo-mark svg{width:100%;height:100%;display:block;overflow:visible;}',
      '.ab-header .ab-logo-text{font-family:"DM Serif Display",Georgia,serif;font-size:20px;font-weight:400;letter-spacing:-0.005em;line-height:1;}',
      '.ab-header .ab-logo-text em{font-style:italic;color:#d4ae4a;font-weight:400;}',
      '.ab-nav{display:flex;gap:28px;}',
      '.ab-nav a{font-size:13px;color:#a09890;text-decoration:none;font-weight:500;transition:color .15s;}',
      '.ab-nav a:hover,.ab-nav a.active{color:#d4ae4a;}',
      '.ab-header-right{display:flex;gap:8px;align-items:center;}',
      '.ab-header-btn{padding:8px 14px;border:1px solid rgba(184,150,46,0.2);background:transparent;color:#d4ae4a;font-family:inherit;font-size:12px;font-weight:500;cursor:pointer;border-radius:6px;text-decoration:none;transition:all .15s;}',
      '.ab-header-btn:hover{background:rgba(184,150,46,0.12);}',
      '.ab-header-btn.fill{background:#b8962e;border-color:#b8962e;color:#000;font-weight:600;}',
      '.ab-header-btn.fill:hover{background:#d4ae4a;}',
      '@media(max-width:880px){',
      '  .ab-header{padding:0 14px;height:56px;}',
      '  .ab-nav{display:none;}',
      '  .ab-header-btn{display:none;}',
      '  .ab-header-btn.fill{display:inline-flex;padding:7px 14px;font-size:12px;}',
      '}',
      // Padding top body pour libérer espace header fixed
      'body{padding-top:60px;}',
      '@media(max-width:880px){body{padding-top:56px;}}',
      // Cache les anciens headers legacy quand le shared est présent (évite doublons)
      'header.header{display:none !important;}',
      // Bottom nav mobile
      '.ab-bottomnav{display:none;position:fixed;bottom:0;left:0;right:0;background:rgba(22,22,22,0.96);backdrop-filter:blur(12px);border-top:1px solid rgba(255,255,255,0.07);z-index:80;padding-bottom:env(safe-area-inset-bottom);}',
      '.ab-bottomnav-inner{display:flex;}',
      '.ab-bn-item{flex:1;display:flex;flex-direction:column;align-items:center;padding:9px 0 7px;text-decoration:none;color:#6a6058;font-size:10px;gap:3px;transition:color .15s;}',
      '.ab-bn-item.active{color:#d4ae4a;}',
      '.ab-bn-icon{font-size:17px;}',
      '@media(max-width:880px){',
      '  .ab-bottomnav{display:block;}',
      '  body{padding-bottom:60px;}',
      '}',
      // Footer
      '.ab-footer{padding:50px 24px 36px;border-top:1px solid rgba(255,255,255,0.07);color:#6a6058;font-size:12px;background:#0f0f0f;margin-top:60px;}',
      '.ab-footer-inner{max-width:1200px;margin:0 auto;}',
      '.ab-footer-cols{display:grid;grid-template-columns:1.6fr 1fr 1fr 1fr 1fr;gap:26px;margin-bottom:26px;}',
      '@media(max-width:980px){.ab-footer-cols{grid-template-columns:1fr 1fr 1fr;gap:22px;}}',
      '@media(max-width:680px){.ab-footer-cols{grid-template-columns:1fr 1fr;gap:20px;}}',
      '.ab-footer-col h4{font-family:DM Serif Display,serif;color:#f0ece4;font-size:13px;margin:0 0 12px;font-weight:400;}',
      '.ab-footer-col a{display:block;color:#a09890;text-decoration:none;font-size:12px;padding:4px 0;transition:color .15s;}',
      '.ab-footer-col a:hover{color:#d4ae4a;}',
      '.ab-footer-brand{color:#a09890;font-size:12px;line-height:1.7;}',
      '.ab-footer-brand .ab-mini{display:inline-flex;align-items:center;gap:10px;color:#f0ece4;font-family:"DM Serif Display",Georgia,serif;font-size:15px;font-weight:400;margin-bottom:8px;}',
      '.ab-footer-brand .ab-mini em{font-style:italic;color:#d4ae4a;font-weight:400;}',
      '.ab-footer-brand .ab-mini-mark{width:26px;height:18px;color:#d4ae4a;display:inline-block;}',
      '.ab-footer-brand .ab-mini-mark svg{width:100%;height:100%;display:block;overflow:visible;}',
      '.ab-footer-bot{padding-top:22px;border-top:1px solid rgba(255,255,255,0.07);text-align:center;font-size:11px;color:#6a6058;}',
    ].join('');
    document.head.appendChild(s);
  }

  function activePath(){ return location.pathname || '/'; }

  function isActive(targetPath){
    var p = activePath();
    if (targetPath === '/' && p === '/') return true;
    if (targetPath !== '/' && p.indexOf(targetPath.replace('.html','')) !== -1) return true;
    return false;
  }

  /* Monogramme italique calligraphique "𝒜𝐵" en SVG (style Vogue/Tiffany).
     Utilise DM Serif Display italic (déjà chargée sur le site). */
  function logoMonogramSVG(){
    return '<svg viewBox="0 0 90 50" xmlns="http://www.w3.org/2000/svg" aria-label="AirBizness">' +
      '<text x="45" y="40" text-anchor="middle" ' +
      'font-family="DM Serif Display, Playfair Display, Georgia, serif" ' +
      'font-style="italic" font-weight="400" font-size="48" ' +
      'fill="currentColor" letter-spacing="-1">AB</text>' +
      // Fioriture trait fin sous les lettres (signature)
      '<line x1="22" y1="46" x2="68" y2="46" stroke="currentColor" stroke-width="0.6" opacity="0.5"/>' +
    '</svg>';
  }

  // Commutateur de verticales (etude_switch_verticales.md, étape 1) — masque les
  // liens nav dont la verticale n'est pas active. Source = /api/config/verticals
  // (AIRBIZNESS_VERTICALS dans .env). Cache module pour les re-render (auth-change).
  var _VERTS = null;
  function applyVertGate(){
    function run(enabled){
      var links = document.querySelectorAll('#ab-header [data-vreq], #ab-bottomnav [data-vreq], #ab-footer [data-vreq]');
      for (var i = 0; i < links.length; i++) {
        var req = links[i].getAttribute('data-vreq').split(',');
        var ok = true;
        for (var j = 0; j < req.length; j++) { if (enabled.indexOf(req[j]) < 0) { ok = false; break; } }
        if (!ok) links[i].style.display = 'none';
      }
      // Texte footer adapté aux 3 positions (pas d'allusion croisée)
      var mode = (enabled.indexOf('hotels')>=0 && enabled.indexOf('flights')>=0) ? 'both'
               : (enabled.indexOf('flights')>=0 ? 'flights' : 'hotels');
      var FT = { hotels:'Hôtels premium, réservés en direct.',
                 flights:'Vols Business, réservés en direct.',
                 both:'Vols, hôtels, activités, transferts.' };
      var ft = document.getElementById('ab-foot-verticals');
      if (ft) ft.textContent = FT[mode];
    }
    if (_VERTS) { run(_VERTS); return; }
    fetch('/api/config/verticals').then(function(r){ return r.json(); }).then(function(cfg){
      _VERTS = (cfg && cfg.enabled && cfg.enabled.length) ? cfg.enabled : ['hotels'];
      run(_VERTS);
    }).catch(function(){ _VERTS = ['hotels']; run(_VERTS); }); // défaut sûr = hôtels seuls
  }

  function injectHeader(){
    var el = document.getElementById('ab-header');
    if (!el) return;
    // Bouton compte adaptatif (Pascal 2026-05-31) : "Connexion" si pas auth,
    // "Mon compte (prénom)" si connecté. Re-render au login/logout via évent.
    var u = (window.Auth && window.Auth.isLoggedIn()) ? window.Auth.getUser() : null;
    var rightBtn = u
      ? '<a href="/compte.html" class="ab-header-btn fill" title="Mon espace voyageur">Mon compte · ' + escHtml(u.given_name || u.full_name || u.email.split('@')[0]) + '</a>'
      : '<a href="/login.html?next=' + encodeURIComponent(location.pathname + location.search) + '" class="ab-header-btn fill">Connexion</a>';
    el.innerHTML =
      '<header class="ab-header">' +
        '<a href="/" class="ab-logo">' +
          '<div class="ab-logo-mark">' + logoMonogramSVG() + '</div>' +
          '<div class="ab-logo-text">Air<em>Bizness</em></div>' +
        '</a>' +
        '<nav class="ab-nav">' +
          '<a href="/" data-vreq="flights" class="' + (isActive('/')?'active':'') + '">Vols</a>' +
          '<a href="/hotels.html" data-vreq="hotels" class="' + (isActive('/hotels')?'active':'') + '">Hôtels</a>' +
          '<a href="/sejour.html" data-vreq="hotels,flights" class="' + (isActive('/sejour')?'active':'') + '">Séjours</a>' +
          '<a href="/activites.html" data-vreq="flights" class="' + (isActive('/activites')?'active':'') + '">Activités</a>' +
          '<a href="/mes-voyages.html" class="' + (isActive('/mes-voyages')?'active':'') + '">Mes voyages</a>' +
        '</nav>' +
        '<div class="ab-header-right">' +
          '<a href="/mes-alertes.html" class="ab-header-btn">Mes alertes</a>' +
          rightBtn +
        '</div>' +
      '</header>';
    applyVertGate();
  }

  // Petit helper d'échappement HTML pour le given_name/full_name du user
  function escHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  // Re-render le header quand le user se connecte/déconnecte (évent émis par le carnet Auth)
  window.addEventListener('ab-auth-change', injectHeader);

  function injectBottomNav(){
    var el = document.getElementById('ab-bottomnav');
    if (!el) return;
    // SVG line-art monochrome — stroke currentColor pour héritage couleur
    var ICON_STYLE = 'width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;';
    var iconPlane = '<svg viewBox="0 0 24 24" style="'+ICON_STYLE+'"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>';
    var iconHotel = '<svg viewBox="0 0 24 24" style="'+ICON_STYLE+'"><path d="M3 21V8l9-5 9 5v13"/><path d="M9 21v-6h6v6"/><path d="M3 21h18"/></svg>';
    var iconStar = '<svg viewBox="0 0 24 24" style="'+ICON_STYLE+'"><path d="M12 2 14.5 9 22 9.5 16 14 18 21 12 17 6 21 8 14 2 9.5 9.5 9z"/></svg>';
    var iconUser = '<svg viewBox="0 0 24 24" style="'+ICON_STYLE+'"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>';
    el.innerHTML =
      '<nav class="ab-bottomnav">' +
        '<div class="ab-bottomnav-inner">' +
          '<a class="ab-bn-item ' + (isActive('/')?'active':'') + '" data-vreq="flights" href="/"><div class="ab-bn-icon">'+iconPlane+'</div>Vols</a>' +
          '<a class="ab-bn-item ' + (isActive('/hotels')?'active':'') + '" data-vreq="hotels" href="/hotels.html"><div class="ab-bn-icon">'+iconHotel+'</div>Hôtels</a>' +
          '<a class="ab-bn-item ' + (isActive('/activites')?'active':'') + '" data-vreq="flights" href="/activites.html"><div class="ab-bn-icon">'+iconStar+'</div>Activités</a>' +
          '<a class="ab-bn-item ' + (isActive('/mes-voyages')?'active':'') + '" href="/mes-voyages.html"><div class="ab-bn-icon">'+iconUser+'</div>Voyages</a>' +
        '</div>' +
      '</nav>';
    applyVertGate();
  }

  function injectFooter(){
    var el = document.getElementById('ab-footer');
    if (!el) return;
    el.innerHTML =
      '<footer class="ab-footer">' +
        '<div class="ab-footer-inner">' +
          '<div class="ab-footer-cols">' +
            '<div class="ab-footer-col">' +
              '<div class="ab-footer-brand">' +
                '<div class="ab-mini">' +
                  '<span class="ab-mini-mark">' + logoMonogramSVG() + '</span>' +
                  '<span>Air<em>Bizness</em></span>' +
                '</div>' +
                'Marketplace de voyage premium.<br>' +
                '<span id="ab-foot-verticals">Vols, hôtels, activités, transferts.</span><br><br>' +
                '<span style="color:#6a6058;font-size:11px;">Société européenne · Opérations MENA et Asie du Sud-Est.</span>' +
              '</div>' +
            '</div>' +
            '<div class="ab-footer-col">' +
              '<h4>Explorer</h4>' +
              '<a href="/hotels.html">Hôtels</a>' +
              '<a href="/activites.html" data-vreq="flights">Activités</a>' +
              '<a href="/" data-vreq="flights">Vols Business</a>' +
              '<a href="/catalog.html">Catalog hôtels</a>' +
            '</div>' +
            '<div class="ab-footer-col">' +
              '<h4>Mon compte</h4>' +
              '<a href="/mes-voyages.html">Mes voyages</a>' +
              '<a href="/mes-alertes.html">Mes alertes</a>' +
              '<a href="/compte.html">Connexion</a>' +
            '</div>' +
            '<div class="ab-footer-col">' +
              '<h4>Professionnels</h4>' +
              '<a href="/pour-les-hoteliers.html">Espace hôteliers</a>' +
              '<a href="mailto:partenariats@airbizness.com">Partenariats</a>' +
              '<a href="mailto:presse@airbizness.com">Presse</a>' +
              '<a href="/contact.html">Nous contacter</a>' +
            '</div>' +
            '<div class="ab-footer-col">' +
              '<h4>Notre engagement</h4>' +
              '<a href="/notre-garantie.html"><strong style="color:#d4ae4a">Garantie Sérénité</strong></a>' +
              '<a href="/notre-garantie.html#patterns">12 patterns d&#39;anticipation</a>' +
              '<a href="/notre-garantie.html#faq">FAQ &amp; engagements</a>' +
            '</div>' +
            '<div class="ab-footer-col">' +
              '<h4>Légal</h4>' +
              '<a href="/mentions-legales.html">Mentions légales</a>' +
              '<a href="/cgv.html">CGV</a>' +
              '<a href="/confidentialite.html">Confidentialité</a>' +
            '</div>' +
          '</div>' +
          '<div class="ab-footer-bot">© AirBizness 2026 · Marketplace de réservation voyage · Tous droits réservés</div>' +
        '</div>' +
      '</footer>';
  }

  function run(){ injectHeader(); injectBottomNav(); injectFooter(); applyVertGate(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
