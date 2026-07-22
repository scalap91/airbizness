"""Module PAIEMENT (séparé de la réservation).

Stripe payment-intents + webhook de confirmation. Volontairement isolé :
la réservation gère l'offre, le paiement gère l'argent.
"""
import json
import os
import uuid as _uuid
import psycopg2, psycopg2.extras
import stripe
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from main import (limiter, DB_CONFIG,
                  PaymentIntentRequest, PaymentIntentRequest2, FlightPaymentIntentRequest,
                  _compute_hotel_options_total,
                  STRIPE_CAPTURE_MANUAL, STRIPE_WEBHOOK_SECRET, HOTEL_OPTION_PRICES,
                  _is_mock_offer, _load_offer_for_booking, _alert_telegram, _serialize_deal,
                  _is_mock_rate_key, _pack_db_conn,
                  _send_activity_booking_confirmation, _send_booking_failed_mail,
                  _send_flight_booking_confirmation, _send_hotel_booking_confirmation,
                  _send_pack_confirmation_email,
                  _stripe_cancel_intent, _stripe_capture_intent,
                  _stripe_event_mark_processed, _stripe_event_seen, _stripe_refund_auto)

router = APIRouter()


@router.post("/create-payment-intent")
@limiter.limit("10/minute")
def create_payment_intent(request: Request, req: PaymentIntentRequest):
    offer_id = (req.booking or {}).get("offerId") or (req.booking or {}).get("offer_id")
    if not offer_id:
        raise HTTPException(status_code=400, detail="Missing offer_id in booking")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT price, expires_at FROM deals WHERE offer_id = %s LIMIT 1", (offer_id,))
    deal = cur.fetchone()
    cur.close()
    conn.close()

    if not deal:
        raise HTTPException(status_code=404, detail="Offer not found or expired")
    if deal["expires_at"] and deal["expires_at"] < datetime.now(deal["expires_at"].tzinfo):
        raise HTTPException(status_code=410, detail="Offer expired")

    amount_eur = req.amount / 100.0
    deal_price = float(deal["price"])
    # Floor: 95% of deal price (5% tolerance for FX). Ceiling: deal + 500€ of legit add-ons (insurance/extras).
    min_eur = deal_price * 0.95
    max_eur = deal_price + 500
    if amount_eur < min_eur or amount_eur > max_eur:
        raise HTTPException(
            status_code=400,
            detail=f"Amount {amount_eur:.2f}€ does not match deal {deal_price:.2f}€ (allowed range {min_eur:.2f}-{max_eur:.2f})",
        )

    intent = stripe.PaymentIntent.create(
        amount=req.amount,
        currency=req.currency,
        metadata={"offer_id": offer_id, "deal_price": deal_price},
        automatic_payment_methods={"enabled": True},
    )
    return {"client_secret": intent.client_secret}


