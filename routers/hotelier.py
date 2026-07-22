from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response, JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
import os
import psycopg2
import psycopg2.extras
import json
import urllib.parse
import secrets as _secrets
import sib_api_v3_sdk
from slowapi import Limiter
from slowapi.util import get_remote_address

from providers.hbx.photos import extract_best_main_photo, extract_gallery_photos

BREVO_KEY = os.getenv("BREVO_KEY")
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()




def _gen_claim_token() -> str:
    """Token URL-safe pour le claim. 32 bytes = 43 chars en base64."""
    return _secrets.token_urlsafe(32)


class ClaimSendRequest(BaseModel):
    hotel_code: int


@router.post("/claim/send")
@limiter.limit("30/minute")
def claim_send(request: Request, body: ClaimSendRequest):
    """Envoie un email de revendication au contact HBX de l'hôtel."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT hotel_code, name, email, phone_main, city, country_code,
               category_stars, address, main_image_url
        FROM hbx_hotels_catalog WHERE hotel_code = %s
    """, (body.hotel_code,))
    hotel = cur.fetchone()
    if not hotel:
        cur.close(); conn.close()
        raise HTTPException(404, "Hôtel introuvable dans le catalog")
    if not hotel["email"]:
        cur.close(); conn.close()
        raise HTTPException(400, "Aucun email contact pour cet hôtel")

    # Crée un claim
    token = _gen_claim_token()
    cur.execute("""
        INSERT INTO hotel_claims
            (hotel_code, claim_token, target_email, target_phone, status, email_sent_at)
        VALUES (%s, %s, %s, %s, 'pending', NOW())
        RETURNING id
    """, (body.hotel_code, token, hotel["email"], hotel["phone_main"]))
    claim_id = cur.fetchone()["id"]
    conn.commit()

    # Envoi Brevo
    message_id = None
    err = None
    try:
        message_id = _send_claim_email(hotel, token)
    except Exception as e:
        err = str(e)[:300]

    cur.execute("""
        UPDATE hotel_claims SET email_message_id=%s, email_send_attempts=email_send_attempts+1,
               email_last_error=%s WHERE id=%s
    """, (message_id, err, claim_id))
    conn.commit()
    cur.close(); conn.close()

    return {
        "claim_id": claim_id,
        "token": token,
        "hotel_code": body.hotel_code,
        "hotel_name": hotel["name"],
        "sent_to": hotel["email"],
        "message_id": message_id,
        "error": err,
        "claim_url": f"https://airbizness.com/claim.html?token={token}",
    }


def _send_claim_email(hotel: dict, token: str) -> Optional[str]:
    """Envoie l'email de claim via Brevo. Retourne le messageId."""
    if not BREVO_KEY:
        raise RuntimeError("BREVO_KEY non configuré")
    cfg = sib_api_v3_sdk.Configuration()
    cfg.api_key["api-key"] = BREVO_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))

    claim_url = f"https://airbizness.com/claim.html?token={token}"
    hotel_name = hotel["name"]
    stars = "★" * (hotel.get("category_stars") or 0)
    city = hotel.get("city") or ""
    country = hotel.get("country_code") or ""
    img = hotel.get("main_image_url") or ""

    html = f"""
<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f3ee;font-family:'DM Sans',Arial,Helvetica,sans-serif;color:#2a2620;">
<div style="max-width:600px;margin:0 auto;background:#fff;">

  <!-- HERO -->
  <div style="background:#0f0f0f;padding:32px 32px 24px;text-align:center;">
    <div style="display:inline-block;width:32px;height:32px;background:#b8962e;clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);vertical-align:middle;"></div>
    <span style="font-size:20px;font-weight:600;color:#f0ece4;margin-left:8px;vertical-align:middle;">AirBizness</span>
    <div style="margin-top:18px;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#d4ae4a;">Réclamation de fiche établissement</div>
  </div>

  <!-- TITRE -->
  <div style="padding:36px 36px 18px;">
    <h1 style="font-family:Georgia,'DM Serif Display',serif;font-size:28px;line-height:1.2;margin:0 0 8px;color:#1a1a1a;font-weight:400;">
      Votre établissement <em style="color:#b8962e;font-style:italic;">{hotel_name}</em><br>
      apparaît sur AirBizness.
    </h1>
    <p style="color:#6a6058;font-size:14px;line-height:1.6;margin:14px 0;">
      Bonjour, notre marketplace référence votre établissement parmi les adresses recommandées dans la région
      <strong>{city}{', ' + country if country else ''}</strong>. Vous pouvez gratuitement revendiquer la fiche,
      gérer vos photos, votre description, et accéder à des conditions de partenariat direct.
    </p>
  </div>

  <!-- APERÇU FICHE -->
  {f'<div style="padding:0 36px;"><img src="{img}" alt="{hotel_name}" style="width:100%;height:240px;object-fit:cover;border-radius:10px;border:1px solid #e6e3da;"></div>' if img else ''}

  <div style="padding:18px 36px;background:#f9f8f3;border-top:1px solid #e6e3da;border-bottom:1px solid #e6e3da;margin:18px 0;">
    <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#6a6058;margin-bottom:6px;">Fiche actuelle</div>
    <div style="font-family:Georgia,serif;font-size:22px;color:#1a1a1a;margin-bottom:4px;">{hotel_name}</div>
    <div style="font-size:13px;color:#b8962e;margin-bottom:6px;">{stars} {hotel.get('category_stars') or ''} étoiles</div>
    <div style="font-size:13px;color:#6a6058;">{hotel.get('address') or ''}{', ' + city if city else ''}</div>
  </div>

  <!-- POURQUOI RÉCLAMER -->
  <div style="padding:0 36px 8px;">
    <h2 style="font-family:Georgia,serif;font-size:18px;color:#1a1a1a;margin:0 0 12px;">
      Pourquoi <em style="color:#b8962e;">réclamer votre fiche</em> ?
    </h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:8px 0;color:#6a6058;width:24px;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Contrôlez vos photos et votre description (mises en avant à vos clients)</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Accédez à un partenariat direct (commission inférieure aux wholesalers)</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Recevez les réservations à votre nom, sans intermédiaire visible</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Badge "Vérifié par l'établissement" — boost de visibilité et de confiance</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Suivi des réservations, statistiques, contact direct avec nos voyageurs</td></tr>
    </table>
  </div>

  <!-- CTA -->
  <div style="padding:32px 36px;text-align:center;">
    <a href="{claim_url}" style="display:inline-block;background:#b8962e;color:#000;font-weight:600;font-size:14px;padding:14px 32px;border-radius:8px;text-decoration:none;letter-spacing:0.4px;">
      Réclamer ma fiche gratuitement
    </a>
    <div style="font-size:11px;color:#a09890;margin-top:14px;">Lien valide 90 jours, à usage unique.</div>
  </div>

  <!-- FOOTER -->
  <div style="padding:24px 36px;background:#1a1a1a;color:#a09890;font-size:11px;line-height:1.6;text-align:center;">
    Vous recevez cet email parce que votre établissement <strong style="color:#f0ece4;">{hotel_name}</strong> est référencé via nos partenaires hôteliers professionnels.<br>
    Si vous n'êtes pas le propriétaire ou un manager autorisé, ignorez ce message — aucune fiche ne sera modifiée.<br><br>
    <span style="color:#6a6058;">AirBizness · Marketplace de voyage · Opéré depuis le Maroc</span>
  </div>

</div>
</body></html>
    """

    msg = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": hotel["email"], "name": hotel_name}],
        sender={"name": "AirBizness Partenariats", "email": "partenariats@airbizness.com"},
        reply_to={"email": "partenariats@airbizness.com", "name": "AirBizness Partenariats"},
        subject=f"[AirBizness] Votre fiche {hotel_name} — réclamation",
        html_content=html,
    )
    resp = api.send_transac_email(msg)
    return getattr(resp, "message_id", None) or str(resp)


