import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PreviewPromotionRead } from "../api/adminPromotionPreview";
import type { AdminPromotion, AdminPromotionRule, PromotionRead, PromotionState as AdminState } from "../api/adminPromotions";
import type { ProductPage, ProductSummary } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse } from "../api/promotion";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import type { StorefrontPreview } from "../preview";
import { RouterProvider } from "../router";
import type { RouteLocation, RoutePath } from "../router";
import {
  AdminPromotionPreviewView,
  currentTab,
  exitPath,
  hasItemRules,
  loadPreview,
  LOADING,
  pickDetailProduct,
  PREVIEW_START,
  previewLocation,
  previewTarget,
} from "./AdminPromotionPreviewPage";
import type { PreviewApi, PreviewState, PreviewTab } from "./AdminPromotionPreviewPage";

// 两次读取（readAdminPromotion 与 readPreviewPromotion）与详情页签取商品都以替身给出；页面以 react-dom/server 渲染给定的状态
// （服务端渲染不跑 effect，被预览的前台页面停在各自的读取中）。

afterEach(() => {
  vi.unstubAllGlobals();
});

const PREVIEW: StorefrontPreview = { promotionId: 7, csrfToken: "csrf-7" };

function adminRule(kind: string): AdminPromotionRule {
  return {
    rule_id: 1,
    kind,
    discount_mode: kind === "item_discount" ? "percent" : null,
    percent: kind === "item_discount" ? 20 : null,
    amount_sen: null,
    buy_quantity: kind === "buy_x_get_y" ? 2 : null,
    get_quantity: kind === "buy_x_get_y" ? 1 : null,
    get_percent: kind === "buy_x_get_y" ? 100 : null,
    threshold_sen: kind === "free_shipping" ? 12000 : null,
    product_ids: [],
    category_ids: [],
    tiers: [],
    products: [],
  };
}

function adminPromotion(state: AdminState, kinds: string[] = ["item_discount"]): AdminPromotion {
  return {
    id: 7,
    version: 3,
    state,
    ended_early_at: null,
    name: "CNY 2027",
    starts_at: "2027-02-01T00:00",
    ends_at: "2027-02-15T23:59",
    coupon_combinable: true,
    points_combinable: true,
    rules: kinds.map(adminRule),
    decoration: {
      pack: "cny",
      palette: "cny",
      ambient: "garland",
      home_poster: "split",
      list_strip: "band",
      card_badge: "tag",
      detail_ribbon: "ribbon",
      cart_progress: "bar",
      poster_mode: "template",
    },
    copy: {
      title_en: "Lunar New Year Sale",
      title_zh: null,
      title_ms: null,
      subtitle_en: null,
      subtitle_zh: null,
      subtitle_ms: null,
      badge_en: null,
      badge_zh: null,
      badge_ms: null,
      strip_en: null,
      strip_zh: null,
      strip_ms: null,
      poster_alt_en: null,
      poster_alt_zh: null,
      poster_alt_ms: null,
    },
    poster_desktop_url: null,
    poster_phone_url: null,
  };
}

function display(): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar New Year Sale",
    subtitle: null,
    badge: "Lunar deal",
    strip: null,
    pack: "cny",
    palette: "cny",
    ambient: "corners",
    home_poster: "split",
    list_strip: "band",
    card_badge: "tag",
    detail_ribbon: "ribbon",
    cart_progress: "bar",
    poster_mode: "template",
    poster_desktop: null,
    poster_phone: null,
    poster_alt: null,
    ends_at: "2027-02-15T15:59:00Z",
    coupon_combinable: true,
    points_combinable: true,
    rules: [],
  };
}

const RESPONSE: PromotionResponse = { promotion: display(), server_time: "2027-01-20T04:00:00Z" };
const READY: PreviewState = { status: "ready", preview: PREVIEW, response: RESPONSE, itemRules: true };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  state: PreviewState;
  location?: RouteLocation;
  picking?: boolean;
  exit?: RoutePath;
  language?: Language;
}

