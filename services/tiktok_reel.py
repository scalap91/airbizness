"""Générateur de vidéo diaporama 9:16 pour TikTok — module ② social (2026-06-19).

Prend les VRAIES photos d'un hôtel (galerie HBX, get_hotel_unified_data) et produit
une vidéo verticale 1080x1920 : photos en fondu enchaîné + léger zoom (Ken Burns) +
titre (nom hôtel / ville). Grounding strict : QUE des photos réelles de l'hôtel, jamais
d'image inventée (doctrine content_grounding). Aucun appel TikTok ici : ce module ne
fait QUE le fichier .mp4. La publication TikTok sera un module séparé (attend les clés).

Sortie : un .mp4 muet par défaut (piste audio silencieuse pour compat). La musique
(libre de droits) est un TODO branché plus tard.
"""
import os
import re
import subprocess
import tempfile
import urllib.request


def _hires(u: str) -> str:
    """Force la plus grande variante Hotelbeds (xxl=2048px) pour éviter la pixelisation.
    bigger=800px → trop petit une fois forcé en 1080x1920. Si motif absent, URL inchangée."""
    if not isinstance(u, str):
        return u
    return re.sub(r"/giata/(small|bigger|xl|original)/", "/giata/xxl/", u)

W, H = 1080, 1920          # format vertical TikTok 9:16
SLIDE_SEC = 3.0            # durée par photo
CARD_SEC = 2.2            # durée carte intro/outro
XFADE_SEC = 0.6           # durée du fondu enchaîné
# Identité AirBizness (mêmes codes que les fiches : sombre + or + serif)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"      # serif (titres)
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"  # sans (labels, étoiles)
BG = "0x0a0a14"           # fond sombre fiche
GOLD = "0xD4AE4A"         # or fiche
# Musique libre de droits embarquée dans AirBizness (autonome). Sert pour la démo et
# pour un éventuel post via API. En post manuel/scraping, on mettra plutôt un son TikTok.
MUSIC_DEFAULT = "/var/www/airbizness/assets/music/ambient-1.mp3"


def _flatten_gallery(h: dict, limit: int = 5) -> list:
    """Liste d'URLs photos réelles depuis h['gallery'] (catégories HBX), ordre varié."""
    gal = h.get("gallery") or {}
    urls, seen = [], set()
    # priorise quelques catégories pour de la variété visuelle
    order = ["general", "rooms", "restaurant", "outdoor", "bar", "other"]
    cats = order + [k for k in gal.keys() if k not in order]
    # round-robin pour alterner les types de photos
    pools = {k: list(gal.get(k) or []) for k in cats}
    while len(urls) < limit and any(pools.values()):
        for k in cats:
            if not pools[k]:
                continue
            u = pools[k].pop(0)
            u = u if isinstance(u, str) else (u.get("url") if isinstance(u, dict) else None)
            if u and u not in seen:
                seen.add(u); urls.append(_hires(u))
                if len(urls) >= limit:
                    break
    return urls


def _download(urls: list, workdir: str) -> list:
    paths = []
    req_headers = {"User-Agent": "Mozilla/5.0 (AirBizness reel builder)"}
    for i, u in enumerate(urls):
        # xxl en priorité ; fallback xxl→bigger si la variante n'existe pas (403).
        candidates = [u]
        if "/giata/xxl/" in u:
            candidates.append(u.replace("/giata/xxl/", "/giata/bigger/"))
        got = False
        for c in candidates:
            try:
                dst = os.path.join(workdir, f"img{i}.jpg")
                req = urllib.request.Request(c, headers=req_headers)
                with urllib.request.urlopen(req, timeout=15) as r, open(dst, "wb") as f:
                    data = r.read()
                    if len(data) < 2000:
                        continue
                    f.write(data)
                paths.append(dst); got = True
                break
            except Exception as e:
                last = e
        if not got:
            print(f"[tiktok_reel] skip image {i}: {last if 'last' in dir() else 'n/a'}")
    return paths


def _esc(t: str) -> str:
    """Échappe le texte pour drawtext ffmpeg."""
    return (t or "").replace("\\", "").replace(":", r"\:").replace("'", "").replace("%", "")


def _card_filter(idx: int, lines: list, out_label: str) -> str:
    """Carte brandée AirBizness : fond sombre (input color idx) + cascade de drawtext.
    lines = [(texte, font, taille, couleur, y_ratio), ...]. y_ratio = position verticale 0..1."""
    pieces = []
    src = f"[{idx}:v]"
    for j, (txt, font, size, color, yr) in enumerate(lines):
        dst = f"[c{idx}_{j}]" if j < len(lines) - 1 else f"[{out_label}]"
        pieces.append(
            f"{src}drawtext=fontfile={font}:text='{_esc(txt)}':fontcolor={color}:"
            f"fontsize={size}:x=(w-tw)/2:y=(h*{yr}){dst}"
        )
        src = dst
    return ";".join(pieces)


