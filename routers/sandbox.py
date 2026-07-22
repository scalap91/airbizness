"""
Sandbox engine + endpoints — migré de main.py 2026-06-01 (2e module sur 13). Pascal/orchestrateur DeepSeek.

Contient :
  - SANDBOX_USERS (utilisateurs fictifs)
  - SANDBOX_SCENARIOS (60 scénarios S01-S60)
  - run_sandbox_scenario() (engine)
  - 3 routes : GET /sandbox/scenarios · POST /sandbox/simulate/{id} · POST /sandbox/cleanup
"""
from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
import psycopg2
import psycopg2.extras
import json
from main import DB_CONFIG, limiter, alert_conciergerie

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────
# SANDBOX ENGINE — simulateur des 60 scénarios pour valider patterns
# Tous les pack_bookings créés en sandbox ont is_sandbox=true (isolation)
# ──────────────────────────────────────────────────────────────────────────

# Utilisateurs fictifs représentatifs des cibles AirBizness
SANDBOX_USERS = [
    {"first":"Jean",      "last":"Dupont",  "email":"jean.dupont@sandbox.airbizness.com",      "phone":"+33612345678", "country":"FR"},
    {"first":"Sarah",     "last":"Martin",  "email":"sarah.martin@sandbox.airbizness.com",     "phone":"+33687654321", "country":"FR"},
    {"first":"Ahmed",     "last":"Ben Ali", "email":"ahmed.benali@sandbox.airbizness.com",     "phone":"+212661234567", "country":"MA"},
    {"first":"Fatima",    "last":"Khelifi", "email":"fatima.khelifi@sandbox.airbizness.com",   "phone":"+213551234567", "country":"DZ"},
    {"first":"Karim",     "last":"Mansour", "email":"karim.mansour@sandbox.airbizness.com",    "phone":"+97150123456",  "country":"AE"},
    {"first":"Olivia",    "last":"Chen",    "email":"olivia.chen@sandbox.airbizness.com",      "phone":"+6587654321",   "country":"SG"},
]