function render({ state, location = PREVIEW_START, picking = false, exit = "/admin/promotions/7", language = "en" }: RenderOptions): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath="/admin/promotions/7/preview">
        <AdminPromotionPreviewView
          mode="light"
          state={state}
          location={location}
          picking={picking}
          exit={exit}
          onTab={() => undefined}
          onNavigate={() => undefined}
        />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function text(language: Language, key: CopyKey): string {
  return escapeHtml(COPY[key][language]);
}

const TABS: readonly [PreviewTab, CopyKey][] = [
  ["home", "admin.preview_home"],
  ["list", "admin.preview_list"],
  ["detail", "admin.preview_detail"],
  ["cart", "admin.preview_cart"],
  ["sale", "admin.preview_sale"],
];

// 预览条的完整标记：promo.preview_bar、五个页签按钮（当前页签 aria-current）与回到 exit 的 promo.preview_exit。
function bar(language: Language, current: PreviewTab | null, disabled: boolean, exit: RoutePath = "/admin/promotions/7"): string {
  const tabs = TABS.map(
    ([tab, key]) =>
      `<button type="button"${tab === current ? ` aria-current="page"` : ""}${disabled ? ` disabled=""` : ""}>${text(language, key)}</button>`,
  ).join("");
  return (
    `<div class="acs-admin acs-admin__previewbar" data-mode="light"><strong>${text(language, "promo.preview_bar")}</strong>` +
    `<nav aria-label="${text(language, "admin.preview")}">${tabs}</nav>` +
    `<a class="acs-admin__btn acs-admin__btn--secondary" href="${exit}">${text(language, "promo.preview_exit")}</a></div>`
  );
}

describe("preview bar", () => {
  // UX 0.20 A12「前台预览」「预览条在最上方、所有前台内容之前，不可关闭」与线框「| [promo.preview_bar] ( [promo.preview_exit] ) |」
  // 「| [admin.preview_home] | [admin.preview_list] | [admin.preview_detail] | [admin.preview_cart] | [admin.preview_sale] |」：
  // 读取中三种语言都先是预览条（没有关闭控件，只有回到编辑区的链接），五个页签禁用、没有当前页签；之下只有标 aria-busy 的空提示区，没有前台页面。
  it.each(LANGUAGES)("shows the bar with disabled tabs while loading in %s", (language) => {
    expect(render({ state: LOADING, language })).toBe(
      `<div class="site-promo-preview">${bar(language, null, true)}` +
        `<div class="acs-admin site-promo-preview__status" data-mode="light" aria-busy="true"></div></div>`,
    );
  });

  // 同一条「预览条在最上方、所有前台内容之前」与「<前台页面，含演示横幅、页头与页脚，按该活动正在生效时的样子显示>」：读到后三种语言都是
  // 预览条在前、被预览的前台（.acs 根元素，含 common.demo_banner、页头与页脚）在后；页签可点，首页为当前页签；活动装饰已加上（生效的样子）。
  it.each(LANGUAGES)("puts the bar before the previewed storefront in %s", (language) => {
    const html = render({ state: READY, language });
    expect(html.startsWith(`<div class="site-promo-preview">${bar(language, "home", false)}<div class="acs site"`)).toBe(true);
    expect(html).toContain(text(language, "common.demo_banner"));
    expect(html).toContain(`<header class="acs-header">`);
    expect(html).toContain(`<footer class="acs-footer site-footer">`);
    expect(html).toContain(`class="site__promo acs-promo" data-promo-pack="cny"`);
    expect(html).not.toContain("site-promo-preview__status");
  });

  // UX 0.20 A12「前台预览」「预览中点击商品、分类等链接仍留在预览里」的取数一侧（[admin.preview_detail] 取商品期间）：页签都禁用，前台照旧显示。
  it("disables the tabs while the detail tab is picking a product", () => {
    const html = render({ state: READY, picking: true });
    expect(html).toContain(bar("en", "home", true));
    expect(html).toContain(`<main class="site-home">`);
  });
});

