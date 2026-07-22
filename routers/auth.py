"""
Auth endpoints : signup, login, me — Pascal 2026-05-31.

Coexiste avec l'auth Google existante (main.py /api/account/google).
On stocke password_hash dans users (colonne nullable, NULL pour les users Google).
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from main import DB_CONFIG
from services.auth_token import (
    hash_password, verify_password, generate_token, get_current_user,
    make_single_use_token, hash_single_use_token
)
from services.mail import _send_brevo

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://airbizness.com").rstrip("/")

log = logging.getLogger("auth")
# Pas de prefix /api : nginx strip déjà /api/ avant forward (les autres routers idem)
router = APIRouter(prefix="/auth", tags=["auth"])


# ───────────────────────── Schémas ─────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ───────────────────────── Endpoints ─────────────────────────

@router.post("/signup")
def signup(req: SignupRequest):
    """Crée un compte email+password → JWT 30j.
    Auto-ajoute aussi un user_passenger is_self=TRUE (le titulaire = 1er voyageur)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Email déjà pris ?
        cur.execute("SELECT id, password_hash FROM users WHERE LOWER(email) = LOWER(%s)", (req.email,))
        existing = cur.fetchone()
        if existing:
            if existing.get("password_hash"):
                raise HTTPException(409, "Cet email a déjà un compte. Connectez-vous.")
            # User Google existe sans password → on l'enrichit
            pwd_hash = hash_password(req.password)
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (pwd_hash, existing["id"])
            )
            user_id = existing["id"]
        else:
            # Nouveau user
            pwd_hash = hash_password(req.password)
            full_name = f"{req.first_name} {req.last_name}".strip()
            cur.execute(
                """
                INSERT INTO users (email, password_hash, full_name, given_name, auth_provider, email_verified, created_at)
                VALUES (%s, %s, %s, %s, 'email', FALSE, NOW())
                RETURNING id
                """,
                (req.email, pwd_hash, full_name, req.first_name)
            )
            user_id = cur.fetchone()["id"]

        # Auto-crée le user_passenger is_self s'il n'existe pas déjà
        cur.execute(
            "SELECT id FROM user_passengers WHERE user_id = %s AND is_self = TRUE",
            (user_id,)
        )
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO user_passengers (user_id, first_name, last_name, type, is_self)
                VALUES (%s, %s, %s, 'adult', TRUE)
                """,
                (user_id, req.first_name, req.last_name)
            )

        # Fetch user pour réponse
        cur.execute(
            "SELECT id, email, full_name, given_name, picture_url FROM users WHERE id = %s",
            (user_id,)
        )
        user = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()

    token = generate_token(user["id"], user["email"])
    return {"token": token, "user": dict(user)}


@router.post("/login")
def login(req: LoginRequest):
    """Login email+password → JWT 30j."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, email, password_hash, full_name, given_name, picture_url FROM users WHERE LOWER(email) = LOWER(%s)",
            (req.email,)
        )
        user = cur.fetchone()
        if not user or not user.get("password_hash") or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(401, "Email ou mot de passe incorrect")
        # Maj last_login
        cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
        conn.commit()
    finally:
        cur.close(); conn.close()

    token = generate_token(user["id"], user["email"])
    user.pop("password_hash", None)
    return {"token": token, "user": dict(user)}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """Retourne infos user courant (JWT requis)."""
    return current_user


@router.post("/logout")
def logout():
    """Logout côté serveur = no-op (JWT stateless). Le client doit jeter son token."""
    return {"ok": True, "msg": "Token à supprimer côté client (localStorage.removeItem)"}


# ════════════════════════════════════════════════════════════════════════
# TOKENS À USAGE UNIQUE — Pascal 2026-05-31
# Magic-link (login sans password), password-reset, email-verify
# ════════════════════════════════════════════════════════════════════════

class EmailOnlyRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