@router.get("/claim/preview-email/{hotel_code}")
def claim_preview_email(hotel_code: int):
    """Retourne le HTML email tel qu'il sera envoyé — pour preview admin."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT hotel_code, name, email, phone_main, city, country_code,
               category_stars, address, main_image_url
        FROM hbx_hotels_catalog WHERE hotel_code = %s
    """, (hotel_code,))
    hotel = cur.fetchone()
    cur.close(); conn.close()
    if not hotel:
        raise HTTPException(404, "Hôtel introuvable")
    # Fake token pour preview
    fake_token = "PREVIEW-TOKEN-DO-NOT-USE"
    # Re-utilise _send_claim_email mais sans envoyer, juste rendre le HTML
    # On duplique le rendu HTML inline pour pas exécuter Brevo
    claim_url = f"https://airbizness.com/claim.html?token={fake_token}"
    hotel_name = hotel["name"]
    stars = "★" * (hotel.get("category_stars") or 0)
    city = hotel.get("city") or ""
    country = hotel.get("country_code") or ""
    img = hotel.get("main_image_url") or ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Preview</title></head>
<body style="margin:0;background:#f5f3ee;font-family:'DM Sans',Arial,sans-serif;color:#2a2620;">
<div style="max-width:600px;margin:30px auto;background:#fff;box-shadow:0 8px 32px rgba(0,0,0,0.12);">
  <div style="background:#0f0f0f;padding:32px 32px 24px;text-align:center;">
    <div style="display:inline-block;width:32px;height:32px;background:#b8962e;clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);vertical-align:middle;"></div>
    <span style="font-size:20px;font-weight:600;color:#f0ece4;margin-left:8px;vertical-align:middle;">AirBizness</span>
    <div style="margin-top:18px;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#d4ae4a;">Réclamation de fiche établissement</div>
  </div>
  <div style="padding:36px 36px 18px;">
    <h1 style="font-family:Georgia,serif;font-size:28px;line-height:1.2;margin:0 0 8px;color:#1a1a1a;font-weight:400;">Votre établissement <em style="color:#b8962e;font-style:italic;">{hotel_name}</em><br>apparaît sur AirBizness.</h1>
    <p style="color:#6a6058;font-size:14px;line-height:1.6;margin:14px 0;">Bonjour, notre marketplace référence votre établissement parmi les adresses recommandées dans la région <strong>{city}{', ' + country if country else ''}</strong>. Vous pouvez gratuitement revendiquer la fiche, gérer vos photos, votre description, et accéder à des conditions de partenariat direct.</p>
  </div>
  {f'<div style="padding:0 36px;"><img src="{img}" alt="{hotel_name}" style="width:100%;height:240px;object-fit:cover;border-radius:10px;border:1px solid #e6e3da;"></div>' if img else ''}
  <div style="padding:18px 36px;background:#f9f8f3;border-top:1px solid #e6e3da;border-bottom:1px solid #e6e3da;margin:18px 0;">
    <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#6a6058;margin-bottom:6px;">Fiche actuelle</div>
    <div style="font-family:Georgia,serif;font-size:22px;color:#1a1a1a;margin-bottom:4px;">{hotel_name}</div>
    <div style="font-size:13px;color:#b8962e;margin-bottom:6px;">{stars} {hotel.get('category_stars') or ''} étoiles</div>
    <div style="font-size:13px;color:#6a6058;">{hotel.get('address') or ''}{', ' + city if city else ''}</div>
  </div>
  <div style="padding:0 36px 8px;">
    <h2 style="font-family:Georgia,serif;font-size:18px;color:#1a1a1a;margin:0 0 12px;">Pourquoi <em style="color:#b8962e;">réclamer votre fiche</em> ?</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:8px 0;color:#6a6058;width:24px;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Contrôlez vos photos et votre description (mises en avant à vos clients)</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Accédez à un partenariat direct (commission inférieure aux wholesalers)</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Recevez les réservations à votre nom, sans intermédiaire visible</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Badge "Vérifié par l'établissement" — boost de visibilité et de confiance</td></tr>
      <tr><td style="padding:8px 0;color:#6a6058;vertical-align:top;">✓</td><td style="padding:8px 0;color:#2a2620;">Suivi des réservations, statistiques, contact direct avec nos voyageurs</td></tr>
    </table>
  </div>
  <div style="padding:32px 36px;text-align:center;">
    <a href="{claim_url}" style="display:inline-block;background:#b8962e;color:#000;font-weight:600;font-size:14px;padding:14px 32px;border-radius:8px;text-decoration:none;letter-spacing:0.4px;">Réclamer ma fiche gratuitement</a>
    <div style="font-size:11px;color:#a09890;margin-top:14px;">Lien valide 90 jours, à usage unique.</div>
  </div>
  <div style="padding:24px 36px;background:#1a1a1a;color:#a09890;font-size:11px;line-height:1.6;text-align:center;">Vous recevez cet email parce que votre établissement <strong style="color:#f0ece4;">{hotel_name}</strong> est référencé via nos partenaires hôteliers professionnels.<br>Si vous n'êtes pas le propriétaire ou un manager autorisé, ignorez ce message — aucune fiche ne sera modifiée.<br><br><span style="color:#6a6058;">AirBizness · Marketplace de voyage · Opéré depuis le Maroc</span></div>
