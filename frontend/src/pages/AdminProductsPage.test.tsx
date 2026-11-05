import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import type { AdminOption, AdminOptionValue, AdminProduct, AdminProductImage, AdminProductListItem, ProductsRead } from "../api/adminProducts";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PRODUCTS_PATH, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  addOption,
  addSku,
  addValue,
  AdminProductsView,
  canAddOption,
  canAddSku,
  canAddValue,
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
  chooseValue,
  closeEditing,
  createdItem,
  createInput,
  createStep,
  editorOf,
  formOf,
  isChoosable,
  isDirty,
  limitCount,
  localName,
  LOADING,
  MAX_OPTIONS,
  MAX_VALUES,
  MAX_VARIANTS,
  newCategoryChoices,
  newEditor,
  newForm,
  newSkuRows,
  openProduct,
  openProducts,
  optionRemovable,
  priceSen,
  priceText,
  productName,
  productStep,
  publishReady,
  readStep,
  reloadNewProduct,
  reloadProducts,
  removeOption,
  removeSku,
  removeValue,
  rereadStep,
  rereadToken,
  savedItem,
  saveInput,
  saveStep,
  settleEditing,
  showsAddSku,
  skuRemovable,
  skuRows,
  startCreating,
  startEditing,
  startSaving,
  stockCount,
  submitNewProduct,
  submitProduct,
  texts,
  tokenStep,
  translationMissing,
  valueChoices,
  valueRemovable,
  variantText,
  withImages,
} from "./AdminProductsPage";
import type { AdminProductsViewProps, Editing, NewEditor, OpenEditor, ProductForm, ProductsMoves, ProductsState, SaveNotice } from "./AdminProductsPage";

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
  onAddValue: () => undefined,
  onRemoveValue: () => undefined,
  onAddSku: () => undefined,
  onRemoveSku: () => undefined,
  onChooseValue: () => undefined,
  onAddOption: () => undefined,
  onRemoveOption: () => undefined,
  onSave: () => undefined,
  onReload: () => undefined,
  onCreate: () => undefined,
  // 图片区（SHOP-TASK-207）。
  imagesBusy: false,
  onImages: () => undefined,
  onReread: () => Promise.resolve("ok" as const),
  onLogin: () => undefined,
  onImagesBusy: () => undefined,
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

