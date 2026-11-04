import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import type { AdminOption, AdminOptionValue, AdminProduct, AdminProductListItem, ProductsRead } from "../api/adminProducts";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PRODUCTS_PATH, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminProductsView,
  categoryChoices,
  categoryName,
  categoryUnavailable,
  changeActive,
  changeCategory,
  changeDescription,
  changeMaxPerOrder,
  changeName,
  changeOptionName,
  changePrice,
  changeStock,
  changeValueName,
  changeVariantActive,
  chooseContent,
  closeEditing,
  editorOf,
  formOf,
  isChoosable,
  isDirty,
  localName,
  LOADING,
  openProduct,
  openProducts,
  priceText,
  productName,
  productStep,
  readStep,
  settleEditing,
  skuRows,
  startEditing,
  texts,
  translationMissing,
  variantText,
} from "./AdminProductsPage";
import type { AdminProductsViewProps, Editing, OpenEditor, ProductForm, ProductsMoves, ProductsState } from "./AdminProductsPage";

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

// 带两个规格名的商品（列表的 TEE，分类 1）：马来文名称与描述为 null、中文描述有值；Navy 的中文为 null、马来文为空串，L 只有英文。
// 三个 SKU 的价格为 3900、0 与 5 仙。
const BLACK: AdminOptionValue = { id: 31, code: "black", name_en: "Black", name_zh: "黑色", name_ms: "Hitam" };
const NAVY: AdminOptionValue = { id: 32, code: "navy", name_en: "Navy", name_zh: null, name_ms: "" };
const MEDIUM: AdminOptionValue = { id: 33, code: "m", name_en: "M", name_zh: "中", name_ms: "Sederhana" };
const LARGE: AdminOptionValue = { id: 34, code: "l", name_en: "L", name_zh: null, name_ms: null };
const COLOUR: AdminOption = { id: 21, code: "colour", name_en: "Colour", name_zh: "颜色", name_ms: "Warna", values: [BLACK, NAVY] };
const SIZE: AdminOption = { id: 22, code: "size", name_en: "Size", name_zh: "尺码", name_ms: null, values: [MEDIUM, LARGE] };
const SHIRT: AdminProduct = {
  id: 3,
  slug: "crew-neck-tee",
  version: 4,
  category_id: 1,
  name_en: "Crew Neck Tee",
  name_zh: "圆领T恤",
  name_ms: null,
  description_en: "A soft cotton tee.",
  description_zh: "柔软的棉质T恤。",
  description_ms: null,
  is_active: true,
  max_per_order: 10,
  options: [COLOUR, SIZE],
  variants: [
    { id: 41, sku: "crew-neck-tee-black-m", price_sen: 3900, daily_initial_stock: 20, available_stock: 18, is_active: true, option_value_ids: [31, 33] },
    { id: 42, sku: "crew-neck-tee-navy-m", price_sen: 0, daily_initial_stock: 0, available_stock: 0, is_active: false, option_value_ids: [32, 33] },
    { id: 43, sku: "crew-neck-tee-navy-l", price_sen: 5, daily_initial_stock: 7, available_stock: 3, is_active: true, option_value_ids: [32, 34] },
  ],
  images: [],
};
// 同一件商品、三种语言的文案都齐全。
const FULL: AdminProduct = {
  ...SHIRT,
  name_ms: "Baju Leher Bulat",
  description_ms: "Baju kapas lembut.",
  options: [
    { ...COLOUR, values: [BLACK, { ...NAVY, name_zh: "藏青", name_ms: "Biru Tua" }] },
    { ...SIZE, name_ms: "Saiz", values: [MEDIUM, { ...LARGE, name_zh: "大", name_ms: "Besar" }] },
  ],
};

// 分类读取的结果（接口顺序）：可选的为 Apparel、Bags 与 Kitchen；Seasonal 停用，其余英文名称为 null、空串或只有空白。
const SEASONAL: AdminCategory = { id: 4, slug: "seasonal", name_en: "Seasonal", name_zh: "季节", name_ms: null, is_active: false, version: 2, visible_products: 0 };
const NO_EN: AdminCategory = { id: 6, slug: "category-6", name_en: null, name_zh: "无名", name_ms: null, is_active: true, version: 1, visible_products: 0 };
const EMPTY_EN: AdminCategory = { id: 8, slug: "category-8", name_en: "", name_zh: "空", name_ms: null, is_active: true, version: 1, visible_products: 0 };
const BLANK_EN: AdminCategory = { id: 10, slug: "category-10", name_en: "   ", name_zh: "空白", name_ms: null, is_active: true, version: 1, visible_products: 0 };
const KITCHEN: AdminCategory = { id: 12, slug: "kitchen", name_en: "Kitchen", name_zh: "厨房", name_ms: "", is_active: true, version: 1, visible_products: 2 };
const CATEGORIES = [SEASONAL, APPAREL, NO_EN, BAGS, EMPTY_EN, BLANK_EN, KITCHEN];