</div>
</body></html>"""
    return Response(content=html, media_type="text/html")


@router.get("/claim/verify/{token}")
def claim_verify(token: str):
    """Retourne les infos du claim depuis le token (pour /claim.html)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.*, h.name AS hotel_name, h.city, h.country_code, h.category_stars,
               h.address, h.main_image_url, h.description_en, h.email AS hotel_email_hbx,
               h.phone_main, h.images_count, h.facilities_count
        FROM hotel_claims c
        JOIN hbx_hotels_catalog h ON h.hotel_code = c.hotel_code
        WHERE c.claim_token = %s
    """, (token,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return JSONResponse({"error": "invalid_token"}, status_code=404)
    if row["expires_at"] and row["expires_at"] < datetime.now(row["expires_at"].tzinfo):
        return JSONResponse({"error": "expired_token"}, status_code=410)

    d = dict(row)
    for k in ("created_at", "email_sent_at", "claimed_at", "expires_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    # Masque info sensible avant claim
    if d.get("status") == "pending":
        d.pop("target_email", None)
        d.pop("target_phone", None)
    return d


class ClaimActivateRequest(BaseModel):
    token: str
    manager_name: str
    manager_email: EmailStr
    manager_phone: Optional[str] = None
    manager_role: str = "manager"   # 'owner' | 'manager' | 'reception' | 'marketing'


@router.post("/claim/activate")
@limiter.limit("10/minute")
def claim_activate(request: Request, body: ClaimActivateRequest):
    """Active un claim : marque comme claimed + crée hotel_managed_data row."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM hotel_claims WHERE claim_token = %s", (body.token,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(404, "Token invalide")
    if row["status"] == "claimed":
        cur.close(); conn.close()
        return {"already_claimed": True, "hotel_code": row["hotel_code"]}
    if row["expires_at"] and row["expires_at"] < datetime.now(row["expires_at"].tzinfo):
        cur.close(); conn.close()
        raise HTTPException(410, "Token expiré")

    # Vérification "light" : l'email du manager DOIT matcher (au moins partiellement)
    # le domaine de l'email cible, OU être le même que target_email.
    target = (row["target_email"] or "").lower()
    sender = body.manager_email.lower()
    target_domain = target.split("@")[-1] if "@" in target else ""
    sender_domain = sender.split("@")[-1] if "@" in sender else ""
    auto_verified = (target == sender) or (target_domain and target_domain == sender_domain)

    ip = request.client.host if request.client else None
    cur.execute("""
        UPDATE hotel_claims SET status='claimed', claimed_at=NOW(),
            claimed_by_email=%s, claimed_by_name=%s, claimed_by_role=%s, claimed_ip=%s
        WHERE id = %s
    """, (body.manager_email, body.manager_name, body.manager_role, ip, row["id"]))

    # Crée ou met à jour la row managed_data
    cur.execute("""
        INSERT INTO hotel_managed_data (hotel_code, managed_by, manager_name, manager_phone, manager_role,
                                        is_verified, verification_method, verified_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_code) DO UPDATE SET
            managed_by = EXCLUDED.managed_by,
            manager_name = EXCLUDED.manager_name,
            manager_phone = EXCLUDED.manager_phone,
            manager_role = EXCLUDED.manager_role,
            updated_at = NOW()
    """, (row["hotel_code"], body.manager_email, body.manager_name, body.manager_phone,
          body.manager_role, auto_verified, 'email_match' if auto_verified else None,
          datetime.utcnow() if auto_verified else None))
    conn.commit()
    cur.close(); conn.close()

    return {
        "claimed": True,
        "hotel_code": row["hotel_code"],
        "auto_verified": auto_verified,
        "manager_url": f"https://airbizness.com/hotel-manager.html?token={body.token}",
    }


@router.get("/hotel-manager/profile")
def hotel_manager_profile(token: str):
    """Retourne le profil HBX + managed merged pour l'extranet."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.hotel_code AS claim_hotel_code, c.status, c.claimed_by_email, c.claimed_by_name,
               h.name, h.city, h.country_code, h.category_stars, h.address,
               h.email AS hbx_email, h.phone_main, h.web, h.description_en,
               h.images_count, h.facilities_count, h.main_image_url, h.raw,
               m.*
        FROM hotel_claims c
        JOIN hbx_hotels_catalog h ON h.hotel_code = c.hotel_code
        LEFT JOIN hotel_managed_data m ON m.hotel_code = c.hotel_code
        WHERE c.claim_token = %s
    """, (token,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return JSONResponse({"error": "invalid_token"}, status_code=404)
    if row["status"] not in ("claimed", "active"):
        return JSONResponse({"error": "not_claimed"}, status_code=403)

    d = dict(row)
    # hotel_code peut être null si pas de row hotel_managed_data → fallback claim
    if not d.get("hotel_code"):
        d["hotel_code"] = d.get("claim_hotel_code")
    d.pop("claim_hotel_code", None)
    raw = d.pop("raw", None) or {}
    # Galerie HBX par catégorie
    HBX_TYPE_MAP = {"HAB": "rooms", "RES": "restaurant", "BAR": "bar",
                    "GEN": "general", "CON": "general", "COM": "general",
                    "DEP": "outdoor", "TER": "outdoor"}
    gallery = {"rooms": [], "general": [], "restaurant": [], "bar": [], "outdoor": [], "other": []}
    for img in (raw.get("images") or [])[:50]:
        path = img.get("path") if isinstance(img, dict) else None
        if path:
            cat = HBX_TYPE_MAP.get(img.get("imageTypeCode") or "", "other")
            gallery[cat].append(f"https://photos.hotelbeds.com/giata/bigger/{path}")
    d["hbx_gallery"] = gallery
    # Convert dates
    for k in ("created_at", "updated_at", "verified_at", "last_login_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


class HotelManagerUpdateRequest(BaseModel):
    token: str
    display_name: Optional[str] = None
    short_description: Optional[str] = None
    long_description_fr: Optional[str] = None
    long_description_en: Optional[str] = None
    long_description_ar: Optional[str] = None
    phone_public: Optional[str] = None
    email_public: Optional[str] = None
    website: Optional[str] = None
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    child_policy: Optional[str] = None
    pet_policy: Optional[str] = None
    cancellation_text: Optional[str] = None
    main_photo_url: Optional[str] = None
    custom_facilities: Optional[list] = None
    owner_photos: Optional[list] = None


@router.post("/hotel-manager/update")
@limiter.limit("60/minute")
def hotel_manager_update(request: Request, body: HotelManagerUpdateRequest):
    """Update les champs édités par l'hôtelier."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.hotel_code, c.status, c.claimed_by_email
        FROM hotel_claims c WHERE c.claim_token = %s
    """, (body.token,))
    cl = cur.fetchone()
    if not cl:
        cur.close(); conn.close()
        raise HTTPException(404, "Token invalide")
    if cl["status"] not in ("claimed", "active"):
        cur.close(); conn.close()
        raise HTTPException(403, "Claim non actif")

    fields_to_update = body.dict(exclude_unset=True, exclude={"token"})
    if not fields_to_update:
        cur.close(); conn.close()
        return {"updated": False, "message": "Aucun champ fourni"}

    sets = []
    vals = []
    for k, v in fields_to_update.items():
        if k in ("custom_facilities", "owner_photos"):
            sets.append(f"{k}=%s::jsonb")
            vals.append(json.dumps(v))
        else:
            sets.append(f"{k}=%s")
            vals.append(v)
    sets.append("updated_at=NOW()")
    sets.append("last_login_at=NOW()")
    vals.append(cl["hotel_code"])

    cur.execute(f"""
        UPDATE hotel_managed_data SET {', '.join(sets)} WHERE hotel_code=%s
        RETURNING hotel_code, updated_at
    """, tuple(vals))
    res = cur.fetchone()

    # Audit log
    for k, v in fields_to_update.items():
        cur.execute("""
            INSERT INTO hotel_managed_audit (hotel_code, acted_by, action, field, after, ip)
            VALUES (%s, %s, 'update', %s, %s, %s)
        """, (cl["hotel_code"], cl["claimed_by_email"], k, str(v)[:500],
              request.client.host if request.client else None))
    conn.commit()
    cur.close(); conn.close()
    return {"updated": True, "hotel_code": cl["hotel_code"], "updated_at": res["updated_at"].isoformat()}


# ─────────────────────────────────────────────────────────────────────
# CONCIERGERIE HÔTELIER — Réserver pour mon client (Pascal 2026-05-24)
#
# L'hôtelier qui a claim sa fiche utilise AirBizness pour réserver
# vol / transfert / activité au nom d'un de ses clients VIP.
# 3 endpoints :
#   - POST /hotel-manager/booking-intent  : génère session_id + redirect_url
#   - GET  /hotel-manager/bookings-by-hotel : liste les résa initiées par cet hôtel
#   - GET  /hotel-manager/commissions      : commissions 5% cumulées
# ─────────────────────────────────────────────────────────────────────

class HotelManagerBookingIntentRequest(BaseModel):
    token: str
    service: str  # "flight" | "transfer" | "activity" | "hotel"
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_ref: Optional[str] = None  # n° chambre / référence interne


def _validate_hotel_manager_token(token: str):
    """Retourne (hotel_code, claimed_by_email) ou lève 403/404.
    Accepte status='claimed' OU 'active' (Pascal a posé des tokens en
    'active' pour la démo, l'ancien flow utilise 'claimed').
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT hotel_code, status, claimed_by_email, claimed_by_name, expires_at
        FROM hotel_claims WHERE claim_token = %s
    """, (token,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise HTTPException(404, "Token invalide")
    if row["status"] not in ("claimed", "active"):
        raise HTTPException(403, "Claim non actif")
    if row["expires_at"] and row["expires_at"] < datetime.now(row["expires_at"].tzinfo):
        raise HTTPException(410, "Token expiré")
    return row


@router.post("/hotel-manager/booking-intent")
@limiter.limit("60/minute")
def hotel_manager_booking_intent(request: Request, body: HotelManagerBookingIntentRequest):
    """Le concierge clique sur Vol/Transfert/Activité depuis l'extranet hôtelier.
    Renvoie une redirect_url avec les query params on_behalf_of + guest_*."""
    claim = _validate_hotel_manager_token(body.token)
    hotel_code = str(claim["hotel_code"])

    svc = (body.service or "").lower()
    if svc not in ("flight", "transfer", "activity", "hotel"):
        raise HTTPException(400, "Service invalide (flight/transfer/activity/hotel)")

    import uuid as _u2
    session_id = _u2.uuid4().hex[:12]

    # Build redirect URL avec query params standardisés
    qs_parts = [
        f"on_behalf_of={hotel_code}",
        f"hm_session={session_id}",
    ]
    if body.guest_name:
        qs_parts.append(f"guest_name={urllib.parse.quote(body.guest_name)}")
    if body.guest_email:
        qs_parts.append(f"guest_email={urllib.parse.quote(body.guest_email)}")
    if body.guest_ref:
        qs_parts.append(f"guest_ref={urllib.parse.quote(body.guest_ref)}")
    qs = "&".join(qs_parts)

    if svc == "flight":
        redirect_path = f"/resultats.html?{qs}"
    elif svc == "transfer":
        # Tunnel transfert seul = pas encore de page dédiée → on dirige vers
        # le tunnel hôtel qui propose les transferts en option.
        redirect_path = f"/hotels.html?{qs}"
    elif svc == "activity":
        redirect_path = f"/activites.html?{qs}"
    else:  # hotel
        redirect_path = f"/hotels.html?{qs}"

    return {
        "session_id": session_id,
        "redirect_url": redirect_path,
        "on_behalf_of": hotel_code,
    }


@router.get("/hotel-manager/bookings-by-hotel")
def hotel_manager_bookings_by_hotel(token: str, limit: int = 20):
    """Liste les résa initiées par cet hôtel (on_behalf_of=<hotel_code>).
    Cherche dans pack_bookings.raw_payload, flight_bookings.raw_offer,
    bookings_v2.hbx_booking_raw.airbizness_options, activity_bookings_v2."""
    claim = _validate_hotel_manager_token(token)
    hotel_code = str(claim["hotel_code"])

    limit = max(1, min(int(limit or 20), 100))
    items: list = []

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 1) Vols
        cur.execute("""
            SELECT airbizness_ref, origin, destination, status, total_eur,
                   created_at, raw_offer
            FROM flight_bookings
            WHERE raw_offer->'on_behalf_of'->>'hotel_code' = %s
            ORDER BY created_at DESC LIMIT %s
        """, (hotel_code, limit))
        for r in cur.fetchall():
            raw = r["raw_offer"] or {}
            obo = (raw.get("on_behalf_of") or {}) if isinstance(raw, dict) else {}
            items.append({
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "service": "flight",
                "service_label": "Vol",
                "guest_name": obo.get("guest_name") or "—",
                "guest_ref": obo.get("guest_ref"),
                "status": r["status"],
                "total_eur": float(r["total_eur"] or 0),
                "ref": r["airbizness_ref"],
                "detail": f"{r['origin'] or ''} → {r['destination'] or ''}".strip(" →"),
            })

        # 2) Packs
        cur.execute("""
            SELECT airbizness_ref, flight_origin, flight_destination, hotel_name,
                   status, total_amount, created_at, raw_payload
            FROM pack_bookings
            WHERE raw_payload->>'on_behalf_of' = %s
            ORDER BY created_at DESC LIMIT %s
        """, (hotel_code, limit))
        for r in cur.fetchall():
            raw = r["raw_payload"] or {}
            items.append({
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "service": "pack",
                "service_label": "Pack vol+hôtel",
                "guest_name": raw.get("guest_name") or "—",
                "guest_ref": raw.get("guest_ref"),
                "status": r["status"],
                "total_eur": float(r["total_amount"] or 0),
                "ref": r["airbizness_ref"],
                "detail": f"{r['flight_origin'] or ''} → {r['flight_destination'] or ''} · {r['hotel_name'] or ''}",
            })

        # 3) Hôtel seul (bookings_v2)
        cur.execute("""
            SELECT airbizness_ref, hotel_name, check_in, check_out, status,
                   gross_price, created_at, hbx_booking_raw
            FROM bookings_v2
            WHERE hbx_booking_raw->'airbizness_options'->>'on_behalf_of' = %s
            ORDER BY created_at DESC LIMIT %s
        """, (hotel_code, limit))
        for r in cur.fetchall():
            raw = r["hbx_booking_raw"] or {}
            opts = (raw.get("airbizness_options") or {}) if isinstance(raw, dict) else {}
            items.append({
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "service": "hotel",
                "service_label": "Hôtel",
                "guest_name": opts.get("guest_name") or "—",
                "guest_ref": opts.get("guest_ref"),
                "status": r["status"],
                "total_eur": float(r["gross_price"] or 0),
                "ref": r["airbizness_ref"],
                "detail": f"{r['hotel_name'] or ''} · {r['check_in']}→{r['check_out']}",
            })

        # 4) Activités
        cur.execute("""
            SELECT airbizness_ref, activity_name, operation_date, status,
                   gross_price, created_at, hbx_booking_raw
            FROM activity_bookings_v2
            WHERE hbx_booking_raw->'airbizness_options'->'on_behalf_of'->>'hotel_code' = %s
            ORDER BY created_at DESC LIMIT %s
        """, (hotel_code, limit))
        for r in cur.fetchall():
            raw = r["hbx_booking_raw"] or {}
            obo = (((raw.get("airbizness_options") or {}).get("on_behalf_of")) or {}) if isinstance(raw, dict) else {}
            items.append({
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "service": "activity",
                "service_label": "Activité",
                "guest_name": obo.get("guest_name") or "—",
                "guest_ref": obo.get("guest_ref"),
                "status": r["status"],
                "total_eur": float(r["gross_price"] or 0),
                "ref": r["airbizness_ref"],
                "detail": f"{r['activity_name'] or ''} · {r['operation_date']}",
            })
    finally:
        cur.close(); conn.close()

    # Tri global desc + limit
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return {
        "hotel_code": hotel_code,
        "count": len(items),
        "items": items[:limit],
    }


@router.get("/hotel-manager/commissions")
def hotel_manager_commissions(token: str, period_days: int = 30):
    """Calcule la commission 5% sur les résa initiées par cet hôtel.
    Période : derniers `period_days` jours. Seules les résa confirmées comptent."""
    claim = _validate_hotel_manager_token(token)
    hotel_code = str(claim["hotel_code"])

    period_days = max(1, min(int(period_days or 30), 365))
    commission_rate = 0.05  # 5%

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    by_service = {"flight": 0.0, "pack": 0.0, "hotel": 0.0, "activity": 0.0}
    count_by_service = {"flight": 0, "pack": 0, "hotel": 0, "activity": 0}

    try:
        # Vols confirmés
        cur.execute("""
            SELECT COALESCE(SUM(total_eur),0) AS s, COUNT(*) AS c
            FROM flight_bookings
            WHERE raw_offer->'on_behalf_of'->>'hotel_code' = %s
              AND status = 'confirmed'
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hotel_code, str(period_days)))
        row = cur.fetchone()
        by_service["flight"] = float(row["s"] or 0)
        count_by_service["flight"] = int(row["c"] or 0)

        # Packs confirmés
        cur.execute("""
            SELECT COALESCE(SUM(total_amount),0) AS s, COUNT(*) AS c
            FROM pack_bookings
            WHERE raw_payload->>'on_behalf_of' = %s
              AND status = 'confirmed'
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hotel_code, str(period_days)))
        row = cur.fetchone()
        by_service["pack"] = float(row["s"] or 0)
        count_by_service["pack"] = int(row["c"] or 0)

        # Hôtels confirmés
        cur.execute("""
            SELECT COALESCE(SUM(gross_price),0) AS s, COUNT(*) AS c
            FROM bookings_v2
            WHERE hbx_booking_raw->'airbizness_options'->>'on_behalf_of' = %s
              AND status = 'confirmed'
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hotel_code, str(period_days)))
        row = cur.fetchone()
        by_service["hotel"] = float(row["s"] or 0)
        count_by_service["hotel"] = int(row["c"] or 0)

        # Activités confirmées
        cur.execute("""
            SELECT COALESCE(SUM(gross_price),0) AS s, COUNT(*) AS c
            FROM activity_bookings_v2
            WHERE hbx_booking_raw->'airbizness_options'->'on_behalf_of'->>'hotel_code' = %s
              AND status = 'confirmed'
              AND created_at > NOW() - (%s || ' days')::interval
        """, (hotel_code, str(period_days)))
        row = cur.fetchone()
        by_service["activity"] = float(row["s"] or 0)
        count_by_service["activity"] = int(row["c"] or 0)
    finally:
        cur.close(); conn.close()

    total_gross = sum(by_service.values())
    total_count = sum(count_by_service.values())
    total_commission = round(total_gross * commission_rate, 2)

    return {
        "hotel_code": hotel_code,
        "period_days": period_days,
        "commission_rate": commission_rate,
        "total_gross_eur": round(total_gross, 2),
        "total_commission_eur": total_commission,
        "count_bookings": total_count,
        "by_service": {
            k: {
                "gross_eur": round(v, 2),
                "commission_eur": round(v * commission_rate, 2),
                "count": count_by_service[k],
            } for k, v in by_service.items()
        },
    }


@router.get("/claim/stats")
def claim_stats():
    """Stats globales programme claim."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
          (SELECT COUNT(*) FROM hbx_hotels_catalog WHERE email IS NOT NULL AND email<>'') AS eligible,
          (SELECT COUNT(*) FROM hotel_claims) AS sent,
          (SELECT COUNT(*) FROM hotel_claims WHERE status='claimed') AS claimed,
          (SELECT COUNT(*) FROM hotel_claims WHERE status='pending') AS pending,
          (SELECT COUNT(*) FROM hotel_managed_data WHERE is_verified=true) AS verified
    """)
    return dict(cur.fetchone())


@router.get("/hotels/manager-extranet", response_class=HTMLResponse)
def serve_hotel_manager_extranet():
    try:
        with open("/var/www/airbizness/public/hotel-manager.html", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Extranet hôtelier indisponible</h1>", status_code=404)


# ── Preview admin : vraie home (pré-coming-soon) accessible à Pascal pour tester
# son travail sans subir le filtre nginx qui aliase tout vers coming-soon.
# Path /hotels/* est routé vers FastAPI, on profite du pattern.
@router.get("/hotels/admin-preview", response_class=HTMLResponse)
def serve_admin_preview_home():
    try:
        with open("/var/www/airbizness/public/admin-home.html", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Vraie home indisponible (backup manquant)</h1>", status_code=404)



@router.get("/hbx/catalog/cron-progress")
def hbx_cron_progress():
    """Progress du cron progressif HBX catalog."""
    conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE last_status = 'done') AS done,
          COUNT(*) FILTER (WHERE last_status = 'rate_limited') AS rate_limited,
          COUNT(*) FILTER (WHERE last_status = 'quota_exceeded') AS quota_blocked,
          COUNT(*) FILTER (WHERE last_status = 'error') AS errored,
          COUNT(*) FILTER (WHERE last_status IS NULL OR last_status = 'pending') AS pending,
          COUNT(*) FILTER (WHERE last_status = 'ok') AS in_progress,
          COUNT(*) AS total,
          COALESCE(SUM(hotels_fetched), 0) AS hotels_fetched,
          MAX(last_sync_at) AS last_sync_at
        FROM hbx_catalog_sync_state
    """)
    g = dict(cur.fetchone() or {})

    # Par priorité
    cur.execute("""
        SELECT priority,
               COUNT(*) AS destinations,
               COUNT(*) FILTER (WHERE last_status = 'done') AS done,
               SUM(hotels_fetched) AS hotels
        FROM hbx_catalog_sync_state
        GROUP BY priority ORDER BY priority
    """)
    by_prio = [dict(r) for r in cur.fetchall()]

    # 10 dernières destinations syncées
    cur.execute("""
        SELECT destination_code, destination_name, country_code,
               hotels_fetched, total_available, last_status, last_sync_at, last_error
        FROM hbx_catalog_sync_state
        WHERE last_sync_at IS NOT NULL
        ORDER BY last_sync_at DESC LIMIT 12
    """)
    recent = [dict(r) for r in cur.fetchall()]

    # Top blocages
    cur.execute("""
        SELECT destination_code, last_status, attempts, last_error, next_try_at
        FROM hbx_catalog_sync_state
        WHERE last_status IN ('rate_limited', 'quota_exceeded', 'error')
        ORDER BY attempts DESC LIMIT 5
    """)
    blockers = [dict(r) for r in cur.fetchall()]

    cur.close(); conn.close()
    return {
        "global": g,
        "by_priority": by_prio,
        "recent_syncs": recent,
        "blockers": blockers,
    }


@router.get("/api/admin/hbx-sync/status")
def admin_hbx_sync_status():
    """Statut consolidé du sync HBX full-catalog.

    Retourne :
      - total_destinations / destinations_synced / destinations_pending
      - hotels_fetched_total (somme catalog_sync_state.hotels_fetched)
      - hotels_in_catalog (hbx_hotels_catalog row count)
      - hotels_in_canonical / hotels_in_provider_map
      - last_sync_at / last_status (du run le plus récent)
      - top_recent (10 dernières destinations syncées)
      - error_summary (compte par statut)
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
          COUNT(*) AS total_destinations,
          COUNT(*) FILTER (WHERE last_status = 'done') AS destinations_done,
          COUNT(*) FILTER (WHERE last_status = 'partial') AS destinations_partial,
          COUNT(*) FILTER (WHERE last_status IS NULL OR last_status NOT IN ('done', 'quota_exceeded')) AS destinations_pending,
          COUNT(*) FILTER (WHERE last_sync_at IS NOT NULL) AS destinations_touched,
          COUNT(*) FILTER (WHERE last_status = 'error') AS destinations_error,
          COUNT(*) FILTER (WHERE last_status = 'rate_limited') AS destinations_rate_limited,
          COUNT(*) FILTER (WHERE last_status = 'quota_exceeded') AS destinations_quota_blocked,
          COALESCE(SUM(hotels_fetched), 0) AS hotels_fetched_total,
          MAX(last_sync_at) AS last_sync_at
        FROM hbx_catalog_sync_state
    """)
    s = dict(cur.fetchone() or {})

    cur.execute("SELECT COUNT(*) AS n FROM hbx_hotels_catalog")
    s["hotels_in_catalog"] = (cur.fetchone() or {}).get("n", 0)

    cur.execute("SELECT COUNT(*) AS n FROM hotels_canonical")
    s["hotels_in_canonical"] = (cur.fetchone() or {}).get("n", 0)

    cur.execute("SELECT COUNT(*) AS n FROM hotels_provider_map WHERE provider = 'hbx'")
    s["hotels_in_provider_map_hbx"] = (cur.fetchone() or {}).get("n", 0)

    cur.execute("""
        SELECT destination_code, destination_name, country_code,
               hotels_fetched, total_available, last_status, last_sync_at
        FROM hbx_catalog_sync_state
        WHERE last_sync_at IS NOT NULL
        ORDER BY last_sync_at DESC
        LIMIT 10
    """)
    s["top_recent"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT status, COUNT(*) AS n
        FROM hbx_catalog_sync_log
        WHERE started_at > NOW() - INTERVAL '1 hour'
        GROUP BY status
        ORDER BY n DESC
    """)
    s["recent_log_summary_1h"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT destination_code, destination_label, status,
               fetched, inserted, updated, errors,
               started_at, finished_at, error_detail
        FROM hbx_catalog_sync_log
        ORDER BY started_at DESC
        LIMIT 5
    """)
    s["last_runs"] = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()
    return s


