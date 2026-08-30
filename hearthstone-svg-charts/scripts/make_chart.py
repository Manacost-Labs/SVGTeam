#!/usr/bin/env python3
"""Generate a Hearthstone-style SVG chart from a JSON spec.

Usage:
    python3 scripts/make_chart.py spec.json -o chart.svg

Spec (common fields):
{
  "type":     "bars|line|donut|tierlist|beforeafter|matchup|badge|timeline|digest",
  "title":    "Заголовок",
  "subtitle": "патч 33.4 · источник",          // optional
  "footer":   "текст сноски",                   // optional, auto-added scale note
  "theme":    "arena" | "bg",                   // plaque color, default arena
  "frame":    "vector" | "authentic",           // default vector; badge always deck-border
  "data":     ...                               // per-type, see references/generator.md
}

Icons: any "icon" value resolves to assets/datauri/<match>.txt:
  class slug ("mage"), "tribe-murlocs", "bg-tier3", "rarity-epic", "mana",
  "bg-hero-Alexstrasza", or a local image file path (inlined, webp converted).
"""
import argparse
import base64
import json
import math
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DU = ROOT / "assets" / "datauri"

SANS = "Inter, -apple-system, 'Segoe UI', sans-serif"
SERIF = "HSDisplay, Georgia, serif"
DISPLAY_CHARS: set = set()   # chars rendered with SERIF -> font subset

def serif_text(s):
    DISPLAY_CHARS.update(str(s))
    return esc(s)
INK, MUTED = "#30251c", "#735e49"
CREAM = "#f7e8bf"
SERIES = ["#8d171d", "#8f536d", "#2f7a3e", "#b98a2f", "#735e49"]
POS, NEG = "#2f7a3e", "#a33a3a"
GOLD = "#d9ab49"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# ---------------------------------------------------------------- helpers

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def die(msg):
    sys.exit(f"make_chart: {msg}")

def text_w(s, size, serif=False):
    """Rough text width; cyrillic-friendly overestimate."""
    return len(str(s)) * size * (0.64 if serif else 0.60)

def wrap(s, size, max_w, max_lines=2):
    words, lines, cur = str(s).split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if text_w(cand, size) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur); cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words and " ".join(lines).split() != words:
        lines[-1] = lines[-1][: max(1, int(max_w / (size * 0.6)) - 1)].rstrip() + "…"
    return lines

def icon_tag(name, x, y, size=26, ring=True):
    """Game icon with a subtle gold ring (never tint the icon itself)."""
    tag = f'<image href="{icon_uri(name)}" x="{x:g}" y="{y:g}" width="{size}" height="{size}"/>'
    if ring:
        tag += (f'<rect x="{x-1.2:g}" y="{y-1.2:g}" width="{size+2.4}" height="{size+2.4}" rx="{size*0.24:g}" '
                f'fill="none" stroke="url(#goldEdge)" stroke-width="1" opacity="0.55"/>')
    return tag

def icon_uri(name):
    for cand in (f"class-{name}.webp.txt", f"{name}.webp.txt", f"{name}.png.txt",
                 f"{name}.jpg.txt", f"{name}.txt"):
        p = DU / cand
        if p.exists():
            return p.read_text().strip()
    p = pathlib.Path(name).expanduser()
    if p.exists():
        return inline_image(p)
    die(f"иконка/картинка не найдена: {name} (см. assets/datauri или укажи путь к файлу)")

def inline_image(path, max_kb=100, thumb_w=480):
    """Inline a local image; big ones are downscaled via sips first."""
    if path.suffix.lower() not in MIME:
        die(f"неподдерживаемый формат картинки: {path}")
    src = path
    if path.stat().st_size > max_kb * 1024 or path.suffix.lower() == ".webp":
        tmp = pathlib.Path(tempfile.mkstemp(suffix=".png")[1])
        subprocess.run(["sips", "-s", "format", "png", "--resampleWidth", str(thumb_w),
                        str(path), "--out", str(tmp)], check=True, capture_output=True)
        src = tmp
    data = src.read_bytes()
    return f"data:{MIME[src.suffix.lower()]};base64,{base64.b64encode(data).decode()}"