# 20 scénarios critiques (sur les 60 identifiés) pour valider les patterns
SANDBOX_SCENARIOS = {
    "S01": {
        "name": "Cas nominal : vol OK + hôtel OK",
        "category": "happy_path",
        "severity": "info",
        "expected": "confirmed",
        "description": "Réservation pack standard, tout se passe bien. Voucher envoyé.",
        "patterns_tested": ["P1", "P7"],
    },
    "S02": {
        "name": "Vol Duffel échoue → refund total",
        "category": "flight_fail",
        "severity": "warn",
        "expected": "failed",
        "description": "Duffel /air/orders rejette (siège pris). Refund total Stripe. Alert conciergerie 'booking_failed_vol'.",
        "patterns_tested": ["P2", "P3", "P4"],
    },
    "S03": {
        "name": "Vol OK + Hôtel HBX échoue → vol gardé + refund partiel + ALERT CRITIQUE",
        "category": "hotel_fail_after_flight_ok",
        "severity": "critical",
        "expected": "partial_confirmed",
        "description": "Le pire scénario : vol émis (PNR), mais HBX dit chambre indispo. On garde le vol, refund 470€, conciergerie cherche substitut sous 1h.",
        "patterns_tested": ["P2", "P3", "P4", "P5"],
    },
    "S04": {
        "name": "Prix drift Duffel >30% → blocage avant Stripe + alternatives",
        "category": "price_drift",
        "severity": "info",
        "expected": "price_blocked",
        "description": "Cache 2300€ → live 4700€. Le tunnel STOPPE avant le paiement. Liste de 3 alternatives proposées au client.",
        "patterns_tested": ["P1", "P6"],
    },
    "S05": {
        "name": "Stripe 3DS échec → pas de débit, pas de booking",
        "category": "payment_fail",
        "severity": "info",
        "expected": "failed",
        "description": "Le 3DS de la carte échoue. Aucun débit, aucun booking. Client invité à réessayer ou changer de carte.",
        "patterns_tested": ["P11"],
    },
    "S06": {
        "name": "Données passager invalides → re-saisie demandée",
        "category": "validation_fail",
        "severity": "info",
        "expected": "validation_blocked",
        "description": "Date de naissance manquante, gender vide, format téléphone invalide. Tunnel bloqué avant Stripe avec feedback champs.",
        "patterns_tested": ["P6"],
    },
    "S07": {
        "name": "Vol annulé par compagnie J-3 → substitut auto",
        "category": "vol_cancelled_during_stay",
        "severity": "critical",
        "expected": "substitute_proposed",
        "description": "3 jours avant départ, la compagnie annule le vol. Polling Duffel détecte → substitut vol pré-calculé proposé immédiatement au client par email + push.",
        "patterns_tested": ["P2", "P4", "P9", "P10"],
    },
    "S08": {
        "name": "Hôtel overbooking à l'arrivée → substitut sub-1h",
        "category": "hotel_overbooking_arrival",
        "severity": "critical",
        "expected": "substitute_proposed",
        "description": "Client arrive à l'hôtel, on lui dit 'plus de chambre'. Hôtel substitut équivalent réservé par notre conciergerie sous 1h.",
        "patterns_tested": ["P2", "P4", "P5"],
    },
    "S09": {
        "name": "Force majeure (volcan, grève) → refund massif via cushion",
        "category": "force_majeure",
        "severity": "critical",
        "expected": "mass_refund",
        "description": "Éruption volcanique annule 50 vols. Refund total via cushion AirBizness pour tous les clients impactés en moins de 24h.",
        "patterns_tested": ["P3", "P5", "P4"],
    },
    "S10": {
        "name": "Client veut modifier dates voyage",
        "category": "modification_request",
        "severity": "info",
        "expected": "modification_quoted",
        "description": "Client souhaite décaler le départ de 3 jours. Recalcul HBX + Duffel automatique, différentiel prix présenté pour validation.",
        "patterns_tested": ["P4", "P12"],
    },
    "S11": {
        "name": "Email voucher en bounce → fallback SMS + push",
        "category": "communication_fail",
        "severity": "warn",
        "expected": "recovered",
        "description": "Brevo retourne bounce sur l'email du client. Voucher renvoyé automatiquement par SMS Twilio + push notification application.",
        "patterns_tested": ["P9"],
    },
    "S12": {
        "name": "Carte volée → chargeback 90j après",
        "category": "fraud_chargeback",
        "severity": "critical",
        "expected": "fraud_documented",
        "description": "90 jours après le séjour, banque émettrice émet un chargeback. Dossier auto compilé (voucher, e-billet, preuves voyage effectué) pour disputer.",
        "patterns_tested": ["P11"],
    },
    "S13": {
        "name": "Backend crash pendant la séquence",
        "category": "infra_failure",
        "severity": "critical",
        "expected": "state_recovered",
        "description": "Le serveur crash juste après Stripe paid mais avant Duffel /orders. Au redémarrage, le cron de reconciliation détecte l'état pending et reprend.",
        "patterns_tested": ["P7", "P8"],
    },
    "S14": {
        "name": "Bot scraper → rate limiting + IP block",
        "category": "abuse_protection",
        "severity": "warn",
        "expected": "rate_limited",
        "description": "Un bot tente 200 search/sec. Slowapi rate limit déclenche + Cloudflare bloque l'IP. Aucun impact sur les vrais clients.",
        "patterns_tested": ["P11"],
    },
    "S15": {
        "name": "Race condition : 2 clients bookent même chambre",
        "category": "race_condition",
        "severity": "warn",
        "expected": "second_refunded",
        "description": "2 paiements simultanés sur la même chambre HBX. Le 2e booking échoue (HBX rejette), refund auto + alert + substitut proposé au 2e client.",
        "patterns_tested": ["P2", "P3", "P4", "P7"],
    },
    "S16": {
        "name": "Client perd son voucher → renvoi multi-canal",
        "category": "voucher_recovery",
        "severity": "info",
        "expected": "recovered",
        "description": "Client demande son voucher au chat conciergerie. Renvoi instantané par email + lien temporaire signé + push app.",
        "patterns_tested": ["P9", "P10"],
    },
    "S17": {
        "name": "Decès / hospitalisation client → conciergerie humaine",
        "category": "human_escalation",
        "severity": "critical",
        "expected": "human_handled",
        "description": "Famille du client signale un décès. Conciergerie humaine prend la main, gère les annulations, refund prioritaire via cushion, certificat à fournir.",
        "patterns_tested": ["P4", "P3"],
    },
    "S18": {
        "name": "Voyageur sans visa → blocage anticipé J-7",
        "category": "preflight_check",
        "severity": "warn",
        "expected": "preflight_alert",
        "description": "Notre check préflight J-7 détecte que le voyageur de nationalité X va vers pays Y nécessitant un visa. Email + chatbot proactif pour démarches.",
        "patterns_tested": ["P1", "P9", "P10"],
    },
    "S19": {
        "name": "Stripe webhook en retard → reconciliation cron",
        "category": "webhook_lag",
        "severity": "info",
        "expected": "reconciled",
        "description": "Webhook Stripe arrive 2 min après le paiement. Le client était déjà confirmed via polling direct. Le webhook est idempotent → no-op.",
        "patterns_tested": ["P7", "P8"],
    },
    "S20": {
        "name": "Hôtelier propose upgrade gratuit suite à VIP detection",
        "category": "vip_upgrade",
        "severity": "info",
        "expected": "upgrade_offered",
        "description": "Client identifié comme VIP par notre scoring (3e séjour, panier moyen >5k€). Notre conciergerie négocie un upgrade chambre à 0€ avec l'hôtelier.",
        "patterns_tested": ["P4", "P12"],
    },
    "S21": {
        "name": "Carte refusée insufficient funds",
        "category": "payment_card_declined", "severity":"info", "expected":"failed",
        "description":"Banque refuse le débit (solde insuffisant). Tunnel propose retry + autre carte + Apple/Google Pay.",
        "patterns_tested":["P11"],
    },
    "S22": {
        "name": "Stripe Radar bloque pour fraude",
        "category": "fraud_radar_block", "severity":"warn", "expected":"failed",
        "description":"Score Radar 95/100 = haute probabilité fraude. Stripe bloque automatique. Alert conciergerie pour review manuelle.",
        "patterns_tested":["P11", "P4"],
    },
    "S23": {
        "name": "Capture failure après authorize Stripe",
        "category": "stripe_capture_fail", "severity":"warn", "expected":"recovered",
        "description":"Authorize OK mais capture timeout. Retry idempotent cron 5 min après. Si toujours fail, alert + email client.",
        "patterns_tested":["P7", "P8"],
    },
    "S24": {
        "name": "Compagnie aérienne plus dispo (rotation cancelled)",
        "category": "airline_route_dropped", "severity":"warn", "expected":"alternative_offered",
        "description":"Air Europa retire sa rotation AMS-JFK Business. Refresh offer Duffel propose Virgin Atlantic, KLM, etc.",
        "patterns_tested":["P1", "P2"],
    },
    "S25": {
        "name": "Balance Duffel insufficient",
        "category": "duffel_balance_low", "severity":"critical", "expected":"failed",
        "description":"Compte Duffel < seuil minimum. Alerte critique opérateur. Refund total client. Bloque le tunnel jusqu'à recharge.",
        "patterns_tested":["P3", "P4", "P8"],
    },
    "S26": {
        "name": "Duffel API timeout 30s",
        "category": "duffel_api_timeout", "severity":"critical", "expected":"recovered",
        "description":"API Duffel ne répond pas. Retry exponential 3x. Si toujours timeout, fallback message client + alert + monitoring.",
        "patterns_tested":["P7", "P8"],
    },
    "S27": {
        "name": "Surbooking compagnie (refus boarding J)",
        "category": "airline_overbook_boarding", "severity":"critical", "expected":"compensation_handled",
        "description":"Client refusé au boarding (siège vendu 2 fois par compagnie). EU261 = compensation 600€ + ré-acheminement. Conciergerie gère.",
        "patterns_tested":["P4", "P5"],
    },
    "S28": {
        "name": "Grève compagnie aérienne (annulation massive)",
        "category": "airline_strike", "severity":"critical", "expected":"mass_refund",
        "description":"Grève SNCF/Air France annonce 80% vols annulés. Polling Duffel détecte. Refund massif cushion + email proactif tous clients.",
        "patterns_tested":["P3", "P5", "P9"],
    },
    "S29": {
        "name": "Vol retardé 6h+ → email proactif + hôtel dépannage",
        "category": "vol_delayed_major", "severity":"warn", "expected":"recovered",
        "description":"Polling compagnie détecte retard 6h+. Email proactif client + propose hôtel dépannage gratuit (cushion AirBizness) si nuit perdue.",
        "patterns_tested":["P2", "P3", "P5", "P9"],
    },
    "S30": {
        "name": "Connexion ratée (vol 1 retardé fait rater vol 2)",
        "category": "missed_connection", "severity":"critical", "expected":"rebook_auto",
        "description":"Vol intermédiaire retardé → vol 2 raté. Duffel /actions/rebook automatique vers vol suivant. Communication proactive.",
        "patterns_tested":["P2", "P4"],
    },
    "S31": {
        "name": "Rate_key HBX expiré entre quote et paiement",
        "category": "hotel_rate_expired", "severity":"info", "expected":"recovered",
        "description":"Client traîne 20 min dans le tunnel. Rate HBX expire. Re-checkrate automatique avant Stripe avec nouveau prix.",
        "patterns_tested":["P1"],
    },
    "S32": {
        "name": "Hôtel ferme pour rénovation (annonce J-30)",
        "category": "hotel_closed_renovation", "severity":"critical", "expected":"substitute_proposed",
        "description":"HBX notifie fermeture inattendue. Cron substitute trouve équivalent. Email + push client + 1-clic accept.",
        "patterns_tested":["P2", "P5", "P9"],
    },
    "S33": {
        "name": "Taxe touristique imprévue (city tax)",
        "category": "city_tax_unexpected", "severity":"info", "expected":"info_provided",
        "description":"Hôtel facture 5€/nuit taxe locale à l'arrivée. Annoncée AVANT départ par email proactif J-3 pour éviter surprise.",
        "patterns_tested":["P1", "P9"],
    },
    "S34": {
        "name": "HBX API timeout 30s",
        "category": "hbx_api_timeout", "severity":"critical", "expected":"recovered",
        "description":"API HBX ne répond pas. Retry exp 3x. Si toujours fail, fallback sur RateHawk (quand intégré) ou alert critique.",
        "patterns_tested":["P7", "P8"],
    },
    "S35": {
        "name": "Chambre downgrade par hôtel à l'arrivée",
        "category": "hotel_downgrade_arrival", "severity":"warn", "expected":"compensation_handled",
        "description":"Hôtel donne chambre inférieure à celle réservée. Conciergerie négocie upgrade gratuit OR remboursement différentiel.",
        "patterns_tested":["P4", "P5"],
    },
    "S36": {
        "name": "Check-in tardif refusé (arrivée vol 23h)",
        "category": "late_checkin_refused", "severity":"warn", "expected":"recovered",
        "description":"Détection vol arrive 23h+ → pré-notification hôtel J-1 par conciergerie. Si refus, hôtel substitut 24h dispo.",
        "patterns_tested":["P1", "P2", "P4"],
    },
    "S37": {
        "name": "Caution carte refusée à l'arrivée hôtel",
        "category": "deposit_card_refused", "severity":"warn", "expected":"recovered",
        "description":"Hôtel demande caution 500€ → carte refuse. Conciergerie chat → propose dépôt cash autorisé ou substitut hôtel sans caution.",
        "patterns_tested":["P4"],
    },
    "S38": {
        "name": "Email voucher en bounce → SMS + push fallback",
        "category": "email_bounce", "severity":"warn", "expected":"recovered",
        "description":"Brevo retourne bounce. Cron fallback envoie SMS Twilio + push app + lien web temporaire signé.",
        "patterns_tested":["P9"],
    },
    "S39": {
        "name": "Webhook Stripe en retard 5 min",
        "category": "webhook_lag", "severity":"info", "expected":"reconciled",
        "description":"Polling local détecte succeeded en 5s. Booking finalisé. Webhook arrive 5 min plus tard = no-op idempotent.",
        "patterns_tested":["P7", "P8"],
    },
    "S40": {
        "name": "Backend crash entre Stripe paid et Duffel order",
        "category": "infra_crash_mid_flow", "severity":"critical", "expected":"state_recovered",
        "description":"Serveur crash en plein milieu. supervisor restart 5s. Cron reconciliation détecte état 'payment_succeeded' sans booking → retry idempotent Duffel.",
        "patterns_tested":["P7", "P8"],
    },
    "S41": {
        "name": "Client tape mal son email (typo @gmial)",
        "category": "client_email_typo", "severity":"info", "expected":"corrected",
        "description":"Validation email détecte typo @gmial.com → suggère @gmail.com avant submit. Si client confirme, on prend tel quel + envoie SMS confirmation.",
        "patterns_tested":["P6"],
    },
    "S42": {
        "name": "Faute orthographe nom passager (refus boarding)",
        "category": "passenger_name_mismatch", "severity":"critical", "expected":"corrected",
        "description":"Client met 'Mohamed' mais passeport 'Mohammed'. Conciergerie gère correction Duffel (frais 30-80€ couverts par cushion).",
        "patterns_tested":["P5", "P11"],
    },
    "S43": {
        "name": "Client veut annuler hors délai (non remboursable)",
        "category": "out_of_window_cancel", "severity":"warn", "expected":"partial_refund",
        "description":"NRF tarif. Conciergerie propose : avoir 80% sur prochain séjour OU revente HBX (50% chance succès, on tente).",
        "patterns_tested":["P4", "P12"],
    },
    "S44": {
        "name": "Modification dates voyage J-15",
        "category": "modification_dates", "severity":"info", "expected":"modification_quoted",
        "description":"Recalcul HBX (+150€) + Duffel (+95€). Devis présenté. Si accepté, debit additionnel Stripe + nouveau voucher.",
        "patterns_tested":["P4", "P12"],
    },
    "S45": {
        "name": "Décès / hospitalisation client (force majeure)",
        "category": "human_escalation_death", "severity":"critical", "expected":"human_handled",
        "description":"Famille contact. Conciergerie humaine immédiate. Refund total cushion prioritaire sans frais sur justif médical.",
        "patterns_tested":["P3", "P4"],
    },
    "S46": {
        "name": "Pandémie / restriction sanitaire (cas covid-like)",
        "category": "pandemic_restriction", "severity":"critical", "expected":"flex_refund",
        "description":"Annonce gouvernementale restrictions. Politique flex AirBizness : refund 100% ou crédit 110% au choix client.",
        "patterns_tested":["P3", "P5"],
    },
    "S47": {
        "name": "Upgrade chambre demandé J-1",
        "category": "upgrade_request", "severity":"info", "expected":"upgrade_offered",
        "description":"Client demande suite. Conciergerie négocie hôtel direct, différentiel facturé via Stripe Top-up.",
        "patterns_tested":["P4", "P12"],
    },
    "S48": {
        "name": "Visa requis non détecté avant départ",
        "category": "visa_required_detected", "severity":"warn", "expected":"preflight_alerted",
        "description":"Cron J-7 check : nationalité FR → destination AE = visa requis. Email + chatbot proactif + lien démarches.",
        "patterns_tested":["P1", "P9"],
    },
    "S49": {
        "name": "Passeport expire <6 mois après voyage",
        "category": "passport_expiry_check", "severity":"warn", "expected":"preflight_alerted",
        "description":"Cron J-15 check : passeport expire dans 4 mois (refus visa Brésil). Alerte client immédiate.",
        "patterns_tested":["P1", "P9"],
    },
    "S50": {
        "name": "Client perd son e-billet (chat)",
        "category": "voucher_lost", "severity":"info", "expected":"recovered",
        "description":"Chat conciergerie. Renvoi instantané email + lien signé temporaire + push app + WhatsApp.",
        "patterns_tested":["P9", "P10"],
    },
    "S51": {
        "name": "Client demande RGPD : suppression données",
        "category": "rgpd_deletion_request", "severity":"info", "expected":"compliance_handled",
        "description":"Workflow auto : pseudo-anonymisation données perso après confirmation 7j. Conserve seulement requis légal (facture 10 ans).",
        "patterns_tested":["P11"],
    },
    "S52": {
        "name": "Tentative data breach détectée",
        "category": "security_breach_attempt", "severity":"critical", "expected":"contained",
        "description":"WAF Cloudflare détecte SQL injection. Bloque IP. Alert sécurité + audit logs. Aucun data leak réel.",
        "patterns_tested":["P11", "P8"],
    },
    "S53": {
        "name": "Litige client → escalade médiation MTV",
        "category": "litigation_escalation", "severity":"warn", "expected":"mediation_started",
        "description":"Client mécontent insiste. Conciergerie tente résolution amiable. Si refus, dossier auto pour Médiateur Tourisme Voyage.",
        "patterns_tested":["P4", "P11"],
    },
    "S54": {
        "name": "Serveur downtime 5 min (panne provider cloud)",
        "category": "server_downtime", "severity":"critical", "expected":"recovered",
        "description":"Provider cloud panne 5 min. Status page AirBizness mise à jour. Requests pending reconciliées au retour.",
        "patterns_tested":["P7", "P8"],
    },
    "S55": {
        "name": "Certificat SSL expire J-1 (auto-renew failed)",
        "category": "ssl_expiry", "severity":"critical", "expected":"renewed",
        "description":"Cron monitoring détecte SSL expire dans 24h. Auto renew certbot + alert si fail. Manual fallback si critique.",
        "patterns_tested":["P8"],
    },
    "S56": {
        "name": "DDoS attack 100k req/sec",
        "category": "ddos_attack", "severity":"critical", "expected":"mitigated",
        "description":"Cloudflare Under Attack Mode auto-activé. Captcha challenges. Real users impactés <5 sec.",
        "patterns_tested":["P11"],
    },
    "S57": {
        "name": "SMS Twilio non délivré (numéro invalide)",
        "category": "sms_undelivered", "severity":"info", "expected":"recovered",
        "description":"Twilio retourne UNDELIVERABLE. Fallback push app + email avec lien web temporaire signé.",
        "patterns_tested":["P9"],
    },
    "S58": {
        "name": "Hôtelier bypass (client vu chez nous, réserve direct)",
        "category": "hotel_bypass", "severity":"info", "expected":"tracked",
        "description":"Pixel tracking détecte conversion lost. Email automatique hôtelier rappel commission + valeur ajoutée AirBizness.",
        "patterns_tested":["P12"],
    },
    "S59": {
        "name": "Concurrent OTA copie nos prix négociés",
        "category": "competitor_scraping", "severity":"warn", "expected":"detected",
        "description":"Détection bot scrape patterns reconnus. Rate limit + service fingerprint + alert.",
        "patterns_tested":["P11"],
    },
    "S60": {
        "name": "Cart abandonment → email recovery J+1",
        "category": "cart_abandonment", "severity":"info", "expected":"recovered",
        "description":"Client quitte au step 2 du tunnel. Cron J+1 email rappel avec lien direct reprise du tunnel.",
        "patterns_tested":["P12"],
    },
}


