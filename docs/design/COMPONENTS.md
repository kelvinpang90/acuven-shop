# 组件指南

> 设计系统中每个组件的用法说明（英文原文）。类名见 `tokens/acuven-shop.css`；每个组件的样例写法可在 `pages/` 的静态页面中找到。


## DemoBanner

The demo banner states on every storefront page that nothing here is real.

- Markup: `<div class="acs-banner"><span class="acs-tag acs-tag--demo">DEMO</span><span>…</span></div>`, placed first in the page, above the header.
- Copy: `common.demo_banner` (desktop), `common.demo_banner_short` (phones, under 768px); the tag uses `common.demo_badge`.
- The theme sets the ground (`--banner`, optional stripes or dots via `--banner-2`) and the text (`--on-banner`). Set `--gutter` to the page gutter.
- Do not make it dismissible, sticky-hidden or optional. Store decoration has no switch for it.

## DemoHint

Demo hints mark each page's demo note (★) and the three demo actions (◆).

- ★ page hint: `acs-hint` with the star mark, placed near the page title, e.g. `home.demo_hint`, `checkout.demo_hint`.
- ◆ action hint: the same box with the diamond mark, directly beside or above Place demo order (`checkout.place_order_hint`), the simulated payment buttons (`pay.action_hint`) and Submit refund request (`refund.submit_hint`).
- Always-open notices: `acs-hint acs-hint--block` for `checkout.phone_notice` and `checkout.form_notice`, each with the `checkout.form_notice_link` link to P14. Never collapse them and never add a checkbox.
- The mark is decorative (`aria-hidden`); the text carries the meaning.

## Button

Buttons trigger actions; links styled as buttons navigate.

- `acs-btn--primary`: one per view, the next step (Start shopping, Add to cart, Check out, Place demo order, Simulate success, Submit refund request).
- `acs-btn--secondary`: alternatives (Continue shopping, Simulate failure, Keep order).
- `acs-btn--quiet`: inline text actions (Forgot password?, Change, the password login link).
- `acs-btn--danger`: destructive actions only (Cancel this order, Delete my account, Yes, cancel order).
- Sizes: default 48px, `--lg` 56px for heroes, `--sm` 36px only inside dense rows (Apply, Copy). Use `--block` on phones for the main action.
- While a request runs, set `aria-busy="true"` and `disabled`, and show the matching copy (`checkout.submitting`, `pay.processing`).

## Field

Form fields: label above, control, then a hint or an error.

