"""TikTok Content Posting API — OAuth + upload brouillon (module social, 2026-06-19).

Flux validé Pascal : on uploade la vidéo (générée par services/tiktok_reel.py) dans
l'INBOX TikTok du compte AirBizness en BROUILLON (scope video.upload). Pascal ouvre
l'appli TikTok, colle un son tendance, publie. Sanctionné, pas de scraping, pas de ban.

Routes (atteintes via /api/tiktok/* — nginx strippe /api/) :
  GET  /tiktok/auth?admin_token=…        → redirige vers l'autorisation TikTok
  GET  /tiktok/callback?code=&state=     → échange le code, stocke le token
  POST /tiktok/post?admin_token=&slug=…  → génère le reel de l'hôtel + upload brouillon
  GET  /tiktok/status?admin_token=…      → état de connexion

Token stocké hors git dans /var/www/airbizness/.tiktok-token.json.
"""
import os
import json
import time
import secrets
import urllib.request
import urllib.parse

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse

from routers.schema import require_admin_token

router = APIRouter()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = "https://airbizness.com/api/tiktok/callback"
SCOPES = "video.upload"
TOKEN_FILE = "/var/www/airbizness/.tiktok-token.json"
STATE_FILE = "/var/www/airbizness/.tiktok-state"  # state CSRF partagé entre workers (fichier, pas mémoire)


def _set_state(v: str):
    with open(STATE_FILE, "w") as f:
        f.write(v)


def _get_state() -> str:
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _save_token(tok: dict):
    tok["_saved_at"] = int(time.time())
    with open(TOKEN_FILE, "w") as f:
        json.dump(tok, f)


def _load_token() -> dict:
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _valid_token() -> str:
    """Renvoie un access_token valide (rafraîchit si expiré). HTTPException si pas connecté."""
    tok = _load_token()
    if not tok.get("access_token"):
        raise HTTPException(400, "TikTok non connecté — ouvrir /api/tiktok/auth d'abord")
    age = int(time.time()) - tok.get("_saved_at", 0)
    if age >= tok.get("expires_in", 86400) - 120:  # bientôt expiré → refresh
        try:
            new = _post_form("https://open.tiktokapis.com/v2/oauth/token/", {
                "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token", "refresh_token": tok.get("refresh_token", ""),
            })
            if new.get("access_token"):
                _save_token(new); tok = new
        except Exception as e:
            print(f"[tiktok] refresh fail: {e}")
    return tok["access_token"]


@router.get("/tiktok/auth")
async def tiktok_auth(admin_token: str = Query(None), request: Request = None):
    # auth gardée par token admin
    await require_admin_token(admin_token=admin_token, request=request)
    if not CLIENT_KEY:
        raise HTTPException(503, "TIKTOK_CLIENT_KEY absent")
    state = secrets.token_urlsafe(16)
    _set_state(state)
    params = {
        "client_key": CLIENT_KEY, "scope": SCOPES, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "state": state,
    }
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/tiktok/callback")
async def tiktok_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return HTMLResponse(f"<h2>TikTok a refusé : {error}</h2><p>{error_description}</p>", status_code=400)
    if not code or not state or state != _get_state():
        return HTMLResponse("<h2>État invalide ou code manquant</h2>", status_code=400)
    try:
        tok = _post_form("https://open.tiktokapis.com/v2/oauth/token/", {
            "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
            "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI,
        })
    except Exception as e:
        return HTMLResponse(f"<h2>Échange du code échoué</h2><pre>{e}</pre>", status_code=500)
    if not tok.get("access_token"):
        return HTMLResponse(f"<h2>Pas de token</h2><pre>{json.dumps(tok)[:500]}</pre>", status_code=400)
    _save_token(tok)
    return HTMLResponse(
        "<div style='font-family:sans-serif;background:#0a0a14;color:#fff;padding:48px;text-align:center'>"
        "<h1 style='color:#d4ae4a'>✓ TikTok connecté</h1>"
        "<p>AirBizness peut maintenant déposer des vidéos en brouillon sur ton TikTok.</p></div>"
    )


def upload_to_inbox(video_path: str) -> dict:
    """Upload un .mp4 dans l'inbox TikTok (brouillon). Retourne {ok, publish_id|error}."""
    if not os.path.exists(video_path):
        return {"ok": False, "error": "fichier introuvable"}
    token = _valid_token()
    size = os.path.getsize(video_path)
    # 1) init
    init_body = json.dumps({"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1,
    }}).encode()
    req = urllib.request.Request(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        data=init_body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            init = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"init {e.code}: {e.read()[:300].decode(errors='ignore')}"}
    data = init.get("data") or {}
    publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
    if not upload_url:
        return {"ok": False, "error": f"init sans upload_url: {json.dumps(init)[:300]}"}
    # 2) PUT des octets vidéo
    with open(video_path, "rb") as f:
        blob = f.read()
    put = urllib.request.Request(upload_url, data=blob, method="PUT", headers={
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{size-1}/{size}",
    })
    try:
        with urllib.request.urlopen(put, timeout=120) as r:
            _ = r.read()
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"upload {e.code}: {e.read()[:300].decode(errors='ignore')}"}
    return {"ok": True, "publish_id": publish_id, "size": size}


@router.get("/tiktok/status")
async def tiktok_status(admin_token: str = Query(None), request: Request = None):
    await require_admin_token(admin_token=admin_token, request=request)
    tok = _load_token()
    return {"connected": bool(tok.get("access_token")),
            "scope": tok.get("scope"), "open_id": tok.get("open_id"),
            "saved_at": tok.get("_saved_at")}


@router.post("/tiktok/post")
async def tiktok_post(slug: str, admin_token: str = Query(None), request: Request = None):
    await require_admin_token(admin_token=admin_token, request=request)
    from services.hotel_data import get_hotel_unified_data
    from services.tiktok_reel import build_hotel_reel
    h = get_hotel_unified_data(slug)
    if not h:
        raise HTTPException(404, f"hôtel introuvable: {slug}")
    out = f"/tmp/reel_{slug}.mp4"
    built = build_hotel_reel(h, out)
    if not built.get("ok"):
        return JSONResponse({"ok": False, "step": "build", "error": built.get("error")}, status_code=500)
    res = upload_to_inbox(out)
    return JSONResponse({"hotel": h.get("name"), "build": built, "upload": res})
