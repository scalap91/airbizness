"""providers.ratehawk.client — Client HTTP WorldOTA / RateHawk B2B.

Auth : HTTP Basic (KEY_ID:KEY_SECRET).
Base : https://api.worldota.net/api/b2b/v3/
Doc  : https://docs.worldota.net/

Toutes les requêtes RateHawk sont des POST avec body JSON, sauf /hotel/info qui est GET.
"""
from __future__ import annotations
import base64
import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("providers.ratehawk.client")


class RateHawkError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, raw: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw = raw or {}


class RateHawkClient:
    """Client HTTP minimaliste mais robuste pour WorldOTA API."""

    TIMEOUT_S = 25
    RETRY_MAX = 2
    RETRY_BACKOFF_S = (1, 3)

    def __init__(self):
        self.key_id = os.environ.get("RATEHAWK_KEY_ID")
        self.key_secret = os.environ.get("RATEHAWK_KEY_SECRET")
        self.env = os.environ.get("RATEHAWK_ENV", "sandbox").lower()
        if not self.key_id or not self.key_secret:
            raise RateHawkError("RATEHAWK_KEY_ID / RATEHAWK_KEY_SECRET non configurés")
        # HOST par env — 3 royaumes d'auth distincts (doc ETG /fundamentals/authorization,
        # vérifié 2026-07-22 : une clé sandbox ne s'auth QUE sur le host sandbox) :
        #   sandbox → https://api-sandbox.worldota.net  (clés sandbox, ex. 823)
        #   test    → https://api.worldota.net
        #   prod    → https://api.ratehawk.com          (migration Mikhail 2026-07-21)
        # Override explicite via RATEHAWK_HOST (gagne toujours).
        host = os.environ.get("RATEHAWK_HOST", "").strip().rstrip("/")
        if not host:
            if self.env in ("prod", "production", "live"):
                host = "https://api.ratehawk.com"
            elif self.env == "test":
                host = "https://api.worldota.net"
            else:  # sandbox (défaut)
                host = "https://api-sandbox.worldota.net"
        self.host = host
        self.base_url = f"{host}/api/b2b/v3"
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "AirBizness/1.0",
        }
        self._quota_counter = 0
        self._trace: Optional[list] = None  # opt-in (certif) : trace requête/réponse brute

    def enable_trace(self) -> None:
        """Active la capture des couples requête/réponse bruts (audit/certification).
        OFF par défaut → aucun impact en prod. Réinitialise la trace courante."""
        self._trace = []

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None) -> dict:
        url = self.base_url + path
        last_exc: Exception | None = None
        for attempt in range(self.RETRY_MAX):
            t0 = time.time()
            try:
                with httpx.Client(timeout=self.TIMEOUT_S) as cli:
                    r = cli.request(method, url, headers=self.headers,
                                    json=json_body, params=params)
                self._quota_counter += 1
                elapsed = int((time.time() - t0) * 1000)
                logger.info(f"[RateHawk] {method} {path} → {r.status_code} ({elapsed}ms) [n={self._quota_counter}]")

                if self._trace is not None:
                    try: _resp = r.json()
                    except Exception: _resp = {"_raw_text": r.text[:1000]}
                    self._trace.append({
                        "endpoint": path, "method": method,
                        "partner_request": json_body if json_body is not None else params,
                        "http_status": r.status_code, "etg_response": _resp,
                    })

                if r.status_code == 401:
                    raise RateHawkError("Auth failed (KEY_ID/SECRET invalide)",
                                        status_code=401)
                if r.status_code == 403:
                    raise RateHawkError("Forbidden (compte non actif ou IP non whitelistée)",
                                        status_code=403)
                if r.status_code == 429:
                    if attempt < self.RETRY_MAX - 1:
                        time.sleep(self.RETRY_BACKOFF_S[attempt])
                        continue
                    raise RateHawkError("Rate limited", status_code=429)
                if 500 <= r.status_code < 600:
                    if attempt < self.RETRY_MAX - 1:
                        time.sleep(self.RETRY_BACKOFF_S[attempt])
                        continue
                    raise RateHawkError(f"Server error {r.status_code}",
                                        status_code=r.status_code)
                if r.status_code >= 400:
                    body = {}
                    try: body = r.json()
                    except Exception: pass
                    raise RateHawkError(
                        f"HTTP {r.status_code}: {body.get('error') or body.get('message') or r.text[:200]}",
                        status_code=r.status_code, raw=body,
                    )

                # WorldOTA renvoie {"status": "ok" | "error", "data": {...}}
                data = r.json()
                if isinstance(data, dict) and data.get("status") == "error":
                    raise RateHawkError(
                        data.get("error", "unknown_error"),
                        raw=data,
                    )
                return data
            except httpx.TimeoutException:
                last_exc = RateHawkError(f"Timeout sur {method} {path}")
                if attempt < self.RETRY_MAX - 1:
                    time.sleep(self.RETRY_BACKOFF_S[attempt])
                    continue
                raise last_exc
            except RateHawkError:
                raise
            except Exception as e:
                last_exc = RateHawkError(f"Transport error: {e}")
                if attempt < self.RETRY_MAX - 1:
                    time.sleep(self.RETRY_BACKOFF_S[attempt])
                    continue
                raise last_exc
        raise last_exc or RateHawkError("Retry épuisé")

    # ── Méthodes publiques ────────────────────────────────────────────────

    @staticmethod
    def _guests(guests: Optional[list], adults: int) -> list:
        """Normalise l'occupation. `guests` = liste de chambres [{adults, children:[âges]}]
        (multi-chambres + enfants, pour la certif) ; sinon 1 chambre d'`adults` adultes."""
        return guests if guests else [{"adults": adults, "children": []}]

    def search_hotels(self, region_id: int, check_in: str, check_out: str,
                      adults: int = 2, currency: str = "EUR",
                      language: str = "en", residency: str = "gb",
                      guests: Optional[list] = None) -> dict:
        """SERP-style search par region (= destination). `guests` = multi-chambres/enfants."""
        return self._request("POST", "/search/serp/region/", json_body={
            "checkin": check_in,
            "checkout": check_out,
            "residency": residency,
            "guests": self._guests(guests, adults),
            "region_id": region_id,
            "currency": currency,
            "language": language,
        })

    def search_hotel_page(self, hid: int, check_in: str, check_out: str,
                          adults: int = 2, currency: str = "EUR",
                          language: str = "en", residency: str = "gb",
                          guests: Optional[list] = None) -> dict:
        """Hotel page : toutes les chambres × tarifs d'1 hôtel précis.
        Doc ETG actuelle : identifiant = `hid` (entier), pas la clé texte ;
        `residency` (ISO2) requis ; `guests` = liste de chambres [{adults, children:[âges]}]
        (multi-chambres + enfants — indispensable aux cas de certif)."""
        return self._request("POST", "/search/hp/", json_body={
            "checkin": check_in,
            "checkout": check_out,
            "residency": residency,
            "language": language,
            "guests": self._guests(guests, adults),
            "hid": int(hid),
            "currency": currency,
        })

    def get_hotel_info(self, hotel_id: str, language: str = "en") -> dict:
        """Static content : descriptions, photos, équipements."""
        return self._request("GET", "/hotel/info/", params={
            "id": hotel_id,
            "language": language,
        })

    def prebook(self, book_hash: str, price_increase_percent: int = 20) -> dict:
        """Re-vérifie le tarif avant réservation (étape hotelpage → book_hash `h-…`).
        Doc actuelle : body = {hash, price_increase_percent} SEUL (pas d'ip_address).
        Renvoie un nouveau `book_hash` `p-…` à passer au form de réservation."""
        return self._request("POST", "/hotel/prebook/", json_body={
            "hash": book_hash,
            "price_increase_percent": price_increase_percent,
        })

    # ── Réservation en 3 temps (doc ETG actuelle) : form → finish → status ──
    def booking_form(self, *, partner_order_id: str, book_hash: str,
                     user_ip: str = "0.0.0.0", language: str = "en") -> dict:
        """1/3 — Crée le process de réservation. `book_hash` = le `p-…` du prebook."""
        return self._request("POST", "/hotel/order/booking/form/", json_body={
            "partner_order_id": partner_order_id,
            "book_hash": book_hash,
            "language": language,
            "user_ip": user_ip,
        })

    def booking_finish(self, *, partner_order_id: str, rooms: list[dict],
                       user: dict, supplier_data: dict, payment_type: dict,
                       language: str = "en", return_path: str = "https://airbizness.com/return") -> dict:
        """2/3 — Soumet les voyageurs + paiement. rooms=[{guests:[{first_name,last_name}]}].
        payment_type ex. {type:'deposit', amount:'…', currency_code:'EUR'} (dispo lu du form)."""
        return self._request("POST", "/hotel/order/booking/finish/", json_body={
            "user": user,
            "supplier_data": supplier_data,
            "partner": {"partner_order_id": partner_order_id},
            "language": language,
            "rooms": rooms,
            "payment_type": payment_type,
            "return_path": return_path,
        })

    def booking_status(self, partner_order_id: str) -> dict:
        """3/3 — Poll le statut de la réservation (finish/status)."""
        return self._request("POST", "/hotel/order/booking/finish/status/", json_body={
            "partner_order_id": partner_order_id,
        })

    def get_order(self, partner_order_id: str) -> dict:
        """Récupère la réservation confirmée (endpoint actuel /hotel/order/info/).
        Validé sandbox : renvoie orders[].{order_id, status, hotel_data, amount_payable}."""
        return self._request("POST", "/hotel/order/info/", json_body={
            "ordering": {"ordering_type": "desc", "ordering_by": "created_at"},
            "pagination": {"page_size": "1", "page_number": "1"},
            "search": {"order_ids": [], "partner_order_ids": [partner_order_id]},
            "language": "en",
        })

    def cancel_order(self, partner_order_id: str) -> dict:
        """Annulation (endpoint actuel /hotel/order/cancel/)."""
        return self._request("POST", "/hotel/order/cancel/", json_body={
            "partner_order_id": partner_order_id,
        })

    def health(self) -> dict:
        """Ping rapide : tente un search trivial pour valider auth + connectivité."""
        t0 = time.time()
        try:
            r = self._request("POST", "/search/multicomplete/", json_body={
                "query": "Paris", "language": "en",
            })
            return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                    "key_id": self.key_id, "env": self.env,
                    "regions_found": len((r.get("data") or {}).get("regions", []))}
        except RateHawkError as e:
            return {"ok": False, "error": str(e), "key_id": self.key_id, "env": self.env}