- Structure: `.acs-field` > `.acs-field__label` + `.acs-input` / `.acs-select` + `.acs-field__hint` or `.acs-field__error`. Always a real `<label for>`.
- Phone: `.acs-phone` pairs a country-code select with the number input (checkout step 1 lists all countries, default +60; auth pages list +60 and +65 only).
- Read-only (the guest's shipping phone at checkout): `readonly` input with a quiet Change button.
- Errors: add `.acs-field--invalid`, set `aria-invalid` and point `aria-describedby` at the error. The copy comes from UX-COPY (e.g. `checkout.phone_invalid`).
- Verification code: `.acs-otp`, `inputmode="numeric"`.

## Options

Option chips select a product variant; the stepper sets a quantity.

- Chips are `<button aria-pressed>`, one group per option name (Colour, Size). Disable a value that no SKU offers with the other selected values; it shows struck through.
- The stepper uses `common.a11y_qty_decrease` / `common.a11y_qty_increase` as the labels of its minus and plus buttons, and an `<output>` for the value. Disable minus at the minimum.

## Tag

Tags label state; they are not buttons.

- `--demo`: the DEMO badge only (banner, P06 title).
- `--outline`: `detail.english_only`, awaiting payment, refund under review.
- `--accent`: paid, packed, shipped.
- `--success`: completed, refund approved. `--danger`: refund rejected. `--muted`: cancelled. `--neutral`: out of stock today, and statuses in the back office.
- Always show the words; colour alone never carries the state.

## Alert

Alerts report the result of an action or a blocking problem.

- `--danger` for errors (`auth.code_wrong`, `lookup.not_found`, `common.rate_limited`), `--success` for confirmations (`refund.submitted`), `--info` for neutral status (`common.network_check`, `common.service_unavailable`).
- Use `role="alert"` for errors, `role="status"` otherwise. Field-level errors use `.acs-field__error` instead.

## Dialog

An in-page confirmation for irreversible actions.

- Used for `pay.cancel_confirm` with `pay.cancel_confirm_no` (secondary) and `pay.cancel_confirm_yes` (danger). Browser `confirm()` is not used.
- Keep focus inside while open; Escape and the secondary button close it.

## ProductCard

Product cards and category tiles for P01 and P02.

- Card: image tile, name, then price (`list.price_from` when a product has several variants, otherwise `common.price_myr`). Add `detail.english_only` beside the name when the current language falls back to English.
- Out of stock today: add `.acs-pcard--oos` and replace the price line with a neutral `list.out_of_stock` tag.
- Grid: `.acs-pgrid`, 4 columns on desktop, 2 on phones. Tiles alternate `tile` and `tile-alt`.
- Category tile: `.acs-cat` with a square image and the category name from the catalogue.

## Summary

The money summary on checkout (P05) and order detail (P09).

- Each line is `.acs-row`: label left, amount right in `--font-num` with tabular figures. The total uses `.acs-row--total`.
- Amounts come from the server and print as `RM {amount}`. Discount rows show the plain amount with no minus sign.
- The reference currency line (`common.fx_reference`) and `checkout.fx_note` appear only after a shipping country is chosen.

## Tabs

Tabs switch between views of one page.

- P12: Password / SMS code (`auth.login_method_password`, `auth.login_method_sms`). P13 on phones: My orders / My points / My coupons / Settings.
- Use `role="tablist"`, `role="tab"` and `aria-selected`; the selected tab is underlined in `--accent`.

## OrderProgress

The five-step demo fulfilment progress on P09.

- Steps: `order.status_awaiting`, `status_paid`, `status_packed`, `status_shipped`, `status_completed`. Mark finished steps `data-state="done"` and the current one `data-state="current"` with `aria-current="step"`.
- Horizontal on desktop, vertical under 768px. A cancelled order shows the `order.status_cancelled` tag instead of the progress.

## SiteHeader

The storefront header under the demo banner.

- Desktop: brand, search (`list.search_placeholder`, `common.search`), language links (`common.lang_*`); second row: Shop, Track order, then Log in or My account, and Cart ({count}).
- Phone: menu button (`common.nav_menu`), brand, current language, Cart; search on its own row.
- The brand is the text ACUVEN SHOP in the display face, or the admin's uploaded logo inside `.acs-brand` (max 40px high).

## SiteFooter

The storefront footer.

- `common.footer_demo`, the Privacy link (`common.nav_privacy`) and the WhatsApp button (`common.whatsapp_cta`).
- When the WhatsApp contact link is not configured, the button is removed entirely: no placeholder or coming-soon text.

## AdminShell

The back-office frame: demo banner, side navigation, content.

- Always the fixed neutral `acs-admin` style; store themes never apply here. Light and dark follow the page.
- `admin.demo_banner` sits at the top of every admin page. Tables use `.acs-admin__table`; the selected row sets `aria-selected`.
- No export buttons anywhere in the back office.
- Form fields use `.acs-admin__field` (label above the control), errors use `.acs-admin__alert`, and the desktop language links use `.acs-admin__lang` with `aria-current="true"` on the current language (added 2026-10-05 for A01).
- A08 store design (added 2026-10-08): each theme option is a `.acs-admin__choice` label holding its radio, a `.acs-swatch` strip and the theme name; the strip is an `.acs` element with that theme's `data-shop-theme` and three `<span>`s, `.acs-swatch__surface`, `.acs-swatch__accent`, `.acs-swatch__demo`. Each accent option is a `.acs-admin__ring` label (screen-reader name `admin.accent_option`) holding a visually hidden radio and an `.acs.acs-swatch--dot` with `data-shop-theme` and `data-accent`. The checked option is outlined through `:has(input:checked)`; on phones the theme options become plain 44px rows and the checked one is bold. The preview sits in `.acs-admin__preview`, inside an `.acs` element carrying the chosen theme, accent and `data-mode` (`light` or `dark`), and is built from the storefront components.

## FestivalPromotion

Festival promotion decoration (added 2026-11-10; DESIGN 1.23「节日促销活动」第 12 条, UX 0.20「1. 全局框架」). Presets live in `tokens/promotions.json`; the boards are `PROMO-*`, `P0x-promo-*`, `P15-*` and `A12-*`.

- Wrapper: while a promotion is shown, put one `<div class="acs-promo" data-promo-pack="<pack id>">` directly inside the page's `.acs` element, around the banner, header, page and footer. Add `data-promo-palette="<pack id>"` when the promotion uses festive colours; leave it out to keep the store theme colours (the promo colours then follow the theme accent). Set `data-promo-ambient="corners"` or `"fall"` for those ornaments; the garland is an element (below). Remove the wrapper attributes, or the wrapper, as soon as no promotion is running.
- A palette replaces colours only: fonts, radii, borders and layout still come from the theme on `.acs`. Palettes pass the same contrast checks as themes, plus `--promo-1` 3:1 on the page, `--on-promo-1` 4.5:1 on `--promo-1`, `--promo-ink` 4.5:1 on surfaces and `--on-promo-soft` 4.5:1 on `--promo-soft`.
- Ornaments (`ambient`): `garland` = `<div class="acs-promo-garland" aria-hidden="true"></div>` right after the header; `corners` and `fall` are drawn by the wrapper behind the content. All are decorative, never cover the demo banner, hints or controls, and the falling motifs stop under `prefers-reduced-motion`. `none` draws nothing.
- Home poster (`home_poster`): template mode is `section.acs-promo-poster.acs-promo-poster--split` or `--centered` with `__text` (title, subtitle, `.acs-promo-count` countdown, a `.acs-btn.acs-btn--promo` link to `/sale`) and an empty `__art` (aria-hidden). Image mode is `a.acs-promo-poster-img` holding the uploaded `<img>` (3:1 on desktop, 4:5 on phones, cropped from the centre), with the alt text in the image.
- Sale strip (`list_strip`): `.acs-promo-strip.acs-promo-strip--band` or `--ticket` with a motif, `__title` and `__end` (countdown and, outside P15, the `promo.view_all` link).
- Badges (`card_badge`): inside `.acs-pcard__img`, a `.acs-promo-badges` box holding the badge phrase (if any) and then the rule, each an `.acs-promo-badge--tag` or `--seal` (the seal also starts with an `.acs-promo-motif`). Prices: `.acs-prices` with `.acs-price-was` (struck original price) then `.acs-price-sale`, each starting with an `.acs-vh` screen-reader label (`promo.a11y_original`, `promo.a11y_sale`).
- Price ribbon (`detail_ribbon`): `.acs-promo-ribbon--ribbon` or `--panel` with `__prices` (`.acs-price-was`, `.acs-price-sale.acs-promo-ribbon__sale`) and `__meta` (badges). The rules area after the ★ hint is `.acs-promo-rules`: heading with the sale title, `ul` of rule texts, not-combinable notes, then the countdown and the `promo.view_all` link.
- Cart progress (`cart_progress`): `.acs-promo-progress` with either `__track` + `__fill` (`--progress: <percent>`) or `__steps` with ten `span`s (`data-on` on the filled ones), then `__labels`; hints are `.acs-promo-progress__hint` lines. The bar only shows the nearest threshold not yet reached.
- Back office: palette swatches are `.acs-promo-swatch` (inside an `.acs` element, with `data-promo-palette`); the storefront preview bar is `.acs-admin.acs-admin__previewbar` above the previewed page; it sticks to the top while scrolling and cannot be closed.
