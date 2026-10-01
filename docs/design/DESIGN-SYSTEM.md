# Acuven Shop 设计系统

> 视觉稿阶段在 Claude Design 设计系统中编写的规范原文（英文），2026-09-30 随视觉稿导出。页面内容与流程以 `docs/UX.md`、`docs/UX-COPY.md` 为准，本文件只管样式。

Acuven Shop is a public demo online store: prospects walk through browsing, checkout, a simulated payment and a refund, and nothing real is ever charged or shipped. The storefront is **decoratable**: the store admin picks one of 10 themes, an accent colour from that theme's palette, a logo, and the order and visibility of home-page blocks. Every theme shares one layout and one component set; a theme only changes variables.

## Using this system

- Wrap every storefront page in an element with class `acs` and set `data-shop-theme="<theme id>"`, optionally `data-accent="<accent id>"` (omit for the theme's default accent) and `data-mode="auto"` (dark follows the visitor's system setting; `light` / `dark` force a mode, used only in mockups).
- Load `docs/design/tokens/acuven-shop.css` (the frontend copies it). It declares the Latin web fonts, served from the site itself (`frontend/public/fonts/`, Latin subset only; no third-party font requests), defines all 10 themes × light/dark as CSS custom properties, then the component classes (`acs-*`). Components read **semantic variables only** (`--surface`, `--ink`, `--accent`…); never hard-code a colour, font or radius in a page.
- The machine-readable theme table is `docs/design/tokens/themes.json` (every colour per theme and mode, fonts, radii, border width, banner pattern, accent options). `docs/design/tokens/tokens.json` carries the default theme (Pandan) only.
- The back office (A01–A08) uses the fixed `acs-admin` style and is never themed.

## Content fundamentals

- Default language English; visitors can switch to 中文 or Bahasa Melayu. All interface text comes from `docs/UX-COPY.md` keys; product names and descriptions come from the catalogue. Mockups use the `en` column.
- Money is always `RM {amount}` with two decimals, computed by the server. Show discount rows as the plain amount (`Coupon discount  RM 10.00`), with no minus sign or recolouring, because UX-COPY defines no signed format. Points are "points", never money.
- Say "demo" plainly and often. Write in the second person ("your order"), sentence case, no exclamation marks, no emoji.
- Never show the order number or phone number in a URL. Mockups use a placeholder order number (`K7Q2-9MXA`); the real format is not defined yet (DESIGN only requires high entropy).

## Demo markers (cannot be themed away)

- `acs-banner` is the first element on every storefront page: `common.demo_banner` on desktop, `common.demo_banner_short` on phones, with a `DEMO` tag. Store decoration can restyle it through the theme but can never hide it or remove it.
- Each page's ★ hint uses `acs-hint` with the star mark. The ◆ hints beside Place order, the simulated payment buttons and the refund submit button use the diamond mark. The checkout phone notice and form notice use `acs-hint acs-hint--block`, always open, with no checkbox.
- The `demo` colour family (`--demo`, `--demo-soft`, `--demo-mark`) means "this is a demo" and nothing else. Never use it for sales, errors, product imagery or decoration. Every theme's demo colour differs from its accent.
- The home-page `home.demo_hint` must sit outside any block the admin can hide.

## Visual foundations

