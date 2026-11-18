import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { productsUrl } from "../api/catalog";
import type { CategoryListItem, FilterOption, ProductPage, ProductSummary, Remote } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse } from "../api/promotion";
import App from "../App";
import { saleLinkPath } from "../components/PromoParts";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PreviewProvider, usePreview } from "../preview";
import type { StorefrontPreview } from "../preview";
import { PromotionProvider } from "../promotion";
import { countdownText } from "../promoText";
import { RouterProvider } from "../router";
import { fetchMoreProducts, ProductListView } from "./ProductListPage";
import type { MoreResults } from "./ProductListPage";
import { DEFAULT_PRODUCT_LIST_QUERY, parseProductListQuery, toProductListRequest } from "./productListQuery";
import type { ProductListQuery } from "./productListQuery";

function item(slug: string, overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    slug,
    name: { text: `Name ${slug}`, english_fallback: false },
    category: { slug: "bags", name: { text: "Bags", english_fallback: false } },
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

const categoryList: CategoryListItem[] = [
  { slug: "apparel", name: { text: "Apparel", english_fallback: false }, image: null },
  { slug: "bags", name: { text: "Bags", english_fallback: false }, image: null },
];

const optionList: FilterOption[] = [
  {
    code: "colour",
    name: { text: "Colour", english_fallback: false },
    values: [
      { code: "black", name: { text: "Black", english_fallback: false } },
      { code: "white", name: { text: "White", english_fallback: false } },
    ],
  },
];

const loading: Remote<never> = { status: "loading" };
const failed: Remote<never> = { status: "error" };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  query?: ProductListQuery;
  products?: Remote<ProductPage>;
  categories?: Remote<CategoryListItem[]>;
  options?: Remote<FilterOption[]>;
  more?: MoreResults | null;
  language?: Language;
}

