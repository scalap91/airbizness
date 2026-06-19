// Bandeau de consentement cookies — RGPD/CNIL v1
// Stocke le choix dans localStorage.ab_cookie_consent: 'accepted' | 'refused'
// L'absence de choix = pas de cookies non-essentiels.
(function () {
  if (typeof window === 'undefined') return;
  var KEY = 'ab_cookie_consent';
  // Émet un signal global pour que les traceurs tiers (ex. CJ am.js) ne se chargent
  // qu'APRÈS consentement explicite. detail = 'accepted' | 'refused'.
  function signalConsent(choice) {
    try { window.dispatchEvent(new CustomEvent('ab-consent', { detail: choice })); } catch (e) {}
  }
  window.abConsent = function () { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  var existing = null;
  try { existing = localStorage.getItem(KEY); } catch (e) {}
  if (existing === 'accepted' || existing === 'refused') { signalConsent(existing); return; }

  function injectStyles() {
    if (document.getElementById('ab-cookie-styles')) return;
    var s = document.createElement('style');
    s.id = 'ab-cookie-styles';
    s.textContent = [
      // Banner COMPACT — barre fine en bas, ne masque jamais le hero
      '.ab-cookie-banner{position:fixed;left:0;right:0;bottom:0;z-index:9999;',
      'background:rgba(15,15,15,0.96);backdrop-filter:blur(12px);',
      'border-top:1px solid rgba(184,150,46,0.25);',
      'padding:14px 24px;box-shadow:0 -8px 32px rgba(0,0,0,0.4);',
      'font-family:"DM Sans",system-ui,sans-serif;color:#f0ece4;',
      'display:flex;align-items:center;gap:18px;flex-wrap:wrap;justify-content:center;}',
      '.ab-cookie-text{flex:1;min-width:260px;max-width:680px;font-size:12.5px;line-height:1.5;color:#a09890;}',
      '.ab-cookie-text strong{color:#f0ece4;font-weight:600;}',
      '.ab-cookie-text a{color:#d4ae4a;}',
      '.ab-cookie-actions{display:flex;gap:8px;flex-shrink:0;}',
      '.ab-cookie-btn{padding:9px 18px;border-radius:6px;',
      'font-family:inherit;font-size:12px;font-weight:600;cursor:pointer;border:1px solid transparent;white-space:nowrap;}',
      '.ab-cookie-accept{background:#b8962e;color:#000;}',
      '.ab-cookie-accept:hover{background:#d4ae4a;}',
      '.ab-cookie-refuse{background:transparent;border-color:rgba(255,255,255,0.15);color:#a09890;}',
      '.ab-cookie-refuse:hover{background:rgba(255,255,255,0.05);color:#f0ece4;}',
      '@media(max-width:680px){',
      '  .ab-cookie-banner{padding:12px 14px;gap:10px;flex-direction:column;align-items:stretch;}',
      '  .ab-cookie-text{font-size:12px;}',
      '  .ab-cookie-actions{justify-content:stretch;}',
      '  .ab-cookie-btn{flex:1;padding:10px 14px;}',
      '}',
    ].join('');
    document.head.appendChild(s);
  }

  function dismiss(choice) {
    try { localStorage.setItem(KEY, choice); } catch (e) {}
    var el = document.getElementById('ab-cookie-banner');
    if (el) el.remove();
    document.body.style.paddingBottom = '';
    signalConsent(choice);
  }

  function build() {
    injectStyles();
    var banner = document.createElement('div');
    banner.id = 'ab-cookie-banner';
    banner.className = 'ab-cookie-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-label', 'Consentement aux cookies');
    banner.innerHTML =
      '<div class="ab-cookie-text">' +
        '<strong>Cookies essentiels uniquement.</strong> ' +
        'Aucun traceur tiers sans votre accord. ' +
        '<a href="/mentions-legales.html">Politique de confidentialité</a>.' +
      '</div>' +
      '<div class="ab-cookie-actions">' +
        '<button class="ab-cookie-btn ab-cookie-refuse" type="button">Refuser</button>' +
        '<button class="ab-cookie-btn ab-cookie-accept" type="button">Accepter</button>' +
      '</div>';
    var btns = banner.querySelectorAll('button');
    btns[0].addEventListener('click', function () { dismiss('refused'); });
    btns[1].addEventListener('click', function () { dismiss('accepted'); });
    document.body.appendChild(banner);
    // Réserve l'espace pour ne pas masquer le footer
    requestAnimationFrame(function () {
      var h = banner.getBoundingClientRect().height;
      var currentPb = parseInt(getComputedStyle(document.body).paddingBottom, 10) || 0;
      document.body.style.paddingBottom = (currentPb + h + 12) + 'px';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