describe("previewed pages", () => {
  // UX 0.20 A12「前台预览」线框的五个页签与「<前台页面，含演示横幅、页头与页脚，按该活动正在生效时的样子显示>」：五种位置各显示对应的
  // 前台页面（首页、商品列表、商品详情、购物车与专题页），对应页签为当前页签；商品列表保留查询串（页头搜索框预先填入搜索词）。
  it.each([
    [{ path: "/", search: "" }, "home", `<main class="site-home">`],
    [{ path: "/products", search: "?q=tee" }, "list", `<main class="site-list">`],
    [{ path: "/products/crew-neck-tee", search: "" }, "detail", `<main class="site-detail" aria-busy="true">`],
    [{ path: "/cart", search: "" }, "cart", `<main class="site-cart">`],
    [{ path: "/sale", search: "" }, "sale", `<main class="site-sale">`],
  ] as const)("shows the storefront page for %j", (location, tab, main) => {
    const html = render({ state: READY, location });
    expect(html).toContain(bar("en", tab, false));
    expect(html).toContain(main);
    expect(currentTab(location)).toBe(tab);
  });

  // UX 0.20 A12「前台预览」「预览中点击商品、分类等链接仍留在预览里」：从页头搜索或分类进入的商品列表位置保留查询串，
  // 页头搜索框预先填入搜索词，与前台一样。
  it("keeps the search words of the product list", () => {
    expect(render({ state: READY, location: { path: "/products", search: "?q=tee" } })).toContain(`value="tee"`);
  });

  // UX 0.20 A12「前台预览」「预览中点击商品、分类等链接仍留在预览里」：商品、分类（商品列表带查询串）、购物车与专题页的链接在预览里换位置；
  // 其余路径（页头的会员与查单、页脚的隐私说明、结账等）不在预览里，点击后留在原处（previewLocation 为 null，页面不换位置）。
  it("stays put for paths outside the five storefront pages", () => {
    expect(previewLocation({ path: "/products", search: "?category=bags&page=2" })).toEqual({ path: "/products", search: "?category=bags&page=2" });
    expect(previewLocation({ path: "/products/a%20b", search: "" })).toEqual({ path: "/products/a%20b", search: "" });
    expect(previewLocation({ path: "/", search: "" })).toEqual({ path: "/", search: "" });
    expect(previewLocation({ path: "/cart", search: "" })).toEqual({ path: "/cart", search: "" });
    expect(previewLocation({ path: "/sale", search: "" })).toEqual({ path: "/sale", search: "" });
    for (const path of ["/login", "/account", "/track", "/track/order", "/privacy", "/checkout", "/pay", "/register", "/admin/promotions/7"] as const) {
      expect(previewLocation({ path, search: "" }), path).toBeNull();
      expect(currentTab({ path, search: "" }), path).toBeNull();
    }
  });
});

function item(slug: string): ProductSummary {
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
  };
}

function productPage(slugs: string[]): ProductPage {
  return { total: slugs.length, page: 1, page_size: 24, items: slugs.map(item) };
}

const SALE_URL = "/api/catalog/products?lang=en&sort=newest&page=1&sale=true";
const NEWEST_URL = "/api/catalog/products?lang=en&sort=newest&page=1";

// 商品列表替身：按地址给出结果（没有给出的地址算取数失败），记下每次的地址与预览上下文。
function fakeProducts(pages: Readonly<Record<string, ProductPage>>) {
  const urls: string[] = [];
  const previews: StorefrontPreview[] = [];
  const fetcher = (url: string, _signal: AbortSignal, preview: StorefrontPreview) => {
    urls.push(url);
    previews.push(preview);
    const found = pages[url];
    return found === undefined ? Promise.reject(new Error("failed")) : Promise.resolve(found);
  };
  return { fetcher, urls, previews };
}

