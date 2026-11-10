# Source data for the festival promotion packs (DESIGN 1.23「节日促销活动」第 12 条; hand-authored).
# gen.py builds each pack's light and dark palette from the seeds below, checks contrast like the
# themes, and writes tokens/promotions.json plus the palette and motif rules in acuven-shop.css.
#
# Motifs are single-colour SVG shapes (viewBox 0 0 24 24) used as CSS masks, so the palette
# colours them. "motif" is the small repeating shape (page ornaments, badges, bullets);
# "art" is the larger shape drawn on the home poster.

# Variant ids per decoration slot, the same for every pack in the first release
# (UX 0.20「1. 全局框架」: six slots; ambient also has "none").
SLOTS = {
    "ambient": ["garland", "corners", "fall"],
    "home_poster": ["split", "centered"],
    "list_strip": ["band", "ticket"],
    "card_badge": ["tag", "seal"],
    "detail_ribbon": ["ribbon", "panel"],
    "cart_progress": ["bar", "steps"],
}
OPTIONAL_NONE = {"ambient": "none"}

PACKS = [
    {
        "id": "cny",
        "name": "Chinese New Year",
        "zh": "农历新年",
        "seeds": {"primary": "#b8141f", "secondary": "#e2a72e", "demo": "#7cc6c0"},
        "motif": (
            "<circle cx='12' cy='6' r='4'/><circle cx='17.7' cy='10.1' r='4'/>"
            "<circle cx='15.5' cy='16.9' r='4'/><circle cx='8.5' cy='16.9' r='4'/>"
            "<circle cx='6.3' cy='10.1' r='4'/>"
        ),
        "art": (
            "<rect x='11.4' y='0.5' width='1.2' height='3'/>"
            "<rect x='8.5' y='3.5' width='7' height='2' rx='.6'/>"
            "<ellipse cx='12' cy='12.5' rx='8.5' ry='7'/>"
            "<rect x='8.5' y='19.5' width='7' height='2' rx='.6'/>"
            "<rect x='11.3' y='21.5' width='1.4' height='2.5'/>"
        ),
    },
    {
        "id": "raya",
        "name": "Hari Raya",
        "zh": "开斋节",
        "seeds": {"primary": "#0f6b45", "secondary": "#d9a521", "demo": "#f0a39b"},
        "motif": (
            "<path fill-rule='evenodd' d='M12 1.5 21.5 11"
            " 12 20.5 2.5 11z M12 6 17 11 12 16 7 11z'/>"
            "<path d='M12 9 14 11 12 13 10 11z M11 20.5h2l1.5 3h-1.5l-1-1.6-1 1.6h-1.5z'/>"
        ),
        "art": (
            "<path d='M12 2c-2.2 3.2-3.4 5.4-3.4 7.4a3.4 3.4 0 0 0 6.8 0C15.4 7.4 14.2 5.2 12 2z'/>"
            "<path d='M3.5 14h17c0 3.6-3.8 6-8.5 6s-8.5-2.4-8.5-6z'/>"
            "<rect x='9' y='20.5' width='6' height='2' rx='.6'/>"
        ),
    },
    {
        "id": "deepavali",
        "name": "Deepavali",
        "zh": "屠妖节",
        "seeds": {"primary": "#6b1f8f", "secondary": "#f08a24", "demo": "#9fd8c8"},
        "motif": (
            "<path d='M12 1.5c-2 3-3.3 5.2-3.3 7.4a3.3 3.3 0 0 0 6.6 0c0-2.2-1.3-4.4-3.3-7.4z'/>"
            "<path d='M2.5 13h19c0 4.4-4.2 8-9.5 8s-9.5-3.6-9.5-8z'/>"
        ),
        "art": "".join(
            f"<ellipse cx='12' cy='5.6' rx='2.4' ry='4.6' transform='rotate({a} 12 12)'/>"
            for a in range(0, 360, 45)
        )
        + "<circle cx='12' cy='12' r='2.4'/>",
    },
    {
        "id": "christmas",
        "name": "Christmas",
        "zh": "圣诞节",
        "seeds": {"primary": "#1c5a39", "secondary": "#c62f2f", "demo": "#f3c769"},
        "motif": (
            "<rect x='11' y='1.5' width='2' height='21' rx='1'/>"
            "<rect x='11' y='1.5' width='2' height='21' rx='1' transform='rotate(60 12 12)'/>"
            "<rect x='11' y='1.5' width='2' height='21' rx='1' transform='rotate(120 12 12)'/>"
        ),
        "art": (
            "<path d='M12 1.5 19.5 11h-3.6L21.5 18.5h-19L8.1 11H4.5z'/>"
            "<rect x='10.4' y='18.5' width='3.2' height='4'/>"
        ),
    },
    {
        "id": "merdeka",
        "name": "Merdeka",
        "zh": "国庆",
        "seeds": {"primary": "#14317c", "secondary": "#d4202e", "demo": "#ffd23f"},
        "motif": (
            "<ellipse cx='12' cy='6.2' rx='3.8' ry='5.2'/>"
            "<ellipse cx='12' cy='6.2' rx='3.8' ry='5.2' transform='rotate(72 12 12)'/>"
            "<ellipse cx='12' cy='6.2' rx='3.8' ry='5.2' transform='rotate(144 12 12)'/>"
            "<ellipse cx='12' cy='6.2' rx='3.8' ry='5.2' transform='rotate(216 12 12)'/>"
            "<ellipse cx='12' cy='6.2' rx='3.8' ry='5.2' transform='rotate(288 12 12)'/>"
        ),
        "art": "".join(
            f"<rect x='11.2' y='1' width='1.6' height='7' rx='.8' transform='rotate({a} 12 12)'/>"
            for a in range(0, 360, 30)
        )
        + "<circle cx='12' cy='12' r='2.2'/>",
    },
    {
        "id": "mega_sale",
        "name": "Mega sale (11.11, 12.12, Black Friday)",
        "zh": "电商大促",
        "seeds": {"primary": "#d9481c", "secondary": "#6a2fd6", "demo": "#93d99a"},
        "motif": "<path d='M13.5 1.5 4 14h6.5l-1.5 8.5L19.5 9.5H13z'/>",
        "art": (
            "<path fill-rule='evenodd' d='M2.5 2.5h9l10 10-9 9-10-10z"
            " M7.5 5.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z'/>"
        ),
    },
    {
        "id": "generic",
        "name": "General sale",
        "zh": "通用",
        "seeds": {"primary": "#283a73", "secondary": "#f2665a", "demo": "#ffd166"},
        "motif": (
            "<path d='M12 1.5C12.9 9 15 11.1 22.5 12 15 12.9 12.9 15"
            " 12 22.5 11.1 15 9 12.9 1.5 12 9 11.1 11.1 9 12 1.5z'/>"
        ),
        "art": (
            "<rect x='2.5' y='8' width='19' height='4.5' rx='.8'/>"
            "<rect x='4' y='13.5' width='16' height='9' rx='.8'/>"
            "<path d='M12 8C9.8 3.5 5.5 3.6 6.3 6.3 7 8 12 8 12"
            " 8zM12 8c2.2-4.5 6.5-4.4 5.7-1.7C17 8 12 8 12 8z'/>"
        ),
    },
]
