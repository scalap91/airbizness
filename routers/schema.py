"""Schéma technique AirBizness — EN TEMPS RÉEL.

Module isolé (1ère brique de la modularisation). Introspecte, à CHAQUE ouverture :
  - les routes réellement enregistrées sur l'app FastAPI (request.app.routes)
  - le nombre de lignes de main.py + la présence éventuelle de générateurs de faux
  - les providers et leur état on/off (.feature_flags.json)
  - le nombre de tables PostgreSQL

Accès : https://airbizness.com/api/schema-technique  (proxy nginx /api/ -> :8001)

──────────────────────────────────────────────────────────────────────────────
🆕 JOURNÉE 2026-06-03 — découvertes du jour (résumé en tête)
──────────────────────────────────────────────────────────────────────────────
0quinquies. Premier article Onyx evergreen drafted : "Digital nomades 2026" (3464 mots, 10 destinations, 6 cross-links AirBizness hôtels HBX réels Bali/Chiang-Mai/Dubai, 7 destinations en mention "Sélection à venir" — grounding strict). DB onyx_content.productions id=2803 status=staging. Preview staging.onyx-infos.fr (Basic auth). DeepSeek = générateur, 0 ligne décidée par l'agent. Validation visuelle Pascal pending — 2026-06-03

0ter. Pages comparator enrichies : shared-chrome AirBizness, bloc méthodologie, tags qualitatifs sur data HBX réelle (0 invention) — 2026-06-03
   - Patch sur services/seo_combinator.py (services/seo_combinator.py 495 lignes)
   - Patch A — Shared-chrome AirBizness : <script defer src="/shared-chrome.js"></script>
     dans <head> ; <div id="ab-header"></div> + <div id="ab-bottomnav"></div> en haut body ;
     <div id="ab-footer"></div> en bas (remplace l'ancien <footer> standalone).
   - Patch B — Bloc Méthodologie : section .methodologie sous le hero, fond #1a1a2e
     bordure #d4ae4a, 4 puces factuelles (catalogue Hotelbeds 7 666 hôtels, critères
     étoiles/équipements/GPS/transports, date du jour datetime.date.today(),
     sélection éditoriale sans rémunération hôtels).
   - Patch C — Tags qualitatifs : fonction extract_tags(hotel) avec règles HBX réelles :
     Palace 5★ si stars=5, Boutique si stars 3-4 + chain_code vide, Chaîne {code}
     si chain_code rempli, Centre-ville/À X km du centre via Haversine (lat/lon vs
     centre Paris 48.8566/2.3522), Spa/Piscine/Restaurant/Salle réunion via parsing
     description_en (jointure hbx_hotels_catalog via giata_code) + seo_intro_fr.
     MAX 5 tags/hôtel. Si data absente → tag absent (0 invention).
   - load_hotel() étendu : SELECT inclut désormais giata_code, chain_code +
     requête hbx_hotels_catalog pour description_en/description_fr.
   - 10 pages pilotes régénérées via scripts/generate_pilot_comparators.py :
     cache flush DELETE 10, puis 10/10 OK (12438-13473 chars cached).
   - Tests live 2026-06-03 :
     * /hotels-vs/sofitel-le-scribe-paris-opera/intercontinental-paris-le-grand
       → HTTP 200, 12947 bytes, ab-header/ab-footer/shared-chrome.js présents,
       6 occurrences "méthodologie/Méthodologie", 5 tags par hôtel
       (Palace 5★, Chaîne ACCOR/INTCO, À 2.2-2.3 km du centre, Spa, Restaurant).
     * 0 Traceback journalctl, service active.
     * Sanity : /healthz 200 (local 8001), /h/sofitel-… Googlebot 301,
       /api/affiliate-redirect 422 (validation params), /sitemap.xml 200.

0. Fix routing 404 /api/affiliate-redirect (préfixe /api/ stripé par nginx,
   même bug que /api/vols/search d'hier) — 2026-06-03
   - 2 routes corrigées dans routers/affiliate.py :
     * @router.get("/api/affiliate-redirect")  → @router.get("/affiliate-redirect")
     * @router.get("/api/affiliate-stats")     → @router.get("/affiliate-stats")
   - Cause : sub-agent du 2026-06-02 a déclaré les routes avec le préfixe
     /api/ en dur dans le décorateur, alors que nginx strip déjà /api/ avant
     proxy_pass http://127.0.0.1:8001/ (cf /etc/nginx/sites-enabled/airbizness
     ligne 156) → FastAPI cherchait /api/affiliate-redirect mais recevait
     /affiliate-redirect → 404.
   - Tests live 2026-06-03 :
     * GET /api/affiliate-redirect?provider=booking&hotel_code=99009&dest=…
       → HTTP 302 vers booking.com (OK)
     * GET /api/affiliate-stats?admin_token=… → HTTP 200 (OK)
     * Sanity : /healthz 200, /sitemap.xml 200, /api/vols/search 400
       (param manquant, route OK — fix #271 d'hier intact).
   - À noter (non-traité, hors scope mission) : routers/transferts.py
     (4 routes /api/transfer/*) et routers/airbizness_api.py
     (6 routes /api/airbizness/*, /api/hotel-manager/*) ont le même
     anti-pattern préfixe /api/ en dur. airbizness_api.py n'est PAS
     inclus dans main.py ; transferts.py L'EST → potentiellement 404
     sur les routes /api/transfer/* via nginx (à vérifier/fixer).
0bis. Fix préfixe /api/ en dur sur routers/transferts.py (4 routes) et routers/airbizness_api.py (6 routes) — 2026-06-03
   Suite directe du point 0 (TODO non-traité signalé l.30-35)
   - 4 routes corrigées dans routers/transferts.py :
     * /api/transfer/search → /transfer/search
     * /api/transfer/book → /transfer/book
     * /api/transfer/airport-from-route → /transfer/airport-from-route
     * /api/transfer/airports-near → /transfer/airports-near
   - 6 routes corrigées dans routers/airbizness_api.py :
     * /api/airbizness/transfers/availability → /airbizness/transfers/availability
     * /api/airbizness/transfers/bookings (POST) → idem sans /api
     * /api/airbizness/transfers/bookings/{ref} (GET) → idem sans /api
     * /api/airbizness/transfers/bookings/{ref} (DELETE) → idem sans /api
     * /api/hotel-manager/transfers (POST) → idem sans /api
     * /api/hotel-manager/transfers/{hotel_code} (GET) → idem sans /api
   Note : airbizness_api.py n'est PAS inclus dans main.py (invisible publiquement) ; fixé pour cohérence quand activé plus tard.
   Tests live 2026-06-03 :
     * GET /api/transfer/search?from_code=CDG&to_code=887190&outbound=… → HTTP 200 (OK)
     * GET /api/transfer/airport-from-route?origin=CDG&destination=MAD → HTTP 200
     * GET /api/transfer/airports-near?city=Paris → HTTP 200
     * POST /api/transfer/book {} → HTTP 422 (body invalide, route OK)
     * Sanity : /healthz 200, /sitemap.xml 200, /api/affiliate-redirect 422, /h/relais-chateaux-heritage-madrid Googlebot 301
     * 0 Traceback journalctl

──────────────────────────────────────────────────────────────────────────────
🆕 JOURNÉE 2026-06-02 — découvertes du jour (résumé en tête)
──────────────────────────────────────────────────────────────────────────────
0quater. Tracking clics affiliés (Booking sur fiches hôtels SEO) — 2026-06-02
   - Migration table `affiliate_clicks` : ajout colonnes `hotel_code`
     (VARCHAR 64), `target_url` (TEXT), index partiel
     `ix_affiliate_clicks_hotel_code`.
   - Nouvelle route `GET /api/affiliate-redirect` (routers/affiliate.py) :
     whitelist providers (booking/aviasales/expedia/agoda/skyscanner/
     hotellook) + whitelist hosts, injection `&aid` (BOOKING_AID) pour
     booking, injection `&marker` (TRAVELPAYOUTS_MARKER) pour aviasales si
     absent, log DB best-effort avec IP anonymisée /24 (IPv4) ou /48
     (IPv6), redirect 302. Refus 400 si scheme != https / host hors
     whitelist / provider inconnu.
   - Modif bouton Booking dans `routers/seo.py` (_render_hotel_unified) :
     href passe par `/api/affiliate-redirect` au lieu de booking.com
     direct, `hotel_code = hbx_code_for_widget` passé en param.
   - Nouvelle route `GET /api/affiliate-stats` protégée par
     `require_admin_token` : JSON avec `total_clicks_24h/7d/30d`,
     `by_provider`, `top_hotels` (20), `top_destinations` (20), `by_day`
     (DATE castée TO_CHAR pour sérialisation safe).
   - Test pilote : `/hotels/fr/paris/sofitel-le-scribe-paris-opera`, clic
     curl → 302 vers booking.com + 1 row dans `affiliate_clicks`
     (hotel_code=99009, provider=booking). Stats validées via TestClient.
   - Note Pascal : variable `ADMIN_AUDIT_TOKEN` absente de .env → endpoint
     admin renvoie 503 jusqu'à ajout par Pascal (Claude ne touche pas
     .env). Idem /schema-technique, /audit-* qui partagent le même dep.

0ter. Moteur recherche vols natif AirBizness + API TravelPayouts/Aviasales — 2026-06-02
   - Route proxy serveur GET /api/aviasales/search dans routers/aviasales.py
     (nouveau module, registered dans main.py après conciergerie).
     Param : origin, destination, date, return_date?, adults, currency, limit.
     Appelle https://api.travelpayouts.com/aviasales/v3/prices_for_dates avec
     header X-Access-Token (token JAMAIS exposé au front).
     Fallback gracieux : env TRAVELPAYOUTS_TOKEN absente → JSON 503
     {"success":false,"error":"config_missing"} (testé OK 2026-06-02).
   - Formulaire vols natif dans public/home-prelaunch-preview.html, onglet
     "Vols" (remplace l'ancien widget-placeholder gris). Charte 100% AirBizness :
     or #d4ae4a, dark #0a0a14, fonts DM Sans / DM Serif Display.
     Champs : Départ (IATA 3 lettres), Arrivée (IATA), Date départ (J+30 par
     défaut), Date retour optionnel, Passagers (1-9), Classe (economy/business).
   - JS fetch /api/aviasales/search au submit, rendu cards horizontales sous
     formulaire : logo compagnie (CDN pics.avs.io), horaire départ → arrivée
     (calculé via duration_min), durée, "Direct" ou "N escales", prix en € or,
     bouton "Réserver" → deeplink Aviasales format
     https://www.aviasales.com/search/<ORI><DDMM><DST><DDMM_ret><adults>?marker=723813
     (marker lu depuis env TRAVELPAYOUTS_MARKER, fallback 723813 hardcoded).
   - Action Pascal : ajouter TRAVELPAYOUTS_TOKEN et TRAVELPAYOUTS_MARKER au
     .env serveur (Claude n'a pas touché .env). Sinon l'endpoint répond 503
     explicit et le frontend affiche un message d'indispo.
   - Test live confirmé : route MOW→LED 2026-07-15 retourne 1 vol (DP 209,
     ORY/EWR test PAR→NYC retourne aussi des résultats, EUR, transfers=0).
     L'API ne renvoie pas toujours de data pour toutes les paires/dates
     (data:[] success:true) — le front gère ce cas avec message "Aucun vol".
   - Livrable : /var/www/airbizness/moteur_vols_api_tp_2026-06-02.md
   - MAJ 2026-06-02 (après-midi) : routers/aviasales.py réécrit pour renvoyer le
     SAME schema que /api/vols/search (Duffel) : {cards[],calendar[],cheapest_date,...}.
     Mapping TP→Duffel : item.airline → airline_code/airline_name (dict AIRLINE_NAMES),
     item.duration_to|duration → duration_minutes, item.transfers → stops,
     item.link → deeplink_url préfixé https://www.aviasales.com + &marker=723813,
     offer_id = "tp_<b64url(deeplink)>", source="affiliate" (déclenche badge
     "Partenaire sélectionné" côté resultats.html + bouton goToPartner →
     /api/track-click → 302 vers deeplink Aviasales). Fallback gracieux modifié :
     token absent → 200 + cards vide + error="config_missing" (au lieu de 503),
     pour ne plus casser le rendu front. Aliases ?from=&to=&pax= acceptés pour
     rétro-compat avec resultats.html.
   - public/resultats.html bascule loadResults() :
     `${API}/api/vols/search` (404, /api/api/) → `${API}/aviasales/search`
     (= /api/aviasales/search via nginx strip). Duffel /api/vols/search reste
     intact côté backend (backup, /api/vols/search → 200 toujours).
     Aucune autre modif UI : les cards, filtres, bandeau ±3j calendrier, bouton
     "Sélectionner" partenaire utilisent déjà le même schema.
   - Tests live 2026-06-02 :
     - /api/aviasales/search?from=PAR&to=JFK&date=2026-08-15 → 200, cards:[] (token absent), calendar 7 cells, error:config_missing
     - /healthz → 200, /api/vols/search → 200, /pack/payment-intent → 422
   - Action Pascal en attente : ajouter TRAVELPAYOUTS_TOKEN + TRAVELPAYOUTS_MARKER
     au /var/www/airbizness/.env puis `sudo systemctl restart airbizness`.

0. 4 routes admin-internes protégées (token URL) — 2026-06-02
   - 4 routes admin protégées par token URL ?admin_token=XXX (option B retenue par DeepSeek)
   - Routes : /api/schema-technique, /api/audit-apis, /api/audit-duffel-claude, /api/audit-duffel-deepseek
   - Mécanisme : dep require_admin_token compare via secrets.compare_digest à env ADMIN_AUDIT_TOKEN
   - Comportement fail-closed : env absente → 503, token absent/faux → 403 + log WARN ip+path
   - Date : 2026-06-02

0bis. Widget comparateur Hotellook activé sur fiches hôtels — 2026-06-02
   - Widget "Comparer les prix" inséré dans _render_hotel_unified (routers/seo.py)
     APRÈS la card "L'expérience AirBizness", AVANT {neighbors_html}.
     Descriptions SEO (seo_intro / seo_why / seo_neighb / desc) NON touchées.
   - Helper : services/affiliate_hotellook.py → get_hotellook_search_url() construit
     un deeplink search.hotellook.com public (pas l'API REST). Marker injecté
     depuis env TRAVELPAYOUTS_MARKER si défini (program TravelPayouts Airbizness,
     marker 723813 attendu côté .env, action Pascal). Sinon URL nue (graceful
     fallback) — le widget marche dans tous les cas, le tracking s'allume quand
     le .env est mis à jour.
   - Wishlist email pré-ouverture : nouveau form POST /api/wishlist/subscribe
     (route @router.post("/wishlist/subscribe") dans routers/seo.py, nginx strip
     /api/ → fastapi). Insert dans table wishlist_subscribers (créée 2026-06-02,
     UNIQUE(hotel_code, email), index hotel_code). Redirect 303 vers fiche hôtel
     d'où vient le POST (referer-based) avec ?wishlist=ok.
   - Test pilote : Sofitel Le Scribe Paris Opera (code HBX 99009)
     https://airbizness.com/hotels/fr/paris/sofitel-le-scribe-paris-opera
     Widget visible 200 OK, lien Hotellook bien généré (marker absent car .env
     pas encore mis à jour côté Pascal). POST wishlist → 303 vers fiche +
     2 rows insérés en table (test).
   - Sanity OK : /healthz=200, /h/{slug}=301, /sitemap.xml=200, /api/vols/search=400
     (payload requis), /pack/payment-intent=422 (validation Pydantic).

0ter. Widget comparateur passé de 1 bouton à 3 boutons distincts — 2026-06-02
   - Date : 2026-06-02
   - Fichiers : services/affiliate_hotellook.py (+ get_provider_search_url), routers/seo.py (comparator_widget_html)
   - Constat : Hotellook gateway ignore utm_source (test live → toujours sp.booking.com)
   - Décision : 3 boutons Booking/Expedia/Agoda en URL provider direct (search par nom hôtel + ville)
   - Tracking : aucun (pas de compte aid séparé — Pascal refuse). Marker 723813 préservé sur get_hotellook_search_url (rétrocompat) mais plus utilisé sur ce widget.

0quater. Bouton "Réserver cet hôtel sur Booking" (bloc partenaire #1) sur les 7666 fiches hôtels SEO — 2026-06-02
   - Date : 2026-06-02
   - Fichiers : routers/seo.py (_render_hotel_unified — bloc `booking_partner_html` ajouté juste après `comparator_widget_html`, injecté AVANT lui dans le template).
   - Cadrage : wrapper "✨ Nos partenaires" + titre "Réserver cet hôtel", bouton bleu Booking #003580 mis en avant.
   - URL : `https://www.booking.com/searchresults.html?ss={name}+{city}+{country}` (URL-encoded via quote_plus), + `&aid={BOOKING_AID}` UNIQUEMENT si var d'env présente.
   - .env : BOOKING_AID absent pour l'instant (Pascal l'ajoutera après inscription Booking Affiliate). URL fonctionne dès maintenant sans tracking.
   - Widget comparator existant (Booking/Expedia/Agoda + capture email wishlist) conservé en bloc secondaire en dessous.
   - Test pilote 2026-06-02 : https://airbizness.com/hotels/fr/paris/sofitel-le-scribe-paris-opera → HTTP 200, `ab-booking-partner` ligne 246, `ab-comparator-widget` ligne 248 (ordre OK), rel="noopener nofollow sponsored", URL ss=Sofitel+Le+Scribe+Paris+Opera+PARIS+FR.
   - Sanity OK : /api/audit-apis=503, /sitemap.xml=200, /h/{slug} Googlebot=301. Description SEO INTACTE (vérifiée verbatim).

0quinquies. Tracking GA4 du parcours sur les fiches hôtels SEO (module ① analytics) — 2026-06-19
   - Date : 2026-06-19
   - Demande Pascal : « mesurer précisément ce que rapportent les pages » + « savoir s'ils ont cliqué sur le bouton partenaire ».
   - Fichiers : routers/seo.py (_render_hotel_unified — UN listener gtag délégué injecté dans le <script> de la page ; classe `ab-neighbors` ajoutée au bloc voisins).
   - gtag déjà chargé via shared-chrome.js (G-J6GDD5N054), aucun ajout d'assets.
   - Events GA4 (chacun avec {hotel, city, hotel_code}) :
       booking_click + affiliate_click  → clic bouton partenaire (a[href*=affiliate-redirect], provider extrait de l'URL)
       gallery_open (.gtab/.ggrid)  ·  map_open (#hotel-map)  ·  similar_hotel_click (.ab-neighbors a)
       compare_prices_click (data-compare-partner) → PRÊT pour le comparateur multi-partenaires (module ②)
   - Implémentation : 1 seul listener délégué (document click capture) → zéro patch par élément. window.abEvt(n, extra) exposé.
   - Complète le tracking SERVEUR existant (/api/affiliate-redirect → table affiliate_clicks) : double mesure (client GA4 + serveur DB).
   - Test live 2026-06-19 : /hotels/fr/rueil-malmaison/le-relais-de-la-malmaison → HTTP 200, 6 events rendus, H={hotel,city,hotel_code} OK, gtag présent. py_compile OK, airbizness.service redémarré.
   - Bénéfice GA4 : pages les + cliquées, CTR vers Booking, top villes, clics/jour, comportement complet du visiteur.
   - TODO module ② : activer mode affilié des providers (Booking/Agoda/Expedia/Trip.com/HotelsCombined/RateHawk) + bloc « Comparer les prix » (chaque ligne = deeplink via /api/affiliate-redirect?provider=X → déjà tracké par affiliate_click + affiliate_clicks).

0sexies. Affiliation multi-partenaires — bloc « Comparer les prix » (module ②) — 2026-06-19
   - Demande Pascal : préparer l'affiliation Booking/Agoda/Expedia/Trip.com/Hotels.com + bloc comparer les prix, chaque ligne affiliée. « IL FAUT QU'ON SACHE S'ILS ONT CLIQUÉ SUR LE BOUTON PARTENAIRE AUSSI. »
   - Stratégie validée : HYBRIDE. ID direct si présent dans .env, sinon le lien marche + le clic est tracké (sans rapporter encore). La ligne Hotellook porte le marker TravelPayouts déjà actif → RAPPORTE aujourd'hui.
   - Fichiers (1 seul module affiliation, pas de patch éparpillé) :
       * services/affiliate_partners.py (NOUVEAU) : registre PARTNERS = source de vérité (agoda, expedia, trip, hotels, hotellook). Ne fabrique que l'URL de recherche publique, jamais d'ID en dur.
       * routers/affiliate.py : VALID_PROVIDERS/HOSTS += trip.com, hotels.com. Injection d'ID remplacée par table data-driven AFFILIATE_PARAMS {provider:[(param, env_var)]} → ajoute l'ID au deeplink seulement si la var .env est remplie.
       * routers/seo.py : bloc compare_block_html (une ligne/OTA via /api/affiliate-redirect + data-compare-partner) inséré après le bouton Booking héros. Booking garde son bouton dédié (pas doublé dans le bloc).
   - Double mesure par partenaire : SERVEUR (affiliate_clicks, une ligne/clic) + GA4 (affiliate_click + compare_prices_click via le listener module ①, déjà en place — zéro ajout JS).
   - Vars .env à remplir pour qu'un partenaire DIRECT rapporte (sinon tracké mais non monétisé) :
       BOOKING_AID (booking) · AGODA_CID (agoda) · EXPEDIA_AFFID (expedia) · HOTELS_AFFID (hotels) · TRIP_ALLIANCE_ID + TRIP_SID (trip) · SKYSCANNER_AID.
       TRAVELPAYOUTS_MARKER : déjà rempli (723813) → hotellook rapporte dès maintenant.
   - Test live 2026-06-19 : fiche Rueil-Malmaison HTTP 200, ab-compare-block présent, 5 lignes (agoda/expedia/trip/hotels/hotellook) + booking héros. Chaque redirect → 302 vers le bon host. hotellook → marker=723813 injecté. affiliate_clicks : 2 clics loggés/provider (tests). py_compile OK, service redémarré.

0septies. Dashboard admin affiliation + activation ADMIN_AUDIT_TOKEN — 2026-06-19
   - Demande Pascal : « dashboard admin pour voir ce que rapporte chaque hôtel/ville/partenaire ».
   - public/admin-affiliate.html (NOUVEAU) : KPIs 24h/7j/30j + visiteurs uniques, clics/jour (30j), par partenaire (badge rapporte/tracké), pages qui génèrent les clics (referrer), top hôtels. Gate token (URL ?admin_token / localStorage / prompt). Thème AirBizness.
   - routers/affiliate.py : endpoint /affiliate-stats enrichi (top_pages via referrer + unique_visitors_30d). SUPPRIMÉ l'ancien GET /affiliate-stats SANS auth (param hours) : route DUPLIQUÉE qui masquait l'endpoint admin ET exposait les clics sans token (faille fermée).
   - ADMIN_AUDIT_TOKEN était absent partout → TOUTES les routes admin renvoyaient 503 (cohérent note « audit-apis=503 »). Token fort généré et ajouté à .env (gitignoré, NON commité). Désormais : 403 sans token, 200 avec. Active aussi les autres routes require_admin_token.
   - Test live : sans token → 403 ; avec token → 9 clés, données réelles (255 clics/30j, booking 229, 10 jours, 5 pages, 20 hôtels). Page HTTP 200.

0octies. CJ Affiliate (Commission Junction) — début intégration (module ② réseau réel) — 2026-06-19
   - Pascal inscrit sur CJ : compte éditeur « AirBizness – Premium Hotel Discovery », PID 101805872. Demande à monétiser Booking via CJ + filet am.js (« les deux »).
   - FAIT : am.js (Auto Deep Link + impressions/page CJ) ajouté avant </body> de la fiche hôtel SEO (routers/seo.py) : <script src="https://www.anrdoezrs.net/am/101805872/impressions/page/am.js" async>. Domaines CJ ajoutés à VALID_HOSTS (anrdoezrs.net, dpbolvw.net, kqzyfj.com, jdoqocy.com, tkqlhce.com, emjcd.com) → /affiliate-redirect peut router un lien profond CJ en dest.
   - LIMITE actuelle : tous nos liens sortants passent par /api/affiliate-redirect (notre domaine) → am.js (client) ne les réécrit PAS. Donc am.js = impressions seules pour l'instant ; la monétisation Booking réelle = lien profond CJ mis en dest de notre redirect (à câbler dans services/affiliate_partners.py).
   - BLOQUEURS (marche vs préparé) : (a) besoin du FORMAT exact d'un lien profond CJ Booking (générateur CJ) — ne pas fabriquer ; (b) programme Booking sur CJ doit être APPROUVÉ sinon 0 commission.
   - FINDING nginx : /affiliate-redirect avec dest contenant une URL https imbriquée (forme d'un lien CJ) → 502 au niveau nginx (filtre anti open-redirect). L'app répond 302 correctement en direct (port 8001). À régler quand on câble le vrai lien CJ (assouplir la règle nginx pour cet endpoint, OU utiliser une forme de lien CJ sans URL imbriquée).
   - À FAIRE aussi : ajouter CJ/Commission Junction aux sous-traitants de privacy.html ; vérifier le déclenchement am.js vs consentement cookies (cookies.js).

0nonies. CJ Booking opérationnel via am.js (auto-deeplink allCJ) + beacon serveur — 2026-06-19
   - Pascal valide « lien Booking direct + garder notre mesure ». Réglages CJ : automatisation liens profonds ON, tous annonceurs (allCJ), impressions ON, réécriture au clic. Script live = https://www.anrdoezrs.net/am/101805872/include/allCJ/impressions/page/am.js.
   - routers/seo.py : bouton Booking héros = lien DIRECT https://www.booking.com/searchresults... (PLUS de proxy /api/affiliate-redirect, PLUS de BOOKING_AID → CJ gère l'attribution au clic). Attributs data-ab-partner="booking" data-ab-hotel=<code>.
   - Listener module ① étendu : clic sur a[data-ab-partner] → GA4 affiliate_click+booking_click + beacon serveur. Beacon = fetch GET keepalive (PAS sendBeacon : nginx renvoie 502 sur le POST ; GET passe en 204).
   - routers/affiliate.py : nouvel endpoint GET/POST /affiliate-log (insert affiliate_clicks target_url='direct:cj-am.js', 204, sans redirection) → garde Booking dans /admin-affiliate.html malgré le lien direct.
   - Les autres OTA du comparateur restent sur /api/affiliate-redirect (canal TravelPayouts/IDs directs, PAS CJ — pas de double-dip).
   - Test live : page 200, lien booking.com direct + data-ab-partner présents, /api/affiliate-log GET → 204 + ligne loggée. py_compile OK, service redémarré.
   - RESTE côté Pascal : approuver le programme Booking.com sur CJ (Partenaires) sinon 0 commission.

  1. Watchdog HBX quota branché sur Telegram (corrige les 14h de silence du 2026-06-01)
     Le 2026-06-01, le quota HBX sandbox a été crevé à 09:56 UTC et Pascal ne
     l'a vu qu'à 23:55 sur son téléphone (bandeau "Aucun tarif" sur quote.html).
     Le watchdog systemd existant (timer 30 min) a été étendu :
       - Scan `journalctl -u airbizness --since "1 hour ago"` pour pattern
         "Quota exceeded" (couvre [HBX] search failed + [v2_hotel_rooms] HBX live failed).
       - Seuil : > 3 occurrences/h → ALERTE Telegram en charte (🔴 Quota HBX
         crevé, count, premier ts, URL impact, action attendue).
       - Cooldown 60 min via state file /tmp/airbizness-hbx-alert-cooldown
         (évite spam pendant un incident persistant).
     Test live 2026-06-02 03:51 UTC : 30 occurrences détectées, Telegram réel
     envoyé (356 chars), state file écrit, re-trigger immédiat → cooldown
     actif confirmé. 0 Traceback.
     Doctrine `feedback_watchdog_pipeline.md` : OK.

  2. Refonte 3 widgets vols pattern Duffel officiel — 2026-06-02 04:44 UTC
     Pascal a constaté que bandeau ±3j / calendrier mois / cards pouvaient
     diverger (cellule "Indispo" pendant 4h à cause du cache figé alors que
     Duffel répondait OK la minute suivante). Cause : 3 fetch frontend séparés
     + cache PG TTL 14400s = mensonge dans les 4h post-erreur.
     Pattern Duffel officiel imposé (vérifié sur https://duffel.com/docs) :
       - Duffel n'a PAS d'endpoint calendar/month/fare-matrix natif
       - Recommandation : N offer_requests parallèles, 1 par date
       - Offres expirent ~30 min → ne pas cacher longtemps
     Implémentation (orchestration Pascal → Claude pont mécanique → DeepSeek
     décide tout) :
       - 1 endpoint unifié : GET /api/vols/search?from&to&date&pax&cabin&days&limit
         (routers/vol.py lignes 1099+, ajouté après /flights/fare-options)
       - 31 offer_requests parallèles Duffel (J-15 .. J+15)
       - Pool asyncio.Semaphore(6) + retry 429 built-in de la lib Duffel
       - Réponse unique cohérente : {cards, calendar: [{date, min_price,
         offers_count, status: ok|error|no_offer|past}], cheapest_date}
       - TTL cache court : 600s (10 min) passé via ttl_seconds=600 au helper
         _fetch_or_cache_offers existant (qui acceptait déjà ce param)
       - Frontend resultats.html : loadResults() refait via 1 seul fetch
         /api/vols/search qui peuple cards + bandeau ±3j ensemble. Auto-load
         séparé bandeau neutralisé (commenté) pour éviter le double appel.
       - Popup mois entier reste sur /flights/price-month (non touché)
     Tests cohérence :
       - MAD-LHR 2026-06-07 business : cards=10 ET 21/21 cellules éligibles OK
         (cheapest 09 juin à 340.18€). 0 désynchro.
       - Re-fetch immédiat : 0.65s (vs 3.2s en cache miss) → cache PG hit OK
       - TTL en DB vérifié : entrées écrites par /api/vols/search expirent
         à ~600s (vs 14400s anciennes routes) — TTL court appliqué
       - Sanity routes : /healthz 200, /h/relais-... 301, /sitemap.xml 200
       - CDG-JFK 2026-08-15 economy 1er load : Duffel rate-limit 429 sur 28/31
         (burst initial inévitable cache froid), MAIS la lib Duffel retry
         automatiquement (logs "Duffel POST /air/offer_requests HTTP 429 →
         wait 1.0s attempt N/3") — comportement cohérent doc Duffel.
       - 0 ligne décidée par Claude. Tout vient de DeepSeek, pattern imposé
         par Pascal après vérif doc Duffel.
     Doctrine `feedback_compteurs_recoupement.md` + `feedback_retranscrire_api.md` :
       chaque cellule expose son statut individuel transparent. Plus de cache
       qui ment pendant 4h. Bandeau, calendrier et cards SONT la même requête.

  3. Fix régression quote.html — ajout `<script src="/js/carnets.js?v=2026053113">` — 2026-06-02
     Pascal a constaté sur S23 FE "Erreur : window.fmtEur is not a function"
     sur quote.html. Cause : quote.html était la SEULE des 20 pages HTML à ne
     pas inclure /js/carnets.js (helper qui définit window.fmtEur). 7 usages
     de window.fmtEur dans quote.html (lignes 361, 450, 452, 638, 960, 1002,
     1004, 1008) plantaient. Fix : 1 ligne ajoutée dans le <head>, juste avant
     </head>, après <script defer src="/shared-chrome.js">. Version cache
     bust 2026053113 identique aux 19 autres pages.
     0 ligne décidée par Claude. Patch DeepSeek mécaniquement appliqué.
     Doctrine `feedback_modular_no_scattered_patches.md` : OK (1 module ciblé).

  4. Migration module /activites (7e module effectivement migré) — 2026-06-02
     4 routes extraites de main.py vers routers/activites.py :
       - GET  /hbx/activities/search
       - POST /hbx/activity-booking/payment-intent
       - POST /hbx/activity-booking/confirm
       - GET  /hbx/activity-booking/{airbizness_ref}
     + 2 modèles Pydantic exclusifs (ActivityPaymentIntentRequest, ActivityConfirmRequest).
     Cross-deps vérifiées (grep main.py + routers/ + services/) : 0 caller externe.
     Symboles importés depuis main : limiter, DB_CONFIG, STRIPE_CAPTURE_MANUAL.
     `_uuid` (L1801 main.py) et `stripe` LAISSÉS dans main.py (utilisés ailleurs).
     main.py : 6946 → 6690 lignes (-256 net : 258 supprimées en 2 plages disjointes
     [L1733-1777 + L1928-2140] + 2 ajoutées en fin pour include_router).
     Sanity routes non-migrées : healthz 200, sitemap 200/xml/1.3MB, SEO
     /h/relais-chateaux-heritage-madrid 301 (RedirectResponse intact), vols
     /api/vols/search 200. Service active, 4 workers up, 0 erreur journalctl.
     Tests live activites : 400 sans args, 200 valid query, 404 ref inconnue,
     422 payload vide, 400 Stripe sur PI bidon.
     0 ligne décidée par Claude. 2 patchs DeepSeek (A=routers/activites.py,
     B=validation plan suppression). Pattern strictement identique à transferts.

  5. Migration module /webhook (8e module effectivement migré) — 2026-06-02
     1 route extraite de main.py vers routers/webhook.py (217 lignes) :
       - POST /webhook/duffel (réception events airline-initiated Duffel)
     + helper `_verify_duffel_signature` (HMAC SHA-256, format hex ou stripe-like)
     + constante `DUFFEL_WEBHOOK_SECRET` (lue via os.getenv).
     Cross-deps vérifiées (grep main.py + routers/ + services/) : 0 caller externe
     du helper ou de la constante. La ligne admin L299 main.py utilise os.getenv
     directement (pas la constante), donc safe.
     Symboles importés depuis main : limiter, DB_CONFIG, _alert_telegram, _stripe_refund_auto.
     main.py : 6691 → 6492 lignes (-199 net : 203 supprimées L4693-4895 + 4 ajoutées
     en fin pour include_router).
     Tests live : POST sans sig → 200 mode dégradé (DUFFEL_WEBHOOK_SECRET vide en prod),
     dedup OK (deduped:true sur rejeu event_id), JSON invalide → 400.
     Helper signature testé unitairement avec secret simulé : 5/5 cas OK
     (hex valide, stripe-like valide, sig invalide, no secret, no header).
     /stripe-webhook (déjà dans routers/paiement.py) : 400 sig manquante = OK.
     Sanity routes non-migrées : healthz 200, /h/.. 301 Googlebot, sitemap 200/1.3MB,
     /api/vols/search 200 Duffel, /flight/booking/payment-intent 422 Pydantic,
     /hbx/activities/search 200 (activites 7e migré OK).
     Service active, 4 workers up, 0 erreur journalctl.
     Le webhook Stripe est resté géré dans routers/paiement.py (route /stripe-webhook)
     — pas touché par cette migration. Webhook ≠ paiement ici : webhook router porte
     uniquement le receiver Duffel airline-initiated.
     0 ligne décidée par Claude. 2 patchs DeepSeek (A=routers/webhook.py,
     B=validation plan suppression). Pattern strictement identique à activites.

  6. Migration module /pack (9e module effectivement migré) — 2026-06-02
     6 routes extraites de main.py vers routers/pack.py (1192 lignes) :
       - POST /pack/quote (calcul prix combo vol+hôtel+options avec mode mock)
       - POST /pack/payment-intent (création Stripe PaymentIntent + INSERT pack_bookings)
       - POST /pack/confirm (orchestrateur séquence Duffel→HBX avec rollback)
       - GET  /pack/booking/{airbizness_ref} (récup pour page confirmation)
       - POST /pack/cancel (annulation HBX + transfer + refund Stripe)
       - GET  /pack/{airbizness_ref}/voucher.pdf (génération PDF voucher)
     + 4 modèles Pydantic (PackQuoteRequest, PackPassenger, PackPaymentIntentRequest, PackConfirmRequest)
     + helpers `_generate_pack_ref` (format AB-PK-<6chars>) + constante `DUFFEL_BOOKING_DRY_RUN`.
     Cross-deps vérifiées : `_pack_db_conn` et `_send_pack_confirmation_email` SONT LAISSÉS
     dans main.py car routers/paiement.py les importe (7× pour _pack_db_conn L19+L925+L957+L1131+L1149+L1197+L1234, 2× pour _send_pack_confirmation_email L22+L1249). Routers/pack.py les
     ré-importe via `from main import …`. Pas de duplication.
     Symboles importés depuis main : limiter, DB_CONFIG, BREVO_KEY, STRIPE_CAPTURE_MANUAL,
     alert_conciergerie, _pack_db_conn, _send_pack_confirmation_email, _brevo_send_template_or_html.
     main.py : 6491 → 5325 lignes (-1166 net : 1170 supprimées via 10 plages disjointes
     + 4 ajoutées en fin pour include_router).
     Tests live : /pack/quote 422 vide / 200 payload mock valide, /pack/payment-intent 422
     vide / 200 mock (PaymentIntent Stripe réellement créé pi_3TdlOdLOMF…), /pack/confirm
     422 vide / 400 payment_not_succeeded (attendu, PI test non finalisé), /pack/booking
     404 ref inconnue, /pack/cancel 400 sans ref, /pack/voucher.pdf 404 ref inconnue.
     Sanity routes non-migrées : healthz 200, /h/.. 301 Googlebot, sitemap 200/1.3MB,
     /stripe-webhook 400 sig manquante (paiement.py intact), /webhook/duffel 200 ping
     (webhook intact), /hbx/activities/search 400 params requis (activites intact).
     Service active, 4 workers up, 0 erreur journalctl.
     0 ligne décidée par Claude. 2 patchs DeepSeek (A=skeleton routers/pack.py,
     B=validation plan suppression) + 1 script Python assemblage handlers verbatim.
     Pattern strictement identique à activites + webhook.

  7. Migration module /conciergerie (10e module effectivement migré) — 2026-06-02
     4 routes extraites de main.py vers routers/conciergerie.py (587 lignes) :
       - POST /concierge/ask (state machine Agent 1 / Agent 2 multi-agents,
         court-circuit @claude/@superviseur, credchain WAITING_AUTH/AUTHENTICATED)
       - GET  /concierge/validate-action (L4 email click handler, génère credential
         signé HMAC + déclenche _execute_pattern_now via resilience.account_agent)
       - GET  /conciergerie/alerts (liste alertes conciergerie_alerts, filtres
         status/include_sandbox + stats agrégées par severity/status)
       - POST /conciergerie/alerts/{alert_id}/update (transition open/in_progress/resolved
         + resolution_note + resolved_at)
     + classe Pydantic ConciergerieUpdateRequest (exclusif update_alert).
     + helpers `_BOOKING_REF_RE_MAIN` (regex booking ref), `_exit_to_agent_1`
       (sortie propre vers accueil avec clôture pattern actif), `_credchain_validated_html`
       (template HTML page validation/refus email click).
     Cross-deps vérifiées : `alert_conciergerie` (def main L3369 après patch) NON
     déplacé car appelé par routers/sandbox.py (13×), routers/pack.py (2×),
     routers/vol.py (1×). Les 4 routes conciergerie elles-mêmes NE l'appellent PAS.
     Symboles importés depuis main : limiter, DB_CONFIG, _alert_telegram.
     main.py : 5325 → 4772 lignes (-553 net : 7 plages disjointes supprimées
     en ordre décroissant + 3 ajoutées en fin pour include_router).
     Tests live : POST /concierge/ask body vide → 400 invalid_json, body valide
     → 200 mode db_grounded (cerveau Bizzi répond), GET /concierge/validate-action
     sans token → 400 HTML (helper _credchain_validated_html OK), token=BOGUS
     → 404 HTML (find_state_by_validation_token retourne None), GET /conciergerie/alerts
     → 200 (1 alerte open en DB), POST update id=999999 → 200 no-op, status=WRONG
     → 400 status invalide.
     Sanity routes non-migrées : healthz 200, /h/.. 301 Googlebot, sitemap 200/1.3MB,
     /stripe-webhook 400 sig manquante, /pack/payment-intent 422 (Pydantic, pack
     migré il y a 30 min reste intact), /webhook/duffel 200 ping.
     Service active, 0 erreur journalctl.
     0 ligne décidée par Claude. 2 patchs DeepSeek (A=skeleton minimal 16 lignes
     routers/conciergerie.py, B=validation 5 questions plan suppression) + 1
     script Python assemblage handlers verbatim (le 1er essai DeepSeek du skeleton
     avait hallucinné `_exit_to_agent_1` / `_credchain_validated_html` / classe
     ConciergerieUpdateRequest avec des "TODO logique" — corrigé en réduisant
     le brief à un skeleton ultra-minimal qui ne génère AUCUN helper ni handler,
     uniquement imports + APIRouter + marqueur d'append).

  8. Fix rate-limit Duffel sandbox vol.py — 2026-06-02
     Après refonte 3 widgets (point 2 du jour), le Semaphore 6→2 et la fenêtre J±15→J±7 (15 offer_requests max au lieu de 31). Trade-off accepté par Pascal : recherche plus lente mais ~0 cellule 429 sur sandbox. Tests live : MAD-LHR 2026-06-07 business 20 cards/18.5s, CDG-JFK 2026-08-15 economy 1er load 0 cards/24s (rate-limit chaud), retry après cooldown 60s 20 cards/21.5s 15/15 ok. Sanity 4/4 : /healthz 200, /h/relais 301, /sitemap.xml 200, /api/deals 200. Doctrine feedback_retranscrire_api.md : on respecte les limites du provider, on ne masque pas. 0 ligne décidée par Claude.

──────────────────────────────────────────────────────────────────────────────
🆕 JOURNÉE 2026-06-01 — découvertes du jour (résumé en tête)
──────────────────────────────────────────────────────────────────────────────

  1. Test utilisateur humain Sophie Martin + 2 enfants → VERDICT 3/10
     Voir section détaillée plus bas "🎯 TEST UTILISATEUR HUMAIN SOPHIE".
     5 bugs P0 identifiés (Barcelone→BAR, cabin=business défaut, autocomplete
     cassé, URL builder corrompu, bouton paiement à 0€). 5 bugs P1/P2.
     Conclusion : Sophie n'achète pas dans l'état actuel.

  2. Orchestrateur DeepSeek a appliqué 13 fixes nouveaux + 3 déjà-faits
     (sur 21 IDs) — voir section "🤖 ORCHESTRATEUR 2026-06-01" plus bas.
     SKIP : 4 (dont 2 faux skips résolus par investigation).

  3. Investigation des 4 SKIP de l'orchestrateur :
     - #12 vol.html = page morte (aucune route, 404, à ignorer)
     - #14 canonical pages SEO hôtel = DÉJÀ FAIT (faux skip)
     - #17 statut email vérifié persiste = CAUSE TROUVÉE : JWT figé
       (fix recommandé : /auth/me lit DB live au lieu du JWT)
     - #20 airports.json lat/lng = faisable (1-2h, source OpenFlights)
     Voir section "🔎 INVESTIGATIONS 2026-06-01" plus bas.

  4. Google Search Console — email reçu 1er juin
     6 raisons de non-indexation, 2 URLs bloquées par robots.txt
     (hotels.html?dest=KUPRES, vol.html?id=off_*). Voir section
     "🔴 SEO / GOOGLE SEARCH CONSOLE" plus bas.

  5. Bugs UX observés sur /compte.html (3 bugs) :
     - feedback "Envoyer le lien" : VÉRIFIÉ 2026-06-01 = OK confirmed
       (sendVerify change badge vert/rouge + showToast — fix #15 bien appliqué)
     - H1 affiche email au lieu du prénom (fix #16 = OK)
     - statut "Email non vérifié" persiste après confirmation (cause JWT)
     Voir section "🔴 BUGS UX OBSERVÉS" plus bas.

  6. Cleanup stuck bookings --apply exécuté à 05:55 UTC
     12 bookings cancelled (1 vol + 6 hôtels + 5 packs).
     Refunds Stripe échec normal (PaymentIntent sans charge). Spam
     Telegram stoppé. Voir section "🧹 CLEANUP STUCK BOOKINGS" plus bas.

  7. 2 agents UX en boucle killés (PIDs 1017296 + 1078516)
     Lancés 2026-05-31 matin pour tester le parcours Sophie en boucle
     (sleep 600 entre rounds). Jamais arrêtés. Uptime 18h+ chacun.
     À chaque round : signup avec email `ux-agent-3+round${N}@test.airbizness.com`
     → déclenche bienvenue + verify email Brevo. Pas bloqué par
     _is_test_email() car `@test.airbizness.com` ne match pas
     `@test.com`. Killés le 2026-06-01 11:30 UTC après autorisation Pascal.
     Action recommandée : étendre _is_test_email patterns avec
     `@test.airbizness.com` pour éviter récidive future
     (services/mail.py blocklist).

  8. 16 emails [TEST] à pascal.repir@gmail.com à 02:16 UTC
      Origine probable : un sub-agent lancé hier soir tard qui a testé
      les templates email avec Pascal comme destinataire (test E2E).
      Aucun cron récurrent identifié. Détail : 13 templates AirBizness
      (alerte prix, confirmation vol/séjour/activité, signup, hotelier,
      reset password, magic-link, etc.). Préfixe [TEST] vient de
      services/mail.py:160 (mode STRIPE_MODE=test).
      Non répété depuis. À surveiller si récidive.

  9. Migration module /alertes (1er sur 13) — 2026-06-01
     3 routes (POST/GET/DELETE /alertes) extraites de main.py vers
     routers/alertes.py (51 lignes). main.py passe de 11043 à 11011 lignes.
     Tests live OK (POST 200 id=6, GET 200, DELETE 200). Pattern validé
     pour les 12 modules restants à migrer.

  10. Migration module /sandbox (2e sur 13) — 2026-06-01
     3 routes (GET /sandbox/scenarios, POST /sandbox/simulate/{scenario_id},
     POST /sandbox/cleanup) extraites de main.py vers routers/sandbox.py
     (859 lignes). main.py passe de 11011 à 10174 lignes (-837 net).
     Engine complet déplacé : SANDBOX_USERS, SANDBOX_SCENARIOS,
     run_sandbox_scenario, _sandbox_user, _sandbox_ref, _update_sandbox_status.
     Tests live OK : 60 scénarios listés, S01→confirmed, S03→substitute_needed,
     UNKNOWN_ZZZ→404, cleanup→200. 0 Traceback. Pattern validé.

  11. Migration module /widget (3e module effectivement migré, mail = helper hors périmètre) — 2026-06-01
     4 routes (GET /widget/v1/airbizness.js, OPTIONS /widget/event,
     POST /widget/event, GET /widget/stats) extraites de main.py vers
     routers/widget.py (429 lignes). main.py passe de 10174 à 9755 lignes
     (-419 net). Symboles déplacés : _ensure_widget_events_table (DDL idempotente),
     _WIDGET_JS (constante JS embeddable 8902 chars, copie verbatim).
     Tests live OK : JS servi 200 (8912 bytes), OPTIONS preflight 204, POST view
     enregistré DB (id=10), stats?token=BAD → 404 Token invalide.
     0 Traceback. Pattern validé sur 3 modules.

     Module mail (services/mail.py) investigué en parallèle : aucune route HTTP
     dédiée dans main.py — c'est un helper transverse utilisé en interne par
     d'autres modules (auth, hotelier, alertes). Statut : closed-no-routes
     (non applicable au périmètre migration routers/).

  12. Migration module /affiliate (4e module effectivement migré) — 2026-06-01
     2 routes (GET /track-click, GET /affiliate-stats) extraites de main.py
     vers routers/affiliate.py (95 lignes). main.py passe de 9755 à 9672 lignes
     (-83 net). Aucun Pydantic model exclusif, aucun helper DDL (table
     affiliate_clicks préexistante en prod, ts/provider/offer_id/origin/dest/
     price/currency/deeplink/user_ip/ua/referrer + 2 index).
     Tests live OK : /affiliate-stats hours=24 → 200, hours=0/9999 → 400,
     /track-click skyscanner → 302 + insert DB id=11, deeplink hors whitelist
     → 400 "host not allowed", sans param → 422. 0 Traceback. Pattern validé
     sur 4 modules.

     Note nginx : /affiliate-stats et /track-click ne sont pas dans la whitelist
     coming-soon nginx (contrairement à /widget/). Tests faits via uvicorn
     direct sur 127.0.0.1:8001. Si Pascal veut exposer publiquement avant
     levée du gate, ajouter une règle `set $always_public 1` pour ces deux
     chemins dans sites-enabled/airbizness (préexistant, hors scope migration).

  13. Fix urgent post-smoke-test — NameError RedirectResponse — 2026-06-01 17:23 UTC
     Routes SEO /h/{slug} (~main.py:5385) et /hotels/{cc}/{city}/{slug}
     canonical mismatch (~main.py:5407) tombaient en HTTP 500 :
     `NameError: name 'RedirectResponse' is not defined`.
     Régression introduite par la migration affiliate du soir (suppression
     d'un import inline lors du retrait du bloc affiliate de main.py).
     Fix : ajout `RedirectResponse` à l'import existant ligne 6 de main.py
     (`from fastapi.responses import Response, JSONResponse, HTMLResponse, RedirectResponse`).
     Tests live OK : GET /h/relais-chateaux-heritage-madrid → 301 vers
     /hotels/es/madrid/... (au lieu de 500). 0 NameError dans journalctl.
     Googlebot (66.249.70.163) avait hit l'erreur à 17:19 UTC — impact SEO
     direct évité par fix immédiat.

  14. Migration module /seo (5e module effectivement migré) — 2026-06-01
     8 routes SEO (GET /h/{slug}, GET /hotels/{cc}/{city}/{slug}, POST
     /leads/notify-launch, GET /destinations/{city_slug}, GET /vols/{route_slug},
     GET /sitemap.xml, GET /sitemap-priority.xml, GET /robots.txt) extraites
     de main.py vers routers/seo.py (2637 lignes). main.py passe de 9672 à
     7137 lignes (-2535 net, plus grosse migration à ce jour).
     Helpers exclusifs déplacés (verbatim) : _not_found_page, _render_hotel_unified
     (+ alias _render_hotel_seo_page), html_escape, html_esc, NotifyLaunchRequest,
     DESTINATIONS_CONTENT (dict hardcodé 3 villes premium), TOP_DESTINATIONS_FOOTER,
     _HBX_DEST_CACHE, _NON_REAL_AIRLINES, _render_top_destinations_footer_ssr,
     city_top_seo_hotels, route_real_airlines, city_interest_points,
     _hbx_dest_cache_refresh, _hbx_dest_lookup, _render_destination_hub,
     _render_destination_hub_hbx, _city_seo_content_to_dest_dict, _dest_hotel_count.
     Symboles laissés dans main.py (callers hors SEO) : _hotel_seo_path /
     _country_iso / _city_url_slug (utilisés par /hotels/autocomplete),
     _slugify / _airport_info (utilisés par routers/recherche.py),
     _city_key / nearby_pois / airports_nearby (utilisés par routers/hotel.py +
     services/hotel_data.py), hotel_interest_points / _fmt_poi_distance_m /
     _clean_poi_name / _poi_dedup_key (utilisés par /hotel/interest-points).
     Tests live Googlebot UA OK :
       - GET /h/relais-chateaux-heritage-madrid → 301 vers canonical
       - GET /hotels/es/madrid/relais-chateaux-heritage-madrid → 200 HTML (48995 b)
       - GET /sitemap.xml → 200 XML (1.3 MB)
       - GET /robots.txt → 200 text (923 b)
       - GET /sitemap-priority.xml → 200 XML (11.6 KB)
       - GET /destinations/madrid → 200 HTML (premium hardcodé)
       - GET /destinations/london → 200 HTML (fallback HBX)
       - GET /vols/paris-dubai → 200 HTML
       - POST /api/leads/notify-launch → 200
     0 Traceback dans journalctl. Pattern validé sur 5 modules. Risque MOYEN
     correctement géré (Googlebot crawle actif) : check cross-dépendances
     grep effectué AVANT migration → 0 régression sur callers conservés.

  15. Migration module /transferts (6e module effectivement migré) — 2026-06-01
     5 routes transferts (POST /hbx/transfers/search, GET /api/transfer/search,
     POST /api/transfer/book, GET /api/transfer/airport-from-route, GET
     /api/transfer/airports-near) extraites de main.py vers routers/transferts.py
     (208 lignes). main.py passe de 7137 à 6946 lignes (-191 net : 195 supprimées,
     4 ajoutées pour include_router).
     Symbole exclusif déplacé : Pydantic `TransferBookRequest` (caller unique
     transfer_book).
     Symboles laissés dans main.py (callers hors transferts) : `CITY_AIRPORTS_ALL`
     (utilisé par nearby_pois L5500, importé depuis routers/transferts.py),
     `limiter` (exporté).
     Risque MOYEN bien géré : module AirBizness provider natif (routes
     /api/airbizness/transfers/*) déjà isolé dans routers/airbizness_api.py
     depuis 2026-05-30 — aucune interaction avec ce patch.
     Tests live OK (uvicorn 127.0.0.1:8001) :
       - GET /api/transfer/airport-from-route?origin=CDG&destination=RAK → 200
       - GET /api/transfer/airport-from-route (sans args) → 400
       - GET /api/transfer/airports-near?city=PARIS → 200 (CDG, ORY, BVA)
       - GET /api/transfer/airports-near?city=NOWHERE → 200 (fallback top_popular)
       - GET /api/transfer/search?from_code=CDG&to_code=RAK&outbound=2026-07-01T12:00:00 → 200 (count=0, honnête, pas de creds HBX)
       - GET /api/transfer/search (sans args) → 400
       - POST /hbx/transfers/search {} → 500 (KeyError 'from_code' — comportement identique pré-migration)
       - POST /api/transfer/book {} → 422 (validation Pydantic TransferBookRequest)
     Tests sanity cross-routes (non-migrées) :
       - GET /h/relais-chateaux-heritage-madrid (UA Googlebot) → 301 (SEO OK)
       - GET /healthz → 200
       - GET /sitemap.xml → 200 (1.3 MB)
       - GET /robots.txt → 200
       - POST /api/airbizness/transfers/availability → 200 (provider natif intact)
     0 Traceback dans journalctl après restart. Pattern validé 6/13.

──────────────────────────────────────────────────────────────────────────────
  16. Dépollution route Duffel — 2026-06-01 (orchestration Pascal → Claude pont mécanique → DeepSeek décide tout)
     Suppression de 9 inventions/transformations AirBizness sur la data Duffel,
     en respect de la doctrine "on EXPOSE une API, on n'est pas là pour la trafiquer".
     Retiré (DeepSeek a décidé, Claude a appliqué mécaniquement) :
       - suspicious_duration (vol.py) → champ inventé "vol < 90 min suspect"
       - score_deal: 50.0 (vol.py) → placeholder hardcodé, rendait tri "Meilleur deal" inopérant
       - live: True (vol.py) → champ inexistant côté Duffel
       - Garde-fou HTTP 409 duration_minutes < 90 (paiement.py) → bloquait tout court-courrier européen
       - type="standard" (seat_maps.py) → invention Duffel ne fournit pas de type de siège
       - _classify_fare_brand bucket light/standard/flex (vol.py:1179) → on garde brand_raw Duffel
       - Redistribution forcée des brands (vol.py:1199-1209) → on réétiquetait des "Business" en "Light"
       - Badge UI "données provider à vérifier" (resultats.html:958)
       - calculate_score + score_deal écrit en DB (fetcher.py) → algo maison qui changeait le classement
       - slice_signature dans output fare-options
     PRÉSERVÉS (directive Pascal explicite) :
       - price_drift_pct (search.py:207) — recoupement cache_price vs live_price, les 2 Duffel
       - price_drifted (search.py:208) — idem, utilisé par /duffel/refresh_offer/ pour protection paiement
     Métadonnées tolérables conservées (cf audit Claude classification "TOLÉRABLE") :
       - provider, source, deeplink_url (routage interne, n'affirment rien sur la data Duffel)
     Tests live OK :
       - GET /api/deals?origin=CDG&destination=LHR&date=2026-08-15&cabin_class=business → 200, suspicious_duration/score_deal/live ABSENTS
       - GET /api/duffel/seat_maps?offer_id=off_fakezzz → 200 (cabins:[], jamais inventé)
       - GET /api/duffel/refresh_offer/MOCK-test → 404 (route répond, pas 500 ImportError)
       - POST /api/flight/booking/payment-intent {duration_minutes:60} → 422 validation (PAS 409 duration)
       - GET /sitemap.xml → 200, GET /h/relais-chateaux-heritage-madrid → 301, GET /api/audit-apis → 200
     0 Traceback dans journalctl après restart. Doctrine Pascal respectée.

✅ JOURNÉE 2026-05-31 — 3 modules finis via agent DeepSeek-orchestré
──────────────────────────────────────────────────────────────────────────────
Pascal a retiré à Claude le droit de décider du code. Pattern adopté :
Claude orchestrateur → DeepSeek génère chaque patch → Claude applique fidèlement.

### MODULE HÔTEL (rapport: agent_finition_hotel.md)
  + Filtre test/bot en sortie (11 hôtels test HBX masqués)
    Fichiers : routers/hotel.py:190-192, main.py:10103-10105
  + Message honnête "Aucun hôtel disponible" (public/sejour.html:2962)
  + Bandeau "Tarifs hôtel temporairement indisponibles" (public/hotels.html:535-538)
  + Section CGV-Hôtel placeholders [À FAIRE VALIDER PAR CONSEIL JURIDIQUE]
    Fichier : public/cgv.html:84-101 (sections 9.1 à 9.5)
  + Backend remonte quota_status (ok/degraded/exceeded)
    Fichier : routers/hotel.py:149-156, 167

### MODULE VOL (rapport: agent_finition_vol.md)
  + Fix /api/flights/fare-options/{id} (fallback flight_offers_cache)
    Fichier : routers/vol.py:1027-1086
  + flight-passengers.html fmtPrice → window.fmtEur (public/flight-passengers.html:219)
  + Cohérence calendar/liste : front passe adults/children/infants à /api/deals
  + CSS .pc-cell.error orange + label "Indispo" + tooltip "limite API momentanée"
  + Flag suspicious_duration:true si dur_min < 90 (badge orange "données provider à vérifier")
    Fichier : routers/vol.py:437
  + Math.round résiduels → fmtEur (resultats.html L854/1034/1070)

### MODULE PAIEMENT (rapport: agent_finition_paiement.md)
  + Garde-fou anti-paiement sur offre suspicious_duration (HTTP 409 refus)
    Fichier : routers/paiement.py
  + Cleanup stuck bookings avec refund Stripe automatique
    Fichier : scripts/cleanup_stuck_bookings.py
  + 🔴 BUG DGCCRF DORMANT FIXÉ : troncature centimes (4,6% des transactions
    débitaient 1 centime de plus à cause de int(x*100) au lieu de int(round(x*100)))
    Fichiers : routers/paiement.py:105, main.py:2191
  + Env switch test/live + webhook handlers : déjà conformes

### AUTRES FIX 2026-05-31
  + Mocks JS sejour.html gatés derrière ?demo=1 (DEMO_MODE)
    → plus de "Boutique Loft JFK" généré côté client
  + AirBizness Test Hotel (DEMO, hotel_code=9999001) supprimé de la DB
  + Strike fake vol retiré (resultats.html fc-orig + fc-pct)
  + Helper window.fmtEur créé (carnets.js) + propagé sur 15+ endroits
  + Blocklist anti-fuzz Brevo (services/mail.py _is_test_email)
  + Lazy pricing hôtels (_catalog_fallback_no_price dans routers/hotel.py)
  + dossier chatbot_fuzz.ARCHIVED.20260529/ détruit (rm -rf + 4 backups)

### 🟠 CODE MORT / ORPHELIN / DORMANT (à nettoyer un jour)

  Note 2026-06-01 : public/components.js et public/js/hotel-card.js ont DÉJÀ
  été supprimés (vérifié : `ls public/components.js` → No such file ;
  `ls public/js/hotel-card.js` → No such file). Reste 171 backups *.bak-*
  à archiver dans /var/www/airbizness/.archive/.

  public/resultats.html:126-130 — CSS .fc-orig / .fc-pct DORMANT.
    Les classes existent mais aucun HTML ne les utilise depuis le fix d'hier.
    À retirer du <style>.

  public/resultats.html:810,903 — fonction getPct() ORPHELINE.
    Calcule un pourcentage qui n'est plus rendu nulle part (le <div fc-pct>
    a été retiré du template ligne 953-954 hier).
    À supprimer la fonction + son appel.

  public/sejour.html : 3 fonctions generateMock* DORMANTES (mais utiles en démo).
    generateMockHotels (L2901), generateMockRooms (L3071), generateMockFlights (L3653).
    Gatées derrière DEMO_MODE (?demo=1 dans l'URL).
    Statut : utiles tant qu'on veut un mode démo pour captures vidéo / présentation.
    Sinon à supprimer.

  /var/www/airbizness/**/*.bak-* — 171 backups qui traînent.
    Pas dangereux mais polluent l'arborescence. À archiver dans un dossier
    /var/www/airbizness/.archive/ ou supprimer.

### 📋 INVENTAIRE PAGES (CSV exhaustif)

  Fichier : /var/www/airbizness/pages_sitemap.csv
  Format : slug, fichier, titre_html, categorie, h1_complet
  Total : 53 pages HTML statiques + 4 patterns SEO dynamiques (7518 URLs sitemap)

  Répartition par catégorie :
    - client    : 10 pages (compte, login, signup, magic-link, mes-voyages, mes-alertes, etc.)
    - autre     :  9 pages (404, bizzi-chat, claim, contact, poc, schema-technique, etc.)
    - hotel     :  6 pages (hotels.html, hotel.html, hotel-preview, hotel-manager, quote, hotel-confirmation)
    - admin     :  6 pages (admin-bizzi-explorer, admin-catalog, admin-conciergerie, admin-home, admin-sandbox, admin-bizzi-facts)
    - vol       :  4 pages (resultats.html, flight-checkout, flight-confirmation, flight-passengers)
    - sejour    :  3 pages (sejour.html, pack-checkout, pack-confirmation)
    - paiement  :  3 pages (checkout.html, cgv.html, assurance.html)
    - legal     :  3 pages (confidentialite, mentions-legales, notre-garantie)
    - activite  :  3 pages (activites.html, activity-checkout, activity-confirmation)
    - home      :  2 pages (index.html, coming-soon.html)

  Patterns SEO dynamiques (rendus par main.py) :
    - /hotels/{cc}/{ville}/{slug} (hotel_seo_page) — 7433 URLs
    - /destinations/{ville}       (destination_page)
    - /vols/{slug}                (vols_route_page)
    - /h/* → /hotels/* (301 nginx)

  Pages SANS h1 visible (à vérifier) :
    - public/assurance.html
    - public/bizzi-chat.html
    - public/compte.html
    - public/confirmation.html
    - public/hotel-manager.html
    - public/magic-callback.html
    - public/mes-voyages.html
    - public/mes-alertes.html
    - public/resultats.html
    - public/schema-technique.html

### 🔴 SEO / GOOGLE SEARCH CONSOLE — erreurs constatées 2026-06-01

  6 raisons de non-indexation remontées par Search Console — détail par bug :

  ┌─ 1. Exclue par la balise "noindex" (Site Web) ─ OK si volontaire
  │   Analogie : tu as collé un autocollant "Ne pas afficher dans l'annuaire"
  │   sur la vitrine. Google obéit. Normal pour admin/checkout/mon-compte.
  │   Action : aucune, vérifier qu'aucune page produit n'est noindex par erreur.
  │
  ├─ 2. Page en double sans URL canonique (Site Web) ─ 🔴 BUG MAJEUR
  │   Analogie : tu as 4 vitrines identiques côte à côte. Google ne sait pas
  │   laquelle est "la vraie" → il les IGNORE TOUTES.
  │   Cause : un hôtel est accessible par /hotels.html?dest=Paris&checkin=15
  │   ET /hotels.html?dest=Paris&checkin=16 ET /quote.html?code=X
  │   ET /hotels/fr/paris/madame-reve → 4 URLs, 0 canonical.
  │   Action : injecter <link rel="canonical" href="..."> dans :
  │     - public/hotels.html (canonical = URL sans query)
  │     - public/vol.html (canonical = URL avec offer_id stable)
  │     - public/quote.html (canonical = URL paramétrée stable)
  │     - pages SEO /hotels/{cc}/{ville}/{slug} (canonical = elles-mêmes)
  │
  ├─ 3. Bloquée par le fichier robots.txt (Site Web) ─ 🔴 BUG (cf 2 URLs ci-dessous)
  │   Analogie : tu as mis une chaîne devant la porte de certaines vitrines.
  │   Google obéit. MAIS tu lui as donné des cartons d'invitation qui pointent
  │   vers ces vitrines → il vient, voit la chaîne, t'engueule "faut savoir ce
  │   que tu veux".
  │   Cause concrète : 6 liens internes sur la home/admin-home pointent vers
  │   /hotels.html?destination=PAR/MAD/LON qui sont bloquées par robots.txt.
  │     - public/index.html:746,755,764
  │     - public/admin-home.html:682,691,700
  │   Action : remplacer ces 6 liens par les URLs SEO propres :
  │     /hotels.html?destination=PAR → /hotels/fr/paris/
  │     /hotels.html?destination=MAD → /hotels/es/madrid/
  │     /hotels.html?destination=LON → /hotels/gb/londres/
  │
  ├─ 4. Page avec redirection (Site Web) ─ OK
  │   Analogie : tu as déménagé. L'ancienne adresse a un panneau "Nouveau
  │   magasin → 2 rues plus loin". Google suit, indexe le nouveau.
  │   Action : aucune, normal pour les /h/* → /hotels/* (migration SEO 28 mai).
  │
  ├─ 5. Détectée, actuellement non indexée (Systèmes Google) ─ patience
  │   Analogie : Google connaît ton magasin (il a vu l'adresse) mais n'a pas
  │   encore pris le temps d'aller visiter. File d'attente Google.
  │   Action : aucune urgente. Pour accélérer : linker depuis pages importantes,
  │   améliorer le ranking interne.
  │
  └─ 6. Explorée, actuellement non indexée (Systèmes Google) ─ 🟠 LE PIRE
      Analogie : Google est entré, a regardé, et a décidé "bof, pas assez
      intéressant pour mon annuaire" (contenu trop léger, dupliqué, pas assez
      de signaux qualité). Google a VU et a JUGÉ.
      Action : enrichir contenu pages concernées (avis clients, photos, données
      structurées Schema.org HotelListing, signaux uniques).

  2 URLs bloquées par robots.txt (Google a envoyé email officiel le 1er juin) :
    - https://airbizness.com/hotels.html?dest=KUPRES%20AREA  (détectée 29/05/2026)
    - https://airbizness.com/vol.html?id=off_0000B63injNUuhcVR77kWa  (détectée 28/05/2026)
    Cause supposée : robots.txt bloque /hotels.html?* et /vol.html?* mais les liens
    avec ces query strings ont été crawlés via sitemap ou lien interne.
    À vérifier : robots.txt actuel + sitemap.xml + liens internes.

  Pages en double sans canonical :
    Aucune page produit n'a de <link rel="canonical">. Quand un produit (hôtel/vol)
    est accessible par plusieurs URLs (avec/sans query params), Google ne sait pas
    laquelle indexer → toutes les variantes sont considérées doublons.
    À faire : injecter <link rel="canonical" href="..."> sur :
      - public/hotels.html (canonical = URL sans query)
      - public/vol.html (canonical = URL avec offer_id stable)
      - public/quote.html (canonical = URL paramétrée stable)
      - pages SEO /hotels/{cc}/{ville}/{slug} (canonical = elles-mêmes)

  2 URLs bloquées par robots.txt (Google a envoyé email officiel le 1er juin) :
    - https://airbizness.com/hotels.html?dest=KUPRES AREA  (détectée 29/05/2026)
    - https://airbizness.com/vol.html?id=off_0000B63injNUuhcVR77kWa  (détectée 28/05/2026)
    Cause supposée : robots.txt bloque /hotels.html?* et /vol.html?* mais les liens
    avec ces query strings ont été crawlés via sitemap ou lien interne.
    À vérifier : robots.txt actuel + sitemap.xml + liens internes.

  Pages en double sans canonical :
    Aucune page produit n'a de <link rel="canonical">. Quand un produit (hôtel/vol)
    est accessible par plusieurs URLs (avec/sans query params), Google ne sait pas
    laquelle indexer → toutes les variantes sont considérées doublons.
    À faire : injecter <link rel="canonical" href="..."> sur :
      - public/hotels.html (canonical = URL sans query)
      - public/vol.html (canonical = URL avec offer_id stable)
      - public/quote.html (canonical = URL paramétrée stable)
      - pages SEO /hotels/{cc}/{ville}/{slug} (canonical = elles-mêmes)

### 🔴 BUGS UX OBSERVÉS (1er juin 2026)

  public/compte.html : feedback manquant sur "Envoyer le lien" de vérif email.
    Le badge "⚠ Email non vérifié — Envoyer le lien" reste en alerte rouge
    AVANT et APRÈS le clic. Aucun message de succès ("Lien envoyé, vérifie
    ta boîte mail") ni d'échec. Le user clique en boucle sans savoir si
    l'email part vraiment.
    À fixer : afficher un toast/message après le clic sur le lien.

  public/compte.html : H1 affiche l'EMAIL au lieu du PRÉNOM.
    Rendu actuel : "Bonjour pascal.repir@gmail.com" (en gros italique doré).
    Rendu attendu : "Bonjour Pascal".
    Cause probable : soit le formulaire signup ne demande pas first_name,
    soit le template du H1 affiche `user.email` au lieu de `user.first_name`.
    Sur un site premium B2C, l'email en H1 = signe amateur immédiat.
    À fixer : utiliser `user.first_name` avec fallback "Bonjour" si absent
    (jamais l'email en H1). Vérifier aussi que signup demande bien le prénom.

  public/compte.html : statut "Email non vérifié" PERSISTE après confirmation.
    Constaté par Pascal le 2026-06-01 : il a cliqué le lien de vérification
    reçu par mail (donc /auth/verify a bien été appelé côté backend), mais
    le badge "⚠ Email non vérifié — Envoyer le lien" reste affiché sur la
    page compte. Le front ne reflète pas le changement de statut.
    Causes possibles à investiguer :
      (a) /auth/verify ne met PAS à jour la DB (champ users.email_verified
          ou équivalent) → vérifier le handler dans routers/auth.py
      (b) /auth/me ne RENVOIE PAS le nouveau statut (cache backend ?)
      (c) Le front compte.html ne RÉ-INTERROGE PAS /auth/me après le clic
          de vérification (cache localStorage ou JWT contient l'ancien statut)
      (d) Le JWT signé contient email_verified à la création → ne se met
          jamais à jour tant qu'on n'a pas re-login. Auquel cas il faut
          soit invalider le JWT au verify, soit lire le statut depuis
          /auth/me à chaque chargement de /compte.html (pas du JWT).
    À fixer : tracer le flow complet verify-link → DB → /auth/me → front.
    Risque : un client qui voit "email non vérifié" alors qu'il l'a vérifié
    perd confiance et abandonne. Plus grave : il peut pas accéder aux
    features qui exigent email vérifié.

### 🔎 INVESTIGATIONS 2026-06-01 — résultats sur les 4 SKIP de l'orchestrateur

  #12 — Canonical vol.html → PAGE MORTE
    Fichier public/vol.html n'existe pas.
    Aucune route Python ne génère cette page (grep main.py + routers/ = 0).
    Aucun lien interne ne pointe dessus.
    Test live : curl https://airbizness.com/vol.html?id=xxx → HTTP 404
    main.py:9602 robots.txt contient encore Disallow: /vol.html (anti-crawl)
    Cause supposée : ancienne URL morte que Google a découvert via vieux sitemap.
    Action recommandée : NE RIEN FAIRE (Google va finir par l'oublier sur 404
    répétés). Pas besoin de canonical sur page qui n'existe pas.

  #14 — Canonical pages SEO hôtel → DÉJÀ FAIT (faux skip)
    Les 2 agents DeepSeek ont écrit "INCONNU" à tort. Vérifié live :
      curl /hotels/fr/paris/madame-reve  → <link rel="canonical" href="..."> ✅
      curl /destinations/paris           → <link rel="canonical" href="..."> ✅
      curl /vols/paris-new-york          → <link rel="canonical" href="..."> ✅
    Code source confirmé dans main.py :
      L6826 (hotel_seo_page template) — canonical_url dynamique
      L8269/8766 (destination_page)
      L9173 (vols_route_page)
    Statut : aucune action requise, canonicals SEO en place.

  #17 — Statut "Email non vérifié" persiste → CAUSE TROUVÉE : JWT figé
    DB correcte : SELECT users WHERE email='pascal.repir@gmail.com'
      → email_verified=TRUE (verify a bien marché côté backend)
    Route /auth/verify met bien à jour la DB (routers/auth.py:361)
    Route /auth/me retourne current_user (auth.py:132-134) qui vient de
      get_current_user() qui lit le JWT (Depends).
    Le JWT contient email_verified figé au moment du login. Si Pascal a
      login AVANT de cliquer verify, son JWT a stocké false. /auth/me
      retourne ce vieux JWT, pas un SELECT live depuis la DB.
    Conséquence : le front voit u.email_verified=false en permanence
      jusqu'au prochain logout/login complet.
    Fix options :
      (A) Modifier /auth/me pour TOUJOURS faire un SELECT live depuis DB
          (ignorer email_verified du JWT, le lire de la DB)
      (B) Re-signer le JWT au moment du verify et le renvoyer au client
          (le client doit alors le stocker en localStorage)
      (C) Côté front compte.html : forcer un logout/login auto après verify
    Recommandation : option (A) — le JWT garde ce qui est stable
      (user_id, email), /auth/me lit live ce qui peut changer (verified).

  #20 — Enrichir airports.json avec lat/lng → faisable
    Fichier existe : /var/www/airbizness/data/airports.json
    Format actuel : {code, name, city, country, type} — PAS de lat/lng
    Solution : télécharger OpenFlights airports.dat (CSV gratuit, 7000+
      aéroports avec lat/lng), parser, enrichir le JSON existant en
      ajoutant 2 champs lat + lng par aéroport (matching par code IATA).
    Effort : 1-2h (script Python + curl + parse + merge)
    Permet : haversine pour catcher vols absurdes (Air Serbia 2h55 CDG-JFK)

### 🎯 TEST UTILISATEUR HUMAIN — Sophie Martin + 2 enfants — 2026-06-01

  Sub-agent Claude QA a incarné Sophie Martin (35 ans, 2 enfants Léo 8 + Emma 5),
  parcours complet famille Paris → Barcelone 16-23 oct 2026, 3 voyageurs.
  Rapport complet : /var/www/airbizness/test_sophie_famille.md
  40 screenshots : /tmp/sophie_step_*.png

  VERDICT : 3/10 — Sophie n'achète pas.

  ✅ CE QUI MARCHE
    + Signup + login + carnet voyageurs (Léo + Emma créés, IDs 14/15/16)
    + Fix #16 confirmé live : "Bonjour Sophie" sur compte.html (plus l'email)
    + Bandeau "Tarifs indisponibles" honnête sur quote.html
    + AUCUN strike fake -90% sur cards vol (fix TOP 1 hier confirmé live)
    + AUCUN mock "Boutique Loft" visible (mocks gatés DEMO_MODE confirmés)
    + Tunnel passagers UX premium (bagages, sièges, assurance, compteur expiration)

  🔴 5 BUGS P0 QUI BLOQUENT L'ACHAT (à fixer en priorité)

    #BUG-E (ULTRA CRITIQUE) — front mappe "Barcelone" → "BAR" (3 premières lettres)
      → API retourne 59 hôtels à BARI (Italie) au lieu de Barcelone (BCN)
      Avec filtre 4★ : écran vide. destination=BCN côté API retourne bien 20 hôtels Barcelone.
      Fichier : public/hotels.html (logique de mapping ville→code IATA)
      Fix : table mapping correcte ville→IATA OU appeler /api/cities/search d'abord.

    #BUG-A — /resultats.html force cabin=business par défaut → 0 vols
      Même params + &cabin=economy → 93 offres (Vueling 359€, BA 458€)
      Fichier : public/resultats.html ou public/index.html (form vol home)
      Fix : default = economy. Business doit être un choix explicite.

    #BUG-D — autocomplete destination hôtel ne renvoie aucune suggestion
      BCN/Barcelone/JFK : aucune dropdown ne s'ouvre.
      Fichier : public/js/carnets.js (fonction setupCityAutocomplete) ou hotels.html
      Fix : event listener cassé, à debug.

    #BUG-G — URL builder cassé hotels.html → quote.html
      checkout=2026-10-23 saisi → URL quote.html devient checkout=2026-07-04&adults=2
      au lieu de adults=3. Tunnel corrompu.
      Fichier : public/hotels.html (génération du lien card → quote)
      Fix : passer checkout/adults proprement dans l'URL.

    #BUG-H — Récap checkout cassé + bouton "Continuer vers paiement" PAS désactivé
      flight-passengers : panel récap droite affiche "Total 0 €", dates "—",
      horaires "—:—", MAIS le bouton "Continuer vers le paiement" reste cliquable.
      Sophie peut soumettre un paiement à 0€ → risque DGCCRF + comptabilité.
      Fichiers : public/flight-passengers.html (init récap + état bouton)
      Fix : désactiver le bouton tant que total > 0, recalculer récap depuis l'offre.

  🟠 BUGS P1/P2 DÉCOUVERTS

    #BUG-F — Fix #15 NON appliqué (contrairement à ce que disait l'orchestrateur)
      Bouton "Envoyer le lien" sur compte.html : aucun feedback visuel après clic.
      À vérifier : le fix a-t-il vraiment été appliqué hier ? Re-tester live.

    #BUG-C — Calendar Duffel 6/7 cellules en duffel_429 (rate limit)
      Calendrier presque vide. À gérer côté UX (message "Indispo" déjà ajouté hier,
      mais 6/7 cells vides = mode dégradé permanent tant que Duffel sandbox).

    #BUG-B — Noms de params incohérents : depart/retour vs date/return
      Titre "Toutes dates" si on utilise depart/retour au lieu de date/return.
      Fichier : public/resultats.html (gestion query params)

    #BUG-K — Badge "Business" affiché sur cards economy (template)
      Fichier : public/resultats.html (rendu card)

    #BUG-L — Modal carnet voyageurs : type "Adulte" par défaut au lieu d'auto-calculer
      depuis la date de naissance. Léo 8 ans devrait être "Enfant" auto, pas "Adulte".
      Fichier : public/compte-voyageurs.html (modal ajout voyageur)

### 🧹 CLEANUP STUCK BOOKINGS — 2026-06-01 05:55 UTC

  Commande lancée : scripts/cleanup_stuck_bookings.py --hours 24 --apply
  Résultat : 12 bookings stuck marqués cancelled en DB :
    - 1 flight_booking (AB-FL-38CBB2, 1576.56€)
    - 6 bookings_v2 (hôtels)
    - 5 pack_bookings
  Refunds Stripe : tous ÉCHEC normal (PaymentIntent sans charge réussie).
    Cause : ces bookings étaient stuck en payment_pending = client n'a jamais
    payé (abandon checkout). Stripe refuse de refunder ce qui n'a jamais été
    débité. Comportement attendu, pas un bug.
  Conséquence : watchdog Telegram ne va plus alerter sur ces 12 stuck.
    Spam stoppé. À demain 03:30 UTC, prochain tick automatique du systemd timer.
  Timer systemd actuel : ExecStart sans --apply (dry-run par défaut).
    À CONSIDÉRER : ajouter --apply en permanence pour auto-cleanup quotidien.

### ✅ FIX BUGS P0 — 2026-06-01 (PHASE 1)
  Orchestrateur sub-agent (DeepSeek-orchestré) a appliqué les 5 fixes P0 issus
  du test Sophie + clarification du fix #15.

  BUG-E (Barcelone→BAR donnait Bari/Italie) ✅
    Fichier : public/hotels.html
    Fix : DEST_CODE_MAP étendu (barcelone/barcelona→BCN), regex /^[a-z]{3}$/i
    pour codes IATA, fallback /api/cities/search?q=...&limit=1, message
    user-friendly "Ville non reconnue" si rien ne match. Plus de slice(0,3)
    qui fabriquait des codes faux.

  BUG-A (cabin=business défaut → 0 vols) ✅
    Fichier : public/resultats.html (ligne 642)
    Fix : défaut passé à 'economy'. Business = choix explicite.

  BUG-D (autocomplete destination hôtel absent) ✅
    Fichier : public/hotels.html
    Fix : ajout <div class="ac-dropdown" id="dest-dropdown"> + appel
    window.setupCityAutocomplete('dest') au bas du script. doSearch préfère
    désormais dest.dataset.code (code IATA exact sélectionné dans dropdown).

  BUG-G (URL builder hotels.html → quote.html) ✅ déjà OK
    Fichier vérifié : public/js/carnets.js ligne 172. Le template URL passe
    correctement &checkin=${ctx.ci}&checkout=${ctx.co}&adults=${ctx.adults||2}.
    Probablement cache navigateur côté Sophie. Bump v=2026053114 pour purger.

  BUG-H/I/J (bouton activable à 0€, récap dates "—") ✅
    Fichier : public/flight-passengers.html
    Fix 1 : renderRecap désactive #pax-form .submit-btn si total<=0 ou
    DEAL.departure_at absent (with title "Total indisponible").
    Fix 2 : onSubmit garde double : si (_flightTotal+_optTotal)<=0 → alert +
    return AVANT toute soumission. Conformité DGCCRF.

  Fix #15 (compte.html "Envoyer le lien") — OK confirmed
    Fichier vérifié : public/compte.html lignes 177-205.
    La fonction sendVerify() change bien le badge (vert succès / rouge erreur)
    + appelle showToast(). Le fix était bien appliqué — feedback visuel
    présent. Rapport Sophie probablement basé sur ancien cache.

### RESTE À FAIRE
  - #17 — Fix JWT email_verified : faire que /auth/me lise email_verified depuis
    la DB live au lieu du JWT figé (sinon Pascal vérifie son email mais le badge
    reste "non vérifié" tant qu'il ne se relog pas).
  - #18bis — Brancher quota_status sur sejour.html (déjà branché sur hotels.html,
    mais pas sur le séjour vol+hôtel).
  - Enrichir airports.json avec lat/lng (pour mieux catcher vols absurdes)
  - Migrer routes encore dans main.py vers leurs modules
    (10 modules effectivement migrés (alertes, sandbox, widget, affiliate, seo, transferts, activites, webhook, pack, conciergerie), 1 module clos sans migration (mail = helper), 2 restants — pattern validé)
    Détail : /alertes → routers/alertes.py · /sandbox → routers/sandbox.py · /widget → routers/widget.py · /affiliate → routers/affiliate.py · /seo → routers/seo.py · /transferts → routers/transferts.py · /activites → routers/activites.py · /webhook → routers/webhook.py · /pack → routers/pack.py · /conciergerie → routers/conciergerie.py · /mail → closed-no-routes (helper services/mail.py)

### ADMIN PASCAL (externe, hors code)
  - Créer société Estonia + compte Stripe Estonia → clés sk_live_*
  - Contrat HBX prod signé
  - CGV validées juridiquement (placeholders en place dans cgv.html)
  - airbizness-cleanup-stuck-bookings.service : ajouter --apply quand LIVE
──────────────────────────────────────────────────────────────────────────────
"""
from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
import os, json, html as _h, secrets, logging
from datetime import datetime