def _create_token(user_id: int, email: str, purpose: str, ttl_minutes: int = 15) -> str:
    """Crée un token à usage unique en DB. Retourne le raw token (à envoyer par email)."""
    raw, hashed = make_single_use_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO user_tokens (user_id, email, token_hash, purpose, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, email, hashed, purpose, expires_at)
        )
        conn.commit()
    finally:
        cur.close(); conn.close()
    return raw


def _consume_token(raw: str, purpose: str) -> dict:
    """Valide + consomme un token. Retourne {user_id, email}. Lève 400 si invalide/expiré/déjà consommé."""
    hashed = hash_single_use_token(raw)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT id, user_id, email, expires_at, consumed_at
            FROM user_tokens
            WHERE token_hash = %s AND purpose = %s
            """,
            (hashed, purpose)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Lien invalide ou expiré")
        if row["consumed_at"]:
            raise HTTPException(400, "Ce lien a déjà été utilisé")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(400, "Ce lien a expiré (durée de validité 15 minutes)")
        # Marque consumed
        cur.execute("UPDATE user_tokens SET consumed_at = NOW() WHERE id = %s", (row["id"],))
        conn.commit()
        return {"user_id": row["user_id"], "email": row["email"]}
    finally:
        cur.close(); conn.close()


def _send_token_email(email: str, subject: str, html_body: str) -> bool:
    """Wrapper Brevo. Si pas de KEY → log silencieux mais ne casse pas l'endpoint."""
    try:
        return _send_brevo(email, email, subject, html_body)
    except Exception as e:
        log.error(f"[email] envoi échoué à {email} : {e}")
        return False


def _mail_template(title: str, intro: str, cta_label: str, cta_url: str, hint: str = "") -> str:
    """Template HTML simple, sobre, charte AirBizness."""
    return f"""
    <div style="background:#0f0f0f;padding:40px 20px;font-family:'DM Sans',Arial,sans-serif;color:#f0ece4;">
      <div style="max-width:520px;margin:0 auto;background:#161616;border:1px solid rgba(184,150,46,0.2);border-radius:14px;padding:34px 28px;">
        <div style="font-family:'DM Serif Display',Georgia,serif;font-size:24px;margin-bottom:6px;">Air<em style="color:#d4ae4a;font-style:italic;">Bizness</em></div>
        <h1 style="font-family:'DM Serif Display',serif;font-size:24px;margin:18px 0 12px;color:#f0ece4;">{title}</h1>
        <p style="color:#a09890;font-size:14.5px;line-height:1.6;margin-bottom:22px;">{intro}</p>
        <a href="{cta_url}" style="display:inline-block;background:#b8962e;color:#000;padding:13px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">{cta_label}</a>
        {f'<p style="color:#6a6058;font-size:12px;margin-top:24px;line-height:1.5;">{hint}</p>' if hint else ''}
        <p style="color:#6a6058;font-size:11px;margin-top:28px;border-top:1px solid rgba(255,255,255,0.07);padding-top:16px;">Lien direct : <span style="color:#a09890;word-break:break-all;">{cta_url}</span></p>
      </div>
    </div>"""