- **Colour.** Surfaces: `surface` (page), `surface-alt` (bands, hero panel, summary), `surface-raised` (cards, inputs, dialogs), `tile` and `tile-alt` (image placeholders). Text: `ink`, `ink-muted` (hints, captions). Lines: `rule` for decorative hairlines, `border` for control borders. Actions: `accent` fill with `on-accent` text; `accent-ink` for links and accent-coloured text. Status: `success`, `danger` and their `-soft` backgrounds. Every pair was checked in all 10 themes, in both modes and with every accent option: text ≥ 4.5:1, control borders, focus ring and demo marks ≥ 3:1.
- **Type.** Three families per theme: `--font-display` for headings (display-xl to display-s, multiplied by the theme's `--display-scale`), `--font-body` for everything else, and `--font-num` for prices, order numbers and SKUs, always with tabular figures. Chinese uses the visitor's system fonts (PingFang SC, Microsoft YaHei, Noto Sans CJK SC and similar; serif themes fall back to Songti SC, Noto Serif CJK SC and similar); no Chinese web font is downloaded. Do not uppercase Chinese or Malay running text; `acs-label` is for short Latin eyebrows only.
- **Spacing.** Spacing uses a 4px base: `space-1` 4 through `space-9` 96. Desktop frames are 1440 wide with an 80px gutter and 1280 content; phones are 390 with a 16px gutter. Sections are `space-9` apart on desktop and `space-7` on phones.
- **Shape.** Each theme sets four radii: `--radius-control` (inputs, chips), `--radius-card` (panels, notices, dialogs), `--radius-tile` (images) and `--radius-button`. It also sets `--border-w` (1–2px). Cards stay flat; only dropdowns, dialogs and the phone's sticky bar use `shadow-raise`.
- **Focus.** Draw a 2px solid ring in `--focus`, offset 2px, on every interactive element.
- **Touch.** Every control is at least 44px high.
- **Layout.** All themes share one layout. Home page: hero panel (text left, three product plinths right whose corners follow `--hero-shape`), "How this demo works" (four numbered steps), categories (six tiles, 3 × 2 on desktop, a horizontal row on phones), then featured products (four cards).

## Iconography

- Draw inline stroke SVGs at 1.8–2px, coloured with `currentColor`. No icon font and no emoji.
- The ★ and ◆ demo marks are filled SVG shapes in `--demo-mark`.
- Product and category images are placeholder pictograms (classes `ph-fill`, `ph-ink`, `ph-soft`) until real photos exist. There is no logo file yet: the brand is set as the text `ACUVEN SHOP` in the display face. When the admin uploads a logo, it replaces the text inside `acs-brand`.

## Themes

Ten store themes, chosen by the admin in store decoration. Each has light and dark values (dark follows the visitor's system setting) and 4–5 accent options for the admin to pick from. Full values: `docs/design/tokens/themes.json`; regenerate with `python docs/design/tools/gen.py`.

| # | id | Name | Display / body / numbers | Radii control·card·tile·button | Border | Banner | Suits |
|---|---|---|---|---|---|---|---|
| 1 | `pandan` (default) | Pandan 班兰 | Instrument Serif / Manrope / Manrope | 6·6·4·6 | 1px | solid | lifestyle, home |
| 2 | `pasar` | Pasar Pagi 早市 | Bricolage Grotesque / Onest / Onest | pill·24·28·pill | 1.5px | stripes | groceries, community shops |
| 3 | `receipt` | Receipt 小票 | IBM Plex Sans / IBM Plex Sans / IBM Plex Mono | 2·2·0·2 | 1px | solid | gadgets, tools |
| 4 | `kopitiam` | Kopitiam 咖啡店 | Young Serif / Figtree / Figtree | 8·10·10·8 | 1.5px | solid | food, drinks, bakery |
| 5 | `batik` | Batik 蜡染 | DM Serif Display / Work Sans / Work Sans | 4·8·8·4 | 1px | solid | crafts, traditional wear |
| 6 | `malam` | Pasar Malam 夜市 | Unbounded / Rubik / Rubik | 10·16·16·pill | 1.5px | stripes | streetwear, snacks |
| 7 | `gula` | Gula-Gula 糖果 | Fredoka / Nunito / Nunito | 16·28·32·pill | 2px | dots | kids, sweets |
| 8 | `galeri` | Galeri 画廊 | Syne / Karla / Karla | 0·0·0·0 | 1px | solid | fashion, design goods |
| 9 | `songket` | Songket 金线 | Cormorant / Mulish / Mulish | 2·4·4·2 | 1px | solid | festive gifts, premium |
| 10 | `litar` | Litar 电路 | Geist / Geist / Geist Mono | 6·8·8·6 | 1px | solid | electronics |

Rules:

- `--hero-shape` sets the corner shape of the home hero's product plinths: arches for Pandan, Kopitiam, Pasar Malam and Songket, a full pill for Gula-Gula, 32px for Pasar Pagi, 8px for Batik and Litar, square for Receipt and Galeri.
- A theme may change colours, fonts, radii, border width, the hero shape and the banner pattern. It may not change layout, copy, the order of checkout steps, or anything the demo markers need to stay visible.
- Accent options are pre-checked for contrast in both modes. The admin cannot enter an arbitrary colour.
- Adding a theme means adding one entry to `docs/design/tools/themes_src.py` and running `gen.py`: every colour pair is re-checked, and the build fails if any pair falls below its minimum.
