import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import type { AdminProduct, AdminProductListItem, ProductsRead } from "../api/adminProducts";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PRODUCTS_PATH, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminProductsView,
  categoryName,
  closeEditing,
  localName,
  LOADING,
  openProduct,
  openProducts,
  productName,
  productStep,
  readStep,
  settleEditing,
  startEditing,
} from "./AdminProductsPage";
import type { Editing, ProductsMoves, ProductsState } from "./AdminProductsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PRODUCTS_PATH = "/admin/products";
const LOGIN = "/admin/login";
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  PRODUCTS_PATH,
  "/admin/categories",
  "/admin/coupons",
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_products",
  "admin.nav_categories",
  "admin.nav_coupons",
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

// 三语都有的商品与分类、只有英文（中文为 null、马来文为空串）的商品与分类、名称全缺的商品与分类。
const TEE: AdminProductListItem = {
  id: 3,
  slug: "crew-neck-tee",
  name_en: "Crew Neck Tee",
  name_zh: "圆领T恤",
  name_ms: "Baju Leher Bulat",
  is_active: true,
  category_id: 1,
  category_name_en: "Apparel",
  category_name_zh: "服饰",
  category_name_ms: "Pakaian",
};
const TOTE: AdminProductListItem = {
  id: 5,
  slug: "linen-tote-bag",
  name_en: "Linen Tote Bag",
  name_zh: null,
  name_ms: "",
  is_active: false,
  category_id: 2,
  category_name_en: "Bags",
  category_name_zh: null,
  category_name_ms: "",
};
const NAMELESS: AdminProductListItem = {
  id: 7,
  slug: "product-7",
  name_en: null,
  name_zh: null,
  name_ms: null,
  is_active: false,
  category_id: 9,
  category_name_en: "",
  category_name_zh: null,
  category_name_ms: null,
};
// 接口给的顺序（不按 ID）。
const LISTED = [TOTE, TEE, NAMELESS];

const APPAREL: AdminCategory = { id: 1, slug: "apparel", name_en: "Apparel", name_zh: "服饰", name_ms: "Pakaian", is_active: true, version: 3, visible_products: 1 };
const BAGS: AdminCategory = { id: 2, slug: "bags", name_en: "Bags", name_zh: null, name_ms: "", is_active: true, version: 1, visible_products: 0 };

function product(id: number, name: string): AdminProduct {
  return {
    id,
    slug: `product-${String(id)}`,
    version: 2,
    category_id: 1,
    name_en: name,
    name_zh: null,
    name_ms: null,
    description_en: "A soft cotton tee.",
    description_zh: null,
    description_ms: null,
    is_active: true,
    max_per_order: 10,
    options: [],
    variants: [{ id: 11, sku: `sku-${String(id)}`, price_sen: 3900, daily_initial_stock: 20, available_stock: 18, is_active: true, option_value_ids: [] }],
    images: [],
  };
}
const TEE_PRODUCT = product(3, "Crew Neck Tee");
const TOTE_PRODUCT = product(5, "Linen Tote Bag");

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={PRODUCTS_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={PRODUCTS_PATH} storage={storage(language)} />);
}

function ready(products: AdminProductListItem[] = LISTED, editing: Editing | null = null): ProductsState {
  return { status: "ready", products, categories: [APPAREL, BAGS], csrfToken: "csrf-1", editing };
}

function view(state: ProductsState): ReactNode {
  return <AdminProductsView state={state} onEdit={() => undefined} onBack={() => undefined} />;
}

function render(state: ProductsState, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(view(state), language));
}

// 已登录（会话 200）时的后台框架：内容区为本页的 View。
function renderSignedIn(language: Language, state: ProductsState): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="products"
        state={SIGNED_IN}
        busy={false}
        menuOpen={false}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        {view(state)}
      </AdminFrameView>,
      language,
    ),
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const labels = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...labels];
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/);
}

