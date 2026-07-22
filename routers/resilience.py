from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


class ActivateSubstituteRequest(BaseModel):
    substitute_index: int = 0
    reason: str = "manual"


class ValidateBookingRequest(BaseModel):
    passengers: list
    email: str
    phone: str
    country: str
    arrival_time: Optional[str] = None
    flight_number: Optional[str] = None
    special_requests: Optional[str] = None


@router.post("/resilience/substitutes/precompute")
@limiter.limit("10/minute")
def resilience_precompute_substitutes(request: Request, booking_ref: Optional[str] = None):
    """Lance le calcul de substitutes pour 1 booking ou tous les actifs.
    À appeler par cron toutes les 6h OU manuellement pour un booking."""
    from resilience import precompute_substitutes
    return precompute_substitutes(booking_ref=booking_ref)


@router.get("/resilience/substitutes/{booking_ref}")
def resilience_get_substitutes(booking_ref: str):
    """Retourne les substitutes pré-calculés pour un booking (utilisé par chatbot)."""
    from resilience import get_substitutes
    return get_substitutes(booking_ref)


@router.post("/resilience/substitutes/{booking_ref}/activate-hotel")
@limiter.limit("20/minute")
def resilience_activate_sub_hotel(request: Request, booking_ref: str,
                                    body: ActivateSubstituteRequest):
    """Active le substitut hôtel #N : booke HBX nouveau + cancel ancien +
    cushion absorbe différentiel + log substitution."""
    from resilience import activate_substitute_hotel
    return activate_substitute_hotel(booking_ref, body.substitute_index, body.reason)


@router.post("/resilience/substitutes/{booking_ref}/activate-flight")
@limiter.limit("20/minute")
def resilience_activate_sub_flight(request: Request, booking_ref: str,
                                     body: ActivateSubstituteRequest):
    """Active le substitut vol #N (Phase 1 : log intention pour conciergerie)."""
    from resilience import activate_substitute_flight
    return activate_substitute_flight(booking_ref, body.substitute_index, body.reason)


@router.get("/resilience/cushion/history")
def resilience_cushion_history(airbizness_ref: Optional[str] = None, limit: int = 50):
    """Liste les mouvements cushion (refunds, indemnités, prepay substituts)."""
    from resilience import get_cushion_history
    return {"movements": get_cushion_history(airbizness_ref, limit)}


@router.get("/resilience/cushion/balance")
def resilience_cushion_balance():
    """Solde cushion + pending."""
    from resilience.cushion import get_cushion_balance
    return get_cushion_balance()


@router.post("/resilience/validation/booking")
def resilience_validate_booking(body: ValidateBookingRequest):
    """Valide tous les champs du form pack en une fois."""
    from resilience import validate_booking_input
    return validate_booking_input(body.dict())


@router.post("/resilience/monitoring/check-all")
@limiter.limit("6/minute")
def resilience_check_all_providers(request: Request):
    """Lance un health check sur tous les providers. À appeler par cron 5 min."""
    from resilience.monitoring import check_all_providers
    return check_all_providers()


@router.get("/resilience/monitoring/status")
def resilience_providers_status():
    """État actuel de tous les providers (lecture seule)."""
    from resilience import get_providers_status
    return {"providers": get_providers_status()}


@router.post("/resilience/idempotency/reconcile")
@limiter.limit("6/minute")
def resilience_reconcile_pending(request: Request, max_age_minutes: int = 15):
    """Cron 5 min : reconcile bookings stuck en payment_pending."""
    from resilience import reconcile_pending_bookings
    return reconcile_pending_bookings(max_age_minutes)