function render({
  query = DEFAULT_PRODUCT_LIST_QUERY,
  products = loading,
  categories = { status: "ready", data: categoryList },
  options = { status: "ready", data: optionList },
  more = null,
  language = "en",
}: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath="/products">
        <ProductListView
          query={query}
          products={products}
          categories={categories}
          options={options}
          more={more}
          onQueryChange={() => undefined}
          onLoadMore={() => undefined}
        />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const aside = (html: string) => part(html, /<aside[\s\S]*?<\/aside>/);
const drawer = (html: string) => part(html, /<div[^>]*role="dialog"[\s\S]*$/);
const pagination = (html: string) => part(html, /<div class="site-desktop-only site-list__pages">[\s\S]*?<\/div>/);

describe("product list page", () => {
  // UX P02：标题 list.title 与 ★ list.demo_hint。
  it.each(LANGUAGES)("shows the title and the demo hint in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(`<h1 class="acs-display-l">${COPY["list.title"][language]}</h1>`);
    expect(html).toContain(COPY["list.demo_hint"][language]);
    expect(html.indexOf(COPY["list.title"][language])).toBeLessThan(html.indexOf(COPY["list.demo_hint"][language]));
  });

  // UX P02：结果数 list.results_count 用接口给的总数，不是当前页的件数。
  it.each(LANGUAGES)("shows the result count from the server in %s", (language) => {
    const html = render({ products: { status: "ready", data: page(30, 1, [item("a"), item("b")]) }, language });
    expect(html).toContain(COPY["list.results_count"][language].replace("{count}", "30"));
  });

  // UX P02：排序三项，当前排序来自网址。
  it("offers the three sort orders and selects the current one", () => {
    const html = render({ query: { ...DEFAULT_PRODUCT_LIST_QUERY, sort: "price_desc" } });
    const select = part(html, /<select[\s\S]*?<\/select>/);
    expect(select).toContain(`aria-label="${COPY["list.sort"].en}"`);
    expect([...select.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]])).toEqual([
      ["newest", COPY["list.sort_newest"].en],
      ["price_asc", COPY["list.sort_price_asc"].en],
      ["price_desc", COPY["list.sort_price_desc"].en],
    ]);
    expect(select).toMatch(/<option value="price_desc" selected="">/);
  });

  // SHOP-TASK-015 验收第 5 条「前端不自行过滤或排序」：显示商品列表接口返回的这一页，按返回顺序。
  it("shows the products in the order returned", () => {
    const items = [item("c", { min_price_sen: 9900 }), item("a", { min_price_sen: 100 }), item("b")];
    const html = render({
      query: { ...DEFAULT_PRODUCT_LIST_QUERY, sort: "price_asc", categories: ["apparel"] },
      products: { status: "ready", data: page(3, 1, items) },
    });
    expect([...html.matchAll(/<span class="acs-pcard__name">([^<]*)<\/span>/g)].map((m) => m[1])).toEqual([
      "Name c",
      "Name a",
      "Name b",
    ]);
  });

  // UX P02「去向：P03」与 SHOP-TASK-017 验收第 2 条「本任务起商品卡…按路由规则链到详情页」：每张商品卡链到它的详情页。
  it("links each product card to its detail page", () => {
    const html = render({ products: { status: "ready", data: page(2, 1, [item("a"), item("b", { sold_out_today: true })]) } });
    expect([...html.matchAll(/<a class="acs-pcard[^"]*" href="([^"]*)">/g)].map((m) => m[1])).toEqual(["/products/a", "/products/b"]);
  });

  // UX P02「演示提示」：售罄显示 list.out_of_stock。
  it("labels products sold out today", () => {
    const html = render({ products: { status: "ready", data: page(2, 1, [item("a", { sold_out_today: true }), item("b")]) } });
    expect(html.match(new RegExp(COPY["list.out_of_stock"].en, "g"))).toHaveLength(1);
  });

  // UX P02：空结果显示 list.empty 与 list.filter_clear。
  it.each(LANGUAGES)("shows the empty result message with clear filters in %s", (language) => {
    const html = render({
      query: { ...DEFAULT_PRODUCT_LIST_QUERY, q: "nothing" },
      products: { status: "ready", data: page(0, 1, []) },
      language,
    });
    const empty = part(html, /<div class="site-list__empty">[\s\S]*?<\/div>/);
    expect(empty).toContain(COPY["list.empty"][language]);
    expect(empty).toContain(`type="button">${COPY["list.filter_clear"][language]}</button>`);
    expect(html).not.toContain("acs-pcard");
    expect(html).not.toContain(COPY["list.load_more"][language]);
  });

  // SHOP-TASK-015 验收第 7 条「请求失败时在…列表位置显示 common.error_retry」：标题、提示与筛选照常显示。
  it.each(LANGUAGES)("shows the retry message in place of the list in %s", (language) => {
    const html = render({ products: failed, language });
    const main = part(html, /<div class="site-list__main">[\s\S]*$/);
    expect(main).toContain(`role="alert">${COPY["common.error_retry"][language]}</p>`);
    expect(html).toContain(COPY["list.demo_hint"][language]);
    expect(aside(html)).toContain("Apparel");
    expect(html).not.toContain(COPY["list.empty"][language]);
  });

  // SHOP-TASK-015 验收第 7 条「请求失败时在对应区块…显示 common.error_retry」：筛选项失败时显示在筛选的位置，商品列表照常。
  it("shows the retry message in the filters when filter options fail", () => {
    const html = render({ categories: failed, products: { status: "ready", data: page(1, 1, [item("a")]) } });
    expect(aside(html)).toContain(COPY["common.error_retry"].en);
    expect(aside(html)).toContain("Colour");
    expect(html).toContain("Name a");
  });
});

