/* AirBizness i18n — système simple FR/EN.
   Usage : <span data-i18n="key">Texte FR par défaut</span>
           ou via JS : t('key')
   Langue stockée en localStorage 'ab_lang'.
*/
(function(){
  const TRANSLATIONS = {
    fr: {
      // Header / nav
      'nav.home': 'Accueil',
      'nav.hotels': 'Hôtels',
      'nav.activities': 'Activités',
      'nav.my_trips': 'Mes voyages',
      'nav.back': '← Retour',
      'nav.back_home': '← Accueil',

      // Hotels list
      'hotels.eyebrow': 'Hôtels Curated',
      'hotels.title': 'Notre sélection <em>d\'adresses.</em>',
      'hotels.sub': 'Vous restez sur AirBizness, de la recherche à la confirmation.',
      'hotels.field.destination': 'Destination',
      'hotels.field.checkin': 'Arrivée',
      'hotels.field.checkout': 'Départ',
      'hotels.field.guests': 'Voyageurs',
      'hotels.search_btn': 'Rechercher',
      'hotels.filter.stars3': '★★★ et plus',
      'hotels.filter.stars4': '★★★★ et plus',
      'hotels.filter.stars5': '★★★★★ uniquement',
      'hotels.filter.budget_any': 'Tous budgets',
      'hotels.sort.price_asc': 'Prix croissant',
      'hotels.sort.price_desc': 'Prix décroissant',
      'hotels.sort.stars': 'Étoiles ↓',
      'hotels.address_in': 'Adresses à',
      'hotels.price_from': 'À partir de',
      'hotels.no_result': 'Aucune adresse disponible',

      // Activities
      'activities.eyebrow': 'Expériences & visites',
      'activities.title': 'Activités <em>signature</em>',
      'activities.sub': 'Visites guidées, billets attractions, expériences locales.',

      // Common
      'common.loading': 'Chargement…',
      'common.error': 'Erreur',
      'common.reserve': 'Réserver chez AirBizness',
      'common.view_details': 'Voir détails',
      'common.see_availability': 'Voir disponibilités',
      'common.total': 'Total',
      'common.nights': 'nuits',
      'common.adults': 'adultes',
      'common.adult': 'adulte',

      // My trips
      'trips.title': 'Mes <em>voyages</em>',
      'trips.sub': 'Retrouvez ici toutes vos réservations AirBizness.',
      'trips.email_placeholder': 'Votre email',
      'trips.show_btn': 'Afficher mes voyages',
      'trips.empty.title': 'Aucun voyage enregistré',
      'trips.empty.sub': 'Vous n\'avez pas encore de réservation à cette adresse.',
      'trips.empty.cta': 'Découvrir nos hôtels',
      'trips.status.confirmed': 'Confirmée',
      'trips.status.payment_pending': 'Paiement en cours',
      'trips.status.cancelled': 'Annulée',
      'trips.status.booking_failed': 'Échec',
    },
    en: {
      'nav.home': 'Home',
      'nav.hotels': 'Hotels',
      'nav.activities': 'Activities',
      'nav.my_trips': 'My trips',
      'nav.back': '← Back',
      'nav.back_home': '← Home',

      'hotels.eyebrow': 'Curated hotels',
      'hotels.title': 'Our selection <em>of addresses.</em>',
      'hotels.sub': 'Stay on AirBizness from search to confirmation.',
      'hotels.field.destination': 'Destination',
      'hotels.field.checkin': 'Check-in',
      'hotels.field.checkout': 'Check-out',
      'hotels.field.guests': 'Guests',
      'hotels.search_btn': 'Search',
      'hotels.filter.stars3': '★★★ and up',
      'hotels.filter.stars4': '★★★★ and up',
      'hotels.filter.stars5': '★★★★★ only',
      'hotels.filter.budget_any': 'Any budget',
      'hotels.sort.price_asc': 'Price low → high',
      'hotels.sort.price_desc': 'Price high → low',
      'hotels.sort.stars': 'Stars ↓',
      'hotels.address_in': 'Addresses in',
      'hotels.price_from': 'From',
      'hotels.no_result': 'No availability',

      'activities.eyebrow': 'Experiences & tours',
      'activities.title': 'Signature <em>activities</em>',
      'activities.sub': 'Guided tours, attraction tickets, local experiences.',

      'common.loading': 'Loading…',
      'common.error': 'Error',
      'common.reserve': 'Book on AirBizness',
      'common.view_details': 'View details',
      'common.see_availability': 'Check availability',
      'common.total': 'Total',
      'common.nights': 'nights',
      'common.adults': 'adults',
      'common.adult': 'adult',

      'trips.title': 'My <em>trips</em>',
      'trips.sub': 'Find all your AirBizness bookings here.',
      'trips.email_placeholder': 'Your email',
      'trips.show_btn': 'Show my trips',
      'trips.empty.title': 'No trips yet',
      'trips.empty.sub': 'No booking found for this email.',
      'trips.empty.cta': 'Discover our hotels',
      'trips.status.confirmed': 'Confirmed',
      'trips.status.payment_pending': 'Payment pending',
      'trips.status.cancelled': 'Cancelled',
      'trips.status.booking_failed': 'Failed',
    },
  };

  function getLang(){
    return localStorage.getItem('ab_lang') || 'fr';
  }

  function setLang(lang){
    if (!TRANSLATIONS[lang]) return;
    localStorage.setItem('ab_lang', lang);
    apply();
  }

  function t(key){
    const lang = getLang();
    return (TRANSLATIONS[lang] && TRANSLATIONS[lang][key]) ||
           (TRANSLATIONS.fr[key]) || key;
  }

  function apply(){
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const val = t(key);
      el.innerHTML = val;
    });
    // <option data-i18n="...">
    document.querySelectorAll('option[data-i18n]').forEach(el => {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    // placeholders
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
    });
    document.documentElement.setAttribute('lang', getLang());
  }

  // Switcher UI
  function injectSwitcher(){
    if (document.getElementById('langSwitcher')) return;
    const sw = document.createElement('div');
    sw.id = 'langSwitcher';
    sw.style.cssText = 'position:fixed; bottom:18px; right:18px; z-index:1000; display:flex; gap:0; background:rgba(15,15,15,0.85); border:1px solid rgba(184,150,46,0.3); border-radius:99px; padding:4px; backdrop-filter:blur(8px);';
    ['fr', 'en'].forEach(l => {
      const btn = document.createElement('button');
      btn.textContent = l.toUpperCase();
      btn.style.cssText = 'padding:6px 12px; background:transparent; color:#a09890; border:none; font-family:inherit; font-size:11px; cursor:pointer; border-radius:99px; font-weight:600; letter-spacing:0.5px;';
      if (getLang() === l) {
        btn.style.background = '#b8962e';
        btn.style.color = '#000';
      }
      btn.onclick = () => { setLang(l); injectSwitcher(); };
      sw.appendChild(btn);
    });
    document.getElementById('langSwitcher')?.remove();
    document.body.appendChild(sw);
  }

  // Public API
  window.AB_I18N = { t, getLang, setLang, apply };

  // Auto-init
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    injectSwitcher();
  });
  if (document.readyState !== 'loading') {
    apply();
    injectSwitcher();
  }
})();
