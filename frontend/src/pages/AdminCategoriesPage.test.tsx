import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCategory } from "../api/adminCategories";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_CATEGORIES_PATH, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminCategoriesPage, {
  AdminCategoriesView,
  changeEnabled,
  changeName,
  chooseContent,
  isDirty,
  LOADING,
  needsDisableConfirm,
  NEW_FORM,
  openCategories,
  openEditor,
  precheck,
  readStep,
  rejectSave,
  reloadCategories,
  saveAction,
  saveStep,
  startSaving,
  submitCategory,
  translationMissing,
} from "./AdminCategoriesPage";
import type { CategoriesMoves, CategoriesState, Editor } from "./AdminCategoriesPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CATEGORIES_PATH = "/admin/categories";
const LOGIN = "/admin/login";
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  "/admin/stripe-pending",
  "/admin/products",
  CATEGORIES_PATH,
  "/admin/coupons",
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_stripe_pending",
  "admin.nav_products",
  "admin.nav_categories",
  "admin.nav_coupons",
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

const APPAREL: AdminCategory = { id: 1, slug: "apparel", name_en: "Apparel", name_zh: "服饰", name_ms: "Pakaian", is_active: true, version: 3, visible_products: 4 };
const SEASONAL: AdminCategory = { id: 7, slug: "seasonal", name_en: "Seasonal", name_zh: null, name_ms: null, is_active: false, version: 1, visible_products: 0 };
const UNNAMED: AdminCategory = { id: 9, slug: "category-9", name_en: null, name_zh: "无名", name_ms: "", is_active: false, version: 0, visible_products: 0 };
const BLANK: AdminCategory = { id: 12, slug: "blank", name_en: "", name_zh: null, name_ms: null, is_active: false, version: 2, visible_products: 0 };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={CATEGORIES_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={CATEGORIES_PATH} storage={storage(language)} />);
}

function ready(categories: AdminCategory[], editor: Editor | null = null, csrfToken = "csrf-1"): CategoriesState {
  return { status: "ready", categories, csrfToken, editor };
}

function view(state: CategoriesState): ReactNode {
  return (
    <AdminCategoriesView
      state={state}
      onEdit={() => undefined}
      onCreate={() => undefined}
      onBack={() => undefined}
      onContent={() => undefined}
      onName={() => undefined}
      onEnabled={() => undefined}
      onSave={() => undefined}
      onReload={() => undefined}
      onConfirmDisable={() => undefined}
      onCancelDisable={() => undefined}
    />
  );
}

function render(state: CategoriesState, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(view(state), language));
}

// 已登录（会话 200）时的后台框架：内容区为本页的 View。
function renderSignedIn(language: Language, state: CategoriesState): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="categories"
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