// 编辑区的面板（form，含其内容）。
function form(html: string): string {
  return element(html, "form", "site-admin-products__form");
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

// common.save 旁的五种保存结果（SHOP-TASK-204）。
const NOTICES: readonly SaveNotice[] = ["admin.product_saved", "admin.product_invalid", "admin.product_changed", "common.network_check", "common.error_retry"];

// 写请求的 JSON 请求体与 CSRF 请求头。
function bodyOf(call: Call | undefined): unknown {
  return JSON.parse(typeof call?.init.body === "string" ? call.init.body : "null");
}

function csrfOf(call: Call | undefined): string | null {
  return new Headers(call?.init.headers).get("X-CSRF-Token");
}

// 已取到的编辑区（不是时测试失败）。
function openOf(state: ProductsState): OpenEditor {
  const editing = state.status === "ready" ? state.editing : null;
  if (editing === null || editing.status !== "ready") {
    throw new Error("no open editor");
  }
  return editing;
}

// common.save 与其旁的保存结果（含其内容）。
function saveArea(html: string): string {
  return element(html, "div", "site-admin-products__save");
}

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

// SHOP-TASK-207 改动：编辑区多了图片区，其中 admin.image_count 的 {count}（0–8 张）与 {max}（8）为运行时数据，填好的文字也算字典文案。
function dictionary(language: Language): Set<string> {
  const counts = Array.from({ length: 9 }, (_, count) => formatCopy(COPY["admin.image_count"][language], { count, max: 8 }));
  return new Set<string>([...Object.values(COPY).map((entry) => entry[language]), ...counts]);
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
  // SHOP-TASK-206 改动：UX A04 桌面线框第一行「[admin.nav_products] ( [common.create] )」，标题旁多了 common.create，原来「内容区只有标题」的
  // 文字断言因此改为标题与 common.create（读取中禁用，列表取到后才可点）。
  it.each(LANGUAGES)("starts the content area with the products title while loading in %s", (language) => {
    const main = element(renderSignedIn(language, LOADING), "main", "acs-admin__main");
    expect(tags(main, "h1")).toEqual(['<h1 class="acs-admin__h">']);
    expect(textNodes(main)).toEqual([COPY["admin.nav_products"][language], COPY["common.create"][language]]);
    expect(attributes(tags(element(main, "div", "site-admin-products__title"), "button")[0] ?? "").has("disabled")).toBe(true);
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
    expect(editingOf(run.state)).toEqual({
      status: "ready",
      id: 3,
      product: TEE_PRODUCT,
      base: formOf(TEE_PRODUCT),
      form: formOf(TEE_PRODUCT),
      content: "en",
      saving: false,
      notice: null,
    });
    const html = render(run.state, language);
    const editor = element(html, "div", "site-admin-products__editor");
    expect(attributes(tags(editor, "div")[0] ?? "").get("aria-busy")).toBe("false");
    expect(
      form(editor).startsWith(`<form class="acs-admin__panel site-admin-products__form"><h2 class="acs-admin__h">${escapeHtml(COPY["admin.product_edit"][language])}</h2>`),
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
  // 当前分类不可选的提示与中文、马来文内容语言，以及 SHOP-TASK-204 的保存中与各种保存结果）里，看得见或读得出的每段文字都是当前语言的字典文案，或商品与分类数据本身（名称、描述、
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
      // SHOP-TASK-204：保存中与五种保存结果。
      renderSignedIn(language, opened(startSaving(editorOf(3, SHIRT)), [SEASONAL_TEE, TOTE], CATEGORIES)),
      ...NOTICES.map((notice) => renderSignedIn(language, opened({ ...editorOf(3, SHIRT), notice }, [SEASONAL_TEE, TOTE], CATEGORIES))),
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
    expect(editorOf(3, SHIRT)).toEqual({ status: "ready", id: 3, product: SHIRT, base: expected, form: expected, content: "en", saving: false, notice: null });
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
  // 不显示去掉的按钮；不显示 common.create、图片区与当日库存调整的按钮」：说明按视觉稿顺序都在；每个规格名一行（admin.option_name、
  // admin.option_values 与读屏标签 admin.option_value 的规格值输入框）；编辑区里的按钮只有内容语言三个与（SHOP-TASK-204 起）最后的
  // common.save（UX A04 线框最后一行「( [common.save] )」），没有图片。
  // SHOP-TASK-205 改动：UX A04 线框「[admin.option_values] [<值>__] [<值>__] ( [admin.add_value] )」「( [admin.add_sku] )」，每个规格名的值后多了
  // admin.add_value、SKU 区下方多了 admin.add_sku（桌面与手机各一个），原来「只有内容语言与保存」的按钮断言与规格区的文字顺序因此失效；改为文字顺序
  // 含 admin.add_value，按钮依次为内容语言三个、两个 admin.add_value、两个 admin.add_sku 与 common.save，仍没有 (✗)（没有新增行）。
  // SHOP-TASK-207 改动：UX A04 线框在 admin.publish_requirements 与 admin.product_options 之间多了图片区「( [admin.upload] )」，原来「没有图片区的按钮」
  // 「没有文件输入框」的断言因此失效；改为内容语言之后多一个 admin.upload（这件商品没有图片，没有 (‹)(›)(✗) 与 img），图片区里有一个文件输入框。
  it.each(LANGUAGES)("shows the standing notes and no buttons beyond the content languages, the add buttons and common.save in %s", (language) => {
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
      COPY["admin.add_value"][language],
      COPY["admin.option_name"][language],
      COPY["admin.option_values"][language],
      COPY["admin.add_value"][language],
      COPY["admin.options_fixed_note"][language],
    ]);
    expect(tags(options, "input").filter((tag) => attributes(tag).get("aria-label") === COPY["admin.option_value"][language])).toHaveLength(4);
    const buttons = tags(html, "button");
    expect(buttons.map((tag) => attributes(tag).get("aria-pressed"))).toEqual(["true", "false", "false", undefined, undefined, undefined, undefined, undefined, undefined]);
    const upload = element(html, "div", "site-admin-images__upload");
    expect(tags(upload, "button")).toEqual([buttons[3]]);
    expect(textNodes(upload)).toEqual([COPY["admin.upload"][language]]);
    expect(buttons.slice(4).map((tag) => classes(tag).find((name) => name.startsWith("site-admin-products__add-")))).toEqual([
      "site-admin-products__add-value",
      "site-admin-products__add-value",
      "site-admin-products__add-sku",
      "site-admin-products__add-sku",
      undefined,
    ]);
    expect(attributes(buttons[8] ?? "").get("type")).toBe("submit");
    expect(html).not.toContain(`aria-label="${escapeHtml(COPY["admin.remove"][language])}"`);
    expect(nodes[nodes.length - 1]).toBe(COPY["common.save"][language]);
    expect(html).not.toMatch(/<img\b/);
    expect(tags(element(html, "div", "site-admin-images"), "input").filter((tag) => attributes(tag).get("type") === "file")).toHaveLength(1);
    expect(nodes).not.toContain(COPY["common.create"][language]);
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

  // ---- SHOP-TASK-204 保存已有商品 ----

  // 保存返回的商品：英文名称改了、改到 Bags 分类并下架，version 5。
  const SAVED: AdminProduct = { ...SHIRT, version: 5, name_en: "Crew Neck Tee II", category_id: 2, is_active: false };

  // HANDOFF 0.82 第（3）点「价格照 A06 运费的写法按文字换成整仙（不经浮点数），超过 50,000,000 仙不合格」、UX A04「价格按 RM 输入、至多两位小数，
  // 提交时按文字换成整仙（不经浮点数相乘，与 A06 运费相同）」与「上限提示」「价格 RM0.00–RM500,000.00」：合格的例子换成整仙；0.29 与 1.15 为
  // 29 与 115（浮点数乘 100 会得 28.999… 与 114.999…）；超过上限、三位小数、空串、带空白、负号与非数字都不合格。
  it("turns price text into whole sen without floating point", () => {
    expect(["0", "0.05", "8", "8.5", "12.34", "500000.00", "500000", "0.29", "1.15"].map(priceSen)).toEqual([0, 5, 800, 850, 1234, 50000000, 50000000, 29, 115]);
    const rejected = ["500000.01", "500001", "1.234", "", " ", " 8", "8 ", "-1", "-0.05", "abc", "RM8", "8.", ".5", "1e3", "1,000", "0x10", "1234567890"];
    expect(rejected.map(priceSen)).toEqual(rejected.map(() => null));
  });

  // UX A04「库存与限购只收整数」「上限提示」「每单限购 1–1000」「每日初始库存…0–9,999」与 HANDOFF 0.82 第（3）点「库存为 1–4 位数字，限购 1–1000」：
  // 库存 0 与 9999 合格、10000 不合格；限购 1 与 1000 合格、0 与 1001 不合格；空串、带空白、负号与小数都不合格。任一项不合格时 saveInput 为 null。
  it("checks the stock and limit formats and ranges", () => {
    expect(["0", "7", "0007", "9999"].map(stockCount)).toEqual([0, 7, 7, 9999]);
    const badStock = ["10000", "", "-1", "1.0", " 5", "5 ", "abc"];
    expect(badStock.map(stockCount)).toEqual(badStock.map(() => null));
    expect(["1", "10", "1000", "0001"].map(limitCount)).toEqual([1, 10, 1000, 1]);
    const badLimit = ["0", "1001", "9999", "10000", "", "-1", "1.5", " 10"];
    expect(badLimit.map(limitCount)).toEqual(badLimit.map(() => null));
    const base = editorOf(3, SHIRT);
    for (const editor of [changeStock(base, 2, "9999"), changeStock(base, 2, "0"), changeMaxPerOrder(base, "1"), changeMaxPerOrder(base, "1000"), changePrice(base, 0, "500000.00")]) {
      expect(saveInput(editor.form, 4)).not.toBeNull();
    }
    for (const editor of [
      changeStock(base, 2, "10000"),
      changeMaxPerOrder(base, "0"),
      changeMaxPerOrder(base, "1001"),
      changePrice(base, 0, "500000.01"),
      changePrice(base, 1, "1.234"),
      changePrice(base, 2, ""),
    ]) {
      expect(saveInput(editor.form, 4)).toBeNull();
    }
  });

  // UX A04「上架」「勾选 [admin.active] 时页面先按请求里能看到的部分检查（英文名称与描述、全部规格名与规格值的英文名称、至少一个启用的 SKU），
  // 不满足即不提交…分类是否启用由服务端判断。下架不检查」与 HANDOFF 0.82 第（3）点：上架时英文名称、英文描述、某个规格名或规格值的英文名称
  // 为空或只有空白、或没有启用的 SKU 各自拦下；同样的值下架时不拦；中文、马来文为空不拦；当前分类不可选也不在页面拦。
  it("checks the publish requirements only when published", () => {
    const base = editorOf(3, SHIRT);
    expect(publishReady(base.form)).toBe(true);
    const blocked: [string, OpenEditor][] = [
      ["english name", changeName(base, "  ")],
      ["english description", changeDescription(base, "")],
      ["option name", changeOptionName(base, 1, " ")],
      ["value name", changeValueName(base, 0, 1, "\t")],
      ["no enabled sku", changeVariantActive(changeVariantActive(base, 0, false), 2, false)],
    ];
    for (const [name, editor] of blocked) {
      expect(publishReady(editor.form), name).toBe(false);
      expect(saveInput(editor.form, 4), name).toBeNull();
      expect(saveInput(changeActive(editor, false).form, 4), name).not.toBeNull();
    }
    expect(saveInput(changeName(chooseContent(base, "zh"), "").form, 4)).not.toBeNull();
    expect(saveInput(editorOf(3, SEASONAL_SHIRT).form, 4)).not.toBeNull();
  });

  // UX A04「输入不是这些格式、超出范围时页面不提交、直接显示 [admin.product_invalid]…页面保留已填的值」与本任务验收「不通过时不提交、显示
  // admin.product_invalid、保留已填的值」：没有发出任何请求，编辑区已填的值不变、保存中解除，common.save 旁为 admin.product_invalid。
  it("does not submit when the page check fails", async () => {
    const calls = stubFetch({});
    const editor = startSaving(changeDescription(changePrice(editorOf(3, SHIRT), 0, "39.999"), "New text"));
    const run = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls).toEqual([]);
    expect(openOf(run.state)).toEqual({ ...editor, saving: false, notice: "admin.product_invalid" });
    const area = saveArea(render(run.state, "zh"));
    expect(textNodes(area)).toEqual([COPY["common.save"].zh, COPY["admin.product_invalid"].zh]);
    expect(area).toContain('role="alert"');
  });

  // UX A04「一次保存」与本任务验收「提交——…以 SHOP-TASK-200 的保存提交读取（或上次保存）给的 version、文案原样、下拉框所选的分类 ID、上架、
  // 每单限购、全部规格名与规格值（id 与三语名称，按读取的顺序）与全部 SKU（id、价格整仙、每日初始库存与启用，按读取的顺序），不带 SKU 的规格值组合；
  // CSRF 令牌为列表读取给的」：saveInput 与实际发出的 PUT 请求体相同；首尾空白与换行原样，null 的文案为空串，没有 option_values。
  it("submits every editable field in read order", async () => {
    let editor = editorOf(3, SHIRT);
    editor = changeName(editor, "  Crew Neck Tee  ");
    editor = changeDescription(chooseContent(editor, "zh"), "柔软\n");
    editor = changeValueName(editor, 1, 1, " 大 ");
    editor = chooseContent(editor, "en");
    editor = changeCategory(editor, 2);
    editor = changePrice(editor, 0, "8.5");
    editor = changeStock(editor, 2, "9999");
    editor = changeVariantActive(editor, 1, true);
    editor = changeMaxPerOrder(editor, "1000");
    const expected = {
      version: 4,
      category_id: 2,
      name_en: "  Crew Neck Tee  ",
      name_zh: "圆领T恤",
      name_ms: "",
      description_en: "A soft cotton tee.",
      description_zh: "柔软\n",
      description_ms: "",
      is_active: true,
      max_per_order: 1000,
      options: [
        {
          id: 21,
          name_en: "Colour",
          name_zh: "颜色",
          name_ms: "Warna",
          values: [
            { id: 31, name_en: "Black", name_zh: "黑色", name_ms: "Hitam" },
            { id: 32, name_en: "Navy", name_zh: "", name_ms: "" },
          ],
        },
        {
          id: 22,
          name_en: "Size",
          name_zh: "尺码",
          name_ms: "",
          values: [
            { id: 33, name_en: "M", name_zh: "中", name_ms: "Sederhana" },
            { id: 34, name_en: "L", name_zh: " 大 ", name_ms: "" },
          ],
        },
      ],
      variants: [
        { id: 41, price_sen: 850, daily_initial_stock: 20, is_active: true },
        { id: 42, price_sen: 0, daily_initial_stock: 0, is_active: true },
        { id: 43, price_sen: 5, daily_initial_stock: 9999, is_active: true },
      ],
    };
    expect(saveInput(editor.form, 4)).toStrictEqual(expected);
    const calls = stubFetch({ "/api/admin/products/3": [json(200, SHIRT)] });
    const controller = new AbortController();
    const saving = startSaving(editor);
    await submitProduct("csrf-1", saving, controller.signal, runner(opened(saving)).moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/products/3", "PUT"]]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(csrfOf(calls[0])).toBe("csrf-1");
    expect(bodyOf(calls[0])).toStrictEqual(expected);
    expect(JSON.stringify(bodyOf(calls[0]))).not.toContain("option_values");
  });

  // UX A04「保存结果：成功时页面改显示服务端返回的保存后数据（版本号随之更新），显示 [admin.product_saved]」「有未保存的修改（编辑区的值与最近一次
  // 读取或保存的不同…）」与本任务 purpose「保存成功后列表中该行的名称、分类名称（取自分类读取结果）与状态随返回的商品更新」：编辑区为返回的商品
  // （version 5，新的基线，内容语言不变），不再算有修改；列表该行改为新名称、Bags 与未上架，其他行不变；common.save 旁为 role="status" 的提示。
  // 保存期间又打开了别的商品时，结果不覆盖那个编辑区（列表仍更新）。
  it("shows the saved product and updates its list row", async () => {
    stubFetch({ "/api/admin/products/3": [json(200, SAVED)] });
    const editor = startSaving(chooseContent(changeActive(changeCategory(changeName(editorOf(3, SHIRT), "Crew Neck Tee II"), 2), false), "zh"));
    const run = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
    expect(run.events).toEqual(["update"]);
    const saved = openOf(run.state);
    expect(saved).toEqual({ ...editorOf(3, SAVED), content: "zh", notice: "admin.product_saved" });
    expect(saved.product.version).toBe(5);
    expect(isDirty(saved)).toBe(false);
    const row: AdminProductListItem = {
      ...TEE,
      name_en: "Crew Neck Tee II",
      name_zh: "圆领T恤",
      name_ms: null,
      is_active: false,
      category_id: 2,
      category_name_en: "Bags",
      category_name_zh: null,
      category_name_ms: "",
    };
    expect(run.state.status === "ready" ? run.state.products : null).toEqual([TOTE, row, NAMELESS]);
    const html = render(run.state);
    const table = element(html, "div", "site-admin-products__table");
    expect(textNodes(table).slice(4, 7)).toEqual(["Crew Neck Tee II", "Bags", COPY["admin.unpublished"].en]);
    const area = saveArea(html);
    expect(textNodes(area)).toEqual([COPY["common.save"].en, COPY["admin.product_saved"].en]);
    expect(area).toContain('<span role="status">');
    expect(savedItem(TEE, { ...SHIRT, category_id: 1 }, [])).toEqual({ ...TEE, name_ms: null });
    expect(savedItem(TEE, { ...SHIRT, category_id: 9 }, [])).toMatchObject({ category_id: 9, category_name_en: null, category_name_zh: null, category_name_ms: null });
    const step = saveStep({ kind: "ok", product: SAVED }, 3);
    if (typeof step !== "function") {
      throw new Error("ok is a change");
    }
    const elsewhere = step(ready(LISTED, { status: "loading", id: 5 }));
    expect(editingOf(elsewhere)).toEqual({ status: "loading", id: 5 });
    expect(elsewhere.status === "ready" ? elsewhere.products[1] : null).toEqual(row);
  });

  // UX A04「服务端回答「取值不合格」时同样显示」「两者都整次保存不生效，页面保留已填的值」「服务端回答「商品已被改动」时显示 [admin.product_changed]
  // 与 [admin.reload]」「其他失败显示 [common.error_retry]，保留已填的值」与 HANDOFF 0.82 第（3）点「网络中断只提示、不自动重新读取」：
  // 422、409、500 与网络中断各只发了那一次 PUT，已填的值与 version 不变、仍算有修改，common.save 旁为对应的提示（409 另有 type=button 的 admin.reload）。
  it.each(LANGUAGES)("keeps the values on invalid, changed, other failures and network loss in %s", async (language) => {
    const cases = [
      [json(422, { detail: "product_invalid" }), "admin.product_invalid"],
      [json(409, { detail: "product_changed" }), "admin.product_changed"],
      [json(500, {}), "common.error_retry"],
      ["network", "common.network_check"],
    ] as const;
    for (const [reply, notice] of cases) {
      const calls = stubFetch({ "/api/admin/products/3": [reply] });
      const editor = startSaving(changePrice(editorOf(3, SHIRT), 0, "8.50"));
      const run = runner(opened(editor));
      await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
      expect(calls.map((call) => call.init.method)).toEqual(["PUT"]);
      expect(run.events).toEqual(["update"]);
      expect(openOf(run.state)).toEqual({ ...editor, saving: false, notice });
      expect(isDirty(openOf(run.state))).toBe(true);
      const area = saveArea(render(run.state, language));
      if (notice === "admin.product_changed") {
        expect(textNodes(area)).toEqual([COPY["common.save"][language], COPY["admin.product_changed"][language], COPY["admin.reload"][language]]);
        expect(attributes(tags(area, "button")[1] ?? "").get("type")).toBe("button");
      } else {
        expect(textNodes(area)).toEqual([COPY["common.save"][language], COPY[notice][language]]);
      }
      expect(area).toContain('role="alert"');
    }
  });

  // HANDOFF 0.82 第（3）点与 UX A04「保存结果」：保存 401 时照现有写法以 replace 回到 A01，没有其他请求。
  it("returns to A01 when the save has no session", async () => {
    const calls = stubFetch({ "/api/admin/products/3": [json(401, {})] });
    const editor = startSaving(editorOf(3, SHIRT));
    const run = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls).toHaveLength(1);
    expect(run.events).toEqual([`replace ${LOGIN}`]);
    expect(saveStep({ kind: "none" }, 3)).toBe("login");
    expect(saveStep({ kind: "csrf" }, 3)).toBe("reread");
  });

  // UX A04「点它重新读取，未保存的修改丢弃，不自动合并」「点「已被改动」提示里的 [admin.reload] 是明确放弃修改，不再确认」与本任务 purpose
  // 「admin.reload 整页重新读取（列表与分类）后重新读取同一商品，未保存的修改丢弃」：先显示读取中，依次读取列表、分类与该商品，编辑区为新读取的
  // 商品（照 editorOf，没有提示、不算有修改）。整页读取 401 时回到 A01；其他失败只有提示、不读取商品。
  it("reloads the page and then the same product on admin.reload", async () => {
    const fresh: AdminProduct = { ...SHIRT, version: 6, name_en: "Changed elsewhere" };
    const calls = stubFetch({
      "/api/admin/products": [json(200, { products: LISTED, csrf_token: "csrf-9" })],
      "/api/admin/categories": [CATEGORIES_OK()],
      "/api/admin/products/3": [json(200, fresh)],
    });
    const run = runner(opened({ ...changeName(editorOf(3, SHIRT), "Mine"), notice: "admin.product_changed" }));
    const controller = new AbortController();
    await reloadProducts(3, controller.signal, run.moves);
    expect(run.events).toEqual(["show loading", "show ready", "update"]);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/products", "GET"],
      ["/api/admin/categories", "GET"],
      ["/api/admin/products/3", "GET"],
    ]);
    expect(calls.every((call) => call.init.signal === controller.signal)).toBe(true);
    expect(run.state).toEqual({ ...ready(LISTED, editorOf(3, fresh)), csrfToken: "csrf-9" });
    expect(isDirty(openOf(run.state))).toBe(false);
    stubFetch({ "/api/admin/products": [json(401, {})], "/api/admin/categories": [CATEGORIES_OK()] });
    const login = runner(opened(editorOf(3, SHIRT)));
    await reloadProducts(3, new AbortController().signal, login.moves);
    expect(login.events).toEqual(["show loading", `replace ${LOGIN}`]);
    const failed = stubFetch({ "/api/admin/products": [LIST_OK()], "/api/admin/categories": [json(500, {})] });
    const broken = runner(opened(editorOf(3, SHIRT)));
    await reloadProducts(3, new AbortController().signal, broken.moves);
    expect(failed).toHaveLength(2);
    expect(broken.state).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // HANDOFF 0.82 第（3）点「保存 403 时重新读取取得新令牌，编辑区、已填的值与 version 都保留」与本任务 purpose「显示 common.error_retry」：
  // 403 后重新读取列表与分类，页面换成新列表、新分类与新令牌，编辑区仍是 version 4 的那份、已填的值不变，common.save 旁为 common.error_retry；
  // 下一次保存以新令牌与 version 4 提交；改任何输入后提示收起。
  it("re-reads after a 403 and keeps the editor, values and version", async () => {
    const elsewhere: AdminProductListItem = { ...TEE, name_en: "Elsewhere" };
    const calls = stubFetch({
      "/api/admin/products/3": [json(403, { detail: "csrf_invalid" })],
      "/api/admin/products": [json(200, { products: [elsewhere], csrf_token: "csrf-2" })],
      "/api/admin/categories": [json(200, { categories: [APPAREL, BAGS, KITCHEN], csrf_token: "category-csrf" })],
    });
    const editor = startSaving(changeMaxPerOrder(changeName(editorOf(3, SHIRT), "Mine"), "12"));
    const run = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/products/3", "PUT"],
      ["/api/admin/products", "GET"],
      ["/api/admin/categories", "GET"],
    ]);
    const kept = { ...editor, saving: false, notice: "common.error_retry" as const };
    expect(run.state).toEqual({ ...ready([elsewhere], kept, [APPAREL, BAGS, KITCHEN]), csrfToken: "csrf-2" });
    expect(openOf(run.state).product.version).toBe(4);
    expect(textNodes(saveArea(render(run.state)))).toEqual([COPY["common.save"].en, COPY["common.error_retry"].en]);
    expect(changeName(openOf(run.state), "Mine!").notice).toBeNull();
    const retry = stubFetch({ "/api/admin/products/3": [json(200, SHIRT)] });
    const again = startSaving(openOf(run.state));
    await submitProduct(run.state.status === "ready" ? run.state.csrfToken : "", again, new AbortController().signal, runner(run.state).moves);
    expect(csrfOf(retry[0])).toBe("csrf-2");
    expect(bodyOf(retry[0])).toMatchObject({ version: 4, name_en: "Mine", max_per_order: 12 });
  });

  // 本任务 purpose「该次重新读取为 none 时回到 A01，network 或其他失败时改显示 common.network_check 或 common.error_retry，编辑区照样保留，
  // 令牌仍为旧的」：403 后列表网络中断时为 common.network_check、分类 500 时为 common.error_retry，列表、编辑区与令牌 csrf-1 都不变；
  // 分类 401 时以 replace 回到 A01。
  it("keeps the old token when the re-read after a 403 fails", async () => {
    for (const [products, categories, notice] of [
      ["network", CATEGORIES_OK(), "common.network_check"],
      [LIST_OK(), json(500, {}), "common.error_retry"],
    ] as const) {
      stubFetch({ "/api/admin/products/3": [json(403, {})], "/api/admin/products": [products], "/api/admin/categories": [categories] });
      const editor = startSaving(changeName(editorOf(3, SHIRT), "Mine"));
      const run = runner(opened(editor));
      await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
      expect(run.state).toEqual(opened({ ...editor, saving: false, notice }));
    }
    stubFetch({ "/api/admin/products/3": [json(403, {})], "/api/admin/products": [LIST_OK()], "/api/admin/categories": [json(401, {})] });
    const editor = startSaving(editorOf(3, SHIRT));
    const login = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, login.moves);
    expect(login.events).toEqual([`replace ${LOGIN}`]);
    expect(rereadStep({ kind: "none" }, { kind: "ok", categories: [APPAREL], csrf_token: "category-csrf" }, 3)).toBe("login");
  });

  // 本任务 purpose「这些提示与其他保存结果一样，改任何输入后收起」：五种提示在改名称、描述、分类、上架、限购、规格名、规格值、价格、库存与启用
  // 任一项后都收起；只切换内容语言不算改输入，提示保留。
  it("clears the save result on any input change", () => {
    const changes: ((editor: OpenEditor) => OpenEditor)[] = [
      (e) => changeName(e, "x"),
      (e) => changeDescription(e, "x"),
      (e) => changeCategory(e, 2),
      (e) => changeActive(e, false),
      (e) => changeMaxPerOrder(e, "9"),
      (e) => changeOptionName(e, 0, "x"),
      (e) => changeValueName(e, 0, 0, "x"),
      (e) => changePrice(e, 0, "1"),
      (e) => changeStock(e, 0, "1"),
      (e) => changeVariantActive(e, 0, false),
    ];
    for (const notice of NOTICES) {
      const noticed: OpenEditor = { ...editorOf(3, SHIRT), notice };
      for (const change of changes) {
        expect(change(noticed).notice, notice).toBeNull();
      }
      expect(chooseContent(noticed, "ms").notice).toBe(notice);
    }
  });

  // UX A04「保存中按钮禁用，防重复提交」、视觉稿 A04-states「保存中：Save 禁用」与本任务验收「保存中禁用并标 aria-busy」：未保存时 common.save
  // 为 type=submit、可用、aria-busy=false；startSaving 后禁用、aria-busy=true，先前的提示收起。
  it("disables common.save while saving", () => {
    const idle = tags(saveArea(render(opened(editorOf(3, SHIRT)))), "button")[0] ?? "";
    expect(attributes(idle).get("type")).toBe("submit");
    expect(attributes(idle).has("disabled")).toBe(false);
    expect(attributes(idle).get("aria-busy")).toBe("false");
    const saving = startSaving({ ...editorOf(3, SHIRT), notice: "admin.product_saved" });
    expect(saving.saving).toBe(true);
    expect(saving.notice).toBeNull();
    const area = saveArea(render(opened(saving)));
    const busy = tags(area, "button")[0] ?? "";
    expect(attributes(busy).has("disabled")).toBe(true);
    expect(attributes(busy).get("aria-busy")).toBe("true");
    expect(textNodes(area)).toEqual([COPY["common.save"].en]);
  });

  // ---- SHOP-TASK-205 新增规格值与 SKU ----

  // 已有商品 SHIRT 上：Colour 新增 Olive（v1，位置 2）与 Sand（v2，位置 3），一行新增 SKU（s1，位置 3）选 Olive / L，价格与库存未填。
  const withNewRows = (): OpenEditor => {
    let editor = addValue(addValue(editorOf(3, SHIRT), 0), 0);
    editor = changeValueName(editor, 0, 2, "Olive");
    editor = changeValueName(editor, 0, 3, "Sand");
    editor = addSku(editor);
    return chooseValue(chooseValue(editor, 3, 0, "v1"), 3, 1, "34");
  };

  // class 含 className 的按钮（开始标签）。
  const buttonsOf = (html: string, className: string): string[] => tags(html, "button").filter((tag) => classes(tag).includes(className));

  // UX A04「规格」「每个规格名 1–20 个值，[admin.add_value] 达 20 个后禁用」与本任务验收「每个规格名的值后为 admin.add_value，该规格名下已有的与新增的值
  // 合计达 20 个时禁用」：Colour 已有 2 个，新增到 19 个时两个 admin.add_value 都可用；第 20 个之后 Colour 的禁用、再加不变，Size 的不受影响。
  it("disables admin.add_value at 20 values per option", () => {
    let editor = editorOf(3, SHIRT);
    for (let count = 2; count < MAX_VALUES - 1; count += 1) {
      editor = addValue(editor, 0);
    }
    expect(editor.form.options[0]?.values).toHaveLength(19);
    const disabled = (e: OpenEditor) => buttonsOf(form(render(opened(e))), "site-admin-products__add-value").map((tag) => attributes(tag).has("disabled"));
    expect(disabled(editor)).toEqual([false, false]);
    const full = addValue(editor, 0);
    expect(MAX_VALUES).toBe(20);
    expect(full.form.options[0]?.values).toHaveLength(20);
    expect(full.form.options.map(canAddValue)).toEqual([false, true]);
    expect(disabled(full)).toEqual([true, false]);
    expect(addValue(full, 0)).toBe(full);
  });

  // UX A04「SKU」「一件商品至多 50 个 SKU，达到后按钮禁用」与本任务验收「该商品已有的与新增的 SKU 合计达 50 个时禁用」：已有 3 个，新增到 49 个时
  // 桌面与手机的 admin.add_sku 都可用；第 50 个之后都禁用、再加不变。
  it("disables admin.add_sku at 50 SKUs", () => {
    let editor = editorOf(3, SHIRT);
    for (let count = 3; count < MAX_VARIANTS - 1; count += 1) {
      editor = addSku(editor);
    }
    expect(editor.form.variants).toHaveLength(49);
    const disabled = (e: OpenEditor) => buttonsOf(form(render(opened(e))), "site-admin-products__add-sku").map((tag) => attributes(tag).has("disabled"));
    expect(disabled(editor)).toEqual([false, false]);
    const full = addSku(editor);
    expect(MAX_VARIANTS).toBe(50);
    expect(full.form.variants).toHaveLength(50);
    expect(canAddSku(full.form)).toBe(false);
    expect(disabled(full)).toEqual([true, true]);
    expect(addSku(full)).toBe(full);
  });

  // UX A04「SKU」「没有规格名的商品固定一个 SKU，不显示 [admin.add_sku]」与本任务验收「没有规格名的商品不显示」：没有规格名的商品既没有 admin.add_sku
  // 按钮也没有它的文字，addSku 不变；有规格名的商品桌面与手机各一个。
  it.each(LANGUAGES)("hides admin.add_sku for a product without options in %s", (language) => {
    const single = editorOf(3, TEE_PRODUCT);
    expect(showsAddSku(single.form)).toBe(false);
    expect(canAddSku(single.form)).toBe(false);
    expect(addSku(single)).toBe(single);
    const html = form(render(opened(single), language));
    expect(buttonsOf(html, "site-admin-products__add-sku")).toEqual([]);
    expect(textNodes(html)).not.toContain(COPY["admin.add_sku"][language]);
    const shirt = textNodes(form(render(opened(editorOf(3, SHIRT)), language)));
    expect(shirt.filter((text) => text === COPY["admin.add_sku"][language])).toHaveLength(2);
  });

  // HANDOFF 0.82 第（4）点「新增 SKU 的规格值下拉框首项为空选项、默认选它」与本任务 purpose「每个规格名一个下拉框（读屏标签 admin.option_value；首项为值与
  // 文字都为空的空选项，默认选它；其余为该规格名下已有的与本次新增的值，文字按后台界面语言、缺译回退英文，都为空时为空文字）」：中文界面下 Colour 为
  // 空选项、黑色、Navy（缺中文回退英文）、新增的 Olive（只有英文）与名称全空的新增值（空文字），Size 为空选项、中、L；桌面与手机都默认选空选项。
  it("lists an empty choice first, then existing and new values", () => {
    let editor = addValue(addValue(editorOf(3, SHIRT), 0), 0);
    editor = changeValueName(editor, 0, 2, "Olive");
    editor = addSku(editor);
    const colour = [
      ["", "", true],
      ["31", "黑色", false],
      ["32", "Navy", false],
      ["v1", "Olive", false],
      ["v2", "", false],
    ];
    const size = [
      ["", "", true],
      ["33", "中", false],
      ["34", "L", false],
    ];
    expect(newSkuRows(editor, "zh")).toEqual([
      {
        index: 3,
        form: editor.form.variants[3],
        selects: [colour, size].map((choices) => ({ choices: choices.map(([value, label]) => ({ value, label })), selected: "" })),
      },
    ]);
    const sizeOption = editor.form.options[1];
    expect(sizeOption === undefined ? null : valueChoices(sizeOption, "ms")).toEqual([
      { value: "", label: "" },
      { value: "33", label: "Sederhana" },
      { value: "34", label: "L" },
    ]);
    const html = form(render(opened(editor), "zh"));
    for (const part of [element(html, "div", "site-admin-products__skus"), element(html, "ul", "site-admin-products__sku-cards")]) {
      const selects = [...part.matchAll(/<select\b[^>]*>[\s\S]*?<\/select>/g)].map((m) => m[0]);
      expect(selects.map((select) => attributes(tags(select, "select")[0] ?? "").get("aria-label"))).toEqual([COPY["admin.option_value"].zh, COPY["admin.option_value"].zh]);
      expect(selects.map(selectOptions)).toEqual([colour, size]);
    }
    const chosen = form(render(opened(chooseValue(editor, 3, 0, "v1")), "zh"));
    const first = /<select\b[^>]*>[\s\S]*?<\/select>/.exec(element(chosen, "div", "site-admin-products__skus"))?.[0] ?? "";
    expect(selectOptions(first).filter(([, , selected]) => selected).map(([value]) => value)).toEqual(["v1"]);
  });

  // UX A04「规格」「本次新增、还没保存的规格值与 SKU 可用 (✗) 去掉…被本次新增的 SKU 选用的规格值…的 (✗) 禁用，先去掉或改选那些 SKU」、视觉稿 A04-desktop
  // 的标注「被新 SKU 选用的规格值（此例 Olive）的 (✕) 禁用…未被选用的（此例 Sand）可去掉」与 HANDOFF 0.82 第（4）点「A04 的 (✗) 没有专用读屏标签，
  // 沿用 A06 的 admin.remove」：已有的值没有 (✗)；被新增 SKU 选用的 Olive 禁用、Sand 可去掉；改选或去掉那行 SKU 后 Olive 也可去掉；
  // 新增 SKU 的 (✗) 在桌面末行与手机卡片各一个、可用，已有 SKU 不能去掉。
  it.each(LANGUAGES)("disables (✗) on new values chosen by a new SKU in %s", (language) => {
    const editor = withNewRows();
    expect([0, 1, 2, 3].map((index) => valueRemovable(editor.form, 0, index))).toEqual([false, false, false, true]);
    expect([0, 1].map((index) => valueRemovable(editor.form, 1, index))).toEqual([false, false]);
    const html = form(render(opened(editor), language));
    const removes = (part: string) => buttonsOf(part, "site-admin-products__remove").map((tag) => [attributes(tag).get("aria-label"), attributes(tag).get("type"), attributes(tag).has("disabled")]);
    const label = COPY["admin.remove"][language];
    expect(removes(element(html, "div", "site-admin-products__options"))).toEqual([
      [label, "button", true],
      [label, "button", false],
    ]);
    expect(removes(element(html, "div", "site-admin-products__skus"))).toEqual([[label, "button", false]]);
    expect(removes(element(html, "ul", "site-admin-products__sku-cards"))).toEqual([[label, "button", false]]);
    const names = (e: OpenEditor) => e.form.options[0]?.values.map((value) => value.names.en);
    expect(removeValue(editor, 0, 2)).toBe(editor);
    expect(removeValue(editor, 0, 0)).toBe(editor);
    expect(names(removeValue(editor, 0, 3))).toEqual(["Black", "Navy", "Olive"]);
    const unchosen = chooseValue(editor, 3, 0, "");
    expect(valueRemovable(unchosen.form, 0, 2)).toBe(true);
    expect(names(removeValue(unchosen, 0, 2))).toEqual(["Black", "Navy", "Sand"]);
    const dropped = removeSku(editor, 3);
    expect(dropped.form.variants).toHaveLength(3);
    expect(valueRemovable(dropped.form, 0, 2)).toBe(true);
    expect(removeSku(editor, 0)).toBe(editor);
    expect(chooseValue(editor, 0, 0, "32")).toBe(editor);
  });

  // 本任务 purpose「新增 SKU 一行为每个规格名一个下拉框…、admin.sku_code_on_save、价格与每日初始库存输入框（初值空串）、admin.sku_enabled 勾选框（默认勾选）、
  // 当日可用库存显示 —、(✗)」与验收「桌面在表格末尾、手机为一张卡片」「已有 SKU 的规格值组合仍只显示、不能改」：新增行排在三个已有行之后，已有行仍没有下拉框；
  // 表格多一列（列头为空）；手机卡片同样排在最后，为 admin.sku_code_on_save 与 (✗)、两个下拉框、价格与库存、启用与当日可用库存 —。
  it.each(LANGUAGES)("renders a new SKU at the end of the table and as a card in %s", (language) => {
    const editor = addSku(editorOf(3, SHIRT));
    expect(editor.form.variants[3]).toEqual({ tempId: "s1", choices: ["", ""], price: "", stock: "", isActive: true });
    const html = form(render(opened(editor), language));
    const withoutSelects = (part: string) => textNodes(part.replace(/<select\b[\s\S]*?<\/select>/g, ""));
    const table = element(html, "div", "site-admin-products__skus");
    const thead = /<thead>[\s\S]*<\/thead>/.exec(table)?.[0] ?? "";
    expect(tags(thead, "th")).toHaveLength(7);
    expect(textNodes(thead)).toHaveLength(6);
    const rows = [...(/<tbody>([\s\S]*)<\/tbody>/.exec(table)?.[1] ?? "").matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => m[1] ?? "");
    expect(rows).toHaveLength(4);
    expect(rows.slice(0, 3).map((row) => tags(row, "select"))).toEqual([[], [], []]);
    const last = rows[3] ?? "";
    expect(withoutSelects(last)).toEqual([COPY["admin.sku_code_on_save"][language], "—"]);
    expect(tags(last, "select")).toHaveLength(2);
    expect(tags(last, "input").map((tag) => [attributes(tag).get("aria-label"), attributes(tag).get("value") ?? attributes(tag).has("checked")])).toEqual([
      [COPY["admin.price"][language], ""],
      [COPY["admin.initial_stock"][language], ""],
      [COPY["admin.sku_enabled"][language], true],
    ]);
    const cards = [...element(html, "ul", "site-admin-products__sku-cards").matchAll(/<li\b[^>]*>([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
    expect(cards).toHaveLength(4);
    const card = cards[3] ?? "";
    expect(withoutSelects(card)).toEqual([
      COPY["admin.sku_code_on_save"][language],
      COPY["admin.price"][language],
      COPY["admin.initial_stock"][language],
      COPY["admin.sku_enabled"][language],
      COPY["admin.available_today"][language],
      "—",
    ]);
    expect(tags(card, "select")).toHaveLength(2);
    expect(tags(card, "input").map((tag) => attributes(tag).get("value") ?? attributes(tag).has("checked"))).toEqual(["", "", true]);
  });

  // 本任务验收「缺译提示（admin.translation_missing）把新增的值也算进去」与 purpose「新增规格值为一组三语名称（随内容语言显示该语言的输入框，读屏标签
  // admin.option_value）」：三语齐全的商品在中文内容语言下新增一个值即有缺译提示，填了中文后没有、换到马来文又有；新增值的输入框显示内容语言的名称。
  it("counts new values in the translation notice", () => {
    const full = chooseContent(editorOf(3, FULL), "zh");
    expect(translationMissing(full)).toBe(false);
    const added = addValue(full, 0);
    expect(translationMissing(added)).toBe(true);
    const named = changeValueName(added, 0, 2, "橄榄");
    expect(translationMissing(named)).toBe(false);
    expect(translationMissing(chooseContent(named, "ms"))).toBe(true);
    const html = form(render(opened(named)));
    const input = tags(element(html, "span", "site-admin-products__new-value"), "input").map((tag) => [
      attributes(tag).get("aria-label"),
      attributes(tag).get("value"),
      attributes(tag).get("lang"),
    ]);
    expect(input).toEqual([[COPY["admin.option_value"].en, "橄榄", "zh-Hans"]]);
  });

  // 本任务 purpose「新增 SKU 任一规格名未选值时页面先查拦下、显示 admin.product_invalid；新增规格值的英文名称是否为空与重复、组合是否重复由服务端判断」与验收
  // 「页面先查扩展到新增 SKU（价格、每日初始库存的格式与范围；任一规格名未选值即不合格；上架时启用的 SKU 计入新增的）与新增规格值（上架时英文名称不为空）」：
  // 未选值、价格或库存为空或超出范围时上架与下架都拦下；新增值英文名称为空只在上架时拦下，与已有值重名不拦；只有新增 SKU 启用时仍满足上架条件；
  // 拦下时没有请求、显示 admin.product_invalid。
  it("checks the new rows before submitting", async () => {
    const filled = changeStock(changePrice(withNewRows(), 3, "39.00"), 3, "12");
    expect(saveInput(filled.form, 4)).not.toBeNull();
    const blocked: [string, OpenEditor][] = [
      ["no colour", chooseValue(filled, 3, 0, "")],
      ["no size", chooseValue(filled, 3, 1, "")],
      ["empty price", changePrice(filled, 3, "")],
      ["price over the cap", changePrice(filled, 3, "500000.01")],
      ["empty stock", changeStock(filled, 3, "")],
      ["stock over the cap", changeStock(filled, 3, "10000")],
    ];
    for (const [name, editor] of blocked) {
      expect(saveInput(editor.form, 4), name).toBeNull();
      expect(saveInput(changeActive(editor, false).form, 4), name).toBeNull();
    }
    const blankValue = changeValueName(filled, 0, 3, " ");
    expect(publishReady(blankValue.form)).toBe(false);
    expect(saveInput(blankValue.form, 4)).toBeNull();
    expect(saveInput(changeActive(blankValue, false).form, 4)).not.toBeNull();
    expect(saveInput(changeValueName(filled, 0, 3, "Black").form, 4)).not.toBeNull();
    const onlyNew = changeVariantActive(changeVariantActive(filled, 0, false), 2, false);
    expect(publishReady(onlyNew.form)).toBe(true);
    expect(publishReady(changeVariantActive(onlyNew, 3, false).form)).toBe(false);
    const calls = stubFetch({});
    const saving = startSaving(chooseValue(filled, 3, 1, ""));
    const run = runner(opened(saving));
    await submitProduct("csrf-1", saving, new AbortController().signal, run.moves);
    expect(calls).toEqual([]);
    expect(openOf(run.state)).toEqual({ ...saving, saving: false, notice: "admin.product_invalid" });
    expect(textNodes(saveArea(render(run.state)))).toEqual([COPY["common.save"].en, COPY["admin.product_invalid"].en]);
  });

  // DESIGN 1.18 第 11 条「新增的规格值与 SKU 各带请求内的临时编号，按请求顺序排在已有的之后」、本任务验收「保存请求在已有的规格值之后按新增顺序写出新增规格值
  // （temp_id 与三语名称），在已有的 SKU 之后按新增顺序写出新增 SKU（temp_id、option_values 按规格名的顺序，已有的值以 id、新增的值以 temp_id 引用，价格整仙、
  // 每日初始库存与启用）」与 purpose「临时编号由页面按新增顺序生成、在一次请求里不重复（如规格值 v1、v2，SKU s1、s2）」：先加后去掉的值与 SKU 不进请求，
  // 之后新增的编号继续递增；saveInput 与实际 PUT 的请求体相同，临时编号互不重复。
  it("writes the new values and SKUs after the existing ones with unique temporary ids", async () => {
    let editor = changeValueName(addValue(editorOf(3, SHIRT), 0), 0, 2, "Olive");
    editor = changeValueName(chooseContent(addValue(editor, 1), "zh"), 1, 2, "特大");
    editor = changeValueName(chooseContent(editor, "en"), 1, 2, "XL");
    editor = removeValue(addValue(editor, 0), 0, 3);
    editor = removeSku(addSku(addSku(editor)), 3);
    editor = chooseValue(chooseValue(editor, 3, 0, "v1"), 3, 1, "34");
    editor = changeStock(changePrice(editor, 3, "39"), 3, "12");
    editor = addSku(editor);
    editor = chooseValue(chooseValue(editor, 4, 0, "31"), 4, 1, "v2");
    editor = changeVariantActive(changeStock(changePrice(editor, 4, "42.5"), 4, "0"), 4, false);
    const expected = {
      version: 4,
      category_id: 1,
      name_en: "Crew Neck Tee",
      name_zh: "圆领T恤",
      name_ms: "",
      description_en: "A soft cotton tee.",
      description_zh: "柔软的棉质T恤。",
      description_ms: "",
      is_active: true,
      max_per_order: 10,
      options: [
        {
          id: 21,
          name_en: "Colour",
          name_zh: "颜色",
          name_ms: "Warna",
          values: [
            { id: 31, name_en: "Black", name_zh: "黑色", name_ms: "Hitam" },
            { id: 32, name_en: "Navy", name_zh: "", name_ms: "" },
            { temp_id: "v1", name_en: "Olive", name_zh: "", name_ms: "" },
          ],
        },
        {
          id: 22,
          name_en: "Size",
          name_zh: "尺码",
          name_ms: "",
          values: [
            { id: 33, name_en: "M", name_zh: "中", name_ms: "Sederhana" },
            { id: 34, name_en: "L", name_zh: "", name_ms: "" },
            { temp_id: "v2", name_en: "XL", name_zh: "特大", name_ms: "" },
          ],
        },
      ],
      variants: [
        { id: 41, price_sen: 3900, daily_initial_stock: 20, is_active: true },
        { id: 42, price_sen: 0, daily_initial_stock: 0, is_active: false },
        { id: 43, price_sen: 5, daily_initial_stock: 7, is_active: true },
        { temp_id: "s2", option_values: [{ temp_id: "v1" }, { id: 34 }], price_sen: 3900, daily_initial_stock: 12, is_active: true },
        { temp_id: "s3", option_values: [{ id: 31 }, { temp_id: "v2" }], price_sen: 4250, daily_initial_stock: 0, is_active: false },
      ],
    };
    expect(saveInput(editor.form, 4)).toStrictEqual(expected);
    const calls = stubFetch({ "/api/admin/products/3": [json(200, SHIRT)] });
    const saving = startSaving(editor);
    await submitProduct("csrf-1", saving, new AbortController().signal, runner(opened(saving)).moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/products/3", "PUT"]]);
    expect(bodyOf(calls[0])).toStrictEqual(expected);
    const declared = [...expected.options.flatMap((option) => option.values), ...expected.variants].flatMap((row) => ("temp_id" in row ? [row.temp_id] : []));
    expect(declared).toEqual(["v1", "v2", "s2", "s3"]);
    expect(new Set(declared).size).toBe(declared.length);
  });

  // 本任务 purpose「保存成功后以返回的商品为准（新增的变为已有的）」与验收「ok 时以返回的商品为准，新增行全部变为已有行」：返回的商品里 Olive 为 35、
  // 新 SKU 为 44（当日可用库存等于每日初始库存 12，UX A04「SKU」）；编辑区照 editorOf 换成它，没有新增行、没有 (✗)、不算有修改，规格列与 SKU 代码取自返回的商品。
  it("turns the new rows into existing ones after a successful save", async () => {
    const olive: AdminOptionValue = { id: 35, code: "olive", name_en: "Olive", name_zh: null, name_ms: null };
    const saved: AdminProduct = {
      ...SHIRT,
      version: 5,
      options: [{ ...COLOUR, values: [BLACK, NAVY, olive] }, SIZE],
      variants: [
        ...SHIRT.variants,
        { id: 44, sku: "crew-neck-tee-olive-l", price_sen: 3900, daily_initial_stock: 12, available_stock: 12, is_active: true, option_value_ids: [35, 34] },
      ],
    };
    stubFetch({ "/api/admin/products/3": [json(200, saved)] });
    const editor = startSaving(changeStock(changePrice(removeValue(withNewRows(), 0, 3), 3, "39.00"), 3, "12"));
    const run = runner(opened(editor));
    await submitProduct("csrf-1", editor, new AbortController().signal, run.moves);
    const after = openOf(run.state);
    expect(after).toEqual({ ...editorOf(3, saved), notice: "admin.product_saved" });
    expect(isDirty(after)).toBe(false);
    expect(newSkuRows(after, "en")).toEqual([]);
    expect(after.form.options[0]?.values[2]).toEqual({ id: 35, names: { en: "Olive", zh: "", ms: "" } });
    expect(skuRows(after, "en").map((row) => [row.sku, row.variant, row.available])[3]).toEqual(["crew-neck-tee-olive-l", "Olive / L", 12]);
    const html = form(render(run.state));
    expect(textNodes(html)).not.toContain(COPY["admin.sku_code_on_save"].en);
    expect(buttonsOf(html, "site-admin-products__remove")).toEqual([]);
  });

  // 本任务验收「有任一新增规格值或新增 SKU 即为有未保存的修改（扩展 SHOP-TASK-203 的导出判定函数）；admin.reload 与切换商品丢弃新增行」：新增一个值或一行
  // SKU 即算修改（切换内容语言后仍算），加了又去掉不算；重新读取（admin.reload 与打开商品都照 productStep）得到的编辑区没有新增行；点另一件商品的
  // common.edit 后编辑区换成读取中，不再有修改。
  it("counts new rows as unsaved changes and drops them on reload or switching", () => {
    const base = editorOf(3, SHIRT);
    expect(isDirty(addValue(base, 1))).toBe(true);
    expect(isDirty(addSku(base))).toBe(true);
    expect(isDirty(chooseContent(addSku(base), "zh"))).toBe(true);
    expect(isDirty(removeValue(addValue(base, 1), 1, 2))).toBe(false);
    expect(isDirty(removeSku(addSku(base), 3))).toBe(false);
    const changed = withNewRows();
    expect(isDirty(changed)).toBe(true);
    const reread = productStep({ kind: "ok", product: SHIRT }, 3);
    expect(reread).toEqual(editorOf(3, SHIRT));
    expect(isDirty(reread === "login" ? null : reread)).toBe(false);
    const switched = startEditing(opened(changed), 5);
    expect(switched === null ? null : editingOf(switched)).toEqual({ status: "loading", id: 5 });
    expect(isDirty(switched === null ? null : editingOf(switched))).toBe(false);
  });

  // 本任务验收「页面文字全部来自字典」：有新增规格值与新增 SKU（含中文内容语言与保存中）时，看得见或读得出的每段文字都是当前语言的字典文案，或商品数据本身
  // （名称、描述、SKU 代码、规格列、当日可用库存、下拉框里的规格值名称与新增 SKU 当日可用库存的 —）。
  it.each(LANGUAGES)("shows only dictionary text with new rows in %s", (language) => {
    const allowed = dictionary(language);
    allowed.add("—");
    const editors = [withNewRows(), chooseContent(addSku(withNewRows()), "zh"), startSaving(withNewRows())];
    for (const item of LISTED) {
      allowed.add(productName(item, language));
      allowed.add(categoryName(item, language));
    }
    for (const label of ["common.lang_zh", "common.lang_ms"] as const) {
      allowed.add(COPY["admin.translation_missing"][language].replace("{language}", COPY[label][language]));
    }
    for (const editor of editors) {
      allowed.add(editor.form.descriptions[editor.content]);
      for (const choice of categoryChoices([APPAREL, BAGS], editor.product, TEE, language)) {
        allowed.add(choice.label);
      }
      for (const row of skuRows(editor, language)) {
        allowed.add(row.sku);
        allowed.add(row.variant);
        allowed.add(String(row.available));
      }
      for (const row of newSkuRows(editor, language)) {
        for (const select of row.selects) {
          for (const choice of select.choices) {
            allowed.add(choice.label);
          }
        }
      }
    }
    for (const editor of editors) {
      for (const value of visibleTexts(renderSignedIn(language, opened(editor)))) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });

  // ---- SHOP-TASK-206 新建商品 ----

  // 新建表单（不是时测试失败）。
  const newOf = (state: ProductsState): NewEditor => {
    const editing = editingOf(state);
    if (editing === null || editing.status !== "new") {
      throw new Error("no new product form");
    }
    return editing;
  };

  // (✗) 的读屏标签、type 与是否禁用。
  const removesOf = (part: string) => buttonsOf(part, "site-admin-products__remove").map((tag) => [attributes(tag).get("aria-label"), attributes(tag).get("type"), attributes(tag).has("disabled")]);

  // 每个 select 的选项（value、文字、是否选中）。
  const selectOptionsOf = (html: string): [string, string, boolean][][] => [...html.matchAll(/<select\b[^>]*>[\s\S]*?<\/select>/g)].map((m) => selectOptions(m[0]));

  // tbody 的各行（不含 tr 标签）。
  const bodyRows = (table: string): string[] => [...(/<tbody>([\s\S]*)<\/tbody>/.exec(table)?.[1] ?? "").matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => m[1] ?? "");

  // 没有规格名的新建表单：名称 Insulated Bottle、分类 Bags、价格 15.00、每日初始库存 40（视觉稿 A04-desktop-new 下半的例子）。
  const filledNew = (): NewEditor => changeStock(changePrice(changeCategory(changeName(newEditor([APPAREL, BAGS]), "Insulated Bottle"), 2), 0, "15.00"), 0, "40");

  // 新建返回的商品（201）：ID 9、分类 Bags、未上架、一个 SKU（代码由服务端生成）。
  const CREATED: AdminProduct = {
    ...product(9, "Insulated Bottle"),
    slug: "insulated-bottle",
    version: 1,
    category_id: 2,
    description_en: null,
    is_active: false,
    variants: [{ id: 51, sku: "insulated-bottle-std", price_sen: 1500, daily_initial_stock: 40, available_stock: 40, is_active: true, option_value_ids: [] }],
  };

  // UX A04 桌面线框第一行「[admin.nav_products] ( [common.create] )」「[admin.product_edit]（新建时为 [admin.product_new]）」、HANDOFF 0.82 第（5）点
  // 「新建表单：分类默认第一项，上架不勾选，限购 10，没有规格名时 SKU 表固定一行」与本任务 purpose 的初值（文案全为空串；规格名为零个，显示 admin.no_options；
  // SKU 表固定一行：admin.sku_code_on_save、价格与每日初始库存为空串、admin.sku_enabled 勾选、当日可用库存 —，没有下拉框与 (✗)）：列表取到后
  // common.create 可点；startCreating 打开空白表单（分类为第一个可选的分类 Apparel，停用的 Seasonal 不算）；面板以 h2 admin.product_new 开始，
  // 不显示 admin.category_current_unavailable、admin.add_sku 与任何 (✗)；规格区为 admin.no_options 与 admin.add_option。列表还没取到时不变。
  it.each(LANGUAGES)("opens a blank new product form with a single SKU row in %s", (language) => {
    expect(attributes(tags(element(render(ready(), language), "div", "site-admin-products__title"), "button")[0] ?? "").has("disabled")).toBe(false);
    expect(startCreating(LOADING)).toBeNull();
    expect(startCreating(ready())).toEqual(ready(LISTED, newEditor([APPAREL, BAGS])));
    expect(newForm(CATEGORIES)).toEqual({
      names: { en: "", zh: "", ms: "" },
      descriptions: { en: "", zh: "", ms: "" },
      categoryId: 1,
      isActive: false,
      maxPerOrder: "10",
      options: [],
      variants: [{ tempId: "s1", choices: [], price: "", stock: "", isActive: true }],
    });
    const state = startCreating(ready(LISTED, null, CATEGORIES));
    expect(state === null ? null : newOf(state)).toEqual({ status: "new", base: newForm(CATEGORIES), form: newForm(CATEGORIES), content: "en", saving: false, notice: null });
    const html = render(ready(LISTED, newEditor(CATEGORIES), CATEGORIES), language);
    expect(rootClasses(html)).toContain("site-admin-products--editing");
    const panel = form(html);
    expect(panel.startsWith(`<form class="acs-admin__panel site-admin-products__form"><h2 class="acs-admin__h">${escapeHtml(COPY["admin.product_new"][language])}</h2>`)).toBe(true);
    expect(inputValue(field(panel, COPY["admin.product_name"][language]))).toBe("");
    expect(textareaValue(field(panel, COPY["admin.product_description"][language]))).toBe("");
    expect(selectOptions(field(panel, COPY["admin.product_category"][language]))).toEqual(
      newCategoryChoices(CATEGORIES, language).map((choice, index) => [String(choice.id), choice.label, index === 0]),
    );
    expect(newCategoryChoices(CATEGORIES, "en").map((choice) => choice.id)).toEqual([1, 2, 12]);
    expect(isChecked(field(panel, COPY["admin.active"][language]))).toBe(false);
    expect(inputValue(field(panel, COPY["admin.max_per_order"][language]))).toBe("10");
    expect(textNodes(panel)).not.toContain(COPY["admin.category_current_unavailable"][language]);
    expect(textNodes(element(panel, "div", "site-admin-products__options"))).toEqual([
      COPY["admin.product_options"][language],
      COPY["admin.no_options"][language],
      COPY["admin.add_option"][language],
      COPY["admin.options_fixed_note"][language],
    ]);
    const table = element(panel, "div", "site-admin-products__skus");
    expect(tags(table, "th")).toHaveLength(6);
    const rows = bodyRows(table);
    expect(rows).toHaveLength(1);
    expect(textNodes(rows[0] ?? "")).toEqual([COPY["admin.sku_code_on_save"][language], "—", "—"]);
    expect(tags(rows[0] ?? "", "input").map((tag) => attributes(tag).get("value") ?? attributes(tag).has("checked"))).toEqual(["", "", true]);
    const cards = element(panel, "ul", "site-admin-products__sku-cards");
    expect(textNodes(cards)).toEqual([
      COPY["admin.sku_code_on_save"][language],
      COPY["admin.price"][language],
      COPY["admin.initial_stock"][language],
      COPY["admin.sku_enabled"][language],
      COPY["admin.available_today"][language],
      "—",
    ]);
    expect(tags(panel, "select")).toHaveLength(1);
    expect(buttonsOf(panel, "site-admin-products__remove")).toEqual([]);
    expect(buttonsOf(panel, "site-admin-products__add-sku")).toEqual([]);
    expect(buttonsOf(panel, "site-admin-products__add-option").map((tag) => attributes(tag).has("disabled"))).toEqual([false]);
  });

  // UX A04「规格」「规格名只在新建商品时给出（0–3 个，[admin.add_option] 达 3 个后禁用）」与本任务 purpose「admin.add_option 新增一个规格名（三语名称、
  // 一个空的值与 admin.add_value、(✗)），达 3 个后禁用；每行 SKU 随之多一个下拉框…（默认空选项）；有规格名时显示 admin.add_sku」：每次新增的规格名
  // 三语名称为空串、带一个空的值，临时编号递增；SKU 那一行每次多一个空选项的下拉框；第 3 个之后 admin.add_option 禁用、再加不变。
  it.each(LANGUAGES)("adds up to 3 options and then disables admin.add_option in %s", (language) => {
    const one = addOption(newEditor([APPAREL]));
    expect(one.form.options).toEqual([{ tempId: "o1", names: { en: "", zh: "", ms: "" }, values: [{ tempId: "v1", names: { en: "", zh: "", ms: "" } }] }]);
    expect(one.form.variants).toEqual([{ tempId: "s1", choices: [""], price: "", stock: "", isActive: true }]);
    const two = addOption(one);
    const addButtons = (e: NewEditor) => buttonsOf(form(render(ready(LISTED, e), language)), "site-admin-products__add-option").map((tag) => attributes(tag).has("disabled"));
    expect(addButtons(two)).toEqual([false]);
    const full = addOption(two);
    expect(MAX_OPTIONS).toBe(3);
    expect(full.form.options.map((option) => ("tempId" in option ? [option.tempId, option.values.map((value) => ("tempId" in value ? value.tempId : ""))] : null))).toEqual([
      ["o1", ["v1"]],
      ["o2", ["v2"]],
      ["o3", ["v3"]],
    ]);
    expect(full.form.variants[0]).toEqual({ tempId: "s1", choices: ["", "", ""], price: "", stock: "", isActive: true });
    expect(canAddOption(full.form)).toBe(false);
    expect(addOption(full)).toBe(full);
    expect(addButtons(full)).toEqual([true]);
    const html = form(render(ready(LISTED, full), language));
    const options = element(html, "div", "site-admin-products__options");
    expect(textNodes(options)).not.toContain(COPY["admin.no_options"][language]);
    expect(tags(options, "input").filter((tag) => attributes(tag).get("aria-label") === COPY["admin.option_value"][language])).toHaveLength(3);
    const row = bodyRows(element(html, "div", "site-admin-products__skus"))[0] ?? "";
    expect(selectOptionsOf(row)).toEqual([
      [["", "", true], ["v1", "", false]],
      [["", "", true], ["v2", "", false]],
      [["", "", true], ["v3", "", false]],
    ]);
    expect(buttonsOf(html, "site-admin-products__add-sku")).toHaveLength(2);
  });

  // UX A04「规格」「被本次新增的 SKU 选用的规格值（或其值被选用的规格名）的 (✗) 禁用」、视觉稿 A04-desktop-new 的标注「被新 SKU 选用的规格值及其规格名，
  // (✕) 禁用」、HANDOFF 0.82 第（5）点「被选用的值、只剩一个的值、有值被选用的规格名，其 (✗) 禁用」与本任务 purpose「每行带 (✗)，只剩一行时禁用」
  // 「(✗) 读屏标签沿用 admin.remove」：只有一个值时该值的 (✗) 禁用、规格名的可用，SKU 只剩一行时它的 (✗) 禁用（桌面与手机）；再加一个值并让 SKU 选用
  // 第一个值后，第一个值与规格名的 (✗) 禁用、第二个值的可用；多一行 SKU 后两行的 (✗) 都可用。禁用时去掉不变。
  it.each(LANGUAGES)("disables each (✗) on the new product form by its rule in %s", (language) => {
    const label = COPY["admin.remove"][language];
    const one = addOption(newEditor([APPAREL]));
    expect(valueRemovable(one.form, 0, 0)).toBe(false);
    expect(optionRemovable(one.form, 0)).toBe(true);
    expect(skuRemovable(one.form, 0)).toBe(false);
    const first = form(render(ready(LISTED, one), language));
    expect(removesOf(element(first, "div", "site-admin-products__options"))).toEqual([
      [label, "button", true],
      [label, "button", false],
    ]);
    expect(removesOf(element(first, "div", "site-admin-products__skus"))).toEqual([[label, "button", true]]);
    expect(removesOf(element(first, "ul", "site-admin-products__sku-cards"))).toEqual([[label, "button", true]]);
    expect(removeValue(one, 0, 0)).toBe(one);
    expect(removeSku(one, 0)).toBe(one);
    const chosen = chooseValue(addValue(one, 0), 0, 0, "v1");
    expect([0, 1].map((index) => valueRemovable(chosen.form, 0, index))).toEqual([false, true]);
    expect(optionRemovable(chosen.form, 0)).toBe(false);
    expect(removesOf(element(form(render(ready(LISTED, chosen), language)), "div", "site-admin-products__options"))).toEqual([
      [label, "button", true],
      [label, "button", false],
      [label, "button", true],
    ]);
    expect(removeValue(chosen, 0, 0)).toBe(chosen);
    expect(removeOption(chosen, 0)).toBe(chosen);
    expect(removeValue(chosen, 0, 1).form.options[0]?.values).toHaveLength(1);
    expect(optionRemovable(chooseValue(chosen, 0, 0, "").form, 0)).toBe(true);
    const rows = addSku(chosen);
    expect([0, 1].map((index) => skuRemovable(rows.form, index))).toEqual([true, true]);
    const html = form(render(ready(LISTED, rows), language));
    expect(removesOf(element(html, "div", "site-admin-products__skus"))).toEqual([
      [label, "button", false],
      [label, "button", false],
    ]);
    expect(removesOf(element(html, "ul", "site-admin-products__sku-cards"))).toEqual([
      [label, "button", false],
      [label, "button", false],
    ]);
    expect(removeSku(rows, 0).form.variants.map((row) => ("tempId" in row ? row.tempId : null))).toEqual(["s2"]);
  });

  // HANDOFF 0.82 第（5）点「规格名减到零个时只留第一行」与本任务 purpose「规格名减到零个时 SKU 表只保留第一行并清除其下拉框」：去掉第二个规格名时每行 SKU
  // 去掉它之下所选的值、保留第一个规格名下所选的；再去掉最后一个规格名后只剩第一行（价格等已填的值保留）、没有所选的值，页面又为 admin.no_options、
  // 没有下拉框、admin.add_sku 与 (✗)。
  it("keeps only the first SKU row once the options go to zero", () => {
    let editor = addSku(addOption(addOption(newEditor([APPAREL]))));
    editor = changePrice(chooseValue(editor, 0, 0, "v1"), 0, "15.00");
    editor = changePrice(editor, 1, "9");
    const choices = (e: NewEditor) => e.form.variants.map((row) => ("tempId" in row ? [row.tempId, row.choices] : null));
    expect(choices(editor)).toEqual([
      ["s1", ["v1", ""]],
      ["s2", ["", ""]],
    ]);
    const single = removeOption(editor, 1);
    expect(single.form.options).toHaveLength(1);
    expect(choices(single)).toEqual([
      ["s1", ["v1"]],
      ["s2", [""]],
    ]);
    const none = removeOption(chooseValue(single, 0, 0, ""), 0);
    expect(none.form.options).toEqual([]);
    expect(none.form.variants).toEqual([{ tempId: "s1", choices: [], price: "15.00", stock: "", isActive: true }]);
    const html = form(render(ready(LISTED, none)));
    expect(textNodes(element(html, "div", "site-admin-products__options"))).toContain(COPY["admin.no_options"].en);
    expect(bodyRows(element(html, "div", "site-admin-products__skus"))).toHaveLength(1);
    expect(tags(html, "select")).toHaveLength(1);
    expect(buttonsOf(html, "site-admin-products__add-sku")).toEqual([]);
    expect(buttonsOf(html, "site-admin-products__remove")).toEqual([]);
  });

  // HANDOFF 0.82 第（5）点与本任务 purpose「没有可选分类时下拉框为空，页面先查拦下」「页面先查在 SHOP-TASK-204、205 的基础上另查已选分类」：只有停用
  // 或缺英文名称的分类时下拉框没有选项、分类为 0，其余都填好也不提交（没有请求，显示 admin.product_invalid，表单保留）；所选的分类停用时同样拦下，
  // 可选时通过。价格、库存与限购格式不对、有规格名未选值、上架而缺英文描述时也拦下；规格名与值的英文名称为空在下架时不拦（由服务端判断）。
  it("blocks the create without a choosable category and checks the rest like a save", async () => {
    const blocked = changeStock(changePrice(changeName(newEditor([SEASONAL, NO_EN]), "Insulated Bottle"), 0, "15.00"), 0, "40");
    expect(blocked.form.categoryId).toBe(0);
    expect(selectOptions(field(form(render(ready(LISTED, blocked, [SEASONAL, NO_EN]))), COPY["admin.product_category"].en))).toEqual([]);
    expect(createInput(blocked.form, [SEASONAL, NO_EN])).toBeNull();
    const calls = stubFetch({});
    const saving = startSaving(blocked);
    const run = runner(ready(LISTED, saving, [SEASONAL, NO_EN]));
    await submitNewProduct("csrf-1", saving, [SEASONAL, NO_EN], new AbortController().signal, run.moves);
    expect(calls).toEqual([]);
    expect(newOf(run.state)).toEqual({ ...saving, saving: false, notice: "admin.product_invalid" });
    expect(textNodes(saveArea(render(run.state)))).toEqual([COPY["common.save"].en, COPY["admin.product_invalid"].en]);
    const filled = filledNew();
    expect(createInput(filled.form, [APPAREL, BAGS])).not.toBeNull();
    expect(createInput(changeCategory(filled, 4).form, CATEGORIES)).toBeNull();
    for (const [name, editor] of [
      ["empty price", changePrice(filled, 0, "")],
      ["stock over the cap", changeStock(filled, 0, "10000")],
      ["limit 0", changeMaxPerOrder(filled, "0")],
      ["published without an English description", changeActive(filled, true)],
      ["no value chosen", addOption(filled)],
    ] as const) {
      expect(createInput(editor.form, [APPAREL, BAGS]), name).toBeNull();
    }
    const unnamed = chooseValue(addOption(filled), 0, 0, "v1");
    expect(createInput(unnamed.form, [APPAREL, BAGS])).not.toBeNull();
    expect(createInput(changeActive(changeDescription(unnamed, "Keeps drinks cold."), true).form, [APPAREL, BAGS])).toBeNull();
  });

  // DESIGN 1.18 第 11 条「新建商品的价格按文字换成整仙提交」、SHOP-TASK-200 的新建请求体与本任务验收「以 SHOP-TASK-200 的新建提交下拉框所选的分类 ID、
  // 文案原样、上架、每单限购、规格名（按顺序，三语名称与值）、值（temp_id 与三语名称）与 SKU（temp_id、option_values 以 temp_id 按规格名的顺序引用值，
  // 没有规格名时为空数组；价格整仙、每日初始库存与启用）；临时编号在一次请求里不重复」：没有规格名时 options 为空、option_values 为空数组；以列表给的
  // CSRF 令牌 POST /api/admin/products，请求体与 createInput 相同。有两个规格名（Colour：Red、Green；Size：500 ml）与两行 SKU 时按顺序写出，SKU 以
  // temp_id 引用值，临时编号互不重复。
  it("writes the create request with temporary ids", async () => {
    const plain = {
      category_id: 2,
      name_en: "Insulated Bottle",
      name_zh: "",
      name_ms: "",
      description_en: "",
      description_zh: "",
      description_ms: "",
      is_active: false,
      max_per_order: 10,
      options: [],
      variants: [{ temp_id: "s1", option_values: [], price_sen: 1500, daily_initial_stock: 40, is_active: true }],
    };
    expect(createInput(filledNew().form, [APPAREL, BAGS])).toStrictEqual(plain);
    const calls = stubFetch({ "/api/admin/products": [json(201, CREATED)] });
    const saving = startSaving(filledNew());
    await submitNewProduct("csrf-1", saving, [APPAREL, BAGS], new AbortController().signal, runner(ready(LISTED, saving)).moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/products", "POST"]]);
    expect(csrfOf(calls[0])).toBe("csrf-1");
    expect(bodyOf(calls[0])).toStrictEqual(plain);

    let editor = addOption(changeName(newEditor([APPAREL]), " Insulated Bottle "));
    editor = changeValueName(changeOptionName(editor, 0, "Colour"), 0, 0, "Red");
    editor = changeValueName(changeOptionName(chooseContent(editor, "zh"), 0, "颜色"), 0, 0, "红");
    editor = chooseContent(editor, "en");
    editor = changeValueName(addValue(editor, 0), 0, 1, "Green");
    editor = changeValueName(changeOptionName(addOption(editor), 1, "Size"), 1, 0, "500 ml");
    editor = chooseValue(chooseValue(editor, 0, 0, "v1"), 0, 1, "v3");
    editor = changeStock(changePrice(editor, 0, "29.00"), 0, "30");
    editor = addSku(editor);
    editor = chooseValue(chooseValue(editor, 1, 0, "v2"), 1, 1, "v3");
    editor = changeVariantActive(changeStock(changePrice(editor, 1, "29.5"), 1, "0"), 1, false);
    const expected = {
      category_id: 1,
      name_en: " Insulated Bottle ",
      name_zh: "",
      name_ms: "",
      description_en: "",
      description_zh: "",
      description_ms: "",
      is_active: false,
      max_per_order: 10,
      options: [
        {
          name_en: "Colour",
          name_zh: "颜色",
          name_ms: "",
          values: [
            { temp_id: "v1", name_en: "Red", name_zh: "红", name_ms: "" },
            { temp_id: "v2", name_en: "Green", name_zh: "", name_ms: "" },
          ],
        },
        { name_en: "Size", name_zh: "", name_ms: "", values: [{ temp_id: "v3", name_en: "500 ml", name_zh: "", name_ms: "" }] },
      ],
      variants: [
        { temp_id: "s1", option_values: [{ temp_id: "v1" }, { temp_id: "v3" }], price_sen: 2900, daily_initial_stock: 30, is_active: true },
        { temp_id: "s2", option_values: [{ temp_id: "v2" }, { temp_id: "v3" }], price_sen: 2950, daily_initial_stock: 0, is_active: false },
      ],
    };
    expect(createInput(editor.form, [APPAREL])).toStrictEqual(expected);
    const declared = [...expected.options.flatMap((option) => option.values), ...expected.variants].map((row) => row.temp_id);
    expect(new Set(declared).size).toBe(declared.length);
    const optionCalls = stubFetch({ "/api/admin/products": [json(201, CREATED)] });
    const options = startSaving(editor);
    await submitNewProduct("csrf-1", options, [APPAREL], new AbortController().signal, runner(ready(LISTED, options, [APPAREL])).moves);
    expect(bodyOf(optionCalls[0])).toStrictEqual(expected);
  });

  // UX A04「保存结果」「新建成功后进入该商品的编辑区、显示 [admin.product_created]」与本任务 purpose「新建成功后以返回的商品在列表最前加一行（分类名称取自
  // 分类读取结果）、进入它的编辑区并显示 admin.product_created」「之后按已有商品编辑与保存」：只有那一次 POST；列表最前多出 Insulated Bottle（分类 Bags
  // 的三语名称），编辑区为返回的商品（照 editorOf，内容语言不变）、标题 admin.product_edit、common.save 旁为 role="status" 的 admin.product_created，
  // 不算有修改；之后以返回的 version 照 saveInput 保存。分类不在读取结果里时该行的分类名称为 null。
  it.each(LANGUAGES)("adds the created product to the top of the list and opens it in %s", async (language) => {
    const calls = stubFetch({ "/api/admin/products": [json(201, CREATED)] });
    const saving = startSaving(chooseContent(filledNew(), "zh"));
    const run = runner(ready(LISTED, saving));
    await submitNewProduct("csrf-1", saving, [APPAREL, BAGS], new AbortController().signal, run.moves);
    expect(calls).toHaveLength(1);
    expect(run.events).toEqual(["update"]);
    const item = createdItem(CREATED, [APPAREL, BAGS]);
    expect(item).toEqual({
      id: 9,
      slug: "insulated-bottle",
      name_en: "Insulated Bottle",
      name_zh: null,
      name_ms: null,
      is_active: false,
      category_id: 2,
      category_name_en: "Bags",
      category_name_zh: null,
      category_name_ms: "",
    });
    expect(createdItem(CREATED, [APPAREL])).toMatchObject({ category_name_en: null, category_name_zh: null, category_name_ms: null });
    const after = openOf(run.state);
    expect(run.state).toEqual(ready([item, ...LISTED], { ...editorOf(9, CREATED), content: "zh", notice: "admin.product_created" }));
    expect(isDirty(after)).toBe(false);
    const html = render(run.state, language);
    const rows = [...element(html, "div", "site-admin-products__table").matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => textNodes(m[1] ?? ""));
    expect(rows[0]?.slice(0, 2)).toEqual([productName(item, language), categoryName(item, language)]);
    expect(form(html).startsWith(`<form class="acs-admin__panel site-admin-products__form"><h2 class="acs-admin__h">${escapeHtml(COPY["admin.product_edit"][language])}</h2>`)).toBe(true);
    const area = saveArea(html);
    expect(textNodes(area)).toEqual([COPY["common.save"][language], COPY["admin.product_created"][language]]);
    expect(area).toContain('role="status"');
    expect(saveInput(changePrice(after, 0, "16").form, after.product.version)).toMatchObject({ version: 1, variants: [{ id: 51, price_sen: 1600 }] });
  });

  // 本任务 purpose「网络中断只显示 common.network_check、保留表单、不自动重新读取（新建可能已经成功…再次提交会多出一件商品；由管理员核对列表后把多余的下架）」
  // （HANDOFF 0.82 第（5）点，Kelvin 2026-11-03 确认）与验收「invalid 显示 admin.product_invalid 并保留表单；changed、csrf、network、none 与其他失败照 purpose
  // 与 204 的写法」：422、409、500 与网络中断各只发了那一次 POST、没有重新读取，新建表单与已填的值不变、仍算有修改，common.save 旁为对应的提示（409 另有
  // admin.reload）；401 以 replace 回到 A01。
  it("keeps the form on invalid, changed, other failures and network loss without re-reading", async () => {
    const cases = [
      [json(422, { detail: "product_invalid" }), "admin.product_invalid"],
      [json(409, { detail: "product_changed" }), "admin.product_changed"],
      [json(500, {}), "common.error_retry"],
      ["network", "common.network_check"],
    ] as const;
    for (const [reply, notice] of cases) {
      const calls = stubFetch({ "/api/admin/products": [reply] });
      const saving = startSaving(filledNew());
      const run = runner(ready(LISTED, saving));
      await submitNewProduct("csrf-1", saving, [APPAREL, BAGS], new AbortController().signal, run.moves);
      expect(calls.map((call) => call.init.method), notice).toEqual(["POST"]);
      expect(run.events).toEqual(["update"]);
      expect(run.state).toEqual(ready(LISTED, { ...saving, saving: false, notice }));
      expect(isDirty(newOf(run.state))).toBe(true);
      const area = saveArea(render(run.state));
      const shown = notice === "admin.product_changed" ? [COPY["common.save"].en, COPY[notice].en, COPY["admin.reload"].en] : [COPY["common.save"].en, COPY[notice].en];
      expect(textNodes(area)).toEqual(shown);
      expect(area).toContain('role="alert"');
    }
    stubFetch({ "/api/admin/products": [json(401, {})] });
    const saving = startSaving(filledNew());
    const login = runner(ready(LISTED, saving));
    await submitNewProduct("csrf-1", saving, [APPAREL, BAGS], new AbortController().signal, login.moves);
    expect(login.events).toEqual([`replace ${LOGIN}`]);
    expect(createStep({ kind: "none" })).toBe("login");
    expect(createStep({ kind: "csrf" })).toBe("reread");
  });

  // 本任务 purpose「403 照 204 重新读取并保留表单」：POST 403 后重新读取列表与分类，页面换成新列表、新分类与新令牌，新建表单与已填的值保留、common.save 旁为
  // common.error_retry；下一次新建以新令牌提交。
  it("re-reads after a 403 and keeps the new product form", async () => {
    const calls = stubFetch({
      "/api/admin/products": [json(403, { detail: "csrf_invalid" }), json(200, { products: [TEE], csrf_token: "csrf-2" })],
      "/api/admin/categories": [json(200, { categories: [BAGS, KITCHEN], csrf_token: "category-csrf" })],
    });
    const saving = startSaving(filledNew());
    const run = runner(ready(LISTED, saving));
    await submitNewProduct("csrf-1", saving, [APPAREL, BAGS], new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/products", "POST"],
      ["/api/admin/products", "GET"],
      ["/api/admin/categories", "GET"],
    ]);
    expect(run.state).toEqual({ ...ready([TEE], { ...saving, saving: false, notice: "common.error_retry" }, [BAGS, KITCHEN]), csrfToken: "csrf-2" });
    const retry = stubFetch({ "/api/admin/products": [json(201, CREATED)] });
    const again = startSaving(newOf(run.state));
    await submitNewProduct("csrf-2", again, [BAGS, KITCHEN], new AbortController().signal, runner(run.state).moves);
    expect(csrfOf(retry[0])).toBe("csrf-2");
    expect(rereadStep({ kind: "none" }, { kind: "ok", categories: [APPAREL], csrf_token: "category-csrf" }, "new")).toBe("login");
  });

  // 本任务 purpose「changed 时 admin.reload 整页重新读取，新建表单恢复为空白」：先显示读取中，读取列表与分类后为空白的新建表单（分类为新读取结果里的第一个
  // 可选分类），已填的值丢弃、不算有修改；整页读取失败时只有提示。
  it("reloads the page into a blank form on admin.reload", async () => {
    const calls = stubFetch({
      "/api/admin/products": [json(200, { products: [TEE], csrf_token: "csrf-3" })],
      "/api/admin/categories": [json(200, { categories: [SEASONAL, BAGS, APPAREL], csrf_token: "category-csrf" })],
    });
    const run = runner(ready(LISTED, { ...changeName(filledNew(), "Mine"), notice: "admin.product_changed" }));
    await reloadNewProduct(new AbortController().signal, run.moves);
    expect(run.events).toEqual(["show loading", "show ready"]);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/products", "GET"],
      ["/api/admin/categories", "GET"],
    ]);
    expect(run.state).toEqual({ ...ready([TEE], newEditor([SEASONAL, BAGS, APPAREL]), [SEASONAL, BAGS, APPAREL]), csrfToken: "csrf-3" });
    expect(newOf(run.state).form.categoryId).toBe(2);
    expect(isDirty(newOf(run.state))).toBe(false);
    stubFetch({ "/api/admin/products": [LIST_OK()], "/api/admin/categories": [json(500, {})] });
    const broken = runner(ready(LISTED, filledNew()));
    await reloadNewProduct(new AbortController().signal, broken.moves);
    expect(broken.state).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // 本任务验收「新建表单中任一项与初值不同即为有未保存的修改（扩展导出的判定函数）」与 UX A04「列表」（有未保存的修改时点 [common.edit]、[common.create] 先弹
  // 确认框）：初值不算、切换内容语言不算；名称、描述、分类、上架、限购、价格、库存、启用、新增规格名、规格名与值的名称、新增 SKU 任一项改了都算；改回原值、
  // 加了又去掉的规格名不算。新建表单打开时点某件商品的 common.edit 照常改为读取中。
  it("detects unsaved changes on the new product form", () => {
    const base = newEditor([APPAREL, BAGS]);
    expect(isDirty(base)).toBe(false);
    expect(isDirty(chooseContent(base, "ms"))).toBe(false);
    const withOption = addOption(base);
    const changes: [string, NewEditor][] = [
      ["name", changeName(base, "x")],
      ["description", changeDescription(base, "x")],
      ["category", changeCategory(base, 2)],
      ["published", changeActive(base, true)],
      ["limit", changeMaxPerOrder(base, "11")],
      ["price", changePrice(base, 0, "1")],
      ["stock", changeStock(base, 0, "1")],
      ["enabled", changeVariantActive(base, 0, false)],
      ["option", withOption],
      ["option name", changeOptionName(withOption, 0, "x")],
      ["value name", changeValueName(withOption, 0, 0, "x")],
      ["sku", addSku(withOption)],
    ];
    for (const [name, editor] of changes) {
      expect(isDirty(editor), name).toBe(true);
    }
    expect(isDirty(changeName(changeName(base, "x"), ""))).toBe(false);
    expect(isDirty(changeMaxPerOrder(changeMaxPerOrder(base, "11"), "10"))).toBe(false);
    expect(isDirty(removeOption(withOption, 0))).toBe(false);
    expect(startEditing(ready(LISTED, changeName(base, "x")), 3)).toEqual(ready(LISTED, { status: "loading", id: 3 }));
  });

  // UX A04「内容语言」（[admin.translation_missing] 只在内容语言为中文或马来文、且该语言的商品名称、描述或任一规格名、规格值的名称有缺时显示）与本任务验收
  // 「admin.translation_missing 涵盖新建表单的名称、描述、规格名与值」：中文内容语言下空白表单有提示；填了中文名称与描述后没有；新增规格名后又有，规格名与
  // 值都填了中文后没有；英文内容语言下没有。
  it("covers the new product form in the translation notice", () => {
    let editor = chooseContent(newEditor([APPAREL]), "zh");
    expect(translationMissing(editor)).toBe(true);
    const notice = COPY["admin.translation_missing"].en.replace("{language}", COPY["common.lang_zh"].en);
    expect(textNodes(form(render(ready(LISTED, editor))))).toContain(notice);
    editor = changeDescription(changeName(editor, "保温瓶"), "保冷保温。");
    expect(translationMissing(editor)).toBe(false);
    editor = addOption(editor);
    expect(translationMissing(editor)).toBe(true);
    editor = changeOptionName(editor, 0, "颜色");
    expect(translationMissing(editor)).toBe(true);
    editor = changeValueName(editor, 0, 0, "红");
    expect(translationMissing(editor)).toBe(false);
    expect(translationMissing(chooseContent(addValue(editor, 0), "en"))).toBe(false);
    expect(textNodes(form(render(ready(LISTED, editor))))).not.toContain(notice);
  });

  // 本任务验收「页面文字全部来自字典」：新建表单（空白、有规格名与 SKU、没有可选分类、新建中）里看得见或读得出的每段文字都是当前语言的字典文案，或数据本身
  // （列表的商品与分类名称、分类下拉的分类名称、下拉框里的规格值名称与当日可用库存、规格列的 —）。
  it.each(LANGUAGES)("shows only dictionary text on the new product form in %s", (language) => {
    const allowed = dictionary(language);
    allowed.add("—");
    for (const item of LISTED) {
      allowed.add(productName(item, language));
      allowed.add(categoryName(item, language));
    }
    for (const label of ["common.lang_zh", "common.lang_ms"] as const) {
      allowed.add(COPY["admin.translation_missing"][language].replace("{language}", COPY[label][language]));
    }
    for (const choice of newCategoryChoices([APPAREL, BAGS], language)) {
      allowed.add(choice.label);
    }
    const named = chooseValue(changeValueName(changeOptionName(addOption(filledNew()), 0, "Colour"), 0, 0, "Red"), 0, 0, "v1");
    const states = [
      ready(LISTED, newEditor([APPAREL, BAGS])),
      ready(LISTED, chooseContent(addSku(named), "zh")),
      ready(LISTED, newEditor([SEASONAL]), [SEASONAL]),
      ready(LISTED, startSaving(named)),
    ];
    for (const state of states) {
      for (const row of newSkuRows(newOf(state), language)) {
        for (const select of row.selects) {
          for (const choice of select.choices) {
            allowed.add(choice.label);
          }
        }
      }
      for (const value of visibleTexts(renderSignedIn(language, state))) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });

  // ---- SHOP-TASK-207 图片区的接入 ----

  const PHOTOS: AdminProductImage[] = [
    { id: 61, storage_ref: "/media/products/61.webp" },
    { id: 62, storage_ref: "/media/products/62.webp" },
  ];
  const PHOTO_SHIRT: AdminProduct = { ...SHIRT, images: PHOTOS };

  // 图片区（含其内容）。
  const imageArea = (html: string): string => element(html, "div", "site-admin-images");

  // UX A04 桌面线框「[admin.publish_requirements]」之后为「[admin.product_images] [admin.image_count]」至「[admin.images_immediate]」，再为
  // 「[admin.product_options]」，「新建未保存：( [admin.upload] ) 禁用，旁边 [admin.image_save_first]」；本任务验收「位置在每单限购与规格区之前（照视觉稿）」
  // 「接入——…把图片区放进编辑区、传入商品 ID、当前图片…」「测试——…编辑区出现图片区」：已有商品的编辑区里图片区在 admin.publish_requirements 与规格区之间，
  // 缩略图为该商品的图片（按接口顺序），张数为 2，admin.upload 可用；新建表单的同一位置为 0 张、没有缩略图、admin.upload 禁用，旁边为 admin.image_save_first。
  it.each(LANGUAGES)("puts the image area between the publish note and the options in %s", (language) => {
    const html = form(render(opened(editorOf(3, PHOTO_SHIRT)), language));
    const area = imageArea(html);
    const at = html.indexOf(area);
    expect(html.indexOf(escapeHtml(COPY["admin.publish_requirements"][language]))).toBeLessThan(at);
    expect(html.indexOf('class="site-admin-products__options"')).toBeGreaterThan(at);
    expect(tags(area, "img").map((tag) => attributes(tag).get("src"))).toEqual(PHOTOS.map((photo) => photo.storage_ref));
    expect(textNodes(area)).toContain(formatCopy(COPY["admin.image_count"][language], { count: 2, max: 8 }));
    expect(attributes(tags(element(area, "div", "site-admin-images__upload"), "button")[0] ?? "").has("disabled")).toBe(false);
    const created = form(render(ready(LISTED, newEditor([APPAREL, BAGS])), language));
    const empty = imageArea(created);
    expect(created.indexOf(escapeHtml(COPY["admin.publish_requirements"][language]))).toBeLessThan(created.indexOf(empty));
    expect(created.indexOf('class="site-admin-products__options"')).toBeGreaterThan(created.indexOf(empty));
    expect(tags(empty, "img")).toEqual([]);
    expect(textNodes(empty)).toContain(formatCopy(COPY["admin.image_count"][language], { count: 0, max: 8 }));
    expect(textNodes(element(empty, "div", "site-admin-images__upload"))).toEqual([COPY["admin.upload"][language], COPY["admin.image_save_first"][language]]);
    expect(attributes(tags(empty, "button")[0] ?? "").has("disabled")).toBe(true);
  });

  // UX A04「列表」「有未保存的修改（…图片与当日库存调整立即生效，不算在内）」、「图片」「…不需要 [common.save]，也不改商品的版本号」与本任务验收「编辑区未保存的
  // 修改与 version 不因图片操作改变，未保存判定不含图片」「测试——…图片变化不算未保存的修改」：withImages 只换该商品的 product.images；没有修改的编辑区换了图片
  // 仍不算有修改，有修改的编辑区已填的值、基线与 version 都不变、仍算有修改；编辑区已是别的商品、读取中、新建表单或页面还没取到时不变。
  it("replaces only the images of the open product and keeps the edits", () => {
    const clean = editorOf(3, SHIRT);
    const replaced = withImages(opened(clean), 3, PHOTOS);
    expect(replaced).toEqual(opened({ ...clean, product: { ...SHIRT, images: PHOTOS } }));
    expect(isDirty(editingOf(replaced))).toBe(false);
    const edited = changeName(clean, "Tee");
    const after = openOf(withImages(opened(edited), 3, PHOTOS));
    expect(after.form).toBe(edited.form);
    expect(after.base).toBe(edited.base);
    expect(after.product.version).toBe(SHIRT.version);
    expect(after.product.images).toEqual(PHOTOS);
    expect(isDirty(after)).toBe(true);
    for (const state of [opened(clean), ready(LISTED, { status: "loading", id: 3 }), ready(LISTED, newEditor([APPAREL])), LOADING]) {
      expect(withImages(state, state === LOADING ? 3 : 5, PHOTOS)).toBe(state);
    }
    const loading = ready(LISTED, { status: "loading", id: 3 });
    expect(withImages(loading, 3, PHOTOS)).toBe(loading);
  });

  // 本任务 purpose「商品保存或新建进行中图片区的按钮也都禁用，图片操作进行中 common.save 禁用（两者互斥，避免较晚到达的保存结果以旧的图片列表覆盖较新的）」：
  // imagesBusy 为真时 common.save 禁用；保存中（startSaving）与新建中图片区的按钮（admin.upload 与每张的 (‹)(›)(✗)）都禁用；都不在进行中时 common.save 与
  // admin.upload 可用。
  it("keeps saving and image work apart", () => {
    const state = opened(editorOf(3, PHOTO_SHIRT));
    const busy = renderToStaticMarkup(wrap(<AdminProductsView state={state} {...HANDLERS} imagesBusy />, "en"));
    expect(attributes(tags(saveArea(busy), "button")[0] ?? "").has("disabled")).toBe(true);
    const idle = render(state);
    expect(attributes(tags(saveArea(idle), "button")[0] ?? "").has("disabled")).toBe(false);
    expect(attributes(tags(element(idle, "div", "site-admin-images__upload"), "button")[0] ?? "").has("disabled")).toBe(false);
    const saving = tags(imageArea(render(opened(startSaving(editorOf(3, PHOTO_SHIRT))))), "button");
    expect(saving).toHaveLength(7);
    expect(saving.every((tag) => attributes(tag).has("disabled"))).toBe(true);
    const creating = tags(imageArea(render(ready(LISTED, startSaving(newEditor([APPAREL, BAGS]))))), "button");
    expect(creating.every((tag) => attributes(tag).has("disabled"))).toBe(true);
  });

  // 本任务 purpose「商品保存成功后图片区以返回的商品为准」：保存成功时编辑区的图片为返回商品的图片，图片区随之改显示返回的列表。
  it("shows the saved product's images after a save", () => {
    const step = saveStep({ kind: "ok", product: { ...SHIRT, version: 5, images: PHOTOS } }, 3);
    if (typeof step === "string") {
      throw new Error("expected a change");
    }
    const next = step(opened(startSaving(editorOf(3, SHIRT))));
    expect(openOf(next).product.images).toEqual(PHOTOS);
    expect(tags(imageArea(render(next)), "img").map((tag) => attributes(tag).get("src"))).toEqual(PHOTOS.map((photo) => photo.storage_ref));
  });

  // 本任务 purpose「图片操作的 403 由页面重新读取列表与分类取得新的 CSRF 令牌（编辑区与已填的值保留）」：rereadToken 并行读取列表与分类（带图片区的 signal），
  // 都取到时换成新的列表、分类与令牌，编辑区（含已填的值与 version）不变并返回 ok；任一 401 返回 login、网络中断 network、其他失败 failed，页面都不变；
  // tokenStep 的结果同样。
  it("rereads the list for a new token and keeps the editor", async () => {
    const editor = changeName(editorOf(3, SHIRT), "Tee");
    const calls = stubFetch({
      "/api/admin/products": [json(200, { products: [TEE], csrf_token: "csrf-2" }), json(401, { detail: "not_authenticated" }), "network", json(500, {})],
      "/api/admin/categories": [json(200, { categories: [BAGS], csrf_token: "category-csrf" }), CATEGORIES_OK(), CATEGORIES_OK(), CATEGORIES_OK()],
    });
    const controller = new AbortController();
    const run = runner(opened(editor));
    expect(await rereadToken(controller.signal, run.moves)).toBe("ok");
    expect(calls.slice(0, 2).map((call) => [call.url, call.init.signal])).toEqual([
      ["/api/admin/products", controller.signal],
      ["/api/admin/categories", controller.signal],
    ]);
    expect(run.state).toEqual({ ...ready([TEE], editor, [BAGS]), csrfToken: "csrf-2" });
    const kept = run.state;
    for (const outcome of ["login", "network", "failed"]) {
      expect(await rereadToken(controller.signal, run.moves)).toBe(outcome);
      expect(run.state).toBe(kept);
    }
    expect(tokenStep({ kind: "none" }, { kind: "ok", categories: [], csrf_token: "c" })).toBe("login");
    expect(tokenStep({ kind: "network" }, { kind: "failed" })).toBe("network");
    expect(tokenStep({ kind: "failed" }, { kind: "ok", categories: [], csrf_token: "c" })).toBe("failed");
  });
});
