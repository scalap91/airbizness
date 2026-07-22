"""providers.ratehawk.provider — Adapter RateHawk vers HotelProvider unifiée.

RateHawk = WorldOTA API. Catalog ~2M hôtels mondial.
Friendly aux petits acteurs, fort sur Asie + Maghreb + Europe.

Activation : variables env RATEHAWK_KEY_ID + RATEHAWK_KEY_SECRET (HTTP Basic).
"""
from __future__ import annotations
import logging
import os
import time
from typing import Optional

from providers.base import (
    HotelProvider, HotelQuery, Offer, BookingResult,
    UnifiedHotel, UnifiedDestination, RateVerification,
)
from providers.ratehawk.client import RateHawkClient, RateHawkError

logger = logging.getLogger("providers.ratehawk.provider")


# Mapping rapide IATA / code commun → region_id WorldOTA (à enrichir avec /search/multicomplete)
# À l'usage : utiliser l'API multicomplete pour résoudre dynamiquement.
DESTINATION_REGION_HINT = {
    # rempli à la demande via cache /regions
}


class RateHawkProvider(HotelProvider):
    """RateHawk / WorldOTA — wholesaler ouvert aux petits OTAs."""

    name = "ratehawk"
    is_affiliate = False
    has_content = True
    has_booking = True
    coverage = ["WORLD"]

    def __init__(self):
        self.key_id = os.environ.get("RATEHAWK_KEY_ID")
        self.key_secret = os.environ.get("RATEHAWK_KEY_SECRET")
        self.sandbox = os.environ.get("RATEHAWK_ENV", "sandbox").lower() == "sandbox"
        self.enabled = bool(self.key_id and self.key_secret)
        self._client: RateHawkClient | None = None

    @property
    def client(self) -> RateHawkClient:
        if self._client is None:
            self._client = RateHawkClient()
        return self._client

    # ── Helpers mapping ──────────────────────────────────────────────────

    def _resolve_region_id(self, destination_text: str) -> int | None:
        """Résout un texte 'Paris' / 'MAD' en region_id WorldOTA via /multicomplete."""
        if destination_text in DESTINATION_REGION_HINT:
            return DESTINATION_REGION_HINT[destination_text]
        try:
            r = self.client._request("POST", "/search/multicomplete/", json_body={
                "query": destination_text, "language": "en",
            })
            regions = (r.get("data") or {}).get("regions") or []
            if regions:
                rid = regions[0].get("id")
                DESTINATION_REGION_HINT[destination_text] = rid
                return rid
        except RateHawkError as e:
            logger.warning(f"[ratehawk] multicomplete '{destination_text}' failed: {e}")
        return None

    @staticmethod
    def _stars(hotel: dict) -> int | None:
        return hotel.get("star_rating") or hotel.get("stars")

    # ── Méthodes interface ───────────────────────────────────────────────

    def search(self, query: HotelQuery) -> list[Offer]:
        if not self.enabled:
            logger.debug("[ratehawk] disabled — no credentials")
            return []
        region_id = self._resolve_region_id(query.destination)
        if not region_id:
            logger.warning(f"[ratehawk] destination '{query.destination}' non résolue en region_id")
            return []
        try:
            raw = self.client.search_hotels(
                region_id=region_id,
                check_in=query.check_in,
                check_out=query.check_out,
                adults=query.guests or 2,
                currency="EUR",
            )
        except RateHawkError as e:
            logger.warning(f"[ratehawk] search failed: {e}")
            return []

        offers: list[Offer] = []
        for hotel in (raw.get("data") or {}).get("hotels", []) or []:
            best_rate = None
            best_net = None
            for rate in hotel.get("rates", []) or []:
                # WorldOTA renvoie pricing dans payment_options[0].amount
                opts = rate.get("payment_options", {}).get("payment_types", []) or []
                if not opts:
                    continue
                price = float(opts[0].get("amount", 0) or 0)
                if price <= 0:
                    continue
                if best_net is None or price < best_net:
                    best_net = price
                    best_rate = rate
            if not best_rate or not best_net:
                continue

            sell = round(best_net * 1.18, 2)  # marge ~18%
            offers.append(Offer(
                provider="ratehawk",
                provider_offer_id=f"ratehawk:{best_rate.get('book_hash')}",
                vertical="hotel",
                price=sell,
                currency="EUR",
                title=hotel.get("name") or hotel.get("hotel_id") or "Hôtel",
                details={
                    "hotel_code": hotel.get("id") or hotel.get("hotel_id"),
                    "stars": self._stars(hotel),
                    "book_hash": best_rate.get("book_hash"),
                    "price_net": best_net,
                    "room_name": best_rate.get("room_name"),
                    "meal": best_rate.get("meal"),
                    "cancellation_policies": best_rate.get("payment_options", {}).get("cancellation_penalties", []),
                },
                raw=best_rate,
            ))
        logger.info(f"[ratehawk] search → {len(offers)} offers")
        return offers

    def get_content(self, provider_hotel_code: str) -> Optional[UnifiedHotel]:
        if not self.enabled:
            return None
        try:
            raw = self.client.get_hotel_info(provider_hotel_code)
        except RateHawkError as e:
            logger.warning(f"[ratehawk] get_content {provider_hotel_code}: {e}")
            return None
        h = (raw.get("data") or {}).get("hotel") or {}
        if not h:
            return None
        return UnifiedHotel(
            giata_code=str(h.get("giata_id") or h.get("giata_code") or "") or None,
            source_provider="ratehawk",
            source_hotel_code=str(h.get("id") or provider_hotel_code),
            name=h.get("name", ""),
            stars=h.get("star_rating") or h.get("stars"),
            country_code=h.get("region", {}).get("country_code") or h.get("country_code"),
            city=h.get("region", {}).get("name") or "",
            address=h.get("address", ""),
            postal_code=h.get("postal_code"),
            latitude=h.get("latitude"),
            longitude=h.get("longitude"),
            email=h.get("email"),
            phone=h.get("phone"),
            web=h.get("web") or h.get("website"),
            description=h.get("description_struct", {}).get("paragraphs", [""])[0] if h.get("description_struct") else h.get("description"),
            photos=[img.get("url") or img for img in (h.get("images") or [])][:50],
            facilities=h.get("amenity_groups") or [],
            raw=h,
        )

    def checkrate(self, rate_key: str) -> RateVerification:
        if not self.enabled:
            return RateVerification(ok=False, rate_key=rate_key, reason="provider_disabled")
        book_hash = rate_key.removeprefix("ratehawk:")
        try:
            raw = self.client.prebook(book_hash)
            data = raw.get("data") or {}
            # Forme actuelle : data.hotels[].rates[].payment_options.payment_types[].amount ;
            # data.changed = le tarif a-t-il bougé. On renvoie aussi le nouveau book_hash `p-…`.
            prate = ((data.get("hotels") or [{}])[0].get("rates") or [{}])[0]
            current = ((prate.get("payment_options") or {}).get("payment_types") or [{}])[0].get("amount")
            return RateVerification(
                ok=True, rate_key="ratehawk:" + (prate.get("book_hash") or book_hash),
                current_price=float(current) if current else None,
                price_changed=bool(data.get("changed")),
                raw=data,
            )
        except RateHawkError as e:
            return RateVerification(ok=False, rate_key=rate_key, reason=str(e))

    def book(self, rate_key: str, guest: dict, holder: dict, our_ref: str,
             payment_intent_id: Optional[str] = None, user_ip: str = "0.0.0.0") -> BookingResult:
        """Chaîne ETG actuelle (validée sandbox 2026-07-22, order 100037297) :
        prebook(`h-…`) → book_hash `p-…` → booking_form → booking_finish.
        `finish` renvoie `ok` = ACCEPTÉ ; la confirmation finale se lit ensuite
        en asynchrone via get_booking() (percent 0→100 puis order/info)."""
        if not self.enabled:
            return BookingResult(success=False, provider="ratehawk", error="provider_disabled")
        h_hash = rate_key.removeprefix("ratehawk:")
        first = guest.get("first_name") or holder.get("name") or ""
        last = guest.get("last_name") or holder.get("surname") or ""
        email = guest.get("email") or holder.get("email", "") or ""
        phone = str(guest.get("phone") or holder.get("phone", "") or "")
        try:
            # 1) prebook → nouveau book_hash p- + option de paiement dispo
            pre = self.client.prebook(h_hash)
            prate = (((pre.get("data") or {}).get("hotels") or [{}])[0].get("rates") or [{}])[0]
            p_hash = prate.get("book_hash")
            if not p_hash:
                return BookingResult(success=False, provider="ratehawk", error="prebook_no_hash")
            # 2) form (crée l'ordre, renvoie les payment_types réellement offerts)
            form = self.client.booking_form(partner_order_id=our_ref, book_hash=p_hash, user_ip=user_ip)
            pts = (form.get("data") or {}).get("payment_types") or \
                  (prate.get("payment_options") or {}).get("payment_types") or [{}]
            pt = pts[0]
            payment_type = {"type": pt.get("type", "deposit"),
                            "amount": pt.get("amount"),
                            "currency_code": pt.get("currency_code", "EUR")}
            # 3) finish (voyageurs + paiement)
            fin = self.client.booking_finish(
                partner_order_id=our_ref,
                rooms=[{"guests": [{"first_name": first, "last_name": last}]}],
                user={"email": email, "comment": "", "phone": phone},
                supplier_data={"first_name_original": first, "last_name_original": last,
                               "phone": phone, "email": email},
                payment_type=payment_type)
            return BookingResult(
                success=(fin.get("status") == "ok"), provider="ratehawk",
                provider_booking_id=our_ref,  # partner_order_id = notre clé de suivi
                confirmation=fin,
                error=None if fin.get("status") == "ok" else fin.get("error"),
            )
        except RateHawkError as e:
            return BookingResult(success=False, provider="ratehawk", error=str(e))

    def cancel(self, provider_booking_ref: str) -> BookingResult:
        if not self.enabled:
            return BookingResult(success=False, provider="ratehawk", error="provider_disabled")
        try:
            raw = self.client.cancel_order(provider_booking_ref)
            return BookingResult(success=True, provider="ratehawk",
                                 provider_booking_id=provider_booking_ref,
                                 confirmation=raw)
        except RateHawkError as e:
            return BookingResult(success=False, provider="ratehawk", error=str(e))

    def get_booking(self, provider_booking_ref: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            return self.client.get_order(provider_booking_ref)
        except RateHawkError as e:
            logger.warning(f"[ratehawk] get_booking: {e}")
            return None

    def health(self) -> dict:
        if not self.enabled:
            return {"ok": False, "reason": "no_credentials"}
        return self.client.health()