def build_hotel_reel(h: dict, out_path: str, max_photos: int = 5, music_path: str = None) -> dict:
    """Construit la vidéo au format fiche AirBizness. Retourne {ok, path, photos, error}.
    music_path : piste audio (def. MUSIC_DEFAULT). None explicite = vidéo muette."""
    music = music_path if music_path is not None else (MUSIC_DEFAULT if os.path.exists(MUSIC_DEFAULT) else None)
    name = h.get("name") or ""
    city = h.get("city") or h.get("city_name") or ""
    city = city.title() if city.isupper() else city
    try:
        stars = int(h.get("stars") or 0)
    except (TypeError, ValueError):
        stars = 0
    urls = _flatten_gallery(h, max_photos)
    if len(urls) < 2:
        return {"ok": False, "error": f"pas assez de photos réelles ({len(urls)})", "photos": len(urls)}

    with tempfile.TemporaryDirectory() as wd:
        imgs = _download(urls, wd)
        if len(imgs) < 2:
            return {"ok": False, "error": f"téléchargement insuffisant ({len(imgs)})", "photos": len(imgs)}

        n = len(imgs)
        name_fs = 76 if len(name) <= 20 else (58 if len(name) <= 30 else 46)

        # Entrées : [0]=carte intro, [1..n]=photos, [n+1]=carte outro, [n+2]=audio silencieux.
        total = CARD_SEC * 2 + n * SLIDE_SEC
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", str(CARD_SEC), "-i", f"color=c={BG}:s={W}x{H}:r=30"]
        for p in imgs:
            cmd += ["-loop", "1", "-t", str(SLIDE_SEC), "-i", p]
        cmd += ["-f", "lavfi", "-t", str(CARD_SEC), "-i", f"color=c={BG}:s={W}x{H}:r=30"]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music]      # boucle si plus courte que la vidéo
        else:
            cmd += ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

        fc = []
        # Carte intro (style fiche : marque + nom serif + ville + étoiles or)
        intro_lines = [
            ("AIRBIZNESS", FONT_SANS, 40, GOLD, 0.30),
            (name, FONT, name_fs, "white", 0.40),
        ]
        if city:
            intro_lines.append((city, FONT, 46, GOLD, 0.50))
        if 1 <= stars <= 5:
            intro_lines.append(("★" * stars, FONT_SANS, 48, GOLD, 0.57))
        fc.append(_card_filter(0, intro_lines, "vintro"))

        # Photos réelles : cover 1080x1920 + léger Ken Burns.
        for i in range(n):
            fc.append(
                f"[{i+1}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps=30,"
                f"zoompan=z='min(zoom+0.0009,1.12)':d={int(SLIDE_SEC*30)}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30[vp{i}]"
            )

        # Carte outro (clôture marque + CTA)
        outro_lines = [
            ("AIRBIZNESS", FONT, 84, GOLD, 0.38),
            ("Votre séjour premium", FONT_SANS, 40, "white", 0.49),
            ("airbizness.com", FONT_SANS, 38, GOLD, 0.55),
        ]
        fc.append(_card_filter(n + 1, outro_lines, "voutro"))

        # Fondus enchaînés : intro → photos → outro.
        order = ["vintro"] + [f"vp{i}" for i in range(n)] + ["voutro"]
        durs = [CARD_SEC] + [SLIDE_SEC] * n + [CARD_SEC]
        last = order[0]
        acc = durs[0]
        for k in range(1, len(order)):
            off = acc - XFADE_SEC
            out = f"x{k}"
            fc.append(f"[{last}][{order[k]}]xfade=transition=fade:duration={XFADE_SEC}:offset={off:.2f}[{out}]")
            last = out
            acc += durs[k] - XFADE_SEC

        dur = total - (n + 1) * XFADE_SEC  # durée réelle après fondus
        if music:
            fc.append(
                f"[{n+2}:a]afade=t=in:st=0:d=1,"
                f"afade=t=out:st={max(dur-1.5, 0):.2f}:d=1.5,volume=0.8[aout]"
            )
            audio_map = "[aout]"
        else:
            audio_map = f"{n+2}:a"
        cmd += [
            "-filter_complex", ";".join(fc),
            "-map", f"[{last}]", "-map", audio_map,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not os.path.exists(out_path):
            return {"ok": False, "error": "ffmpeg: " + r.stderr[-600:], "photos": len(imgs)}
        return {"ok": True, "path": out_path, "photos": len(imgs), "duration_s": round(total - (n + 1) * XFADE_SEC, 1)}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/airbizness")
    from services.hotel_data import get_hotel_unified_data
    slug = sys.argv[1] if len(sys.argv) > 1 else "hotel-le-faubourg"
    h = get_hotel_unified_data(slug)
    if not h:
        print("hôtel introuvable:", slug); sys.exit(1)
    out = f"/tmp/reel_{slug}.mp4"
    print("Build pour:", h.get("name"), "| photos galerie:", sum(len(v or []) for v in (h.get('gallery') or {}).values()))
    print(build_hotel_reel(h, out))