// 文字为 text 的按钮标签（第一个）。
function buttonWith(html: string, text: string): string | undefined {
  return [...html.matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)].find((m) => textNodes(m[2] ?? "").join(" ") === text)?.[1];
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按顺序给出回答；"network" 表示网络中断（fetch 以 TypeError 拒绝）。多出的请求以错误拒绝。
function stubFetch(...replies: (Response | "network")[]): Call[] {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      const reply = queue.shift();
      if (reply === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      return reply === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return calls;
}

function bodyOf(call: Call | undefined): unknown {
  return JSON.parse(typeof call?.init.body === "string" ? call.init.body : "null");
}

// 页面对结果的处理：show 换成新状态、update 按当前状态改动、replace 记下去向。
function runner(initial: CategoriesState) {
  let state = initial;
  const events: string[] = [];
  const moves: CategoriesMoves = {
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
  };
}

function editorOf(state: CategoriesState): Editor | null {
  return state.status === "ready" ? state.editor : null;
}

function dictionary(language: Language): Set<string> {
  return new Set<string>(Object.values(COPY).map((entry) => entry[language]));
}

describe("AdminCategoriesPage", () => {
  // UX A10「入口：后台导航 [admin.nav_categories]」、0.17「独立一页 /admin/categories」与 SHOP-TASK-196 验收「页面」一条「两页都以后台框架渲染」：
  // 经路由打开 /admin/categories 渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），不套前台框架（没有前台页头、页脚与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_CATEGORIES_PATH).toBe(CATEGORIES_PATH);
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX 0.17「导航项 [admin.nav_categories] 排在 [admin.nav_products] 之后」、「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、
  // [admin.nav_products]、[admin.nav_categories]（0.17）、[admin.nav_coupons]、…」与 SHOP-TASK-196 验收「导航」一条「桌面导航与手机菜单都按这个顺序列出」：
  // 经路由打开本页，桌面导航与 ☰ 菜单都依次为这九项，分类排在商品之后并标 aria-current="page"；各项文字为字典里当前语言的文案。
  // SHOP-TASK-226 改动：按 UX 0.19「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、[admin.nav_stripe_pending]（0.19）、…」，
  // NAV_PATHS、NAV_KEYS、aria-current 的期望与按项数截取的 slice 在退款之后加入 Stripe 待处理，改为十项（当前项仍为分类）。
  it.each(LANGUAGES)("marks categories current, after products, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminCategoriesPage />, language))]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([
          undefined,
          undefined,
          undefined,
          undefined,
          "page",
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
        ]);
        expect(textNodes(nav).slice(0, 10)).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
      }
    }
  });

  // UX A10 手机线框「☰  [admin.nav_categories]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」。
  it.each(LANGUAGES)("names the categories item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_categories"][language])}</span>`,
    );
  });

  // SHOP-TASK-199 改动：SHOP-TASK-196 的「内容区只有标题」改为本任务的内容。UX A10 桌面线框第一行「[admin.nav_categories] ( [common.create] )」
  // 与本任务验收「列表」一条「标题 admin.nav_categories 旁为 common.create」：已登录时内容区以本页根元素开始，标题行为 h1（acs-admin__h）
  // 与可点的 common.create（未满 30 个）。
  it.each(LANGUAGES)("starts the content area with the title and common.create in %s", (language) => {
    const main = element(renderSignedIn(language, ready([APPAREL])), "main", "acs-admin__main");
    expect(main).toContain(
      `<div class="site-admin-categories"><div class="site-admin-categories__list"><div class="site-admin-categories__title"><h1 class="acs-admin__h">${escapeHtml(
        COPY["admin.nav_categories"][language],
      )}</h1><button class="acs-admin__btn" type="button">${escapeHtml(COPY["common.create"][language])}</button></div>`,
    );
  });

  // SHOP-TASK-199 改动：SHOP-TASK-196 的「不发请求」改为本任务的读取。本任务 Purpose「接 SHOP-TASK-198 的请求模块」与 UX A10「查看全部分类」：
  // 打开页面以 GET /api/admin/categories 读取一次（带页面的 signal），取到后显示列表、不选中任何分类（HANDOFF 0.81「打开页面时不选中任何分类」）。
  it("reads the list once on open, with no category selected", async () => {
    const calls = stubFetch(json(200, { categories: [APPAREL, SEASONAL], csrf_token: "csrf-1" }));
    const controller = new AbortController();
    const run = runner(LOADING);
    await openCategories(controller.signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/categories", "GET"]]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(run.state).toEqual(ready([APPAREL, SEASONAL]));
    expect(editorOf(run.state)).toBeNull();
  });

  // 本任务验收「读取与保存的 401 照 A06 页面现有写法回到 A01」与「读取时网络中断照 A06 显示 common.network_check」：读取 401 以 replace 回到 A01，
  // 网络中断与其他失败只显示提示（不显示列表）；读取中列表区标 aria-busy。
  it("maps read outcomes like A06", async () => {
    expect(readStep({ kind: "none" })).toBe("login");
    expect(readStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(readStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
    stubFetch(json(401, {}));
    const run = runner(LOADING);
    await openCategories(new AbortController().signal, run.moves);
    expect(run.events).toEqual([`replace ${LOGIN}`]);
    expect(element(render(LOADING), "div", "site-admin-categories__body")).toContain('aria-busy="true"');
    const failed = element(render({ status: "failed", error: "common.network_check" }), "div", "site-admin-categories__body");
    expect(failed).toContain(escapeHtml(COPY["common.network_check"].en));
    expect(failed).not.toMatch(/<table\b/);
  });

  // UX A10「列表」一段「全部分类按 ID 从小到大」「名称列显示英文名称」「[admin.categories_count] 显示已有个数与上限 30」与桌面线框四个列头加
  // ( [common.edit] )；本任务验收「顺序照接口」「状态为 admin.category_on 或 admin.category_off（样式照视觉稿）」：表格列头依次为四列与空列，
  // 各行按接口顺序为英文名称、slug、前台可见件数（靠右）、状态标签（启用 acs-tag--success、停用 acs-tag--neutral，照 A10-desktop）与 common.edit。
  it.each(LANGUAGES)("lists every category in API order with the count in %s", (language) => {
    const html = render(ready([SEASONAL, APPAREL]), language);
    expect(html).toContain(escapeHtml(translate(language, "admin.categories_count", { count: 2, max: 30 })));
    const table = element(html, "div", "site-admin-categories__table");
    expect(textNodes(element(table, "thead", ""))).toEqual(
      (["admin.category_name", "admin.category_slug", "admin.category_visible", "admin.category_enabled"] as const).map((key) => COPY[key][language]),
    );
    const rows = [...table.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1).map((m) => textNodes(m[1] ?? ""));
    expect(rows).toEqual([
      ["Seasonal", "seasonal", "0", COPY["admin.category_off"][language], COPY["common.edit"][language]],
      ["Apparel", "apparel", "4", COPY["admin.category_on"][language], COPY["common.edit"][language]],
    ]);
    expect(table).toContain(`<span class="acs-tag acs-tag--neutral">${escapeHtml(COPY["admin.category_off"][language])}</span>`);
    expect(table).toContain(`<span class="acs-tag acs-tag--success">${escapeHtml(COPY["admin.category_on"][language])}</span>`);
    expect(table).toContain('<td class="site-admin-categories__num">4</td>');
  });

  // UX A10「已满时 [common.create] 禁用并显示 [admin.categories_full]」：30 个时 common.create 带 disabled 并显示 admin.categories_full；
  // 29 个时可点、不显示。
  it.each(LANGUAGES)("disables common.create and shows admin.categories_full at 30 in %s", (language) => {
    const many = (count: number) => Array.from({ length: count }, (_, index) => ({ ...SEASONAL, id: index + 1, slug: `c-${String(index + 1)}` }));
    const full = render(ready(many(30)), language);
    expect(attributes(buttonWith(full, COPY["common.create"][language]) ?? "").has("disabled")).toBe(true);
    expect(full).toContain(escapeHtml(COPY["admin.categories_full"][language]));
    expect(full).toContain(escapeHtml(translate(language, "admin.categories_count", { count: 30, max: 30 })));
    const room = render(ready(many(29)), language);
    expect(attributes(buttonWith(room, COPY["common.create"][language]) ?? "").has("disabled")).toBe(false);
    expect(room).not.toContain(escapeHtml(COPY["admin.categories_full"][language]));
  });

  // UX A10「名称列显示英文名称（缺英文名称时显示 —）」与本任务验收「为空或 null 时显示 —」：英文名称为 null 与空串的分类在表格与卡片里都显示 —，
  // 不改用中文或马来文名称。
  it("shows — for a missing English name", () => {
    const html = render(ready([UNNAMED, BLANK]));
    const table = element(html, "div", "site-admin-categories__table");
    const rows = [...table.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1).map((m) => textNodes(m[1] ?? "")[0]);
    expect(rows).toEqual(["—", "—"]);
    const cards = element(html, "ul", "site-admin-categories__cards");
    expect([...cards.matchAll(/<span class="site-admin-categories__card-text"><span>([^<]*)<\/span>/g)].map((m) => m[1])).toEqual(["—", "—"]);
    expect(html).not.toContain("无名");
  });

  // UX A10 手机线框「<英文名称> [admin.category_on]|[admin.category_off] >」「[admin.category_visible] <n>」「（> 读屏标签 [common.edit]）」与本任务验收
  // 「两套都渲染、由 site.css 按宽度显隐」「整张卡片为带读屏标签 common.edit 的按钮」：表格与卡片都在，每张卡片是一个 type=button、
  // aria-label 为 common.edit 的按钮，里面有英文名称、admin.category_visible 与件数、状态标签。
  it.each(LANGUAGES)("renders both the desktop table and the phone cards in %s", (language) => {
    const html = render(ready([APPAREL, SEASONAL]), language);
    expect(html).toMatch(/<table class="acs-admin__table">/);
    const cards = element(html, "ul", "site-admin-categories__cards");
    const buttons = [...cards.matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)];
    expect(buttons).toHaveLength(2);
    for (const [index, category] of [APPAREL, SEASONAL].entries()) {
      const [, open = "", inner = ""] = buttons[index] ?? [];
      expect(attributes(open).get("type")).toBe("button");
      expect(attributes(open).get("aria-label")).toBe(COPY["common.edit"][language]);
      expect(classes(open)).toContain("site-admin-categories__card");
      expect(textNodes(inner)).toEqual([
        category.name_en ?? "",
        COPY["admin.category_visible"][language],
        String(category.visible_products),
        COPY[category.is_active ? "admin.category_on" : "admin.category_off"][language],
      ]);
    }
  });

  // HANDOFF 0.81「打开页面时不选中任何分类，编辑区在点 common.edit 或 common.create 之后才显示」：取到列表后没有编辑区，页面不渲染表单、
  // common.back 与 --editing；打开编辑区后才有。
  it("shows no editor until one is opened", () => {
    const closed = render(ready([APPAREL]));
    expect(closed).not.toMatch(/<form\b/);
    expect(hasClass(closed, "site-admin-categories--editing")).toBe(false);
    expect(buttonWith(closed, COPY["common.back"].en)).toBeUndefined();
    const open = render(ready([APPAREL], openEditor(APPAREL)));
    expect(open).toMatch(/<form\b/);
    expect(hasClass(open, "site-admin-categories--editing")).toBe(true);
    expect(buttonWith(open, COPY["common.back"].en)).toBeDefined();
  });

  // UX A10 编辑区线框「[admin.category_edit]（新建时为 [admin.category_new]）」「[admin.category_slug] <slug>（新建时不显示）」「[admin.category_slug_hint]」
  // 与本任务验收「新建默认勾选」「admin.category_slug_hint 常显」：编辑已有分类时标题为 admin.category_edit、显示 slug；新建时标题为
  // admin.category_new、不显示 admin.category_slug，勾选框默认勾选；两者都有 slug 说明与 common.save。
  it.each(LANGUAGES)("titles the editor and shows the slug only for an existing category in %s", (language) => {
    const edit = element(render(ready([APPAREL], openEditor(APPAREL)), language), "form", "site-admin-categories__form");
    expect(edit).toContain(`<h2 class="acs-admin__h">${escapeHtml(COPY["admin.category_edit"][language])}</h2>`);
    expect(textNodes(element(edit, "div", "site-admin-categories__slug"))).toEqual([
      COPY["admin.category_slug"][language],
      "apparel",
      COPY["admin.category_slug_hint"][language],
    ]);
    const created = element(render(ready([APPAREL], openEditor(null)), language), "form", "site-admin-categories__form");
    expect(created).toContain(`<h2 class="acs-admin__h">${escapeHtml(COPY["admin.category_new"][language])}</h2>`);
    expect(created).not.toContain(escapeHtml(COPY["admin.category_slug"][language]) + "<");
    expect(textNodes(element(created, "div", "site-admin-categories__slug"))).toEqual([COPY["admin.category_slug_hint"][language]]);
    expect(tags(created, "input").filter((tag) => attributes(tag).get("type") === "checkbox").map((tag) => attributes(tag).has("checked"))).toEqual([true]);
    expect(buttonWith(created, COPY["common.save"][language])).toBeDefined();
  });

  // UX A10「内容语言：与 A04 相同」、编辑区线框「[admin.content_language] ([common.lang_en]) ([common.lang_zh]) ([common.lang_ms])」与本任务验收
  // 「aria-pressed 标出当前内容语言，默认英文」「切换不丢失其他语言已填的值」：默认英文；三种语言各填一次后来回切换，各语言的值都在，
  // 输入框显示当前内容语言的值，只有当前语言的按钮 aria-pressed="true"。
  it("switches the content language without losing the other names", () => {
    let editor = openEditor(SEASONAL);
    expect(editor.content).toBe("en");
    editor = changeName(editor, "Seasonal picks");
    editor = changeName(chooseContent(editor, "zh"), "季节");
    editor = changeName(chooseContent(editor, "ms"), "Bermusim");
    editor = chooseContent(editor, "zh");
    expect(editor.form.names).toEqual({ en: "Seasonal picks", zh: "季节", ms: "Bermusim" });
    const form = element(render(ready([SEASONAL], editor)), "form", "site-admin-categories__form");
    const group = element(form, "div", "site-admin-categories__languages");
    expect(textNodes(group)).toEqual([COPY["admin.content_language"].en, "English", "中文", "Bahasa Melayu"]);
    expect(tags(group, "button").map((tag) => attributes(tag).get("aria-pressed"))).toEqual(["false", "true", "false"]);
    const name = element(form, "label", "site-admin-categories__name");
    expect(textNodes(name)).toEqual([COPY["admin.category_name"].en]);
    expect(attributes(tags(name, "input")[0] ?? "").get("value")).toBe("季节");
  });

  // UX A10「[admin.category_translation_missing] 只在内容语言为中文或马来文、且该语言的名称为空时显示」与本任务验收「（去掉首尾空白后）」
  // 「{language} 为内容语言在当前界面语言下的名称（common.lang_zh 或 common.lang_ms 的文字）」：英文为空不显示；中文为空或只有空白时显示，
  // {language} 为 common.lang_zh；马来文为空时 {language} 为 common.lang_ms；有值时不显示。
  it.each(LANGUAGES)("shows admin.category_translation_missing only for an empty Chinese or Malay name in %s", (language) => {
    const missingText = (content: Language) =>
      escapeHtml(translate(language, "admin.category_translation_missing", { language: COPY[content === "zh" ? "common.lang_zh" : "common.lang_ms"][language] }));
    const english = changeName(openEditor(null), "");
    expect(translationMissing(english)).toBe(false);
    expect(render(ready([], english), language)).not.toContain("site-admin-categories__missing");
    const chinese = changeName(chooseContent(openEditor(SEASONAL), "zh"), "  ");
    expect(translationMissing(chinese)).toBe(true);
    expect(render(ready([SEASONAL], chinese), language)).toContain(missingText("zh"));
    const malay = chooseContent(openEditor(SEASONAL), "ms");
    expect(translationMissing(malay)).toBe(true);
    expect(render(ready([SEASONAL], malay), language)).toContain(missingText("ms"));
    const filled = chooseContent(openEditor(APPAREL), "ms");
    expect(translationMissing(filled)).toBe(false);
    expect(render(ready([APPAREL], filled), language)).not.toContain("site-admin-categories__missing");
  });

  // UX A10「勾选启用须有英文名称，页面先检查，不满足即不提交、显示 [admin.category_invalid]」与本任务验收「页面只先查「勾选启用而英文名称为空
  // （去掉首尾空白后）」一种，其余取值由服务端判断」：勾选启用而英文名称为空或只有空白时为 invalid（不提交），提示为 admin.category_invalid；
  // 不启用时英文名称为空也直接提交；名称超长等其余取值页面不查。
  it("blocks only an enabled category without an English name", () => {
    expect(precheck({ ...NEW_FORM, names: { en: "   ", zh: "中文", ms: "" } })).toBe("admin.category_invalid");
    expect(saveAction(openEditor(null))).toBe("invalid");
    expect(saveAction(changeName(openEditor(APPAREL), " \t"))).toBe("invalid");
    expect(saveAction(changeEnabled(openEditor(null), false))).toBe("submit");
    expect(saveAction(changeName(openEditor(null), "x".repeat(500)))).toBe("submit");
    const rejected = rejectSave(openEditor(null));
    expect(rejected.notice).toBe("admin.category_invalid");
    expect(element(render(ready([], rejected)), "div", "site-admin-categories__save")).toContain(escapeHtml(COPY["admin.category_invalid"].en));
  });

  // UX A10「取消勾选后保存时，该分类的 [admin.category_visible] 大于 0 即先弹 [admin.category_disable_confirm]（{count} 为该件数），确认后才提交；
  // 为 0 时直接提交」与本任务验收「按钮 common.cancel 与 admin.category_disable；样式照 SHOP-TASK-197 的确认框」：可见 4 件的启用分类改为不启用时
  // 为 confirm，确认框为 site-admin__overlay 里 role=dialog 的面板，文字的 {count} 为 4，按钮依次为 common.cancel（次要）与 admin.category_disable；
  // 可见 0 件、原本已停用或没有改启用状态时直接提交。
  it.each(LANGUAGES)("asks before disabling a category with products shown in %s", (language) => {
    const disabling = changeEnabled(openEditor(APPAREL), false);
    expect(needsDisableConfirm(disabling)).toBe(true);
    expect(saveAction(disabling)).toBe("confirm");
    expect(saveAction(changeEnabled(openEditor({ ...APPAREL, visible_products: 0 }), false))).toBe("submit");
    expect(needsDisableConfirm(changeEnabled(openEditor(SEASONAL), false))).toBe(false);
    expect(saveAction(openEditor(APPAREL))).toBe("submit");
    expect(render(ready([APPAREL], disabling), language)).not.toMatch(/role="dialog"/);
    const html = render(ready([APPAREL], { ...disabling, confirming: true }), language);
    const overlay = element(html, "div", "site-admin__overlay");
    const dialog = element(overlay, "div", "site-admin__confirm");
    expect(attributes(tags(dialog, "div")[0] ?? "").get("role")).toBe("dialog");
    expect(attributes(tags(dialog, "div")[0] ?? "").get("aria-modal")).toBe("true");
    expect(textNodes(dialog)).toEqual([
      translate(language, "admin.category_disable_confirm", { count: 4 }),
      COPY["common.cancel"][language],
      COPY["admin.category_disable"][language],
    ]);
    expect(classes(buttonWith(dialog, COPY["common.cancel"][language]) ?? "")).toContain("acs-admin__btn--secondary");
    expect(classes(buttonWith(dialog, COPY["admin.category_disable"][language]) ?? "")).not.toContain("acs-admin__btn--secondary");
  });

  // 本任务验收「编辑以四项另加读取（或上次保存）给的 version 调用保存」「名称原样提交」与 UX A10「保存结果」「成功时页面改显示服务端返回的保存后数据
  // （版本号与列表随之更新），显示 [admin.category_saved]」：PUT /api/admin/categories/1 的请求体为原样的三项名称、启用状态与 version 3；
  // 成功后列表与编辑区换成返回的分类（version 4），显示 role=status 的 admin.category_saved，不再算有未保存的修改。
  it("saves an existing category and shows the saved data", async () => {
    const saved: AdminCategory = { ...APPAREL, name_en: "Apparel & more", version: 4 };
    const calls = stubFetch(json(200, saved));
    const editor = startSaving(changeName(openEditor(APPAREL), " Apparel & more "));
    const run = runner(ready([APPAREL, SEASONAL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/categories/1", "PUT"]]);
    expect(bodyOf(calls[0])).toEqual({ name_en: " Apparel & more ", name_zh: "服饰", name_ms: "Pakaian", is_active: true, version: 3 });
    const after = run.state;
    expect(after.status === "ready" ? after.categories : null).toEqual([saved, SEASONAL]);
    const next = editorOf(after);
    expect(next?.category).toEqual(saved);
    expect(next?.form.names.en).toBe("Apparel & more");
    expect(next?.notice).toBe("admin.category_saved");
    expect(next?.saving).toBe(false);
    expect(isDirty(next)).toBe(false);
    expect(element(render(after), "div", "site-admin-categories__save")).toContain(`<span role="status">${escapeHtml(COPY["admin.category_saved"].en)}</span>`);
  });

  // UX A10「新建：…新建成功后进入该分类的编辑区、显示 [admin.category_created] 与生成的 slug」与本任务验收「新建以三项名称与启用状态调用新建」：
  // POST /api/admin/categories 的请求体只有三项名称与启用状态；返回的分类排在列表末尾，编辑区改为该分类（标题 admin.category_edit、显示 slug），
  // 显示 admin.category_created。
  it("enters the new category's editor after creating it", async () => {
    const created: AdminCategory = { id: 8, slug: "outdoor", name_en: "Outdoor", name_zh: "", name_ms: null, is_active: true, version: 0, visible_products: 0 };
    const calls = stubFetch(json(201, created));
    const editor = startSaving(changeName(openEditor(null), "Outdoor"));
    const run = runner(ready([APPAREL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([["/api/admin/categories", "POST"]]);
    expect(bodyOf(calls[0])).toEqual({ name_en: "Outdoor", name_zh: "", name_ms: "", is_active: true });
    const after = run.state;
    expect(after.status === "ready" ? after.categories : null).toEqual([APPAREL, created]);
    expect(editorOf(after)?.category).toEqual(created);
    expect(editorOf(after)?.notice).toBe("admin.category_created");
    const form = element(render(after), "form", "site-admin-categories__form");
    expect(form).toContain(escapeHtml(COPY["admin.category_edit"].en));
    expect(textNodes(element(form, "div", "site-admin-categories__slug"))).toContain("outdoor");
    expect(form).toContain(`<span role="status">${escapeHtml(COPY["admin.category_created"].en)}</span>`);
  });

  // UX A10「保存结果」：「取值不合格」显示 [admin.category_invalid]，保留已填的值；「分类已被改动」显示 [admin.category_changed] 与 [admin.reload]；
  // 其他失败显示 [common.error_retry]。三种结果下列表不变、已填的值与 version 不变、按钮恢复可点；admin.category_changed 的提示里有 admin.reload 按钮。
  it.each<[Response, string]>([
    [json(422, { detail: "category_invalid" }), "admin.category_invalid"],
    [json(409, { detail: "category_changed" }), "admin.category_changed"],
    [json(500, {}), "common.error_retry"],
  ])("keeps the filled values on a failed save (%#)", async (reply, notice) => {
    stubFetch(reply);
    const editor = startSaving(changeName(openEditor(APPAREL), "Clothes"));
    const run = runner(ready([APPAREL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    const after = editorOf(run.state);
    expect(after?.notice).toBe(notice);
    expect(after?.saving).toBe(false);
    expect(after?.form.names.en).toBe("Clothes");
    expect(after?.category).toEqual(APPAREL);
    expect(run.state.status === "ready" ? run.state.categories : null).toEqual([APPAREL]);
    const save = element(render(run.state), "div", "site-admin-categories__save");
    expect(save).toContain('role="alert"');
    expect(buttonWith(save, COPY["admin.reload"].en) !== undefined).toBe(notice === "admin.category_changed");
  });

  // 本任务验收「保存中禁用并标 aria-busy」与「改任何输入后收起保存的结果提示」：保存中 common.save 带 disabled 与 aria-busy="true"；
  // 改名称或启用状态后提示收起。
  it("disables common.save while saving and clears the result after an edit", () => {
    const saving = render(ready([APPAREL], startSaving(openEditor(APPAREL))));
    const button = attributes(buttonWith(saving, COPY["common.save"].en) ?? "");
    expect(button.has("disabled")).toBe(true);
    expect(button.get("aria-busy")).toBe("true");
    const shown: Editor = { ...openEditor(APPAREL), notice: "admin.category_changed" };
    expect(changeName(shown, "A").notice).toBeNull();
    expect(changeEnabled(shown, false).notice).toBeNull();
  });

  // 本任务验收「读取或保存返回的名称为 null 时，编辑区的输入框与比较基线都当作空串（提交时为空串）」「三项名称或启用状态与最近一次读取或保存的值
  // 不同即为有未保存的修改」「新建时为三项空串与勾选启用」与 UX A10「编辑区的值与最近一次读取或保存的不同」：名称为 null 的分类打开后未改不算修改，
  // 输入框为空串；改了名称或启用状态才算，改回原值又不算；新建表单未改不算；没有编辑区时不算。
  it("decides unsaved changes against the normalized values", () => {
    const opened = openEditor(SEASONAL);
    expect(opened.form.names).toEqual({ en: "Seasonal", zh: "", ms: "" });
    expect(isDirty(opened)).toBe(false);
    expect(attributes(tags(element(render(ready([SEASONAL], chooseContent(opened, "zh"))), "label", "site-admin-categories__name"), "input")[0] ?? "").get("value")).toBe("");
    expect(isDirty(changeName(chooseContent(opened, "zh"), "季节"))).toBe(true);
    expect(isDirty(changeName(changeName(chooseContent(opened, "zh"), "季节"), ""))).toBe(false);
    expect(isDirty(changeEnabled(opened, true))).toBe(true);
    expect(isDirty(chooseContent(opened, "ms"))).toBe(false);
    expect(isDirty(openEditor(null))).toBe(false);
    expect(openEditor(null).form).toEqual({ names: { en: "", zh: "", ms: "" }, is_active: true });
    expect(isDirty(changeName(openEditor(null), "x"))).toBe(true);
    expect(isDirty(null)).toBe(false);
  });

  // HANDOFF 0.81「重新读取（admin.reload）整页读取后，编辑区显示同一分类的最新数据（已不存在时收起编辑区）」与 UX A10「点它重新读取，未保存的修改丢弃」：
  // admin.reload 先显示读取中，取到后编辑区为同一分类的最新 version 与名称、没有修改与提示；该分类已不在列表里时收起编辑区。
  it("shows the latest data of the same category after admin.reload", async () => {
    const latest: AdminCategory = { ...APPAREL, name_en: "Apparel (new)", version: 5 };
    stubFetch(json(200, { categories: [latest, SEASONAL], csrf_token: "csrf-2" }));
    const run = runner(ready([APPAREL, SEASONAL], { ...changeName(openEditor(APPAREL), "Mine"), notice: "admin.category_changed" }));
    await reloadCategories(new AbortController().signal, run.moves, 1);
    expect(run.events).toEqual(["show loading", "show ready"]);
    const editor = editorOf(run.state);
    expect(editor?.category).toEqual(latest);
    expect(editor?.form.names.en).toBe("Apparel (new)");
    expect(editor?.notice).toBeNull();
    expect(isDirty(editor)).toBe(false);
    stubFetch(json(200, { categories: [SEASONAL], csrf_token: "csrf-3" }));
    const gone = runner(ready([APPAREL, SEASONAL], openEditor(APPAREL)));
    await reloadCategories(new AbortController().signal, gone.moves, 1);
    expect(editorOf(gone.state)).toBeNull();
  });

  // HANDOFF 0.81 第（3）点与本任务验收「新建或保存遇到 403 时重新读取整页（取得新的 CSRF 令牌与列表），编辑区仍停在同一分类（或新建表单），
  // 已填的名称、启用状态与提交前的 version 都保留（不换成重新读取的 version），在表单上方显示 common.error_retry」：403 后再发一次 GET，
  // 页面换成新令牌与新列表，编辑区的 category 仍是 version 3 的那份、已填的值不变，alert 为 common.error_retry（渲染在表单之前）；
  // 下一次保存以新令牌与 version 3 提交。
  it("re-reads after a 403 and keeps the editor, values and version", async () => {
    const elsewhere: AdminCategory = { ...APPAREL, version: 6 };
    const calls = stubFetch(json(403, {}), json(200, { categories: [elsewhere, SEASONAL], csrf_token: "csrf-2" }));
    const editor = startSaving(changeEnabled(changeName(openEditor(APPAREL), "Clothes"), false));
    const run = runner(ready([APPAREL, SEASONAL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/admin/categories/1", "PUT"],
      ["/api/admin/categories", "GET"],
    ]);
    const after = run.state;
    expect(after.status === "ready" ? [after.csrfToken, after.categories] : null).toEqual(["csrf-2", [elsewhere, SEASONAL]]);
    const kept = editorOf(after);
    expect(kept?.category?.version).toBe(3);
    expect(kept?.form).toEqual({ names: { en: "Clothes", zh: "服饰", ms: "Pakaian" }, is_active: false });
    expect(kept?.alert).toBe("common.error_retry");
    expect(kept?.saving).toBe(false);
    const html = element(render(after), "div", "site-admin-categories__editor");
    expect(html.indexOf(escapeHtml(COPY["common.error_retry"].en))).toBeLessThan(html.indexOf("<form"));
    const retry = stubFetch(json(200, { ...APPAREL, version: 4 }));
    if (kept !== null) {
      await submitCategory("csrf-2", startSaving(kept), new AbortController().signal, runner(after).moves);
    }
    expect(bodyOf(retry[0])).toMatchObject({ version: 3 });
    expect((retry[0]?.init.headers as Record<string, string> | undefined)?.["X-CSRF-Token"]).toBe("csrf-2");
  });

  // 本任务验收「新建或保存时网络中断只显示 common.network_check、保留表单，不自动重新读取」（HANDOFF 0.81「新建或保存时网络中断只提示、不自动重新读取」）：
  // 新建时 fetch 中断，只发了那一次 POST，提示为 common.network_check，表单仍是新建表单、已填的值不变。
  it("only shows common.network_check when creating loses the connection", async () => {
    const calls = stubFetch("network");
    const editor = startSaving(changeName(openEditor(null), "Outdoor"));
    const run = runner(ready([APPAREL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    expect(calls).toHaveLength(1);
    expect(run.events).toEqual(["update"]);
    const after = editorOf(run.state);
    expect(after?.notice).toBe("common.network_check");
    expect(after?.category).toBeNull();
    expect(after?.form.names.en).toBe("Outdoor");
    expect(element(render(run.state), "div", "site-admin-categories__save")).toContain(escapeHtml(COPY["common.network_check"].en));
  });

  // 本任务验收「读取与保存的 401 照 A06 页面现有写法回到 A01」：保存 401 以 replace 回到 A01，不再更新页面。
  it("returns to A01 when saving gets 401", async () => {
    stubFetch(json(401, {}));
    const editor = startSaving(openEditor(APPAREL));
    const run = runner(ready([APPAREL], editor));
    await submitCategory("csrf-1", editor, new AbortController().signal, run.moves);
    expect(run.events).toEqual([`replace ${LOGIN}`]);
    expect(saveStep({ kind: "none" }, false)).toBe("login");
  });

  // 本任务验收「测试」一条「页面文字全部来自字典」：经路由打开、列表、编辑区（含缺译提示、保存提示与 403 提示）、新建表单与停用确认框里，
  // 看得见或读得出的每段文字都是当前语言的字典文案（带变量的按本页给的值替换），或分类数据本身（名称、slug、件数与名称缺失时的 —）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = dictionary(language);
    for (const value of ["Apparel", "apparel", "4", "Seasonal", "seasonal", "0", "—", "category-9", "季节"]) {
      allowed.add(value);
    }
    allowed.add(translate(language, "admin.categories_count", { count: 3, max: 30 }));
    allowed.add(translate(language, "admin.category_disable_confirm", { count: 4 }));
    allowed.add(translate(language, "admin.category_translation_missing", { language: COPY["common.lang_ms"][language] }));
    const editing: Editor = { ...chooseContent(changeEnabled(openEditor(APPAREL), false), "ms"), confirming: true, alert: "common.error_retry" };
    const html = [
      renderApp(language),
      renderSignedIn(language, ready([APPAREL, SEASONAL, UNNAMED])),
      renderSignedIn(language, ready([APPAREL, SEASONAL, UNNAMED], { ...changeName(editing, ""), notice: "admin.category_changed" })),
      renderSignedIn(language, ready([APPAREL, SEASONAL, UNNAMED], { ...openEditor(null), notice: "admin.category_created" })),
      renderSignedIn(language, { status: "failed", error: "common.error_retry" }),
    ];
    for (const page of html) {
      for (const value of visibleTexts(page)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
