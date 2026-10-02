import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CategoryListItem, FilterOption, ProductPage, ProductSummary, Remote } from "../api/catalog";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { ProductListView } from "./ProductListPage";
import type { MoreResults } from "./ProductListPage";
import { DEFAULT_PRODUCT_LIST_QUERY, parseProductListQuery } from "./productListQuery";
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

  // 验收：显示商品列表接口返回的这一页，按返回顺序，页面不自行过滤或排序。
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

  // 验收：请求失败时在列表位置显示 common.error_retry；标题、提示与筛选照常显示。
  it.each(LANGUAGES)("shows the retry message in place of the list in %s", (language) => {
    const html = render({ products: failed, language });
    const main = part(html, /<div class="site-list__main">[\s\S]*$/);
    expect(main).toContain(`role="alert">${COPY["common.error_retry"][language]}</p>`);
    expect(html).toContain(COPY["list.demo_hint"][language]);
    expect(aside(html)).toContain("Apparel");
    expect(html).not.toContain(COPY["list.empty"][language]);
  });

  // 验收：筛选项请求失败时在筛选的位置显示 common.error_retry，商品列表照常。
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

  // 第一页没有可用的上一页、最后一页没有可用的下一页。
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

  // 追加的商品接在当前页之后；追加到最后一页后不再显示 list.load_more。
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

  // 验收：追加失败时在列表位置显示 common.error_retry，已显示的商品保留，可再按 list.load_more。
  it("keeps the shown products and offers load more again when loading more fails", () => {
    const more: MoreResults = { items: [], lastPage: 1, status: "error" };
    const html = render({ products: { status: "ready", data: page(48, 1, [item("a")]) }, more });
    expect(html).toContain(COPY["common.error_retry"].en);
    expect(html).toContain("Name a");
    expect(html).toContain(COPY["list.load_more"].en);
  });
});

describe("product list route", () => {
  // 验收：商品列表在 /products；未取到数据时（服务端渲染不发请求）页头、标题与提示照常，没有错误文案。
  it("renders the product list page inside the frame", () => {
    const html = renderToStaticMarkup(<App initialPath="/products?q=tee&sort=price_asc" storage={storage("zh")} />);
    expect(html).toContain(COPY["list.title"].zh);
    expect(html).toContain(COPY["list.demo_hint"].zh);
    expect(html).toContain(COPY["common.footer_demo"].zh);
    expect(html).not.toContain(COPY["common.error_retry"].zh);
    expect(html).toMatch(/<option value="price_asc" selected="">/);
  });
});