describe("detail tab", () => {
  // UX 0.20 A12「前台预览」「[admin.preview_detail] 打开活动范围内的第一件商品」：有商品级规则（打折或买 X 享 Y）时取预览商品列表
  // sale=true 的第一件，不再取最新列表；取数都带预览上下文。
  it("opens the first sale product when the sale has item rules", async () => {
    const { fetcher, urls, previews } = fakeProducts({ [SALE_URL]: productPage(["sale-a", "sale-b"]), [NEWEST_URL]: productPage(["new-a"]) });
    const signal = new AbortController().signal;
    expect(hasItemRules(adminPromotion("draft", ["free_shipping", "buy_x_get_y"]))).toBe(true);
    expect(await pickDetailProduct(true, "en", signal, PREVIEW, fetcher)).toBe("sale-a");
    expect(urls).toEqual([SALE_URL]);
    expect(previews).toEqual([PREVIEW]);
  });

  // 同一条「（没有商品级规则时打开最新上架的商品）」：只有整单级规则时直接取按最新排序的第一件；活动范围内没有商品（sale=true 列表为空）时同样改取最新的第一件。
  it("opens the newest product without item rules or sale products", async () => {
    expect(hasItemRules(adminPromotion("draft", ["free_shipping", "spend_discount"]))).toBe(false);
    const signal = new AbortController().signal;
    const orderOnly = fakeProducts({ [NEWEST_URL]: productPage(["new-a", "new-b"]) });
    expect(await pickDetailProduct(false, "en", signal, PREVIEW, orderOnly.fetcher)).toBe("new-a");
    expect(orderOnly.urls).toEqual([NEWEST_URL]);
    const emptySale = fakeProducts({ [SALE_URL]: productPage([]), [NEWEST_URL]: productPage(["new-a"]) });
    expect(await pickDetailProduct(true, "en", signal, PREVIEW, emptySale.fetcher)).toBe("new-a");
    expect(emptySale.urls).toEqual([SALE_URL, NEWEST_URL]);
  });

  // 同一条的另一面：都没有商品或取数失败时没有可打开的商品（页面不换位置、不提示）。
  it("opens nothing when there is no product or the fetch fails", async () => {
    const signal = new AbortController().signal;
    const none = fakeProducts({ [SALE_URL]: productPage([]), [NEWEST_URL]: productPage([]) });
    expect(await pickDetailProduct(true, "en", signal, PREVIEW, none.fetcher)).toBeNull();
    const failed = fakeProducts({});
    expect(await pickDetailProduct(true, "en", signal, PREVIEW, failed.fetcher)).toBeNull();
    expect(failed.urls).toEqual([SALE_URL]);
    expect(await pickDetailProduct(false, "en", signal, PREVIEW, failed.fetcher)).toBeNull();
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：不传替身时经 fetchCatalog 带预览上下文，地址换成后台预览接口、带同源 cookie。
  it("reads the products through the admin preview endpoint", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(new Response(JSON.stringify(productPage(["sale-a"])), { status: 200 }));
    });
    expect(await pickDetailProduct(true, "zh", new AbortController().signal, PREVIEW)).toBe("sale-a");
    expect(calls.map((call) => call.url)).toEqual(["/api/admin/promotions/7/preview/products?lang=zh&sort=newest&page=1&sale=true"]);
    expect(calls[0]?.init.credentials).toBe("same-origin");
  });
});

const OK_PREVIEW: PreviewPromotionRead = { kind: "ok", response: RESPONSE };

function okRead(state: AdminState, kinds?: string[]): PromotionRead {
  return { kind: "ok", promotion: adminPromotion(state, kinds), csrf_token: "csrf-7" };
}