@router.get("/hbx/catalog/stats")
def hbx_catalog_stats():
    """Compteur global du catalog HBX (combien d'hôtels par destination, complétude)."""
    conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
          COUNT(*)                                  AS total,
          COUNT(latitude)                           AS avec_gps,
          COUNT(email) FILTER (WHERE email <> '')   AS avec_email,
          COUNT(phone_main)                         AS avec_phone,
          COUNT(web)                                AS avec_web,
          COUNT(description_en)                     AS avec_desc_en,
          COUNT(description_fr)                     AS avec_desc_fr,
          COALESCE(SUM(images_count), 0)            AS total_images,
          MAX(updated_at)                           AS dernier_sync
        FROM hbx_hotels_catalog
    """)
    global_stats = dict(cur.fetchone() or {})

    cur.execute("""
        SELECT destination_code,
               COUNT(*) AS hotels,
               COUNT(*) FILTER (WHERE category_stars = 5) AS five_stars,
               COUNT(*) FILTER (WHERE category_stars = 4) AS four_stars,
               COUNT(*) FILTER (WHERE category_stars = 3) AS three_stars,
               COUNT(latitude) AS avec_gps,
               MAX(updated_at) AS last_seen
        FROM hbx_hotels_catalog
        GROUP BY destination_code
        ORDER BY hotels DESC
    """)
    by_dest = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT id, destination_code, destination_label, phase,
               started_at, finished_at, status,
               total_available, fetched, inserted, updated, errors, error_detail
        FROM hbx_catalog_sync_log
        ORDER BY started_at DESC
        LIMIT 30
    """)
    recent_syncs = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT destination_code,
               SUM(total_available) FILTER (WHERE total_available IS NOT NULL) AS total_disponible,
               MAX(fetched) AS deja_fetched
        FROM hbx_catalog_sync_log
        WHERE status = 'ok'
        GROUP BY destination_code
    """)
    coverage = {r["destination_code"]: dict(r) for r in cur.fetchall()}

    cur.close(); conn.close()
    return {
        "global": global_stats,
        "by_destination": by_dest,
        "recent_syncs": recent_syncs,
        "coverage": coverage,
    }


@router.get("/hbx/catalog/hotel/{hotel_code}")
def hbx_catalog_hotel_detail(hotel_code: int):
    """Fiche hôtel complète depuis le catalog DB (pas d'appel HBX live)."""
    conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT hotel_code, name, category_code, category_stars, chain_code,
               destination_code, zone_code, country_code, state_code,
               city, address, postal_code, latitude, longitude,
               email, phone_main, phone_fax, web, giata_code, ranking,
               description_en, description_fr,
               images_count, facilities_count, main_image_url,
               raw, last_update_hbx, updated_at
        FROM hbx_hotels_catalog WHERE hotel_code = %s
    """, (hotel_code,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    h = dict(row)
    raw = h.get("raw") or {}

    # Images : construction URLs CDN HBX
    images = []
    for img in (raw.get("images") or [])[:30]:
        path = img.get("path")
        if path:
            images.append({
                "url": f"https://photos.hotelbeds.com/giata/bigger/{path}",
                "type_code": img.get("imageTypeCode"),
                "order": img.get("order"),
            })

    h["images"] = images
    h["rooms"] = (raw.get("rooms") or [])[:20]
    h["facilities"] = raw.get("facilities") or []
    h["interest_points"] = raw.get("interestPoints") or []
    h["terminals"] = raw.get("terminals") or []
    h["board_codes"] = raw.get("boardCodes") or []
    h["segment_codes"] = raw.get("segmentCodes") or []
    h.pop("raw", None)  # pas la peine de tout renvoyer
    return h


@router.get("/hbx/catalog/hotels")
def hbx_catalog_hotels(
    destination: Optional[str] = None,
    country: Optional[str] = None,
    min_stars: Optional[int] = None,
    has_email: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Liste paginée du catalog (pour vérifier visuellement ce qu'on a)."""
    where = ["1=1"]
    params = []
    if destination:
        where.append("destination_code = %s"); params.append(destination.upper())
    if country:
        where.append("country_code = %s"); params.append(country.upper())
    if min_stars:
        where.append("category_stars >= %s"); params.append(min_stars)
    if has_email:
        where.append("email IS NOT NULL AND email <> ''")
    if q and q.strip():
        where.append("(name ILIKE %s OR city ILIKE %s)")
        like = "%" + q.strip() + "%"
        params.append(like); params.append(like)

    # Exclure les hôtels test HBX (Inventado Test, Rene Test Bot Hotels, etc.)
    where.append("name NOT ILIKE %s"); params.append('%test%')
    where.append("name NOT ILIKE %s"); params.append('%bot%')
    where.append("name NOT IN ('Inventado Test', 'This Hotel Is A Testing')")

    conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT hotel_code, name, category_stars, chain_code,
               destination_code, country_code, city, address, postal_code,
               latitude, longitude, email, phone_main, web,
               images_count, facilities_count, main_image_url,
               LEFT(description_en, 200) AS description_short,
               raw,
               updated_at
        FROM hbx_hotels_catalog
        WHERE {' AND '.join(where)}
        ORDER BY category_stars DESC NULLS LAST, ranking DESC NULLS LAST, hotel_code
        LIMIT %s OFFSET %s
    """, params + [limit, offset])
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        raw = d.pop("raw", None)
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except Exception: raw = {}
        if not isinstance(raw, dict):
            raw = {}
        imgs = raw.get("images") or []
        if not isinstance(imgs, list):
            imgs = []
        # Mapping unifié multi-provider : main = GEN+order min, gallery = espaces communs
        best_main = extract_best_main_photo(imgs, provider="hbx")
        if best_main:
            d["main_image_url"] = best_main  # override le main_image_url DB (souvent mauvais code)
        d["gallery_photos"] = extract_gallery_photos(imgs, provider="hbx", max_photos=8)
        # Si gallery vide (pas de raw.images), fallback : au moins la main si elle existe
        if not d["gallery_photos"] and d.get("main_image_url"):
            d["gallery_photos"] = [d["main_image_url"]]
        d["images_total"] = d.get("images_count") or len(imgs) or len(d["gallery_photos"])
        rows.append(d)

    cur.execute(f"SELECT COUNT(*) AS n FROM hbx_hotels_catalog WHERE {' AND '.join(where)}", params)
    total = cur.fetchone()["n"]
    cur.close(); conn.close()
    return {"total": total, "limit": limit, "offset": offset, "hotels": rows}


