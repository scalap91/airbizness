"""Générateur de reel TikTok 9:16 « fiche AirBizness en résumé » (module social, 2026-06-19).

Reprend le CONTENU de la fiche hôtel (get_hotel_unified_data) et le condense en vidéo
verticale : carte d'intro brandée → scènes = vraie photo + bande texte or (atouts réels
tirés de la fiche : édito, pourquoi cet hôtel, équipements, lieux proches) → carte CTA.
Grounding strict : QUE des photos + textes réels de la fiche, jamais d'invention.
100 % autonome (ffmpeg local AirBizness, aucune dépendance T2M/GPU).
Ne produit QUE le .mp4 ; la publication TikTok est un module séparé (routers/tiktok.py).
"""
import os
import re
import textwrap
import subprocess
import tempfile
import urllib.request

W, H = 1080, 1920
SLIDE_SEC = 3.2
CARD_SEC = 2.2
XFADE_SEC = 0.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
BG = "0x0a0a14"
GOLD = "0xD4AE4A"
MUSIC_DEFAULT = "/var/www/airbizness/assets/music/ambient-1.mp3"

PREMIUM_FAC = ["Restaurant", "Bar", "Spa", "Piscine", "Salle de sport", "Fitness",
               "Wi-Fi gratuit", "Parking", "Conciergerie", "Climatisation",
               "Room service", "Petit-déjeuner", "Centre d'affaires"]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _first_sentence(s: str, maxlen: int = 88) -> str:
    s = _clean(s)
    parts = re.split(r"(?<=[.!?])\s", s)
    out = parts[0] if parts else s
    if len(out) > maxlen:
        out = out[:maxlen].rsplit(" ", 1)[0] + "…"
    return out.rstrip(".")


def _wrap(s: str, width: int = 24, maxlines: int = 3) -> str:
    lines = textwrap.wrap(_clean(s), width=width)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = lines[-1].rstrip(".") + "…"
    return "\n".join(lines)


def _top_facilities(h: dict, n: int = 4) -> str:
    fc = h.get("facilities_by_category") or {}
    labels = [it.get("label") for items in fc.values() for it in (items or [])
              if isinstance(it, dict) and it.get("label")]
    picked = []
    for p in PREMIUM_FAC:
        for l in labels:
            if p.lower() in l.lower() and l not in picked:
                picked.append(l); break
        if len(picked) >= n:
            break
    for l in labels:
        if len(picked) >= n:
            break
        if l not in picked:
            picked.append(l)
    return " · ".join(picked[:n])


def _location_text(h: dict) -> str:
    pois = [p.get("name") for p in (h.get("pois_nearby") or [])
            if isinstance(p, dict) and p.get("name")][:3]
    line1 = _wrap("À deux pas de " + ", ".join(pois), 24, 3) if pois else ""
    air = [a for a in (h.get("airports") or []) if isinstance(a, dict) and a.get("distance_km")]
    line2 = ""
    if air:
        b = min(air, key=lambda a: a["distance_km"])
        line2 = f"{b.get('name', 'Aéroport')} à {round(b['distance_km'])} km"
    return "\n".join([x for x in (line1, line2) if x])


def _scene_captions(h: dict) -> list:
    """Liste de légendes courtes tirées de la fiche réelle, dans l'ordre du storyboard."""
    caps = []
    intro = h.get("seo_intro_fr") or h.get("description_fr")
    if intro:
        caps.append(_wrap(_first_sentence(intro, 90), 26, 3))
    why = h.get("seo_why_business_fr")
    if why:
        caps.append(_wrap(_first_sentence(why, 90), 26, 3))
    fac = _top_facilities(h)
    if fac:
        caps.append(_wrap(fac, 30, 2))
    loc = _location_text(h)
    if loc:
        caps.append(loc)
    return [c for c in caps if c.strip()]


def _flatten_gallery(h: dict, limit: int) -> list:
    urls, seen = [], set()
    # Photo PRINCIPALE en premier (comme le héros de la fiche).
    main = h.get("photo_main") or h.get("best_photo_url") or h.get("hbx_main_image")
    if main:
        hu = _hires(main); urls.append(hu); seen.add(hu)
    gal = h.get("gallery") or {}
    order = ["general", "rooms", "restaurant", "outdoor", "bar", "other"]
    cats = order + [k for k in gal.keys() if k not in order]
    pools = {k: list(gal.get(k) or []) for k in cats}
    while len(urls) < limit and any(pools.values()):
        for k in cats:
            if not pools[k]:
                continue
            u = pools[k].pop(0)
            u = u if isinstance(u, str) else (u.get("url") if isinstance(u, dict) else None)
            hu = _hires(u) if u else None
            if hu and hu not in seen:
                seen.add(hu); urls.append(hu)
                if len(urls) >= limit:
                    break
    return urls


def _hires(u: str) -> str:
    if not isinstance(u, str):
        return u
    return re.sub(r"/giata/(small|bigger|xl|original)/", "/giata/xxl/", u)


def _download(urls: list, workdir: str) -> list:
    paths = []
    hdr = {"User-Agent": "Mozilla/5.0 (AirBizness reel)"}
    for i, u in enumerate(urls):
        cands = [u] + ([u.replace("/giata/xxl/", "/giata/bigger/")] if "/giata/xxl/" in u else [])
        for c in cands:
            try:
                dst = os.path.join(workdir, f"img{i}.jpg")
                req = urllib.request.Request(c, headers=hdr)
                with urllib.request.urlopen(req, timeout=15) as r, open(dst, "wb") as f:
                    data = r.read()
                    if len(data) < 2000:
                        continue
                    f.write(data)
                paths.append(dst); break
            except Exception:
                continue
    return paths