// 两次读取的替身：记下每次调用；页面的处理记下显示的状态与 replace 的去向。
function fakeApi(one: PromotionRead, preview: PreviewPromotionRead = OK_PREVIEW) {
  const calls: string[] = [];
  const api: PreviewApi = {
    readOne: (promotionId) => {
      calls.push(`one ${String(promotionId)}`);
      return Promise.resolve(one);
    },
    readPreview: (promotionId, language) => {
      calls.push(`preview ${String(promotionId)} ${language}`);
      return Promise.resolve(preview);
    },
  };
  return { api, calls };
}

function fakeMoves() {
  const shown: PreviewState[] = [];
  const replaced: RoutePath[] = [];
  const moves = {
    show: (state: PreviewState) => {
      shown.push(state);
    },
    replace: (path: RoutePath) => {
      replaced.push(path);
    },
  };
  return { moves, shown, replaced };
}

describe("opening the preview", () => {
  // UX 0.20 A12「前台预览」「草稿与已发布未开始的活动可预览」与「预览凭管理员会话经后台接口取数」：先读活动（取 CSRF 令牌与状态），
  // 再按当前语言读一次预览活动；读到后为前台预览，预览上下文带活动 ID 与 CSRF 令牌，记下是否有商品级规则。
  it.each(["draft", "scheduled"] as const)("opens the preview of a %s promotion", async (state) => {
    const { api, calls } = fakeApi(okRead(state, ["spend_discount"]));
    const { moves, shown, replaced } = fakeMoves();
    await loadPreview(7, "ms", new AbortController().signal, moves, api);
    expect(calls).toEqual(["one 7", "preview 7 ms"]);
    expect(shown).toEqual([{ status: "ready", preview: PREVIEW, response: RESPONSE, itemRules: false }]);
    expect(replaced).toEqual([]);
  });

  // 同一条「草稿与已发布未开始的活动可预览」的另一面：进行中与已结束不读预览，只显示 common.error_retry（预览条照旧，见上方读取中的写法）。
  it.each(["running", "ended"] as const)("does not preview a %s promotion", async (state) => {
    const { api, calls } = fakeApi(okRead(state));
    const { moves, shown } = fakeMoves();
    await loadPreview(7, "en", new AbortController().signal, moves, api);
    expect(calls).toEqual(["one 7"]);
    expect(shown).toEqual([{ status: "gone", error: "common.error_retry" }]);
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：任一次读取没有会话（401）时以 replace 回到 A01，不显示提示。
  it("goes back to the admin login without a session", async () => {
    const first = fakeApi({ kind: "none" });
    const firstMoves = fakeMoves();
    await loadPreview(7, "en", new AbortController().signal, firstMoves.moves, first.api);
    expect(first.calls).toEqual(["one 7"]);
    expect(firstMoves.replaced).toEqual(["/admin/login"]);
    expect(firstMoves.shown).toEqual([]);
    const second = fakeApi(okRead("draft"), { kind: "none" });
    const secondMoves = fakeMoves();
    await loadPreview(7, "en", new AbortController().signal, secondMoves.moves, second.api);
    expect(secondMoves.replaced).toEqual(["/admin/login"]);
    expect(secondMoves.shown).toEqual([]);
  });

  // UX 0.20 A12「前台预览」「前台预览的唯一禁用条件是：已保存的内容里没有开始时间或结束时间，或结束时间已过——这时 … 显示
  // [admin.promo_preview_needs_time]」：预览接口答不可预览时显示这句；不存在、其他失败与网络中断分别为 common.error_retry 与 common.network_check。
  it.each([
    [{ kind: "unavailable" }, "admin.promo_preview_needs_time"],
    [{ kind: "missing" }, "common.error_retry"],
    [{ kind: "failed" }, "common.error_retry"],
    [{ kind: "network" }, "common.network_check"],
  ] as const)("shows the notice for the preview read %j", async (read, error) => {
    const { api } = fakeApi(okRead("scheduled"), read);
    const { moves, shown } = fakeMoves();
    await loadPreview(7, "en", new AbortController().signal, moves, api);
    expect(shown).toEqual([{ status: "gone", error }]);
  });

  // 同一组提示的第一次读取：活动不存在与其他失败为 common.error_retry，网络中断为 common.network_check，都不再读预览；
  // 段值不合法（不是正整数）时按不存在处理，一个请求也不发。
  it.each([
    [{ kind: "missing" }, "common.error_retry"],
    [{ kind: "failed" }, "common.error_retry"],
    [{ kind: "network" }, "common.network_check"],
  ] as const)("shows the notice for the promotion read %j", async (read, error) => {
    const { api, calls } = fakeApi(read);
    const { moves, shown } = fakeMoves();
    await loadPreview(7, "en", new AbortController().signal, moves, api);
    expect(calls).toEqual(["one 7"]);
    expect(shown).toEqual([{ status: "gone", error }]);
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：A12 原句没有写段值不合法的情形，这里守住的是同一组失败提示的写法——
  // 段值不是正整数时不经后台接口取数（一个请求也不发），按活动不存在显示 common.error_retry（预览条照旧）。
  it("sends no request for an invalid segment", async () => {
    for (const segment of ["abc", "0", "007", "-1", "2147483648", "1.5"]) {
      expect(previewTarget("/admin/promotions/:id/preview", { id: segment }), segment).toBeNull();
    }
    expect(previewTarget("/admin/promotions/:id/preview", { id: "7" })).toBe(7);
    const { api, calls } = fakeApi(okRead("draft"));
    const { moves, shown } = fakeMoves();
    await loadPreview(null, "en", new AbortController().signal, moves, api);
    expect(calls).toEqual([]);
    expect(shown).toEqual([{ status: "gone", error: "common.error_retry" }]);
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：A12 原句没有写离开页面的情形，这里守住的是这两次读取的收尾——
  // 离开页面（中止）之后不再更新页面，也不以 replace 换位置。
  it("stops after the page is left", async () => {
    const controller = new AbortController();
    const { api } = fakeApi(okRead("draft"));
    const { moves, shown, replaced } = fakeMoves();
    const pending = loadPreview(7, "en", controller.signal, moves, api);
    controller.abort();
    await pending;
    expect(shown).toEqual([]);
    expect(replaced).toEqual([]);
  });

  // UX 0.20 A12「前台预览」「预览条在最上方、所有前台内容之前，不可关闭」：失败与不可预览时仍先是预览条（页签禁用），之下只有提示，没有前台页面。
  it.each(LANGUAGES)("keeps the bar above the notices in %s", (language) => {
    for (const error of ["admin.promo_preview_needs_time", "common.error_retry", "common.network_check"] as const) {
      expect(render({ state: { status: "gone", error }, language })).toBe(
        `<div class="site-promo-preview">${bar(language, null, true)}` +
          `<div class="acs-admin site-promo-preview__status" data-mode="light" aria-busy="false">` +
          `<div class="acs-admin__alert" role="alert"><span>${text(language, error)}</span></div></div></div>`,
      );
    }
  });
});

describe("closing the preview", () => {
  // UX 0.20 A12「前台预览」线框「( [promo.preview_exit] )」：退出为链接，段值合法时回到该活动的编辑区，否则回到活动列表。
  it("leads back to the editor or the promotion list", () => {
    expect(exitPath(7)).toBe("/admin/promotions/7");
    expect(exitPath(null)).toBe("/admin/promotions");
    expect(render({ state: LOADING, exit: exitPath(null) })).toContain(bar("en", null, true, "/admin/promotions"));
    expect(render({ state: READY, exit: exitPath(7) })).toContain(
      `<a class="acs-admin__btn acs-admin__btn--secondary" href="/admin/promotions/7">${text("en", "promo.preview_exit")}</a>`,
    );
  });
});