// 当前分类为停用的 Seasonal 的商品与它在列表里的那一项。
const SEASONAL_SHIRT: AdminProduct = { ...SHIRT, category_id: 4 };
const SEASONAL_TEE: AdminProductListItem = { ...TEE, category_id: 4, category_name_en: "Seasonal", category_name_zh: "季节", category_name_ms: null };

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

function ready(products: AdminProductListItem[] = LISTED, editing: Editing | null = null, categories: AdminCategory[] = [APPAREL, BAGS]): ProductsState {
  return { status: "ready", products, categories, csrfToken: "csrf-1", editing };
}

const HANDLERS: Omit<AdminProductsViewProps, "state"> = {
  onEdit: () => undefined,
  onBack: () => undefined,
  onContent: () => undefined,
  onName: () => undefined,
  onDescription: () => undefined,
  onCategory: () => undefined,
  onActive: () => undefined,
  onMaxPerOrder: () => undefined,
  onOptionName: () => undefined,
  onValueName: () => undefined,
  onPrice: () => undefined,
  onStock: () => undefined,
  onVariantActive: () => undefined,
};

function view(state: ProductsState): ReactNode {
  return <AdminProductsView state={state} {...HANDLERS} />;
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

// 编辑区的面板（含其内容）。
function form(html: string): string {
  return element(html, "div", "site-admin-products__form");
}

// 全部 label 元素（label 不嵌套）；field 为第一个文本节点是 text 的那一个。
function labels(html: string): string[] {
  return [...html.matchAll(/<label\b[^>]*>[\s\S]*?<\/label>/g)].map((m) => m[0]);
}

function field(html: string, text: string): string {
  const found = labels(html).find((label) => textNodes(label)[0] === text);
  if (found === undefined) {
    throw new Error(`no label ${text}`);
  }
  return found;
}

// 第一个 input 的 value 与是否勾选；textarea 的内容；select 的各项（value、文字、是否选中）。
function inputValue(html: string): string | undefined {
  return attributes(tags(html, "input")[0] ?? "").get("value");
}

function isChecked(html: string): boolean {
  return attributes(tags(html, "input")[0] ?? "").has("checked");
}

function textareaValue(html: string): string | undefined {
  const m = /<textarea\b[^>]*>([\s\S]*?)<\/textarea>/.exec(html);
  return m === null ? undefined : unescapeHtml(m[1] ?? "");
}

function selectOptions(html: string): [string, string, boolean][] {
  return [...html.matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)].map((m) => {
    const found = attributes(`<option${m[1] ?? ""}>`);
    return [found.get("value") ?? "", unescapeHtml(m[2] ?? ""), found.has("selected")];
  });
}

