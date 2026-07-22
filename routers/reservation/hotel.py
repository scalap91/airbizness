"""Réservation — sous-module HÔTEL (/hotel/booking/*).
Branché aux offres hôtel (catalogue providers, HBX). Le PAIEMENT est séparé (routers/paiement.py).
NB : routes renommées 2026-05-30 (était /hbx/booking/...). Le paiement-intent est dans routers/paiement.py.
"""
import json
import uuid as _uuid
import psycopg2, psycopg2.extras
import stripe
from fastapi import APIRouter, Request, HTTPException
from main import (DB_CONFIG, limiter, _is_mock_rate_key,
                  CheckrateRequest, BookingConfirmRequest, BookingByEmailRequest,
                  format_cancellation_fr, board_label_fr, describe_hbx_room,
                  HOTEL_OPTION_PRICES, _send_brevo_booking_confirmation)
from providers.hbx.photos import extract_best_main_photo, extract_room_photos

router = APIRouter()


@router.get("/hotel/booking/options-pricing")
def hbx_options_pricing():
    """Expose la grille tarifaire options hôtel pour le front."""
    return HOTEL_OPTION_PRICES


@router.get("/hotel/booking/{airbizness_ref}")
@limiter.limit("60/minute")
def hbx_get_booking(request: Request, airbizness_ref: str):
    """Récupère un booking depuis bookings_v2 pour /hotel-confirmation.html.
    Réexpose les options AirBizness (stockées dans hbx_booking_raw JSONB) en top-level."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, hbx_reference, status,
                   holder_name, holder_surname, user_email,
                   hotel_code, hotel_name, destination_code,
                   check_in, check_out, nights, adults, rooms_count,
                   net_price, gross_price, currency,
                   rate_key_verified,
                   created_at, confirmed_at, cancellation_policies,
                   hbx_booking_raw
            FROM bookings_v2 WHERE airbizness_ref = %s
        """, (airbizness_ref,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(404, "Booking not found")
        # Cast date pour JSON
        d = dict(row)
        for k in ("check_in", "check_out", "created_at", "confirmed_at"):
            if d.get(k):
                d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
        if d.get("gross_price") is not None:
            d["gross_price"] = float(d["gross_price"])
        if d.get("net_price") is not None:
            d["net_price"] = float(d["net_price"])
        # Flag is_mock pour la bannière "Réservation de démonstration" sur la confirmation
        d["is_mock"] = bool(
            _is_mock_rate_key(d.get("rate_key_verified"))
            or (str(d.get("hbx_reference") or "").startswith("AB-HT-"))
        )
        # ── Re-expose options en top-level pour /hotel-confirmation.html ──
        raw = d.pop("hbx_booking_raw", None)
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except Exception: raw = None
        opts = (raw or {}).get("airbizness_options") if isinstance(raw, dict) else None
        if isinstance(opts, dict):
            d["options"] = {
                "late_checkin": bool(opts.get("late_checkin")),
                "special_requests": opts.get("special_requests"),
                "insurance": bool(opts.get("insurance")),
                "transfer": opts.get("transfer") or "none",
                "options_total": int(opts.get("options_total") or 0),
                "room_total": float(opts.get("room_total") or 0),
                # Transfer HBX dynamique
                "transfer_rate_key": opts.get("transfer_rate_key"),
                "transfer_price": float(opts.get("transfer_price") or 0),
                "transfer_label": opts.get("transfer_label"),
                "transfer_meta": opts.get("transfer_meta"),
            }
        else:
            d["options"] = {
                "late_checkin": False, "special_requests": None,
                "insurance": False, "transfer": "none",
                "options_total": 0,
                "room_total": float(d.get("gross_price") or 0),
                "transfer_rate_key": None, "transfer_price": 0,
                "transfer_label": None, "transfer_meta": None,
            }
        d["transfer_booking_ref"] = (raw or {}).get("transfer_booking_ref") if isinstance(raw, dict) else None
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/hotel/booking/checkrate")
@limiter.limit("30/minute")
def hbx_checkrate(request: Request, body: CheckrateRequest):
    """Re-vérifie le tarif HBX avant le paiement + enrichit avec infos hôtel
    (photo, étoiles, adresse, check-in/out hours) pour la sidebar récap checkout."""
    try:
        import sys as _sys
        if "/var/www/airbizness" not in _sys.path:
            _sys.path.insert(0, "/var/www/airbizness")
        from providers.hbx.hotels.checkrate import checkrate
        from providers.hbx import config as hbx_config
        # Strip 'hbx:' préfixe si présent (rate_key issu de l'aggregator multi-provider)
        native_rate_key = body.rate_key.removeprefix("hbx:") if body.rate_key else body.rate_key
        v = checkrate(native_rate_key)
        # Apply marge + TVA
        net = v["net"]
        pricing = hbx_config.PRICING["hotels"]
        gross = round(net * (1 + pricing["margin_pct"]) * (1 + pricing["vat_pct"]), 2)

        # ── Enrichissement infos hôtel pour la sidebar checkout (photo, étoiles, etc.)
        hotel_info: dict = {}
        try:
            _conn = psycopg2.connect(**DB_CONFIG)
            _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _cur.execute("""
                SELECT name, category_stars, city, country_code, address, postal_code,
                       latitude, longitude, main_image_url, raw
                FROM hbx_hotels_catalog WHERE hotel_code = %s
            """, (body.hotel_code,))
            _row = _cur.fetchone()
            _cur.close(); _conn.close()
            if _row:
                _d = dict(_row)
                _raw = _d.pop("raw", None)
                if isinstance(_raw, str):
                    try: _raw = json.loads(_raw)
                    except Exception: _raw = None
                if not isinstance(_raw, dict):
                    _raw = {}
                _imgs = _raw.get("images") or []
                best_img = extract_best_main_photo(_imgs, "hbx")
                # Photos par chambre depuis HAB+roomCode (pour mini photo chambre)
                room_photos = extract_room_photos(_imgs, "hbx", max_per_room=1)

                # Check-in/out hours HBX standards : interestPoints / generalInfo / etc
                # Format HBX : raw.checkInTime = "15:00", raw.checkOutTime = "12:00"
                check_in_time = _raw.get("checkInTime") or _raw.get("checkIn", {}).get("from") if isinstance(_raw.get("checkIn"), dict) else _raw.get("checkInTime")
                check_out_time = _raw.get("checkOutTime") or _raw.get("checkOut", {}).get("until") if isinstance(_raw.get("checkOut"), dict) else _raw.get("checkOutTime")

                hotel_info = {
                    "name": _d.get("name"),
                    "stars": _d.get("category_stars"),
                    "city": _d.get("city"),
                    "country_code": _d.get("country_code"),
                    "address": _d.get("address"),
                    "postal_code": _d.get("postal_code"),
                    "latitude": _d.get("latitude"),
                    "longitude": _d.get("longitude"),
                    "main_image_url": best_img or _d.get("main_image_url"),
                    "check_in_time": check_in_time,    # "15:00" si dispo
                    "check_out_time": check_out_time,  # "12:00" si dispo
                    "room_photos_by_code": room_photos,
                }
        except Exception:
            pass

        cancel_info = format_cancellation_fr(v.get("cancellation_policies") or [])

        return {
            "rate_key": v["rate_key"],
            "net_price": net,
            "gross_price": gross,
            "currency": v["currency"],
            "hotel_name": v["hotel_name"],
            "board_code": v.get("board_code"),
            "board_name": board_label_fr(v.get("board_code") or "", v.get("board_name") or ""),
            "rate_class": v["rate_class"],
            "room_code": v.get("room_code"),
            "room_name": v.get("room_name"),
            "room_description": describe_hbx_room(v.get("room_code") or "", v.get("room_name") or ""),
            "cancellation_policies": v["cancellation_policies"],
            "cancellation_label": cancel_info["label"],
            "cancellation_until_fr": cancel_info["until_date_fr"],
            "cancellation_is_free": cancel_info["is_free"],
            "is_refundable": v.get("rate_class") != "NRF",
            "hotel_info": hotel_info,
        }
    except Exception as e:
        raise HTTPException(400, f"checkrate failed: {e}")




@router.post("/hotel/booking/confirm")
@limiter.limit("30/minute")
def hbx_confirm_booking(request: Request, body: BookingConfirmRequest):
    """Appelé après 3DS validé côté front. Crée la résa HBX, met à jour DB, envoie email."""
    # 1. Vérifier Stripe PaymentIntent
    try:
        intent = stripe.PaymentIntent.retrieve(body.payment_intent_id)
    except Exception as e:
        raise HTTPException(400, f"Stripe retrieve fail: {e}")

    if intent.status != "succeeded":
        raise HTTPException(400, f"Paiement non confirmé (status={intent.status})")

    # 2. Récupère bookings_v2 row
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bookings_v2 WHERE airbizness_ref = %s", (body.airbizness_ref,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Booking ref not found")
        if row["status"] == "confirmed":
            # Déjà confirmé, idempotent
            cur.close(); conn.close()
            return {"airbizness_ref": body.airbizness_ref,
                    "hbx_reference": row["hbx_reference"],
                    "status": "confirmed", "idempotent": True}
        cur.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    # 3. Créer booking HBX (ou MOCK booking si rate_key MOCK-HBX-*)
    try:
        if _is_mock_rate_key(row.get("rate_key_verified")):
            # ── Mode MOCK : pas d'appel HBX, on génère une réf hôtel fictive.
            #    Doctrine Pascal : parcours doit aboutir au paiement même HBX KO.
            mock_ref = f"AB-HT-{_uuid.uuid4().hex[:6].upper()}"
            hbx_booking = {
                "reference": mock_ref,
                "hotel_name": row.get("hotel_name") or "Hôtel",
                "check_in": row["check_in"].isoformat() if hasattr(row["check_in"], "isoformat") else str(row["check_in"]),
                "check_out": row["check_out"].isoformat() if hasattr(row["check_out"], "isoformat") else str(row["check_out"]),
                "total_net": float(row.get("gross_price") or 0),
                "cancellation_policies": [],
                "raw": {"mock": True, "note": "Réservation de démonstration — HBX quota épuisé."},
            }
        else:
            import sys as _sys
            if "/var/www/airbizness" not in _sys.path:
                _sys.path.insert(0, "/var/www/airbizness")
            from providers.hbx.hotels.booking import create_booking
            hbx_booking = create_booking(
                rate_key=row["rate_key_verified"],
                holder_name=row["holder_name"],
                holder_surname=row["holder_surname"],
                client_reference=row["airbizness_ref"],
                remark=row["remark"] or "Réservation AirBizness",
            )
    except Exception as e:
        # PROBLÈME : paiement OK mais booking HBX KO → refund auto Stripe
        refund_id = None
        try:
            refund = stripe.Refund.create(
                payment_intent=body.payment_intent_id,
                metadata={"airbizness_ref": body.airbizness_ref,
                           "reason": "hbx_booking_failed_auto_refund"},
            )
            refund_id = refund.id
            print(f"[booking.confirm] HBX KO → refund auto Stripe OK: {refund_id}")
        except Exception as refund_err:
            print(f"[booking.confirm] HBX KO + refund auto Stripe KO: {refund_err}")
        # Update DB
        try:
            with conn, conn.cursor() as cur2:
                cur2.execute("""
                    UPDATE bookings_v2 SET status='booking_failed',
                                          payment_status='succeeded',
                                          cancelled_at=NOW()
                    WHERE airbizness_ref=%s
                """, (body.airbizness_ref,))
        except Exception:
            pass
        raise HTTPException(500,
            f"HBX booking fail (refund {'OK' if refund_id else 'KO'}): {e}")

    # 4. UPDATE bookings_v2 → confirmed
    #    On préserve airbizness_options (posé au payment-intent) en mergeant côté Postgres.
    import json as _json

    # 4.a) Transfer HBX best-effort booking (depuis raw)
    transfer_booking_ref = None
    try:
        raw_in = row.get("hbx_booking_raw") or {}
        if isinstance(raw_in, str):
            raw_in = _json.loads(raw_in)
        opts = (raw_in or {}).get("airbizness_options") or {}
        t_rk = opts.get("transfer_rate_key")
        if t_rk:
            from providers import hbx_transfer as _ht
            tres = _ht.book_transfer(
                rate_key=t_rk,
                holder_name=f"{row.get('holder_name','')} {row.get('holder_surname','')}".strip(),
                holder_email=row.get("user_email") or "",
                holder_phone=row.get("user_phone") or "",
                client_reference=body.airbizness_ref,
            )
            transfer_booking_ref = tres.get("reference")
    except Exception as e:
        print(f"[booking confirm] transfer book best-effort fail: {e}")

    try:
        with conn, conn.cursor() as cur:
            patch = {"hbx_response": hbx_booking.get("raw") or {}}
            if transfer_booking_ref:
                patch["transfer_booking_ref"] = transfer_booking_ref
            cur.execute("""
                UPDATE bookings_v2 SET
                  status='confirmed',
                  hbx_reference=%s,
                  net_price=%s,
                  payment_status='succeeded',
                  payment_at=NOW(),
                  confirmed_at=NOW(),
                  cancellation_policies=%s::jsonb,
                  hbx_booking_raw = COALESCE(hbx_booking_raw, '{}'::jsonb) || %s::jsonb
                WHERE airbizness_ref=%s
            """, (
                hbx_booking["reference"],
                hbx_booking["total_net"],
                _json.dumps(hbx_booking.get("cancellation_policies") or [], default=str),
                _json.dumps(patch, default=str),
                body.airbizness_ref,
            ))
    except Exception as e:
        print(f"[booking confirm] UPDATE fail: {e}")
    finally:
        try: conn.close()
        except: pass

    # 5. Email Brevo (best-effort)
    try:
        _send_brevo_booking_confirmation(row, hbx_booking)
    except Exception as e:
        print(f"[booking confirm] email fail: {e}")

    return {
        "airbizness_ref": body.airbizness_ref,
        "hbx_reference": hbx_booking["reference"],
        "status": "confirmed",
        "hotel_name": hbx_booking["hotel_name"],
        "check_in": hbx_booking["check_in"],
        "check_out": hbx_booking["check_out"],
        "transfer_booking_ref": transfer_booking_ref,
    }


