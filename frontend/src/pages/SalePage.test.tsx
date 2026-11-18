import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CardPromotion, ProductPage, ProductSummary } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse, RuleDisplay } from "../api/promotion";
import App from "../App";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, CopyVars, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PreviewProvider, usePreview } from "../preview";
import type { StorefrontPreview } from "../preview";
import { PromotionProvider } from "../promotion";
import { countdownText } from "../promoText";
import { RouterProvider } from "../router";
import type { MoreResults } from "./ProductListPage";
import SalePage, { createSaleList, fetchSaleProducts, SALE_LIST_START, saleListKey, saleProductsUrl, saleRuleLines } from "./SalePage";
import type { SaleListState } from "./SalePage";

// 有活动的状态以 PromotionProvider 的 initial 传入（读取替身从不返回，时钟替身固定为 0）；
// 商品区的状态以 SalePage 的 initial 传入，目录读取以替身代替（服务端渲染不读取）；读取与翻页的行为由 createSaleList 的测试守住。

afterEach(() => {
  vi.unstubAllGlobals();
});

const HOUR_MS = 3_600_000;
const SERVER_TIME = "2026-11-14T04:00:00Z";
// 服务端时间之后一小时结束：倒计时文字为 countdownText(language, HOUR_MS)。
const ENDS_AT = "2026-11-14T05:00:00Z";

function rule(overrides: Partial<RuleDisplay>): RuleDisplay {
  return {
    rule_id: 1,
    position: 1,
    kind: "item_discount",
    discount_mode: "percent",
    percent: 20,
    amount_sen: null,
    buy_quantity: null,
    get_quantity: null,
    get_percent: null,
    threshold_sen: null,
    tiers: [],
    categories: [],
    has_products: false,
    ...overrides,
  };
}

// 满额两档（position 1）、免运费（position 4）：整单级。
const SPEND = rule({
  rule_id: 31,
  position: 1,
  kind: "spend_discount",
  discount_mode: null,
  percent: null,
  tiers: [
    { threshold_sen: 15000, amount_sen: 1500, percent: null },
    { threshold_sen: 30000, amount_sen: null, percent: 10 },
  ],
});
const SHIPPING = rule({ rule_id: 32, position: 4, kind: "free_shipping", discount_mode: null, percent: null, threshold_sen: 12000 });
// 商品级：两个分类另有单件商品（position 3）、一个分类（position 2）、只有单件商品（position 5）。
const APPAREL = rule({
  rule_id: 11,
  position: 3,
  categories: [
    { slug: "apparel", name: "Apparel" },
    { slug: "bags", name: "Bags" },
  ],
  has_products: true,
});
const HOME = rule({
  rule_id: 12,
  position: 2,
  kind: "buy_x_get_y",
  discount_mode: null,
  percent: null,
  buy_quantity: 2,
  get_quantity: 1,
  get_percent: 100,
  categories: [{ slug: "home", name: "Home" }],
});
const PICKS = rule({ rule_id: 13, position: 5, discount_mode: "amount", percent: null, amount_sen: 500, has_products: true });

function sale(overrides: Partial<PromotionDisplay> = {}): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar New Year Sale",
    subtitle: "20% off apparel, buy 2 get 1 free on home items, and save more as you spend.",
    badge: "Lunar deal",
    strip: "Lunar New Year Sale — strip text",
    pack: "cny",
    palette: "cny",
    ambient: "garland",
    home_poster: "split",
    list_strip: "band",
    card_badge: "tag",
    detail_ribbon: "ribbon",
    cart_progress: "bar",
    poster_mode: "template",
    poster_desktop: null,
    poster_phone: null,
    poster_alt: null,
    ends_at: ENDS_AT,
    coupon_combinable: true,
    points_combinable: true,
    // 接口顺序与 position 不同：页面按 position 排。
    rules: [SPEND, APPAREL, SHIPPING, HOME, PICKS],
    ...overrides,
  };
}

const ORDER_LEVEL_ONLY = sale({ rules: [SHIPPING, SPEND] });

function t(language: Language, key: CopyKey, vars?: CopyVars): string {
  return translate(language, key, vars);
}

// UX P15 的规则顺序：商品级规则按 position、说明后附范围；整单级规则在后、按 position。
function expectedLines(language: Language): string[] {
  const separator = language === "zh" ? "、" : ", ";
  return [
    `${t(language, "promo.rule_bxgy_free", { x: 2, y: 1 })} — ${t(language, "promo.rule_scope", { names: "Home" })}`,
    `${t(language, "promo.rule_item_percent", { percent: 20 })} — ${t(language, "promo.rule_scope_with_items", { names: `Apparel${separator}Bags` })}`,
    t(language, "promo.rule_item_amount", { amount: formatSen(500) }),
    t(language, "promo.rule_tier_amount", { threshold: formatSen(15000), amount: formatSen(1500) }),
    t(language, "promo.rule_tier_percent", { threshold: formatSen(30000), percent: 10 }),
    t(language, "promo.rule_free_shipping", { threshold: formatSen(12000) }),
  ];
}