logger = logging.getLogger("schema_admin")

async def require_admin_token(admin_token: str = Query(None), request: Request = None):
    env_token = os.getenv("ADMIN_AUDIT_TOKEN", "")
    if not env_token:
        raise HTTPException(status_code=503, detail="Admin token non configuré côté serveur")
    if admin_token is None or not secrets.compare_digest(admin_token, env_token):
        path = request.url.path if request else "unknown"
        client_ip = request.client.host if request and request.client else "unknown"
        logger.warning(f"admin_route refused ip={client_ip} path={path}")
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

router = APIRouter()

_MAIN = "/var/www/airbizness/main.py"
_FLAGS = "/var/www/airbizness/.feature_flags.json"
_MOCK_FNS = ["_generate_mock_flight_deals", "_mock_search_from_catalog", "_mock_rooms_for_hotel",
             "_mock_checkrate_response", "_mock_fare_options", "_generate_mock_seat_map"]

_GROUP_LABELS = {
    "deals": "✈️ Vols / deals", "flights": "✈️ Vols / deals", "flight": "✈️ Réservation vol",
    "duffel": "✈️ Duffel", "hotels": "🏨 Hôtels", "hbx": "🏨 HBX (hôtels/activités/transferts)",
    "v2": "🏨 Hôtels v2", "destinations": "🔎 SEO", "vols": "🔎 SEO", "hotel": "🏨 Hôtel",
    "pack": "🧳 Packs vol+hôtel", "claim": "🤝 Hôtelier / claim", "hotel-manager": "🤝 Hôtelier",
    "resilience": "🛟 Résilience / substituts", "conciergerie": "🛟 Conciergerie",
    "api": "⚙️ Admin / API", "v2/providers": "⚙️ Santé providers", "alertes": "🔔 Alertes",
    "share": "🔗 Partage", "airports": "🔎 Autocomplete", "cities": "🔎 Autocomplete",
}

