"""Module pack — tunnel paiement combo Stripe+Duffel+HBX. Migré depuis main.py le 2026-06-02 (9e module migré)."""

import json
import os
import psycopg2
import psycopg2.extras
import stripe
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr
from main import (limiter, DB_CONFIG, BREVO_KEY, STRIPE_CAPTURE_MANUAL,
                    alert_conciergerie, _pack_db_conn, _send_pack_confirmation_email,
                    _brevo_send_template_or_html)

DUFFEL_BOOKING_DRY_RUN = os.environ.get("DUFFEL_BOOKING_DRY_RUN", "false").lower() == "true"


class PackQuoteRequest(BaseModel):
    # Vol aller
    flight_origin: str
    flight_destination: str
    flight_departure_date: str
    flight_price: float
    flight_airline: Optional[str] = None
    flight_cabin: Optional[str] = "business"
    flight_offer_token: Optional[str] = None
    flight_return_date: Optional[str] = None
    flight_departure_at: Optional[str] = None
    flight_duration_minutes: Optional[int] = None
    flight_stops: Optional[int] = None
    # Trip type (Pascal 2026-05-24 — vol aller-retour par défaut pour pack)
    trip_type: Optional[str] = "one_way"   # 'round_trip' | 'one_way'
    # Vol RETOUR (Pascal 2026-05-24) — tous optionnels, présents si trip_type=round_trip
    flight_return_offer_token: Optional[str] = None
    flight_return_price: Optional[float] = None
    flight_return_departure_at: Optional[str] = None
    flight_return_duration_minutes: Optional[int] = None
    flight_return_stops: Optional[int] = None
    flight_return_airline: Optional[str] = None
    # Hôtel
    hotel_rate_key: Optional[str] = None      # accepte None pour mock catalog
    hotel_code: int
    hotel_name: Optional[str] = None
    hotel_destination_code: Optional[str] = None
    hotel_check_in: str
    hotel_check_out: str
    hotel_room_name: Optional[str] = None
    hotel_board_name: Optional[str] = None
    hotel_main_photo: Optional[str] = None
    hotel_is_refundable: Optional[bool] = True
    hotel_price_hint: Optional[float] = None  # pour le mock : prix indicatif depuis catalog
    adults: int = 2
    # Options de séjour (toutes optionnelles — stockées dans raw_payload)
    late_checkin: Optional[bool] = False
    special_requests: Optional[str] = None
    # Fix B1 Pascal 2026-05-31 : List[Optional[str]] (les éléments peuvent être null
    # quand un passager n'a pas choisi de bagage soute). Avant : List[str] refusait null
    # dans le tableau → 422 sur payment-intent → résa impossible.
    baggage_per_passenger: Optional[List[Optional[str]]] = None  # ['23kg','15kg',null] selon adults — alias rétrocompat (aller)
    baggage_outbound_per_passenger: Optional[List[Optional[str]]] = None  # bagages aller × adults
    baggage_inbound_per_passenger: Optional[List[Optional[str]]] = None   # bagages retour × adults (None si one_way)
    # ── Pascal 2026-05-25 (allbyleg) : cabin_premium/flex/transfer désormais PAR LEG ──
    # Garde les anciens flags en alias rétrocompat (= outbound si fournis sans suffixe).
    cabin_premium: Optional[bool] = False
    flex_ticket: Optional[bool] = False
    transfer: Optional[str] = None  # "none" / "oneway" / "roundtrip" — legacy
    cabin_premium_outbound: Optional[bool] = False
    cabin_premium_inbound: Optional[bool] = False
    flex_ticket_outbound: Optional[bool] = False
    flex_ticket_inbound: Optional[bool] = False
    transfer_outbound: Optional[str] = None       # 'none' | 'with_transfer'
    transfer_outbound_rate_key: Optional[str] = None
    transfer_outbound_price: Optional[float] = 0.0
    transfer_outbound_label: Optional[str] = None
    transfer_outbound_meta: Optional[dict] = None
    transfer_inbound: Optional[str] = None
    transfer_inbound_rate_key: Optional[str] = None
    transfer_inbound_price: Optional[float] = 0.0
    transfer_inbound_label: Optional[str] = None
    transfer_inbound_meta: Optional[dict] = None
    insurance: Optional[bool] = False  # = multirisque séjour (global, étape Récap)
    # Assurance par vol (Pascal 2026-05-25) — par leg, ~12€/voyageur, indépendant
    flight_insurance_outbound: Optional[bool] = False
    flight_insurance_inbound: Optional[bool] = False
    # Sièges sélectionnés (Pascal 2026-05-25) — stocké dans raw_payload
    # Format : {"outbound": {"0":"seat_1A","1":"seat_1B"}, "inbound": {"0":"seat_5C"}}
    selected_seats: Optional[dict] = None


