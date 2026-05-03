// Bandeau de consentement cookies — RGPD/CNIL v1
// Stocke le choix dans localStorage.ab_cookie_consent: 'accepted' | 'refused'
// L'absence de choix = pas de cookies non-essentiels.
(function () {
  if (typeof window === 'undefined') return;
  var KEY = 'ab_cookie_consent';
  var existing = null;
  try { existing = localStorage.getItem(KEY); } catch (e) {}
  if (existing === 'accepted' || existing === 'refused') return;

  function injectStyles() {
    if (document.getElementById('ab-cookie-styles')) return;
    var s = document.createElement('style');
    s.id = 'ab-cookie-styles';
    s.textContent = [
      '.ab-cookie-banner{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;',
      'background:#161616;border:1px solid rgba(184,150,46,0.3);border-radius:10px;',
      'padding:18px 20px;box-shadow:0 16px 40px rgba(0,0,0,0.55);',
      'font-family:"DM Sans",system-ui,sans-serif;color:#f0ece4;',
      'display:flex;flex-direction:column;gap:12px;max-width:560px;margin:0 auto;}',
      '.ab-cookie-banner h3{font-size:14px;font-weight:600;color:#d4ae4a;margin:0;}',
      '.ab-cookie-banner p{font-size:12.5px;line-height:1.55;color:#a09890;margin:0;}',
      '.ab-cookie-banner p a{color:#d4ae4a;}',
      '.ab-cookie-actions{display:flex;gap:8px;flex-wrap:wrap;}',
      '.ab-cookie-btn{flex:1;min-width:120px;padding:10px 14px;border-radius:6px;',
      'font-family:inherit;font-size:12px;font-weight:600;cursor:pointer;border:1px solid transparent;}',
      '.ab-cookie-accept{background:#b8962e;color:#000;}',
      '.ab-cookie-accept:hover{background:#d4ae4a;}',
      '.ab-cookie-refuse{background:transparent;border-color:rgba(255,255,255,0.15);color:#f0ece4;}',
      '.ab-cookie-refuse:hover{background:rgba(255,255,255,0.05);}',
      '@media(max-width:520px){.ab-cookie-banner{left:8px;right:8px;bottom:8px;padding:14px 16px;}',
      '.ab-cookie-btn{min-width:0;}}',
    ].join('');
    document.head.appendChild(s);
  }

  function dismiss(choice) {
    try { localStorage.setItem(KEY, choice); } catch (e) {}
    var el = document.getElementById('ab-cookie-banner');
    if (el) el.remove();
  }

  function build() {
    injectStyles();
    var banner = document.createElement('div');
    banner.id = 'ab-cookie-banner';
    banner.className = 'ab-cookie-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-label', 'Consentement aux cookies');
    banner.innerHTML =
      '<h3>Cookies & confidentialité</h3>' +
      '<p>Nous utilisons des cookies strictement nécessaires au fonctionnement du site (session de réservation, paiement). ' +
      'Aucun cookie publicitaire ni traceur tiers n\'est posé sans votre accord. ' +
      'Voir la <a href="/mentions-legales.html">politique de confidentialité</a>.</p>' +
      '<div class="ab-cookie-actions">' +
      '<button class="ab-cookie-btn ab-cookie-refuse" type="button">Refuser</button>' +
      '<button class="ab-cookie-btn ab-cookie-accept" type="button">Accepter</button>' +
      '</div>';
    var btns = banner.querySelectorAll('button');
    btns[0].addEventListener('click', function () { dismiss('refused'); });
    btns[1].addEventListener('click', function () { dismiss('accepted'); });
    document.body.appendChild(banner);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