// 编辑区已打开的页面状态。
function opened(editor: OpenEditor, products: AdminProductListItem[] = LISTED, categories: AdminCategory[] = [APPAREL, BAGS]): ProductsState {
  return ready(products, editor, categories);
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
  // 点正在读取或已打开的同一件时为 null（本任务 purpose「点当前已打开的那一件商品的 common.edit 照 202 不做任何事，不经确认、不丢失修改」：
  // 有修改时也为 null，页面据此不弹确认）；列表还没取到时也为 null；读取失败（编辑区没有打开）的那一件再点时重新读取。
  // 读取中的编辑区标 aria-busy，只有 common.back，没有标题。
  it("collapses the editor and starts reading on common.edit", () => {
    const open = ready(LISTED, editorOf(3, TEE_PRODUCT));
    const reading = startEditing(open, 5);
    expect(reading).toEqual(ready(LISTED, { status: "loading", id: 5 }));
    expect(reading === null ? null : startEditing(reading, 5)).toBeNull();
    expect(startEditing(open, 3)).toBeNull();
    expect(startEditing(ready(LISTED, changeName(editorOf(3, TEE_PRODUCT), "Changed")), 3)).toBeNull();
    expect(startEditing(LOADING, 3)).toBeNull();
    expect(startEditing(ready(LISTED, { status: "failed", id: 7, error: "common.error_retry" }), 7)).toEqual(ready(LISTED, { status: "loading", id: 7 }));
    const html = render(ready(LISTED, { status: "loading", id: 5 }));
    expect(rootClasses(html)).toContain("site-admin-products--editing");
    const editor = element(html, "div", "site-admin-products__editor");
    expect(attributes(tags(editor, "div")[0] ?? "").get("aria-busy")).toBe("true");
    expect(textNodes(editor)).toEqual([COPY["common.back"].en]);
    expect(html).not.toContain(COPY["admin.product_edit"].en);
  });

  // SHOP-TASK-202 验收「点某行 common.edit…读取该商品，ok 时显示编辑区（标题 admin.product_edit）」与「页面状态里保留读取到的单件商品」：
  // GET /api/admin/products/{id} 带传入的 signal；取到后编辑区为该商品（基线与已填的值都为读取的值、内容语言英文，照 editorOf），
  // 面板以 h2 admin.product_edit 开始，列表仍在。
  it.each(LANGUAGES)("shows admin.product_edit once the product is read in %s", async (language) => {
    const calls = stubFetch({ "/api/admin/products/3": [json(200, TEE_PRODUCT)] });
    const controller = new AbortController();
    const run = runner(ready(LISTED, { status: "loading", id: 3 }));
    await openProduct(3, controller.signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/products/3", "GET"]]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(editingOf(run.state)).toEqual({ status: "ready", id: 3, product: TEE_PRODUCT, base: formOf(TEE_PRODUCT), form: formOf(TEE_PRODUCT), content: "en" });
    const html = render(run.state, language);
    const editor = element(html, "div", "site-admin-products__editor");
    expect(attributes(tags(editor, "div")[0] ?? "").get("aria-busy")).toBe("false");
    expect(
      form(editor).startsWith(`<div class="acs-admin__panel site-admin-products__form"><h2 class="acs-admin__h">${escapeHtml(COPY["admin.product_edit"][language])}</h2>`),
    ).toBe(true);
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
    expect(editingOf(run.state)).toEqual(editorOf(5, TOTE_PRODUCT));
    const late: Editing = editorOf(3, TEE_PRODUCT);
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
      editorOf(3, TEE_PRODUCT),
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

  // SHOP-TASK-202 与本任务验收「测试」一条「页面文字全部来自字典」：经路由打开、读取中、读取失败、列表与编辑区的各种状态（含缺译提示、
  // 当前分类不可选的提示与中文、马来文内容语言）里，看得见或读得出的每段文字都是当前语言的字典文案，或商品与分类数据本身（名称、描述、
  // SKU 代码、规格列、当日可用库存与缺失时的 —）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = dictionary(language);
    const editors = [editorOf(3, TEE_PRODUCT), editorOf(3, SHIRT), chooseContent(editorOf(3, SHIRT), "zh"), chooseContent(editorOf(3, SEASONAL_SHIRT), "ms")];
    for (const item of [...LISTED, SEASONAL_TEE]) {
      allowed.add(productName(item, language));
      allowed.add(categoryName(item, language));
    }
    for (const label of ["common.lang_zh", "common.lang_ms"] as const) {
      allowed.add(COPY["admin.translation_missing"][language].replace("{language}", COPY[label][language]));
    }
    for (const editor of editors) {
      allowed.add(editor.form.descriptions[editor.content]);
      for (const choice of categoryChoices(CATEGORIES, editor.product, SEASONAL_TEE, language)) {
        allowed.add(choice.label);
      }
      for (const row of skuRows(editor, language)) {
        allowed.add(row.sku);
        allowed.add(row.variant);
        allowed.add(String(row.available));
      }
    }
    const html = [
      renderApp(language),
      renderSignedIn(language, LOADING),
      renderSignedIn(language, { status: "failed", error: "common.network_check" }),
      renderSignedIn(language, ready()),
      renderSignedIn(language, ready(LISTED, { status: "loading", id: 3 })),
      renderSignedIn(language, ready(LISTED, { status: "failed", id: 3, error: "common.error_retry" })),
      ...editors.map((editor) => renderSignedIn(language, opened(editor, [SEASONAL_TEE, TOTE], CATEGORIES))),
    ];
    for (const page of html) {
      for (const value of visibleTexts(page)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });

  // 本任务验收「读取为 null 的文案在输入框与比较基线里都当作空串」「价格输入框的初值为整仙用整数运算写成的两位小数文字（照 feeText）」：
  // formOf 给出的值（也是 editorOf 的基线）里 null 为空串，价格 3900、0 与 5 仙为 39.00、0.00 与 0.05，库存与限购为十进制文字；
  // 规格名与规格值、SKU 都按接口顺序。
  it("turns the read product into the initial values", () => {
    expect([0, 5, 99, 3900, 50000000].map(priceText)).toEqual(["0.00", "0.05", "0.99", "39.00", "500000.00"]);
    expect(texts({ en: "Tee", zh: null, ms: "" })).toEqual({ en: "Tee", zh: "", ms: "" });
    const expected: ProductForm = {
      names: { en: "Crew Neck Tee", zh: "圆领T恤", ms: "" },
      descriptions: { en: "A soft cotton tee.", zh: "柔软的棉质T恤。", ms: "" },
      categoryId: 1,
      isActive: true,
      maxPerOrder: "10",
      options: [
        {
          id: 21,
          names: { en: "Colour", zh: "颜色", ms: "Warna" },
          values: [
            { id: 31, names: { en: "Black", zh: "黑色", ms: "Hitam" } },
            { id: 32, names: { en: "Navy", zh: "", ms: "" } },
          ],
        },
        {
          id: 22,
          names: { en: "Size", zh: "尺码", ms: "" },
          values: [
            { id: 33, names: { en: "M", zh: "中", ms: "Sederhana" } },
            { id: 34, names: { en: "L", zh: "", ms: "" } },
          ],
        },
      ],
      variants: [
        { id: 41, price: "39.00", stock: "20", isActive: true },
        { id: 42, price: "0.00", stock: "0", isActive: false },
        { id: 43, price: "0.05", stock: "7", isActive: true },
      ],
    };
    expect(formOf(SHIRT)).toEqual(expected);
    expect(editorOf(3, SHIRT)).toEqual({ status: "ready", id: 3, product: SHIRT, base: expected, form: expected, content: "en" });
  });

  // UX A04 线框「[admin.product_name] [____]」「[admin.product_description] [____]」「[admin.product_category] [▾] [admin.active] ☐」
  // 「[admin.max_per_order] [M3 __]」与本任务验收「描述为多行输入框」「读取单件后的各输入框初值（含 null 当作空串）」：英文内容语言下
  // 名称输入框、描述 textarea、分类下拉（选中商品的分类）、上架勾选框与每单限购输入框为读取的值；马来文内容语言下 null 的名称与描述为空串。
  it.each(LANGUAGES)("renders the read values in the inputs in %s", (language) => {
    const html = form(render(opened(editorOf(3, SHIRT)), language));
    expect(inputValue(field(html, COPY["admin.product_name"][language]))).toBe("Crew Neck Tee");
    expect(textareaValue(field(html, COPY["admin.product_description"][language]))).toBe("A soft cotton tee.");
    expect(selectOptions(field(html, COPY["admin.product_category"][language])).filter(([, , selected]) => selected).map(([value]) => value)).toEqual(["1"]);
    expect(isChecked(field(html, COPY["admin.active"][language]))).toBe(true);
    expect(isChecked(field(form(render(opened(changeActive(editorOf(3, SHIRT), false)), language)), COPY["admin.active"][language]))).toBe(false);
    const limit = field(html, COPY["admin.max_per_order"][language]);
    expect(inputValue(limit)).toBe("10");
    expect(attributes(tags(limit, "input")[0] ?? "").get("inputmode")).toBe("numeric");
    const malay = form(render(opened(chooseContent(editorOf(3, SHIRT), "ms")), language));
    expect(inputValue(field(malay, COPY["admin.product_name"][language]))).toBe("");
    expect(textareaValue(field(malay, COPY["admin.product_description"][language]))).toBe("");
  });

  // UX A04 线框「[admin.content_language] ([common.lang_en]) ([common.lang_zh]) ([common.lang_ms])」与本任务验收「三个按钮（aria-pressed
  // 标出当前内容语言，默认英文）切换商品名称、描述以及每个规格名与规格值的名称输入框显示与编辑的语言，切换不丢失其他语言已填的值」。
  it("switches the content language without losing other languages", () => {
    const start = editorOf(3, SHIRT);
    const pressed = (editor: OpenEditor) =>
      tags(element(form(render(opened(editor))), "div", "site-admin-products__languages"), "button").map((tag) => attributes(tag).get("aria-pressed"));
    expect(pressed(start)).toEqual(["true", "false", "false"]);
    expect(textNodes(element(form(render(opened(start))), "div", "site-admin-products__languages"))).toEqual([
      COPY["admin.content_language"].en,
      COPY["common.lang_en"].en,
      COPY["common.lang_zh"].en,
      COPY["common.lang_ms"].en,
    ]);
    let editor = changeName(chooseContent(start, "zh"), "圆领T恤（新）");
    editor = changeOptionName(editor, 0, "色");
    editor = changeValueName(editor, 1, 1, "大");
    editor = changeDescription(chooseContent(editor, "ms"), "Baju kapas.");
    editor = chooseContent(editor, "en");
    expect(pressed(editor)).toEqual(["true", "false", "false"]);
    expect(editor.form.names).toEqual({ en: "Crew Neck Tee", zh: "圆领T恤（新）", ms: "" });
    expect(editor.form.descriptions).toEqual({ en: "A soft cotton tee.", zh: "柔软的棉质T恤。", ms: "Baju kapas." });
    expect(editor.form.options.map((option) => option.names.zh)).toEqual(["色", "尺码"]);
    expect(editor.form.options.map((option) => option.values.map((value) => value.names.zh))).toEqual([["黑色", ""], ["中", "大"]]);
    const zh = chooseContent(editor, "zh");
    expect(pressed(zh)).toEqual(["false", "true", "false"]);
    const html = form(render(opened(zh)));
    expect(inputValue(field(html, COPY["admin.product_name"].en))).toBe("圆领T恤（新）");
    expect(textareaValue(field(html, COPY["admin.product_description"].en))).toBe("柔软的棉质T恤。");
    const options = element(html, "div", "site-admin-products__options");
    expect(labels(options).map(inputValue)).toEqual(["色", "尺码"]);
    const values = tags(options, "input").filter((tag) => attributes(tag).get("aria-label") === COPY["admin.option_value"].en);
    expect(values.map((tag) => attributes(tag).get("value"))).toEqual(["黑色", "", "中", "大"]);
    expect([...new Set(tags(html, "input").filter((tag) => attributes(tag).has("lang")).map((tag) => attributes(tag).get("lang")))]).toEqual(["zh-Hans"]);
    expect(textareaValue(field(form(render(opened(chooseContent(editor, "ms")))), COPY["admin.product_description"].en))).toBe("Baju kapas.");
  });

  // UX A04「[admin.translation_missing]（缺译时）」与本任务验收「内容语言为中文或马来文且该语言的商品名称、描述或任一规格名、规格值的名称为空
  // （去掉首尾空白后）时显示一条 admin.translation_missing（{language} 为该语言在当前界面语言下的名称）」：文案齐全时三种内容语言都没有；
  // 英文内容语言即使中文、马来文有缺也没有；中文或马来文下名称、描述、规格名、规格值任一项为空或只有空白时有，且只有一条。
  it.each(LANGUAGES)("shows one translation notice only for missing Chinese or Malay text in %s", (language) => {
    const count = (editor: OpenEditor) => (render(opened(editor), language).match(/class="site-admin-products__missing"/g) ?? []).length;
    for (const content of LANGUAGES) {
      expect(translationMissing(chooseContent(editorOf(3, FULL), content))).toBe(false);
      expect(count(chooseContent(editorOf(3, FULL), content))).toBe(0);
    }
    expect(translationMissing(editorOf(3, SHIRT))).toBe(false);
    expect(count(editorOf(3, SHIRT))).toBe(0);
    expect(count(chooseContent(editorOf(3, SHIRT), "ms"))).toBe(1);
    for (const content of ["zh", "ms"] as const) {
      const full = chooseContent(editorOf(3, FULL), content);
      const blanks = [
        changeName(full, "  "),
        changeDescription(full, ""),
        changeOptionName(full, 1, " "),
        changeValueName(full, 0, 1, ""),
        changeValueName(full, 1, 0, "\t"),
      ];
      for (const editor of blanks) {
        expect(translationMissing(editor)).toBe(true);
        expect(count(editor)).toBe(1);
        expect(translationMissing(chooseContent(editor, "en"))).toBe(false);
      }
      const html = render(opened(changeName(full, "")), language);
      expect(element(html, "p", "site-admin-products__missing")).toContain(
        escapeHtml(COPY["admin.translation_missing"][language].replace("{language}", COPY[content === "zh" ? "common.lang_zh" : "common.lang_ms"][language])),
      );
    }
  });

  // HANDOFF 0.82 第（2）点「分类下拉只列启用且有英文名称（不为 null、去掉首尾空白后不为空）的分类，商品当前分类不在其中时另加一项排在最前」
  // 与本任务 purpose 的落实写法（按接口顺序，文字按后台界面语言、缺译回退英文；另加的一项文字取商品列表该项的 category_name_*，都缺时为 —）：
  // 停用的、英文名称为 null、空串或只有空白的都不列。
  it("lists only choosable categories, with an unavailable current category first", () => {
    expect(CATEGORIES.map(isChoosable)).toEqual([false, true, false, true, false, false, true]);
    expect(categoryChoices(CATEGORIES, SHIRT, TEE, "en")).toEqual([
      { id: 1, label: "Apparel", choosable: true },
      { id: 2, label: "Bags", choosable: true },
      { id: 12, label: "Kitchen", choosable: true },
    ]);
    expect(categoryChoices(CATEGORIES, SHIRT, TEE, "zh").map((choice) => choice.label)).toEqual(["服饰", "Bags", "厨房"]);
    expect(categoryChoices(CATEGORIES, SHIRT, TEE, "ms").map((choice) => choice.label)).toEqual(["Pakaian", "Bags", "Kitchen"]);
    expect(categoryChoices(CATEGORIES, SEASONAL_SHIRT, SEASONAL_TEE, "zh")).toEqual([
      { id: 4, label: "季节", choosable: false },
      { id: 1, label: "服饰", choosable: true },
      { id: 2, label: "Bags", choosable: true },
      { id: 12, label: "厨房", choosable: true },
    ]);
    expect(categoryChoices(CATEGORIES, SEASONAL_SHIRT, SEASONAL_TEE, "ms")[0]).toEqual({ id: 4, label: "Seasonal", choosable: false });
    const nameless = { ...SEASONAL_TEE, category_name_en: "", category_name_zh: null, category_name_ms: "" };
    expect(categoryChoices(CATEGORIES, SEASONAL_SHIRT, nameless, "zh")[0]).toEqual({ id: 4, label: "—", choosable: false });
    expect(categoryChoices(CATEGORIES, SEASONAL_SHIRT, undefined, "en")[0]).toEqual({ id: 4, label: "—", choosable: false });
    const noEnglish = { ...SEASONAL_TEE, category_id: 6, category_name_en: null, category_name_zh: "无名", category_name_ms: null };
    expect(categoryChoices(CATEGORIES, { ...SHIRT, category_id: 6 }, noEnglish, "zh")[0]).toEqual({ id: 6, label: "无名", choosable: false });
    expect(categoryChoices(CATEGORIES, { ...SHIRT, category_id: 6 }, noEnglish, "en")[0]).toEqual({ id: 6, label: "—", choosable: false });
    expect([1, 2, 12].map((id) => categoryUnavailable(CATEGORIES, id))).toEqual([false, false, false]);
    expect([4, 6, 8, 10, 99].map((id) => categoryUnavailable(CATEGORIES, id))).toEqual([true, true, true, true, true]);
  });

  // UX A04「[admin.category_current_unavailable]（当前分类已停用或缺英文名称时）」、视觉稿 A04-states「下拉框仍显示它、可以保留」与本任务验收
  // 「选中的不是可选分类时其下显示 admin.category_current_unavailable」：当前分类为停用的 Seasonal 时下拉框以它为第一项且选中，其下有提示；
  // 改选 Apparel 后提示不显示、Seasonal 仍是第一项；当前分类可选时没有提示。
  it.each(LANGUAGES)("explains an unavailable current category under the select in %s", (language) => {
    const start = editorOf(3, SEASONAL_SHIRT);
    const html = form(render(opened(start, [SEASONAL_TEE, TOTE], CATEGORIES), language));
    const category = element(html, "div", "site-admin-products__placement");
    const labelText = (id: number) => categoryChoices(CATEGORIES, SEASONAL_SHIRT, SEASONAL_TEE, language).find((choice) => choice.id === id)?.label ?? "";
    expect(selectOptions(category)).toEqual([
      ["4", labelText(4), true],
      ["1", labelText(1), false],
      ["2", labelText(2), false],
      ["12", labelText(12), false],
    ]);
    expect(textNodes(element(category, "p", "site-admin-products__unavailable"))).toEqual([COPY["admin.category_current_unavailable"][language]]);
    const moved = form(render(opened(changeCategory(start, 1), [SEASONAL_TEE, TOTE], CATEGORIES), language));
    expect(selectOptions(moved).map(([value, , selected]) => [value, selected])).toEqual([
      ["4", false],
      ["1", true],
      ["2", false],
      ["12", false],
    ]);
    expect(hasClass(moved, "site-admin-products__unavailable")).toBe(false);
    expect(hasClass(form(render(opened(editorOf(3, SHIRT), LISTED, CATEGORIES), language)), "site-admin-products__unavailable")).toBe(false);
  });

  // 本任务 purpose 的落实写法「规格列（admin.variant）为该 SKU 各规格值最近一次读取或保存的名称，按后台界面语言、缺译回退英文，以「 / 」相连，
  // 没有规格名时为 —」：Navy 的中文为 null、马来文为空串时回退英文；L 只有英文；规格值改名后（未保存）规格列仍为读取的名称。
  it("writes the option values column", () => {
    const [blackM, navyM, navyL] = SHIRT.variants;
    if (blackM === undefined || navyM === undefined || navyL === undefined) {
      throw new Error("fixture");
    }
    expect([blackM, navyM, navyL].map((variant) => variantText(SHIRT, variant, "en"))).toEqual(["Black / M", "Navy / M", "Navy / L"]);
    expect([blackM, navyM, navyL].map((variant) => variantText(SHIRT, variant, "zh"))).toEqual(["黑色 / 中", "Navy / 中", "Navy / L"]);
    expect([blackM, navyM, navyL].map((variant) => variantText(SHIRT, variant, "ms"))).toEqual(["Hitam / Sederhana", "Navy / Sederhana", "Navy / L"]);
    const single = TEE_PRODUCT.variants[0];
    expect(single === undefined ? null : variantText(TEE_PRODUCT, single, "zh")).toBe("—");
    const renamed = changeValueName(editorOf(3, SHIRT), 0, 0, "Jet");
    expect(skuRows(renamed, "en").map((row) => row.variant)).toEqual(["Black / M", "Navy / M", "Navy / L"]);
  });

  // UX A04 桌面线框「[admin.sku] [admin.variant] [admin.price] [admin.initial_stock] [admin.sku_enabled] [admin.available_today]」「<sku> <规格值>
  // [M1 ____] [M2 ___] ☑ <n>」、手机线框「SKU 卡片」六项与本任务验收「桌面为表格…手机为每个 SKU 一张卡片（同样六项），两套都渲染」：
  // 表格六个列头；每行为 SKU 代码、规格列、价格与每日初始库存输入框（读取的文字）、启用勾选框、当日可用库存；卡片按同样顺序有六项。
  // 表格在每单限购之前、卡片在其后（照 A04-desktop 与 A04-phone-edit）；没有 admin.adjust_today（SHOP-TASK-208）。
  it.each(LANGUAGES)("renders the SKUs as a desktop table and phone cards in %s", (language) => {
    const html = form(render(opened(changePrice(editorOf(3, SHIRT), 2, "0.5 RM")), language));
    const table = element(html, "div", "site-admin-products__skus");
    expect(textNodes(/<thead>[\s\S]*<\/thead>/.exec(table)?.[0] ?? "")).toEqual(
      (["admin.sku", "admin.variant", "admin.price", "admin.initial_stock", "admin.sku_enabled", "admin.available_today"] as const).map((key) => COPY[key][language]),
    );
    const variants = skuRows(editorOf(3, SHIRT), language).map((row) => row.variant);
    const rows = [...(/<tbody>([\s\S]*)<\/tbody>/.exec(table)?.[1] ?? "").matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => m[1] ?? "");
    expect(rows.map(textNodes)).toEqual([
      ["crew-neck-tee-black-m", variants[0], "18"],
      ["crew-neck-tee-navy-m", variants[1], "0"],
      ["crew-neck-tee-navy-l", variants[2], "3"],
    ]);
    const cells = (row: string) => tags(row, "input").map((tag) => [attributes(tag).get("aria-label"), attributes(tag).get("value") ?? attributes(tag).has("checked")]);
    expect(rows.map(cells)).toEqual(
      [
        ["39.00", "20", true],
        ["0.00", "0", false],
        ["0.5 RM", "7", true],
      ].map(([price, stock, enabled]) => [
        [COPY["admin.price"][language], price],
        [COPY["admin.initial_stock"][language], stock],
        [COPY["admin.sku_enabled"][language], enabled],
      ]),
    );
    const cards = [...element(html, "ul", "site-admin-products__sku-cards").matchAll(/<li\b[^>]*>([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
    expect(cards.map(textNodes)).toEqual(
      [
        ["crew-neck-tee-black-m", variants[0], "18"],
        ["crew-neck-tee-navy-m", variants[1], "0"],
        ["crew-neck-tee-navy-l", variants[2], "3"],
      ].map(([sku, variant, available]) => [
        COPY["admin.sku"][language],
        sku,
        COPY["admin.variant"][language],
        variant,
        COPY["admin.price"][language],
        COPY["admin.initial_stock"][language],
        COPY["admin.sku_enabled"][language],
        COPY["admin.available_today"][language],
        available,
      ]),
    );
    expect(cards.map((card) => tags(card, "input").map((tag) => attributes(tag).get("value") ?? attributes(tag).has("checked")))).toEqual([
      ["39.00", "20", true],
      ["0.00", "0", false],
      ["0.5 RM", "7", true],
    ]);
    const limit = html.indexOf('class="site-admin-products__limit"');
    expect(html.indexOf('class="site-admin-products__skus"')).toBeLessThan(limit);
    expect(html.indexOf('class="site-admin-products__sku-cards"')).toBeGreaterThan(limit);
    expect(tags(table, "button")).toEqual([]);
    expect(tags(element(html, "ul", "site-admin-products__sku-cards"), "button")).toEqual([]);
  });

  // UX A04 线框的常显文字与本任务验收「常显的说明——admin.publish_requirements、admin.product_options 一组下的 admin.options_fixed_note、
  // admin.max_per_order_hint、admin.no_delete_note、admin.initial_stock_note 与 admin.rules_apply_new，位置照视觉稿」「规格名与规格值只能改名称，
  // 不显示去掉的按钮；不显示 common.create、common.save、图片区与当日库存调整的按钮」：说明按视觉稿顺序都在；每个规格名一行（admin.option_name、
  // admin.option_values 与读屏标签 admin.option_value 的规格值输入框）；编辑区里的按钮只有内容语言三个，没有图片。
  it.each(LANGUAGES)("shows the standing notes and no buttons beyond the content languages in %s", (language) => {
    const html = form(render(opened(editorOf(3, SHIRT)), language));
    const nodes = textNodes(html);
    const notes = (
      [
        "admin.publish_requirements",
        "admin.product_options",
        "admin.options_fixed_note",
        "admin.max_per_order_hint",
        "admin.no_delete_note",
        "admin.initial_stock_note",
        "admin.rules_apply_new",
      ] as const
    ).map((key) => nodes.indexOf(COPY[key][language]));
    expect(notes.every((index) => index >= 0)).toBe(true);
    expect([...notes].sort((a, b) => a - b)).toEqual(notes);
    const options = element(html, "div", "site-admin-products__options");
    expect(textNodes(options)).toEqual([
      COPY["admin.product_options"][language],
      COPY["admin.option_name"][language],
      COPY["admin.option_values"][language],
      COPY["admin.option_name"][language],
      COPY["admin.option_values"][language],
      COPY["admin.options_fixed_note"][language],
    ]);
    expect(tags(options, "input").filter((tag) => attributes(tag).get("aria-label") === COPY["admin.option_value"][language])).toHaveLength(4);
    expect(tags(html, "button").map((tag) => attributes(tag).get("aria-pressed"))).toEqual(["true", "false", "false"]);
    expect(html).not.toMatch(/<img\b/);
    expect(tags(html, "input").filter((tag) => attributes(tag).get("type") === "file")).toEqual([]);
    for (const key of ["common.create", "common.save"] as const) {
      expect(nodes).not.toContain(COPY[key][language]);
    }
  });

  // 本任务验收「未保存确认」一条：三语名称与描述、分类、上架、每单限购、规格名与规格值的三语名称、各 SKU 的价格文字、每日初始库存文字与启用状态，
  // 任一项与最近一次读取的值（null 当作空串，价格为读取值写成的文字）不同即为有未保存的修改——未改不算、改了才算、改回原值不算；
  // 只切换内容语言不算；没有编辑区、读取中与读取失败时不算。价格 39.00 改为 39.0 算修改（比较的是文字）。
  it("detects unsaved changes against the last read values", () => {
    const base = editorOf(3, SHIRT);
    expect(isDirty(null)).toBe(false);
    expect(isDirty({ status: "loading", id: 3 })).toBe(false);
    expect(isDirty({ status: "failed", id: 3, error: "common.error_retry" })).toBe(false);
    expect(isDirty(base)).toBe(false);
    expect(isDirty(chooseContent(chooseContent(base, "zh"), "ms"))).toBe(false);
    expect(isDirty(changeName(chooseContent(base, "ms"), ""))).toBe(false);
    const changes: [string, (editor: OpenEditor) => OpenEditor, (editor: OpenEditor) => OpenEditor][] = [
      ["name", (e) => changeName(e, "Tee"), (e) => changeName(e, "Crew Neck Tee")],
      ["null name", (e) => changeName(chooseContent(e, "ms"), "Baju"), (e) => changeName(e, "")],
      ["description", (e) => changeDescription(chooseContent(e, "zh"), "新描述"), (e) => changeDescription(e, "柔软的棉质T恤。")],
      ["category", (e) => changeCategory(e, 2), (e) => changeCategory(e, 1)],
      ["published", (e) => changeActive(e, false), (e) => changeActive(e, true)],
      ["limit", (e) => changeMaxPerOrder(e, "11"), (e) => changeMaxPerOrder(e, "10")],
      ["option name", (e) => changeOptionName(e, 1, "Fit"), (e) => changeOptionName(e, 1, "Size")],
      ["value name", (e) => changeValueName(chooseContent(e, "zh"), 0, 1, "藏青"), (e) => changeValueName(e, 0, 1, "")],
      ["price", (e) => changePrice(e, 0, "39.0"), (e) => changePrice(e, 0, "39.00")],
      ["stock", (e) => changeStock(e, 2, "07"), (e) => changeStock(e, 2, "7")],
      ["enabled", (e) => changeVariantActive(e, 1, true), (e) => changeVariantActive(e, 1, false)],
    ];
    for (const [name, change, revert] of changes) {
      const changed = change(base);
      expect(isDirty(changed), name).toBe(true);
      expect(isDirty(chooseContent(changed, "en")), name).toBe(true);
      expect(isDirty(revert(changed)), name).toBe(false);
    }
  });
});