describe("desktop filters and pages", () => {
  // UX P02：侧栏 list.filter_title、list.filter_category 下的分类与各规格名下的值，勾选状态来自网址。
  it("lists categories and option values with the chosen ones checked", () => {
    const html = aside(
      render({ query: { ...DEFAULT_PRODUCT_LIST_QUERY, categories: ["bags"], options: ["colour:white"] } }),
    );
    expect(html).toContain(`<h2 class="acs-display-s">${COPY["list.filter_title"].en}</h2>`);
    expect(html).toContain(`<legend class="acs-field__label">${COPY["list.filter_category"].en}</legend>`);
    expect(html).toContain(`<legend class="acs-field__label">Colour</legend>`);
    const checked = [...html.matchAll(/<input type="checkbox"( checked="")?\/><span>([^<]*)<\/span>/g)].map((m) => [
      m[2],
      m[1] !== undefined,
    ]);
    expect(checked).toEqual([
      ["Apparel", false],
      ["Bags", true],
      ["Black", false],
      ["White", true],
    ]);
    expect(html).toContain(`type="button">${COPY["list.filter_clear"].en}</button>`);
  });

  // UX P02：桌面页码翻页，上一页、下一页的读屏标签为 common.a11y_page_prev、common.a11y_page_next；页码是带查询参数的链接。
  it.each(LANGUAGES)("links the previous and next pages in %s", (language) => {
    const query = parseProductListQuery("?category=bags&page=2");
    const html = pagination(render({ query, products: { status: "ready", data: page(60, 2, [item("a")]) }, language }));
    expect(html).toContain(`aria-label="${COPY["common.a11y_page_prev"][language]}"`);
    expect(html).toContain(`aria-label="${COPY["common.a11y_page_next"][language]}"`);
    expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]?.replace(/&amp;/g, "&"))).toEqual([
      "/products?category=bags",
      "/products?category=bags",
      "/products?category=bags&page=2",
      "/products?category=bags&page=3",
      "/products?category=bags&page=3",
    ]);
    expect(html).toMatch(/aria-current="page" href="\/products\?category=bags&amp;page=2">2<\/a>/);
  });

  // 派生实现约束（实现选择）：守住 UX P02 桌面页码翻页「(‹) 1 2 3 (›)」与 SHOP-TASK-015 验收第 6 条「上一页、下一页读屏标签用 common.a11y_page_prev、common.a11y_page_next」——第一页不给可用的上一页，最后一页不给可用的下一页。
  it("disables previous on the first page and next on the last", () => {
    const first = pagination(render({ products: { status: "ready", data: page(50, 1, [item("a")]) } }));
    expect(first).not.toContain(COPY["common.a11y_page_prev"].en);
    expect(first).toContain(COPY["common.a11y_page_next"].en);
    const last = pagination(
      render({ query: { ...DEFAULT_PRODUCT_LIST_QUERY, page: 3 }, products: { status: "ready", data: page(50, 3, [item("a")]) } }),
    );
    expect(last).toContain(COPY["common.a11y_page_prev"].en);
    expect(last).not.toContain(COPY["common.a11y_page_next"].en);
  });

  // 派生实现约束（实现选择）：守住 UX P02 桌面页码翻页「(‹) 1 2 3 (›)」与 SHOP-TASK-015 验收第 6 条「桌面用…页码翻页」——只有一页时无页可翻，不渲染页码。
  it("has no pages when everything fits on one page", () => {
    const html = render({ products: { status: "ready", data: page(24, 1, [item("a")]) } });
    expect(html).not.toContain("site-list__pages");
  });
});

describe("phone filter drawer and load more", () => {
  // UX P02 手机：(list.filter_title) 打开全屏筛选抽屉，抽屉里有 list.filter_clear 与 list.filter_apply；抽屉默认关着。
  it.each(LANGUAGES)("has a closed full-screen drawer with clear and apply in %s", (language) => {
    const html = render({ language });
    const panel = drawer(html);
    expect(panel).toMatch(/^<div id="[^"]+" class="acs site-phone-only site-drawer" role="dialog" aria-modal="true"[^>]* hidden="">/);
    expect(panel).toContain(`>${COPY["list.filter_title"][language]}</h2>`);
    expect(panel).toContain(`type="button">${COPY["list.filter_clear"][language]}</button>`);
    expect(panel).toContain(`type="button">${COPY["list.filter_apply"][language]}</button>`);
    expect(html).toMatch(
      new RegExp(`<button class="[^"]*site-phone-only site-list__filter-button"[^>]*aria-expanded="false"[^>]*>${COPY["list.filter_title"][language]}</button>`),
    );
  });

  // UX P02 手机：list.load_more 追加下一页；只在还有下一页时出现。
  it.each(LANGUAGES)("offers load more while there are more pages in %s", (language) => {
    const html = render({ products: { status: "ready", data: page(30, 1, [item("a")]) }, language });
    expect(html).toMatch(
      new RegExp(`<button class="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-list__more"[^>]*>${COPY["list.load_more"][language]}</button>`),
    );
    const done = render({ products: { status: "ready", data: page(30, 2, [item("a")]) }, query: { ...DEFAULT_PRODUCT_LIST_QUERY, page: 2 } });
    expect(done).not.toContain(COPY["list.load_more"].en);
  });

  // SHOP-TASK-015 验收第 6 条「list.load_more 追加下一页」（UX P02 手机）：追加的商品接在当前页之后。
  // 派生实现约束（实现选择）：守住同一条「追加下一页」——到最后一页后没有下一页可追加，不再显示 list.load_more。
  it("appends loaded pages after the current page", () => {
    const more: MoreResults = { items: [item("c"), item("d")], lastPage: 2, status: "idle" };
    const html = render({ products: { status: "ready", data: page(48, 1, [item("a"), item("b")]) }, more });
    expect([...html.matchAll(/<span class="acs-pcard__name">([^<]*)<\/span>/g)].map((m) => m[1])).toEqual([
      "Name a",
      "Name b",
      "Name c",
      "Name d",
    ]);
    expect(html).not.toContain(COPY["list.load_more"].en);
  });

  // SHOP-TASK-015 验收第 6 条「list.load_more 追加下一页」与第 7 条「在…列表位置显示 common.error_retry」：追加失败时已显示的商品保留，可再按 list.load_more。
  it("keeps the shown products and offers load more again when loading more fails", () => {
    const more: MoreResults = { items: [], lastPage: 1, status: "error" };
    const html = render({ products: { status: "ready", data: page(48, 1, [item("a")]) }, more });
    expect(html).toContain(COPY["common.error_retry"].en);
    expect(html).toContain("Name a");
    expect(html).toContain(COPY["list.load_more"].en);
  });
});