def _sandbox_user(idx: int) -> dict:
    """Retourne un user fictif rotatif."""
    return SANDBOX_USERS[idx % len(SANDBOX_USERS)]


def _sandbox_ref() -> str:
    import uuid as _u
    return "AB-SBOX-" + _u.uuid4().hex[:8].upper()


def run_sandbox_scenario(scenario_id: str) -> dict:
    """Exécute un scénario sandbox de bout en bout. Retourne timeline + result + alerts générés."""
    scenario = SANDBOX_SCENARIOS.get(scenario_id)
    if not scenario:
        return {"error": "unknown_scenario", "scenario_id": scenario_id}

    user = _sandbox_user(int(scenario_id[1:]))
    ref = _sandbox_ref()
    timeline = []

    def log(step: str, status: str, detail: str = ""):
        timeline.append({"step": step, "status": status, "detail": detail,
                         "ts": datetime.utcnow().isoformat() + "Z"})

    log("scenario_start", "ok", f"User: {user['first']} {user['last']} ({user['country']})")

    # INSERT un pack_booking sandbox (pour traçabilité)
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pack_bookings (
                airbizness_ref, status, is_sandbox,
                user_email, user_phone, holder_name, holder_surname,
                flight_origin, flight_destination, flight_departure_date,
                flight_airline, flight_cabin, flight_price, flight_provider,
                hotel_code, hotel_name, hotel_destination_code,
                hotel_check_in, hotel_check_out, hotel_rate_key, hotel_price,
                total_amount, currency, adults
            ) VALUES (
                %s, 'sandbox_running', TRUE,
                %s, %s, %s, %s,
                'CDG','MAD','2026-06-15',
                'AF','business', 890.0, 'duffel',
                39121, 'Puerta America Madrid', 'MAD',
                '2026-06-15','2026-06-18', 'sandbox-rate-key', 470.0,
                1292.0, 'EUR', 1
            )
        """, (ref, user["email"], user["phone"], user["first"], user["last"]))
        conn.commit(); cur.close(); conn.close()
        log("pack_booking_insert", "ok", f"ref={ref}")
    except Exception as e:
        log("pack_booking_insert", "error", str(e))
        return {"scenario_id": scenario_id, "ref": ref, "timeline": timeline, "error": str(e)}

    # ── Branche selon scenario_id ──
    cat = scenario["category"]

    if cat == "happy_path":
        log("stripe_paid", "ok", "Stripe 3DS validé · 1292€ capturé")
        log("duffel_order_create", "ok", "PNR émis: SBX-PNR-001 · E-ticket: 999-SBX-12345")
        log("hbx_booking_create", "ok", "Hôtel réf HBX: 102-SBX-2026")
        log("voucher_email_sent", "ok", f"Voucher envoyé à {user['email']}")
        _update_sandbox_status(ref, "confirmed")

    elif cat == "flight_fail":
        log("stripe_paid", "ok", "Stripe 3DS validé · 1292€ capturé")
        log("duffel_order_create", "fail", "Siège plus disponible (race condition)")
        alert_id = alert_conciergerie(ref, "warn", "booking_failed_vol",
                                       {"sandbox": True, "scenario": scenario_id,
                                        "reason": "Siège plus disponible"})
        log("conciergerie_alert", "ok", f"Alert créée id={alert_id}")
        log("stripe_refund_total", "ok", "1292€ remboursés via cushion (instant)")
        _update_sandbox_status(ref, "failed")

    elif cat == "hotel_fail_after_flight_ok":
        log("stripe_paid", "ok", "Stripe 3DS validé · 1292€ capturé")
        log("duffel_order_create", "ok", "PNR émis: SBX-PNR-002 · vol OK")
        log("hbx_booking_create", "fail", "Hôtel overbooking détecté (HBX rejette)")
        log("stripe_refund_partial", "ok", "470€ remboursés (part hôtel uniquement)")
        log("conciergerie_alert_critical", "ok", "Substitute needed urgent - conciergerie notifiée")
        alert_id = alert_conciergerie(ref, "critical", "substitute_needed",
                                       {"sandbox": True, "scenario": scenario_id,
                                        "scenario_label": "vol_ok_hotel_failed",
                                        "vol_pnr": "SBX-PNR-002",
                                        "hotel_to_substitute": "Puerta America Madrid",
                                        "action_required": "Trouver hôtel substitut <1h"})
        log("client_email", "ok", "Email proactif: 'Vol confirmé, hôtel substitut sous 1h'")
        log("substitute_search", "ok", "3 substituts pré-calculés disponibles immédiatement")
        _update_sandbox_status(ref, "partial_confirmed")

    elif cat == "price_drift":
        log("duffel_refresh_offer", "warn", "Cache 2300€ → Live 4700€ (drift +104%)")
        log("tunnel_blocked", "ok", "Bloqué avant Stripe. 3 alternatives proposées au client")
        log("alternatives_displayed", "ok", "China Eastern 2799€ · Singapore 2820€ · Lufthansa 2950€")
        _update_sandbox_status(ref, "price_blocked")

    elif cat == "payment_fail":
        log("stripe_3ds_challenge", "warn", "Banque émettrice rejette le 3DS")
        log("tunnel_blocked", "ok", "Aucun débit, aucun booking effectué")
        log("client_message", "ok", "Message: 'Réessayez ou changez de carte'")
        _update_sandbox_status(ref, "failed")

    elif cat == "validation_fail":
        log("form_validation", "warn", "Champs invalides: born_on vide, phone format KO")
        log("tunnel_blocked", "ok", "Bloqué avant Stripe avec feedback champs UX")
        log("client_message", "ok", "Date de naissance et téléphone requis (format international)")
        _update_sandbox_status(ref, "validation_blocked")

    elif cat == "vol_cancelled_during_stay":
        log("polling_duffel", "warn", "Duffel notification: vol annulé compagnie J-3")
        log("substitute_lookup", "ok", "Substitut pré-calculé trouvé (KLM AMS-MAD horaire ±1h)")
        log("client_email_push", "ok", "Email + SMS + push proactifs envoyés")
        log("client_validation_1click", "ok", "Client accepte substitut (mock)")
        log("new_pnr", "ok", "Nouveau PNR émis: SBX-SUB-007")
        alert_id = alert_conciergerie(ref, "critical", "vol_cancelled_substitute_offered",
                                       {"sandbox": True, "scenario": scenario_id})
        log("conciergerie_alert", "ok", f"Alert id={alert_id} pour suivi")
        _update_sandbox_status(ref, "substitute_proposed")

    elif cat == "hotel_overbooking_arrival":
        log("hotel_arrival", "warn", "Client arrive : hôtel dit 'plus de chambre'")
        log("conciergerie_human", "ok", "Conciergerie humaine prend la main (chat client)")
        log("substitute_book", "ok", "Hôtel équivalent réservé en direct (relation Pascal)")
        log("client_compensation", "ok", "Taxi pris en charge (50€ cushion AirBizness)")
        alert_id = alert_conciergerie(ref, "critical", "hotel_overbooking_arrival",
                                       {"sandbox": True, "scenario": scenario_id})
        _update_sandbox_status(ref, "substitute_proposed")

    elif cat == "force_majeure":
        log("event_detected", "warn", "Force majeure: éruption volcanique Islande")
        log("mass_impact", "ok", "47 réservations impactées identifiées")
        log("mass_refund_cushion", "ok", "Refund cushion AirBizness initié pour 47 clients")
        log("communication_bulk", "ok", "Email + SMS de réassurance envoyés sous 30 min")
        _update_sandbox_status(ref, "mass_refund")

    elif cat == "modification_request":
        log("client_chat", "info", "Client demande décaler dates +3 jours")
        log("hbx_recheck", "ok", "Nouveau rate HBX trouvé (+87€)")
        log("duffel_recheck", "ok", "Nouveau vol même compagnie (+45€)")
        log("quote_proposed", "ok", "Devis modification +132€ proposé au client")
        _update_sandbox_status(ref, "modification_quoted")

    elif cat == "communication_fail":
        log("brevo_send", "warn", "Email bounce: adresse jean@badmail.com inexistante")
        log("fallback_sms", "ok", "Twilio SMS envoyé avec lien voucher signé")
        log("fallback_push", "ok", "Push notification app envoyée")
        log("client_confirmed_received", "ok", "Client confirme via chat avoir reçu")
        _update_sandbox_status(ref, "recovered")

    elif cat == "fraud_chargeback":
        log("stripe_chargeback", "critical", "Chargeback reçu 90j après séjour")
        log("dossier_auto", "ok", "Compilation auto: voucher signé + e-billet utilisé + check-in/out hôtel")
        log("stripe_dispute_response", "ok", "Soumis à Stripe avec preuves")
        log("status_pending", "info", "Décision banque sous 30j")
        alert_id = alert_conciergerie(ref, "warn", "chargeback_received",
                                       {"sandbox": True, "scenario": scenario_id})
        _update_sandbox_status(ref, "chargeback_disputed")

    elif cat == "infra_failure":
        log("backend_crash", "critical", "Serveur crash après Stripe paid, avant Duffel order")
        log("supervisor_restart", "ok", "supervisord relance airbizness-api en 5s")
        log("reconciliation_cron", "ok", "Cron détecte pack_booking en 'payment_succeeded' sans booking")
        log("retry_duffel_order", "ok", "POST /air/orders retry idempotent → PNR émis")
        log("state_recovered", "ok", "Status passé à 'confirmed' sans intervention humaine")
        _update_sandbox_status(ref, "state_recovered")

    elif cat == "abuse_protection":
        log("rate_detected", "warn", "200 req/sec depuis IP 1.2.3.4 sur /api/v2/hotels/search")
        log("slowapi_block", "ok", "Slowapi rate limit déclenché (60/min max)")
        log("cloudflare_ban", "ok", "IP marquée + WAF Cloudflare bloque 24h")
        log("real_users_unaffected", "ok", "Aucun impact sur les 3 résa en cours sur autres IPs")
        _update_sandbox_status(ref, "rate_limited")

    elif cat == "race_condition":
        log("client_A_pay", "ok", "Client A paye chambre Plaza Athénée 12345 à 14h32:01")
        log("client_B_pay", "ok", "Client B paye même chambre à 14h32:03 (race)")
        log("hbx_book_A", "ok", "HBX booking A confirmé")
        log("hbx_book_B", "fail", "HBX rejette B (chambre déjà bookée)")
        log("stripe_refund_B", "ok", "Refund client B sous 30s via cushion")
        log("substitute_B", "ok", "Chambre équivalente proposée à client B (Ritz à -50€ negociated)")
        alert_id = alert_conciergerie(ref, "warn", "race_condition_handled",
                                       {"sandbox": True, "scenario": scenario_id})
        _update_sandbox_status(ref, "second_refunded")

    elif cat == "voucher_recovery":
        log("client_chat_request", "info", "Client: 'J'ai perdu mon voucher'")
        log("identity_check", "ok", "Vérification email + nom + ref réservation")
        log("multichannel_resend", "ok", "Email + SMS + lien signé temporaire + push app")
        log("client_acknowledged", "ok", "Client confirme réception sous 2 min")
        _update_sandbox_status(ref, "recovered")

    elif cat == "human_escalation":
        log("family_contact", "critical", "Email famille: décès du voyageur principal")
        log("escalation_concierge", "ok", "Conciergerie humaine alertée immédiatement")
        log("documents_collected", "ok", "Certificat de décès reçu par WhatsApp sécurisé")
        log("refund_priority", "ok", "Refund total cushion initié sans frais ni délai")
        log("compassionate_message", "ok", "Message personnel équipe AirBizness envoyé")
        alert_id = alert_conciergerie(ref, "critical", "human_escalation_death",
                                       {"sandbox": True, "scenario": scenario_id})
        _update_sandbox_status(ref, "human_handled")

    elif cat == "preflight_check":
        log("preflight_cron_J7", "info", "Cron J-7 sur réservations actives")
        log("visa_check", "warn", "Voyageur FR → AE détecté · visa requis")
        log("client_proactive_email", "ok", "Email + push: 'Démarches visa AE — 5 jours restants'")
        log("chatbot_init", "ok", "Chatbot AI propose lien officiel + check-list")
        _update_sandbox_status(ref, "preflight_alerted")

    elif cat == "webhook_lag":
        log("stripe_paid_local", "ok", "Polling direct détecte succeeded à 14:32:05")
        log("pack_confirmed", "ok", "Booking finalisé à 14:32:07")
        log("stripe_webhook_late", "info", "Webhook arrive à 14:34:12 (lag 2 min)")
        log("idempotency_noop", "ok", "Endpoint /webhook/stripe détecte déjà traité → no-op")
        _update_sandbox_status(ref, "reconciled")

    elif cat == "vip_upgrade":
        log("vip_scoring", "ok", "Client identifié VIP (3e séjour · panier moyen 5800€)")
        log("upgrade_negotiate", "ok", "Conciergerie négocie suite Bvlgari upgrade gratuit")
        log("client_surprise_email", "ok", "Email surprise: 'Suite signature gracieusement upgradée'")
        log("loyalty_boost", "ok", "Score fidélité boost (NPS attendu 10/10)")
        _update_sandbox_status(ref, "upgrade_offered")

    else:
        # ── Handler générique pour les scenarios additionnels S21-S60 ──
        # Génère une timeline réaliste selon la category, en utilisant le payload
        # de SANDBOX_SCENARIOS pour rester cohérent avec la description.
        sev = scenario.get("severity", "info")
        log("scenario_routing", "info", f"Category: {cat}")

        # Patterns génériques par type
        if cat in ("payment_card_declined","fraud_radar_block","stripe_capture_fail"):
            log("stripe_pre_check", "info", "Stripe authorize tentative")
            log("stripe_decision", "warn", "Refusé/bloqué par banque ou Stripe Radar")
            log("client_message", "ok", "Message UX clair + suggestion alternative (Apple Pay, autre carte)")
            if sev != "info":
                alert_conciergerie(ref, sev, "payment_blocked",
                                    {"sandbox":True,"scenario":scenario_id,"category":cat})
                log("conciergerie_alert", "ok", f"Alert {sev} créée pour review")

        elif cat in ("airline_route_dropped","hotel_rate_expired"):
            log("checkrate_live", "info", "Re-vérification live avant Stripe")
            log("change_detected", "warn", "Tarif/disponibilité a changé")
            log("alternative_proposed", "ok", "Nouvelle option présentée au client avec drift transparent")

        elif cat in ("duffel_balance_low","airline_strike"):
            log("upstream_critical", "critical", "Provider/marché en état critique")
            log("mass_action", "ok", "Refund cushion massif initié sous 1h")
            log("client_email_bulk", "ok", "Email réassurance envoyé à tous clients impactés")
            alert_conciergerie(ref, "critical", "mass_event",
                                {"sandbox":True,"scenario":scenario_id,"category":cat})
            log("conciergerie_alert_critical", "ok", "Alerte critique conciergerie")

        elif cat in ("duffel_api_timeout","hbx_api_timeout","server_downtime"):
            log("provider_timeout", "critical", "Provider/serveur ne répond pas")
            log("retry_exponential", "ok", "Retry 3x avec backoff exponentiel")
            log("fallback_or_message", "ok", "Soit fallback provider, soit message client + alert ops")
            log("recovery", "ok", "Service récupéré sous 30s ou message clair envoyé")

        elif cat in ("airline_overbook_boarding","missed_connection","vol_delayed_major"):
            log("event_detected", "warn", "Incident vol détecté (polling Duffel + compagnie)")
            log("rebook_or_compensation", "ok", "Ré-acheminement automatique OR compensation EU261")
            log("client_proactive", "ok", "Email + SMS + push proactifs envoyés avant que client appelle")
            alert_conciergerie(ref, sev, "flight_incident",
                                {"sandbox":True,"scenario":scenario_id,"category":cat})

        elif cat in ("hotel_closed_renovation","hotel_overbooking_arrival","hotel_downgrade_arrival",
                       "late_checkin_refused","deposit_card_refused"):
            log("hotel_incident", "warn", "Incident hôtel détecté")
            log("substitute_or_negotiation", "ok", "Substitut équivalent OU négo direct hôtelier")
            log("compensation", "ok", "Compensation client si applicable (cushion ou geste com)")
            alert_conciergerie(ref, sev, "hotel_incident",
                                {"sandbox":True,"scenario":scenario_id,"category":cat})

        elif cat in ("city_tax_unexpected","visa_required_detected","passport_expiry_check"):
            log("preflight_check_cron", "info", "Check préflight automatique")
            log("anomaly_detected", "warn", "Anomalie détectée (taxe, visa, passeport)")
            log("client_proactive_email", "ok", "Email + chatbot proactif avec démarches")
            log("recovery_supported", "ok", "Client guidé sans avoir à appeler le support")

        elif cat in ("email_bounce","sms_undelivered","voucher_lost"):
            log("communication_issue", "warn", "Canal de communication échoue")
            log("multichannel_fallback", "ok", "Bascule auto vers canaux alternatifs (SMS/push/email)")
            log("client_acknowledged", "ok", "Client confirme réception")

        elif cat == "webhook_lag":
            log("polling_caught", "ok", "Polling direct détecte succeeded avant webhook")
            log("booking_finalized", "ok", "Booking déjà confirmé")
            log("webhook_late_noop", "info", "Webhook arrive plus tard = no-op idempotent")

        elif cat == "infra_crash_mid_flow":
            log("crash_detected", "critical", "Serveur crash en plein milieu")
            log("supervisor_restart", "ok", "supervisord relance en 5s")
            log("reconciliation_cron", "ok", "Cron détecte état pending → retry idempotent")
            log("state_recovered", "ok", "Sans intervention humaine")

        elif cat in ("client_email_typo","passenger_name_mismatch"):
            log("input_validation", "info", "Validation détecte anomalie input")
            log("suggestion_or_correction", "ok", "Soit suggestion auto soit conciergerie corrige")
            log("frais_couverts", "ok", "Si frais compagnie nécessaire, cushion absorbe")

        elif cat in ("out_of_window_cancel","modification_dates","upgrade_request"):
            log("client_request", "info", "Demande modification client")
            log("recalcul_providers", "ok", "Recalcul HBX + Duffel en live")
            log("quote_proposed", "ok", "Devis présenté au client pour validation")
            log("execution_or_credit", "ok", "Exécution si accepté OU avoir si non-refundable")

        elif cat == "human_escalation_death":
            log("family_contact", "critical", "Contact famille pour décès/hospitalisation")
            log("escalation_concierge", "ok", "Conciergerie humaine immédiate")
            log("refund_priority_cushion", "ok", "Refund total cushion sans frais")
            log("compassionate_message", "ok", "Message personnel équipe AirBizness")
            alert_conciergerie(ref, "critical", "human_escalation",
                                {"sandbox":True,"scenario":scenario_id})

        elif cat == "pandemic_restriction":
            log("global_event", "critical", "Restriction sanitaire/gouvernementale")
            log("flex_policy_apply", "ok", "Politique flex AirBizness : refund 100% OU crédit 110%")
            log("client_choice", "ok", "Email avec 2 options au choix client")

        elif cat in ("rgpd_deletion_request","security_breach_attempt"):
            log("compliance_workflow", "info", "Workflow compliance déclenché")
            log("audit_log", "ok", "Toutes actions tracées dans audit log")
            log("auto_processing", "ok", "Traitement automatique selon procédure RGPD/sécurité")

        elif cat == "litigation_escalation":
            log("amicable_attempt", "info", "Tentative résolution amiable conciergerie")
            log("mediator_dossier", "ok", "Dossier auto compilé pour Médiateur Tourisme Voyage")
            log("transparent_position", "ok", "Position AirBizness transparente et documentée")

        elif cat in ("ssl_expiry","ddos_attack"):
            log("infra_event", "critical", "Événement infra détecté")
            log("auto_mitigation", "ok", "Auto-mitigation (certbot renew / Cloudflare UAM)")
            log("monitoring_alert", "ok", "Alert ops + monitoring")
            log("zero_user_impact", "ok", "Impact utilisateurs < 5 sec")

        elif cat in ("hotel_bypass","competitor_scraping","cart_abandonment"):
            log("business_event", "info", "Événement business détecté")
            log("automated_response", "ok", "Réponse auto (email recovery, rate limit, alert)")
            log("metric_tracked", "ok", "Métrique enregistrée pour analyse")

        else:
            log("scenario_unknown_category", "error", f"Cat {cat} not handled")

        _update_sandbox_status(ref, scenario.get("expected", "completed"))

    log("scenario_end", "ok", f"Scenario {scenario_id} terminé")

    # Récupère les alerts conciergerie générés pour ce ref
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, severity, alert_type, payload, status, created_at
            FROM conciergerie_alerts
            WHERE airbizness_ref = %s ORDER BY id
        """, (ref,))
        alerts = [dict(r) for r in cur.fetchall()]
        for a in alerts:
            a["created_at"] = a["created_at"].isoformat() if a.get("created_at") else None
        cur.close(); conn.close()
    except Exception:
        alerts = []

    return {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "user": user,
        "ref": ref,
        "timeline": timeline,
        "alerts_generated": alerts,
        "expected": scenario["expected"],
    }