class PackPassenger(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    nationality: Optional[str] = None
    passport_number: Optional[str] = None


class PackPaymentIntentRequest(BaseModel):
    # Toutes les data du quote (frontend les renvoie après confirmation visuelle)
    flight_origin: str
    flight_destination: str
    flight_departure_date: str
    flight_price: float
    flight_airline: Optional[str] = None
    flight_cabin: Optional[str] = "business"
    flight_offer_token: Optional[str] = None
    flight_return_date: Optional[str] = None
    flight_departure_at: Optional[str] = None
    flight_duration_minutes: Optional[int] = None
    flight_stops: Optional[int] = None
    # Trip type
    trip_type: Optional[str] = "one_way"
    # Vol RETOUR (Pascal 2026-05-24)
    flight_return_offer_token: Optional[str] = None
    flight_return_price: Optional[float] = None
    flight_return_departure_at: Optional[str] = None
    flight_return_duration_minutes: Optional[int] = None
    flight_return_stops: Optional[int] = None
    flight_return_airline: Optional[str] = None
    hotel_rate_key: Optional[str] = None
    hotel_code: int
    hotel_name: str
    hotel_destination_code: Optional[str] = None
    hotel_check_in: str
    hotel_check_out: str
    hotel_price: float
    hotel_room_name: Optional[str] = None
    hotel_board_name: Optional[str] = None
    hotel_main_photo: Optional[str] = None
    hotel_is_refundable: Optional[bool] = True
    total_amount: float
    pack_discount_pct: float = 5.0
    adults: int = 2
    currency: str = "EUR"
    user_email: EmailStr
    holder_name: str
    holder_surname: str
    user_phone: Optional[str] = None
    passengers: Optional[List[PackPassenger]] = None
    is_mock: Optional[bool] = False
    # Options de séjour (toutes optionnelles — stockées dans raw_payload)
    late_checkin: Optional[bool] = False
    special_requests: Optional[str] = None
    baggage_per_passenger: Optional[List[str]] = None  # alias rétrocompat (aller seul)
    # Bagages par leg (Pascal 2026-05-25 stepper 8)
    baggage_outbound_per_passenger: Optional[List[str]] = None
    baggage_inbound_per_passenger: Optional[List[str]] = None
    # ── Pascal 2026-05-25 (allbyleg) : cabin_premium/flex/transfer par leg ──
    cabin_premium: Optional[bool] = False
    flex_ticket: Optional[bool] = False
    transfer: Optional[str] = None
    cabin_premium_outbound: Optional[bool] = False
    cabin_premium_inbound: Optional[bool] = False
    flex_ticket_outbound: Optional[bool] = False
    flex_ticket_inbound: Optional[bool] = False
    transfer_outbound: Optional[str] = None
    transfer_outbound_rate_key: Optional[str] = None
    transfer_outbound_price: Optional[float] = 0.0
    transfer_outbound_label: Optional[str] = None
    transfer_outbound_meta: Optional[dict] = None
    transfer_inbound: Optional[str] = None
    transfer_inbound_rate_key: Optional[str] = None
    transfer_inbound_price: Optional[float] = 0.0
    transfer_inbound_label: Optional[str] = None
    transfer_inbound_meta: Optional[dict] = None
    insurance: Optional[bool] = False  # = multirisque séjour (global, étape Récap)
    # Assurance par vol (Pascal 2026-05-25) — par leg, ~12€/voyageur
    flight_insurance_outbound: Optional[bool] = False
    flight_insurance_inbound: Optional[bool] = False
    options_total: Optional[float] = 0.0
    # Sièges (Pascal 2026-05-25) — {"outbound":{"0":"seat_1A"}, "inbound":{"0":"seat_5C"}}
    selected_seats: Optional[dict] = None
    # Transfer HBX (remplace progressivement 'transfer' radio 35/60€) — Pascal 2026-05-24
    transfer_rate_key: Optional[str] = None      # MOCK-TRANS-XXX ou rate_key HBX
    transfer_price: Optional[float] = 0.0
    transfer_label: Optional[str] = None         # ex "Van familial · Welcome Pickups"
    transfer_meta: Optional[dict] = None         # snapshot dict pour confirmation page
    # Concierge hôtelier (résa pour client) — Pascal 2026-05-24
    on_behalf_of: Optional[str] = None
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_ref: Optional[str] = None
    # Services Duffel réels (bagages/sièges/etc.) — Pascal 2026-05-26 (P1 juridique)
    duffel_services: Optional[List[dict]] = None


class PackConfirmRequest(BaseModel):
    airbizness_ref: str
    payment_intent_id: str


def _generate_pack_ref():
    import uuid as _u
    # Format demandé Pascal : AB-PK-<6chars> (court, scannable, prononçable)
    return "AB-PK-" + _u.uuid4().hex[:6].upper()


router = APIRouter()

# ─── HANDLERS APPENDED BY SCRIPT BELOW ─────────────────────────────────────

@router.post("/pack/quote")
@limiter.limit("30/minute")
def pack_quote(request: Request, body: PackQuoteRequest):
    """Vérifie le rate hôtel + calcule le total pack avec réduction.

    MOCK MODE (doctrine Pascal "site prêt même APIs down") :
      - hotel_rate_key None ou commence par 'MOCK-' → bypass HBX, calcul depuis hint catalog
      - flight_offer_token commence par 'MOCK-' → conserve le prix tel quel (vol mock)
      - Le booking aboutit jusqu'au paiement, marqué is_mock=true en DB.
    """
    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")

    rk = (body.hotel_rate_key or "").strip()
    is_hotel_mock = (not rk) or rk.upper().startswith("MOCK-") or rk.upper().startswith("HBX:MOCK-")
    is_flight_mock = bool(body.flight_offer_token and body.flight_offer_token.upper().startswith("MOCK-"))

    hotel_room_name = body.hotel_room_name or "Chambre standard"
    hotel_board_name = body.hotel_board_name or "Chambre seule (RO)"
    hotel_main_photo = body.hotel_main_photo
    hotel_is_refundable = bool(body.hotel_is_refundable)
    hotel_rate_key_verified = rk
    cancellation_policies: list = []

    if is_hotel_mock:
        # Calcule un prix indicatif crédible : hint si fourni, sinon barème nuit×adults
        try:
            nights = max(1, (datetime.fromisoformat(body.hotel_check_out).date()
                              - datetime.fromisoformat(body.hotel_check_in).date()).days)
        except Exception:
            nights = 1
        if body.hotel_price_hint and body.hotel_price_hint > 0:
            hotel_gross = round(float(body.hotel_price_hint), 2)
        else:
            base_per_night = 145.0  # business-friendly default
            hotel_gross = round(base_per_night * nights * max(1, body.adults / 2.0), 2)
        hotel_net = round(hotel_gross / 1.30, 2)  # marge approximative pour cohérence
        if not hotel_rate_key_verified:
            hotel_rate_key_verified = f"MOCK-HOTEL-{body.hotel_code}-{body.hotel_check_in}"
    else:
        try:
            from providers.hbx.hotels.checkrate import checkrate
            native_key = rk.removeprefix("hbx:")
            hotel_verified = checkrate(native_key)
            from providers.hbx import config as hbx_cfg
            pricing = hbx_cfg.PRICING["hotels"]
            hotel_net = float(hotel_verified["net"])
            hotel_gross = round(hotel_net * (1 + pricing["margin_pct"]) * (1 + pricing["vat_pct"]), 2)
            hotel_rate_key_verified = hotel_verified.get("rate_key") or rk
            hotel_board_name = hotel_verified.get("board_name") or hotel_board_name
            hotel_is_refundable = bool(hotel_verified.get("is_refundable", True))
            cancellation_policies = hotel_verified.get("cancellation_policies", []) or []
        except Exception as e:
            return JSONResponse({"error": "hotel_rate_unavailable",
                                 "detail": str(e)}, status_code=400)

    # ─────────────────────────────────────────────────────────────
    # OPTIONS DE SÉJOUR : late check-in, bagages, transfert, assurance...
    # Calcul du surcoût total + détail par poste (retourné au front)
    # Les options ne bénéficient PAS de la remise pack (sinon Pascal perd marge)
    # ─────────────────────────────────────────────────────────────
    options_total = 0.0
    options_detail: dict = {}
    adults = max(1, int(body.adults or 1))

    if body.late_checkin:
        options_total += 25.0
        options_detail["late_checkin"] = {"label": "Late check-in (arrivée après 20h)", "price": 25.0}

    # ── Pascal 2026-05-25 (allbyleg) : cabin_premium / flex_ticket / transfer PAR LEG ──
    # Rétrocompat : si cabin_premium=true sans cabin_premium_outbound → on mappe vers outbound.
    cabin_out = bool(getattr(body, 'cabin_premium_outbound', False) or (body.cabin_premium and not getattr(body, 'cabin_premium_outbound', False) and not getattr(body, 'cabin_premium_inbound', False)))
    cabin_in  = bool(getattr(body, 'cabin_premium_inbound', False))
    if cabin_out:
        cost = 15.0 * adults
        options_total += cost
        options_detail["cabin_premium_outbound"] = {"label": f"Bagage cabine premium 10kg — vol aller (×{adults})", "price": cost}
    if cabin_in:
        cost = 15.0 * adults
        options_total += cost
        options_detail["cabin_premium_inbound"] = {"label": f"Bagage cabine premium 10kg — vol retour (×{adults})", "price": cost}

    flex_out = bool(getattr(body, 'flex_ticket_outbound', False) or (body.flex_ticket and not getattr(body, 'flex_ticket_outbound', False) and not getattr(body, 'flex_ticket_inbound', False)))
    flex_in  = bool(getattr(body, 'flex_ticket_inbound', False))
    if flex_out:
        cost = 49.0 * adults
        options_total += cost
        options_detail["flex_ticket_outbound"] = {"label": f"Tarif flexible — vol aller (×{adults})", "price": cost}
    if flex_in:
        cost = 49.0 * adults
        options_total += cost
        options_detail["flex_ticket_inbound"] = {"label": f"Tarif flexible — vol retour (×{adults})", "price": cost}

    if body.insurance:
        cost = 35.0 * adults
        options_total += cost
        options_detail["insurance"] = {"label": f"Assurance multirisque séjour (×{adults})", "price": cost}
    # Assurance par vol — Pascal 2026-05-25 (12€/voyageur/leg, indépendant)
    if getattr(body, 'flight_insurance_outbound', False):
        cost = 12.0 * adults
        options_total += cost
        options_detail["flight_insurance_outbound"] = {"label": f"Assurance vol aller (×{adults})", "price": cost}
    if getattr(body, 'flight_insurance_inbound', False):
        cost = 12.0 * adults
        options_total += cost
        options_detail["flight_insurance_inbound"] = {"label": f"Assurance vol retour (×{adults})", "price": cost}

    # ── Transfert PAR LEG (Pascal 2026-05-25 allbyleg) ──
    # On accepte transfer_outbound_price + transfer_inbound_price (HBX dynamique).
    # Rétrocompat : ancien champ `transfer` ("oneway"/"roundtrip") avec montant flat 35/60.
    t_out_price = float(getattr(body, 'transfer_outbound_price', None) or 0)
    t_in_price  = float(getattr(body, 'transfer_inbound_price', None)  or 0)
    if t_out_price > 0:
        options_total += t_out_price
        options_detail["transfer_outbound"] = {
            "label": "Transfert vol aller — aéroport → hôtel",
            "price": round(t_out_price, 2),
            "rate_key": getattr(body, 'transfer_outbound_rate_key', None),
            "snapshot": getattr(body, 'transfer_outbound_label', None),
        }
    if t_in_price > 0:
        options_total += t_in_price
        options_detail["transfer_inbound"] = {
            "label": "Transfert vol retour — hôtel → aéroport",
            "price": round(t_in_price, 2),
            "rate_key": getattr(body, 'transfer_inbound_rate_key', None),
            "snapshot": getattr(body, 'transfer_inbound_label', None),
        }
    # Legacy : si aucun transfert par leg fourni mais l'ancien `transfer` envoyé → conserver mock 35/60
    if t_out_price == 0 and t_in_price == 0:
        if body.transfer == "oneway":
            options_total += 35.0
            options_detail["transfer"] = {"label": "Transfert aéroport-hôtel (aller)", "price": 35.0}
        elif body.transfer == "roundtrip":
            options_total += 60.0
            options_detail["transfer"] = {"label": "Transfert aéroport-hôtel (aller-retour)", "price": 60.0}

    # ── BAGAGES PAR LEG (Pascal 2026-05-25 stepper 8) ──
    # Si baggage_outbound_per_passenger fourni → flow nouveau (aller + retour distincts)
    # Sinon → flow ancien rétrocompat (baggage_per_passenger = aller uniquement)
    def _bag_cost(b: Optional[str]) -> tuple:
        if b == "15kg": return 25.0, "Bagage soute 15kg"
        if b == "23kg": return 45.0, "Bagage soute 23kg"
        if b == "30kg": return 65.0, "Bagage soute 30kg"
        return 0.0, None

    bag_outbound_in = body.baggage_outbound_per_passenger or body.baggage_per_passenger or []
    bag_inbound_in  = body.baggage_inbound_per_passenger or []

    baggage_list_out = []
    bag_total = 0.0
    for i, b in enumerate(bag_outbound_in):
        cost, label = _bag_cost(b)
        if cost > 0:
            bag_total += cost
            baggage_list_out.append({"passenger_idx": i, "weight": b, "price": cost, "label": label, "leg": "outbound"})

    baggage_list_in = []
    for i, b in enumerate(bag_inbound_in):
        cost, label = _bag_cost(b)
        if cost > 0:
            bag_total += cost
            baggage_list_in.append({"passenger_idx": i, "weight": b, "price": cost, "label": label, "leg": "inbound"})

    if bag_total > 0:
        options_total += bag_total
        # Format rétrocompat : baggage_per_passenger (front actuel) + nouveaux blocs séparés
        options_detail["baggage_per_passenger"] = {
            "label": f"Bagages soute ({len(baggage_list_out) + len(baggage_list_in)} segment·s)",
            "price": bag_total,
            "items": baggage_list_out + baggage_list_in,  # rétrocompat
        }
        if baggage_list_out:
            options_detail["baggage_outbound"] = {
                "label": f"Bagages aller ({len(baggage_list_out)} voyageur·s)",
                "price": sum(it["price"] for it in baggage_list_out),
                "items": baggage_list_out,
            }
        if baggage_list_in:
            options_detail["baggage_inbound"] = {
                "label": f"Bagages retour ({len(baggage_list_in)} voyageur·s)",
                "price": sum(it["price"] for it in baggage_list_in),
                "items": baggage_list_in,
            }

    options_total = round(options_total, 2)

    # ── Vol RETOUR (Pascal 2026-05-24) — ajouté au sous-total si fourni ──
    is_round_trip = (body.trip_type or "").lower() == "round_trip"
    return_price = float(body.flight_return_price or 0) if (is_round_trip and body.flight_return_price) else 0.0
    is_flight_return_mock = bool(body.flight_return_offer_token and body.flight_return_offer_token.upper().startswith("MOCK-"))

    sub = float(body.flight_price) + return_price + float(hotel_gross)
    discount_pct = 5.0
    pack_subtotal = round(sub * (1 - discount_pct / 100.0), 2)
    saved = round(sub - pack_subtotal, 2)
    total = round(pack_subtotal + options_total, 2)
    is_mock = bool(is_hotel_mock or is_flight_mock or is_flight_return_mock)

    return {
        "flight": {
            "origin": body.flight_origin,
            "destination": body.flight_destination,
            "departure_date": body.flight_departure_date,
            "departure_at": body.flight_departure_at,
            "duration_minutes": body.flight_duration_minutes,
            "stops": body.flight_stops,
            "price": body.flight_price,
            "airline": body.flight_airline,
            "cabin": body.flight_cabin,
            "is_mock": is_flight_mock,
        },
        "flight_return": ({
            "origin": body.flight_destination,    # le retour part de la destination
            "destination": body.flight_origin,
            "departure_date": body.flight_return_date,
            "departure_at": body.flight_return_departure_at,
            "duration_minutes": body.flight_return_duration_minutes,
            "stops": body.flight_return_stops,
            "price": body.flight_return_price,
            "airline": body.flight_return_airline,
            "cabin": body.flight_cabin,
            "is_mock": is_flight_return_mock,
        } if is_round_trip and body.flight_return_offer_token else None),
        "trip_type": "round_trip" if is_round_trip else "one_way",
        "hotel": {
            "name": body.hotel_name,
            "code": body.hotel_code,
            "check_in": body.hotel_check_in,
            "check_out": body.hotel_check_out,
            "rate_key_verified": hotel_rate_key_verified,
            "net_price": hotel_net,
            "gross_price": hotel_gross,
            "room_name": hotel_room_name,
            "board_name": hotel_board_name,
            "main_photo": hotel_main_photo,
            "is_refundable": hotel_is_refundable,
            "cancellation_policies": cancellation_policies,
            "is_mock": is_hotel_mock,
        },
        "options": {
            "total": options_total,
            "detail": options_detail,
            "selections": {
                "late_checkin": bool(body.late_checkin),
                "special_requests": body.special_requests or None,
                "baggage_per_passenger": bag_outbound_in or [],   # rétrocompat = aller
                "baggage_outbound_per_passenger": bag_outbound_in or [],
                "baggage_inbound_per_passenger": bag_inbound_in or [],
                "cabin_premium": bool(body.cabin_premium),
                "flex_ticket": bool(body.flex_ticket),
                "transfer": body.transfer or "none",
                # ── Par leg (Pascal 2026-05-25 allbyleg) ──
                "cabin_premium_outbound": cabin_out,
                "cabin_premium_inbound": cabin_in,
                "flex_ticket_outbound": flex_out,
                "flex_ticket_inbound": flex_in,
                "transfer_outbound": getattr(body, 'transfer_outbound', None) or "none",
                "transfer_outbound_rate_key": getattr(body, 'transfer_outbound_rate_key', None),
                "transfer_outbound_price": round(t_out_price, 2),
                "transfer_outbound_label": getattr(body, 'transfer_outbound_label', None),
                "transfer_inbound": getattr(body, 'transfer_inbound', None) or "none",
                "transfer_inbound_rate_key": getattr(body, 'transfer_inbound_rate_key', None),
                "transfer_inbound_price": round(t_in_price, 2),
                "transfer_inbound_label": getattr(body, 'transfer_inbound_label', None),
                "insurance": bool(body.insurance),
                "flight_insurance_outbound": bool(getattr(body, 'flight_insurance_outbound', False)),
                "flight_insurance_inbound": bool(getattr(body, 'flight_insurance_inbound', False)),
                "selected_seats": body.selected_seats or {},
            },
        },
        "pricing": {
            "subtotal": round(sub, 2),
            "pack_discount_pct": discount_pct,
            "saved": saved,
            "pack_subtotal": pack_subtotal,
            "options_total": options_total,
            "total": total,
            "currency": "EUR",
        },
        "is_mock": is_mock,
    }


@router.post("/pack/payment-intent")
@limiter.limit("20/minute")
def pack_payment_intent(request: Request, body: PackPaymentIntentRequest):
    """Crée le Stripe PaymentIntent pour le pack + INSERT pack_bookings."""
    if not stripe.api_key:
        raise HTTPException(500, "Stripe non configuré")
    if body.total_amount <= 0:
        raise HTTPException(400, "Total invalide")

    ref = _generate_pack_ref()

    # Crée le PaymentIntent (un seul, pour le total pack)
    try:
        # Audit 2026-05-27 sev 4 #52 : capture_method='manual' si flag activé.
        _pi_kwargs = {
            "amount": int(round(body.total_amount * 100)),
            "currency": body.currency.lower(),
            "metadata": {
                "airbizness_ref": ref,
                "type": "pack",
                "hotel_code": str(body.hotel_code),
                "flight_route": f"{body.flight_origin}-{body.flight_destination}",
                "capture_mode": "manual" if STRIPE_CAPTURE_MANUAL else "automatic",
            },
            "description": f"AirBizness Pack {ref} — {body.flight_origin}→{body.flight_destination} + {body.hotel_name}",
            "receipt_email": body.user_email,
            "automatic_payment_methods": {"enabled": True},
        }
        if STRIPE_CAPTURE_MANUAL:
            _pi_kwargs["capture_method"] = "manual"
        intent = stripe.PaymentIntent.create(**_pi_kwargs)
    except Exception as e:
        raise HTTPException(502, f"Stripe error: {e}")

    # INSERT pack_bookings (raw_payload stocke les champs additionnels :
    # passengers list, room/board, photos, is_mock — sans alourdir le schéma DB)
    passengers_raw = [p.model_dump() if hasattr(p, 'model_dump') else dict(p)
                       for p in (body.passengers or [])]
    raw_extras = {
        "passengers": passengers_raw,
        "hotel_room_name": body.hotel_room_name,
        "hotel_board_name": body.hotel_board_name,
        "hotel_main_photo": body.hotel_main_photo,
        "hotel_is_refundable": body.hotel_is_refundable,
        "flight_departure_at": body.flight_departure_at,
        "flight_duration_minutes": body.flight_duration_minutes,
        "flight_stops": body.flight_stops,
        "is_mock": bool(body.is_mock),
        # ── Vol RETOUR (Pascal 2026-05-24) ──
        "trip_type": body.trip_type or "one_way",
        "flight_return_offer_token": body.flight_return_offer_token,
        "flight_return_price": (float(body.flight_return_price) if body.flight_return_price else None),
        "flight_return_departure_at": body.flight_return_departure_at,
        "flight_return_duration_minutes": body.flight_return_duration_minutes,
        "flight_return_stops": body.flight_return_stops,
        "flight_return_airline": body.flight_return_airline,
        # Options de séjour
        "late_checkin": bool(body.late_checkin),
        "special_requests": body.special_requests,
        "baggage_per_passenger": (body.baggage_outbound_per_passenger or body.baggage_per_passenger or []),
        "baggage_outbound_per_passenger": (body.baggage_outbound_per_passenger or body.baggage_per_passenger or []),
        "baggage_inbound_per_passenger": body.baggage_inbound_per_passenger or [],
        "selected_seats": body.selected_seats or {},
        "cabin_premium": bool(body.cabin_premium),
        "flex_ticket": bool(body.flex_ticket),
        "transfer": body.transfer or "none",
        # ── Par leg (Pascal 2026-05-25 allbyleg) — persistance complète dans raw_payload ──
        "cabin_premium_outbound": bool(getattr(body, 'cabin_premium_outbound', False) or body.cabin_premium),
        "cabin_premium_inbound": bool(getattr(body, 'cabin_premium_inbound', False)),
        "flex_ticket_outbound": bool(getattr(body, 'flex_ticket_outbound', False) or body.flex_ticket),
        "flex_ticket_inbound": bool(getattr(body, 'flex_ticket_inbound', False)),
        "transfer_outbound": getattr(body, 'transfer_outbound', None) or "none",
        "transfer_outbound_rate_key": getattr(body, 'transfer_outbound_rate_key', None),
        "transfer_outbound_price": float(getattr(body, 'transfer_outbound_price', None) or 0),
        "transfer_outbound_label": getattr(body, 'transfer_outbound_label', None),
        "transfer_outbound_meta": getattr(body, 'transfer_outbound_meta', None),
        "transfer_inbound": getattr(body, 'transfer_inbound', None) or "none",
        "transfer_inbound_rate_key": getattr(body, 'transfer_inbound_rate_key', None),
        "transfer_inbound_price": float(getattr(body, 'transfer_inbound_price', None) or 0),
        "transfer_inbound_label": getattr(body, 'transfer_inbound_label', None),
        "transfer_inbound_meta": getattr(body, 'transfer_inbound_meta', None),
        "insurance": bool(body.insurance),
        "flight_insurance_outbound": bool(getattr(body, 'flight_insurance_outbound', False)),
        "flight_insurance_inbound": bool(getattr(body, 'flight_insurance_inbound', False)),
        "options_total": float(body.options_total or 0),
        # Transfer HBX (snapshot pré-paiement, booké ensuite par /pack/confirm)
        "transfer_rate_key": body.transfer_rate_key or None,
        "transfer_price": float(body.transfer_price or 0),
        "transfer_label": body.transfer_label or None,
        "transfer_meta": body.transfer_meta or None,
        # Concierge hôtelier (résa pour client) — Pascal 2026-05-24
        "on_behalf_of": (str(body.on_behalf_of) if body.on_behalf_of else None),
        "guest_name": body.guest_name or None,
        "guest_email": body.guest_email or None,
        "guest_ref": body.guest_ref or None,
        # Services Duffel réels (P1 juridique Pascal 2026-05-26)
        "duffel_services": [
            {"id": s["id"], "quantity": max(1, int(s.get("quantity", 1) or 1))}
            for s in (body.duffel_services or [])
            if isinstance(s, dict) and isinstance(s.get("id"), str) and s.get("id")
        ],
    }
    try:
        with _pack_db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pack_bookings (
                    airbizness_ref, status,
                    user_email, user_phone, holder_name, holder_surname,
                    flight_origin, flight_destination, flight_departure_date,
                    flight_return_date, flight_airline, flight_cabin,
                    flight_price, flight_provider, flight_offer_token,
                    hotel_code, hotel_name, hotel_destination_code,
                    hotel_check_in, hotel_check_out, hotel_rate_key, hotel_price,
                    total_amount, pack_discount_pct, currency, adults,
                    payment_intent_id, payment_status, raw_payload
                ) VALUES (
                    %s, 'payment_pending',
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, 'travelpayouts', %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, 'pending', %s
                )
            """, (
                ref,
                body.user_email, body.user_phone, body.holder_name, body.holder_surname,
                body.flight_origin, body.flight_destination, body.flight_departure_date,
                body.flight_return_date, body.flight_airline, body.flight_cabin,
                body.flight_price, body.flight_offer_token,
                body.hotel_code, body.hotel_name, body.hotel_destination_code,
                body.hotel_check_in, body.hotel_check_out, body.hotel_rate_key, body.hotel_price,
                body.total_amount, body.pack_discount_pct, body.currency, body.adults,
                intent.id, json.dumps(raw_extras),
            ))
    except Exception as e:
        # Si l'INSERT échoue, on cancel le PI immédiatement (pas de double facturation possible)
        try: stripe.PaymentIntent.cancel(intent.id)
        except: pass
        raise HTTPException(500, f"DB error: {e}")

    return {
        "airbizness_ref": ref,
        "payment_intent_id": intent.id,
        "client_secret": intent.client_secret,
    }


@router.post("/pack/confirm")
@limiter.limit("30/minute")
def pack_confirm(request: Request, body: PackConfirmRequest):
    """Après Stripe 3DS validé : SÉQUENCE VOL→HÔTEL avec rollback intelligent.

    Pattern P7 (idempotence + state machine) :
      1. Stripe paid check
      2. Duffel /air/orders (vol d'abord — Pascal : siège plus rare)
         ├─ Échec → refund total Stripe + alert conciergerie
         └─ Succès → PNR + e-ticket émis
      3. HBX /booking (hôtel après)
         ├─ Échec → garder vol, refund PARTIEL (juste hôtel), alert CRITIQUE (substitut needed)
         └─ Succès → both ok
      4. UPDATE status='confirmed', email voucher

    DUFFEL_BOOKING_DRY_RUN=true : mock Duffel response (pour sandbox sans facturer).
    """
    with _pack_db_conn() as conn:
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pack_bookings WHERE airbizness_ref = %s",
                        (body.airbizness_ref,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Pack inconnu")
    if row["status"] == "confirmed":
        # P7 idempotence : déjà confirmé → renvoie l'état actuel
        return {"airbizness_ref": body.airbizness_ref, "status": "confirmed",
                "hbx_reference": row.get("hbx_reference"),
                "duffel_pnr": row.get("duffel_pnr"),
                "already_confirmed": True}

    # ── 1) Stripe paid check ────────────────────────────────────────────
    try:
        pi = stripe.PaymentIntent.retrieve(body.payment_intent_id)
        if pi.status != "succeeded":
            return JSONResponse({"error": "payment_not_succeeded",
                                  "stripe_status": pi.status}, status_code=400)
    except Exception as e:
        raise HTTPException(502, f"Stripe retrieve error: {e}")

    try:
        with _pack_db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE pack_bookings SET payment_status='succeeded', payment_at=NOW()
                WHERE airbizness_ref=%s
            """, (body.airbizness_ref,))
    except Exception:
        pass

    import sys as _sys
    if "/var/www/airbizness" not in _sys.path:
        _sys.path.insert(0, "/var/www/airbizness")

    # ── 1.b) MOCK BYPASS — si vol ou hôtel a un token MOCK-, on confirme
    # sans appeler les vrais providers. Doctrine Pascal : "site prêt même
    # APIs down". Le pack est marqué is_mock=true dans raw_payload.
    raw_extras_in = row.get("raw_payload") or {}
    if isinstance(raw_extras_in, str):
        try: raw_extras_in = json.loads(raw_extras_in)
        except Exception: raw_extras_in = {}
    rk_in = (row.get("hotel_rate_key") or "")
    ot_in = (row.get("flight_offer_token") or "")
    is_mock_pack = (
        bool(raw_extras_in.get("is_mock"))
        or rk_in.upper().startswith("MOCK-") or rk_in.upper().startswith("HBX:MOCK-")
        or ot_in.upper().startswith("MOCK-")
        or not rk_in
    )
    if is_mock_pack:
        mock_pnr = "MK" + body.airbizness_ref.split("-")[-1]
        mock_hbx = "MOCK-HBX-" + body.airbizness_ref.split("-")[-1]
        # PNR retour (Pascal 2026-05-24) : généré si trip_type=round_trip + token retour
        mock_return_pnr = None
        if (raw_extras_in.get("trip_type") == "round_trip"
                and raw_extras_in.get("flight_return_offer_token")):
            mock_return_pnr = mock_pnr + "-R"

        # ── Transfer (best-effort) — book mock/HBX si rate_key fourni
        transfer_ref_mock = None
        try:
            t_rk = (raw_extras_in or {}).get("transfer_rate_key")
            if t_rk:
                from providers import hbx_transfer as _ht
                tres = _ht.book_transfer(
                    rate_key=t_rk,
                    holder_name=f"{row.get('holder_name','')} {row.get('holder_surname','')}".strip(),
                    holder_email=row.get("user_email") or "",
                    holder_phone=row.get("user_phone") or "",
                    client_reference=body.airbizness_ref,
                )
                transfer_ref_mock = tres.get("reference")
        except Exception as e:
            print(f"[pack mock] transfer book best-effort fail: {e}")

        try:
            with _pack_db_conn() as conn, conn.cursor() as cur:
                update_cols = ["status='confirmed'", "confirmed_at=NOW()",
                               "flight_status='confirmed'", "flight_booked_at=NOW()",
                               "duffel_pnr=%s", "duffel_order_id=%s",
                               "hotel_status='confirmed'", "hotel_booked_at=NOW()",
                               "hbx_reference=%s"]
                params = [mock_pnr, "ord_MOCK_" + body.airbizness_ref[-6:],
                          mock_hbx]
                # Merge transfer + return PNR dans raw_payload (un seul jsonb concat évite 2 colonnes)
                _extra_jsonb = {}
                if transfer_ref_mock:
                    _extra_jsonb["transfer_booking_ref"] = transfer_ref_mock
                if mock_return_pnr:
                    _extra_jsonb["flight_return_pnr"] = mock_return_pnr
                if _extra_jsonb:
                    update_cols.append(
                        "raw_payload = COALESCE(raw_payload,'{}'::jsonb) || %s::jsonb"
                    )
                    params.append(json.dumps(_extra_jsonb))
                params.append(body.airbizness_ref)
                cur.execute(
                    f"UPDATE pack_bookings SET {', '.join(update_cols)} WHERE airbizness_ref=%s",
                    params,
                )
        except Exception as e:
            print(f"[pack mock] update error: {e}")
        return {
            "airbizness_ref": body.airbizness_ref,
            "status": "confirmed",
            "is_mock": True,
            "flight_confirmed": True,
            "hotel_confirmed": True,
            "duffel_pnr": mock_pnr,
            "flight_return_pnr": mock_return_pnr,
            "hbx_reference": mock_hbx,
            "transfer_booking_ref": transfer_ref_mock,
        }

    # ── 2) VOL D'ABORD — Duffel /air/orders ────────────────────────────
    duffel_order_id = None
    duffel_pnr = None
    duffel_e_tickets: list = []
    duffel_failed_reason = None
    duffel_offer_id = row.get("duffel_offer_id") or row.get("flight_offer_token")
    duffel_passenger_ids = row.get("duffel_passenger_ids") or []
    if isinstance(duffel_passenger_ids, str):
        try: duffel_passenger_ids = json.loads(duffel_passenger_ids)
        except Exception: duffel_passenger_ids = []

    if DUFFEL_BOOKING_DRY_RUN:
        # SANDBOX : mock une réponse Duffel sans appel réel
        duffel_order_id = f"ord_DRY_{body.airbizness_ref[-10:]}"
        duffel_pnr = "DRYRUN"
        duffel_e_tickets = [{"passenger_id": "mock_pas", "ticket_number": "999-DRY-12345"}]
    else:
        try:
            from providers.duffel import DuffelFlightProvider
            dp = DuffelFlightProvider()
            # Construit passenger info depuis pack_bookings
            passenger_info = [{
                "title": "mr",  # TODO : lire civility depuis form, ici default
                "given_name": row["holder_name"],
                "family_name": row["holder_surname"],
                "born_on": "1990-01-01",  # TODO : lire born_on depuis form
                "email": row["user_email"],
                "phone_number": row.get("user_phone") or "+33000000000",
                "gender": "m",
            }]
            # Si plusieurs adults, réplique le holder pour matcher passenger_ids
            while len(passenger_info) < len(duffel_passenger_ids):
                passenger_info.append({**passenger_info[0]})

            booking_res = dp.booking(
                provider_offer_id=duffel_offer_id,
                passenger={
                    "passenger_ids": duffel_passenger_ids,
                    "passengers_info": passenger_info,
                    "total_amount": float(row.get("flight_price") or 0),
                    "currency": row.get("currency") or "EUR",
                    "airbizness_ref": body.airbizness_ref,
                },
            )
            if booking_res.success:
                duffel_order_id = booking_res.provider_booking_id
                duffel_pnr = booking_res.booking_reference
                duffel_e_tickets = (booking_res.details or {}).get("e_tickets", [])
            else:
                duffel_failed_reason = booking_res.error
        except Exception as e:
            duffel_failed_reason = str(e)[:300]

    if not duffel_order_id:
        # ÉCHEC VOL → REFUND TOTAL + ALERT
        refund_id = None
        try:
            r = stripe.Refund.create(
                payment_intent=body.payment_intent_id,
                metadata={"airbizness_ref": body.airbizness_ref,
                          "reason": "flight_booking_failed"},
            )
            refund_id = r.id
        except Exception as e:
            print(f"[pack] Stripe refund fail: {e}")

        with _pack_db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE pack_bookings SET status='failed', failure_reason=%s,
                       refund_id=%s, refund_amount=total_amount, cancelled_at=NOW(),
                       flight_status='failed'
                WHERE airbizness_ref=%s
            """, (duffel_failed_reason or "duffel_booking_failed",
                  refund_id, body.airbizness_ref))

        # Alert conciergerie P4
        alert_conciergerie(
            airbizness_ref=body.airbizness_ref,
            severity="warn",
            alert_type="booking_failed_vol",
            payload={"duffel_offer_id": duffel_offer_id, "reason": duffel_failed_reason,
                      "refund_id": refund_id, "amount": float(row.get("total_amount") or 0)},
        )

        return JSONResponse({
            "airbizness_ref": body.airbizness_ref,
            "status": "failed",
            "stage": "flight",
            "stripe_refunded": bool(refund_id),
            "refund_id": refund_id,
            "reason": duffel_failed_reason or "Échec émission billet vol.",
        }, status_code=502)

    # Vol OK → on persiste tout de suite (idempotence)
    try:
        with _pack_db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE pack_bookings
                SET duffel_order_id=%s, duffel_pnr=%s, duffel_e_tickets=%s,
                    flight_status='confirmed', flight_provider='duffel',
                    flight_booked_at=NOW()
                WHERE airbizness_ref=%s
            """, (duffel_order_id, duffel_pnr, json.dumps(duffel_e_tickets),
                  body.airbizness_ref))
    except Exception as e:
        print(f"[pack] persist vol error: {e}")

    # ── 3) HÔTEL — HBX /booking ────────────────────────────────────────
    from providers.hbx.hotels.booking import create_booking
    hbx_ref = None
    hbx_failed_reason = None
    try:
        native_rate = (row["hotel_rate_key"] or "").removeprefix("hbx:")
        hbx_result = create_booking(
            rate_key=native_rate,
            holder_name=row["holder_name"],
            holder_surname=row["holder_surname"],
            client_reference=row["airbizness_ref"],
        )
        hbx_ref = (hbx_result.get("booking") or {}).get("reference") or hbx_result.get("reference")
    except Exception as e:
        hbx_failed_reason = str(e)[:300]

    if not hbx_ref:
        # ÉCHEC HÔTEL APRÈS VOL OK : on garde le vol + refund PARTIEL (part hôtel) + alert CRITIQUE
        hotel_amount = float(row.get("hotel_price") or 0)
        refund_id = None
        try:
            r = stripe.Refund.create(
                payment_intent=body.payment_intent_id,
                amount=int(round(hotel_amount * 100)),  # refund partiel
                metadata={"airbizness_ref": body.airbizness_ref,
                          "reason": "hotel_booking_failed_partial_refund"},
            )
            refund_id = r.id
        except Exception as e:
            print(f"[pack] Stripe partial refund fail: {e}")

        with _pack_db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE pack_bookings
                SET status='partial_confirmed', hotel_status='failed',
                    refund_id=%s, refund_amount=%s, failure_reason=%s
                WHERE airbizness_ref=%s
            """, (refund_id, hotel_amount,
                  f"VOL OK ({duffel_pnr}) mais HÔTEL FAIL : {hbx_failed_reason}",
                  body.airbizness_ref))

        # ALERT CRITIQUE conciergerie : substitut hôtel nécessaire URGENT
        alert_conciergerie(
            airbizness_ref=body.airbizness_ref,
            severity="critical",
            alert_type="substitute_needed",
            payload={
                "scenario": "vol_ok_hotel_failed",
                "vol_pnr": duffel_pnr,
                "vol_dep": str(row.get("flight_departure_date") or ""),
                "hotel_code": row.get("hotel_code"),
                "hotel_name": row.get("hotel_name"),
                "hotel_dates": [str(row.get("hotel_check_in") or ""),
                                str(row.get("hotel_check_out") or "")],
                "amount_refunded": hotel_amount,
                "reason": hbx_failed_reason,
                "action_required": "Trouver hôtel substitut équivalent + appeler client. Le vol est confirmé.",
            },
        )

        # On retourne SUCCESS partiel : le vol est bon, client est prévenu de la suite
        return {
            "airbizness_ref": body.airbizness_ref,
            "status": "partial_confirmed",
            "flight_confirmed": True,
            "duffel_pnr": duffel_pnr,
            "hotel_confirmed": False,
            "hotel_refund_amount": hotel_amount,
            "refund_id": refund_id,
            "conciergerie_message": "Votre vol est confirmé. Notre conciergerie vous recontacte sous 1h pour vous trouver un hôtel équivalent (l'hôtel choisi vient de se remplir).",
        }

    # ── 4) BOTH OK → finalize confirmed ─────────────────────────────────
    # 4.a) Transfer best-effort booking (n'empêche jamais la confirmation pack)
    transfer_ref = None
    try:
        t_rk = (raw_extras_in or {}).get("transfer_rate_key")
        if t_rk:
            from providers import hbx_transfer as _ht
            tres = _ht.book_transfer(
                rate_key=t_rk,
                holder_name=f"{row.get('holder_name','')} {row.get('holder_surname','')}".strip(),
                holder_email=row.get("user_email") or "",
                holder_phone=row.get("user_phone") or "",
                client_reference=body.airbizness_ref,
            )
            transfer_ref = tres.get("reference")
    except Exception as e:
        print(f"[pack] transfer book best-effort fail: {e}")

    try:
        with _pack_db_conn() as conn, conn.cursor() as cur:
            if transfer_ref:
                cur.execute("""
                    UPDATE pack_bookings SET status='confirmed', confirmed_at=NOW(),
                           hbx_reference=%s, hotel_status='confirmed', hotel_booked_at=NOW(),
                           raw_payload = COALESCE(raw_payload,'{}'::jsonb) || %s::jsonb
                    WHERE airbizness_ref=%s
                """, (hbx_ref,
                      json.dumps({"transfer_booking_ref": transfer_ref}),
                      body.airbizness_ref))
            else:
                cur.execute("""
                    UPDATE pack_bookings SET status='confirmed', confirmed_at=NOW(),
                           hbx_reference=%s, hotel_status='confirmed', hotel_booked_at=NOW()
                    WHERE airbizness_ref=%s
                """, (hbx_ref, body.airbizness_ref))
    except Exception as e:
        print(f"[pack] final update error: {e}")

    # Email confirmation Brevo
    try:
        _send_pack_confirmation_email(body.airbizness_ref)
    except Exception as e:
        print(f"[pack] email send error: {e}")

    return {
        "airbizness_ref": body.airbizness_ref,
        "status": "confirmed",
        "flight_confirmed": True,
        "hotel_confirmed": True,
        "duffel_pnr": duffel_pnr,
        "duffel_e_tickets": duffel_e_tickets,
        "hbx_reference": hbx_ref,
        "transfer_booking_ref": transfer_ref,
    }


@router.get("/pack/booking/{airbizness_ref}")
def pack_get_booking(airbizness_ref: str):
    """Récupère un pack booking pour la page de confirmation.

    Expose passengers list, room name, board, photos, flight times, is_mock
    extraits de raw_payload (stockés à la création).
    """
    with _pack_db_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM pack_bookings WHERE airbizness_ref=%s", (airbizness_ref,))
        row = cur.fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    d = dict(row)
    # Extract raw_payload extras pour les exposer top-level (pack-confirmation.html)
    raw = d.pop("raw_payload", None) or {}
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except Exception: raw = {}
    if isinstance(raw, dict):
        for k in ("passengers", "hotel_room_name", "hotel_board_name",
                   "hotel_main_photo", "hotel_is_refundable",
                   "flight_departure_at", "flight_duration_minutes",
                   "flight_stops", "is_mock",
                   "late_checkin", "special_requests",
                   "baggage_per_passenger",
                   "baggage_outbound_per_passenger", "baggage_inbound_per_passenger",
                   "selected_seats",
                   "cabin_premium",
                   "flex_ticket", "transfer", "insurance",
                   # Pascal 2026-05-25 allbyleg : par leg
                   "cabin_premium_outbound", "cabin_premium_inbound",
                   "flex_ticket_outbound", "flex_ticket_inbound",
                   "transfer_outbound", "transfer_outbound_rate_key",
                   "transfer_outbound_price", "transfer_outbound_label", "transfer_outbound_meta",
                   "transfer_inbound", "transfer_inbound_rate_key",
                   "transfer_inbound_price", "transfer_inbound_label", "transfer_inbound_meta",
                   "flight_insurance_outbound", "flight_insurance_inbound",
                   "options_total",
                   "transfer_rate_key", "transfer_price", "transfer_label",
                   "transfer_meta", "transfer_booking_ref",
                   # Vol retour (Pascal 2026-05-24)
                   "trip_type",
                   "flight_return_offer_token", "flight_return_price",
                   "flight_return_departure_at", "flight_return_duration_minutes",
                   "flight_return_stops", "flight_return_airline",
                   "flight_return_pnr"):
            if k in raw and raw[k] is not None:
                d[k] = raw[k]
    d.setdefault("passengers", [])
    d.setdefault("is_mock", False)
    # Defaults pour les options (évite undefined côté front)
    d.setdefault("late_checkin", False)
    d.setdefault("cabin_premium", False)
    d.setdefault("flex_ticket", False)
    d.setdefault("insurance", False)
    d.setdefault("flight_insurance_outbound", False)
    d.setdefault("flight_insurance_inbound", False)
    # Pascal 2026-05-25 allbyleg : defaults par leg (UI confirmation propre)
    d.setdefault("cabin_premium_outbound", False)
    d.setdefault("cabin_premium_inbound", False)
    d.setdefault("flex_ticket_outbound", False)
    d.setdefault("flex_ticket_inbound", False)
    d.setdefault("transfer_outbound", "none")
    d.setdefault("transfer_outbound_price", 0.0)
    d.setdefault("transfer_inbound", "none")
    d.setdefault("transfer_inbound_price", 0.0)
    d.setdefault("transfer", "none")
    d.setdefault("baggage_per_passenger", [])
    d.setdefault("baggage_outbound_per_passenger", d.get("baggage_per_passenger") or [])
    d.setdefault("baggage_inbound_per_passenger", [])
    d.setdefault("selected_seats", {})
    d.setdefault("options_total", 0.0)
    # Convert dates
    for k in ("hotel_check_in", "hotel_check_out", "flight_departure_date", "flight_return_date"):
        if d.get(k):
            d[k] = str(d[k])
    for k in ("created_at", "payment_at", "confirmed_at", "cancelled_at",
              "flight_booked_at", "hotel_booked_at"):
        if d.get(k):
            try: d[k] = d[k].isoformat()
            except Exception: d[k] = str(d[k])
    return d


@router.post("/pack/cancel")
@limiter.limit("10/minute")
def pack_cancel(request: Request, body: dict):
    """Cancel un pack — annule HBX + refund Stripe."""
    ref = body.get("airbizness_ref")
    email_confirm = body.get("email_confirm")
    if not ref or not email_confirm:
        raise HTTPException(400, "airbizness_ref et email_confirm requis")

    with _pack_db_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM pack_bookings WHERE airbizness_ref=%s AND user_email=%s",
                    (ref, email_confirm))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Pack non trouvé ou email incorrect")
    if row["status"] in ("cancelled", "refunded", "failed"):
        return {"airbizness_ref": ref, "status": row["status"], "already_handled": True}

    # 1) Cancel HBX
    hbx_cancelled = False
    if row["hbx_reference"]:
        try:
            from providers.hbx.hotels.booking import cancel_booking
            cancel_booking(row["hbx_reference"])
            hbx_cancelled = True
        except Exception as e:
            print(f"[pack cancel] HBX cancel fail: {e}")

    # 1.b) Cancel transfer HBX best-effort (ne bloque pas le pack cancel)
    transfer_cancelled = False
    try:
        raw_in = row.get("raw_payload") or {}
        if isinstance(raw_in, str):
            raw_in = json.loads(raw_in)
        t_ref = (raw_in or {}).get("transfer_booking_ref")
        if t_ref:
            from providers import hbx_transfer as _ht
            res = _ht.cancel_transfer(t_ref)
            transfer_cancelled = bool(res.get("cancelled"))
    except Exception as e:
        print(f"[pack cancel] transfer cancel fail: {e}")

    # 2) Refund Stripe
    refund_id, refund_amount = None, None
    if row["payment_intent_id"] and row["payment_status"] == "succeeded":
        try:
            r = stripe.Refund.create(
                payment_intent=row["payment_intent_id"],
                metadata={"airbizness_ref": ref, "reason": "user_cancellation_pack"},
            )
            refund_id = r.id
            refund_amount = (r.amount or 0) / 100.0
        except Exception as e:
            print(f"[pack cancel] Stripe refund fail: {e}")

    with _pack_db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE pack_bookings SET status='cancelled', cancelled_at=NOW(),
                   refund_id=%s, refund_amount=%s
            WHERE airbizness_ref=%s
        """, (refund_id, refund_amount, ref))

    return {
        "airbizness_ref": ref,
        "status": "cancelled",
        "hbx_cancelled": hbx_cancelled,
        "transfer_cancelled": transfer_cancelled,
        "stripe_refunded": bool(refund_id),
        "refund_id": refund_id,
        "refund_amount": refund_amount,
    }


@router.get("/pack/{airbizness_ref}/voucher.pdf")
def pack_voucher_pdf(airbizness_ref: str):
    """Génère le voucher PDF AirBizness pour 1 pack vol+hôtel."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM pack_bookings WHERE airbizness_ref = %s", (airbizness_ref,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from voucher import render_pack_voucher
        pdf_bytes = render_pack_voucher(dict(row))
    except Exception as e:
        return JSONResponse({"error": "render_failed", "detail": str(e)}, status_code=500)

    safe_ref = airbizness_ref.replace("/", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="voucher-{safe_ref}.pdf"'},
    )