def lerp_color(c1, c2, t):
    a = [int(c1[i:i+2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i+2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))

def nice_ticks(vmin, vmax, n=4):
    span = vmax - vmin
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n + 1:
            step *= m
            break
    start = math.ceil(vmin / step) * step
    ticks, v = [], start
    while v <= vmax + 1e-9:
        ticks.append(round(v, 6)); v += step
    return ticks

# ---------------------------------------------------------------- chrome

def _cloth_uri():
    p = DU / "arena-rail-red.jpg.txt"
    if p.exists():
        return p.read_text().strip()
    src = ROOT / "assets" / "arena-rail-red.jpg"
    if src.exists():
        uri = "data:image/jpeg;base64," + base64.b64encode(src.read_bytes()).decode()
        p.write_text(uri)
        return uri
    return None

def make_defs():
    cloth = _cloth_uri()
    cloth_pat = (f'<pattern id="cloth" patternUnits="userSpaceOnUse" width="375" height="172">'
                 f'<image href="{cloth}" width="375" height="172"/></pattern>') if cloth else ""
    return f'''<defs>
  <linearGradient id="wood" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#6b3f21"/><stop offset="0.35" stop-color="#5f371d"/>
    <stop offset="0.65" stop-color="#472712"/><stop offset="1" stop-color="#2e160b"/>
  </linearGradient>
  <linearGradient id="parchment" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#f7e8bf"/><stop offset="1" stop-color="#ead6a7"/>
  </linearGradient>
  <linearGradient id="goldEdge" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#efc96f"/><stop offset="0.5" stop-color="#d9ab49"/>
    <stop offset="1" stop-color="#a67c2e"/>
  </linearGradient>
  <linearGradient id="bevelTop" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffe4aa" stop-opacity="0.55"/>
    <stop offset="0.5" stop-color="#ffe4aa" stop-opacity="0.10"/>
    <stop offset="1" stop-color="#000000" stop-opacity="0.28"/>
  </linearGradient>
  <linearGradient id="bevelBot" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#000000" stop-opacity="0.38"/>
    <stop offset="0.55" stop-color="#000000" stop-opacity="0.08"/>
    <stop offset="1" stop-color="#ffdf9e" stop-opacity="0.30"/>
  </linearGradient>
  <radialGradient id="vignette" cx="0.5" cy="0.5" r="0.75">
    <stop offset="0.70" stop-color="#30251c" stop-opacity="0"/>
    <stop offset="0.92" stop-color="#30251c" stop-opacity="0.10"/>
    <stop offset="1" stop-color="#241a12" stop-opacity="0.28"/>
  </radialGradient>
  <filter id="grain" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" result="n"/>
    <feColorMatrix in="n" type="matrix"
      values="0 0 0 0 0.45  0 0 0 0 0.36  0 0 0 0 0.26  0 0 0 0.06 0"/>
    <feComposite operator="in" in2="SourceGraphic"/>
  </filter>
  <filter id="blotch" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.011 0.017" numOctaves="3" seed="7" result="n"/>
    <feColorMatrix in="n" type="matrix"
      values="0 0 0 0 0.42  0 0 0 0 0.31  0 0 0 0 0.18  0 0 0 0.10 0"/>
    <feComposite operator="in" in2="SourceGraphic"/>
  </filter>
  <filter id="soft" x="-8%" y="-8%" width="116%" height="116%">
    <feGaussianBlur stdDeviation="3.2"/>
  </filter>
  <path id="cornerCap" d="M 3 30 L 3 13 Q 3 3 13 3 L 30 3 L 30 11.5 L 15 11.5
    Q 11.5 11.5 11.5 15 L 11.5 30 Z"/>
  {cloth_pat}
</defs>'''

def png_size(path):
    d = path.read_bytes()
    return struct.unpack(">II", d[16:24])

def nine_slice(asset, x, y, w, h, c, uid=""):
    uri = (DU / (asset + ".txt")).read_text().strip()
    iw, ih = png_size(ROOT / "assets" / asset)
    img_id = "f9-" + asset.split(".")[0] + uid
    regions = [
        (x, y, c, c, 0, 0, c, c), (x + c, y, w - 2*c, c, c, 0, iw - 2*c, c),
        (x + w - c, y, c, c, iw - c, 0, c, c),
        (x, y + c, c, h - 2*c, 0, c, c, ih - 2*c),
        (x + w - c, y + c, c, h - 2*c, iw - c, c, c, ih - 2*c),
        (x, y + h - c, c, c, 0, ih - c, c, c),
        (x + c, y + h - c, w - 2*c, c, c, ih - c, iw - 2*c, c),
        (x + w - c, y + h - c, c, c, iw - c, ih - c, c, c),
    ]
    out = [f'<g><defs><image id="{img_id}" href="{uri}" width="{iw}" height="{ih}"/></defs>']
    for dx, dy, dw, dh, sx, sy, sw, sh in regions:
        out.append(f'<svg x="{dx:g}" y="{dy:g}" width="{dw:g}" height="{dh:g}" '
                   f'viewBox="{sx:g} {sy:g} {sw:g} {sh:g}" preserveAspectRatio="none" '
                   f'overflow="hidden"><use href="#{img_id}"/></svg>')
    out.append("</g>")
    return "".join(out)

def frame(H, mode="vector", finish="parade"):
    shadow = (f'<rect x="12" y="17" width="776" height="{H-25}" rx="16" '
              f'fill="#1a120b" opacity="0.30" filter="url(#soft)"/>')
    blotch = (f'<rect x="14" y="14" width="772" height="{H-28}" rx="10" filter="url(#blotch)" fill="#fff"/>'
              if finish == "parade" else "")
    if mode == "authentic":
        return (f'{make_defs()}\n{shadow}'
                f'<rect x="18" y="18" width="764" height="{H-36}" rx="6" fill="url(#parchment)"/>'
                + (f'<rect x="18" y="18" width="764" height="{H-36}" rx="6" filter="url(#blotch)" fill="#fff"/>' if finish == "parade" else "")
                + f'<rect x="18" y="18" width="764" height="{H-36}" rx="6" fill="url(#vignette)"/>'
                + nine_slice("main-page-rail-border.png", 6, 6, 788, H - 12, 45))
    corners = "".join(
        f'<use href="#cornerCap" transform="{t}" fill="url(#goldEdge)" stroke="#5d3f12" stroke-width="0.9"/>'
        for t in ("translate(0 0)", f"translate(800 0) scale(-1 1)",
                  f"translate(0 {H}) scale(1 -1)", f"translate(800 {H}) scale(-1 -1)"))
    return f'''{make_defs()}
{shadow}
<rect x="14" y="14" width="772" height="{H-28}" rx="10" fill="url(#parchment)"/>
{blotch}
<rect x="14" y="14" width="772" height="{H-28}" rx="10" fill="url(#vignette)"/>
<rect x="14" y="14" width="772" height="{H-28}" rx="10" filter="url(#grain)" fill="#fff"/>
<rect x="9" y="9" width="782" height="{H-18}" rx="14" fill="none" stroke="#1c0d06" stroke-width="2"/>
<rect x="10" y="10" width="780" height="{H-20}" rx="13" fill="none" stroke="url(#wood)" stroke-width="12"/>
<rect x="4.75" y="4.75" width="790.5" height="{H-9.5}" rx="16" fill="none" stroke="url(#bevelTop)" stroke-width="1.5"/>
<rect x="15.4" y="15.4" width="769.2" height="{H-30.8}" rx="9" fill="none" stroke="url(#bevelBot)" stroke-width="1.4"/>
<rect x="17.5" y="17.5" width="765" height="{H-35}" rx="8" fill="none" stroke="url(#goldEdge)" stroke-width="1.6"/>
{corners}'''

def title_block(spec):
    if not spec.get("title"):
        return "", 96
    t = serif_text(spec.get("title", ""))
    sub = esc(spec.get("subtitle", ""))
    arena = spec.get("theme", "arena") == "arena"
    edge, tail, outline = (("#5d0d13", "#4a0a0f", "#3a080c") if arena
                           else ("#2a1725", "#221022", "#1a0c18"))
    tint, tint_op = ("#8d171d", 0.30) if arena else ("#3d2335", 0.62)
    size = 26 if text_w(t, 26, serif=True) <= 660 else 21
    cloth = _cloth_uri()
    plaque_fill = 'url(#cloth)' if cloth else tint
    return f'''<g>
  <polygon points="46,38 24,38 34,56 24,74 46,74" fill="{tail}" stroke="#1c0d06" stroke-width="1"/>
  <polygon points="754,38 776,38 766,56 776,74 754,74" fill="{tail}" stroke="#1c0d06" stroke-width="1"/>
  <rect x="40" y="32" width="720" height="46" rx="6" fill="{plaque_fill}"/>
  <rect x="40" y="32" width="720" height="46" rx="6" fill="{tint}" opacity="{tint_op}"/>
  <rect x="40" y="32" width="720" height="46" rx="6" fill="url(#bevelTop)" opacity="0.55"/>
  <rect x="40" y="32" width="720" height="46" rx="6" fill="none" stroke="{edge}" stroke-width="1.6"/>
  <rect x="42" y="34" width="716" height="42" rx="5" fill="none" stroke="url(#goldEdge)" stroke-width="0.8" opacity="0.45"/>
  <text x="400" y="{64 if size == 26 else 62}" text-anchor="middle" font-family="{SERIF}"
        font-size="{size}" font-weight="700" fill="#f9ead0" letter-spacing="0.5"
        stroke="{outline}" stroke-width="3" paint-order="stroke" stroke-linejoin="round">{t}</text>
</g>''' + (f'\n<text x="400" y="101" text-anchor="middle" font-family="Georgia, serif" '
           f'font-style="italic" font-size="14" fill="{MUTED}">{sub}</text>' if sub else ""), (112 if sub else 96)

def footer(spec, H, extra=""):
    parts = [p for p in (extra, spec.get("footer", "")) if p]
    if not parts:
        return ""
    return (f'<text x="40" y="{H-34}" font-family="{SANS}" font-size="12" '
            f'fill="{MUTED}">{esc(" · ".join(parts))}</text>')

def doc(W, H, label, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img"\n'
            f'     aria-label="{esc(label)}">\n{body}\n</svg>\n')

# ---------------------------------------------------------------- renderers

def r_bars(spec):
    rows = spec["data"]
    vals = [r["value"] for r in rows]
    vmin = spec.get("vmin", math.floor(min(vals) - 2))
    vmax = spec.get("vmax", math.ceil(max(vals) + 1))
    unit = spec.get("unit", "%")
    H = 118 + len(rows) * 38 + 76
    X0, TRACK = 230, 480
    y0 = 118
    body = []
    show_avg = spec.get("average", True) and len(rows) > 2
    if show_avg:
        avg = sum(vals) / len(vals)
        ax = X0 + TRACK * (avg - vmin) / (vmax - vmin)
        body.append(f'<line x1="{ax:.1f}" y1="{y0-6}" x2="{ax:.1f}" y2="{y0+len(rows)*38-8}" '
                    f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="4 3"/>'
                    f'<text x="{ax:.1f}" y="{y0+len(rows)*38+8}" text-anchor="middle" '
                    f'font-family="{SANS}" font-size="12" fill="{MUTED}">среднее {avg:.1f}{unit}</text>')
    for i, r in enumerate(sorted(rows, key=lambda r: -r["value"])):
        y = y0 + i * 38
        w = TRACK * (r["value"] - vmin) / (vmax - vmin)
        leader = r.get("leader", i == 0 and spec.get("highlight_leader", True))
        stroke = f'stroke="{GOLD}" stroke-width="1.5"' if leader else 'stroke="#5d0d13" stroke-width="1"'
        color = r.get("color", "#8d171d" if spec.get("theme", "arena") == "arena" else "#8f536d")
        label = esc(r["label"])
        icon = icon_tag(r["icon"], 46, y) if r.get("icon") else ""
        star = f'<text x="78" y="{y+18}" font-size="12" fill="{GOLD}">★</text>' if leader else ""
        tx = 92 if leader else 82
        # bar with inner bevel: light crest on top, shade below
        bar = f'''<rect x="{X0}" y="{y+1}" width="{TRACK}" height="24" rx="3" fill="{INK}" opacity="0.08"/>
  <rect x="{X0}" y="{y+1}" width="{w:.1f}" height="24" rx="3" fill="{color}" {stroke}/>
  <rect x="{X0+1}" y="{y+2}" width="{max(w-2,0):.1f}" height="8" rx="2" fill="#ffffff" opacity="0.20"/>
  <rect x="{X0+1}" y="{y+17}" width="{max(w-2,0):.1f}" height="7" rx="2" fill="#000000" opacity="0.14"/>'''
        val = f'{r["value"]}{unit}'
        if leader:
            bar += (f'\n  <circle cx="{X0+w:.1f}" cy="{y+13}" r="6.5" fill="url(#goldEdge)" '
                    f'stroke="#5d3f12" stroke-width="1"/>')
            mw = text_w(val, 14) + 18
            mx = X0 + w + 12
            if mx + mw > 754:
                mx = X0 + w - mw - 14
            bar += (f'\n  <rect x="{mx:.1f}" y="{y+2}" width="{mw:.1f}" height="22" rx="11" '
                    f'fill="#5d0d13" stroke="url(#goldEdge)" stroke-width="1.2"/>'
                    f'\n  <text x="{mx+mw/2:.1f}" y="{y+18}" text-anchor="middle" font-size="14" '
                    f'font-weight="700" fill="{CREAM}">{val}</text>')
        else:
            vx = X0 + w + 8
            vattr = f'x="{vx:.1f}" fill="{INK}"'
            if w > TRACK - 64:
                vattr = f'x="{X0+w-8:.1f}" text-anchor="end" fill="{CREAM}"'
            bar += f'\n  <text {vattr} y="{y+18}" font-size="14" font-weight="600">{val}</text>'
        body.append(f'''<g font-family="{SANS}">{icon}
  <text x="{tx}" y="{y+18}" font-size="14" fill="{INK}">{label}</text>{star}
  {bar}
</g>''')
    return 800, H, "\n".join(body), f"Шкала от {vmin}{unit}"

def r_line(spec):
    d = spec["data"]
    xlabels, series = d["xlabels"], d["series"]
    allv = [v for s in series for v in s["values"]]
    vmin = spec.get("vmin", min(allv) - (max(allv) - min(allv)) * 0.15)
    vmax = spec.get("vmax", max(allv) + (max(allv) - min(allv)) * 0.2)
    unit = spec.get("unit", "%")
    H = 450
    PX0, PX1, PY0, PY1 = 100, 740, H - 80, 150
    def xy(i, v):
        x = PX0 + (PX1 - PX0) * (i / max(len(xlabels) - 1, 1))
        y = PY0 + (PY1 - PY0) * (v - vmin) / (vmax - vmin)
        return x, y
    body = []
    for gv in nice_ticks(vmin, vmax):
        _, gy = xy(0, gv)
        body.append(f'<line x1="{PX0}" y1="{gy:.1f}" x2="{PX1}" y2="{gy:.1f}" stroke="{MUTED}" stroke-width="1" opacity="0.3"/>'
                    f'<text x="{PX0-10}" y="{gy+4:.1f}" text-anchor="end" font-family="{SANS}" font-size="13" fill="{MUTED}">{gv:g}{unit}</text>')
    step = max(1, math.ceil(len(xlabels) / 7))
    for i, lab in enumerate(xlabels):
        if i % step and i != len(xlabels) - 1:
            continue
        x, _ = xy(i, vmin)
        body.append(f'<text x="{x:.1f}" y="{PY0+24}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="{INK}">{esc(lab)}</text>')
    if len(series) > 1:
        lx = 100
        for si, s in enumerate(series):
            col = s.get("color", SERIES[si % len(SERIES)])
            name = esc(s["name"])
            body.append(f'<line x1="{lx}" y1="124" x2="{lx+24}" y2="124" stroke="{col}" stroke-width="3" stroke-linecap="round"/>'
                        f'<text x="{lx+32}" y="128" font-family="{SANS}" font-size="13" fill="{INK}">{name}</text>')
            lx += 32 + text_w(name, 13) + 28
    for si, s in enumerate(series):
        col = s.get("color", SERIES[si % len(SERIES)])
        pts = [xy(i, v) for i, v in enumerate(s["values"])]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if len(series) == 1:
            body.append(f'<defs><linearGradient id="la{si}" x1="0" y1="0" x2="0" y2="1">'
                        f'<stop offset="0" stop-color="{col}" stop-opacity="0.22"/>'
                        f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient></defs>'
                        f'<polygon points="{pts[0][0]:.1f},{PY0} {poly} {pts[-1][0]:.1f},{PY0}" fill="url(#la{si})"/>')
        body.append(f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
        for i, (x, y) in enumerate(pts):
            last = i == len(pts) - 1
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{col}" stroke="{GOLD if last else CREAM}" stroke-width="{2 if last else 1.5}"/>')
            if last:
                body.append(f'<text x="{x:.1f}" y="{y-14:.1f}" text-anchor="middle" font-family="{SANS}" font-size="15" font-weight="700" fill="{col}">{s["values"][-1]:g}{unit}</text>')
    return 800, H, "\n".join(body), f"Шкала от {vmin:g}{unit}"

def r_donut(spec):
    rows = spec["data"]
    total = sum(r["value"] for r in rows)
    if abs(total - 100) > 0.5:
        die(f"donut: сумма долей {total}, должна быть 100")
    H = 450
    CX, CY, R, RIN = 300, 265, 130, 78
    def pt(r, a): return CX + r * math.cos(a), CY + r * math.sin(a)
    body, a0 = [], -math.pi / 2
    for i, r in enumerate(rows):
        col = r.get("color", SERIES[i % len(SERIES)])
        a1 = a0 + 2 * math.pi * r["value"] / total
        large = 1 if (a1 - a0) > math.pi else 0
        x0o, y0o = pt(R, a0); x1o, y1o = pt(R, a1)
        x1i, y1i = pt(RIN, a1); x0i, y0i = pt(RIN, a0)
        body.append(f'<path d="M {x0o:.1f} {y0o:.1f} A {R} {R} 0 {large} 1 {x1o:.1f} {y1o:.1f} '
                    f'L {x1i:.1f} {y1i:.1f} A {RIN} {RIN} 0 {large} 0 {x0i:.1f} {y0i:.1f} Z" '
                    f'fill="{col}" stroke="#ead6a7" stroke-width="3"/>')
        mid = (a0 + a1) / 2
        lx, ly = pt(R + 18, mid)
        anchor = "start" if math.cos(mid) >= 0 else "end"
        lab = esc(r["label"])
        if anchor == "end" and text_w(f"{lab} — {r['value']:g}%", 13) * 0.9 > lx - 30:
            lab = lab[:12] + "…"
        body.append(f'<text x="{lx:.1f}" y="{ly+4:.1f}" text-anchor="{anchor}" font-family="{SANS}" font-size="13" fill="{INK}">{lab} — {r["value"]:g}%</text>')
        a0 = a1
    # carved-ring shading so the donut reads as an inset, not a flat disc
    body.append(f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" stroke="#30251c" stroke-width="2.5" opacity="0.15"/>'
                f'<circle cx="{CX}" cy="{CY}" r="{RIN}" fill="none" stroke="#30251c" stroke-width="2.5" opacity="0.20"/>'
                f'<circle cx="{CX}" cy="{CY}" r="{RIN-2.5}" fill="none" stroke="#fff6dd" stroke-width="1.5" opacity="0.5"/>')
    c = spec.get("center", {})
    if c:
        body.append(f'<text x="{CX}" y="{CY-2}" text-anchor="middle" font-family="{SERIF}" font-size="30" font-weight="700" fill="{INK}">{serif_text(c.get("big",""))}</text>'
                    f'<text x="{CX}" y="{CY+22}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="{MUTED}">{esc(c.get("small",""))}</text>')
    return 800, H, "\n".join(body), "Сегменты по убыванию с 12 часов"

def r_tierlist(spec):
    tiers = spec["data"]
    plq = {"S": (GOLD, INK), "A": ("#8d171d", CREAM), "B": (MUTED, CREAM),
           "C": ("#5f371d", CREAM), "D": ("#443021", CREAM)}
    heads = spec.get("colheaders", [])
    y, ROW, GAP = 152, 34, 16
    body = []
    for ci, h in enumerate(heads):
        body.append(f'<text x="{600 + ci*130}" y="140" text-anchor="end" font-family="{SANS}" font-size="13" font-weight="600" fill="{MUTED}">{esc(h)}</text>')
    for ti, tier in enumerate(tiers):
        letter = tier["tier"]
        fill, tcol = plq.get(letter, (MUTED, CREAM))
        gh = len(tier["rows"]) * ROW
        body.append(f'<rect x="46" y="{y}" width="40" height="{gh-6}" rx="6" fill="{fill}" stroke="#2e160b" stroke-width="1"/>'
                    f'<rect x="46" y="{y}" width="40" height="{gh-6}" rx="6" fill="url(#bevelTop)" opacity="0.5"/>'
                    f'<text x="66" y="{y+(gh-6)/2+8}" text-anchor="middle" font-family="{SERIF}" font-size="22" font-weight="700" fill="{tcol}">{serif_text(letter)}</text>')
        for i, r in enumerate(tier["rows"]):
            ry = y + i * ROW
            icon = icon_tag(r["icon"], 100, ry, 24) if r.get("icon") else ""
            cols = "".join(
                f'<text x="{600 + ci*130}" y="{ry+17}" text-anchor="end" font-weight="{600 if ci == 0 else 400}" fill="{INK if ci == 0 else MUTED}">{esc(v)}</text>'
                for ci, v in enumerate(r.get("cols", [])))
            body.append(f'<g font-family="{SANS}" font-size="14" fill="{INK}">{icon}'
                        f'<text x="134" y="{ry+17}">{esc(r["label"])}</text>{cols}</g>')
        y += gh + GAP
        if ti != len(tiers) - 1:
            dy = y - 10
            body.append(f'<rect x="46" y="{dy}" width="708" height="2" fill="#5f371d" opacity="0.6"/>'
                        f'<rect x="{400-4.5}" y="{dy-3.5}" width="9" height="9" rx="1.5" fill="url(#goldEdge)" '
                        f'stroke="#5d3f12" stroke-width="0.8" transform="rotate(45 400 {dy+1})"/>')
    return 800, y + 44, "\n".join(body), ""

def r_beforeafter(spec):
    rows = spec["data"]
    allv = [v for r in rows for v in (r["before"], r["after"])]
    vmin = spec.get("vmin", math.floor(min(allv) - 2))
    vmax = spec.get("vmax", math.ceil(max(allv) + 1))
    unit = spec.get("unit", "%")
    labels = spec.get("labels", ["до патча", "после патча"])
    X0, TRACK = 230, 400
    def bw(v): return TRACK * (v - vmin) / (vmax - vmin)
    y = 152
    body = [f'''<g font-family="{SANS}" font-size="13">
  <rect x="270" y="118" width="22" height="12" rx="3" fill="{MUTED}" opacity="0.55"/>
  <text x="298" y="128" fill="{INK}">{esc(labels[0])}</text>
  <rect x="410" y="118" width="22" height="12" rx="3" fill="#8d171d"/>
  <text x="438" y="128" fill="{INK}">{esc(labels[1])}</text></g>''']
    for r in rows:
        d = r["after"] - r["before"]
        dcol, arrow, sign = (POS, "▲", "+") if d >= 0 else (NEG, "▼", "−")
        body.append(f'''<g font-family="{SANS}">
  <text x="46" y="{y+22}" font-size="14" fill="{INK}">{esc(r["label"])}</text>
  <rect x="{X0}" y="{y}" width="{TRACK}" height="14" rx="3" fill="{INK}" opacity="0.06"/>
  <rect x="{X0}" y="{y}" width="{bw(r["before"]):.1f}" height="14" rx="3" fill="{MUTED}" opacity="0.55"/>
  <text x="{X0+bw(r["before"])+8:.1f}" y="{y+11}" font-size="13" fill="{MUTED}">{r["before"]}{unit}</text>
  <rect x="{X0}" y="{y+18}" width="{TRACK}" height="14" rx="3" fill="{INK}" opacity="0.06"/>
  <rect x="{X0}" y="{y+18}" width="{bw(r["after"]):.1f}" height="14" rx="3" fill="#8d171d" stroke="#5d0d13" stroke-width="1"/>
  <text x="{X0+bw(r["after"])+8:.1f}" y="{y+30}" font-size="13" font-weight="600" fill="{INK}">{r["after"]}{unit}</text>
  <text x="754" y="{y+22}" text-anchor="end" font-size="15" font-weight="700" fill="{dcol}">{sign}{abs(d):.1f}{unit} {arrow}</text>
</g>''')
        y += 56
    return 800, y + 60, "\n".join(body), f"Шкала от {vmin}{unit}"

def r_matchup(spec):
    d = spec["data"]
    rows, cols, vals = d["rows"], d["cols"], d["values"]
    n, m = len(rows), len(cols)
    if len(vals) != n or any(len(v) != m for v in vals):
        die("matchup: values должен быть матрицей rows×cols")
    LX, TY = 200, 158            # grid origin
    CW = min(80, (754 - LX) // m)
    CH = 38
    H = TY + n * CH + 84
    lo, hi = spec.get("vmin", 40), spec.get("vmax", 60)
    neutral = "#e6d2a1"
    def cell_color(v):
        if v is None: return "none"
        t = max(-1, min(1, (v - 50) / ((hi - lo) / 2)))
        return lerp_color(neutral, POS, t) if t >= 0 else lerp_color(neutral, NEG, -t)
    body = [f'<text x="{LX-8}" y="{TY-30}" text-anchor="end" font-family="{SANS}" font-size="12" fill="{MUTED}">строка против столбца →</text>']
    for j, c in enumerate(cols):
        x = LX + j * CW + CW / 2
        body.append(icon_tag(c["icon"], x - 12, TY - 28, 24)
                    if c.get("icon") else
                    f'<text x="{x:.1f}" y="{TY-12}" text-anchor="middle" font-family="{SANS}" font-size="12" fill="{INK}">{esc(c["label"])[:4]}</text>')
    for i, r in enumerate(rows):
        yv = TY + i * CH
        icon = icon_tag(r["icon"], 46, yv + 7, 24) if r.get("icon") else ""
        body.append(f'{icon}<text x="78" y="{yv+24}" font-family="{SANS}" font-size="13" fill="{INK}">{esc(r["label"])}</text>')
        for j in range(m):
            v = vals[i][j]
            x = LX + j * CW
            if v is None:
                body.append(f'<rect x="{x+2}" y="{yv+2}" width="{CW-4}" height="{CH-4}" rx="3" fill="{INK}" opacity="0.06"/>'
                            f'<text x="{x+CW/2:.1f}" y="{yv+24}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="{MUTED}">—</text>')
            else:
                tcol = CREAM if abs(v - 50) > (hi - lo) * 0.30 else INK
                body.append(f'<rect x="{x+2}" y="{yv+2}" width="{CW-4}" height="{CH-4}" rx="3" fill="{cell_color(v)}"/>'
                            f'<text x="{x+CW/2:.1f}" y="{yv+24}" text-anchor="middle" font-family="{SANS}" font-size="13" font-weight="600" fill="{tcol}">{v:g}</text>')
    # legend gradient
    gy = TY + n * CH + 22
    body.append(f'<defs><linearGradient id="mgrad" x1="0" y1="0" x2="1" y2="0">'
                f'<stop offset="0" stop-color="{NEG}"/><stop offset="0.5" stop-color="{neutral}"/>'
                f'<stop offset="1" stop-color="{POS}"/></linearGradient></defs>'
                f'<rect x="{LX}" y="{gy}" width="180" height="10" rx="3" fill="url(#mgrad)"/>'
                f'<text x="{LX-8}" y="{gy+10}" text-anchor="end" font-family="{SANS}" font-size="12" fill="{MUTED}">{lo}%</text>'
                f'<text x="{LX+188}" y="{gy+10}" font-family="{SANS}" font-size="12" fill="{MUTED}">{hi}%</text>')
    return 800, H, "\n".join(body), "Числа — винрейт строки против столбца"

def r_badge(spec):
    d = spec["data"]
    W, H = 320, 132
    body = [nine_slice("deck-border.png", 4, 4, W - 8, H - 8, 16)]
    icon = d.get("icon")
    tx = W / 2
    if icon:
        body.append(f'<image href="{icon_uri(icon)}" x="34" y="{H/2-24}" width="48" height="48"/>')
        tx = (W + 82) / 2
    body.append(f'<text x="{tx:.0f}" y="{H/2+2:.0f}" text-anchor="middle" font-family="{SERIF}" font-size="38" font-weight="700" fill="{INK}">{serif_text(d["value"])}</text>')
    if d.get("label"):
        body.append(f'<text x="{tx:.0f}" y="{H/2+30:.0f}" text-anchor="middle" font-family="{SANS}" font-size="14" fill="{MUTED}">{esc(d["label"])}</text>')
    # parchment under the transparent center + soft object shadow
    body.insert(0, f'{make_defs()}'
                   f'<rect x="8" y="13" width="{W-16}" height="{H-19}" rx="10" fill="#1a120b" opacity="0.28" filter="url(#soft)"/>'
                   f'<rect x="14" y="14" width="{W-28}" height="{H-28}" rx="6" fill="url(#parchment)"/>'
                   f'<rect x="14" y="14" width="{W-28}" height="{H-28}" rx="6" fill="url(#vignette)"/>')
    return W, H, "\n".join(body), None   # no footer, no title chrome

def r_timeline(spec):
    events = spec["data"]
    n = len(events)
    if n < 2: die("timeline: нужно минимум 2 события")
    H = 340
    LX, RX, CY_ = 80, 720, 210
    scol = {"done": POS, "now": GOLD, "major": "#8d171d", "planned": MUTED}
    body = [f'<line x1="{LX-14}" y1="{CY_}" x2="{RX+20}" y2="{CY_}" stroke="url(#wood)" stroke-width="4" stroke-linecap="round"/>'
            f'<path d="M {RX+34} {CY_} l -14 -7 v 14 Z" fill="#5f371d"/>']
    for i, e in enumerate(events):
        x = LX + (RX - LX) * i / (n - 1)
        st = e.get("status", "planned")
        col = scol.get(st, MUTED)
        above = i % 2 == 0
        if st == "now":
            body.append(f'<rect x="{x-8:.1f}" y="{CY_-8}" width="16" height="16" rx="3" fill="url(#goldEdge)" stroke="#5d3f12" stroke-width="1" transform="rotate(45 {x:.1f} {CY_})"/>')
        elif st == "planned":
            body.append(f'<circle cx="{x:.1f}" cy="{CY_}" r="7" fill="url(#parchment)" stroke="{col}" stroke-width="2.5"/>')
        else:
            body.append(f'<circle cx="{x:.1f}" cy="{CY_}" r="7.5" fill="{col}" stroke="{CREAM}" stroke-width="2"/>')
        ly = CY_ - 26 if above else CY_ + 40
        stem_y1, stem_y2 = (CY_ - 12, ly + 6) if above else (CY_ + 12, ly - 16)
        body.append(f'<line x1="{x:.1f}" y1="{stem_y1}" x2="{x:.1f}" y2="{stem_y2}" stroke="{MUTED}" stroke-width="1" opacity="0.6"/>')
        date = esc(e.get("date", ""))
        lines = wrap(e["label"], 13, 120, 2)
        block = [f'<text x="{x:.1f}" y="{ly}" text-anchor="middle" font-family="{SANS}" font-size="13" font-weight="700" fill="{col if st != "planned" else INK}">{date}</text>']
        for li, ln in enumerate(lines):
            block.append(f'<text x="{x:.1f}" y="{ly + 17 + li*16 if not above else ly - 34 + (li - len(lines) + 1)*16 - 2}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="{INK}">{esc(ln)}</text>')
        if above:
            # date closest to axis, label lines above it
            block = [f'<text x="{x:.1f}" y="{ly}" text-anchor="middle" font-family="{SANS}" font-size="13" font-weight="700" fill="{col if st != "planned" else INK}">{date}</text>']
            for li, ln in enumerate(reversed(lines)):
                block.append(f'<text x="{x:.1f}" y="{ly - 18 - li*16}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="{INK}">{esc(ln)}</text>')
        body.extend(block)
    if spec.get("legend", True):
        items = [("done", "вышло"), ("now", "сейчас"), ("planned", "план")]
        lx = 270
        for st, name in items:
            body.append(f'<circle cx="{lx}" cy="{H-52}" r="6" fill="{scol[st] if st != "planned" else "url(#parchment)"}" stroke="{scol[st]}" stroke-width="2"/>'
                        f'<text x="{lx+13}" y="{H-47}" font-family="{SANS}" font-size="13" fill="{INK}">{name}</text>')
            lx += 13 + text_w(name, 13) + 34
    return 800, H, "\n".join(body), ""

def r_digest(spec):
    items = spec["data"]
    IH, PAD = 104, 118
    H = 128 + len(items) * PAD + 44
    body = []
    y = 128
    for i, it in enumerate(items):
        # thumb 168x94 in deck-border mini frame
        if it.get("image"):
            uri = icon_uri(it["image"])
            body.append(f'<defs><clipPath id="dg{i}"><rect x="52" y="{y+6}" width="156" height="{IH-22}" rx="4"/></clipPath></defs>'
                        f'<image href="{uri}" x="52" y="{y+6}" width="156" height="{IH-22}" preserveAspectRatio="xMidYMid slice" clip-path="url(#dg{i})"/>')
        else:
            body.append(f'<rect x="52" y="{y+6}" width="156" height="{IH-22}" rx="4" fill="{INK}" opacity="0.08"/>'
                        f'<image href="{icon_uri("mana")}" x="112" y="{y+24}" width="36" height="46" opacity="0.7"/>')
        body.append(nine_slice("deck-border.png", 44, y - 2, 172, IH - 6, 14, uid=f"-{i}"))
        cat = it.get("category", "")
        catw = text_w(cat, 12) + 20
        if cat:
            ccol = "#8d171d" if it.get("theme", spec.get("theme", "arena")) == "arena" else "#3d2335"
            body.append(f'<rect x="238" y="{y+2}" width="{catw:.0f}" height="20" rx="10" fill="{ccol}"/>'
                        f'<text x="{238+catw/2:.0f}" y="{y+16}" text-anchor="middle" font-family="{SANS}" font-size="12" fill="{CREAM}">{esc(cat)}</text>')
        if it.get("date"):
            body.append(f'<text x="754" y="{y+17}" text-anchor="end" font-family="{SANS}" font-size="13" fill="{MUTED}">{esc(it["date"])}</text>')
        for li, ln in enumerate(wrap(it["title"], 17, 500, 2)):
            body.append(f'<text x="238" y="{y+48+li*24}" font-family="{SANS}" font-size="17" font-weight="600" fill="{INK}">{esc(ln)}</text>')
        if i != len(items) - 1:
            body.append(f'<rect x="44" y="{y+PAD-11}" width="712" height="1.5" fill="#5f371d" opacity="0.35"/>')
        y += PAD
    return 800, H, "\n".join(body), ""

RENDERERS = {"bars": r_bars, "line": r_line, "donut": r_donut, "tierlist": r_tierlist,
             "beforeafter": r_beforeafter, "matchup": r_matchup, "badge": r_badge,
             "timeline": r_timeline, "digest": r_digest}

# ---------------------------------------------------------------- main

def font_face_style():
    """Subset HSDisplay (Belwe cyr) to the chars actually used and embed it.

    Works even when the SVG is placed via <img> — data-URI needs no network.
    Silently skipped if fontTools/brotli or the font file are missing.
    """
    if not DISPLAY_CHARS:
        return ""
    fpath = ROOT / "assets" / "fonts" / "HSDisplay.otf"
    if not fpath.exists():
        return ""
    try:
        import io
        from fontTools import subset
    except ImportError:
        return ""
    opts = subset.Options(flavor="woff2")
    font = subset.load_font(str(fpath), opts)
    sub = subset.Subsetter(opts)
    sub.populate(text="".join(DISPLAY_CHARS) + " 0123456789%.,-—")
    sub.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return (f'<style>@font-face{{font-family:"HSDisplay";'
            f'src:url(data:font/woff2;base64,{b64}) format("woff2");}}</style>')

def build(spec):
    t = spec.get("type")
    if t not in RENDERERS:
        die(f"неизвестный type '{t}'; доступны: {', '.join(RENDERERS)}")
    finish = spec.get("finish", "quiet" if t in ("badge", "digest") else "parade")
    W, H, content, scale_note = RENDERERS[t](spec)
    if t == "badge":
        return doc(W, H, spec.get("title", "Бейдж"), font_face_style() + "\n" + content)
    tb, _ = title_block(spec)
    body = "\n".join([font_face_style(), frame(H, spec.get("frame", "vector"), finish),
                      tb, content, footer(spec, H, scale_note or "")])
    return doc(W, H, spec.get("title", "График"), body)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="path to JSON spec")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    svg = build(spec)
    pathlib.Path(a.out).write_text(svg, encoding="utf-8")
    print(f"{a.out}: {len(svg.encode())/1024:.0f} KB")

if __name__ == "__main__":
    main()