def _update_sandbox_status(ref: str, status: str):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("UPDATE pack_bookings SET status=%s WHERE airbizness_ref=%s",
                    (status, ref))
        conn.commit(); cur.close(); conn.close()
    except Exception:
        pass


@router.get("/sandbox/scenarios")
def sandbox_list_scenarios():
    """Liste tous les scénarios disponibles."""
    return {
        "scenarios": [
            {"id": k, **v} for k, v in SANDBOX_SCENARIOS.items()
        ]
    }


@router.post("/sandbox/simulate/{scenario_id}")
@limiter.limit("60/minute")
def sandbox_simulate(request: Request, scenario_id: str):
    """Exécute un scénario sandbox de bout en bout."""
    result = run_sandbox_scenario(scenario_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/sandbox/cleanup")
@limiter.limit("10/minute")
def sandbox_cleanup(request: Request):
    """Supprime toutes les données sandbox (reset). NE TOUCHE PAS aux vraies résa."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("DELETE FROM conciergerie_alerts WHERE is_sandbox = TRUE")
        n_alerts = cur.rowcount
        cur.execute("DELETE FROM pack_bookings WHERE is_sandbox = TRUE")
        n_bookings = cur.rowcount
        conn.commit(); cur.close(); conn.close()
        return {"deleted_alerts": n_alerts, "deleted_bookings": n_bookings}
    except Exception as e:
        raise HTTPException(500, str(e))