@router.post("/hotel/bookings/by-email")
@limiter.limit("20/minute")
def hbx_bookings_by_email(request: Request, body: BookingByEmailRequest):
    """Liste les réservations d'un email (pour /mes-voyages.html).

    POST pour ne pas exposer les emails dans les URLs / logs nginx.
    Pour MVP : pas d'auth (juste connaissance de l'email).
    Plus tard : magic link + session pour sécuriser.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT airbizness_ref, hbx_reference, status,
                   holder_name, holder_surname,
                   hotel_code, hotel_name, destination_code, destination_name,
                   check_in, check_out, nights, adults, rooms_count,
                   gross_price, currency,
                   created_at, confirmed_at, cancelled_at
            FROM bookings_v2 WHERE user_email = %s
            ORDER BY created_at DESC LIMIT 50
        """, (body.email,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        bookings = []
        for r in rows:
            d = dict(r)
            for k in ("check_in", "check_out", "created_at", "confirmed_at", "cancelled_at"):
                if d.get(k):
                    d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
            if d.get("gross_price") is not None:
                d["gross_price"] = float(d["gross_price"])
            bookings.append(d)
        return {"count": len(bookings), "bookings": bookings}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────
# TUNNEL CHECKOUT ACTIVITY (2026-05-23) — pattern Hotels adapté
# ─────────────────────────────────────────────────────────────────────