// 第一个 class 含 className 的 name 元素（含其内容）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`no <${name}> with class ${className}`);
  }
  const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  re.lastIndex = start;
  let depth = 0;
  for (let m = re.exec(html); m !== null; m = re.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed <${name}>`);
}

function hasClass(html: string, className: string): boolean {
  return [...html.matchAll(/\sclass="([^"]*)"/g)].some((m) => (m[1] ?? "").split(/\s+/).includes(className));
}

// 根元素（第一个 div）的类名。
function rootClasses(html: string): string[] {
  return classes(tags(html, "div")[0] ?? "");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// 由测试决定何时回答的请求。
function deferred(): { promise: Promise<Response>; resolve: (response: Response) => void } {
  let settle: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((done) => {
    settle = done;
  });
  return {
    promise,
    resolve: (response) => {
      settle(response);
    },
  };
}

interface Call {
  url: string;
  init: RequestInit;
}

type Reply = Response | Promise<Response> | "network";

// fetch 替身：按 URL 各自按顺序给出回答；"network" 表示网络中断（fetch 以 TypeError 拒绝）。多出的请求以错误拒绝。
function stubFetch(replies: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  const queues = new Map(Object.entries(replies).map(([url, list]) => [url, [...list]]));
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const reply = queues.get(url)?.shift();
      if (reply === undefined) {
        return Promise.reject(new Error(`unexpected request ${url}`));
      }
      return reply === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return calls;
}

const LIST_OK = () => json(200, { products: LISTED, csrf_token: "csrf-1" });
const CATEGORIES_OK = () => json(200, { categories: [APPAREL, BAGS], csrf_token: "category-csrf" });

// 页面对结果的处理：show 换成新状态、update 按当前状态改动、replace 记下去向；set 模拟页面自己改状态（点 common.edit 与 common.back）。
function runner(initial: ProductsState) {
  let state = initial;
  const events: string[] = [];
  const moves: ProductsMoves = {
    show: (next) => {
      state = next;
      events.push(`show ${next.status}`);
    },
    update: (change) => {
      state = change(state);
      events.push("update");
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return {
    moves,
    events,
    get state() {
      return state;
    },
    set: (next: ProductsState | null) => {
      if (next !== null) {
        state = next;
      }
    },
  };
}

function editingOf(state: ProductsState): Editing | null {
  return state.status === "ready" ? state.editing : null;
}

function dictionary(language: Language): Set<string> {
  return new Set<string>(Object.values(COPY).map((entry) => entry[language]));
}

describe("AdminProductsPage", () => {
  // UX A04「入口：后台导航」与 SHOP-TASK-196 验收「页面」一条「两页都以后台框架渲染」：
  // 经路由打开 /admin/products 渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），不套前台框架（没有前台页头、页脚与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_PRODUCTS_PATH).toBe(PRODUCTS_PATH);
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、[admin.nav_products]、[admin.nav_categories]（0.17）、
  // [admin.nav_coupons]、…」与 SHOP-TASK-196 验收「导航」一条「桌面导航与手机菜单都按这个顺序列出」：经路由打开本页，
  // 桌面导航与 ☰ 菜单都依次为这九项，商品排在退款之后并标 aria-current="page"；各项文字为字典里当前语言的文案。
  it.each(LANGUAGES)("marks products current, after refunds, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderSignedIn(language, LOADING)]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([
          undefined,
          undefined,
          "page",
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
        ]);
        expect(textNodes(nav).slice(0, 9)).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
      }
    }
  });

  // UX A04 手机线框「☰  [admin.nav_products]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」。
  it.each(LANGUAGES)("names the products item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_products"][language])}</span>`,
    );
  });

  // UX A04 桌面线框内容区第一行「[admin.nav_products]」与本任务验收「列表——标题 admin.nav_products」「打开页面时编辑区不显示」
  // （SHOP-TASK-196 原先「内容区只有标题」的断言改为此条）：已登录、读取中时内容区以 h1 admin.nav_products 开始，其下列表区标 aria-busy，
  // 没有编辑区。
  it.each(LANGUAGES)("starts the content area with the products title while loading in %s", (language) => {
    const main = element(renderSignedIn(language, LOADING), "main", "acs-admin__main");
    expect(tags(main, "h1")).toEqual(['<h1 class="acs-admin__h">']);
    expect(textNodes(main)).toEqual([COPY["admin.nav_products"][language]]);
    expect(element(main, "div", "site-admin-products__body")).toContain('aria-busy="true"');
    expect(hasClass(main, "site-admin-products__editor")).toBe(false);
  });

  // HANDOFF 0.82 第（2）点「打开时并行读取商品列表与分类、不选中商品」与本任务验收「打开页面时并行读取商品列表（SHOP-TASK-200）与分类
  // （SHOP-TASK-198），两者都为 ok 才显示列表」「页面状态里保留…分类读取结果与列表给的 CSRF 令牌」：两个 GET 在任何一个回答之前都已发出、
  // 带同一个 signal；都取到后为按接口顺序的商品、分类读取的结果与列表（不是分类）给的 CSRF 令牌，编辑区为 null。
  it("reads products and categories in parallel and selects nothing", async () => {
    const products = deferred();
    const categories = deferred();
    const calls = stubFetch({ "/api/admin/products": [products.promise], "/api/admin/categories": [categories.promise] });
    const controller = new AbortController();
    const run = runner(LOADING);
    const opening = openProducts(controller.signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/products", "GET"],
      ["/api/admin/categories", "GET"],
    ]);
    expect(calls.map((call) => call.init.signal)).toEqual([controller.signal, controller.signal]);
    categories.resolve(CATEGORIES_OK());
    expect(run.events).toEqual([]);
    products.resolve(LIST_OK());
    await opening;
    expect(run.events).toEqual(["show ready"]);
    expect(run.state).toEqual({ status: "ready", products: LISTED, categories: [APPAREL, BAGS], csrfToken: "csrf-1", editing: null });
  });

  // 本任务验收「任一为 none 时照 AdminShippingPage.tsx 现有写法回到 A01；否则任一为 network 时显示 common.network_check，其他失败显示
  // common.error_retry」：两个读取结果的全部组合。
  it("combines the two read outcomes", () => {
    const productReads: Record<string, ProductsRead> = {
      ok: { kind: "ok", products: LISTED, csrf_token: "csrf-1" },
      none: { kind: "none" },
      network: { kind: "network" },
      failed: { kind: "failed" },
    };
    const categoryReads: Record<string, CategoriesRead> = {
      ok: { kind: "ok", categories: [APPAREL], csrf_token: "category-csrf" },
      none: { kind: "none" },
      network: { kind: "network" },
      failed: { kind: "failed" },
    };
    const network = { status: "failed", error: "common.network_check" };
    const retry = { status: "failed", error: "common.error_retry" };
    const expected: [string, string, unknown][] = [
      ["none", "ok", "login"],
      ["ok", "none", "login"],
      ["none", "none", "login"],
      ["none", "network", "login"],
      ["network", "none", "login"],
      ["none", "failed", "login"],
      ["failed", "none", "login"],
      ["network", "ok", network],
      ["ok", "network", network],
      ["network", "network", network],
      ["network", "failed", network],
      ["failed", "network", network],
      ["failed", "ok", retry],
      ["ok", "failed", retry],
      ["failed", "failed", retry],
      ["ok", "ok", { status: "ready", products: LISTED, categories: [APPAREL], csrfToken: "csrf-1", editing: null }],
    ];
    for (const [products, categories, step] of expected) {
      expect(readStep(productReads[products] ?? { kind: "failed" }, categoryReads[categories] ?? { kind: "failed" }), `${products} ${categories}`).toEqual(step);
    }
  });

  // 本任务验收「任一为 none 时…回到 A01」与「network 与其他失败的提示」：分类 401 时以 replace 回到 A01、不显示页面；
  // 商品列表网络中断时只显示 common.network_check（不显示列表）；其他失败（500）为 common.error_retry。
  it("returns to A01 or shows the failure after opening", async () => {
    stubFetch({ "/api/admin/products": [LIST_OK()], "/api/admin/categories": [json(401, {})] });
    const login = runner(LOADING);
    await openProducts(new AbortController().signal, login.moves);
    expect(login.events).toEqual([`replace ${LOGIN}`]);
    for (const [reply, error] of [
      ["network", "common.network_check"],
      [json(500, {}), "common.error_retry"],
    ] as const) {
      stubFetch({ "/api/admin/products": [reply], "/api/admin/categories": [CATEGORIES_OK()] });
      const run = runner(LOADING);
      await openProducts(new AbortController().signal, run.moves);
      expect(run.state).toEqual({ status: "failed", error });
      const body = element(render(run.state), "div", "site-admin-products__body");
      expect(body).toContain(escapeHtml(COPY[error].en));
      expect(body).not.toMatch(/<(table|ul)\b/);
    }
  });

  // 本任务验收「页面卸载时以 AbortSignal 中止」：读取未回答时 signal 已中止，结果到达后不再更新页面（没有 show、update 或 replace）。
  it("ignores the opening reads after the page is left", async () => {
    stubFetch({ "/api/admin/products": [json(401, {})], "/api/admin/categories": [CATEGORIES_OK()] });
    const controller = new AbortController();
    const run = runner(LOADING);
    const opening = openProducts(controller.signal, run.moves);
    controller.abort();
    await opening;
    expect(run.events).toEqual([]);
  });

  // HANDOFF 0.82「列表的商品与分类名称…按后台界面语言、缺译回退英文（null 与空串都算缺译，都缺时显示 —）」：
  // 界面语言有译名时用它；为 null 或空串时回退英文；英文也为 null 或空串时为 —。
  it("falls back from the admin language to English, then to —", () => {
    expect(localName({ en: "Tee", zh: "T恤", ms: "Baju" }, "zh")).toBe("T恤");
    expect(localName({ en: "Tee", zh: "T恤", ms: "Baju" }, "ms")).toBe("Baju");
    expect(localName({ en: "Tee", zh: null, ms: "" }, "zh")).toBe("Tee");
    expect(localName({ en: "Tee", zh: null, ms: "" }, "ms")).toBe("Tee");
    expect(localName({ en: null, zh: null, ms: null }, "en")).toBe("—");
    expect(localName({ en: "", zh: null, ms: "" }, "zh")).toBe("—");
    expect(localName({ en: null, zh: "T恤", ms: null }, "zh")).toBe("T恤");
    expect(localName({ en: null, zh: "T恤", ms: null }, "en")).toBe("—");
    expect(productName(TEE, "zh")).toBe("圆领T恤");
    expect(categoryName(TEE, "ms")).toBe("Pakaian");
    expect(productName(TOTE, "zh")).toBe("Linen Tote Bag");
    expect(categoryName(TOTE, "ms")).toBe("Bags");
    expect(productName(NAMELESS, "en")).toBe("—");
    expect(categoryName(NAMELESS, "zh")).toBe("—");
  });

  // UX A04「列表：全部商品（含未上架的）…状态显示 [admin.active] 或 [admin.unpublished]」、桌面线框
  // 「<名称> <分类> [admin.active]|[admin.unpublished] ( [common.edit] )」与本任务验收「桌面为表格（每行商品名称、分类名称、状态…与 common.edit）」
  // 「顺序照接口」：表格各行按接口顺序为商品名称、其下的分类名称（照 A04-desktop）、状态与 common.edit；名称按界面语言回退；
  // 上架为 acs-tag--neutral、未上架为 acs-tag--outline（照 A04-desktop）。
  it.each(LANGUAGES)("lists every product in API order in the desktop table in %s", (language) => {
    const table = element(render(ready(), language), "div", "site-admin-products__table");
    const rows = [...table.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => textNodes(m[1] ?? ""));
    const status = (item: AdminProductListItem) => COPY[item.is_active ? "admin.active" : "admin.unpublished"][language];
    expect(rows).toEqual(
      LISTED.map((item) => [productName(item, language), categoryName(item, language), status(item), COPY["common.edit"][language]]),
    );
    const names: Record<Language, string[]> = {
      en: ["Linen Tote Bag", "Bags", "Crew Neck Tee", "Apparel", "—", "—"],
      zh: ["Linen Tote Bag", "Bags", "圆领T恤", "服饰", "—", "—"],
      ms: ["Linen Tote Bag", "Bags", "Baju Leher Bulat", "Pakaian", "—", "—"],
    };
    expect(rows.flatMap((row) => row.slice(0, 2))).toEqual(names[language]);
    const tagClasses = tags(table, "span").filter((tag) => classes(tag).includes("acs-tag")).map((tag) => attributes(tag).get("class"));
    expect(tagClasses).toEqual(["acs-tag acs-tag--outline", "acs-tag acs-tag--neutral", "acs-tag acs-tag--outline"]);
  });

  // UX A04 手机线框「<名称> [admin.active]|[admin.unpublished] >」「（> 读屏标签 [common.edit]）」与本任务验收「手机为卡片（商品名称与状态，
  // 整张卡片为带读屏标签 common.edit 的按钮），两套都渲染」：表格与卡片都在；每张卡片是一个 type=button、aria-label 为 common.edit 的按钮，
  // 里面按接口顺序为商品名称（照回退）与状态，没有分类名称。
  it.each(LANGUAGES)("renders the phone cards alongside the table in %s", (language) => {
    const html = render(ready(), language);
    expect(html).toMatch(/<table class="acs-admin__table">/);
    const cards = element(html, "ul", "site-admin-products__cards");
    const buttons = [...cards.matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)];
    expect(buttons).toHaveLength(LISTED.length);
    for (const [index, item] of LISTED.entries()) {
      const [, open = "", inner = ""] = buttons[index] ?? [];
      expect(attributes(open).get("type")).toBe("button");
      expect(attributes(open).get("aria-label")).toBe(COPY["common.edit"][language]);
      expect(textNodes(inner)).toEqual([productName(item, language), COPY[item.is_active ? "admin.active" : "admin.unpublished"][language]]);
    }
  });

  // 本任务验收「打开页面时编辑区不显示」：列表取到、没有点 common.edit 时，根元素没有 --editing，没有编辑区，也没有 admin.product_edit 与 common.back。
  it("shows no editor until a product is chosen", () => {
    const html = render(ready());
    expect(rootClasses(html)).toEqual(["site-admin-products"]);
    expect(hasClass(html, "site-admin-products__editor")).toBe(false);
    expect(html).not.toContain(COPY["admin.product_edit"].en);
    expect(html).not.toContain(`>${COPY["common.back"].en}<`);
  });

  // HANDOFF 0.82「点编辑后先收起当前编辑区、读取单件成功后才显示编辑区（读取期间不显示内容…）」「点已打开的那一件商品的编辑不做任何事」与
  // 本任务验收「点编辑区已打开（或正在读取）的那一件商品的 common.edit 不做任何事」：点另一件时编辑区换成读取中（原先的单件商品不再显示）；
  // 点正在读取或已打开的同一件时为 null；列表还没取到时也为 null；读取失败（编辑区没有打开）的那一件再点时重新读取。
  // 读取中的编辑区标 aria-busy，只有 common.back，没有标题。
  it("collapses the editor and starts reading on common.edit", () => {
    const opened = ready(LISTED, { status: "ready", id: 3, product: TEE_PRODUCT });
    const reading = startEditing(opened, 5);
    expect(reading).toEqual(ready(LISTED, { status: "loading", id: 5 }));
    expect(reading === null ? null : startEditing(reading, 5)).toBeNull();
    expect(startEditing(opened, 3)).toBeNull();
    expect(startEditing(LOADING, 3)).toBeNull();
    expect(startEditing(ready(LISTED, { status: "failed", id: 7, error: "common.error_retry" }), 7)).toEqual(ready(LISTED, { status: "loading", id: 7 }));
    const html = render(ready(LISTED, { status: "loading", id: 5 }));
    expect(rootClasses(html)).toContain("site-admin-products--editing");
    const editor = element(html, "div", "site-admin-products__editor");
    expect(attributes(tags(editor, "div")[0] ?? "").get("aria-busy")).toBe("true");
    expect(textNodes(editor)).toEqual([COPY["common.back"].en]);
    expect(html).not.toContain(COPY["admin.product_edit"].en);
  });

  // 本任务验收「点某行 common.edit…读取该商品，ok 时显示编辑区（标题 admin.product_edit）」与「页面状态里保留读取到的单件商品」：
  // GET /api/admin/products/{id} 带传入的 signal；取到后编辑区为该商品，渲染出面板与 h2 admin.product_edit，列表仍在。
  it.each(LANGUAGES)("shows admin.product_edit once the product is read in %s", async (language) => {
    const calls = stubFetch({ "/api/admin/products/3": [json(200, TEE_PRODUCT)] });
    const controller = new AbortController();
    const run = runner(ready(LISTED, { status: "loading", id: 3 }));
    await openProduct(3, controller.signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/products/3", "GET"]]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(editingOf(run.state)).toEqual({ status: "ready", id: 3, product: TEE_PRODUCT });
    const html = render(run.state, language);
    const editor = element(html, "div", "site-admin-products__editor");
    expect(attributes(tags(editor, "div")[0] ?? "").get("aria-busy")).toBe("false");
    expect(element(editor, "div", "site-admin-products__form")).toBe(
      `<div class="acs-admin__panel site-admin-products__form"><h2 class="acs-admin__h">${escapeHtml(COPY["admin.product_edit"][language])}</h2></div>`,
    );
    expect(hasClass(html, "site-admin-products__list")).toBe(true);
  });

  // 本任务验收「none 回到 A01，network 与其他失败在编辑区的位置显示 common.network_check 或 common.error_retry」：单件 401 以 replace 回到 A01；
  // 网络中断与 404（商品不存在，归其他失败）各在编辑区里显示提示，没有标题。
  it("returns to A01 or shows the failure where the editor goes", async () => {
    stubFetch({ "/api/admin/products/3": [json(401, {})] });
    const login = runner(ready(LISTED, { status: "loading", id: 3 }));
    await openProduct(3, new AbortController().signal, login.moves);
    expect(login.events).toEqual([`replace ${LOGIN}`]);
    expect(productStep({ kind: "none" }, 3)).toBe("login");
    for (const [reply, error] of [
      ["network", "common.network_check"],
      [json(404, { detail: "product_not_found" }), "common.error_retry"],
    ] as const) {
      stubFetch({ "/api/admin/products/3": [reply] });
      const run = runner(ready(LISTED, { status: "loading", id: 3 }));
      await openProduct(3, new AbortController().signal, run.moves);
      expect(editingOf(run.state)).toEqual({ status: "failed", id: 3, error });
      const editor = element(render(run.state), "div", "site-admin-products__editor");
      expect(textNodes(editor)).toEqual([COPY["common.back"].en, COPY[error].en]);
      expect(editor).toContain('role="alert"');
    }
  });

  // 本任务验收「读取期间再点另一行时以最后一次为准（前一次以 AbortSignal 中止，它的结果不显示）」：先点 Tee、再点 Tote（中止前一次），
  // Tee 的回答先到也不更新页面，编辑区最后为 Tote；即使先前的结果没被中止，settleEditing 也不让它换掉正在读取别的商品或已打开的编辑区。
  it("lets the last common.edit win", async () => {
    const tee = deferred();
    const tote = deferred();
    stubFetch({ "/api/admin/products/3": [tee.promise], "/api/admin/products/5": [tote.promise] });
    const run = runner(ready());
    const first = new AbortController();
    run.set(startEditing(run.state, 3));
    const readingTee = openProduct(3, first.signal, run.moves);
    first.abort();
    const second = new AbortController();
    run.set(startEditing(run.state, 5));
    const readingTote = openProduct(5, second.signal, run.moves);
    tee.resolve(json(200, TEE_PRODUCT));
    await readingTee;
    expect(run.events).toEqual([]);
    expect(editingOf(run.state)).toEqual({ status: "loading", id: 5 });
    tote.resolve(json(200, TOTE_PRODUCT));
    await readingTote;
    expect(run.events).toEqual(["update"]);
    expect(editingOf(run.state)).toEqual({ status: "ready", id: 5, product: TOTE_PRODUCT });
    const late: Editing = { status: "ready", id: 3, product: TEE_PRODUCT };
    expect(settleEditing(ready(LISTED, { status: "loading", id: 5 }), late)).toEqual(ready(LISTED, { status: "loading", id: 5 }));
    expect(settleEditing(run.state, late)).toEqual(run.state);
    expect(settleEditing(ready(), late)).toEqual(ready());
  });

  // HANDOFF 0.82「手机上从点编辑起就隐藏列表、显示返回，返回会中止未完成的读取」与本任务验收「手机上点 common.edit 之后（读取期间、读取失败
  // 与打开后）都隐藏列表、上方为 common.back，点它中止尚未完成的读取、回到列表」：三种编辑区里根元素都有 --editing（site.css 在手机上据此隐藏列表），
  // 编辑区第一个元素为 common.back 按钮；点它后编辑区为 null，被中止的读取之后到达也不更新页面。
  it("puts common.back above the editor and abandons the read on back", async () => {
    const states: Editing[] = [
      { status: "loading", id: 3 },
      { status: "failed", id: 3, error: "common.network_check" },
      { status: "ready", id: 3, product: TEE_PRODUCT },
    ];
    for (const editing of states) {
      const html = render(ready(LISTED, editing), "zh");
      expect(rootClasses(html)).toEqual(["site-admin-products", "site-admin-products--editing"]);
      const editor = element(html, "div", "site-admin-products__editor");
      const back = tags(editor, "button")[0] ?? "";
      expect(classes(back)).toContain("site-admin-products__back");
      expect(attributes(back).get("type")).toBe("button");
      expect(textNodes(editor)[0]).toBe(COPY["common.back"].zh);
    }
    const tee = deferred();
    stubFetch({ "/api/admin/products/3": [tee.promise] });
    const controller = new AbortController();
    const run = runner(ready(LISTED, { status: "loading", id: 3 }));
    const reading = openProduct(3, controller.signal, run.moves);
    controller.abort();
    run.set(closeEditing(run.state));
    tee.resolve(json(200, TEE_PRODUCT));
    await reading;
    expect(run.events).toEqual([]);
    expect(run.state).toEqual(ready());
    expect(closeEditing(LOADING)).toBe(LOADING);
  });

  // 本任务验收「测试」一条「页面文字全部来自字典」：经路由打开、读取中、读取失败、列表与编辑区的三种状态里，看得见或读得出的每段文字都是
  // 当前语言的字典文案，或商品与分类数据本身（名称与名称缺失时的 —）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = dictionary(language);
    for (const item of LISTED) {
      allowed.add(productName(item, language));
      allowed.add(categoryName(item, language));
    }
    const html = [
      renderApp(language),
      renderSignedIn(language, LOADING),
      renderSignedIn(language, { status: "failed", error: "common.network_check" }),
      renderSignedIn(language, ready()),
      renderSignedIn(language, ready(LISTED, { status: "loading", id: 3 })),
      renderSignedIn(language, ready(LISTED, { status: "failed", id: 3, error: "common.error_retry" })),
      renderSignedIn(language, ready(LISTED, { status: "ready", id: 3, product: TEE_PRODUCT })),
    ];
    for (const page of html) {
      for (const value of visibleTexts(page)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