# Hôtel — payment-intent (provider-agnostique). Le front (checkout.html) appelle
# /api/hotel/booking/payment-intent depuis le 2026-05-30 ; l'alias legacy /hbx/...
# a été retiré une fois le front migré.
@router.post("/hotel/booking/payment-intent")
@limiter.limit("30/minute")
def hotel_create_payment_intent(request: Request, body: PaymentIntentRequest2):
    """Crée un Stripe PaymentIntent + INSERT bookings_v2 status='payment_pending'.
    Retourne client_secret pour le 3DS côté front + airbizness_ref pour suivi.
    Inclut options hôtel (late_checkin / insurance / transfer / special_requests) :
    calculées serveur, ajoutées au PI Stripe et stockées dans hbx_booking_raw JSONB."""
    # Validation prix chambre seule : on accepte une tolérance pour éviter abus
    if body.gross_price < 1 or body.gross_price > 50000:
        raise HTTPException(400, "Prix invalide")

    # ── Options : source de vérité serveur, on recalcule (pas confiance au front) ──
    transfer = (body.transfer or "none").lower()
    if transfer not in ("none", "oneway", "roundtrip"):
        transfer = "none"
    options_total = _compute_hotel_options_total(
        late_checkin=bool(body.late_checkin),
        insurance=bool(body.insurance),
        transfer=transfer,
        adults=body.adults,
    )
    special_requests = (body.special_requests or "").strip()[:250] or None

    # Transfer HBX (snapshot pré-paiement — booké au /confirm si rate_key)
    # On l'ajoute AVANT Stripe pour qu'il soit débité au paiement.
    transfer_price_extra = float(body.transfer_price or 0)
    total_with_options = round(float(body.gross_price) + options_total
                                + transfer_price_extra, 2)

    airbizness_ref = f"AB-{_uuid.uuid4().hex[:10].upper()}"

    try:
        # Audit 2026-05-27 sev 4 #52 : capture_method='manual' si flag activé.
        _pi_kwargs = {
            "amount": int(round(total_with_options * 100)),  # Stripe en centimes
            "currency": body.currency.lower(),
            "automatic_payment_methods": {"enabled": True},
            "metadata": {
                "airbizness_ref": airbizness_ref,
                "hotel_code": str(body.hotel_code),
                "user_email": body.user_email,
                "type": "hbx_hotel",
                "options_total": str(options_total),
                "transfer_price": f"{transfer_price_extra:.2f}",
                "capture_mode": "manual" if STRIPE_CAPTURE_MANUAL else "automatic",
            },
            "receipt_email": body.user_email,
            "description": f"AirBizness · {body.hotel_name[:50]} · {body.check_in}→{body.check_out}",
        }
        if STRIPE_CAPTURE_MANUAL:
            _pi_kwargs["capture_method"] = "manual"
        intent = stripe.PaymentIntent.create(**_pi_kwargs)
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")

    # Options sérialisées dans le JSONB hbx_booking_raw (pas d'ALTER TABLE)
    options_payload = {
        "late_checkin": bool(body.late_checkin),
        "special_requests": special_requests,
        "insurance": bool(body.insurance),
        "transfer": transfer,
        "options_total": options_total,
        "room_total": float(body.gross_price),
        "prices": HOTEL_OPTION_PRICES,
        # Transfer HBX
        "transfer_rate_key": body.transfer_rate_key or None,
        "transfer_price": transfer_price_extra,
        "transfer_label": body.transfer_label or None,
        "transfer_meta": body.transfer_meta or None,
        # Concierge hôtelier (résa pour client) — Pascal 2026-05-24
        "on_behalf_of": (str(body.on_behalf_of) if body.on_behalf_of else None),
        "guest_name": body.guest_name or None,
        "guest_email": body.guest_email or None,
        "guest_ref": body.guest_ref or None,
    }

    # Demandes/options dans remark pour transmission HBX (le partenaire les voit)
    remark_bits = [body.remark.strip()] if (body.remark and body.remark.strip()) else []
    extra_bits = []
    if body.late_checkin:
        extra_bits.append("Late check-in (après 20h)")
    if body.insurance:
        extra_bits.append(f"Assurance Multirisque ({body.adults} pax)")
    if transfer == "oneway":
        extra_bits.append("Transfert aéroport aller")
    elif transfer == "roundtrip":
        extra_bits.append("Transfert aéroport aller-retour")
    if special_requests:
        extra_bits.append(f"Demandes: {special_requests}")
    if extra_bits:
        remark_bits.append("Options AirBizness: " + " · ".join(extra_bits))
    full_remark = " | ".join(remark_bits)[:500]

    # INSERT bookings_v2 status='payment_pending' (gross_price = chambre + options)
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bookings_v2 (
                  airbizness_ref, status, user_email, user_phone,
                  holder_name, holder_surname,
                  hotel_code, hotel_name, destination_code,
                  check_in, check_out, adults, rooms_count,
                  net_price, gross_price, currency,
                  rate_key_verified, payment_intent_id, payment_status,
                  remark, hbx_booking_raw
                ) VALUES (
                  %s, 'payment_pending', %s, %s,
                  %s, %s,
                  %s, %s, %s,
                  %s, %s, %s, %s,
                  NULL, %s, %s,
                  %s, %s, %s,
                  %s, %s::jsonb
                )
            """, (
                airbizness_ref, body.user_email, body.user_phone,
                body.holder_name, body.holder_surname,
                body.hotel_code, body.hotel_name, body.destination_code,
                body.check_in, body.check_out, body.adults, body.rooms_count,
                total_with_options, body.currency,
                body.rate_key_verified, intent.id, "pending",
                full_remark, json.dumps({"airbizness_options": options_payload}),
            ))
    except Exception as e:
        # Best-effort, on continue mais on log
        print(f"[booking] INSERT bookings_v2 fail: {e}")

    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "airbizness_ref": airbizness_ref,
        "room_total": float(body.gross_price),
        "options_total": options_total,
        "gross_price": total_with_options,
        "options_breakdown": options_payload,
    }


@router.post("/flight/booking/payment-intent")
@limiter.limit("30/minute")
def flight_create_payment_intent(request: Request, body: FlightPaymentIntentRequest):
    """Crée un Stripe PaymentIntent + INSERT flight_bookings status='payment_pending'.

    Idempotency-Key dispo via header (Stripe). Le front re-tente safe.
    """
    if not body.passengers:
        raise HTTPException(400, "Au moins 1 passager requis")

    is_mock = _is_mock_offer(body.offer_id)

    # Récup détails offer pour persister snapshot (utile pour la page confirmation)
    try:
        offer_snapshot = _load_offer_for_booking(body.offer_id)
    except HTTPException:
        # Pour un offer DB inconnu non-mock → 404 propage
        raise



    # ── Audit 2026-05-27 critique #19/#37 : persiste les passenger_ids du live
    # offer dans raw_offer.passenger_ids pour que le webhook puisse les passer
    # à create_order. Sans ça Duffel matche par name → fragile pour homonymes.
    # On le fait ici (au moment de PI) plutôt que dans le webhook pour rester
    # rapide côté webhook + garantir que l'offer existe encore.
    live_passenger_ids: List[str] = []
    if not is_mock:
        try:
            from providers.duffel import get_offer_live as _gol
            _live_offer = _gol(body.offer_id, with_services=False)
            live_passenger_ids = [
                p["id"] for p in (_live_offer.get("passengers") or [])
                if isinstance(p, dict) and p.get("id")
            ]
            print(f"[flight.pi] live_passenger_ids={live_passenger_ids} (offer={body.offer_id[-12:]})")
        except Exception as _e_lp:
            # Best-effort : si Duffel KO ici, on continue (webhook tentera quand même)
            print(f"[flight.pi] get_offer_live KO offer={body.offer_id}: {_e_lp}")
            _alert_telegram(f"flight.pi get_offer_live KO offer={body.offer_id[-12:]}: {str(_e_lp)[:200]}")

    # Pour les MOCK-*, on enrichit le snapshot avec les champs envoyés par le front
    # (airline_name, origin, destination, departure_at, duration_minutes…) afin
    # que la page confirmation affiche les bonnes infos.
    if is_mock and body.deal_snapshot:
        for k in ("airline_name", "airline_code", "origin", "destination",
                  "departure_at", "duration_minutes", "cabin_class", "stops",
                  "currency"):
            v = body.deal_snapshot.get(k)
            if v is not None and v != "":
                offer_snapshot[k] = v

    # ── Calcul options_total côté serveur (jamais confiance au client) ──
    # Pascal 2026-05-26 align sejour : options par-leg pour aligner le tunnel
    # vol seul sur le tunnel vol+hôtel (sejour.html allbyleg).
    n_pax = len(body.passengers)
    options_total = 0.0

    # Helper bagages par leg
    BAG_PRICES = {"15kg": 25.0, "23kg": 45.0, "30kg": 65.0}
    def _clean_bag_list(arr):
        cleaned = []
        total = 0.0
        if arr:
            for b in arr[:n_pax]:
                bnorm = (b or "").lower() if isinstance(b, str) else None
                if bnorm in BAG_PRICES:
                    total += BAG_PRICES[bnorm]
                    cleaned.append(bnorm)
                else:
                    cleaned.append(None)
        return cleaned, total

    # Bagages aller / retour (prio : bag_outbound_per_passenger, fallback baggage_per_passenger = aller)
    bag_outbound_in = body.baggage_outbound_per_passenger or body.baggage_per_passenger or []
    bag_inbound_in = body.baggage_inbound_per_passenger or []
    baggage_outbound_clean, bag_outbound_total = _clean_bag_list(bag_outbound_in)
    baggage_inbound_clean, bag_inbound_total = _clean_bag_list(bag_inbound_in)
    # Alias rétrocompat (= aller)
    baggage_clean = baggage_outbound_clean
    options_total += bag_outbound_total + bag_inbound_total

    # Options par leg (cabin_premium / flex_ticket / flight_insurance)
    # Si les champs par-leg sont fournis, on les utilise. Sinon on retombe sur l'alias = aller.
    cabin_premium_outbound = bool(body.cabin_premium_outbound) or (bool(body.cabin_premium) and not bool(body.cabin_premium_inbound))
    cabin_premium_inbound = bool(body.cabin_premium_inbound)
    flex_ticket_outbound = bool(body.flex_ticket_outbound) or (bool(body.flex_ticket) and not bool(body.flex_ticket_inbound))
    flex_ticket_inbound = bool(body.flex_ticket_inbound)
    # Assurance vol par leg : alias body.insurance = flight_insurance_outbound si non précisé
    flight_insurance_outbound = bool(body.flight_insurance_outbound) or (bool(body.insurance) and not bool(body.flight_insurance_inbound))
    flight_insurance_inbound = bool(body.flight_insurance_inbound)

    if cabin_premium_outbound:
        options_total += 15 * n_pax
    if cabin_premium_inbound:
        options_total += 15 * n_pax
    if flex_ticket_outbound:
        options_total += 49 * n_pax
    if flex_ticket_inbound:
        options_total += 49 * n_pax
    # Assurance vol : 12€ par leg/pax (aligné sejour.html allbyleg). L'ancien
    # code utilisait 35€/pax pour `insurance` (multirisque séjour). En vol seul
    # on bascule sur la valeur par-leg si fournie, sinon on garde 35 pour rétrocompat.
    if body.flight_insurance_outbound or body.flight_insurance_inbound:
        if flight_insurance_outbound:
            options_total += 12 * n_pax
        if flight_insurance_inbound:
            options_total += 12 * n_pax
    elif body.insurance:
        # Rétrocompat ancien front (vol seul mono-leg)
        options_total += 35 * n_pax

    # Transfer flat legacy (oneway/roundtrip) — seulement si pas de transfer HBX par-leg
    transfer_outbound_price_extra = float(body.transfer_outbound_price or 0)
    transfer_inbound_price_extra = float(body.transfer_inbound_price or 0)
    if transfer_outbound_price_extra > 0 or transfer_inbound_price_extra > 0:
        options_total += transfer_outbound_price_extra + transfer_inbound_price_extra
    else:
        # Anciens flats (alias rétrocompat)
        if body.transfer == "oneway":
            options_total += 35
        elif body.transfer == "roundtrip":
            options_total += 60
        # Transfer HBX legacy (alias aller)
        transfer_price_extra = float(body.transfer_price or 0)
        options_total += transfer_price_extra
        transfer_outbound_price_extra = transfer_price_extra  # alias

    # Sanity-check : si l'offer est en DB, on compare prix vol seul (total - options)
    if not is_mock:
        deal_price = float(offer_snapshot.get("price") or 0)
        if deal_price > 0:
            flight_only = body.total_eur - options_total
            expected_min = deal_price * n_pax * 0.95
            expected_max = deal_price * n_pax + 500
            # En aller-retour, le prix DB est souvent celui A/R complet (pas × n_pax).
            # On élargit donc la tolérance haute pour ne pas faire échouer.
            if body.is_roundtrip:
                expected_max = deal_price * n_pax * 2.2 + 500
            if flight_only < expected_min or flight_only > expected_max:
                raise HTTPException(
                    400,
                    f"Total vol {flight_only}€ (hors options {options_total}€) ne correspond pas au deal {deal_price}€ × {n_pax}",
                )

    # Services Duffel réels (P1 juridique Pascal 2026-05-26) — sanitization
    # Audit 2026-05-27 sev 2 #23 : Valide format Duffel `ase_*` (vrai svc Duffel).
    # Refuse mock seat IDs (`seat_*`) — sinon billet sans siège (422 Duffel).
    duffel_services_clean = []
    rejected_mock_svc_ids = []
    if body.duffel_services:
        for svc in body.duffel_services:
            if not isinstance(svc, dict):
                continue
            sid = svc.get("id")
            if not sid or not isinstance(sid, str):
                continue
            # Audit 2026-05-27 sev 2 #23 : skip mock IDs (seat_, mock_, etc.)
            if not sid.startswith("ase_"):
                rejected_mock_svc_ids.append(sid)
                continue
            try:
                qty = int(svc.get("quantity", 1) or 1)
            except (TypeError, ValueError):
                qty = 1
            duffel_services_clean.append({"id": sid, "quantity": max(1, qty)})
    if rejected_mock_svc_ids and not is_mock:
        print(f"[flight.pi] duffel_services mock IDs rejected ({len(rejected_mock_svc_ids)}): {rejected_mock_svc_ids[:5]}")

    # Sièges sélectionnés par leg — sanitization
    selected_seats_clean = {"outbound": {}, "inbound": {}}
    if isinstance(body.selected_seats, dict):
        for leg in ("outbound", "inbound"):
            leg_seats = body.selected_seats.get(leg) or {}
            if isinstance(leg_seats, dict):
                for pax_idx, sid in leg_seats.items():
                    if sid and isinstance(sid, str):
                        try:
                            selected_seats_clean[leg][str(int(pax_idx))] = sid[:64]
                        except (TypeError, ValueError):
                            continue

    # Bloc options à persister dans raw_offer JSONB
    options_block = {
        # Alias rétrocompat
        "baggage_per_passenger": baggage_clean if baggage_clean else None,
        "cabin_premium": cabin_premium_outbound,
        "flex_ticket": flex_ticket_outbound,
        "insurance": bool(body.insurance),
        "transfer": body.transfer if body.transfer in ("oneway", "roundtrip") else "none",
        "options_total_eur": float(options_total),
        "transfer_rate_key": body.transfer_rate_key or body.transfer_outbound_rate_key or None,
        "transfer_price_eur": transfer_outbound_price_extra,
        "transfer_label": body.transfer_label or body.transfer_outbound_label or None,
        "transfer_meta": body.transfer_meta or body.transfer_outbound_meta or None,
        # Services Duffel réels (consommés par webhook → create_order)
        "duffel_services": duffel_services_clean,
        # ── Par leg (Pascal 2026-05-26 align sejour allbyleg) ──
        "is_roundtrip": bool(body.is_roundtrip),
        "baggage_outbound_per_passenger": baggage_outbound_clean if baggage_outbound_clean else None,
        "baggage_inbound_per_passenger": baggage_inbound_clean if baggage_inbound_clean else None,
        "cabin_premium_outbound": cabin_premium_outbound,
        "cabin_premium_inbound": cabin_premium_inbound,
        "flex_ticket_outbound": flex_ticket_outbound,
        "flex_ticket_inbound": flex_ticket_inbound,
        "flight_insurance_outbound": flight_insurance_outbound,
        "flight_insurance_inbound": flight_insurance_inbound,
        "transfer_outbound": body.transfer_outbound if body.transfer_outbound in ("with_transfer", "none") else "none",
        "transfer_outbound_rate_key": body.transfer_outbound_rate_key or None,
        "transfer_outbound_price_eur": transfer_outbound_price_extra,
        "transfer_outbound_label": body.transfer_outbound_label or None,
        "transfer_outbound_meta": body.transfer_outbound_meta or None,
        "transfer_outbound_address": body.transfer_outbound_address or None,
        "transfer_inbound": body.transfer_inbound if body.transfer_inbound in ("with_transfer", "none") else "none",
        "transfer_inbound_rate_key": body.transfer_inbound_rate_key or None,
        "transfer_inbound_price_eur": transfer_inbound_price_extra,
        "transfer_inbound_label": body.transfer_inbound_label or None,
        "transfer_inbound_meta": body.transfer_inbound_meta or None,
        "transfer_inbound_address": body.transfer_inbound_address or None,
        "selected_seats": selected_seats_clean,
    }
    # Concierge hôtelier (résa pour client) — top-level dans raw_offer pour query rapide
    on_behalf_of_payload = None
    if body.on_behalf_of:
        on_behalf_of_payload = {
            "hotel_code": str(body.on_behalf_of),
            "guest_name": body.guest_name or None,
            "guest_email": body.guest_email or None,
            "guest_ref": body.guest_ref or None,
        }

    import uuid as _u
    airbizness_ref = f"AB-FL-{_u.uuid4().hex[:6].upper()}"

    # Idempotency-Key fournie par le client (optionnel) sinon par ref
    idem_key = request.headers.get("Idempotency-Key") or f"flight-{airbizness_ref}"

    try:
        # Audit 2026-05-27 sev 4 #52 : capture_method='manual' si flag activé.
        # Mode manual : Stripe AUTORISE (pas de débit) → on tente Duffel →
        # succès = .capture() ; échec = .cancel() (0 frais Stripe).
        _pi_kwargs = {
            "amount": int(round(body.total_eur * 100)),
            "currency": body.currency.lower(),
            "automatic_payment_methods": {"enabled": True},
            "metadata": {
                "airbizness_ref": airbizness_ref,
                "offer_id": body.offer_id,
                "user_email": body.user_email,
                "type": "flight",
                "is_mock": "1" if is_mock else "0",
                "passenger_count": str(len(body.passengers)),
                "options_total_eur": f"{options_total:.2f}",
                "capture_mode": "manual" if STRIPE_CAPTURE_MANUAL else "automatic",
            },
            "receipt_email": body.user_email,
            "description": f"AirBizness Vol · {offer_snapshot.get('origin','?')}→{offer_snapshot.get('destination','?')} · {len(body.passengers)} pax",
            "idempotency_key": idem_key,
        }
        if STRIPE_CAPTURE_MANUAL:
            _pi_kwargs["capture_method"] = "manual"
        intent = stripe.PaymentIntent.create(**_pi_kwargs)
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")

    # INSERT flight_bookings
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            dep_at = offer_snapshot.get("departure_at")
            if hasattr(dep_at, "isoformat"):
                dep_at_param = dep_at
            elif isinstance(dep_at, str) and dep_at:
                dep_at_param = dep_at
            else:
                dep_at_param = None
            cur.execute("""
                INSERT INTO flight_bookings (
                  airbizness_ref, offer_id, origin, destination,
                  airline_name, airline_code, departure_at, duration_minutes,
                  cabin_class, passengers, user_email, total_eur, currency,
                  status, stripe_payment_intent, is_mock, raw_offer
                ) VALUES (
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s::jsonb, %s, %s, %s,
                  'payment_pending', %s, %s, %s::jsonb
                )
            """, (
                airbizness_ref, body.offer_id,
                offer_snapshot.get("origin"), offer_snapshot.get("destination"),
                offer_snapshot.get("airline_name"), offer_snapshot.get("airline_code"),
                dep_at_param, offer_snapshot.get("duration_minutes"),
                offer_snapshot.get("cabin_class") or "economy",
                json.dumps([p.dict() for p in body.passengers]),
                body.user_email, body.total_eur, body.currency.upper(),
                intent.id, is_mock,
                json.dumps({
                    **(_serialize_deal(offer_snapshot) if not is_mock else json.loads(json.dumps(offer_snapshot, default=str))),
                    "options": options_block,
                    # Audit 2026-05-27 critique #19/#37 : passenger_ids live offer
                    # → utilisés par webhook Stripe pour mapper pax client → pax Duffel.
                    "passenger_ids": live_passenger_ids,
                    **({"on_behalf_of": on_behalf_of_payload} if on_behalf_of_payload else {}),
                }),
            ))
    except Exception as e:
        print(f"[flight.pi] INSERT fail: {e}")
        # On continue, le webhook stripe pourra réconcilier

    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "airbizness_ref": airbizness_ref,
        "is_mock": is_mock,
        "amount_cents": int(round(body.total_eur * 100)),
        "options_total_eur": float(options_total),
        "flight_total_eur": float(body.total_eur - options_total),
    }


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    """Reçoit les events Stripe. Best-effort : on log et update DB."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Vérification signature (si secret configuré)
    # Audit 2026-05-27 sev 4 #55 : si DUFFEL_MODE=live OU APP_ENV=production
    # et STRIPE_WEBHOOK_SECRET absent → REFUSE le payload (n'importe qui
    # pourrait sinon marquer des bookings comme paid via faux webhook).
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header,
                                                    STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            print(f"[stripe-webhook] sig invalid: {e}")
            raise HTTPException(400, "Signature invalide")
    else:
        is_prod_like = (
            os.getenv("DUFFEL_MODE", "test").lower() == "live"
            or os.getenv("APP_ENV", "").lower() in ("prod", "production")
        )
        if is_prod_like:
            _alert_telegram(
                "🔥 stripe-webhook REJET : STRIPE_WEBHOOK_SECRET absent en prod. "
                "Tout payload sans signature est rejeté (audit #55)."
            )
            raise HTTPException(
                400,
                "STRIPE_WEBHOOK_SECRET requis en mode live/prod (refus payload).",
            )
        # Mode test/dev uniquement : accepte tel quel (avec alerte explicite)
        print("[stripe-webhook] WARN: STRIPE_WEBHOOK_SECRET absent en mode test, payload accepté tel quel")
        try:
            import json as _json
            event = _json.loads(payload)
        except Exception:
            raise HTTPException(400, "Payload invalide")

    event_type = event.get("type") if isinstance(event, dict) else event["type"]
    data = (event.get("data", {}) if isinstance(event, dict) else event["data"]).get("object", {})

    # Audit 2026-05-27 sev 3 : webhook idempotency Stripe events.
    # Stripe peut retry (réseau, 5xx, etc.). Sans dedup, un double event
    # peut faire deux create_order Duffel — l'idempotency-key Duffel filtre
    # le 2e mais on perd de la latence + log/alert bruyant.
    stripe_event_id = event.get("id") if isinstance(event, dict) else event.get("id")
    if stripe_event_id and _stripe_event_seen(stripe_event_id, event_type):
        print(f"[stripe-webhook] dedup event_id={stripe_event_id} type={event_type}")
        return {"received": True, "deduped": True, "event_id": stripe_event_id}

    print(f"[stripe-webhook] event={event_type} id={data.get('id', '?')} evid={stripe_event_id}")

    # Audit 2026-05-27 sev 4 #52 : Manual capture flow.
    # En manual capture, Stripe envoie `payment_intent.amount_capturable_updated`
    # quand le client confirme la CB (autorisation OK, pas encore débité).
    # On bascule ce flow vers le bloc Duffel booking, et on capturera après.
    # Note : `payment_intent.succeeded` reste utilisé en mode automatic ET en
    # mode manual après .capture() — donc nos blocs DOIVENT être idempotent.
    if event_type == "payment_intent.amount_capturable_updated":
        # On marque ce flux comme "trigger booking" → on rejoue la logique du
        # `payment_intent.succeeded` (Duffel create_order, hotel create_booking,
        # etc.). Avec en plus la capture() après chaque succès.
        event_type = "payment_intent.succeeded"
        data["_manual_capture_flow"] = True

    # Mapping airbizness_ref via metadata
    airbizness_ref = (data.get("metadata") or {}).get("airbizness_ref")
    if not airbizness_ref:
        # event sans metadata airbizness → ignore (autre marchand sur ce compte)
        _stripe_event_mark_processed(stripe_event_id)
        return {"received": True, "skipped": "no_airbizness_ref"}

    # Détecte vertical (flight vs hotel vs activity) via metadata
    md_type = (data.get("metadata") or {}).get("type", "")

    flight_booking_result = None
    hotel_booking_result = None
    activity_booking_result = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            if event_type == "payment_intent.succeeded":
                cur.execute("""
                    UPDATE bookings_v2 SET payment_status='succeeded', payment_at=NOW()
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
                cur.execute("""
                    UPDATE flight_bookings SET status='payment_succeeded'
                    WHERE airbizness_ref=%s AND status='payment_pending'
                """, (airbizness_ref,))
            elif event_type == "payment_intent.payment_failed":
                cur.execute("""
                    UPDATE bookings_v2 SET payment_status='failed',
                                          status='payment_failed'
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
                cur.execute("""
                    UPDATE flight_bookings SET status='payment_failed'
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
            elif event_type in ("charge.refunded", "refund.created"):
                cur.execute("""
                    UPDATE bookings_v2 SET payment_status='refunded'
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
            elif event_type == "charge.dispute.created":
                cur.execute("""
                    UPDATE bookings_v2 SET status='disputed'
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))

            # ─── FLIGHT BOOKING : déclenche Duffel create_order ───────────
            # P0 Duffel-compliance (2026-05-26) : sans ce bloc, le client paie
            # mais AUCUN billet n'est émis. Idempotent : si duffel_order_id
            # est déjà set, on ne re-book pas.
            if event_type == "payment_intent.succeeded":
                cur.execute("""
                    SELECT airbizness_ref, offer_id, passengers, raw_offer,
                           total_eur, currency, is_mock, duffel_order_id,
                           user_email
                    FROM flight_bookings
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
                fb_row = cur.fetchone()
                if fb_row:
                    (ab_ref, offer_id, passengers_data, raw_offer,
                     total_amount, currency, is_mock, existing_order_id,
                     user_email) = fb_row

                    if existing_order_id:
                        print(f"[stripe-webhook] flight idempotent ab_ref={ab_ref} order={existing_order_id}")
                    elif is_mock or os.environ.get("DUFFEL_BOOKING_DRY_RUN", "false").lower() == "true":
                        print(f"[stripe-webhook] flight mock/dry-run ab_ref={ab_ref}, skip Duffel")
                    else:
                        # Décompose passagers + services depuis raw_offer.options
                        pax = passengers_data or []
                        if isinstance(pax, str):
                            try: pax = json.loads(pax)
                            except Exception: pax = []
                        opts = raw_offer or {}
                        if isinstance(opts, str):
                            try: opts = json.loads(opts)
                            except Exception: opts = {}
                        options_data = (opts.get("options") if isinstance(opts, dict) else {}) or {}

                        # Build Duffel passengers payload : merge passenger_ids + info
                        # raw_offer.passenger_ids vient du checkrate (ordre passager)
                        passenger_ids = (opts.get("passenger_ids") or []) if isinstance(opts, dict) else []
                        duffel_passengers = []
                        # Audit 2026-05-27 critique #38 : born_on doit être la VRAIE DOB,
                        # plus de fallback "1990-01-01". Si une seule DOB est invalide
                        # on raise avant create_order pour déclencher le refund auto.
                        from providers.duffel import _is_valid_dob as _is_valid_dob_main
                        from providers.duffel import _normalize_phone_e164 as _norm_e164_main
                        from providers.duffel import _infer_passenger_type as _infer_ptype_main
                        dob_errors = []
                        phone_errors = []
                        for i, info in enumerate(pax):
                            if not isinstance(info, dict):
                                continue
                            pid = info.get("duffel_id") or (passenger_ids[i] if i < len(passenger_ids) else None)
                            born_on = (info.get("born_on") or info.get("dateOfBirth") or info.get("dob") or "").strip()
                            if not _is_valid_dob_main(born_on):
                                dob_errors.append(f"pax#{i+1} DOB invalide: {born_on!r}")
                            # Audit 2026-05-27 sev 4 #41 : E.164 phone
                            raw_phone = info.get("phone_number") or info.get("phone", "")
                            phone_e164 = _norm_e164_main(raw_phone)
                            if not phone_e164:
                                phone_errors.append(f"pax#{i+1} phone invalide: {raw_phone!r}")
                            # Audit 2026-05-27 sev 3 #42 : gender déduit du title
                            # (mr→m, mrs/ms/miss→f) à défaut de gender explicite.
                            _t_low = (info.get("title") or "mr").lower()
                            _gender_inferred = "f" if _t_low in ("mrs", "ms", "miss") else "m"
                            entry = {
                                "title": _t_low,
                                "given_name": info.get("given_name") or info.get("firstName", ""),
                                "family_name": info.get("family_name") or info.get("lastName", ""),
                                "born_on": born_on,
                                "email": info.get("email") or user_email or "",
                                "phone_number": phone_e164 or raw_phone or "",
                                "gender": (info.get("gender") or _gender_inferred).lower(),
                                # Audit 2026-05-27 sev 4 #39 : type Duffel
                                "type": info.get("type") or _infer_ptype_main(born_on),
                            }
                            if pid:
                                entry["id"] = pid
                            duffel_passengers.append(entry)
                        if dob_errors:
                            raise ValueError("DOB invalide(s) — booking refusé Duffel: " + " | ".join(dob_errors))
                        if phone_errors:
                            raise ValueError("phone_number invalide(s) — Duffel E.164 requis: " + " | ".join(phone_errors))

                        # Services Duffel sélectionnés (bagages/sièges/etc.)
                        selected_services = []
                        for svc in (options_data.get("duffel_services") or []):
                            if not isinstance(svc, dict) or not svc.get("id"):
                                continue
                            selected_services.append({
                                "id": svc["id"],
                                "quantity": int(svc.get("quantity", 1) or 1),
                            })

                        # Enrich passengers avec identity_documents si fournis
                        # par le front (passport_number/expiry/nationality).
                        # Required par Duffel pour vols intl.
                        for i, info in enumerate(pax):
                            if not isinstance(info, dict) or i >= len(duffel_passengers):
                                continue
                            pn = (info.get("passportNumber") or info.get("passport_number") or "").strip()
                            px = (info.get("passportExpiry") or info.get("passport_expiry") or "").strip()
                            nat = (info.get("nationality") or "").upper()
                            if pn and px and nat:
                                duffel_passengers[i]["identity_documents"] = [{
                                    "type": "passport",
                                    "unique_identifier": pn,
                                    "expires_on": str(px),
                                    # Audit 2026-05-27 critique #30 : alpha-2 (FR, GB) pas alpha-3.
                                    "issuing_country_code": nat[:2],
                                }]

                        try:
                            from providers.duffel import create_order
                            # Audit 2026-05-27 sev 4 #84 : persist idempotency_key
                            duffel_idem = f"order-{ab_ref}-{(offer_id or '')[-12:]}"
                            order = create_order(
                                offer_id=offer_id,
                                passengers=duffel_passengers,
                                total_amount=float(total_amount or 0),
                                currency=(currency or "EUR").upper(),
                                services=selected_services or None,
                                metadata={
                                    "airbizness_ref": ab_ref,
                                    "stripe_pi": data.get("id", ""),
                                },
                                idempotency_key=duffel_idem,
                            )
                            order_id = order.get("id")
                            booking_ref = order.get("booking_reference")
                            cur.execute("""
                                UPDATE flight_bookings
                                SET status='booked',
                                    duffel_order_id=%s,
                                    pnr=%s,
                                    booking_reference=%s,
                                    duffel_documents=%s::jsonb,
                                    raw_order=%s::jsonb,
                                    booked_at=NOW(),
                                    confirmed_at=NOW(),
                                    booking_error=NULL,
                                    duffel_idempotency_key=%s
                                WHERE airbizness_ref=%s
                            """, (order_id, booking_ref, booking_ref,
                                  json.dumps(order.get("documents") or []),
                                  json.dumps(order, default=str),
                                  duffel_idem, ab_ref))
                            flight_booking_result = {"ab_ref": ab_ref, "order": order}
                            print(f"[stripe-webhook] Duffel order created ab_ref={ab_ref} order={order_id} pnr={booking_ref}")
                            # Audit 2026-05-27 sev 4 #52 : capture le PI Stripe
                            # APRÈS confirmation Duffel (manual capture mode).
                            # En automatic mode, c'est un no-op idempotent.
                            try:
                                _pi_id = data.get("id", "")
                                if _pi_id and (STRIPE_CAPTURE_MANUAL
                                                or data.get("_manual_capture_flow")):
                                    _cap_res = _stripe_capture_intent(_pi_id, ab_ref=ab_ref)
                                    if not _cap_res.get("ok"):
                                        _alert_telegram(
                                            f"🔥 Vol {ab_ref}: Duffel OK mais capture Stripe FAIL "
                                            f"({_cap_res.get('error')}). Pi={_pi_id}. "
                                            f"Action: capture manuelle dashboard Stripe."
                                        )
                            except Exception as _e_cap:
                                print(f"[stripe-webhook] capture err ab_ref={ab_ref}: {_e_cap}")
                        except Exception as e:
                            err = str(e)[:500]
                            print(f"[stripe-webhook] DUFFEL create_order FAIL ab_ref={ab_ref}: {err}")
                            # Audit 2026-05-27 sev 5 #47 : détection spécifique
                            # `insufficient_balance` (compte Duffel à zéro). Sans
                            # ce catch, on perd des bookings sans visibilité.
                            duffel_err_code = None
                            try:
                                from providers.duffel import DuffelHttpError as _DHE
                                if isinstance(e, _DHE):
                                    duffel_err_code = e.code()
                            except Exception:
                                pass
                            if duffel_err_code in ("insufficient_balance",
                                                    "balance_low",
                                                    "insufficient_funds"):
                                _alert_telegram(
                                    f"🔥🔥🔥 DUFFEL BALANCE EPUISÉE — vol {ab_ref} bloqué. "
                                    f"RECHARGE DUFFEL IMMÉDIAT. Tous les nouveaux bookings vont fail."
                                )
                            elif duffel_err_code == "offer_no_longer_available":
                                # Audit 2026-05-27 sev 3 #45 : tag spécifique
                                _alert_telegram(
                                    f"⚠️ Vol {ab_ref} : offer_no_longer_available "
                                    f"— le vol a été pris pendant le checkout. Refund auto."
                                )
                            # ── P0 Fix A : refund auto Stripe + mail client + alert ──
                            # Audit 2026-05-27 sev 4 #52 : si manual capture
                            # mode, on CANCEL l'autorisation au lieu de refund
                            # (0 frais Stripe au lieu de 6%). _stripe_cancel
                            # bascule auto vers refund si PI déjà succeeded.
                            _pi_id_fail = data.get("id", "")
                            if STRIPE_CAPTURE_MANUAL or data.get("_manual_capture_flow"):
                                refund_res = _stripe_cancel_intent(
                                    _pi_id_fail,
                                    ab_ref=ab_ref,
                                    reason=f"duffel_booking_failed:{duffel_err_code or 'unknown'}",
                                )
                                # Normalise réponse pour code aval
                                if refund_res.get("ok") and "refund_id" not in refund_res:
                                    refund_res.setdefault("refund_id", None)
                                    refund_res.setdefault("amount",
                                                          float(total_amount or 0))
                            else:
                                refund_res = _stripe_refund_auto(
                                    _pi_id_fail,
                                    airbizness_ref=ab_ref,
                                    reason=f"duffel_booking_failed:{duffel_err_code or 'unknown'}",
                                    error_excerpt=err,
                                )
                            try:
                                if refund_res["ok"]:
                                    cur.execute("""
                                        UPDATE flight_bookings
                                        SET status='payment_succeeded_duffel_failed_refunded',
                                            booking_error=%s,
                                            failure_reason=%s,
                                            refund_id=%s,
                                            refund_amount=%s,
                                            refunded_at=NOW()
                                        WHERE airbizness_ref=%s
                                    """, (err, err, refund_res["refund_id"],
                                          refund_res["amount"], ab_ref))
                                else:
                                    cur.execute("""
                                        UPDATE flight_bookings
                                        SET status='payment_succeeded_booking_failed_refund_failed',
                                            booking_error=%s,
                                            failure_reason=%s
                                        WHERE airbizness_ref=%s
                                    """, (f"Duffel: {err} | Refund: {refund_res['error']}",
                                          f"Duffel: {err} | Refund: {refund_res['error']}",
                                          ab_ref))
                            except Exception as e2:
                                print(f"[stripe-webhook] flight failure UPDATE fail: {e2}")
                            if refund_res["ok"]:
                                _alert_telegram(
                                    f"⚠️ Vol {ab_ref} : Duffel FAIL → refund {refund_res['amount']}€ auto OK ({err})"
                                )
                                # Mail client (best-effort, hors txn)
                                try:
                                    _send_booking_failed_mail(
                                        ab_ref,
                                        to_email=user_email or "",
                                        to_name="",
                                        refund_amount=refund_res["amount"] or 0.0,
                                        original_amount=float(total_amount or 0),
                                        reason="vol_indisponible",
                                    )
                                except Exception as _e_m:
                                    print(f"[stripe-webhook] flight fail-mail err: {_e_m}")
                            else:
                                _alert_telegram(
                                    f"🔥 URGENT {ab_ref} : Duffel FAIL ET Refund FAIL — fix manuel requis "
                                    f"Stripe+DB. Duffel: {err}. Refund: {refund_res['error']}"
                                )

            # ─── PACK BOOKING : Duffel create_order PUIS HBX create_booking ──
            # P0 Duffel-compliance Pack (2026-05-26) : sans ce bloc, client paie
            # mais ni vol ni hôtel ne sont bookés (le frontend doit appeler
            # /pack/confirm explicitement, ce qui peut être loupé).
            # Idempotent : skip si déjà status='confirmed' ou si duffel_order_id existe.
            # Métadata stripe : type='pack' + airbizness_ref.
            if event_type == "payment_intent.succeeded" and md_type == "pack":
                pcur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                pcur.execute("""
                    SELECT * FROM pack_bookings
                    WHERE airbizness_ref=%s
                """, (airbizness_ref,))
                p_row = pcur.fetchone()
                pcur.close()
                if p_row:
                    p_dict = dict(p_row)
                    if p_dict.get("status") == "confirmed":
                        print(f"[stripe-webhook] pack idempotent ab_ref={airbizness_ref} status=confirmed")
                    elif p_dict.get("duffel_order_id"):
                        print(f"[stripe-webhook] pack idempotent ab_ref={airbizness_ref} duffel={p_dict.get('duffel_order_id')}")
                    else:
                        # Mark payment as succeeded immédiatement
                        try:
                            with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                pcur2.execute("""
                                    UPDATE pack_bookings
                                    SET payment_status='succeeded', payment_at=NOW()
                                    WHERE airbizness_ref=%s
                                """, (airbizness_ref,))
                        except Exception as _e_up:
                            print(f"[stripe-webhook] pack payment_status update fail: {_e_up}")

                        # Récupère options pack (services Duffel + transfer + return offer)
                        raw_extras = p_dict.get("raw_payload") or {}
                        if isinstance(raw_extras, str):
                            try: raw_extras = json.loads(raw_extras)
                            except Exception: raw_extras = {}

                        # Détection mock pack (pas d'appel Duffel/HBX réel)
                        rk_in = (p_dict.get("hotel_rate_key") or "")
                        ot_in = (p_dict.get("flight_offer_token") or "")
                        pack_is_mock = (
                            bool(raw_extras.get("is_mock"))
                            or rk_in.upper().startswith("MOCK-")
                            or rk_in.upper().startswith("HBX:MOCK-")
                            or ot_in.upper().startswith("MOCK-")
                            or not rk_in
                            or os.environ.get("DUFFEL_BOOKING_DRY_RUN", "false").lower() == "true"
                        )

                        if pack_is_mock:
                            # MOCK : confirme directement (cohérent avec /pack/confirm mock branch)
                            mock_pnr = "MK" + airbizness_ref.split("-")[-1]
                            mock_hbx = "MOCK-HBX-" + airbizness_ref.split("-")[-1]
                            try:
                                with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                    pcur2.execute("""
                                        UPDATE pack_bookings SET
                                            status='confirmed', confirmed_at=NOW(),
                                            duffel_pnr=%s, duffel_order_id=%s,
                                            flight_status='confirmed', flight_booked_at=NOW(),
                                            hbx_reference=%s, hotel_status='confirmed',
                                            hotel_booked_at=NOW()
                                        WHERE airbizness_ref=%s
                                    """, (mock_pnr, "ord_MOCK_" + airbizness_ref[-6:],
                                          mock_hbx, airbizness_ref))
                                print(f"[stripe-webhook] pack mock booked ab_ref={airbizness_ref}")
                            except Exception as _e_mk:
                                print(f"[stripe-webhook] pack mock update fail: {_e_mk}")
                        else:
                            # ── REAL : Duffel d'abord, HBX ensuite ──
                            duffel_offer_id = p_dict.get("duffel_offer_id") or p_dict.get("flight_offer_token")
                            duffel_passenger_ids = p_dict.get("duffel_passenger_ids") or []
                            if isinstance(duffel_passenger_ids, str):
                                try: duffel_passenger_ids = json.loads(duffel_passenger_ids)
                                except Exception: duffel_passenger_ids = []

                            # Build duffel_passengers depuis raw_extras.passengers ou holder
                            pax_raw = raw_extras.get("passengers") or []
                            duffel_passengers = []
                            # Audit 2026-05-27 critique #38 : DOB strict — pas de fallback.
                            from providers.duffel import _is_valid_dob as _is_valid_dob_pack
                            from providers.duffel import _normalize_phone_e164 as _norm_e164_pack
                            from providers.duffel import _infer_passenger_type as _infer_ptype_pack
                            pack_dob_errors = []
                            pack_phone_errors = []
                            for i, info in enumerate(pax_raw):
                                if not isinstance(info, dict):
                                    continue
                                pid = info.get("duffel_id") or (duffel_passenger_ids[i] if i < len(duffel_passenger_ids) else None)
                                born_on = (info.get("born_on") or info.get("dateOfBirth") or info.get("dob") or "").strip()
                                if not _is_valid_dob_pack(born_on):
                                    pack_dob_errors.append(f"pax#{i+1} DOB invalide: {born_on!r}")
                                # Sev 4 #41
                                raw_phone_p = info.get("phone_number") or info.get("phone") or p_dict.get("user_phone") or ""
                                phone_e164_p = _norm_e164_pack(raw_phone_p)
                                if not phone_e164_p:
                                    pack_phone_errors.append(f"pax#{i+1} phone invalide: {raw_phone_p!r}")
                                entry = {
                                    "title": (info.get("title") or "mr").lower(),
                                    "given_name": info.get("given_name") or info.get("firstName") or p_dict.get("holder_name") or "",
                                    "family_name": info.get("family_name") or info.get("lastName") or p_dict.get("holder_surname") or "",
                                    "born_on": born_on,
                                    "email": info.get("email") or p_dict.get("user_email") or "",
                                    "phone_number": phone_e164_p or raw_phone_p or "",
                                    "gender": (info.get("gender") or "m").lower(),
                                    # Sev 4 #39
                                    "type": info.get("type") or _infer_ptype_pack(born_on),
                                }
                                if pid:
                                    entry["id"] = pid
                                duffel_passengers.append(entry)
                            if pack_phone_errors:
                                # Concatène avec DOB errors pour faire un seul echec lisible
                                pack_dob_errors.extend(pack_phone_errors)

                            if pack_dob_errors:
                                # Audit 2026-05-27 critique #38 : refuse booking si DOB invalide
                                # → raise pour déclencher refund auto + alerte.
                                duffel_failed_reason = "DOB invalide(s): " + " | ".join(pack_dob_errors)
                                duffel_passengers = []  # force skip Duffel call ci-dessous

                            if not duffel_passengers and not pack_dob_errors:
                                # Fallback : holder seul (cohérent avec /pack/confirm)
                                # NB : nécessite holder_dob présent côté raw_extras.holder
                                holder_dob = (raw_extras.get("holder", {}) if isinstance(raw_extras.get("holder"), dict) else {}).get("dob") or p_dict.get("holder_dob")
                                if not _is_valid_dob_pack(holder_dob or ""):
                                    duffel_failed_reason = "Pack fallback : holder_dob manquant ou invalide pour booking Duffel."
                                else:
                                    fallback_pax = {
                                        "title": "mr",
                                        "given_name": p_dict.get("holder_name") or "",
                                        "family_name": p_dict.get("holder_surname") or "",
                                        "born_on": holder_dob,
                                        "email": p_dict.get("user_email") or "",
                                        "phone_number": p_dict.get("user_phone") or "+33000000000",
                                        "gender": "m",
                                    }
                                    duffel_passengers = [fallback_pax]
                                    while len(duffel_passengers) < len(duffel_passenger_ids):
                                        duffel_passengers.append({**fallback_pax})
                                    # Assigne IDs
                                    for i, pid in enumerate(duffel_passenger_ids):
                                        if i < len(duffel_passengers) and pid:
                                            duffel_passengers[i]["id"] = pid

                            # Services Duffel sélectionnés (bagages/sièges) depuis raw_extras
                            selected_services = []
                            for svc in (raw_extras.get("duffel_services") or []):
                                if not isinstance(svc, dict) or not svc.get("id"):
                                    continue
                                selected_services.append({
                                    "id": svc["id"],
                                    "quantity": int(svc.get("quantity", 1) or 1),
                                })

                            # ── 1) Duffel create_order ──
                            duffel_order_obj = None
                            duffel_order_id_new = None
                            duffel_pnr_new = None
                            # NE PAS reset duffel_failed_reason — il peut déjà être set
                            # par le DOB validator ci-dessus (audit 2026-05-27 crit #38).
                            # Enrich passengers avec identity_documents (passeport)
                            # depuis raw_extras.passengers — requis vols intl.
                            for i, info in enumerate(pax_raw):
                                if not isinstance(info, dict) or i >= len(duffel_passengers):
                                    continue
                                pn = (info.get("passportNumber") or info.get("passport_number") or "").strip()
                                px = (info.get("passportExpiry") or info.get("passport_expiry") or "").strip()
                                nat = (info.get("nationality") or "").upper()
                                if pn and px and nat:
                                    duffel_passengers[i]["identity_documents"] = [{
                                        "type": "passport",
                                        "unique_identifier": pn,
                                        "expires_on": str(px),
                                        # Audit 2026-05-27 critique #30 : alpha-2 obligatoire.
                                        "issuing_country_code": nat[:2],
                                    }]

                            # Audit 2026-05-27 critique #38 : skip Duffel call si DOB déjà KO
                            if duffel_failed_reason:
                                print(f"[stripe-webhook] PACK SKIP DUFFEL (pre-check failed) ab_ref={airbizness_ref}: {duffel_failed_reason}")
                            else:
                                try:
                                    from providers.duffel import create_order as _duffel_create_order
                                    duffel_order_obj = _duffel_create_order(
                                        offer_id=duffel_offer_id,
                                        passengers=duffel_passengers,
                                        total_amount=float(p_dict.get("flight_price") or 0),
                                        currency=(p_dict.get("currency") or "EUR"),
                                        services=selected_services or None,
                                        metadata={
                                            "airbizness_ref": airbizness_ref,
                                            "stripe_pi": data.get("id", ""),
                                            "type": "pack",
                                        },
                                        # Audit 2026-05-27 : Idempotency-Key + pre-check
                                        idempotency_key=f"order-{airbizness_ref}-{(duffel_offer_id or '')[-12:]}",
                                    )
                                    duffel_order_id_new = duffel_order_obj.get("id")
                                    duffel_pnr_new = duffel_order_obj.get("booking_reference")
                                except Exception as e:
                                    duffel_failed_reason = str(e)[:500]
                                    print(f"[stripe-webhook] PACK DUFFEL FAIL ab_ref={airbizness_ref}: {duffel_failed_reason}")

                            if not duffel_order_id_new:
                                # ÉCHEC VOL → refund TOTAL Stripe + alert Telegram CRITIQUE
                                # Audit 2026-05-27 sev 4 #52 : si manual capture
                                # → cancel (0 frais Stripe) au lieu de refund.
                                refund_id = None
                                _pi_id_pack = data.get("id", "")
                                if (STRIPE_CAPTURE_MANUAL or data.get("_manual_capture_flow")) and _pi_id_pack:
                                    cancel_res = _stripe_cancel_intent(
                                        _pi_id_pack, ab_ref=airbizness_ref,
                                        reason="pack_flight_booking_failed",
                                    )
                                    if cancel_res.get("ok") and cancel_res.get("refund_id"):
                                        refund_id = cancel_res["refund_id"]
                                else:
                                    try:
                                        r = stripe.Refund.create(
                                            payment_intent=_pi_id_pack,
                                            metadata={"airbizness_ref": airbizness_ref,
                                                      "reason": "pack_flight_booking_failed"},
                                        )
                                        refund_id = r.id
                                    except Exception as _e_rf:
                                        print(f"[stripe-webhook] pack Stripe refund fail: {_e_rf}")
                                try:
                                    with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                        pcur2.execute("""
                                            UPDATE pack_bookings SET
                                                status='failed', flight_status='failed',
                                                failure_reason=%s, refund_id=%s,
                                                refund_amount=total_amount, cancelled_at=NOW()
                                            WHERE airbizness_ref=%s
                                        """, (duffel_failed_reason or "duffel_booking_failed",
                                              refund_id, airbizness_ref))
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] pack failed UPDATE fail: {_e_db}")
                                _alert_telegram(
                                    f"❌ PACK DUFFEL FAIL POST-PAYMENT {airbizness_ref}: "
                                    f"{duffel_failed_reason} | Refund Stripe={'OK' if refund_id else 'KO'}"
                                )
                            else:
                                # Vol OK → persiste immédiatement (idempotence)
                                try:
                                    with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                        pcur2.execute("""
                                            UPDATE pack_bookings SET
                                                duffel_order_id=%s, duffel_pnr=%s,
                                                duffel_e_tickets=%s::jsonb,
                                                flight_status='confirmed',
                                                flight_provider='duffel',
                                                flight_booked_at=NOW()
                                            WHERE airbizness_ref=%s
                                        """, (duffel_order_id_new, duffel_pnr_new,
                                              json.dumps((duffel_order_obj or {}).get("documents") or []),
                                              airbizness_ref))
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] pack flight persist fail: {_e_db}")

                                # ── 2) HBX create_booking (hôtel) ──
                                hbx_ref = None
                                hbx_failed_reason = None
                                try:
                                    from providers.hbx.hotels.booking import create_booking as _hbx_create_booking
                                    native_rate = (p_dict.get("hotel_rate_key") or "").removeprefix("hbx:")
                                    hbx_result = _hbx_create_booking(
                                        rate_key=native_rate,
                                        holder_name=p_dict.get("holder_name"),
                                        holder_surname=p_dict.get("holder_surname"),
                                        client_reference=airbizness_ref,
                                    )
                                    hbx_ref = (hbx_result.get("booking") or {}).get("reference") or hbx_result.get("reference")
                                except Exception as e:
                                    hbx_failed_reason = str(e)[:500]
                                    print(f"[stripe-webhook] PACK HBX FAIL ab_ref={airbizness_ref}: {hbx_failed_reason}")

                                if not hbx_ref:
                                    # ÉCHEC HÔTEL APRÈS VOL OK : refund PARTIEL hôtel + alert CRITIQUE
                                    # Pascal DOIT canceler le vol Duffel manuellement ou rembourser le client
                                    hotel_amount = float(p_dict.get("hotel_price") or 0)
                                    refund_id = None
                                    try:
                                        r = stripe.Refund.create(
                                            payment_intent=data.get("id", ""),
                                            amount=int(round(hotel_amount * 100)),
                                            metadata={"airbizness_ref": airbizness_ref,
                                                      "reason": "pack_hotel_failed_partial_refund"},
                                        )
                                        refund_id = r.id
                                    except Exception as _e_rf:
                                        print(f"[stripe-webhook] pack partial refund fail: {_e_rf}")
                                    try:
                                        with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                            pcur2.execute("""
                                                UPDATE pack_bookings SET
                                                    status='partial_confirmed',
                                                    hotel_status='failed',
                                                    refund_id=%s, refund_amount=%s,
                                                    failure_reason=%s
                                                WHERE airbizness_ref=%s
                                            """, (refund_id, hotel_amount,
                                                  f"VOL OK ({duffel_pnr_new}) MAIS HÔTEL FAIL: {hbx_failed_reason}",
                                                  airbizness_ref))
                                    except Exception as _e_db:
                                        print(f"[stripe-webhook] pack partial UPDATE fail: {_e_db}")
                                    _alert_telegram(
                                        f"⚠️ PACK PARTIAL {airbizness_ref}: Duffel OK ({duffel_pnr_new}) "
                                        f"MAIS HBX FAIL: {hbx_failed_reason} | Partial refund={'OK' if refund_id else 'KO'} "
                                        f"({hotel_amount}€) | Vol Duffel reste émis — cancel manuel ou substitut hôtel."
                                    )
                                else:
                                    # ── 3) BOTH OK : finalize confirmed ──
                                    # Audit 2026-05-27 sev 4 #52 : Stripe capture
                                    # APRÈS Duffel+HBX OK (manual capture mode).
                                    try:
                                        _pi_id_pack_ok = data.get("id", "")
                                        if _pi_id_pack_ok and (STRIPE_CAPTURE_MANUAL
                                                                or data.get("_manual_capture_flow")):
                                            _cap = _stripe_capture_intent(
                                                _pi_id_pack_ok, ab_ref=airbizness_ref)
                                            if not _cap.get("ok"):
                                                _alert_telegram(
                                                    f"🔥 PACK {airbizness_ref}: Duffel+HBX OK mais "
                                                    f"capture Stripe FAIL ({_cap.get('error')}). "
                                                    f"Pi={_pi_id_pack_ok}. Capture manuelle Stripe."
                                                )
                                    except Exception as _e_capk:
                                        print(f"[stripe-webhook] pack capture err: {_e_capk}")
                                    try:
                                        with _pack_db_conn() as pconn, pconn.cursor() as pcur2:
                                            pcur2.execute("""
                                                UPDATE pack_bookings SET
                                                    status='confirmed', confirmed_at=NOW(),
                                                    hbx_reference=%s,
                                                    hotel_status='confirmed',
                                                    hotel_booked_at=NOW()
                                                WHERE airbizness_ref=%s
                                            """, (hbx_ref, airbizness_ref))
                                        print(f"[stripe-webhook] PACK confirmed ab_ref={airbizness_ref} "
                                              f"duffel={duffel_order_id_new} pnr={duffel_pnr_new} hbx={hbx_ref}")
                                    except Exception as _e_db:
                                        print(f"[stripe-webhook] pack confirm UPDATE fail: {_e_db}")
                                    # Email confirmation pack (best-effort, hors txn)
                                    try:
                                        _send_pack_confirmation_email(airbizness_ref)
                                    except Exception as _e_mail:
                                        print(f"[stripe-webhook] pack email fail: {_e_mail}")

            # ─── P0 Fix B : HOTEL SEUL auto-confirm HBX ──────────────────
            # Risque sans ce bloc : client paie, mais si le browser ferme
            # AVANT que le front appelle /hbx/booking/confirm → booking en
            # limbo (Stripe OK, HBX ne sait rien).
            # Idempotent : skip si status='confirmed' ET hbx_reference set.
            if event_type == "payment_intent.succeeded" and md_type == "hbx_hotel":
                hcur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                hcur.execute("SELECT * FROM bookings_v2 WHERE airbizness_ref=%s", (airbizness_ref,))
                h_row = hcur.fetchone()
                hcur.close()
                if h_row:
                    h_dict = dict(h_row)
                    if h_dict.get("status") == "confirmed" and h_dict.get("hbx_reference"):
                        print(f"[stripe-webhook] hotel idempotent ab_ref={airbizness_ref} hbx={h_dict.get('hbx_reference')}")
                    else:
                        rate_key = h_dict.get("rate_key_verified") or ""
                        is_mock_rate = _is_mock_rate_key(rate_key) if rate_key else True
                        if is_mock_rate:
                            # ── MOCK : génère réf fictive (cohérent avec /hbx/booking/confirm)
                            mock_ref = f"AB-HT-{_uuid.uuid4().hex[:6].upper()}"
                            try:
                                with conn.cursor() as cur2:
                                    cur2.execute("""
                                        UPDATE bookings_v2 SET status='confirmed',
                                                              hbx_reference=%s,
                                                              payment_status='succeeded',
                                                              payment_at=NOW(),
                                                              confirmed_at=NOW()
                                        WHERE airbizness_ref=%s
                                    """, (mock_ref, airbizness_ref))
                                print(f"[stripe-webhook] hotel mock confirm ab_ref={airbizness_ref} hbx={mock_ref}")
                            except Exception as _e_db:
                                print(f"[stripe-webhook] hotel mock UPDATE fail: {_e_db}")
                        else:
                            hbx_booking = None
                            hbx_err = None
                            try:
                                from providers.hbx.hotels.booking import create_booking as _hbx_create_booking
                                hbx_booking = _hbx_create_booking(
                                    rate_key=rate_key,
                                    holder_name=h_dict.get("holder_name") or "",
                                    holder_surname=h_dict.get("holder_surname") or h_dict.get("holder_name") or "",
                                    client_reference=airbizness_ref,
                                    remark=h_dict.get("remark") or "Réservation AirBizness",
                                )
                            except Exception as e:
                                hbx_err = str(e)[:500]
                                print(f"[stripe-webhook] HOTEL HBX FAIL ab_ref={airbizness_ref}: {hbx_err}")

                            if hbx_booking and hbx_booking.get("reference"):
                                # Audit 2026-05-27 sev 4 #52 : capture Stripe APRÈS HBX OK
                                try:
                                    _pi_id_h = data.get("id", "")
                                    if _pi_id_h and (STRIPE_CAPTURE_MANUAL
                                                      or data.get("_manual_capture_flow")):
                                        _cap = _stripe_capture_intent(_pi_id_h,
                                                                       ab_ref=airbizness_ref)
                                        if not _cap.get("ok"):
                                            _alert_telegram(
                                                f"🔥 HÔTEL {airbizness_ref}: HBX OK mais "
                                                f"capture Stripe FAIL ({_cap.get('error')})."
                                            )
                                except Exception as _e_caph:
                                    print(f"[stripe-webhook] hotel capture err: {_e_caph}")
                                try:
                                    with conn.cursor() as cur2:
                                        cur2.execute("""
                                            UPDATE bookings_v2 SET
                                              status='confirmed',
                                              hbx_reference=%s,
                                              net_price=%s,
                                              payment_status='succeeded',
                                              payment_at=NOW(),
                                              confirmed_at=NOW(),
                                              cancellation_policies=%s::jsonb,
                                              hbx_booking_raw = COALESCE(hbx_booking_raw, '{}'::jsonb)
                                                                || %s::jsonb
                                            WHERE airbizness_ref=%s
                                        """, (
                                            hbx_booking["reference"],
                                            hbx_booking.get("total_net"),
                                            json.dumps(hbx_booking.get("cancellation_policies") or [], default=str),
                                            json.dumps({"hbx_response": hbx_booking.get("raw") or {}}, default=str),
                                            airbizness_ref,
                                        ))
                                    hotel_booking_result = {
                                        "ab_ref": airbizness_ref,
                                        "hbx": hbx_booking,
                                    }
                                    print(f"[stripe-webhook] HOTEL auto-confirm OK ab_ref={airbizness_ref} hbx={hbx_booking['reference']}")
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] hotel auto-confirm UPDATE fail: {_e_db}")
                            else:
                                # ── HBX FAIL → refund/cancel auto + mail client + alert ──
                                # Audit 2026-05-27 sev 4 #52 : cancel si manual capture
                                _pi_id_h_fail = data.get("id", "")
                                if (STRIPE_CAPTURE_MANUAL or data.get("_manual_capture_flow")) and _pi_id_h_fail:
                                    refund_res = _stripe_cancel_intent(
                                        _pi_id_h_fail, ab_ref=airbizness_ref,
                                        reason="hbx_booking_failed",
                                    )
                                    refund_res.setdefault("refund_id", None)
                                    refund_res.setdefault("amount", 0.0)
                                else:
                                    refund_res = _stripe_refund_auto(
                                        _pi_id_h_fail,
                                        airbizness_ref=airbizness_ref,
                                        reason="hbx_booking_failed",
                                        error_excerpt=hbx_err or "hbx_no_reference",
                                    )
                                try:
                                    with conn.cursor() as cur2:
                                        if refund_res["ok"]:
                                            cur2.execute("""
                                                UPDATE bookings_v2 SET
                                                  status='confirm_failed_refunded',
                                                  payment_status='refunded_auto',
                                                  refund_id=%s, refund_amount=%s,
                                                  refund_at=NOW(), refunded_at=NOW(),
                                                  failure_reason=%s, cancelled_at=NOW()
                                                WHERE airbizness_ref=%s
                                            """, (refund_res["refund_id"], refund_res["amount"],
                                                  hbx_err or "hbx_failed", airbizness_ref))
                                        else:
                                            cur2.execute("""
                                                UPDATE bookings_v2 SET
                                                  status='confirm_failed_refund_failed',
                                                  failure_reason=%s, cancelled_at=NOW()
                                                WHERE airbizness_ref=%s
                                            """, (f"HBX: {hbx_err} | Refund: {refund_res['error']}",
                                                  airbizness_ref))
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] hotel fail UPDATE fail: {_e_db}")
                                if refund_res["ok"]:
                                    _alert_telegram(
                                        f"⚠️ Hôtel {airbizness_ref} : HBX FAIL → refund {refund_res['amount']}€ auto OK ({hbx_err})"
                                    )
                                    try:
                                        _send_booking_failed_mail(
                                            airbizness_ref,
                                            to_email=h_dict.get("user_email") or "",
                                            to_name=h_dict.get("holder_name") or "",
                                            refund_amount=refund_res["amount"] or 0.0,
                                            original_amount=float(h_dict.get("gross_price") or 0),
                                            reason="hotel_indisponible",
                                        )
                                    except Exception as _e_m:
                                        print(f"[stripe-webhook] hotel fail-mail err: {_e_m}")
                                else:
                                    _alert_telegram(
                                        f"🔥 URGENT {airbizness_ref} : HBX FAIL ET Refund FAIL — fix manuel "
                                        f"Stripe+DB. HBX: {hbx_err}. Refund: {refund_res['error']}"
                                    )

            # ─── P0 Fix C : ACTIVITY auto-confirm HBX ────────────────────
            # Même pattern que Fix B mais pour activity_bookings_v2.
            if event_type == "payment_intent.succeeded" and md_type == "hbx_activity":
                acur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                acur.execute("SELECT * FROM activity_bookings_v2 WHERE airbizness_ref=%s", (airbizness_ref,))
                a_row = acur.fetchone()
                acur.close()
                if a_row:
                    a_dict = dict(a_row)
                    if a_dict.get("status") == "confirmed" and a_dict.get("hbx_reference"):
                        print(f"[stripe-webhook] activity idempotent ab_ref={airbizness_ref} hbx={a_dict.get('hbx_reference')}")
                    else:
                        rate_key = a_dict.get("rate_key") or ""
                        # Pas de "mock rate_key" pour activités côté codebase — on tente direct HBX.
                        # Si rate_key vide → on saute (rien à booker).
                        if not rate_key:
                            print(f"[stripe-webhook] activity ab_ref={airbizness_ref} sans rate_key, skip auto-confirm")
                        else:
                            hbx_resp = None
                            hbx_err = None
                            try:
                                from providers.hbx.activities.booking import create_activity_booking as _hbx_act_book
                                hbx_resp = _hbx_act_book(
                                    rate_key=rate_key,
                                    holder_name=a_dict.get("holder_name") or "",
                                    holder_surname=a_dict.get("holder_surname") or a_dict.get("holder_name") or "",
                                    holder_email=a_dict.get("user_email") or "",
                                    holder_phone=a_dict.get("user_phone") or "",
                                    client_reference=airbizness_ref,
                                )
                            except Exception as e:
                                hbx_err = str(e)[:500]
                                print(f"[stripe-webhook] ACTIVITY HBX FAIL ab_ref={airbizness_ref}: {hbx_err}")

                            hbx_ref = None
                            if hbx_resp:
                                hbx_ref = hbx_resp.get("reference") \
                                    or (hbx_resp.get("bookings", [{}])[0] if isinstance(hbx_resp.get("bookings"), list) else {}).get("reference")

                            if hbx_ref:
                                # Audit 2026-05-27 sev 4 #52 : capture Stripe APRÈS HBX OK
                                try:
                                    _pi_id_a = data.get("id", "")
                                    if _pi_id_a and (STRIPE_CAPTURE_MANUAL
                                                      or data.get("_manual_capture_flow")):
                                        _cap = _stripe_capture_intent(_pi_id_a,
                                                                       ab_ref=airbizness_ref)
                                        if not _cap.get("ok"):
                                            _alert_telegram(
                                                f"🔥 ACTIVITÉ {airbizness_ref}: HBX OK mais "
                                                f"capture Stripe FAIL ({_cap.get('error')})."
                                            )
                                except Exception as _e_capa:
                                    print(f"[stripe-webhook] activity capture err: {_e_capa}")
                                try:
                                    with conn.cursor() as cur2:
                                        cur2.execute("""
                                            UPDATE activity_bookings_v2 SET
                                              status='confirmed',
                                              hbx_reference=%s,
                                              payment_status='succeeded',
                                              payment_at=NOW(),
                                              confirmed_at=NOW(),
                                              hbx_booking_raw=%s::jsonb
                                            WHERE airbizness_ref=%s
                                        """, (hbx_ref, json.dumps(hbx_resp, default=str), airbizness_ref))
                                    activity_booking_result = {
                                        "ab_ref": airbizness_ref,
                                        "hbx": hbx_resp,
                                    }
                                    print(f"[stripe-webhook] ACTIVITY auto-confirm OK ab_ref={airbizness_ref} hbx={hbx_ref}")
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] activity auto-confirm UPDATE fail: {_e_db}")
                            else:
                                # ── HBX FAIL → refund/cancel auto + mail + alert ──
                                # Audit 2026-05-27 sev 4 #52 : cancel si manual capture
                                _pi_id_a_fail = data.get("id", "")
                                if (STRIPE_CAPTURE_MANUAL or data.get("_manual_capture_flow")) and _pi_id_a_fail:
                                    refund_res = _stripe_cancel_intent(
                                        _pi_id_a_fail, ab_ref=airbizness_ref,
                                        reason="hbx_activity_failed",
                                    )
                                    refund_res.setdefault("refund_id", None)
                                    refund_res.setdefault("amount", 0.0)
                                else:
                                    refund_res = _stripe_refund_auto(
                                        _pi_id_a_fail,
                                        airbizness_ref=airbizness_ref,
                                        reason="hbx_activity_failed",
                                        error_excerpt=hbx_err or "hbx_no_reference",
                                    )
                                try:
                                    with conn.cursor() as cur2:
                                        if refund_res["ok"]:
                                            cur2.execute("""
                                                UPDATE activity_bookings_v2 SET
                                                  status='confirm_failed_refunded',
                                                  payment_status='refunded_auto',
                                                  refund_id=%s, refund_amount=%s,
                                                  refunded_at=NOW(),
                                                  failure_reason=%s, cancelled_at=NOW()
                                                WHERE airbizness_ref=%s
                                            """, (refund_res["refund_id"], refund_res["amount"],
                                                  hbx_err or "hbx_failed", airbizness_ref))
                                        else:
                                            cur2.execute("""
                                                UPDATE activity_bookings_v2 SET
                                                  status='confirm_failed_refund_failed',
                                                  failure_reason=%s, cancelled_at=NOW()
                                                WHERE airbizness_ref=%s
                                            """, (f"HBX: {hbx_err} | Refund: {refund_res['error']}",
                                                  airbizness_ref))
                                except Exception as _e_db:
                                    print(f"[stripe-webhook] activity fail UPDATE fail: {_e_db}")
                                if refund_res["ok"]:
                                    _alert_telegram(
                                        f"⚠️ Activité {airbizness_ref} : HBX FAIL → refund {refund_res['amount']}€ auto OK ({hbx_err})"
                                    )
                                    try:
                                        _send_booking_failed_mail(
                                            airbizness_ref,
                                            to_email=a_dict.get("user_email") or "",
                                            to_name=a_dict.get("holder_name") or "",
                                            refund_amount=refund_res["amount"] or 0.0,
                                            original_amount=float(a_dict.get("gross_price") or 0),
                                            reason="activite_indisponible",
                                        )
                                    except Exception as _e_m:
                                        print(f"[stripe-webhook] activity fail-mail err: {_e_m}")
                                else:
                                    _alert_telegram(
                                        f"🔥 URGENT {airbizness_ref} : Activité HBX FAIL ET Refund FAIL — "
                                        f"fix manuel. HBX: {hbx_err}. Refund: {refund_res['error']}"
                                    )
    except Exception as e:
        print(f"[stripe-webhook] DB error: {e}")

    # Envoi mail confirmation HORS transaction DB (best-effort, ne pas bloquer ack webhook)
    if flight_booking_result:
        try:
            _send_flight_booking_confirmation(
                flight_booking_result["ab_ref"],
                flight_booking_result["order"],
            )
        except Exception as e:
            print(f"[stripe-webhook] mail confirmation fail: {e}")
    if hotel_booking_result:
        try:
            _send_hotel_booking_confirmation(
                hotel_booking_result["ab_ref"],
                hotel_booking_result["hbx"],
            )
        except Exception as e:
            print(f"[stripe-webhook] hotel mail confirmation fail: {e}")
    if activity_booking_result:
        try:
            _send_activity_booking_confirmation(
                activity_booking_result["ab_ref"],
                activity_booking_result["hbx"],
            )
        except Exception as e:
            print(f"[stripe-webhook] activity mail confirmation fail: {e}")

    # ─── RATEHAWK : à l'autorisation carte (capture manuelle → succeeded), on
    # réserve chez RateHawk puis on capture/annule le paiement. En THREAD DE FOND
    # car le booking RateHawk poll ~90 s : le webhook doit répondre vite (sinon
    # Stripe retry). finalize_ratehawk_booking est idempotent (protège des retries).
    if event_type == "payment_intent.succeeded" and md_type == "ratehawk_hotel":
        try:
            import threading
            from routers.ratehawk_web import finalize_ratehawk_booking
            threading.Thread(target=finalize_ratehawk_booking,
                             args=(airbizness_ref,), daemon=True).start()
            print(f"[stripe-webhook] ratehawk finalize lancé (fond) ref={airbizness_ref}")
        except Exception as e:
            print(f"[stripe-webhook] ratehawk dispatch fail: {e}")

    # Audit 2026-05-27 sev 3 : marque event Stripe comme traité (audit trail)
    _stripe_event_mark_processed(stripe_event_id)

    return {"received": True, "event": event_type, "airbizness_ref": airbizness_ref}