@router.post("/forgot-password")
def forgot_password(req: EmailOnlyRequest):
    """Envoie un email avec lien de reset password (token 15 min, usage unique).
    Pour la sécurité : retourne TOUJOURS 200 OK (même si email inconnu) — évite l'énumération users."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, email FROM users WHERE LOWER(email) = LOWER(%s)", (req.email,))
        user = cur.fetchone()
    finally:
        cur.close(); conn.close()
    if user:
        raw = _create_token(user["id"], user["email"], "password_reset", ttl_minutes=15)
        url = f"{PUBLIC_BASE_URL}/reset-password.html?token={raw}"
        html = _mail_template(
            "Réinitialisation de votre mot de passe",
            "Vous avez demandé à réinitialiser votre mot de passe AirBizness. Cliquez sur le bouton ci-dessous pour définir un nouveau mot de passe. Lien valable 15 minutes.",
            "Définir un nouveau mot de passe", url,
            "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email."
        )
        _send_token_email(user["email"], "AirBizness — Réinitialisation mot de passe", html)
    return {"ok": True, "msg": "Si cet email existe, un lien de réinitialisation a été envoyé."}


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    """Valide le token reçu par email + applique le nouveau password."""
    payload = _consume_token(req.token, "password_reset")
    new_hash = hash_password(req.new_password)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (new_hash, payload["user_id"])
        )
        conn.commit()
    finally:
        cur.close(); conn.close()
    return {"ok": True, "msg": "Mot de passe mis à jour. Vous pouvez vous connecter."}


@router.post("/magic-link")
def magic_link(req: EmailOnlyRequest):
    """Envoie un lien de connexion sans password (token 15 min).
    Si email inconnu : crée le compte SHELL au moment du callback (lazy)."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, email FROM users WHERE LOWER(email) = LOWER(%s)", (req.email,))
        user = cur.fetchone()
        if user:
            user_id = user["id"]; email = user["email"]
        else:
            # Crée user shell (sans password, auth_provider=email)
            cur.execute(
                """
                INSERT INTO users (email, auth_provider, email_verified, created_at)
                VALUES (%s, 'magic_link', FALSE, NOW())
                RETURNING id, email
                """,
                (req.email,)
            )
            new = cur.fetchone()
            user_id = new["id"]; email = new["email"]
            conn.commit()
    finally:
        cur.close(); conn.close()
    raw = _create_token(user_id, email, "magic_link", ttl_minutes=15)
    url = f"{PUBLIC_BASE_URL}/magic-callback.html?token={raw}"
    html = _mail_template(
        "Votre lien de connexion",
        "Cliquez sur le bouton ci-dessous pour vous connecter à AirBizness. Aucun mot de passe à retenir. Lien valable 15 minutes, usage unique.",
        "Se connecter →", url,
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email."
    )
    _send_token_email(email, "AirBizness — Votre lien de connexion", html)
    return {"ok": True, "msg": "Lien envoyé par email."}


@router.post("/magic-callback")
def magic_callback(payload_in: dict):
    """Échange un token magic-link contre un JWT. Le front appelle ça avec {token}."""
    token = (payload_in or {}).get("token", "")
    if not token:
        raise HTTPException(400, "Token manquant")
    payload = _consume_token(token, "magic_link")
    # Marque email_verified=TRUE puisque l'user prouve qu'il contrôle l'email
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "UPDATE users SET email_verified = TRUE, last_login_at = NOW() WHERE id = %s "
            "RETURNING id, email, full_name, given_name, picture_url",
            (payload["user_id"],)
        )
        user = cur.fetchone()
        conn.commit()
    finally:
        cur.close(); conn.close()
    if not user:
        raise HTTPException(401, "Compte introuvable")
    jwt_token = generate_token(user["id"], user["email"])
    return {"token": jwt_token, "user": dict(user)}


@router.post("/send-verify")
def send_verify(current_user: dict = Depends(get_current_user)):
    """Envoie email de vérification au user connecté (si pas déjà vérifié)."""
    if current_user.get("email_verified"):
        return {"ok": True, "msg": "Email déjà vérifié"}
    raw = _create_token(current_user["id"], current_user["email"], "email_verify", ttl_minutes=60 * 24)  # 24h
    url = f"{PUBLIC_BASE_URL}/api/auth/verify?token={raw}"
    html = _mail_template(
        "Confirmez votre email",
        f"Bienvenue {current_user.get('given_name') or ''} ! Cliquez pour confirmer que cet email est bien à vous. Lien valable 24h.",
        "Confirmer mon email", url,
        ""
    )
    _send_token_email(current_user["email"], "AirBizness — Confirmez votre email", html)
    return {"ok": True, "msg": "Email envoyé"}


@router.get("/verify")
def verify_email_callback(token: str):
    """Endpoint GET (cliquable depuis email) qui valide la vérification email."""
    payload = _consume_token(token, "email_verify")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET email_verified = TRUE WHERE id = %s", (payload["user_id"],))
        conn.commit()
    finally:
        cur.close(); conn.close()
    # Redirige vers /compte avec un flag confirmation
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/compte.html?email_verified=1", status_code=302)
