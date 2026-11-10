"""Generate docs/design/tokens/ from themes_src.py and ../src/components.css.

Resolves the 10 store themes, checks every colour pair against WCAG contrast
(text 4.5:1; control borders, focus ring and demo marks 3:1), nudges a failing
colour's OKLCH lightness until it passes, and exits non-zero if anything still
fails. Standard library only.

Run from the repository root: python docs/design/tools/gen.py
"""

import json
import math
import os
import sys
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from promo_src import OPTIONAL_NONE, PACKS, SLOTS  # noqa: E402
from themes_src import STATUS, THEMES  # noqa: E402

OUT = os.path.join(HERE, "..", "tokens")
SRC = os.path.join(HERE, "..", "src")

# Latin subsets served from the site itself (frontend/public/fonts/, Kelvin 2026-10-01): no
# third-party font requests. Files and their OFL licences come from the Fontsource packages
# (variable builds where the family has one). Chinese uses the visitor's system fonts.
FONT_DIR = "/fonts/"
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
)
FONT_FILES = [  # (family, file, weight or variable weight range)
    ("Instrument Serif", "instrument-serif-latin-400-normal.woff2", "400"),
    ("Manrope", "manrope-latin-wght-normal.woff2", "200 800"),
    ("Bricolage Grotesque", "bricolage-grotesque-latin-opsz-normal.woff2", "200 800"),
    ("Onest", "onest-latin-wght-normal.woff2", "100 900"),
    ("IBM Plex Sans", "ibm-plex-sans-latin-wght-normal.woff2", "100 700"),
    # IBM Plex Mono: 400-600 only, the same weights the approved design loaded; bold prices
    # (700) render with the 600 face exactly as in the approved reference images.
    ("IBM Plex Mono", "ibm-plex-mono-latin-400-normal.woff2", "400"),
    ("IBM Plex Mono", "ibm-plex-mono-latin-500-normal.woff2", "500"),
    ("IBM Plex Mono", "ibm-plex-mono-latin-600-normal.woff2", "600"),
    ("Young Serif", "young-serif-latin-400-normal.woff2", "400"),
    ("Figtree", "figtree-latin-wght-normal.woff2", "300 900"),
    ("DM Serif Display", "dm-serif-display-latin-400-normal.woff2", "400"),
    ("Work Sans", "work-sans-latin-wght-normal.woff2", "100 900"),
    ("Unbounded", "unbounded-latin-wght-normal.woff2", "200 900"),
    ("Rubik", "rubik-latin-wght-normal.woff2", "300 900"),
    ("Fredoka", "fredoka-latin-wght-normal.woff2", "300 700"),
    ("Nunito", "nunito-latin-wght-normal.woff2", "200 1000"),
    ("Syne", "syne-latin-wght-normal.woff2", "400 800"),
    ("Karla", "karla-latin-wght-normal.woff2", "200 800"),
    ("Cormorant", "cormorant-latin-wght-normal.woff2", "300 700"),
    ("Mulish", "mulish-latin-wght-normal.woff2", "200 1000"),
    ("Geist", "geist-latin-wght-normal.woff2", "100 900"),
    ("Geist Mono", "geist-mono-latin-wght-normal.woff2", "100 900"),
]
FONT_FACES = "\n".join(
    f"@font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; "
    f"font-display: swap; src: url('{FONT_DIR}{file}') format('woff2'); unicode-range: {LATIN}; }}"
    for family, file, weight in FONT_FILES
)


# ---------- colour math ----------
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def rgb2hex(c):
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in c)


def lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def delin(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def lum(h):
    r, g, b = (lin(v) for v in hex2rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def to_oklch(h):
    r, g, b = (lin(v) for v in hex2rgb(h))
    lc = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mc = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    sc = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    lc, mc, sc = (math.copysign(abs(x) ** (1 / 3), x) for x in (lc, mc, sc))
    big_l = 0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc
    a = 1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc
    bb = 0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc
    return big_l, math.hypot(a, bb), math.atan2(bb, a)


def from_oklch(big_l, c, h):
    a, bb = c * math.cos(h), c * math.sin(h)
    lc = (big_l + 0.3963377774 * a + 0.2158037573 * bb) ** 3
    mc = (big_l - 0.1055613458 * a - 0.0638541728 * bb) ** 3
    sc = (big_l - 0.0894841775 * a - 1.2914855480 * bb) ** 3
    r = 4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc
    g = -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc
    b = -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc
    return rgb2hex(tuple(delin(max(0.0, min(1.0, v))) for v in (r, g, b)))


def ensure(fg, bgs, target):
    """Move fg lightness away from the backgrounds until it reaches target on all of them."""
    if min(contrast(fg, b) for b in bgs) >= target:
        return fg
    big_l, c, h = to_oklch(fg)
    darker = sum(lum(b) for b in bgs) / len(bgs) > 0.18
    cand = fg
    for _ in range(200):
        big_l = max(0.0, min(1.0, big_l - 0.005 if darker else big_l + 0.005))
        cand = from_oklch(big_l, c, h)
        if min(contrast(cand, b) for b in bgs) >= target:
            return cand
    return cand


def pick_on(fill, dark_ink):
    """Text colour on a filled button: white or a dark ink, whichever reads better."""
    return max(("#ffffff", dark_ink), key=lambda c: contrast(c, fill))


# ---------- resolve ----------
log, fails = [], []


def fix(theme, mode, t, key, bg_keys, target):
    before = t[key]
    t[key] = ensure(t[key], [t[k] for k in bg_keys], target)
    if t[key] != before:
        log.append(f"{theme}/{mode}: {key} {before} -> {t[key]} (>= {target} on {bg_keys})")


def resolve_accent(fill, mode, t):
    on = pick_on(fill, t["ink"] if mode == "light" else "#0b0b0b")
    if contrast(on, fill) < 4.5:
        fill = ensure(fill, [on], 4.5)
    ink = ensure(fill, [t["surface"], t["surface-alt"], t["surface-raised"]], 4.5)
    return {"accent": fill, "on-accent": on, "accent-ink": ink, "focus": ink}


def dark_variant(hexlight):
    _, c, h = to_oklch(hexlight)
    return from_oklch(0.80, min(c, 0.13), h)


resolved = {}
for th in THEMES:
    tid = th["id"]
    resolved[tid] = {"modes": {}, "accents": []}
    for mode in ("light", "dark"):
        t = dict(th[mode])
        t.update(STATUS[mode])
        surfaces = ["surface", "surface-alt", "surface-raised"]
        fix(tid, mode, t, "ink", surfaces + ["tile", "tile-alt"], 4.5)
        fix(tid, mode, t, "ink-muted", surfaces, 4.5)
        fix(tid, mode, t, "border", ["surface", "surface-raised"], 3.0)
        fix(tid, mode, t, "on-demo", ["demo"], 4.5)
        fix(tid, mode, t, "on-demo-soft", ["demo-soft"], 4.5)
        fix(tid, mode, t, "demo-mark", ["demo-soft", "surface", "surface-raised"], 3.0)
        fix(tid, mode, t, "on-banner", ["banner", "banner-2"], 4.5)
        fix(tid, mode, t, "on-footer", ["footer"], 4.5)
        for s in ("success", "danger"):
            fix(tid, mode, t, f"on-{s}-soft", [f"{s}-soft"], 4.5)
            fix(tid, mode, t, s, ["surface", "surface-raised"], 4.5)
        acc = resolve_accent(t["accent"], mode, t)
        if acc["accent"] != t["accent"]:
            log.append(f"{tid}/{mode}: accent {t['accent']} -> {acc['accent']} (button text)")
        t.update(acc)
        resolved[tid]["modes"][mode] = t
    lt, dk = resolved[tid]["modes"]["light"], resolved[tid]["modes"]["dark"]
    for i, (aid, aname, hexl) in enumerate(th["accents"]):
        light = resolve_accent(hexl, "light", lt)
        dark_fill = th["dark"]["accent"] if i == 0 else dark_variant(hexl)
        dark = resolve_accent(dark_fill, "dark", dk)
        resolved[tid]["accents"].append({"id": aid, "name": aname, "light": light, "dark": dark})


# ---------- festival promotion palettes (DESIGN 1.23「节日促销活动」第 12 条) ----------
def mix(a, b, t):
    """sRGB blend: t = 0 gives a, t = 1 gives b."""
    return rgb2hex(tuple(x + (y - x) * t for x, y in zip(hex2rgb(a), hex2rgb(b), strict=True)))


WHITE, BLACK, NIGHT = "#ffffff", "#000000", "#0e0e10"


def promo_seed_modes(seeds):
    p, s, d = seeds["primary"], seeds["secondary"], seeds["demo"]
    ink_l = mix(BLACK, p, 0.22)
    light = {
        "surface": mix(WHITE, p, 0.035),
        "surface-alt": mix(WHITE, p, 0.08),
        "surface-raised": mix(WHITE, p, 0.012),
        "tile": mix(WHITE, s, 0.2),
        "tile-alt": mix(WHITE, p, 0.12),
        "ink": ink_l,
        "ink-muted": mix(ink_l, WHITE, 0.32),
        "rule": mix(WHITE, p, 0.16),
        "border": mix(WHITE, ink_l, 0.55),
        "accent": p,
        "demo": d,
        "on-demo": "#161616",
        "demo-soft": mix(WHITE, d, 0.28),
        "on-demo-soft": mix(BLACK, d, 0.4),
        "demo-mark": mix(BLACK, d, 0.55),
        "banner": mix(BLACK, p, 0.62),
        "banner-2": mix(BLACK, p, 0.5),
        "on-banner": mix(WHITE, s, 0.12),
        "footer": mix(BLACK, p, 0.5),
        "on-footer": mix(WHITE, s, 0.1),
        "promo-1": p,
        "promo-2": s,
        "promo-soft": mix(WHITE, s, 0.22),
        "on-promo-soft": ink_l,
    }
    ink_d = mix(WHITE, s, 0.06)
    surf_d = mix(NIGHT, p, 0.1)
    dark = {
        "surface": surf_d,
        "surface-alt": mix(NIGHT, p, 0.16),
        "surface-raised": mix(NIGHT, p, 0.2),
        "tile": mix(NIGHT, s, 0.22),
        "tile-alt": mix(NIGHT, p, 0.26),
        "ink": ink_d,
        "ink-muted": mix(ink_d, surf_d, 0.35),
        "rule": mix(NIGHT, p, 0.32),
        "border": mix(surf_d, ink_d, 0.45),
        "accent": dark_variant(p),
        "demo": d,
        "on-demo": "#161616",
        "demo-soft": mix(NIGHT, d, 0.22),
        "on-demo-soft": mix(WHITE, d, 0.35),
        "demo-mark": d,
        "banner": mix(NIGHT, p, 0.5),
        "banner-2": mix(NIGHT, p, 0.38),
        "on-banner": mix(WHITE, s, 0.12),
        "footer": mix(BLACK, p, 0.12),
        "on-footer": mix(WHITE, p, 0.1),
        "promo-1": dark_variant(p),
        "promo-2": dark_variant(s),
        "promo-soft": mix(NIGHT, s, 0.24),
        "on-promo-soft": ink_d,
    }
    return {"light": light, "dark": dark}


promo_resolved = {}
for pk in PACKS:
    pid = pk["id"]
    promo_resolved[pid] = {}
    for mode, t in promo_seed_modes(pk["seeds"]).items():
        t.update(STATUS[mode])
        name = f"promo:{pid}"
        surfaces = ["surface", "surface-alt", "surface-raised"]
        fix(name, mode, t, "ink", surfaces + ["tile", "tile-alt"], 4.5)
        fix(name, mode, t, "ink-muted", surfaces, 4.5)
        fix(name, mode, t, "border", ["surface", "surface-raised"], 3.0)
        fix(name, mode, t, "on-demo", ["demo"], 4.5)
        fix(name, mode, t, "on-demo-soft", ["demo-soft"], 4.5)
        fix(name, mode, t, "demo-mark", ["demo-soft", "surface", "surface-raised"], 3.0)
        fix(name, mode, t, "on-banner", ["banner", "banner-2"], 4.5)
        fix(name, mode, t, "on-footer", ["footer"], 4.5)
        for s in ("success", "danger"):
            fix(name, mode, t, f"on-{s}-soft", [f"{s}-soft"], 4.5)
            fix(name, mode, t, s, ["surface", "surface-raised"], 4.5)
        t.update(resolve_accent(t["accent"], mode, t))
        # Badge, strip and progress fill: a graphic at least 3:1 on the page, text on it 4.5:1.
        fix(name, mode, t, "promo-1", ["surface", "surface-alt", "surface-raised"], 3.0)
        t["on-promo-1"] = pick_on(t["promo-1"], t["ink"] if mode == "light" else "#0b0b0b")
        fix(name, mode, t, "promo-1", ["on-promo-1"], 4.5)
        t["promo-ink"] = ensure(t["promo-1"], [t[k] for k in surfaces], 4.5)
        fix(name, mode, t, "on-promo-soft", ["promo-soft"], 4.5)
        promo_resolved[pid][mode] = t

# ---------- audit ----------
PAIRS = [
    ("ink", ["surface", "surface-alt", "surface-raised", "tile", "tile-alt"], 4.5),
    ("ink-muted", ["surface", "surface-alt", "surface-raised"], 4.5),
    ("accent-ink", ["surface", "surface-alt", "surface-raised"], 4.5),
    ("on-accent", ["accent"], 4.5),
    ("on-demo", ["demo"], 4.5),
    ("on-demo-soft", ["demo-soft"], 4.5),
    ("demo-mark", ["demo-soft", "surface", "surface-raised"], 3.0),
    ("on-banner", ["banner", "banner-2"], 4.5),
    ("on-footer", ["footer"], 4.5),
    ("border", ["surface", "surface-raised"], 3.0),
    ("focus", ["surface", "surface-raised"], 3.0),
    ("success", ["surface", "surface-raised"], 4.5),
    ("danger", ["surface", "surface-raised"], 4.5),
    ("on-success-soft", ["success-soft"], 4.5),
    ("on-danger-soft", ["danger-soft"], 4.5),
]
for tid, r in resolved.items():
    for mode, t in r["modes"].items():
        for fg, bgs, target in PAIRS:
            for b in bgs:
                c = contrast(t[fg], t[b])
                if c < target - 1e-9:
                    fails.append(f"{tid}/{mode}: {fg} on {b} = {c:.2f} < {target}")
        for a in r["accents"]:
            x = dict(t)
            x.update(a[mode])
            for fg, bgs, target in PAIRS[2:4]:
                for b in bgs:
                    c = contrast(x[fg], x[b])
                    if c < target:
                        fails.append(f"{tid}/{mode}/accent {a['id']}: {fg} on {b} = {c:.2f}")

PROMO_PAIRS = PAIRS + [
    ("promo-1", ["surface", "surface-alt", "surface-raised", "promo-soft"], 3.0),
    ("on-promo-1", ["promo-1"], 4.5),
    ("promo-ink", ["surface", "surface-alt", "surface-raised"], 4.5),
    ("on-promo-soft", ["promo-soft"], 4.5),
]
for pid, modes in promo_resolved.items():
    for mode, t in modes.items():
        for fg, bgs, target in PROMO_PAIRS:
            for b in bgs:
                c = contrast(t[fg], t[b])
                if c < target - 1e-9:
                    fails.append(f"promo:{pid}/{mode}: {fg} on {b} = {c:.2f} < {target}")

print("AUTO-ADJUSTED:")
print("\n".join(log) or "  none")
print("FAILS:")
print("\n".join(fails) or "  none")
if fails:
    sys.exit(1)

# ---------- themes.json ----------
COLOR_KEYS = list(resolved["pandan"]["modes"]["light"].keys())
os.makedirs(OUT, exist_ok=True)
themes_json = {"version": 1, "default": "pandan", "colorKeys": COLOR_KEYS, "themes": []}
for th in THEMES:
    r = resolved[th["id"]]
    themes_json["themes"].append(
        {
            "id": th["id"],
            "name": th["name"],
            "nameZh": th["zh"],
            "suits": th["fit"],
            "fonts": th["fonts"],
            "displayWeight": th["display_weight"],
            "displayScale": th["display_scale"],
            "displayTracking": th["display_tracking"],
            "radius": th["radius"],
            "borderWidth": th["border_w"],
            "banner": th["banner"],
            "heroShape": th["hero_shape"],
            "colors": r["modes"],
            "accentOptions": r["accents"],
        }
    )


def write(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


write("themes.json", json.dumps(themes_json, ensure_ascii=False, indent=1) + "\n")

# ---------- acuven-shop.css ----------
SERIF = {"Instrument Serif", "Young Serif", "DM Serif Display", "Cormorant"}
MONO = {"IBM Plex Mono", "Geist Mono"}

# System CJK fonts after the Latin face, so Chinese text never waits for a download.
CJK_SANS = (
    "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Source Han Sans SC'"
)
CJK_SERIF = "'Songti SC', 'STSong', 'Noto Serif CJK SC', 'Source Han Serif SC', 'SimSun'"


def stack(f):
    if f in MONO:
        return f"'{f}', ui-monospace, 'SFMono-Regular', Menlo, monospace"
    if f in SERIF:
        return f"'{f}', {CJK_SERIF}, Georgia, serif"
    return f"'{f}', {CJK_SANS}, system-ui, -apple-system, 'Segoe UI', sans-serif"


BANNER = {
    "solid": ("none", "auto"),
    "stripes": (
        "repeating-linear-gradient(-45deg, var(--banner) 0 14px, var(--banner-2) 14px 28px)",
        "auto",
    ),
    "dots": ("radial-gradient(circle, var(--banner-2) 3px, transparent 3.5px)", "16px 16px"),
}


def decls(d):
    return " ".join(f"--{k}: {v};" for k, v in d.items())


def dark_sel(base):
    return f'{base}[data-mode="dark"], html[data-theme$="-dark"] {base}:not([data-mode="light"])'


css = ["/* ===== Themes: generated by docs/design/tools/gen.py — do not edit by hand ===== */"]
for th in THEMES:
    tid = th["id"]
    r = resolved[tid]
    base = f'.acs[data-shop-theme="{tid}"]'
    img, size = BANNER[th["banner"]]
    rad = th["radius"]
    shape = {
        "font-display": stack(th["fonts"]["display"]),
        "font-body": stack(th["fonts"]["body"]),
        "font-num": stack(th["fonts"]["num"]),
        "display-weight": th["display_weight"],
        "display-scale": th["display_scale"],
        "display-tracking": th["display_tracking"],
        "radius-control": f"{rad['control']}px",
        "radius-card": f"{rad['card']}px",
        "radius-tile": f"{rad['tile']}px",
        "radius-button": f"{rad['button']}px",
        "border-w": f"{th['border_w']}px",
        "hero-shape": th["hero_shape"],
        "banner-image": img,
        "banner-size": size,
    }
    default = tid == "pandan"
    css.append(f"/* {th['name']} {th['zh']} */")
    sel = f".acs, {base}" if default else base
    css.append(f"{sel} {{ {decls(shape)} {decls(r['modes']['light'])} color-scheme: light; }}")
    dsel = f"{dark_sel('.acs')}, {dark_sel(base)}" if default else dark_sel(base)
    css.append(f"{dsel} {{ {decls(r['modes']['dark'])} color-scheme: dark; }}")
    auto = f'{base}[data-mode="auto"]'
    auto = f'.acs[data-mode="auto"], {auto}' if default else auto
    css.append(
        f"@media (prefers-color-scheme: dark) {{ {auto} "
        f"{{ {decls(r['modes']['dark'])} color-scheme: dark; }} }}"
    )
    for a in r["accents"][1:]:
        abase = f'{base}[data-accent="{a["id"]}"]'
        css.append(f"{abase} {{ {decls(a['light'])} }}")
        css.append(f"{dark_sel(abase)} {{ {decls(a['dark'])} }}")
        css.append(
            f'@media (prefers-color-scheme: dark) {{ {abase}[data-mode="auto"] '
            f"{{ {decls(a['dark'])} }} }}"
        )


# Promotion palettes and motifs. A palette sits on an element inside .acs (the .acs-promo wrapper,
# or a preview sketch), so its values replace the theme colours there whatever the theme selector
# specificity; fonts, radii and layout still come from the theme on .acs.
def svg_url(inner):
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>{inner}</svg>"
    return 'url("data:image/svg+xml,' + quote(svg, safe="/:=' .,-()") + '")'


# A 240 px tile with the motif scattered at a few sizes and angles
# (ornament "fall", poster pattern).
SCATTER = [
    (18, 22, 1.0, 0),
    (128, 8, 0.7, 25),
    (196, 96, 1.1, -15),
    (62, 132, 0.8, 40),
    (150, 178, 0.6, 10),
]


def scatter_url(inner):
    parts = "".join(
        f"<g transform='translate({x} {y}) rotate({r} 12 12) scale({k})'>{inner}</g>"
        for x, y, k, r in SCATTER
    )
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 240'>{parts}</svg>"
    return 'url("data:image/svg+xml,' + quote(svg, safe="/:=' .,-()") + '")'


css.append(
    "/* ===== Festival promotions: generated from tools/promo_src.py "
    "(DESIGN 1.23 第 12 条) ===== */"
)
for pk in PACKS:
    pid = pk["id"]
    css.append(
        f'.acs [data-promo-pack="{pid}"] {{ --promo-motif: {svg_url(pk["motif"])}; '
        f"--promo-art: {svg_url(pk['art'])}; --promo-scatter: {scatter_url(pk['motif'])}; }}"
    )
for pk in PACKS:
    pid = pk["id"]
    lt, dk = promo_resolved[pid]["light"], promo_resolved[pid]["dark"]
    sel = f'[data-promo-palette="{pid}"]'
    css.append(f"/* {pk['name']} {pk['zh']} colours */")
    css.append(f".acs {sel} {{ {decls(lt)} }}")
    dark = (
        f'.acs[data-mode="dark"] {sel}, .acs {sel}[data-mode="dark"], '
        f'html[data-theme$="-dark"] .acs:not([data-mode="light"]) {sel}'
    )
    css.append(f"{dark} {{ {decls(dk)} }}")
    css.append(
        f"@media (prefers-color-scheme: dark) {{ "
        f'.acs[data-mode="auto"] {sel}:not([data-mode="light"]) '
        f"{{ {decls(dk)} }} }}"
    )
# Patterned demo banners repaint with the palette's banner colours.
for th in THEMES:
    img, _ = BANNER[th["banner"]]
    if img != "none":
        css.append(
            f'.acs[data-shop-theme="{th["id"]}"] [data-promo-palette] {{ --banner-image: {img}; }}'
        )

with open(os.path.join(SRC, "components.css"), encoding="utf-8") as f:
    components = f.read()
write("acuven-shop.css", FONT_FACES + "\n" + "\n".join(css) + "\n" + components)

# ---------- tokens.json (default theme only, Design System format) ----------
USAGE = {
    "surface": "Page background.",
    "surface-alt": "Section bands, hero panel, order summary panel.",
    "surface-raised": "Cards, inputs, dropdowns, dialogs.",
    "tile": "Product and category image placeholders.",
    "tile-alt": "Alternating image placeholder tint.",
    "ink": "Body text and headings on every surface and tile.",
    "ink-muted": "Secondary text, hints, captions on surface, surface-alt, surface-raised.",
    "rule": "Decorative hairlines and dividers (not for control borders).",
    "border": "Input, select, chip and stepper borders; at least 3:1 on surfaces.",
    "accent": "Primary button fill, selected chip ring, progress done state.",
    "on-accent": "Text and icons on accent.",
    "accent-ink": "Links and accent-coloured text on surfaces.",
    "focus": "2px focus ring outside controls; at least 3:1 on surfaces.",
    "demo": "DEMO badge fill. Used for demo markers only.",
    "on-demo": "Text on demo.",
    "demo-soft": "Background of star page hints and diamond action hints.",
    "on-demo-soft": "Hint text on demo-soft.",
    "demo-mark": "The star / diamond glyph; at least 3:1 on demo-soft and surfaces.",
    "banner": "Demo banner ground (cannot be hidden by store decoration).",
    "banner-2": "Second banner colour for striped or dotted patterns.",
    "on-banner": "Demo banner text.",
    "footer": "Footer ground.",
    "on-footer": "Footer text and links.",
    "success": "Success icon and text on surfaces.",
    "success-soft": "Success message background.",
    "on-success-soft": "Text on success-soft.",
    "danger": "Error text under fields and error icons on surfaces.",
    "danger-soft": "Error message background.",
    "on-danger-soft": "Text on danger-soft.",
}
pl, pd = resolved["pandan"]["modes"]["light"], resolved["pandan"]["modes"]["dark"]
tokens = {
    "name": "Acuven Shop",
    "version": 1,
    "note": "Default theme (Pandan) only; all 10 themes are in themes.json and acuven-shop.css.",
    "color": {
        "themes": [
            {"id": "pandan-light", "name": "Pandan · Light"},
            {"id": "pandan-dark", "name": "Pandan · Dark"},
        ],
        "tokens": [
            {"name": k, "value": {"pandan-light": pl[k], "pandan-dark": pd[k]}, "usage": USAGE[k]}
            for k in COLOR_KEYS
        ],
    },
    "spacing": {
        "tokens": [
            {"name": f"space-{i + 1}", "value": f"{v}px"}
            for i, v in enumerate([4, 8, 12, 16, 24, 32, 48, 64, 96])
        ]
    },
    "type": {
        "desktop": {"display-xl": 72, "display-l": 48, "display-m": 34, "display-s": 24},
        "phone": {"display-xl": 40, "display-l": 32, "display-m": 26, "display-s": 24},
        "text": {"body-l": 18, "body": 16, "body-s": 14, "caption": 13, "label": 12},
        "note": "Display sizes are multiplied by the theme's --display-scale.",
    },
}
write("tokens.json", json.dumps(tokens, ensure_ascii=False, indent=1) + "\n")

# ---------- promotions.json (the preset lists of DESIGN 1.23「节日促销活动」第 12 条) ----------
promotions = {
    "version": 1,
    "note": "Festival promotion presets: theme packs, their palettes and the decoration "
    "slot variants. "
    'A promotion keeps the store theme colours (palette "theme") or uses one pack\'s palette.',
    "keepThemePalette": "theme",
    "slots": [
        {
            "id": slot,
            "variants": variants,
            **({"none": OPTIONAL_NONE[slot]} if slot in OPTIONAL_NONE else {}),
        }
        for slot, variants in SLOTS.items()
    ],
    "colorKeys": list(promo_resolved[PACKS[0]["id"]]["light"].keys()),
    "packs": [
        {
            "id": pk["id"],
            "name": pk["name"],
            "nameZh": pk["zh"],
            "copyKey": f"admin.pack_{pk['id']}",
            "variants": dict(SLOTS),
            "palette": promo_resolved[pk["id"]],
        }
        for pk in PACKS
    ],
}
write("promotions.json", json.dumps(promotions, ensure_ascii=False, indent=1) + "\n")
print("wrote promotions.json")
print("wrote acuven-shop.css, themes.json, tokens.json")