describe("product list route", () => {
  // SHOP-TASK-015 验收第 5 条「商品列表 P02 路径为 /products」：未取到数据时（服务端渲染不发请求）页头、标题与提示照常，没有错误文案。
  it("renders the product list page inside the frame", () => {
    const html = renderToStaticMarkup(<App initialPath="/products?q=tee&sort=price_asc" storage={storage("zh")} />);
    expect(html).toContain(COPY["list.title"].zh);
    expect(html).toContain(COPY["list.demo_hint"].zh);
    expect(html).toContain(COPY["common.footer_demo"].zh);
    expect(html).not.toContain(COPY["common.error_retry"].zh);
    expect(html).toMatch(/<option value="price_asc" selected="">/);
  });
});

const HOUR_MS = 3_600_000;
const SERVER_TIME = "2026-11-14T04:00:00Z";
// 服务端时间之后一小时结束：时钟替身固定为 0，倒计时文字为 countdownText(language, HOUR_MS)。
const ENDS_AT = "2026-11-14T05:00:00Z";

function sale(overrides: Partial<PromotionDisplay> = {}): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar New Year Sale",
    subtitle: null,
    badge: null,
    strip: "Lunar New Year Sale — 20% off apparel and more",
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
    rules: [],
    ...overrides,
  };
}

// 有活动的状态以 PromotionProvider 的 initial 传入（null 即接口说没有活动）；读取替身从不返回。
function renderPromo(display: PromotionDisplay | null, options: RenderOptions = {}): string {
  const initial: PromotionResponse = { promotion: display, server_time: SERVER_TIME };
  return renderInside(options, (view) => (
    <PromotionProvider initial={initial} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
      {view}
    </PromotionProvider>
  ));
}

function renderInside(
  {
    query = DEFAULT_PRODUCT_LIST_QUERY,
    products = loading,
    categories = { status: "ready", data: categoryList },
    options = { status: "ready", data: optionList },
    more = null,
    language = "en",
  }: RenderOptions,
  wrap: (view: ReactNode) => ReactNode,
): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath="/products">
        {wrap(
          <ProductListView
            query={query}
            products={products}
            categories={categories}
            options={options}
            more={more}
            onQueryChange={() => undefined}
            onLoadMore={() => undefined}
          />,
        )}
      </RouterProvider>
    </LanguageProvider>,
  );
}

const STRIP = /<div class="site-list__strip"><div class="acs-promo-strip[\s\S]*?<\/div><\/div>/;

function strip(html: string): string {
  return STRIP.exec(html)?.[0] ?? "";
}

// promo.view_all 链接：P15 在路由表里时才有（按 saleLinkPath() 的返回值，不写死）。
function viewAll(language: Language): string {
  const path = saleLinkPath();
  return path === null ? "" : `<a href="${path}">${COPY["promo.view_all"][language]}</a>`;
}

const listed: Remote<ProductPage> = { status: "ready", data: page(30, 1, [item("a"), item("b")]) };