def _card_filter(idx: int, lines: list, out_label: str) -> str:
    pieces, src = [], f"[{idx}:v]"
    for j, (txt, font, size, color, yr) in enumerate(lines):
        dst = f"[c{idx}_{j}]" if j < len(lines) - 1 else f"[{out_label}]"
        safe = txt.replace("\\", "").replace(":", r"\:").replace("'", "")
        pieces.append(f"{src}drawtext=fontfile={font}:text='{safe}':fontcolor={color}:"
                      f"fontsize={size}:x=(w-tw)/2:y=(h*{yr}){dst}")
        src = dst
    return ";".join(pieces)


def build_hotel_reel(h: dict, out_path: str, max_photos: int = 4, music_path: str = None) -> dict:
    """Reel 'fiche résumée'. Retourne {ok, path, photos, scenes, error}."""
    music = music_path if music_path is not None else (MUSIC_DEFAULT if os.path.exists(MUSIC_DEFAULT) else None)
    name = h.get("name") or ""
    city = h.get("city") or ""
    city = city.title() if city.isupper() else city
    chain = h.get("chain_code") or ""
    try:
        stars = int(h.get("stars") or 0)
    except (TypeError, ValueError):
        stars = 0

    caps = _scene_captions(h)
    if not caps:
        return {"ok": False, "error": "aucun contenu fiche exploitable"}
    nph = min(len(caps), max_photos)
    caps = caps[:nph]
    urls = _flatten_gallery(h, nph)
    if len(urls) < 1:
        return {"ok": False, "error": "pas de photo réelle"}

    with tempfile.TemporaryDirectory() as wd:
        imgs = _download(urls, wd)
        if not imgs:
            return {"ok": False, "error": "téléchargement photos échoué"}
        n = min(len(imgs), len(caps))
        imgs, caps = imgs[:n], caps[:n]
        # écrit chaque légende dans un fichier (drawtext textfile = multi-lignes sans galère d'échappement)
        capfiles = []
        for i, c in enumerate(caps):
            p = os.path.join(wd, f"cap{i}.txt")
            open(p, "w").write(c)
            capfiles.append(p)

        name_fs = 76 if len(name) <= 20 else (58 if len(name) <= 30 else 46)
        total = CARD_SEC * 2 + n * SLIDE_SEC

        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", str(CARD_SEC), "-i", f"color=c={BG}:s={W}x{H}:r=30"]
        for p in imgs:
            cmd += ["-loop", "1", "-t", str(SLIDE_SEC), "-i", p]
        cmd += ["-f", "lavfi", "-t", str(CARD_SEC), "-i", f"color=c={BG}:s={W}x{H}:r=30"]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music]
        else:
            cmd += ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

        fc = []
        # Carte intro
        intro_lines = [("AIRBIZNESS", FONT_SANS, 40, GOLD, 0.30), (name, FONT, name_fs, "white", 0.40)]
        sub = " · ".join([x for x in [(("★" * stars) if 1 <= stars <= 5 else ""), city] if x])
        if sub:
            intro_lines.append((sub, FONT_SANS, 46, GOLD, 0.50))
        if chain:
            intro_lines.append(("by " + chain.title(), FONT_SANS, 32, "0x999999", 0.565))
        fc.append(_card_filter(0, intro_lines, "vintro"))

        # Photos + bande texte or (lower third)
        for i in range(n):
            cf = capfiles[i].replace(":", r"\:")
            fc.append(
                f"[{i+1}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30,"
                f"zoompan=z='min(zoom+0.0008,1.10)':d={int(SLIDE_SEC*30)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30,"
                f"drawtext=fontfile={FONT}:textfile='{cf}':fontcolor=white:fontsize=50:line_spacing=14:"
                f"box=1:boxcolor=0x0a0a14@0.66:boxborderw=34:x=(w-tw)/2:y=h-th-230[vp{i}]"
            )

        # Carte outro
        outro_lines = [("AIRBIZNESS", FONT, 84, GOLD, 0.38),
                       ("Réservez votre séjour", FONT_SANS, 40, "white", 0.49),
                       ("airbizness.com", FONT_SANS, 38, GOLD, 0.55)]
        fc.append(_card_filter(n + 1, outro_lines, "voutro"))

        # Fondus enchaînés
        order = ["vintro"] + [f"vp{i}" for i in range(n)] + ["voutro"]
        durs = [CARD_SEC] + [SLIDE_SEC] * n + [CARD_SEC]
        last, acc = order[0], durs[0]
        for k in range(1, len(order)):
            out = f"x{k}"
            fc.append(f"[{last}][{order[k]}]xfade=transition=fade:duration={XFADE_SEC}:offset={acc-XFADE_SEC:.2f}[{out}]")
            last = out; acc += durs[k] - XFADE_SEC
        dur = total - (n + 1) * XFADE_SEC
        if music:
            fc.append(f"[{n+2}:a]afade=t=in:st=0:d=1,afade=t=out:st={max(dur-1.5,0):.2f}:d=1.5,volume=0.8[aout]")
            amap = "[aout]"
        else:
            amap = f"{n+2}:a"

        cmd += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", amap,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
        if r.returncode != 0 or not os.path.exists(out_path):
            return {"ok": False, "error": "ffmpeg: " + r.stderr[-700:]}
        return {"ok": True, "path": out_path, "photos": n, "scenes": caps, "duration_s": round(dur, 1)}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/var/www/airbizness")
    from services.hotel_data import get_hotel_unified_data
    slug = sys.argv[1] if len(sys.argv) > 1 else "sofitel-paris-le-faubourg"
    h = get_hotel_unified_data(slug)
    print(build_hotel_reel(h, f"/tmp/reel_{slug}.mp4"))