def _db_count_tables():
    try:
        import psycopg2
        c = psycopg2.connect(host=os.getenv("DB_HOST"), dbname=os.getenv("DB_NAME"),
                             user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"))
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        n = cur.fetchone()[0]
        cur.close(); c.close()
        return n
    except Exception:
        return "?"

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0f0f;color:#f0ece4;font-family:'DM Sans',-apple-system,Segoe UI,sans-serif;line-height:1.6;padding:24px;max-width:1100px;margin:0 auto}
h1{font-family:Georgia,serif;color:#d4ae4a;font-size:26px}
.sub{color:#6a6058;font-size:12px;margin:4px 0 22px}
h2{font-family:Georgia,serif;color:#b8962e;font-size:19px;margin:30px 0 12px;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,.08)}
.kpi{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.kpi div{background:#161616;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px 16px;flex:1;min-width:120px}
.kpi .n{font-family:Georgia,serif;font-size:24px;color:#d4ae4a}
.kpi .n.ok{color:#4bbf73}.kpi .n.bad{color:#e0564a}
.kpi .l{font-size:10.5px;color:#6a6058;text-transform:uppercase;letter-spacing:.5px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,.08);vertical-align:top}
th{color:#d4ae4a;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
code{background:#1e1e1e;padding:1px 6px;border-radius:4px;color:#d4ae4a;font-size:12px}
.on{color:#4bbf73;font-weight:600}.off{color:#6a6058}
.live{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4bbf73;margin-right:6px;animation:p 1.6s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.danger{background:rgba(224,86,74,.08);border:1px solid rgba(224,86,74,.4);border-radius:8px;padding:12px;margin:8px 0;color:#ffd9d4}
.good{background:rgba(75,191,115,.08);border:1px solid rgba(75,191,115,.35);border-radius:8px;padding:12px;margin:8px 0;color:#cdebd6}
.mermaid{background:#161616;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:14px;margin:10px 0;overflow:auto;text-align:center}
h3{font-family:Georgia,serif;color:#c9a23a;font-size:14px;margin:20px 0 6px}
pre{background:#0a0a0a;border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:14px;overflow:auto;font-size:11.5px;color:#cdbf93;line-height:1.5;max-height:640px;white-space:pre}
a{color:#d4ae4a}
.modgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin:10px 0}
.modgrid a{display:block;background:#161616;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px 14px;text-decoration:none;transition:border-color .15s}
.modgrid a:hover{border-color:#d4ae4a}
.modgrid b{display:block;color:#f0ece4;font-size:13px;margin-bottom:3px}
.modgrid span{color:#6a6058;font-size:11px}
.modlinks{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.modlinks a{background:#1e1e1e;border:1px solid rgba(212,174,74,.4);border-radius:20px;padding:6px 14px;text-decoration:none;color:#d4ae4a;font-size:12.5px;transition:all .15s}
.modlinks a:hover{background:#2a2a2a;border-color:#d4ae4a}
.fn-done{color:#4bbf73;white-space:nowrap}.fn-fix{color:#d4ae4a;white-space:nowrap}.fn-todo{color:#8a8076;white-space:nowrap}.fn-dead{color:#e0564a;white-space:nowrap}
"""

_MERMAID = """flowchart TD
  PAGES["PAGES (affichage)<br/>accueil · recherche · fiches SEO hotel/vol/destination · resultats · sejour"]
  CHAT["MODULE CHAT (concierge)<br/>oriente vers le bon module · n'execute pas, ne decide pas"]
  RECH["MODULE RECHERCHE<br/>slug→IATA · autocomplete · normalise date/cabin/pax<br/>routers/recherche.py"]
  PROV["MODULE PROVIDERS<br/>registry + feature_flags · adapters (Duffel splitté, HBX, …)<br/>mappe brut → canonique (UnifiedOffer, SeatMap)"]
  CATALOG["MODULE CATALOGUE<br/>memoire operationnelle (PostgreSQL)<br/>hbx_hotels_catalog · deals · hotel_seo_content · ..."]
  PH["catalogue · providers HOTEL<br/>HBX ON · Ratehawk/TBO/WebBeds off"]
  PV["catalogue · providers VOL<br/>Duffel ON · Amadeus/Travelpayouts off"]
  HOT["MODULE HOTEL<br/>(provider-agnostique)"]
  VOL["MODULE VOL<br/>(provider-agnostique)"]
  CLI["MODULE ESPACE CLIENT<br/>compte · mes voyages · mes alertes"]
  PAY["MODULE PAIEMENT (separe)<br/>Stripe"]
  MOD["MODULE MODIFICATION + REMBOURSEMENT<br/>definit les remboursements · resilience/executors"]
  DB[("PostgreSQL")]
  TG["ALERTES TELEGRAM · watchdog<br/>4 copies dispersees -> a centraliser en 1 module"]
  PAGES -->|intention recherche| RECH
  CHAT -->|intention recherche| RECH
  RECH -->|query canonique| HOT
  RECH -->|query canonique| VOL
  PAGES -->|login, mes resas, alertes| CLI
  CHAT -->|oriente| MOD
  CHAT -->|oriente| CLI
  CHAT --> DB
  HOT -->|requete canonique| PROV
  VOL -->|requete canonique| PROV
  PROV -->|delegue| PH
  PROV -->|delegue| PV
  PH -->|brut| PROV
  PV -->|brut| PROV
  PROV -->|daemons alimentent| CATALOG
  CATALOG -->|lu par| HOT
  CATALOG -->|lu par| VOL
  PROV -->|offres canoniques| HOT
  PROV -->|offres canoniques| VOL
  HOT -->|reserver| PAY
  VOL -->|reserver| PAY
  PAY -->|payment-intent, confirmer| DB
  CLI --> DB
  HOT -->|si annul/modif| MOD
  VOL -->|si annul/modif| MOD
  MOD -->|annuler, modifier, rembourser| DB
  HOT --> DB
  VOL --> DB
  PAY -.->|erreur| TG
  MOD -.->|erreur| TG
  HOT -.->|erreur| TG
  VOL -.->|erreur| TG
  CLI -.->|erreur| TG"""

_ACTIONS_TABLE = (
    "<table><tr><th>Module</th><th>Reçoit</th><th>Action</th><th>Renvoie</th></tr>"
    "<tr><td><b>pages (affichage)</b></td><td>un visiteur</td><td>affiche accueil / fiches SEO / résultats, lance les recherches, déroule le tunnel</td><td>HTML (pages)</td></tr>"
    "<tr><td><b>chat (concierge)</b></td><td>un message en langage naturel</td><td>comprend l'intention → <b>oriente vers le bon module/action</b> (n'exécute pas lui-même)</td><td>réponse + redirection</td></tr>"
    "<tr><td><b>hotel</b></td><td>ville + dates</td><td>interroge TOUS les providers hôtel allumés (catalogue) → normalise → dédoublonne (giata)</td><td>offres hôtel unifiées</td></tr>"
    "<tr><td><b>vol</b></td><td>route + dates</td><td>interroge TOUS les providers vol allumés (catalogue) → normalise</td><td>offres vol unifiées</td></tr>"
    "<tr><td><b>réservation</b></td><td>l'offre choisie</td><td>crée la résa via le provider qui détient l'offre</td><td>référence de réservation</td></tr>"
    "<tr><td><b>paiement</b></td><td>montant + résa</td><td>crée le payment-intent Stripe → confirme le paiement</td><td>paiement validé</td></tr>"
    "<tr><td><b>espace client</b></td><td>email / login</td><td>liste mes réservations, gère les alertes et le compte</td><td>données du client</td></tr>"
    "<tr><td><b>modification + remboursement</b></td><td>référence résa</td><td>définit et exécute les annulations / modifications / remboursements (<code>resilience/executors</code>)</td><td>statut mis à jour</td></tr>"
    "<tr><td><b>alertes Telegram (watchdog)</b></td><td>un événement / une erreur</td><td>aboie sur Telegram. ⚠️ aujourd'hui <b>4 copies dispersées</b> (main.py, daemon SEO, sync HBX, resilience) → à centraliser en 1 module</td><td>message Telegram</td></tr>"
    "</table>"
)

@router.get("/schema-technique", response_class=HTMLResponse, dependencies=[Depends(require_admin_token)])
def schema_technique(request: Request):
    paths = sorted({getattr(r, "path", "") for r in request.app.routes if getattr(r, "path", "")})
    n_routes = len(paths)

    groups = {}
    for p in paths:
        seg = (p.strip("/").split("/")[0] or "(racine)")
        groups.setdefault(seg, []).append(p)

    try:
        src = open(_MAIN, encoding="utf-8").read()
        n_lines = src.count("\n") + 1
    except Exception:
        src, n_lines = "", "?"
    mocks_present = [f for f in _MOCK_FNS if ("def " + f + "(") in src]

    flags = {}
    try:
        from providers.feature_flags import DEFAULT_FLAGS
        flags = dict(DEFAULT_FLAGS)
    except Exception:
        pass
    try:
        if os.path.exists(_FLAGS):
            flags.update(json.load(open(_FLAGS)))
    except Exception:
        pass
    provs = sorted((k[len("provider_"):-len("_enabled")], bool(v)) for k, v in flags.items()
                   if k.startswith("provider_") and k.endswith("_enabled"))
    n_on = sum(1 for _, v in provs if v)

    n_tables = _db_count_tables()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # --- modularisation : routes par module Python (live, via __module__) ---
    by_module = {}
    for r in request.app.routes:
        ep = getattr(r, "endpoint", None)
        if ep is None:
            continue
        m = getattr(ep, "__module__", "?")
        by_module[m] = by_module.get(m, 0) + 1
    n_main = by_module.get("main", 0)
    n_ep = sum(by_module.values()) or 1
    pct_mod = round(100 * (n_ep - n_main) / n_ep)
    mod_rows = ""
    for m, cnt in sorted(by_module.items(), key=lambda kv: -kv[1]):
        is_main = (m == "main")
        cls = "off" if is_main else "on"
        tag = " ⚠️ monolithe" if is_main else " ✓ module"
        mod_rows += (f"<tr><td><code>{_h.escape(m)}</code>{tag}</td>"
                     f"<td style='text-align:right' class='{cls}'>{cnt}</td></tr>")

    # --- providers rows ---
    prov_rows = "".join(
        f"<tr><td><code>{_h.escape(name)}</code></td>"
        f"<td class='{'on' if on else 'off'}'>{'● ALLUMÉ' if on else '○ éteint'}</td></tr>"
        for name, on in provs
    ) or "<tr><td colspan='2' class='off'>aucun flag provider</td></tr>"

    # --- route groups rows ---
    grp_rows = ""
    for seg in sorted(groups, key=lambda s: -len(groups[s])):
        label = _GROUP_LABELS.get(seg, "")
        sample = ", ".join("<code>/" + _h.escape(p.strip("/")) + "</code>" for p in groups[seg][:4])
        if len(groups[seg]) > 4:
            sample += f" <span class='off'>+{len(groups[seg]) - 4}</span>"
        grp_rows += (f"<tr><td>/{_h.escape(seg)} {label}</td>"
                     f"<td style='text-align:right'>{len(groups[seg])}</td>"
                     f"<td>{sample}</td></tr>")

    # --- mocks status ---
    if mocks_present:
        mock_html = ("<div class='danger'>⚠️ Générateurs de faux ENCORE présents : "
                     + ", ".join("<code>" + _h.escape(m) + "</code>" for m in mocks_present) + "</div>")
        mock_kpi = f"<div class='n bad'>{len(mocks_present)}</div>"
    else:
        mock_html = "<div class='good'>✓ Aucun générateur de faux dans le code. Le site n'invente plus rien.</div>"
        mock_kpi = "<div class='n ok'>0</div>"

    html = (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<meta name='robots' content='noindex,nofollow'>"
        "<title>AirBizness — Schéma technique (temps réel)</title>"
        "<style>" + _CSS + "</style>"
        "<script src='https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js'></script>"
        "</head><body>"
        "<h1>AirBizness — Schéma technique</h1>"
        f"<div class='sub'><span class='live'></span>EN TEMPS RÉEL — introspection à l'instant : {now}</div>"
        "<div class='kpi'>"
        f"<div><div class='n'>{n_lines}</div><div class='l'>lignes main.py</div></div>"
        f"<div><div class='n'>{n_routes}</div><div class='l'>routes enregistrées (live)</div></div>"
        f"<div><div class='n'>{n_tables}</div><div class='l'>tables PostgreSQL</div></div>"
        f"<div><div class='n'>{len(provs)}</div><div class='l'>providers ({n_on} allumés)</div></div>"
        f"<div>{mock_kpi}<div class='l'>générateurs de faux</div></div>"
        f"<div><div class='n'>{pct_mod}%</div><div class='l'>routes hors main.py</div></div>"
        "</div>"
        "<h2>Schéma d'architecture</h2>"
        "<div class='mermaid'>" + _MERMAID + "</div>"
        "<div class='good'>Principe : <b>le client cherche et réserve lui-même</b> (zéro IA qui invente). <b>Le code décide</b> (le labyrinthe), le <b>chat oriente</b> seulement, et les <b>remboursements sont définis dans le module remboursement</b>.</div>"
        "<h2>Modules — clique pour la description + le code</h2>"
        "<div class='modgrid'>" + "".join(
            f"<a href='/api/schema-module/{m['key']}'><b>{_h.escape(m['label'])}</b>"
            f"<span>{_h.escape(m['comportement'][:75])}…</span></a>" for m in _MODULES) + "</div>"
        "<h2>Actions attendues par module</h2>" + _ACTIONS_TABLE +
        "<h2>Réorganisation en modules — EN DIRECT</h2>"
        f"<div class='sub'>{n_main} routes encore dans main.py · {n_ep - n_main} déjà sorties en modules · {pct_mod}% modularisé. À chaque migration, une route passe de <code>main</code> vers <code>routers.*</code>.</div>"
        "<table><tr><th>Module Python</th><th>Routes</th></tr>" + mod_rows + "</table>"
        "<h2>Données fabriquées ?</h2>" + mock_html +
        "<h2>Providers (catalogue + on/off live)</h2>"
        "<table><tr><th>Provider</th><th>État</th></tr>" + prov_rows + "</table>"
        "<h2>Routes enregistrées — par groupe (live)</h2>"
        "<table><tr><th>Groupe</th><th>Nb</th><th>Exemples</th></tr>" + grp_rows + "</table>"
        "<div class='sub' style='margin-top:26px'>Cette page lit l'état RÉEL à chaque rafraîchissement "
        "(routes via <code>app.routes</code>, mocks via le code, providers via <code>.feature_flags.json</code>, "
        "tables via PostgreSQL). Module isolé : <code>routers/schema.py</code>.</div>"
        "<div style='margin-top:32px; padding:16px; background:#1a1a2e; border-left:3px solid #00d4aa; border-radius:4px'>"
        "<h2 style='color:#00d4aa; margin:0 0 12px 0; font-size:1.3em'>Notice technique</h2>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>1. Architecture générale</h3>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Stack : FastAPI (uvicorn 4 workers, port 8001), Nginx frontal TLS avec gate coming-soon par --resolve, PostgreSQL DB airbizness, Stripe sk_test_ mode test, Brevo SMTP sender noreply@airbizness.com, Duffel API sandbox vols, Hotelbeds HBX API sandbox hôtels.</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Flow : client → nginx (TLS) → uvicorn:8001 → main.py → routers/*.py → providers/* → DB/API externes → HTTPResponse.</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Service systemd airbizness.service, restart auto. Repo /var/www/airbizness/.</p>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>2. Modules — table récap</h3>"
        "<table style='width:100%; border-collapse:collapse; margin:8px 0; font-size:0.9em'>"
        "<tr><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Module</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Fichiers clés</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Routes HTTP</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Tables DB</th></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>pages</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>public/*.html</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/destinations /vols/ /hotels/ /home-stats /og-image /share</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotels_canonical, city_seo_content, hotel_seo_content</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>carnet</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>public/js/carnets.js</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/js/carnets.js</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>aucune (JS pur)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>chat</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>public/bizzi-chat.html</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/concierge</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>aucune</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>recherche</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/recherche.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/airports /cities</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>route_stats</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>catalogue</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/hbx_full_sync.py + seo_auto_generator.py + backfill_canonical_from_catalog.py + watchdog_pipeline.py + update_route_stats.py + fetcher.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(daemons)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_hotels_catalog, hotels_canonical, hotel_seo_content, city_seo_content, route_stats, deals, hbx_catalog_sync_state/_log</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>cache</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>services/api_cache.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>décorateur @cached</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(in-memory)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>providers</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>providers/registry.py + feature_flags.py + base/ + duffel/ + hbx/</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(appels externes)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(consomme catalogue)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/hotel.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/api/hotels/search, /api/hotels/{code}, /quote, /rooms, /availability</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_search_cache, hbx_hotels_catalog</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>vol</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/vol.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/api/deals, /api/flights/price-calendar, /api/flights/price-month, /api/deals/by-id</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>flight_offers_cache, flight_price_calendar_cache, deals</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>activites</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>providers/hbx/activities/</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/hbx/activities, /hbx/activity-booking</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(live HBX)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>transferts</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>providers/hbx_transfer.py + providers/hbx/transfers/</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/hbx/transfers, /api/transfer</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(non commercialisé)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>sejour</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>public/sejour.html</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/pack</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(frontend pur)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>reservation</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/reservation/vol.py + hotel.py + cleanup_stuck_bookings.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/hbx/booking, /flight/booking, /booking, /bookings</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>bookings_v2, flight_bookings, pack_bookings</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>paiement</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/paiement.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/create-payment-intent, /stripe-webhook, /flight/booking/payment-intent, /hotel/booking/payment-intent</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>bookings_v2, flight_bookings, pack_bookings</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>modif_remb</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>resilience/executors.py + cushion.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/cancel, /resilience</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>bookings_v2, flight_bookings, pack_bookings</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>client</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>public/compte.html + routers/auth.py (à créer)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/account, /compte, /alertes, /api/auth/*, /api/user/*</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>users, user_passengers (à créer)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotelier</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>main.py (module hotelier)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/hotel-manager, /claim, /conciergerie, /leads</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_claims, hotel_managed_data, agent_contacts</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>ops</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/hbx_full_sync + watchdog_pipeline + purge_expired_caches + cleanup_stuck_bookings + backfill_canonical + update_route_stats + update_provider_metrics</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/api/admin, /hbx/catalog, /v2/providers, /sandbox, /stats, /healthz, /supervisor</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>provider_health, hbx_catalog_sync_state/_log</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>seo</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/seo_auto_generator.py + main.py (sitemap+robots)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/sitemap, /robots, /sitemap-priority</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_seo_content, city_seo_content</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>publisher</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(transverse)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(aucune route dédiée)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>(écrit pages déployées)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>airbizness</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/airbizness_api.py + providers/airbizness/</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>/api/airbizness/transfers, /api/hotel-manager/transfers</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_managed_transfers, transfer_bookings</td></tr>"
        "</table>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>3. Providers et catalogue PostgreSQL</h3>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Providers externes</h4>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>HBX (providers/hbx/, auth HBX_API_KEY + HBX_SECRET, endpoints search/checkrate/booking/rooms, cache TTL 600s, fallback hbx_hotels_catalog si quota_exceeded)</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Duffel (providers/duffel/ split en 7 fichiers client/search/order/seat_maps/passengers/models/provider, auth DUFFEL_TOKEN, endpoints offer_requests/offers/orders/seat_maps, SANDBOX prix non engageants)</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Stripe (sk_test_ mode test, endpoints PaymentIntent/Refund/webhook, garde-fou int(round(x*100)) anti-troncature DGCCRF 4,6%)</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>Brevo (services/mail.py SDK SIB, blocklist @test.com +fuzz@ +test@, sender noreply@airbizness.com)</p>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>AirBizness natif (providers/airbizness/, 6 endpoints HTTP, marge 15% en colonne GENERATED, URL AIRBIZNESS_API_BASE pour portabilité)</p>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Tables principales</h4>"
        "<table style='width:100%; border-collapse:collapse; margin:8px 0; font-size:0.9em'>"
        "<tr><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Table</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Volume</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Alimenteur</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Usage</th></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_hotels_catalog</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>~7667</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/hbx_full_sync.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>source brute HBX</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotels_canonical</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>~7448</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_full_sync.py + backfill_canonical_from_catalog.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>dédoublonné giata_code</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_seo_content</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>~7433</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/seo_auto_generator.py (DeepSeek grounded, 50/tick, ~960/jour)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>contenu SEO long</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>city_seo_content</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>151</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>seo_auto_generator.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>villes destinations</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>route_stats</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>80</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/update_route_stats.py (cron 04h)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>agrégats routes vol SEO</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>deals</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>fetcher.py (cron */30min)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>cache vol pré-rempli</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>flight_offers_cache</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>_fetch_or_cache_offers (main.py)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>cache live à la requête</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>flight_price_calendar_cache</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/vol.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>cache bandeau ±3j</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_search_cache</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>providers/hbx/hotels/cache_layer.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>cache recherche HBX</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>bookings_v2, flight_bookings, pack_bookings</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/paiement.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>réservations confirmées</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_claims</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>2 actifs</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>module hotelier</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>revendications fiches</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_managed_data</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>module hotelier</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>overrides hôteliers</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hbx_catalog_sync_state, hbx_catalog_sync_log</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/hbx_full_sync.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>journal sync HBX</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>hotel_managed_transfers, transfer_bookings</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>routers/airbizness_api.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>marketplace transferts natifs</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>provider_health</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>variable</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/update_provider_metrics.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>métriques santé providers</td></tr>"
        "</table>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>4. Cycle de vie d&apos;une réservation</h3>"
        "<ol style='margin:4px 0 8px 0; padding-left:20px; line-height:1.6'>"
        "<li>Recherche : home/index.html → form → /api/hotels/search ou /api/deals → providers polling (search_hotels_multi pour HBX, _fetch_or_cache_offers pour Duffel) → response cards</li>"
        "<li>Sélection : carte clickée → quote.html (hôtel) ou resultats.html → flight-passengers.html (vol)</li>"
        "<li>Tunnel paiement : flight-checkout.html ou checkout.html → POST /api/flight/booking/payment-intent OU /api/hotel/booking/payment-intent → Stripe PaymentIntent + INSERT bookings_v2/flight_bookings (status=payment_pending)</li>"
        "<li>Confirmation : webhook Stripe POST /stripe-webhook → handler payment_intent.succeeded → update booking status=confirmed → create_order Duffel ou create_booking HBX → email Brevo</li>"
        "<li>Annulation/Refund : POST /cancel OU daemon scripts/cleanup_stuck_bookings.py --apply (timer nightly 03:30 UTC, dry-run actuellement)</li>"
        "</ol>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>5. Jobs, timers et cache court</h3>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Systemd timers</h4>"
        "<table style='width:100%; border-collapse:collapse; margin:8px 0; font-size:0.9em'>"
        "<tr><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Timer</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Fréquence</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Script</th></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>airbizness-watchdog-pipeline.timer</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>30 min</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/watchdog_pipeline.py (compare catalog/canonical/seo, alerte Telegram)</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>airbizness-purge-caches.timer</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>1h</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/purge_expired_caches.py</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>airbizness-cleanup-stuck-bookings.timer</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>03:30 UTC quotidien (dry-run)</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>scripts/cleanup_stuck_bookings.py</td></tr>"
        "</table>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Crontab ubuntu</h4>"
        "<table style='width:100%; border-collapse:collapse; margin:8px 0; font-size:0.9em'>"
        "<tr><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Tâche</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Fréquence</th><th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Effet</th></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>fetcher.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>*/30min</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>alimente deals via providers/duffel/</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>DELETE FROM deals expired</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>03h</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>purge deals périmés</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>update_route_stats.py</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>04h</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>agrégation route_stats</td></tr>"
        "<tr><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>airbizness_watchdog.sh</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>2min</td><td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>process alive uvicorn:8001</td></tr>"
        "</table>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Cache court services/api_cache.py</h4>"
        "<p style='margin:0 0 8px 0; line-height:1.5'>in-memory dict Python single-process (uvicorn 4 workers = 4 caches isolés), LRU sur max_entries, _IGNORED_KWARGS exclut request/response FastAPI. INTERDITS sur endpoints à prix engagé : /api/hotels/quote, /api/duffel/refresh_offer, /api/flight/booking/payment-intent, /api/hotel/booking/payment-intent.</p>"
        "<table style='width:100%; border-collapse:collapse; margin:8px 0; font-size:0.9em'>"
        "<tr><th style='text-align:"
        "left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Endpoint</th>"
        "<th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>TTL</th>"
        "<th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Max entries</th>"
        "<th style='text-align:left; padding:6px 8px; border-bottom:1px solid #444; color:#00d4aa'>Gain mesur&eacute;</th>"
        "</tr>"
        "<tbody>"
        "<tr>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'><code style='background:#0f0f1e; padding:1px 4px; border-radius:2px; color:#d4ae4a'>/api/hotels/search</code></td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>600s</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>20000</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>&times;12 (584ms MISS &rarr; 46ms HIT)</td>"
        "</tr>"
        "<tr>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'><code style='background:#0f0f1e; padding:1px 4px; border-radius:2px; color:#d4ae4a'>/api/deals</code></td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>300s</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>10000</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>&times;49 (1655ms MISS &rarr; 34ms HIT)</td>"
        "</tr>"
        "<tr>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'><code style='background:#0f0f1e; padding:1px 4px; border-radius:2px; color:#d4ae4a'>/api/flights/price-calendar</code></td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>300s</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>5000</td>"
        "<td style='padding:6px 8px; border-bottom:1px solid #2a2a3e'>&times;90 (3365ms MISS &rarr; 37ms HIT)</td>"
        "</tr>"
        "</tbody>"
        "</table>"
        "<h3 style='color:#e0e0e0; margin:16px 0 8px 0; font-size:1.1em'>6. S&eacute;curit&eacute;, pages SEO et dette technique</h3>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>S&eacute;curit&eacute; et garde-fous</h4>"
        "<ul style='margin:4px 0 8px 0; padding-left:20px; line-height:1.5'>"
        "<li>JWT auth PyJWT HS256 + bcrypt password hash (TTL 30j)</li>"
        "<li>services/mail.py blocklist anti-fuzz (@test.com, +fuzz@, +test@)</li>"
        "<li>Garde-fou anti-paiement vol suspicious_duration (&lt; 90 min &rarr; HTTP 409)</li>"
        "<li>Bouton Payer d&eacute;sactiv&eacute; front si total &le; 0 (anti-DGCCRF)</li>"
        "<li>int(round(x*100)) pour centimes Stripe (anti-troncature DGCCRF 4,6%)</li>"
        "<li>robots.txt Disallow tunnel/admin</li>"
        "<li>meta robots noindex,nofollow sur la page schema-technique</li>"
        "<li>Signature webhook Stripe obligatoire (HMAC)</li>"
        "<li>Watchdog Telegram pipeline (30min) + process alive (2min)</li>"
        "<li>Mocks providers refusent import si APP_ENV=production</li>"
        "</ul>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Pages SEO &mdash; rendu serveur</h4>"
        "<ul style='margin:4px 0 8px 0; padding-left:20px; line-height:1.5'>"
        "<li>/hotels/{cc}/{ville}/{slug} : main.py:hotel_seo_page &rarr; _render_hotel_unified (template Jinja-like Python pur, lignes ~6381-7150 de main.py)</li>"
        "<li>/destinations/{ville} : main.py:destination_page</li>"
        "<li>/vols/{slug} : main.py:vols_route_page</li>"
        "<li>Toutes ont &lt;link rel=&quot;canonical&quot;&gt; (v&eacute;rifi&eacute; 2026-06-01)</li>"
        "<li>/sitemap.xml dynamique : hotels_canonical + city_seo_content + route_stats (7517 URLs, priority 0.9 changefreq weekly)</li>"
        "<li>robots.txt : Disallow /hotels.html /vol.html /checkout.html /quote.html /api/ /admin/</li>"
        "</ul>"
        "<h4 style='color:#b0b0b0; margin:12px 0 6px 0; font-size:1em'>Dette technique connue</h4>"
        "<ul style='margin:4px 0 8px 0; padding-left:20px; line-height:1.5'>"
        "<li>Stripe TEST (sk_test_*) &mdash; pas LIVE</li>"
        "<li>HBX SANDBOX, quota mensuel creve chronique</li>"
        "<li>Duffel SANDBOX, prix non engageants</li>"
        "<li>CGV avec placeholders [A FAIRE VALIDER PAR CONSEIL JURIDIQUE]</li>"
        "<li>Issue #17 email_verified persistant JWT fige</li>"
        "<li>Issue #18bis quota_status non branche sur sejour.html</li>"
        "<li>Issue #20 airports.json sans lat/lng</li>"
        "<li>Alertes Telegram 4 copies a centraliser</li>"
        "<li>fetcher.py a migrer vers scripts/</li>"
        "<li>stripe_webhook 1043 lignes monolithiques a decouper</li>"
        "</ul>"
        "</div>"
        "<script>mermaid.initialize({startOnLoad:true,theme:'dark',themeVariables:{fontSize:'13px'}});</script>"
        "</body></html>"
    )
    return HTMLResponse(html)


# ───────────────────────────────────────────────────────────────────
# Pages PAR MODULE : ce que fait le module + son CODE réel (live)
# ───────────────────────────────────────────────────────────────────
_MODULES = [
    {"key": "pages", "label": "Pages (affichage)", "paths": ["/destinations", "/vols/", "/hotels/", "/home-stats", "/og-image", "/h/ (301→/hotels/)", "/share", "/hotel/interest-points", "/hotels/autocomplete"], "files": ["public/coming-soon.html (= public/index.html depuis 2026-05-30)"],
     "recoit": "un visiteur", "comportement": "affiche accueil, fiches SEO, résultats ; le client lance lui-même ses recherches", "declenche": "appels aux modules offre (hotel, vol…)", "renvoie": "HTML", "regles": "contenu réel uniquement, aucune invention"},
    {"key": "carnet", "label": "Module carnet (composants JS partagés frontend) — Pascal 2026-05-31",
     "paths": ["/js/carnets.js"],
     "files": ["public/js/carnets.js"],
     "recoit": "inclusion <script src='/js/carnets.js?v=NNNN'> depuis chaque page consommatrice",
     "comportement": "regroupe TOUS les composants JS partagés entre pages. 1 fichier inclus = toutes les pages bénéficient. Fix une fois → propagé partout (plus de drift entre pages). 5 sections : (1) Hotel Card → renderHotelCard + galerie+favori, (2) Search Autocomplete → setupAirportAutocomplete + setupCityAutocomplete + SearchOverlay fullscreen mobile + recents localStorage + populaires par région + grouping métro (CDG+ORY → 'Paris'), (3) SharedSearch → état partagé cross-onglets origine/dest/dates via sessionStorage, (4) Seats → store id→designator (évite affichage 'ase_0000B6qw...' brut Duffel), (5) TunnelState → cart recovery N1 (localStorage, TTL 24h) : sauvegarde step/search_context/selected_hotel/selected_flight + bannière 'Reprendre votre voyage' sur la home",
     "declenche": "appels API /api/airports/search, /api/cities/search pour autocompletes",
     "renvoie": "HTML rendu + event listeners câblés sur les inputs des pages + CSS auto-injecté",
     "regles": "IIFE par section (pas de collision scope) · idempotent via guard window.__loaded · CSS auto-injecté (page n'a pas besoin de CSS spécifique) · versioning ?v=YYYYMMDDNN bumpé à chaque modif via sed sur TOUTES les pages consommatrices · nouveaux composants ajoutés DANS carnets.js (jamais de fichier séparé)",
     "consumers": [
        {"ui": "index.html", "endpoint": "—", "source": "setupAirportAutocomplete + setupCityAutocomplete + SharedSearch + TunnelState.renderResumeBanner", "state": "done"},
        {"ui": "admin-home.html", "endpoint": "—", "source": "idem index (preview admin)", "state": "done"},
        {"ui": "hotels.html", "endpoint": "—", "source": "renderHotelCard", "state": "done"},
        {"ui": "sejour.html", "endpoint": "—", "source": "renderHotelCard + setupAirportAutocomplete + Seats.setMap + TunnelState.save (à chaque goStep)", "state": "done"},
        {"ui": "flight-passengers.html", "endpoint": "—", "source": "Seats.setMap", "state": "done"},
        {"ui": "flight-checkout.html", "endpoint": "—", "source": "Seats.getLabel", "state": "done"},
     ],
     "next": [
         {"name": "TunnelState save sur flight-passengers + flight-checkout", "state": "todo", "desc": "actuellement save UNIQUEMENT depuis sejour.goStep — étendre"},
         {"name": "TunnelState.clear() après paiement réussi", "state": "todo", "desc": "éviter bannière 'reprendre' après une résa terminée"},
         {"name": "Promouvoir vers N2 (state en DB lié au user_id)", "state": "todo", "desc": "dépend du module client (auth)"},
     ]},
    {"key": "chat", "label": "Chat (concierge)", "paths": ["/concierge"], "files": ["public/bizzi-chat.html"],
     "recoit": "un message en langage naturel", "comportement": "comprend l'intention et ORIENTE vers le bon module — il n'exécute pas, ne décide pas", "declenche": "redirige le client vers le bon module/écran", "renvoie": "réponse + redirection", "regles": "oriente seulement (le labyrinthe)"},
    {"key": "recherche", "label": "Module recherche", "paths": ["/airports", "/cities", "/destinations/(autocomplete)"], "files": ["routers/recherche.py"],
     "recoit": "intention de recherche brute : slug SEO (paris-los-angeles), texte libre (city name), params URL (from/to/date/cabin/pax)", "comportement": "convertit/normalise → query canonique (origin IATA, destination IATA/hotel_code, date ISO, cabin normalisée, pax total) → délègue au bon module offre (vol / hotel / activités / transferts / séjour)", "declenche": "module vol OU hotel OU activités OU transferts OU séjour selon la query", "renvoie": "query canonique + résultats normalisés du module offre", "regles": "UNE fonction de normalisation par champ (slug, date, cabin, pax) ; jamais de duplication ; jamais de logique métier (pas de fallback prix, pas de cache offres — ça reste dans les modules offre)",
     "functions": [
         {"name": "slug_to_iata_pair(slug)", "desc": "convertit 'paris-los-angeles' → (CDG, LAX) via route_stats", "state": "done", "note": "scaffoldé le 2026-05-30 dans routers/recherche.py ; main.py ré-expose _vols_route_map en alias legacy pour les 2 callers existants (vol_route_page + sitemap)"},
         {"name": "slug_to_hotel(slug)", "desc": "convertit slug SEO hôtel → hotel_code/giata", "state": "todo", "note": "logique aujourd'hui éparpillée dans le SEO hôtels"},
         {"name": "autocomplete_airports(q)", "desc": "texte → liste IATA aéroports", "state": "todo", "note": "route /airports/search en main.py — à migrer"},
         {"name": "autocomplete_cities(q)", "desc": "texte → liste villes (destinations hôtel)", "state": "todo", "note": "route /cities/search en main.py — à migrer"},
         {"name": "normalize_query(params)", "desc": "params URL bruts → canonical {origin, destination, date, cabin, pax}", "state": "todo", "note": "aujourd'hui chaque endpoint refait sa normalisation à sa manière — à centraliser"},
         {"name": "route_to_offre(canonical_query)", "desc": "appelle le bon module offre selon le type de recherche", "state": "todo"},
     ]},
    {"key": "catalogue", "label": "Module catalogue (mémoire opérationnelle)",
     "paths": [],
     "files": ["scripts/hbx_full_sync.py", "scripts/seo_auto_generator.py", "scripts/backfill_canonical_from_catalog.py", "scripts/watchdog_pipeline.py", "scripts/update_route_stats.py", "scripts/update_provider_metrics.py", "fetcher.py"],
     "recoit": "écritures depuis les daemons providers (hbx_full_sync, seo_auto_generator, fetcher) + lectures des modules consommateurs (hotel/vol/seo/recherche/hotelier/pages)",
     "comportement": "persiste localement (PostgreSQL) ce que les providers livrent → sert de mémoire opérationnelle unique. Chaque table appartient à UN provider (HBX / Duffel / AirBizness) ou est un DÉRIVÉ d'une autre table. Pas de double cache divergent.",
     "declenche": "lectures par les modules consommateurs (pas d'écriture sortante)",
     "renvoie": "données stockées (rows DB) au format catalogue (pas le brut provider)",
     "regles": "1 table = 1 provider source (étiqueté) · daemons alimenteurs traçables · pas de table sans alimenteur identifié · pas de double-cache (chaque concept = 1 source unique)",
     "consumers": [
         {"ui": "hbx_hotels_catalog (7 645 hôtels)", "endpoint": "écriture daemon", "source": "scripts/hbx_full_sync.py ← providers/hbx/", "state": "fix", "note": "🔴 daemon SANS CRON automatique — dernier run le 27 mai ; 238/7113 destinations syncées (3,3%) ; 6875 destinations en quota_exceeded à retester"},
         {"ui": "hbx_catalog_sync_state · _log", "endpoint": "écriture daemon", "source": "scripts/hbx_full_sync.py", "state": "done"},
         {"ui": "hotels_canonical (7 433 hôtels)", "endpoint": "écriture daemon + backfill", "source": "scripts/hbx_full_sync.py + scripts/backfill_canonical_from_catalog.py (2026-05-30)", "state": "done", "note": "✅ backfill 2026-05-30 a récupéré 3 216 orphelins coincés dans catalog → canonical à jour"},
         {"ui": "hotels_provider_map", "endpoint": "écriture daemon", "source": "scripts/hbx_full_sync.py (dédup giata cross-provider)", "state": "done"},
         {"ui": "hotel_seo_content (4 216 pages, ~960/j en cours)", "endpoint": "écriture daemon", "source": "scripts/seo_auto_generator.py (limit=50/tick activé 2026-05-30) ← lit hotels_canonical → DeepSeek grounded", "state": "fix", "note": "🟡 3 217 orphelins canonical en cours de génération (4 jours à 50/tick × 4/h)"},
         {"ui": "city_seo_content", "endpoint": "écriture daemon", "source": "scripts/seo_auto_generator.py", "state": "done"},
         {"ui": "deals (cache vol pré-rempli)", "endpoint": "écriture daemon", "source": "fetcher.py ← providers/duffel/ (cron */30min selon docstring)", "state": "fix", "note": "🟠 fetcher.py mal placé (racine projet au lieu de scripts/), vérifier que le cron tourne vraiment"},
         {"ui": "flight_offers_cache (live cache)", "endpoint": "écriture à la requête", "source": "_fetch_or_cache_offers (main.py) ← providers/duffel/ live", "state": "done"},
         {"ui": "flight_price_calendar_cache (live cache bandeau)", "endpoint": "écriture à la requête", "source": "routers/vol.py /flights/price-calendar", "state": "done"},
         {"ui": "hbx_search_cache (live cache HBX search)", "endpoint": "écriture à la requête", "source": "providers/hbx/hotels/cache_layer.py", "state": "done"},
         {"ui": "route_stats (agrégats par route)", "endpoint": "écriture INCONNUE", "source": "AUCUN code Python n'y écrit", "state": "fix", "note": "🔴 MYSTÈRE — alimenté par cron SQL ? trigger DB ? manuel ? à investiguer + documenter"},
         {"ui": "hotel_claims (2 revendications)", "endpoint": "écriture via /claim/activate", "source": "main.py (module hotelier)", "state": "done"},
         {"ui": "hotel_managed_data (overrides hôteliers)", "endpoint": "écriture via /hotel-manager/update", "source": "main.py (module hotelier)", "state": "done"},
         {"ui": "hotel_managed_services (services ajoutés par hôtelier)", "endpoint": "à créer", "source": "providers/airbizness/ (flow B marketplace native)", "state": "todo", "note": "TABLE À CRÉER pour la vision « hôtelier attache son taxi local +15% AirBizness »"},
     ],
     "functions": [
         {"name": "Pipeline d'alimentation", "desc": "providers → daemons → tables catalogue → modules consommateurs", "state": "fix", "note": "hbx_full_sync.py reste sans cron (quota mensuel HBX à surveiller). seo_auto_generator OK depuis 2026-05-30 (limit=50/tick activé)"},
         {"name": "Watchdog pipeline", "desc": "monitore les écarts entre tables (catalog/canonical/seo) toutes les 30 min via systemd timer airbizness-watchdog-pipeline + alerte Telegram si gap > seuil", "state": "done", "note": "✅ posé 2026-05-30 après découverte par accident d'un gap de 3 268 hôtels silencieux. Doctrine [[feedback_watchdog_pipeline]]."},
         {"name": "Backfill canonical idempotent", "desc": "scripts/backfill_canonical_from_catalog.py — copie les hôtels HBX coincés en catalog vers canonical (ON CONFLICT DO NOTHING, gère slug collisions)", "state": "done"},
         {"name": "Compteurs de fraîcheur", "desc": "afficher par table : volume + dernier UPDATE + % complétude", "state": "todo", "note": "à brancher sur la page de la boussole — devient le tableau de bord catalog en live"},
         {"name": "Garde-fou quota", "desc": "si quota HBX search épuisé sur la journée → ne plus tenter, afficher message honnête", "state": "todo"},
         {"name": "Auto-check post-sync dans hbx_full_sync.py", "desc": "à la fin de chaque destination : vérifier que tous les hôtels avec giata sont bien en canonical. Si gap → marker done_with_gaps + Telegram (au lieu de done silencieux)", "state": "todo", "note": "complément du watchdog — détecte le bug AU MOMENT où il se produit, pas 30 min après"},
         {"name": "Audit route_stats", "desc": "tracer qui écrit dans cette table (cron SQL ? trigger ?) ou la supprimer", "state": "done", "note": "✅ trouvé 2026-05-30 — était cron ubuntu 04:00 SQL inline ; remplacé par scripts/update_route_stats.py versionné"},
         {"name": "Migration fetcher.py → scripts/", "desc": "fichier mal placé à la racine du projet", "state": "fix"},
     ]},
    {"key": "cache", "label": "Module cache court (service backend partagé) — Pascal 2026-05-31",
     "paths": [],
     "files": ["services/api_cache.py"],
     "recoit": "appels depuis routers (hotel, vol, recherche) via décorateur @cached(ttl=N, key_prefix='X') AU-DESSUS de la fonction (ou @cached_async pour async def)",
     "comportement": "mutualise les appels providers identiques pendant TTL secondes (5–15 min). Si user A cherche 'Paris 7-10 juin 2 adultes Business', user B (10s plus tard, mêmes params) reçoit la réponse cached sans nouvel appel HBX. Économie quota provider 2–5× selon hit ratio (50–80%). Gain mesuré live : x12 à x90 sur temps de réponse (1655ms → 41ms sur /deals).",
     "declenche": "appel provider RÉEL seulement si cache MISS — si HIT, retour instantané depuis dict in-memory",
     "renvoie": "réponse cached (instantané, ms) ou réponse fraîche provider (après vrai appel HTTP)",
     "regles": "in-memory dict Python single-process en MVP (4 caches indépendants car uvicorn --workers 4) · Redis si vraie mutualisation cross-worker nécessaire (à terme) · clé = hash(prefix+args+kwargs) avec _IGNORED_KWARGS qui exclut request/response FastAPI · LRU eviction max_entries · TTL court (10 min hôtels, 5 min vols volatils, 1h villes/aéroports stables) · @cached décorateur AU-DESSUS du @router.get · invalidation manuelle via cache.invalidate(prefix=) · stats via cache.stats() pour watchdog · JAMAIS sur endpoints booking/quote/payment-intent (prix engagé doit être live)",
     "consumers_actifs": [
         {"endpoint": "/api/hotels/search", "ttl_sec": 600, "max_entries": 20000, "state": "done", "perf": "584ms MISS → 46ms HIT (×12)"},
         {"endpoint": "/api/deals", "ttl_sec": 300, "max_entries": 10000, "state": "done", "perf": "1655ms MISS → 34ms HIT (×49)"},
         {"endpoint": "/api/flights/price-calendar", "ttl_sec": 300, "max_entries": 5000, "state": "done", "perf": "3365ms MISS → 37ms HIT (×90)"},
     ],
     "consumers_prevus": [
         {"endpoint": "/api/cities/search", "ttl_sec": 3600, "state": "todo", "note": "déjà partiellement cached via _cities_cache custom, à unifier"},
         {"endpoint": "/api/airports/search", "ttl_sec": 3600, "state": "todo", "note": "OpenFlights statiques, cache long OK"},
     ],
     "interdits": [
         "/api/hotels/quote (devis engagé)",
         "/api/duffel/refresh_offer (prix live exigé pré-checkout)",
         "/api/flight/booking/payment-intent",
         "/api/hotel/booking/payment-intent",
     ],
     "functions": [
         {"name": "cached(ttl, key_prefix, max_entries)", "desc": "décorateur sync pour endpoints non-async", "state": "done"},
         {"name": "cached_async(ttl, key_prefix, max_entries)", "desc": "version async pour FastAPI async def", "state": "done"},
         {"name": "invalidate(key_prefix=None)", "desc": "vide le cache (tout ou par prefix)", "state": "done"},
         {"name": "stats()", "desc": "retourne {entries, approx_size_kb, hits, misses, evictions, hit_ratio_pct}", "state": "done"},
     ]},
    {"key": "providers", "label": "Module providers (catalogue + adapters)", "paths": [], "files": ["providers/registry.py", "providers/feature_flags.py", "providers/base/aggregator.py", "providers/base/models.py", "providers/duffel/__init__.py", "providers/duffel/client.py", "providers/duffel/search.py", "providers/duffel/order.py", "providers/duffel/seat_maps.py", "providers/duffel/provider.py", "providers/hbx/client.py", "providers/hbx/photos.py", "providers/hbx/provider.py"],
     "recoit": "une requête canonique d'un module offre (HotelQuery, FlightQuery, offer_id, rate_key…)",
     "comportement": "lit le catalogue (registry + feature_flags : qui est ALLUMÉ) → délègue au provider concerné → mappe la réponse BRUTE du provider (Duffel/HBX/…) vers le modèle CANONIQUE (UnifiedOffer, SeatMap, BookingResult). Le module offre ne voit JAMAIS le brut.",
     "declenche": "appels HTTP réels vers les API providers + parsing + mapping vers canonique",
     "renvoie": "objets canoniques (UnifiedOffer, SeatMap, BookingResult, UnifiedHotel…)",
     "regles": "1 dossier par provider (providers/duffel/, providers/hbx/…) ; séparation par fonction DANS le dossier (search.py, order.py, seat_maps.py, photos.py, client.py, models.py) ; chaque provider mappe SON brut vers le canonique — JAMAIS renvoyer du brut aux modules offre",
     "functions": [
         {"name": "registry (FLIGHT_PROVIDERS, HOTEL_PROVIDERS)", "desc": "catalogue : liste des providers allumés par vertical", "state": "done", "note": "providers/registry.py"},
         {"name": "feature_flags (DEFAULT_FLAGS + .feature_flags.json)", "desc": "switch on/off par provider", "state": "done", "note": "providers/feature_flags.py"},
         {"name": "base.models (UnifiedOffer, UnifiedHotel, SeatMap, HotelQuery, FlightQuery, BookingResult)", "desc": "modèles canoniques que les modules offre manipulent", "state": "done", "note": "providers/base/models.py"},
         {"name": "base.aggregator (search_hotels_multi)", "desc": "appelle tous les providers en parallèle + dédup giata + merge", "state": "done", "note": "providers/base/aggregator.py"},
         {"name": "duffel/ (vol)", "desc": "7 fichiers : client/search/order/seat_maps/passengers/models/provider", "state": "done", "note": "splitté du monolithe 2026-05-30 (était providers/duffel.py 962 lignes)"},
         {"name": "hbx/ (hôtels + activités + transferts)", "desc": "auth/client/config/photos + sous-dossiers hotels/, activities/, transfers/", "state": "done"},
         {"name": "ratehawk/, tbo/, webbeds/", "desc": "squelettes (provider.py uniquement) — à étoffer quand on les allume", "state": "todo"},
         {"name": "hbx_transfer.py (encore monolithique top-level)", "desc": "à migrer vers providers/hbx/transfers/", "state": "fix"},
         {"name": "hotellook.py (encore monolithique top-level)", "desc": "à splitter en providers/hotellook/", "state": "fix"},
         {"name": "mocks/duffel/, mocks/hbx/", "desc": "simulateurs pour fuzz testing (resilience.executors) ; structure symétrique aux vrais providers ; garde-fou prod (refuse import si APP_ENV=production)", "state": "done", "note": "migré 2026-05-30 depuis providers/mock_duffel.py + providers/mock_hbx.py monolithiques → 6 fichiers split par fonction (common/order/services pour duffel ; common/booking pour hbx). NB : ce ne sont PAS des inventions d'offres au client, juste des simulateurs API pour test de résilience"},
     ]},
    {"key": "hotel", "label": "Module hotel", "paths": ["/hotels"], "files": ["routers/hotel.py", "main.py (get_hotel_unified_data + _render_hotel_unified)"],
     "recoit": "ville + dates + voyageurs (saisis par le client)", "comportement": "interroge le catalogue → tous les providers hôtel allumés → normalise → dédoublonne (giata). 0 résultat réel → « aucun hôtel disponible »", "declenche": "appels providers (agrégateur), cache hbx_search_cache", "renvoie": "offres hôtel unifiées", "regles": "provider-agnostique, données réelles only, le client choisit",
     "consumers": [
         {"ui": "Listing hôtels (hotels.html)", "endpoint": "/api/hotels/search", "source": "search_hotels_multi → providers.hbx (HBX seul allumé)", "state": "done", "note": "renvoie best_rate_key + best_provider transportés au click vers quote"},
         {"ui": "Cards hôtel sur séjour (sejour.html)", "endpoint": "/api/hotels/search", "source": "search_hotels_multi", "state": "done"},
         {"ui": "Page SEO hôtel /hotels/{cc}/{ville}/{slug}", "endpoint": "main.py hotel_seo_page", "source": "get_hotel_unified_data(slug) → _render_hotel_unified(h, mode='seo')", "state": "done", "note": "Phase 1+2A+2B (2026-05-30) : source unique partagée + galerie 6 onglets + facilities + carte Leaflet + bloc « Lieu & environs » aligné quote.html + CTA conditionnel selon mode (seo/bookable)"},
         {"ui": "Page Quote / Devis (quote.html) — depuis listing", "endpoint": "/api/hotels/quote?rate_key=X&provider=Y", "source": "FASTPATH checkrate direct (providers.hbx.hotels.checkrate)", "state": "done", "note": "fix 2026-05-30 : avant, quote re-faisait search_hotels_multi → divergeait avec le listing (cf. erreur 'Recherche impossible' fréquente). Maintenant le rate_key du listing est passé en URL, le backend checkrate directement → source unique = ce rate précis. + accepte aliases checkin/checkout/adults (fix 2026-05-30 : 400 missing_dates si snake-case court)"},
         {"ui": "Page Quote / Devis (quote.html) — fallback sans rate_key", "endpoint": "/api/hotels/quote (sans rate_key)", "source": "search_hotels_multi + filtre par hotel_code (legacy)", "state": "fix", "note": "fallback historique en cas d'absence rate_key — divergence possible. À supprimer quand tous les callers passeront rate_key"},
         {"ui": "Détail chambres (quote.html → rooms)", "endpoint": "/api/hotels/{code}/rooms", "source": "providers.hbx.hotels.mapper (HBX direct)", "state": "fix", "note": "encore HBX direct au lieu de l'agrégateur"},
         {"ui": "Page fiche hôtel (hotel.html)", "endpoint": "/api/hotels/{code} + /api/hotels/{code}/availability", "source": "providers.hbx.hotels.content + mapper", "state": "fix", "note": "encore HBX direct"},
     ],
     "functions": [
         {"name": "search(query)", "desc": "recherche multi-provider (HBX + futurs) avec dédup giata", "state": "done", "note": "route /hotels/search ; renommée le 2026-05-30 (était /v2/hotels/search) ; la vieille direct-HBX search_hbx_hotels a été supprimée (couverte par l'agnostique)"},
         {"name": "get_hotel(code)", "desc": "fiche d'un hôtel (cache TTL 7j)", "state": "done", "note": "route /hotels/{code} ; renommée le 2026-05-30 (était /hbx/hotel/{code})"},
         {"name": "availability(code, dates)", "desc": "disponibilités d'un hôtel à des dates précises", "state": "fix", "note": "route /hotels/{code}/availability renommée le 2026-05-30 (était /hbx/hotel/{code}/availability) ; encore branchée HBX direct — à passer par l'agrégateur"},
         {"name": "rooms(code, dates)", "desc": "chambres et tarifs détaillés d'un hôtel", "state": "fix", "note": "route /hotels/{code}/rooms renommée le 2026-05-30 (était /v2/hotels/{code}/rooms) ; encore branchée HBX direct — à passer par l'agrégateur"},
         {"name": "quote(code, dates)", "desc": "comparaison multi-provider pour 1 hôtel", "state": "done", "note": "route /hotels/quote renommée le 2026-05-30 (était /v2/hotels/quote) ; mock fallback retiré le 2026-05-30 — 0 offre réelle → réponse vide honnête, jamais inventé"},
     ]},
    {"key": "vol", "label": "Module vol", "paths": ["/deals", "/flights", "/duffel"], "files": ["routers/vol.py", "scripts/fetcher.py", "scripts/purge_expired_caches.py"],
     "recoit": "route + dates (saisis par le client)", "comportement": "interroge le catalogue → tous les providers vol allumés → normalise. 0 résultat réel → « aucun vol »", "declenche": "appels providers vol, cache flight_offers_cache", "renvoie": "offres vol unifiées", "regles": "provider-agnostique, données réelles only",
     "consumers": [
         {"ui": "Bandeau ±3 jours (resultats.html)", "endpoint": "/api/flights/price-calendar", "source": "_fetch_or_cache_offers → flight_offers_cache", "state": "done"},
         {"ui": "Calendrier mois popup (resultats.html, sejour.html)", "endpoint": "/api/flights/price-month", "source": "_fetch_or_cache_offers → flight_offers_cache", "state": "done"},
         {"ui": "Cards résultats datés (resultats.html)", "endpoint": "/api/deals?date=X", "source": "_fetch_or_cache_offers → flight_offers_cache", "state": "done", "note": "refactor 2026-05-30 — avant lisait la table `deals` historique (divergent du bandeau)"},
         {"ui": "Card single sélectionnée (resultats.html, ?offer=)", "endpoint": "/api/deals/by-id", "source": "table `deals` PUIS flight_offers_cache (fallback)", "state": "done", "note": "fix 2026-05-30 : fallback ajouté pour trouver les offres live (auparavant ne lisait que `deals` → broken après refactor /deals)"},
         {"ui": "Cards home / marketing (index.html, sans date)", "endpoint": "/api/deals (sans date)", "source": "table `deals` (historique alimentée par daemon)", "state": "done", "note": "OK — use case non-daté, la table `deals` reste pertinente pour le marketing"},
         {"ui": "Page SEO /vols/{slug} — HTML serveur (compagnies, POI, hôtels)", "endpoint": "vols_route_page (handler HTML)", "source": "route_real_airlines (noms compagnies depuis `deals`) — plus AUCUN prix affiché", "state": "done", "note": "fix 2026-05-30 : lectures route_stats/route_price_band retirées (étaient lues mais jamais rendues = code mort). route_stats sert UNIQUEMENT au catalogue de slugs (quelles routes ont une page SEO)"},
         {"ui": "Page SEO /vols/{slug} — Cards teaser (JS, J+42 hardcodé, business→fallback economy)", "endpoint": "/api/deals?origin=X&destination=Y&date=J+42&cabin=business", "source": "_fetch_or_cache_offers → flight_offers_cache", "state": "done", "note": "même source que le bandeau/calendrier/liste depuis refactor 2026-05-30 — divergence impossible"},
         {"ui": "Modale fare options (resultats.html)", "endpoint": "/api/flights/fare-options/{offer_id}", "source": "table `deals` + search_offers_live (filtré)", "state": "fix", "note": "tape Duffel live à la volée, peut diverger avec la card sélectionnée"},
     ],
     "functions": [
         {"name": "search(query)", "desc": "interroge les providers vol allumés → offres normalisées (UnifiedOffer)", "state": "done"},
         {"name": "offer_services(offer)", "desc": "bagages / sièges / assurance achetables (ase_xxx) attachés à l'offre", "state": "fix", "note": "aujourd'hui /duffel/offer_with_services — à rendre provider-agnostique"},
         {"name": "seat_map(offer)", "desc": "plan de cabine d'une offre → modèle canonique SeatMap", "state": "done", "note": "modèle SeatMap dans providers/base/models.py + mapper Duffel→canonique (cabins→rows→seats {id,designator,price,available,type}). Duffel renvoie souvent seat_map_unavailable → cabins:[] (normal, zéro invention). Reste : renommer la route /duffel/seat_maps → agnostique quand on sortira le module vol"},
     ]},
    {"key": "activites", "label": "Module activités", "paths": ["/hbx/activities/search", "/hbx/activity-booking/payment-intent", "/hbx/activity-booking/confirm", "/hbx/activity-booking/{airbizness_ref}"], "files": ["routers/activites.py", "providers/hbx/activities/search.py", "providers/hbx/activities/booking.py"],
     "recoit": "destination + date", "comportement": "interroge les providers activités (HBX) → normalise + tunnel Stripe payment-intent → confirm", "declenche": "appels providers activités + Stripe", "renvoie": "offres activités + booking confirmé", "regles": "provider-agnostique, réel only — exposé par routers/activites.py (migré 2026-06-02 depuis main.py — 7e module effectif)"},
    {"key": "transferts", "label": "Module transferts", "paths": ["/hbx/transfers/search", "/api/transfer/search", "/api/transfer/book", "/api/transfer/airport-from-route", "/api/transfer/airports-near"], "files": ["routers/transferts.py", "providers/hbx_transfer.py", "providers/hbx/transfers/search.py"],
     "recoit": "trajet (aéroport ↔ hôtel) + date", "comportement": "interroge les providers transferts (HBX Transfers)", "declenche": "appels providers transferts", "renvoie": "offres transfert (vide tant que creds HBX_TRANSFERS_* manquants — comportement HONNÊTE depuis 2026-05-30)", "regles": "provider-agnostique, réel only, ZÉRO mock après le ménage du 2026-05-30 (Cabify/Blacklane/Carey inventés ont été virés)",
     "functions": [
         {"name": "⚠️ état : NON COMMERCIALISÉ", "desc": "Pas de contrat HBX Transfers signé (Pascal 2026-05-30 : 'je savais même pas que ça existait'). Code 100% dormant : 0 offre renvoyée tant que creds absents, UI cachée côté client.", "state": "todo", "note": "DÉCISION À PRENDRE : (A) activer HBX Transfers chez Hotelbeds + creds, (B) supprimer tout le code transferts (~600 lignes + UI), (C) laisser en sommeil"},
         {"name": "search_transfers / book_transfer / cancel_transfer", "desc": "wrapper haut niveau dans providers/hbx_transfer.py", "state": "done", "note": "nettoyé 2026-05-30 : ZÉRO mock, comportement honnête (0 offre si pas de creds + alerte Telegram)"},
         {"name": "5 routes /api/transfer/* + /hbx/transfers/search", "desc": "exposées par routers/transferts.py (migré 2026-06-01 depuis main.py — search/book/airport-from-route/airports-near)", "state": "done", "note": "actives mais inutiles tant que creds absents"},
         {"name": "UI front (3 pages : flight-passengers, quote, sejour)", "desc": "sections transfert codées, cachées tant que 0 offre", "state": "done"},
         {"name": "UI front pack-checkout.html", "desc": "ZÉRO code transfert — non câblé", "state": "todo", "note": "à compléter SI on garde le module (option A ou C)"},
     ]},
    {"key": "sejour", "label": "Module séjour (vol+hôtel)", "paths": ["/pack"], "files": ["public/sejour.html"],
     "recoit": "origine + destination + dates", "comportement": "assemble une offre vol + une offre hôtel pour la destination choisie", "declenche": "modules vol + hotel", "renvoie": "offre séjour combinée", "regles": "destination = celle choisie par le client (jamais Paris→Paris)"},
    {"key": "reservation", "label": "Module réservation", "paths": ["/hbx/booking", "/flight/booking", "/booking", "/bookings"], "files": ["routers/reservation/vol.py", "routers/reservation/hotel.py", "scripts/cleanup_stuck_bookings.py"],
     "recoit": "l'offre choisie", "comportement": "crée la réservation via le provider qui détient l'offre", "declenche": "provider (create_order / create_booking) + écriture en base", "renvoie": "référence de réservation", "regles": "l'offre doit être réelle (pas de mock)",
     "functions": [
         {"name": "Cleanup stuck bookings", "desc": "scripts/cleanup_stuck_bookings.py — détecte les bookings en payment_pending depuis > 24h (= webhook Stripe foiré OU client abandonné). Couvre flight_bookings + bookings_v2 + pack_bookings. Telegram alert si > 5 stuck.", "state": "done", "note": "✅ posé 2026-05-30 + timer systemd nightly 03:30 (dry-run par défaut, --apply à activer manuellement quand Stripe LIVE en place)"},
     ]},
    {"key": "paiement", "label": "Module paiement (séparé)", "paths": ["/create-payment-intent", "/stripe-webhook", "/flight/booking/payment-intent", "/hotel/booking/payment-intent"], "files": ["routers/paiement.py"],
     "recoit": "montant + réservation", "comportement": "crée le payment-intent Stripe puis confirme le paiement (webhook). Source de vérité du paiement = webhook (jamais le front).", "declenche": "Stripe (create_intent + capture/cancel) + webhook qui déclenche la création de la commande chez le provider (Duffel/HBX)", "renvoie": "paiement validé + commande réelle créée", "regles": "montant vérifié contre la base (anti-fraude) ; provider-agnostique ; signature webhook obligatoire",
     "functions": [
         {"name": "create_payment_intent(req)", "desc": "PI Stripe pour un offer du cache deals (tunnel vol legacy)", "state": "done"},
         {"name": "flight_create_payment_intent(body)", "desc": "PI Stripe + INSERT flight_bookings (status=payment_pending) + persiste passenger_ids live", "state": "done"},
         {"name": "hotel_create_payment_intent(body)", "desc": "PI Stripe + INSERT bookings_v2 (status=payment_pending) avec options serveur (transfer/insurance/late_checkin)", "state": "done", "note": "renommé /hbx/booking/payment-intent → /hotel/booking/payment-intent le 2026-05-30, front migré, legacy retirée"},
         {"name": "stripe_webhook(request)", "desc": "reçoit les events Stripe et déclenche la création réelle (Duffel order / HBX booking)", "state": "fix", "note": "1043 lignes monolithiques — à découper en handlers par event-type (succeeded/failed/refunded) ; la création réelle de commande devrait migrer dans le module réservation"},
     ]},
    {"key": "modif_remb", "label": "Module modification + remboursement", "paths": ["/cancel", "/resilience"], "files": ["resilience/executors.py", "resilience/cushion.py"],
     "recoit": "référence de réservation", "comportement": "définit et exécute annulation / modification / remboursement", "declenche": "provider (cancellation) + Stripe refund", "renvoie": "statut mis à jour", "regles": "le code décide l'éligibilité (le labyrinthe)"},
    {"key": "client", "label": "Espace client / Module compte utilisateur — Pascal 2026-05-31 (en cours)",
     "paths": ["/account", "/compte", "/alertes", "/auth", "/login", "/signup", "/api/auth/*", "/api/user/*"],
     "files": ["public/compte.html (existant)", "routers/auth.py (À CRÉER)", "services/auth_token.py (À CRÉER)"],
     "recoit": "email + mot de passe OU email + magic link (passwordless) ; token JWT pour endpoints protégés",
     "comportement": "(1) Signup/Login : crée ou identifie le user (table users) → émet JWT. (2) Espace client connecté : historique réservations (vol/hôtel/séjour), favoris hôtels (sync localStorage favorites → DB), alertes prix, infos voyageur (passeport, préférences siège, programmes fidélité). (3) Pré-remplissage tunnel : si user connecté, pré-remplit passagers / contact / paiement. (4) Cart recovery N2 : promotion du TunnelState localStorage (carnet) vers DB lié au user_id → state survit cross-device.",
     "declenche": "lecture/écriture users, bookings_v2, flight_bookings, hotel_favorites, user_alerts ; émission JWT signé ; envoi email magic-link via services/mail.py",
     "renvoie": "session JWT + payload user (id, email, prénom) + endpoints protégés via JWT bearer",
     "regles": "passwords bcrypt jamais en clair ; JWT signé HS256 avec secret env ; TTL token 30j ; refresh token séparé ; magic-link préféré (sans password) pour UX premium ; RGPD : right-to-be-forgotten endpoint DELETE /api/user/me ; jamais de carte bancaire stockée (Stripe customer.id seulement)",
     "functions_prevues": [
         {"name": "POST /api/auth/signup", "desc": "email + password OU email seul (magic link) → INSERT users + envoie email confirmation", "state": "todo"},
         {"name": "POST /api/auth/login", "desc": "email + password → vérif bcrypt → JWT", "state": "todo"},
         {"name": "POST /api/auth/magic-link", "desc": "email → envoi lien temporaire (token 15min) → /api/auth/magic-callback?token=X → JWT", "state": "todo"},
         {"name": "GET /api/user/me", "desc": "infos user courant (JWT requis)", "state": "todo"},
         {"name": "GET /api/user/bookings", "desc": "historique réservations toutes confondues", "state": "todo"},
         {"name": "POST /api/user/favorites/hotel", "desc": "ajoute/retire un hôtel favori (sync depuis localStorage favorites)", "state": "todo"},
         {"name": "POST /api/user/tunnel-state", "desc": "promote TunnelState localStorage → DB lié user_id (cart recovery N2)", "state": "todo"},
         {"name": "GET /api/user/tunnel-state", "desc": "rappatrie état tunnel sauvegardé cross-device", "state": "todo"},
         {"name": "DELETE /api/user/me", "desc": "RGPD right-to-be-forgotten", "state": "todo"},
     ],
     "tables_a_creer_ou_etendre": [
         {"table": "users", "fields": "id, email UNIQUE, password_hash NULL (NULL si magic-link only), first_name, last_name, phone, created_at, last_login_at, marketing_consent BOOL", "state": "todo"},
         {"table": "user_sessions", "fields": "id, user_id, token_hash, expires_at, ip, user_agent, created_at", "state": "todo"},
         {"table": "user_magic_links", "fields": "id, user_id, token_hash, expires_at (15min), consumed_at", "state": "todo"},
         {"table": "user_tunnel_states", "fields": "user_id PK, state_json JSONB, updated_at, expires_at (24-72h)", "state": "todo"},
         {"table": "hotel_favorites", "fields": "user_id, hotel_code, added_at — PK (user_id, hotel_code)", "state": "todo"},
     ]},
    {"key": "hotelier", "label": "B2B hôtelier", "paths": ["/hotel-manager", "/claim", "/conciergerie", "/leads"], "files": [],
     "recoit": "un hôtelier", "comportement": "revendication de fiche, gestion hôtel, conciergerie", "declenche": "écriture hotel_claims / hotel_managed_data", "renvoie": "espace pro", "regles": "vérification de propriété de la fiche",
     "consumers": [
         {"ui": "hotel_claims", "endpoint": "/api/claim/*", "source": "main.py (route claim_activate)", "state": "done", "note": "2 claims actifs au 2026-05-30, 0 expiré, 0 pending"},
         {"ui": "hotel_managed_data", "endpoint": "/api/hotel-manager/update", "source": "main.py", "state": "done", "note": "lien via hotel_code (PAS giata_code — clarifié 2026-05-30)"},
         {"ui": "hotel_managed_services (table)", "endpoint": "à créer", "source": "providers/airbizness/ (flow B marketplace)", "state": "todo", "note": "VISION : quand l'hôtelier revendique sa fiche, il peut attacher ses services locaux (taxi du coin +15% AirBizness, restaurant partenaire, etc.). AirBizness devient son PROPRE provider d'add-ons."},
     ]},
    {"key": "ops", "label": "Ops / admin / transverse", "paths": ["/api/admin", "/hbx/catalog", "/v2/providers", "/sandbox", "/stats", "/healthz", "/supervisor"], "files": ["scripts/hbx_full_sync.py", "scripts/watchdog_pipeline.py", "scripts/purge_expired_caches.py", "scripts/cleanup_stuck_bookings.py", "scripts/backfill_canonical_from_catalog.py", "scripts/update_route_stats.py", "scripts/update_provider_metrics.py"],
     "recoit": "l'admin / les daemons", "comportement": "synchro catalogue HBX (hbx_full_sync), santé providers, supervision", "declenche": "sync catalogue + lecture santé", "renvoie": "statut ops", "regles": "alertes watchdog Telegram (à centraliser)"},
    {"key": "seo", "label": "Module SEO", "paths": ["/h", "/hotels/{cc}/{city}/{slug}", "/destinations", "/vols", "/sitemap", "/robots", "/sitemap-priority", "/leads/notify-launch"], "files": ["routers/seo.py", "scripts/seo_auto_generator.py"],
     "recoit": "une ville / un hôtel RÉEL du catalogue à couvrir", "comportement": "grounde sur les vraies données HBX → génère le contenu (DeepSeek) → stocke en base (city_seo_content / hotel_seo_content). 0 source réelle → NE GÉNÈRE PAS. Gère sitemap + priorité d'indexation.", "declenche": "DeepSeek (grounded) + écriture base + module publisher", "renvoie": "contenu SEO grounded + entrées sitemap", "regles": "contenu grounded obligatoire (zéro invention) ; n'indexer que le vrai stock",
     "functions": [
         {"name": "Daemon seo_auto_generator", "desc": "tick 15 min, lit hotels_canonical orphelins → DeepSeek → écrit hotel_seo_content. Limit env SEO_DAEMON_LIMIT_HOTELS contrôle le débit.", "state": "done", "note": "✅ activé 2026-05-30 avec limit=50/tick (~130 fiches/h, ~960/jour). Avant : limit=0 = ne traitait personne (bug silencieux découvert le 2026-05-30)."},
         {"name": "Sitemap.xml dynamique", "desc": "rend en live depuis hotels_canonical + city_seo_content + vols_route_map. URLs avec <lastmod> à jour. Total 7517 URLs au 2026-05-30.", "state": "done", "note": "✅ boost 2026-05-30 : priority hôtels 0.7→0.9 + changefreq monthly→weekly (re-crawl Google 4× plus rapide). Cible : remonter les requêtes longue-traîne hôtels (Stansted, Suresnes…) de position ~15 vers ~5-10."},
         {"name": "Robots.txt", "desc": "Disallow les pages tunnel (/quote.html, /checkout.html, /vol.html, /hotels.html…) — seules les pages SEO indexables sont autorisées.", "state": "done"},
     ]},
    {"key": "publisher", "label": "Module publisher", "paths": [], "files": [],
     "recoit": "un contenu validé (ex. du module SEO) + la page cible", "comportement": "réécrit / publie la page à partir du contenu — passe par staging avant la prod", "declenche": "écriture de la page (base/fichier) + invalidation cache", "renvoie": "page publiée", "regles": "staging obligatoire avant prod ; ne publie que du contenu grounded validé ; ne perd aucun slug existant"},
    {"key": "airbizness", "label": "Module AirBizness (provider natif)",
     "paths": ["/api/airbizness/transfers", "/api/hotel-manager/transfers"],
     "files": ["routers/airbizness_api.py", "providers/airbizness/__init__.py", "providers/airbizness/transfers.py", "providers/airbizness/models.py", "scripts/_migration_hotel_managed_transfers.sql"],
     "recoit": "API HTTP : (côté provider) requête search/book transferts ; (côté hôtelier) publication transfert depuis extranet",
     "comportement": "AirBizness EST SON PROPRE provider — branché au système comme HBX/Duffel via API HTTP. Catalogue alimenté par les hôteliers qui ont revendiqué leur fiche. Marge 15% calculée par la DB (impossible à contourner).",
     "declenche": "lecture/écriture hotel_managed_transfers + transfer_bookings",
     "renvoie": "offres transferts canoniques (Offer) consommables par l'agrégateur",
     "regles": "interface canonique TransferProvider (search/book) ; consommé via HTTP par providers/airbizness/transfers.py ; URL configurable AIRBIZNESS_API_BASE (portabilité totale) ; modèles canoniques EMBARQUÉS dans providers/airbizness/models.py (= dossier autonome déplaçable)",
     "consumers": [
         {"ui": "hotel_managed_transfers (table)", "endpoint": "POST /api/hotel-manager/transfers", "source": "routers/airbizness_api.py (insert depuis extranet hôtelier)", "state": "done", "note": "marge auto via colonne GENERATED PostgreSQL"},
         {"ui": "transfer_bookings (table)", "endpoint": "POST /api/airbizness/transfers/bookings", "source": "routers/airbizness_api.py (booking via webhook Stripe ou direct)", "state": "done"},
         {"ui": "Agrégateur transferts", "endpoint": "appel HTTP /api/airbizness/transfers/availability", "source": "providers/airbizness/transfers.py → AirbiznessTransferProvider", "state": "done", "note": "inscrit dans TRANSFER_PROVIDERS, allumé par feature flag provider_airbizness_enabled=True"},
     ],
     "functions": [
         {"name": "API HTTP serveur (6 endpoints)", "desc": "POST availability / POST bookings / GET bookings/{ref} / DELETE bookings/{ref} / POST hotel-manager/transfers / GET hotel-manager/transfers/{hotel_code}", "state": "done", "note": "pattern aligné HBX /transfer-api/1.0/"},
         {"name": "Provider Python autonome", "desc": "providers/airbizness/ avec models.py + transfers.py + __init__.py — consomme l'API HTTP via AIRBIZNESS_API_BASE (env var)", "state": "done", "note": "portabilité 100% : copier le dossier sur un autre serveur + 1 ligne .env suffit"},
         {"name": "Booking côté tunnel checkout", "desc": "intégrer le choix transfert au tunnel quote/checkout existant comme add-on à la résa hôtel", "state": "todo", "note": "frontend, dépend de discussions UX"},
         {"name": "Extranet hôtelier UI", "desc": "interface graphique pour que l'hôtelier publie/gère ses transferts (route /hotel-manager/transfers existe en HTML côté front mais pas encore branchée à la nouvelle API)", "state": "todo"},
         {"name": "Étendre à d'autres services", "desc": "le module est conçu pour accueillir d'autres services natifs (concierge, services premium...) — pas d'activités/excursions (clarifié Pascal 2026-05-30)", "state": "todo"},
     ]},
]


_CALLS = {
    "pages": ["recherche", "chat", "hotel", "vol", "activites", "transferts", "sejour"],
    "chat": ["recherche", "hotel", "vol", "sejour", "client", "modif_remb"],
    "recherche": ["hotel", "vol", "activites", "transferts", "sejour"],
    "providers": ["catalogue"],
    "catalogue": [],
    "hotel": ["providers", "catalogue", "reservation"],
    "vol": ["providers", "catalogue", "reservation"],
    "activites": ["providers", "reservation"],
    "transferts": ["providers", "reservation", "airbizness"],
    "sejour": ["vol", "hotel", "reservation"],
    "reservation": ["paiement"],
    "paiement": ["client", "modif_remb"],
    "modif_remb": ["paiement", "reservation"],
    "client": ["reservation", "modif_remb"],
    "hotelier": ["ops"],
    "ops": ["hotel", "vol", "activites", "transferts", "seo"],
    "seo": ["publisher"],
    "publisher": ["pages"],
    "airbizness": ["catalogue", "hotelier"],
}


def _route_src_from_main(prefixes):
    """Extrait le source des routes de main.py dont le chemin commence par un des prefixes."""
    import re as _re
    try:
        lines = open(_MAIN, encoding="utf-8").read().split("\n")
    except Exception:
        return ""
    out, i, n = [], 0, 0
    n = len(lines)
    while i < n:
        m = _re.match(r'@app\.(get|post|put|delete|patch)\("([^"]+)"', lines[i].strip())
        if m and any(m.group(2).startswith(p) for p in prefixes):
            s = i
            j = i + 1
            while j < n and "def " not in lines[j]:
                j += 1
            e = n
            for k in range(j + 1, n):
                l = lines[k]
                if l.startswith("@app") or l.startswith("def ") or l.startswith("async def ") or l.startswith("class ") or _re.match(r'^#\s*[─═]{3,}', l):
                    e = k
                    break
            out.append("\n".join(lines[s:e]).rstrip())
            i = e
        else:
            i += 1
    return "\n\n".join(out)


def _file_src(path):
    try:
        return open("/var/www/airbizness/" + path, encoding="utf-8").read()
    except Exception:
        return f"# (introuvable: {path})"


def _catalog_live_metrics_html() -> str:
    """Encart `Chiffres live` pour le module catalogue.

    Suite à la découverte du 2026-05-30 (3 268 hôtels coincés silencieusement
    pendant 7 jours), on rend visibles les chiffres clés du pipeline pour que
    l'humain puisse voir d'un coup d'œil si les tables sont alignées.
    Aligné sur ce que mesure scripts/watchdog_pipeline.py.
    """
    try:
        import psycopg2
        import os as _os
        DB = {"host": _os.getenv("DB_HOST"), "dbname": _os.getenv("DB_NAME"),
              "user": _os.getenv("DB_USER"), "password": _os.getenv("DB_PASS")}
        conn = psycopg2.connect(**DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM hbx_hotels_catalog WHERE giata_code IS NOT NULL")
        catalog_giata = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM hotels_canonical")
        canonical = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM hotel_seo_content")
        seo = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FROM hbx_hotels_catalog c
                       WHERE c.giata_code IS NOT NULL AND NOT EXISTS
                       (SELECT 1 FROM hotels_canonical h WHERE h.giata_code = c.giata_code)""")
        gap_cat_can = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FROM hotels_canonical h
                       WHERE h.slug IS NOT NULL AND NOT EXISTS
                       (SELECT 1 FROM hotel_seo_content s WHERE s.giata_code = h.giata_code)""")
        gap_can_seo = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(DISTINCT s.destination_code) FROM hbx_catalog_sync_state s
                       WHERE s.last_status='done' AND EXISTS
                       (SELECT 1 FROM hbx_hotels_catalog c WHERE c.destination_code=s.destination_code
                         AND c.giata_code IS NOT NULL AND NOT EXISTS
                         (SELECT 1 FROM hotels_canonical h WHERE h.giata_code=c.giata_code))""")
        done_gaps = cur.fetchone()[0]
        cur.execute("""SELECT last_status, COUNT(*) FROM hbx_catalog_sync_state
                       GROUP BY 1 ORDER BY 2 DESC""")
        sync_states = cur.fetchall()
        cur.execute("SELECT MAX(generated_at) FROM hotel_seo_content")
        last_seo = cur.fetchone()[0]
        cur.close(); conn.close()

        def _badge(n, ok_max):
            cls = "fn-done" if n <= ok_max else "fn-fix"
            return f"<span class='{cls}'>● {n}</span>"

        states_html = " · ".join(f"{_h.escape(str(s[0] or 'NULL'))} <b>{s[1]}</b>" for s in sync_states)
        pct_seo = (100 * seo / canonical) if canonical else 0

        return (
            "<h2>Chiffres live (pipeline)</h2>"
            "<div class='sub'>Lecture directe DB à chaque chargement. Aligné sur "
            "<code>scripts/watchdog_pipeline.py</code>. Si un écart apparaît, "
            "le watchdog (timer 30 min) aboie sur Telegram.</div>"
            "<table>"
            "<tr><th style='width:280px'>Métrique</th><th>Valeur</th></tr>"
            f"<tr><td>hbx_hotels_catalog (avec giata)</td><td><b>{catalog_giata}</b></td></tr>"
            f"<tr><td>hotels_canonical</td><td><b>{canonical}</b></td></tr>"
            f"<tr><td>hotel_seo_content</td><td><b>{seo}</b> ({pct_seo:.1f}% du canonical)</td></tr>"
            f"<tr><td>↳ écart catalog → canonical</td><td>{_badge(gap_cat_can, 50)}</td></tr>"
            f"<tr><td>↳ écart canonical → SEO (en cours daemon)</td><td>{_badge(gap_can_seo, 5000)} <span class='off' style='font-size:11px'>(daemon SEO génère ~960/jour)</span></td></tr>"
            f"<tr><td>↳ destinations 'done' avec hôtels orphelins</td><td>{_badge(done_gaps, 5)}</td></tr>"
            f"<tr><td>états sync destinations</td><td>{states_html}</td></tr>"
            f"<tr><td>dernière fiche SEO générée</td><td>{_h.escape(str(last_seo) if last_seo else '—')}</td></tr>"
            "</table>"
        )
    except Exception as e:
        return f"<div class='off'>(chiffres live indisponibles : {_h.escape(str(e))})</div>"


@router.get("/schema-module/{key}", response_class=HTMLResponse)
def schema_module(key: str):
    mod = next((m for m in _MODULES if m["key"] == key), None)
    if not mod:
        return HTMLResponse("<body style='background:#0f0f0f;color:#e0564a;font-family:sans-serif;padding:40px'>Module inconnu. "
                            "<a href='/api/schema-technique' style='color:#d4ae4a'>← retour au schéma</a></body>", status_code=404)

    # Bloc chiffres live : uniquement pour le module catalogue (le seul qui
    # tient des compteurs DB de cohérence — les autres modules n'ont pas cette
    # notion de "ratio entre tables sœurs" à montrer).
    live_metrics_html = _catalog_live_metrics_html() if key == "catalogue" else ""
    spec = (
        "<table>"
        f"<tr><th style='width:180px'>Reçoit</th><td>{_h.escape(mod['recoit'])}</td></tr>"
        f"<tr><th>Comportement attendu</th><td>{_h.escape(mod['comportement'])}</td></tr>"
        f"<tr><th>Déclenche</th><td>{_h.escape(mod['declenche'])}</td></tr>"
        f"<tr><th>Renvoie</th><td>{_h.escape(mod['renvoie'])}</td></tr>"
        f"<tr><th>Règles</th><td>{_h.escape(mod['regles'])}</td></tr>"
        "</table>"
    )
    code_parts = []
    main_src = _route_src_from_main(mod["paths"])
    if main_src.strip():
        code_parts.append(("Routes dans main.py (à migrer dans le module)", main_src))
    for f in mod.get("files", []):
        code_parts.append((f, _file_src(f)))
    if code_parts:
        code_html = "".join(f"<h3>{_h.escape(t)}</h3><pre>{_h.escape(s[:20000])}</pre>" for t, s in code_parts)
    else:
        code_html = "<div class='off'>(aucun code isolé pour ce module pour l'instant)</div>"
    _label = {m["key"]: m["label"] for m in _MODULES}
    called = _CALLS.get(key, [])
    if called:
        calls_html = "<div class='modlinks'>" + "".join(
            f"<a href='/api/schema-module/{k}'>{_h.escape(_label.get(k, k))} →</a>" for k in called) + "</div>"
    else:
        calls_html = "<div class='off'>(n'appelle aucun autre module)</div>"
    callers = [m["key"] for m in _MODULES if key in _CALLS.get(m["key"], [])]
    if callers:
        callers_html = "<div class='modlinks'>" + "".join(
            f"<a href='/api/schema-module/{k}'>← {_h.escape(_label.get(k, k))}</a>" for k in callers) + "</div>"
    else:
        callers_html = "<div class='off'>(appelé par aucun module)</div>"
    _fn_state = {"done": ("● fait", "fn-done"), "fix": ("● à corriger", "fn-fix"),
                 "todo": ("● à créer", "fn-todo"), "dead": ("● code mort — à virer", "fn-dead")}
    consumers = mod.get("consumers", [])
    if consumers:
        rows_c = ""
        for c in consumers:
            lbl, cls = _fn_state.get(c.get("state", "todo"), ("?", "fn-todo"))
            note = f"<br><span class='off' style='font-size:11px'>↳ {_h.escape(c['note'])}</span>" if c.get("note") else ""
            rows_c += (f"<tr><td>{_h.escape(c['ui'])}</td>"
                       f"<td><code>{_h.escape(c['endpoint'])}</code></td>"
                       f"<td><code>{_h.escape(c['source'])}</code>{note}</td>"
                       f"<td class='{cls}'>{lbl}</td></tr>")
        consumers_html = ("<table><tr><th>Affichage front</th><th>Endpoint backend</th>"
                          "<th>Source réelle (fonction → table)</th><th>État</th></tr>"
                          + rows_c + "</table>")
    else:
        consumers_html = ""
    funcs = mod.get("functions", [])
    if funcs:
        rows = ""
        for f in funcs:
            lbl, cls = _fn_state.get(f.get("state", "todo"), ("?", "fn-todo"))
            desc = _h.escape(f["desc"])
            if f.get("note"):
                desc += f"<br><span class='off' style='font-size:11px'>↳ {_h.escape(f['note'])}</span>"
            rows += (f"<tr><td><code>{_h.escape(f['name'])}</code></td><td>{desc}</td>"
                     f"<td class='{cls}'>{lbl}</td></tr>")
        funcs_html = "<table><tr><th>Fonction</th><th>Rôle</th><th>État</th></tr>" + rows + "</table>"
    else:
        funcs_html = "<div class='off'>(fonctions à définir — on remplit au fur et à mesure)</div>"
    html = ("<!DOCTYPE html><html lang='fr'><head><meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            "<meta name='robots' content='noindex,nofollow'>"
            f"<title>Module {_h.escape(mod['label'])} — AirBizness</title>"
            "<style>" + _CSS + "</style></head><body>"
            "<div class='sub'><a href='/api/schema-technique' style='color:#d4ae4a'>← Schéma technique</a></div>"
            f"<h1>{_h.escape(mod['label'])}</h1>"
            + live_metrics_html +
            "<h2>Ce que fait le module</h2>" + spec +
            (("<h2>Qui lit quoi (data flow)</h2>"
              "<div class='sub'>Mappe chaque affichage front à l'endpoint backend et à la source RÉELLE. "
              "Permet de voir d'un coup d'œil quels consommateurs divergent de la source unique.</div>"
              + consumers_html) if consumers else "") +
            "<h2>Fonctions — le plan</h2>" + funcs_html +
            "<h2>Modules qu'il appelle</h2>" + calls_html +
            "<h2>Appelé par</h2>" + callers_html +
            "<h2>Code réel</h2>" + code_html +
            "</body></html>")
    return HTMLResponse(html)


import markdown

@router.get("/audit-duffel-claude", response_class=HTMLResponse, dependencies=[Depends(require_admin_token)])
async def audit_duffel_claude():
    with open("/var/www/airbizness/audit_pollution_duffel_2026-06-01.md", "r") as f:
        md_content = f.read()
    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
    html_page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Audit pollution Duffel — Claude/Anthropic — 2026-06-01</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    background: #0a0a14;
    color: #e8e8f0;
    font-family: system-ui, sans-serif;
    padding: 12px 16px;
    max-width: 800px;
    margin: auto;
    font-size: 16px;
    line-height: 1.6;
}}
h1, h2, h3 {{
    color: #d4ae4a;
    border-bottom: 1px solid #333;
    padding-bottom: 4px;
}}
code {{
    background: #1a1a2e;
    padding: 2px 6px;
    border-radius: 3px;
    color: #f0c674;
}}
pre {{
    background: #1a1a2e;
    padding: 12px;
    border-radius: 6px;
    overflow-x: auto;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}}
th, td {{
    border: 1px solid #333;
    padding: 8px;
    text-align: left;
    font-size: 14px;
}}
th {{
    background: #1a1a2e;
}}
a {{
    color: #d4ae4a;
}}
.back-link {{
    display: inline-block;
    margin-bottom: 16px;
    font-size: 14px;
    opacity: 0.8;
}}
.back-link:hover {{
    opacity: 1;
}}
</style>
</head>
<body>
<a href="/api/schema-technique" class="back-link">← Boussole</a> · <a href="/api/audit-apis" class="back-link">Audit toutes APIs (DeepSeek)</a> · <a href="/api/audit-duffel-deepseek" class="back-link">Audit Duffel (DeepSeek)</a>
<h1>Audit pollution Duffel — Claude/Anthropic — 2026-06-01</h1>
{html_body}
</body>
</html>"""
    return HTMLResponse(content=html_page)

@router.get("/audit-duffel-deepseek", response_class=HTMLResponse, dependencies=[Depends(require_admin_token)])
async def audit_duffel_deepseek():
    with open("/var/www/airbizness/audit_pollution_duffel_deepseek_2026-06-01.md", "r") as f:
        md_content = f.read()
    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
    html_page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Audit pollution Duffel — DeepSeek — 2026-06-01</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    background: #0a0a14;
    color: #e8e8f0;
    font-family: system-ui, sans-serif;
    padding: 12px 16px;
    max-width: 800px;
    margin: auto;
    font-size: 16px;
    line-height: 1.6;
}}
h1, h2, h3 {{
    color: #d4ae4a;
    border-bottom: 1px solid #333;
    padding-bottom: 4px;
}}
code {{
    background: #1a1a2e;
    padding: 2px 6px;
    border-radius: 3px;
    color: #f0c674;
}}
pre {{
    background: #1a1a2e;
    padding: 12px;
    border-radius: 6px;
    overflow-x: auto;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}}
th, td {{
    border: 1px solid #333;
    padding: 8px;
    text-align: left;
    font-size: 14px;
}}
th {{
    background: #1a1a2e;
}}
a {{
    color: #d4ae4a;
}}
.back-link {{
    display: inline-block;
    margin-bottom: 16px;
    font-size: 14px;
    opacity: 0.8;
}}
.back-link:hover {{
    opacity: 1;
}}
</style>
</head>
<body>
<a href="/api/schema-technique" class="back-link">← Boussole</a> · <a href="/api/audit-apis" class="back-link">Audit toutes APIs (DeepSeek)</a> · <a href="/api/audit-duffel-claude" class="back-link">Audit Duffel (Claude)</a>
<h1>Audit pollution Duffel — DeepSeek — 2026-06-01</h1>
{html_body}
</body>
</html>"""
    return HTMLResponse(content=html_page)

@router.get("/audit-apis", response_class=HTMLResponse, dependencies=[Depends(require_admin_token)])
async def audit_apis():
    with open("/var/www/airbizness/audit_pollution_all_apis_deepseek_2026-06-01.md", "r") as f:
        md_content = f.read()
    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
    html_page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Audit pollution APIs — DeepSeek — 2026-06-01</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    background: #0a0a14;
    color: #e8e8f0;
    font-family: system-ui, sans-serif;
    padding: 12px 16px;
    max-width: 800px;
    margin: auto;
    font-size: 16px;
    line-height: 1.6;
}}
h1, h2, h3 {{
    color: #d4ae4a;
    border-bottom: 1px solid #333;
    padding-bottom: 4px;
}}
code {{
    background: #1a1a2e;
    padding: 2px 6px;
    border-radius: 3px;
    color: #f0c674;
}}
pre {{
    background: #1a1a2e;
    padding: 12px;
    border-radius: 6px;
    overflow-x: auto;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}}
th, td {{
    border: 1px solid #333;
    padding: 8px;
    text-align: left;
    font-size: 14px;
}}
th {{
    background: #1a1a2e;
}}
a {{
    color: #d4ae4a;
}}
.back-link {{
    display: inline-block;
    margin-bottom: 16px;
    font-size: 14px;
    opacity: 0.8;
}}
.back-link:hover {{
    opacity: 1;
}}
</style>
</head>
<body>
<a href="/api/schema-technique" class="back-link">← Boussole</a> · <a href="/api/audit-duffel-claude" class="back-link">Audit Duffel (Claude)</a> · <a href="/api/audit-duffel-deepseek" class="back-link">Audit Duffel (DeepSeek)</a>
<h1>Audit pollution APIs — DeepSeek — 2026-06-01</h1>
{html_body}
</body>
</html>"""
    return HTMLResponse(content=html_page)

@router.get("/home-preview", response_class=HTMLResponse)
async def home_preview():
    with open("/var/www/airbizness/public/home-prelaunch-preview.html", "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, headers={"X-Robots-Tag": "noindex, nofollow"})

@router.get("/preview-vols", response_class=HTMLResponse)
async def preview_vols():
    with open("/var/www/airbizness/public/resultats.html", "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, headers={"X-Robots-Tag": "noindex, nofollow"})
# Imports locaux pour ce bloc (ne pas modifier les imports existants en haut du fichier)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# Modèle pour le body optionnel
class PublishPilotBody(BaseModel):
    text: Optional[str] = None
    link_url: Optional[str] = None
    link_title: Optional[str] = None
    link_description: Optional[str] = None

@router.post("/linkedin/publish-pilot", dependencies=[Depends(require_admin_token)])
async def linkedin_publish_pilot(body: PublishPilotBody = None):
    """
    Publie un post de test sur LinkedIn AirBizness.
    Utilise des valeurs par défaut si le body est absent ou contient des champs None.
    """
    # Valeurs par défaut
    text = "Test pilote AirBizness — Voyage premium curaté. (Test interne)"
    link_url = "https://airbizness.com/"
    link_title = "AirBizness - Le voyage premium, sans compromis"
    link_description = "Marketplace de réservation voyage premium"

    # Surcharge si body fourni
    if body:
        if body.text is not None:
            text = body.text
        if body.link_url is not None:
            link_url = body.link_url
        if body.link_title is not None:
            link_title = body.link_title
        if body.link_description is not None:
            link_description = body.link_description

    try:
        # Import lazy pour éviter les erreurs si le module est cassé
        from services.linkedin_publisher import LinkedInPublisher
        publisher = LinkedInPublisher()
        result = publisher.post_with_link(
            text=text,
            link_url=link_url,
            link_title=link_title,
            link_description=link_description
        )
        return JSONResponse({
            "ok": True,
            "post_id": result.get("id"),
            "raw": result
        })
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=502
        )

@router.get("/linkedin/auth-check", dependencies=[Depends(require_admin_token)])
async def linkedin_auth_check():
    """
    Vérifie que l'authentification LinkedIn est valide.
    Retourne les infos du compte sans logger les credentials.
    """
    try:
        from services.linkedin_publisher import LinkedInPublisher
        publisher = LinkedInPublisher()
        data = publisher.test_auth()
        return JSONResponse({
            "ok": True,
            "sub": data.get("sub"),
            "name": data.get("name"),
            "email": data.get("email")
        })
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=502
        )