describe("product list promotion strip", () => {
  // UX 0.20 P02：「标题下显示活动横条（横条文字、倒计时、去 P15）」「活动横条：<横条文字> [promo.ends_in] ⏱ ( [promo.view_all] ) → P15」——
  // 位置照 P02-promo-desktop 与 P02-promo-phone：标题与 ★ 提示之后、筛选与商品之前；三种语言下文字、倒计时与 promo.view_all（P15 不在路由表时不放）。
  it.each(LANGUAGES)("shows the strip below the title in %s", (language) => {
    const html = renderPromo(sale(), { products: listed, language });
    const found = strip(html);
    expect(found).toContain(`<span class="acs-promo-strip__title">Lunar New Year Sale — 20% off apparel and more</span>`);
    expect(found).toContain(`<span>${countdownText(language, HOUR_MS)}</span></span>${viewAll(language)}</span></div></div>`);
    const at = html.indexOf(found);
    expect(html.indexOf(COPY["list.demo_hint"][language])).toBeLessThan(at);
    expect(html.indexOf(`<div class="site-list__body">`)).toBe(at + found.length);
  });

  // UX 0.20 P02：「活动横条按 `list_strip` 的变体显示」——band 与 ticket。
  it.each(["band", "ticket"] as const)("uses the %s strip variant", (variant) => {
    expect(strip(renderPromo(sale({ list_strip: variant })))).toMatch(
      new RegExp(`^<div class="site-list__strip"><div class="acs-promo-strip acs-promo-strip--${variant}"><span class="acs-promo-motif" aria-hidden="true"></span>`),
    );
  });

  // UX 0.20 P02：「文字为活动的横条文字（为空时用活动标题）」。
  it.each([null, ""])("uses the promotion title when the strip text is empty (%j)", (text) => {
    expect(strip(renderPromo(sale({ strip: text })))).toContain(`<span class="acs-promo-strip__title">Lunar New Year Sale</span>`);
  });

  // UX 0.20 P02 说明「列表的价格排序仍按原价」与验收「『价格』排序与筛选不变」——
  // 有活动时除横条之外（排序选项、筛选、结果数与商品）与没有活动时逐字相同。
  it("keeps sorting and filters unchanged", () => {
    for (const query of [DEFAULT_PRODUCT_LIST_QUERY, parseProductListQuery("?sort=price_asc&category=bags")]) {
      const withSale = renderPromo(sale(), { query, products: listed });
      expect(strip(withSale)).not.toBe("");
      expect(withSale.replace(STRIP, "")).toBe(renderPromo(null, { query, products: listed }));
    }
  });

  // UX 0.20「节日促销活动」：「没有生效活动时各页与 0.19 完全相同」——接口说没有活动与读取中时与没有活动功能时逐字相同。
  it("renders no promotion content without a promotion or while loading", () => {
    const without = render({ products: listed });
    expect(renderPromo(null, { products: listed })).toBe(without);
    const whileLoading = renderInside({ products: listed }, (view) => (
      <PromotionProvider fetcher={() => new Promise<PromotionResponse>(() => undefined)}>{view}</PromotionProvider>
    ));
    expect(whileLoading).toBe(without);
    expect(without).not.toContain("acs-promo");
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
  const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => Promise.resolve(new Response(JSON.stringify(page(30, 2, [])), { status: 200 })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("product list load more in a storefront preview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const query = parseProductListQuery("?q=tee&category=bags&sort=price_asc");

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：在 PreviewProvider 里，list.load_more 读下一页走后台预览接口，
  // 条件与页码原样保留，凭同源 cookie、不缓存。
  it("loads more from the preview endpoint inside the preview", async () => {
    const fetchMock = stubOk();
    await expect(fetchMoreProducts("ms", query, 2, previewOf(true))).resolves.toEqual(page(30, 2, []));
    expect(fetchMock.mock.calls).toEqual([
      [
        "/api/admin/promotions/7/preview/products?lang=ms&q=tee&category=bags&sort=price_asc&page=2",
        { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: null },
      ],
    ]);
  });

  // 同一句只说预览：PreviewProvider 之外 list.load_more 照旧读公开接口，地址与选项与之前逐字相同。
  it("loads more from the public endpoint outside the preview", async () => {
    const fetchMock = stubOk();
    expect(previewOf(false)).toBeNull();
    await fetchMoreProducts("ms", query, 2, previewOf(false));
    expect(fetchMock.mock.calls).toEqual([
      [productsUrl("ms", toProductListRequest(query, 2)), { method: "GET", credentials: "omit", headers: { Accept: "application/json" }, signal: null }],
    ]);
  });

  // UX 0.20 A12「前台预览」「<前台页面……按该活动正在生效时的样子显示>」：预览只改取数路径，同样的数据在 PreviewProvider 里显示得与之外逐字相同。
  it("renders the list the same inside the preview", () => {
    const options: RenderOptions = { products: listed, more: { items: [item("c")], lastPage: 2, status: "idle" } };
    expect(renderInside(options, (view) => <PreviewProvider preview={PREVIEW}>{view}</PreviewProvider>)).toBe(render(options));
  });
});