function item(slug: string, overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    slug,
    name: { text: `Name ${slug}`, english_fallback: false },
    category: { slug: "apparel", name: { text: "Apparel", english_fallback: false } },
    image: null,
    min_price_sen: 1800,
    has_multiple_variants: false,
    sold_out_today: false,
    promotion: null,
    min_sale_price_sen: null,
    ...overrides,
  };
}

function page(total: number, pageNumber: number, items: ProductSummary[]): ProductPage {
  return { total, page: pageNumber, page_size: 24, items };
}

function shown(data: ProductPage, more: MoreResults | null = null): SaleListState {
  return { page: data.page, products: { status: "ready", data }, more };
}

const LISTED = shown(page(30, 1, [item("a"), item("b")]));

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  // undefined 即活动读取中；null 即接口说没有活动。
  display?: PromotionDisplay | null | undefined;
  list?: SaleListState;
  language?: Language;
}

function render({ display, list = LISTED, language = "en" }: RenderOptions): string {
  const initial: PromotionResponse | undefined = display === undefined ? undefined : { promotion: display, server_time: SERVER_TIME };
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath="/sale">
        <PromotionProvider initial={initial} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
          <SalePage fetcher={() => new Promise<ProductPage>(() => undefined)} initial={list} />
        </PromotionProvider>
      </RouterProvider>
    </LanguageProvider>,
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const main = (html: string) => part(html, /<main class="site-sale"[\s\S]*<\/main>/);
const intro = (html: string) => part(html, /<div class="site-sale__intro">[\s\S]*?(?=<div class="site-sale__rules">)/);
const rules = (html: string) => part(html, /<section class="acs-promo-rules">[\s\S]*?<\/section>/);
const products = (html: string) => part(html, /<section class="site-sale__products">[\s\S]*?<\/section>/);
const pagination = (html: string) => part(html, /<div class="site-desktop-only site-list__pages">[\s\S]*?<\/div>/);
const cardLinks = (html: string) => [...html.matchAll(/<a class="acs-pcard[^"]*" href="([^"]*)">/g)].map((m) => m[1]);

function hint(language: Language): string {
  return (
    `<p class="acs-hint"><svg class="acs-hint__mark" viewBox="0 0 24 24" aria-hidden="true">` +
    `<path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3L2.9 9.5l6.3-.9z"></path></svg>` +
    `<span>${escapeHtml(COPY["promo.demo_hint"][language])}</span></p>`
  );
}

const CLOCK_ICON =
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">` +
  `<circle cx="12" cy="13" r="8"></circle><path d="M12 9v4l3 2M9 2h6"></path></svg>`;

function strip(language: Language, title: string, variant = "band"): string {
  return (
    `<div class="acs-promo-strip acs-promo-strip--${variant}"><span class="acs-promo-motif" aria-hidden="true"></span>` +
    `<span class="acs-promo-strip__title">${escapeHtml(title)}</span>` +
    `<span class="acs-promo-strip__end"><span class="acs-promo-count">${CLOCK_ICON}<span>${countdownText(language, HOUR_MS)}</span></span></span></div>`
  );
}

function browseAll(language: Language): string {
  return `<a class="acs-btn acs-btn--primary" href="/products">${escapeHtml(COPY["promo.browse_all"][language])}</a>`;
}

function count(language: Language, total: number): string {
  return escapeHtml(t(language, "list.results_count", { count: total }));
}

describe("sale page states", () => {
  // HANDOFF 0.97 落实写法「读取中只显示 ★ promo.demo_hint」与 UX P15「演示提示：常驻横幅；★ [promo.demo_hint]」：
  // 活动读取完成之前主体只有 ★ 提示（标 aria-busy），没有横条、规则、商品或 promo.ended。
  it.each(LANGUAGES)("shows only the demo hint while the sale is loading in %s", (language) => {
    expect(main(render({ language }))).toBe(`<main class="site-sale" aria-busy="true">${hint(language)}</main>`);
  });

  // UX P15「没有生效活动（活动已结束或尚未开始）：[promo.ended] ( [promo.browse_all] ) → P02」与「直接打开 /sale 而没有生效活动时同样显示 [promo.ended]」：
  // ★ 提示之后是 promo.ended 与去 /products 的 promo.browse_all，没有横条、规则与商品区。
  it.each(LANGUAGES)("shows promo.ended and promo.browse_all without a sale in %s", (language) => {
    expect(main(render({ display: null, language }))).toBe(
      `<main class="site-sale">${hint(language)}<div class="acs-card site-sale__ended">` +
        `<p class="acs-display-s">${escapeHtml(COPY["promo.ended"][language])}</p>${browseAll(language)}</div></main>`,
    );
  });

  // UX P15「访客停留期间活动结束时，倒计时到 0 后页面重新读取，显示 [promo.ended]」：读取结果的活动剩余时间不大于 0 时按没有活动处理
  // （promotion.tsx），页面与没有活动时逐字相同。
  it("shows promo.ended once the sale has run out", () => {
    expect(main(render({ display: sale({ ends_at: SERVER_TIME }) }))).toBe(main(render({ display: null })));
  });

  // UX P15 桌面线框「★ [promo.demo_hint] / 活动横条（list_strip）：<活动标题> [promo.ends_in] ⏱ / <活动副标题> / [promo.rules_title] … / [promo.products_title] [list.results_count] / 商品卡」：
  // 有活动时各部分依次出现。
  it.each(LANGUAGES)("shows the hint, the strip, the subtitle, the rules and the products in order in %s", (language) => {
    const html = main(render({ display: sale(), language }));
    expect(html.startsWith(`<main class="site-sale">${hint(language)}<div class="site-sale__intro">`)).toBe(true);
    const positions = [
      html.indexOf(escapeHtml(COPY["promo.demo_hint"][language])),
      html.indexOf("acs-promo-strip__title"),
      html.indexOf(escapeHtml(sale().subtitle ?? "")),
      html.indexOf(escapeHtml(COPY["promo.rules_title"][language])),
      html.indexOf("<ul>"),
      html.indexOf(escapeHtml(COPY["promo.products_title"][language])),
      html.indexOf(count(language, 30)),
      html.indexOf(`<a class="acs-pcard"`),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(html).not.toContain("site-sale__ended");
    expect(html).not.toContain("site-sale__note");
  });

  // UX P15「活动横条（list_strip）：<活动标题>   [promo.ends_in] ⏱」与 COMPONENTS「countdown and, outside P15, the promo.view_all link」：
  // 横条文字是活动标题（不是横条文字），末尾只有倒计时，没有 promo.view_all；副标题在横条之下。
  it.each(LANGUAGES)("shows the sale title in the strip without promo.view_all in %s", (language) => {
    const html = render({ display: sale(), language });
    expect(intro(html)).toBe(
      `<div class="site-sale__intro">${strip(language, "Lunar New Year Sale")}<p class="acs-body-l">${escapeHtml(sale().subtitle ?? "")}</p></div>`,
    );
    expect(html).not.toContain(escapeHtml(COPY["promo.view_all"][language]));
    expect(html).not.toContain("strip text");
  });

  // UX 0.20「节日促销活动」：「list_strip 活动横条（P02、P15）……每位的变体由视觉稿给出」——P15 的横条同样按活动的 list_strip。
  it("uses the ticket strip variant", () => {
    expect(intro(render({ display: sale({ list_strip: "ticket", subtitle: null }) }))).toBe(
      `<div class="site-sale__intro">${strip("en", "Lunar New Year Sale", "ticket")}</div>`,
    );
  });

  // UX P15 桌面线框「活动横条（list_strip）：<活动标题>   [promo.ends_in] ⏱ / <活动副标题>」（副标题是横条之下的一行活动数据）
  // 与 HANDOFF 0.97 落实写法「副标题（为空不显示）」：为 null 或空串时横条之后没有副标题这一行。
  it.each([null, ""])("leaves out an empty subtitle (%j)", (subtitle) => {
    expect(intro(render({ display: sale({ subtitle }) }))).toBe(`<div class="site-sale__intro">${strip("en", "Lunar New Year Sale")}</div>`);
  });

  // UX P15「[promo.rules_title] · <每条规则的说明>（商品级规则在前，按规则排序；整单级规则在后）」与说明「商品级规则含分类时，说明后附 [promo.rule_scope]……
  // 同时含单件商品时改用 [promo.rule_scope_with_items]；只含单件商品时不附范围」：接口顺序打乱时也按 position，两种范围文案与不附范围各一条。
  it.each(LANGUAGES)("lists the rules item rules first with their scopes in %s", (language) => {
    const lines = expectedLines(language);
    expect(saleRuleLines(language, sale())).toEqual(lines);
    expect(rules(render({ display: sale(), language }))).toBe(
      `<section class="acs-promo-rules"><h2 class="acs-display-s">${escapeHtml(COPY["promo.rules_title"][language])}</h2>` +
        `<ul>${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul></section>`,
    );
  });

  // UX P15「· [promo.coupon_not_combinable]?  [promo.points_not_combinable]?」：活动关闭了同用时在说明行之后分别显示。
  it.each(LANGUAGES)("adds the not-combinable notes after the rules in %s", (language) => {
    const html = rules(render({ display: sale({ coupon_combinable: false, points_combinable: false }), language }));
    expect(html.endsWith(
      `</ul><p class="acs-body-s">${escapeHtml(COPY["promo.coupon_not_combinable"][language])}</p>` +
        `<p class="acs-body-s">${escapeHtml(COPY["promo.points_not_combinable"][language])}</p></section>`,
    )).toBe(true);
  });

  // UX P15「只有整单级规则时：商品区替换为 [promo.order_level_only] ( [promo.browse_all] ) → P02」：规则区照常（整单级规则按 position），
  // 之后是 promo.order_level_only 与去 /products 的 promo.browse_all，没有 promo.products_title、结果数与商品卡。
  it.each(LANGUAGES)("replaces the products with promo.order_level_only for order-level rules only in %s", (language) => {
    const html = main(render({ display: ORDER_LEVEL_ONLY, language }));
    expect(saleRuleLines(language, ORDER_LEVEL_ONLY)).toEqual(expectedLines(language).slice(3));
    expect(html.endsWith(
      `</section></div><div class="acs-panel site-sale__note"><p class="acs-body-l">${escapeHtml(COPY["promo.order_level_only"][language])}</p>` +
        `${browseAll(language)}</div></main>`,
    )).toBe(true);
    expect(html).not.toContain(escapeHtml(COPY["promo.products_title"][language]));
    expect(html).not.toContain("site-sale__products");
    expect(html).not.toContain("acs-pcard");
  });
});

describe("sale page products", () => {
  // UX P15「[promo.products_title] [list.results_count] / [图] <名称> …（商品卡同 P02）」与「去向：P03（商品卡）」：
  // 结果数是接口给的总数，商品卡按返回顺序、链到各自的详情页；命中活动规则的卡片显示角标与活动价（「1. 全局框架」商品卡规则）。
  it.each(LANGUAGES)("shows the title, the server count and the product cards in %s", (language) => {
    const hit: CardPromotion = {
      rule_id: APPAREL.rule_id,
      kind: "item_discount",
      discount_mode: "percent",
      percent: 20,
      amount_sen: null,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
    };
    const list = shown(page(30, 1, [item("a", { promotion: hit, min_sale_price_sen: 1440 }), item("b")]));
    const html = products(render({ display: sale(), list, language }));
    expect(html.startsWith(
      `<section class="site-sale__products"><div class="site-sale__products-head"><h2 class="acs-display-m">${escapeHtml(COPY["promo.products_title"][language])}</h2>` +
        `<span class="acs-muted">${count(language, 30)}</span></div><div class="acs-pgrid site-list__grid">`,
    )).toBe(true);
    expect(cardLinks(html)).toEqual(["/products/a", "/products/b"]);
    expect(html.match(/acs-price-sale/g)).toHaveLength(1);
    expect(html).toContain(`<span class="acs-promo-badge acs-promo-badge--tag"><span>Lunar deal</span></span>`);
  });

  // UX P15「商品区列出生效活动全部商品级规则范围内的上架商品，排序与分页同 P02 的默认排序（不设筛选与排序控件）」与桌面线框
  // 「[promo.products_title]  [list.results_count]」，以及 HANDOFF 0.97 落实写法「商品级规则范围内没有上架商品（目录返回 0 件）时
  // 显示 list.results_count 与 list.empty（不显示 list.filter_clear）」：范围内没有上架商品时结果数为 0、显示 list.empty，没有清除筛选。
  it.each(LANGUAGES)("shows the count and list.empty without list.filter_clear when no item is listed in %s", (language) => {
    const html = render({ display: sale(), list: shown(page(0, 1, [])), language });
    expect(products(html)).toBe(
      `<section class="site-sale__products"><div class="site-sale__products-head"><h2 class="acs-display-m">${escapeHtml(COPY["promo.products_title"][language])}</h2>` +
        `<span class="acs-muted">${count(language, 0)}</span></div>` +
        `<div class="site-list__empty"><p class="acs-display-s">${escapeHtml(COPY["list.empty"][language])}</p></div></section>`,
    );
    expect(html).not.toContain(escapeHtml(COPY["list.filter_clear"][language]));
    expect(html).not.toContain(escapeHtml(COPY["list.load_more"][language]));
  });

  // UX P15 桌面线框「[图] <名称> …（商品卡同 P02）」与说明「排序与分页同 P02 的默认排序」，照 P02「接口返回之前」：
  // 商品读取中时网格为空并标 aria-busy，还没有结果数。
  it("shows a busy grid while the products load", () => {
    const html = products(render({ display: sale(), list: SALE_LIST_START }));
    expect(html).toContain(`<div class="acs-pgrid site-list__grid" aria-busy="true"></div>`);
    expect(html).not.toContain("acs-muted");
  });

  // UX P15「排序与分页同 P02 的默认排序」与桌面线框「[promo.products_title] … (‹) 1 2 3 (›)」，以及 HANDOFF 0.97 落实写法
  // 「商品读取失败时照 P02：首次读取与桌面翻页失败显示 ErrorNotice」：商品区的标题之后是 common.error_retry，
  // 没有结果数、商品卡与页码；★ 提示、横条与规则区照常。
  it.each(LANGUAGES)("shows the retry message in place of the products in %s", (language) => {
    for (const pageNumber of [1, 2]) {
      const html = render({ display: sale(), list: { page: pageNumber, products: { status: "error" }, more: null }, language });
      expect(products(html)).toBe(
        `<section class="site-sale__products"><div class="site-sale__products-head"><h2 class="acs-display-m">${escapeHtml(COPY["promo.products_title"][language])}</h2></div>` +
          `<p class="acs-alert acs-alert--danger site-notice" role="alert">${escapeHtml(COPY["common.error_retry"][language])}</p></section>`,
      );
      expect(html).toContain(escapeHtml(COPY["promo.demo_hint"][language]));
      expect(html).toContain(escapeHtml(COPY["promo.rules_title"][language]));
    }
  });

  // HANDOFF 0.97 落实写法「页码只放在页面状态里、不进地址……桌面的分页控件为按钮……样式与显隐照 P02」与 UX P15 桌面「(‹) 1 2 3 (›)」：
  // 页码是按钮（没有 href），当前页标 aria-current，上一页、下一页的读屏标签同 P02；页面里没有带页码的地址。
  it.each(LANGUAGES)("pages with buttons that carry no address in %s", (language) => {
    const html = render({ display: sale(), list: shown(page(60, 2, [item("a")])), language });
    const pages = pagination(html);
    expect(pages).not.toContain("href");
    expect(pages.match(/<button /g)).toHaveLength(5);
    const labels = [...pages.matchAll(/<button ([^>]*)>([\s\S]*?)<\/button>/g)].map(
      (m) => /aria-label="([^"]*)"/.exec(m[1] ?? "")?.[1] ?? m[2],
    );
    expect(labels).toEqual([
      COPY["common.a11y_page_prev"][language],
      "1",
      "2",
      "3",
      COPY["common.a11y_page_next"][language],
    ]);
    expect(pages).toContain(`<button class="acs-btn acs-btn--primary acs-btn--sm" type="button" aria-current="page">2</button>`);
    expect(html).not.toMatch(/page=/);
    expect(html).not.toContain(`href="/sale?`);
  });

  // 同一条 UX P15「(‹) 1 2 3 (›)」、「排序与分页同 P02 的默认排序」与 HANDOFF「样式与显隐照 P02」：
  // 第一页没有可用的上一页、最后一页没有可用的下一页；只有一页时没有页码。
  it("disables previous on the first page and next on the last, and has no pages for a single page", () => {
    const first = pagination(render({ display: sale(), list: shown(page(60, 1, [item("a")])) }));
    expect(first).toMatch(/^<div class="site-desktop-only site-list__pages"><span class="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true">/);
    expect(first).not.toContain(COPY["common.a11y_page_prev"].en);
    const last = pagination(render({ display: sale(), list: shown(page(60, 3, [item("a")])) }));
    expect(last).toMatch(/<span class="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true"><svg[^>]*><path d="M9 6l6 6-6 6"><\/path><\/svg><\/span><\/div>$/);
    expect(last).not.toContain(COPY["common.a11y_page_next"].en);
    expect(render({ display: sale(), list: shown(page(24, 1, [item("a")])) })).not.toContain("site-list__pages");
  });

  // UX P15 手机「( [list.load_more] )」与 HANDOFF 0.97「手机为 list.load_more，样式与显隐照 P02」：还有下一页时为手机才显示的按钮（没有 href），
  // 追加的商品接在当前页之后；到最后一页后不再显示。
  it.each(LANGUAGES)("loads more on phones with a button in %s", (language) => {
    const button = `<button class="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-list__more" type="button" aria-busy="false">${escapeHtml(COPY["list.load_more"][language])}</button>`;
    expect(products(render({ display: sale(), list: shown(page(60, 1, [item("a")])), language }))).toContain(button);
    const more = render({ display: sale(), list: shown(page(60, 1, [item("a")]), { items: [item("c")], lastPage: 2, status: "idle" }), language });
    expect(cardLinks(more)).toEqual(["/products/a", "/products/c"]);
    expect(products(more)).toContain(button);
    const done = render({ display: sale(), list: shown(page(60, 1, [item("a")]), { items: [item("c"), item("d")], lastPage: 3, status: "idle" }), language });
    expect(done).not.toContain(escapeHtml(COPY["list.load_more"][language]));
  });

  // UX P15 手机「( [list.load_more] )」与说明「排序与分页同 P02 的默认排序」，以及 HANDOFF 0.97「手机加载更多失败照 P02 加载更多的失败状态」：
  // 已显示的商品保留，手机显示 common.error_retry，可再按 list.load_more。
  it.each(LANGUAGES)("keeps the shown products and offers load more again when loading more fails in %s", (language) => {
    const html = products(render({ display: sale(), list: shown(page(60, 1, [item("a")]), { items: [], lastPage: 1, status: "error" }), language }));
    expect(cardLinks(html)).toEqual(["/products/a"]);
    expect(html).toContain(
      `<div class="site-phone-only site-list__more-error"><p class="acs-alert acs-alert--danger site-notice" role="alert">${escapeHtml(COPY["common.error_retry"][language])}</p></div>`,
    );
    expect(html).toContain(`>${escapeHtml(COPY["list.load_more"][language])}</button>`);
  });

  // UX 页面地图「P15 | 活动专题页 | /sale」：/sale 在前台框架里渲染，服务端渲染时活动还没读到，主体只有 ★ 提示。
  it.each(LANGUAGES)("renders the sale page for /sale inside the site frame in %s", (language) => {
    const html = renderToStaticMarkup(<App initialPath="/sale" storage={storage(language)} />);
    expect(html).toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(main(html)).toBe(`<main class="site-sale" aria-busy="true">${hint(language)}</main>`);
  });
});

interface FetchCall {
  url: string;
  signal: AbortSignal;
  resolve: (value: ProductPage) => void;
  reject: (reason: unknown) => void;
}

// 目录读取的替身：每次读取记下地址与中止信号，由测试决定何时、以什么结果完成。
function setup(language: Language = "en", initial: SaleListState | null = null) {
  const calls: FetchCall[] = [];
  const fetcher = vi.fn(
    (url: string, signal: AbortSignal) =>
      new Promise<ProductPage>((resolve, reject) => {
        calls.push({ url, signal, resolve, reject });
      }),
  );
  const list = createSaleList(fetcher, language, initial);
  const call = (index: number): FetchCall => {
    const found = calls[index];
    if (found === undefined) {
      throw new Error(`no request ${String(index)}`);
    }
    return found;
  };
  return { list, calls, call, fetcher };
}

// 让已完成的读取把结果交给读取对象。
async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("sale list", () => {
  // UX P15「商品区列出生效活动全部商品级规则范围内的上架商品，排序与分页同 P02 的默认排序」与 HANDOFF 0.97「以 SHOP-TASK-262 的 sale 参数读取目录，
  // 默认排序与每页条数同 P02」：第一次读取请求第 1 页，带 sale=true 与 sort=newest，不带 page_size（同 P02），语言随页面。
  it.each(LANGUAGES)("reads the first page of sale items with the default sort in %s", (language) => {
    const { list, calls, call } = setup(language);
    list.start();
    expect(calls).toHaveLength(1);
    expect(call(0).url).toBe(`/api/catalog/products?lang=${language}&sort=newest&page=1&sale=true`);
    expect(saleProductsUrl(language, 1)).toBe(call(0).url);
    expect(list.current()).toEqual(SALE_LIST_START);
  });

  // 同一条 UX P15「商品区列出生效活动全部商品级规则范围内的上架商品」：第一页到达后显示它；
  // 商品区从给定的已列出状态开始（测试与预览）时照样列出它，不再读取。
  it("shows the first page once it arrives", async () => {
    const { list, call } = setup();
    list.start();
    const data = page(30, 1, [item("a")]);
    call(0).resolve(data);
    await settle();
    expect(list.current()).toEqual(shown(data));

    const given = setup("en", LISTED);
    given.list.start();
    expect(given.fetcher).not.toHaveBeenCalled();
    expect(given.list.current()).toBe(LISTED);
  });

  // UX P15「排序与分页同 P02 的默认排序（不设筛选与排序控件）」、桌面「(‹) 1 2 3 (›)」与手机「( [list.load_more] )」，以及
  // HANDOFF 0.97「页码只放在页面状态里、不进地址（路由的查询参数只给商品列表用）」：桌面翻页与手机加载更多只读目录、改读取对象里的状态，
  // 不碰地址栏与历史记录；翻到第 2 页替换显示的页，加载更多追加第 3 页。
  it("pages and loads more without touching the address", async () => {
    const pushState = vi.fn();
    const replaceState = vi.fn();
    const location = { pathname: "/sale", search: "" };
    vi.stubGlobal("window", { location, history: { pushState, replaceState }, scrollTo: vi.fn() });
    const { list, call } = setup();
    list.start();
    call(0).resolve(page(60, 1, [item("a")]));
    await settle();

    list.showPage(2);
    expect(list.current()).toEqual({ page: 2, products: { status: "loading" }, more: null });
    expect(call(1).url).toBe(saleProductsUrl("en", 2));
    const second = page(60, 2, [item("b")]);
    call(1).resolve(second);
    await settle();
    expect(list.current()).toEqual(shown(second));

    list.loadMore();
    expect(list.current().more).toEqual({ items: [], lastPage: 2, status: "loading" });
    expect(call(2).url).toBe(saleProductsUrl("en", 3));
    call(2).resolve(page(60, 3, [item("c")]));
    await settle();
    expect(list.current()).toEqual(shown(second, { items: [item("c")], lastPage: 3, status: "idle" }));

    expect(pushState).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalled();
    expect(location).toEqual({ pathname: "/sale", search: "" });
  });

  // UX P15「排序与分页同 P02 的默认排序」与桌面「(‹) 1 2 3 (›)」，以及 HANDOFF 0.97「商品读取失败时照 P02：首次读取与桌面翻页失败显示 ErrorNotice」：
  // 两种失败都使商品区为 error，翻页失败时记着所在的页。
  it("fails the first read and a desktop page", async () => {
    const { list, call } = setup();
    list.start();
    call(0).reject(new TypeError("Failed to fetch"));
    await settle();
    expect(list.current()).toEqual({ page: 1, products: { status: "error" }, more: null });

    const again = setup();
    again.list.start();
    again.call(0).resolve(page(60, 1, [item("a")]));
    await settle();
    again.list.showPage(3);
    again.call(1).reject(new Error("503"));
    await settle();
    expect(again.list.current()).toEqual({ page: 3, products: { status: "error" }, more: null });
  });

  // UX P15 手机「( [list.load_more] )」与说明「排序与分页同 P02 的默认排序」，以及 HANDOFF 0.97「手机加载更多失败照 P02 加载更多的失败状态」：
  // 失败时已显示与已追加的商品保留，再按一次重新请求同一页。
  it("keeps the items when loading more fails and retries the same page", async () => {
    const { list, call } = setup();
    list.start();
    const first = page(80, 1, [item("a")]);
    call(0).resolve(first);
    await settle();
    list.loadMore();
    call(1).resolve(page(80, 2, [item("b")]));
    await settle();
    list.loadMore();
    call(2).reject(new Error("503"));
    await settle();
    expect(list.current()).toEqual(shown(first, { items: [item("b")], lastPage: 2, status: "error" }));
    list.loadMore();
    expect(call(3).url).toBe(saleProductsUrl("en", 3));
  });

  // UX「1. 全局框架」「节日促销活动」「到 0 时页面重新读取活动数据并去掉全部活动内容」与 P15「倒计时到 0 后页面重新读取，显示 [promo.ended]」：
  // 活动结束或离开页面时商品区停止，未完成的读取被中止，之后到达的结果不改状态、不再显示活动商品（验收「离开页面时中止未完成的读取」）。
  it("aborts the pending read when stopped", async () => {
    const { list, call } = setup();
    const stop = list.start();
    stop();
    expect(call(0).signal.aborted).toBe(true);
    call(0).resolve(page(30, 1, [item("a")]));
    await settle();
    expect(list.current()).toEqual(SALE_LIST_START);
  });

  // UX P15「排序与分页同 P02 的默认排序」与桌面「(‹) 1 2 3 (›)」：显示的是最后选的那一页；
  // 换页时中止上一页未完成的读取，它的结果不再显示（验收「商品区随页码与加载更多读取」）。
  it("drops a page that is still loading when another page is asked for", async () => {
    const { list, call } = setup();
    list.start();
    list.showPage(2);
    expect(call(0).signal.aborted).toBe(true);
    call(0).resolve(page(30, 1, [item("a")]));
    await settle();
    expect(list.current()).toEqual({ page: 2, products: { status: "loading" }, more: null });
  });

  // UX P15「商品区列出生效活动全部商品级规则范围内的上架商品，排序与分页同 P02 的默认排序」与「1. 全局框架」「节日促销活动」
  // 「到 0 时页面重新读取活动数据」：重新读取后活动换了，商品区列的是新活动的商品、从第 1 页起；切换语言时以新语言从第 1 页读取。
  // HANDOFF 0.97「generation 变化且活动换了（promotion_id 不同或变为没有活动）时、以及切换语言时，回到第 1 页重新读取商品」：
  // 商品区以 saleListKey 为身份，活动 ID 或语言变了即换一个新的商品区，从第 1 页、以新语言读取；同一活动重新读取（其余字段变了）时身份不变。
  // 变为没有活动时商品区不渲染（见上方 promo.ended 的测试），活动回来时同样是新的商品区。
  it("starts again from page 1 when the sale changes or the language switches", () => {
    const key = saleListKey("en", sale());
    expect(saleListKey("en", sale({ title: "Lunar New Year Sale, extended", ends_at: "2026-11-15T05:00:00Z" }))).toBe(key);
    expect(saleListKey("en", sale({ promotion_id: 8 }))).not.toBe(key);
    expect(saleListKey("zh", sale())).not.toBe(key);
    const { list, call } = setup("zh");
    list.start();
    expect(list.current()).toEqual(SALE_LIST_START);
    expect(call(0).url).toBe(saleProductsUrl("zh", 1));
  });
});

const PREVIEW: StorefrontPreview = { promotionId: 7, csrfToken: "csrf-token" };

// 渲染时取出 usePreview() 的结果（服务端渲染不发请求，读取由测试以 fetch 替身发出）。
function PreviewProbe({ onPreview }: { onPreview: (preview: StorefrontPreview | null) => void }) {
  onPreview(usePreview());
  return null;
}

function previewOf(inside: boolean): StorefrontPreview | null {
  const seen: (StorefrontPreview | null)[] = [];
  const probe = (
    <PreviewProbe
      onPreview={(preview) => {
        seen.push(preview);
      }}
    />
  );
  renderToStaticMarkup(inside ? <PreviewProvider preview={PREVIEW}>{probe}</PreviewProvider> : probe);
  expect(seen).toHaveLength(1);
  return seen[0] ?? null;
}

function stubOk() {
  const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => Promise.resolve(new Response(JSON.stringify(page(30, 1, [])), { status: 200 })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("sale products in a storefront preview", () => {
  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：在 PreviewProvider 里，专题页商品区读目录走后台预览接口，
  // sale=true、排序与页码原样保留，凭同源 cookie、不缓存，并带上商品区的中止信号。
  it.each(LANGUAGES)("reads the sale products from the preview endpoint inside the preview in %s", async (language) => {
    const fetchMock = stubOk();
    const controller = new AbortController();
    await expect(fetchSaleProducts(previewOf(true))(saleProductsUrl(language, 2), controller.signal)).resolves.toEqual(page(30, 1, []));
    expect(fetchMock.mock.calls).toEqual([
      [
        `/api/admin/promotions/7/preview/products?lang=${language}&sort=newest&page=2&sale=true`,
        { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal },
      ],
    ]);
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」只说预览：PreviewProvider 之外商品区照旧读公开接口，地址与选项与之前逐字相同。
  it("reads the sale products from the public endpoint outside the preview", async () => {
    const fetchMock = stubOk();
    const controller = new AbortController();
    expect(previewOf(false)).toBeNull();
    await fetchSaleProducts(previewOf(false))(saleProductsUrl("en", 1), controller.signal);
    expect(fetchMock.mock.calls).toEqual([
      [
        "/api/catalog/products?lang=en&sort=newest&page=1&sale=true",
        { method: "GET", credentials: "omit", headers: { Accept: "application/json" }, signal: controller.signal },
      ],
    ]);
  });

  // UX 0.20 A12「前台预览」「未开始的活动按它正在生效的样子显示」：预览只改取数路径，同样的活动与商品在 PreviewProvider 里显示得与之外逐字相同。
  it("renders the sale page the same inside the preview", () => {
    const outside = render({ display: sale() });
    const inside = renderToStaticMarkup(
      <PreviewProvider preview={PREVIEW}>
        <LanguageProvider storage={storage("en")}>
          <RouterProvider initialPath="/sale">
            <PromotionProvider initial={{ promotion: sale(), server_time: SERVER_TIME }} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
              <SalePage fetcher={() => new Promise<ProductPage>(() => undefined)} initial={LISTED} />
            </PromotionProvider>
          </RouterProvider>
        </LanguageProvider>
      </PreviewProvider>,
    );
    expect(inside).toBe(outside);
  });
});
