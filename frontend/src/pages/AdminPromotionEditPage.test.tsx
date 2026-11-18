import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminPromotion, AdminPromotionRule, PromotionProblem, PromotionRead, PromotionsRead, PromotionWrite } from "../api/adminPromotions";
import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PROMOTION_NEW_PATH, adminPromotionPath, matchRoute, resolvePath, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminPromotionEditPage, {
  ALL_OPEN,
  AdminPromotionEditView,
  changeCopy,
  changeField,
  changeRules,
  collapseOnPhone,
  copyCount,
  csrfStep,
  editTarget,
  hasUnsavedChanges,
  loadEditor,
  LOADING,
  openEditor,
  PHONE_QUERY,
  phonePanels,
  pressSave,
  reloadEditor,
  saveStep,
  stashPromotionNotice,
  startSaving,
  submitPromotion,
  takePromotionNotice,
} from "./AdminPromotionEditPage";
import type { Editor, EditHandlers, EditMoves, EditState, PanelsOpen, PromotionApi } from "./AdminPromotionEditPage";
import { addRule, mapRule, moveRule, removeRule } from "./AdminPromotionRules";
import type { ScopeLists } from "./AdminPromotionRules";
import { PROMOTION_STATE_CLASS, PROMOTION_STATE_LABEL } from "./AdminPromotionsPage";
import { formFromPromotion, newPromotionForm, promotionInput } from "./promotionForm";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PROMOTIONS_PATH = "/admin/promotions";
const NEW_PATH = "/admin/promotions/new";
const EDIT_PATH = "/admin/promotions/7";
// 编辑页的两条路由：新建与某个活动（段值为活动的内部 ID）。
const EDIT_PATHS = [NEW_PATH, EDIT_PATH] as const;
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  "/admin/stripe-pending",
  "/admin/products",
  "/admin/categories",
  "/admin/coupons",
  PROMOTIONS_PATH,
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
  "admin.nav_promotions",
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language, path: string): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={path}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(path: string, language: Language): string {
  return renderToStaticMarkup(<App initialPath={path} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页内容首次渲染的样子（读取返回前）。内容组件用到 AdminFrame 的 useUnsavedConfirm，只能放在 AdminFrame 里，
// 所以这里照 A10 的测试放本页的 View（SHOP-TASK-270 起）。
function renderSignedIn(language: Language, menuOpen = false): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="promotions"
        state={SIGNED_IN}
        busy={false}
        menuOpen={menuOpen}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminPromotionEditView state={LOADING} panels={ALL_OPEN} handlers={NO_HANDLERS} />
      </AdminFrameView>,
      language,
      EDIT_PATH,
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
  throw new Error(`unclosed <${name}> with class ${className}`);
}

// 读取与保存结果的替身用的活动：内部名称带尖括号，用来确认按纯文本显示。
function promotion(overrides: Partial<AdminPromotion> = {}): AdminPromotion {
  return {
    id: 7,
    version: 3,
    state: "draft",
    ended_early_at: null,
    name: "Lunar <b>New</b> Year",
    starts_at: "2027-01-20T00:00",
    ends_at: "2027-02-12T23:59",
    coupon_combinable: true,
    points_combinable: false,
    rules: [],
    decoration: newPromotionForm().decoration,
    copy: {
      title_en: "Lunar New Year Sale",
      title_zh: "农历新年特卖",
      title_ms: null,
      subtitle_en: null,
      subtitle_zh: null,
      subtitle_ms: null,
      badge_en: "Lunar deal",
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
    ...overrides,
  };
}

function ready(editor: Editor): EditState {
  return { status: "ready", editor };
}

const NO_HANDLERS: EditHandlers = {
  onField: () => undefined,
  onCopy: () => undefined,
  onRules: () => undefined,
  onSave: () => undefined,
  onReload: () => undefined,
  onPanel: () => undefined,
};

function renderView(state: EditState, language: Language, panels: PanelsOpen = ALL_OPEN): string {
  return renderToStaticMarkup(wrap(<AdminPromotionEditView state={state} panels={panels} handlers={NO_HANDLERS} />, language, EDIT_PATH));
}

// name 属性为 name 的 input 标签。
function input(html: string, name: string): Map<string, string> {
  const tag = tags(html, "input").find((item) => attributes(item).get("name") === name);
  if (tag === undefined) {
    throw new Error(`no input named ${name}`);
  }
  return attributes(tag);
}

// 字段旁的 admin.promo_field_invalid：输入框以 aria-describedby 指向它。
function fieldInvalid(html: string, name: string, language: Language): boolean {
  const id = input(html, name).get("aria-describedby");
  if (id === undefined) {
    return false;
  }
  return html.includes(`<span id="${escapeHtml(id)}" class="site-admin-promo-edit__field-invalid">${escapeHtml(COPY["admin.promo_field_invalid"][language])}</span>`);
}

function countOf(html: string, text: string): number {
  return html.split(text).length - 1;
}

const COPY_INPUTS = ["title", "subtitle", "badge", "strip"].flatMap((group) => LANGUAGES.map((language) => `${group}_${language}`));
const LOCKABLE = ["starts_at", "ends_at", "coupon_combinable", "points_combinable"] as const;

// 请求的替身：没给的那一个被调用即失败。
function fakeApi(overrides: Partial<PromotionApi>): PromotionApi {
  const unexpected = () => Promise.reject(new Error("unexpected request"));
  return { readList: unexpected, readOne: unexpected, create: unexpected, save: unexpected, ...overrides };
}

// 记下页面收到的状态与换页。
function recorder(initial: EditState = LOADING) {
  const record: { state: EditState; replaced: RoutePath[]; moves: EditMoves } = {
    state: initial,
    replaced: [],
    moves: {
      show: (state) => {
        record.state = state;
      },
      update: (change) => {
        record.state = change(record.state);
      },
      replace: (path) => {
        record.replaced.push(path);
      },
    },
  };
  return record;
}

function editorOf(state: EditState): Editor {
  if (state.status !== "ready") {
    throw new Error(`not ready: ${state.status}`);
  }
  return state.editor;
}

// 以 editor 提交一次（保存的结果以替身给出），返回记录。
async function submitWith(editor: Editor, write: PromotionWrite, extra: Partial<PromotionApi> = {}) {
  const record = recorder(ready(startSaving(editor)));
  const api = fakeApi({ create: vi.fn(() => Promise.resolve(write)), save: vi.fn(() => Promise.resolve(write)), ...extra });
  const signal = new AbortController().signal;
  await submitPromotion(editor, signal, record.moves, api);
  return { record, api, signal };
}

function problem(code: string, product_name: string | null = null): PromotionProblem {
  return { code, field: "rules", product_id: product_name === null ? null : 11, product_name };
}

// 改过名称、开始时间、同用开关与英文标题的已有活动。
function editedEditor(overrides: Partial<AdminPromotion> = {}): Editor {
  let editor = openEditor(promotion(overrides), "csrf-1");
  editor = changeField(editor, "name", "Renamed");
  editor = changeField(editor, "starts_at", "2027-01-21T09:30");
  editor = changeField(editor, "coupon_combinable", false);
  return changeCopy(editor, "title_en", "Changed title");
}

describe("AdminPromotionEditPage routes", () => {
  // UX 0.20 页面地图「A12 | 后台促销活动 | /admin/promotions、/admin/promotions/<活动 ID>、…」与 A12「编辑区（桌面，/admin/promotions/<活动 ID>；
  // 新建时同一版式）」：新建为 /admin/promotions/new（排在 :id 之前，new 不当作活动 ID），编辑的段值为活动的内部 ID；两者都不带查询参数。
  it("matches the new page before the promotion ID", () => {
    expect(ADMIN_PROMOTION_NEW_PATH).toBe(NEW_PATH);
    expect(matchRoute(NEW_PATH)).toEqual({ pattern: NEW_PATH, params: {} });
    expect(adminPromotionPath(7)).toBe(EDIT_PATH);
    expect(matchRoute(adminPromotionPath(7))).toEqual({ pattern: "/admin/promotions/:id", params: { id: "7" } });
  });

  // 任务验收「不合法的段值也渲染编辑页（由 SHOP-TASK-270 处理），未知路径仍落到首页」：段值不是活动 ID 时仍进编辑页（后台框架、当前项为促销活动）；
  // 多一段的地址不在路由表里，落到首页。
  it.each(["/admin/promotions/abc", "/admin/promotions/0", "/admin/promotions/-1"])("renders the edit page for %s", (path) => {
    expect(matchRoute(path)?.pattern).toBe("/admin/promotions/:id");
    const html = renderApp(path, "en");
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(element(html, "span", "site-admin__bar-title")).toBe(`<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_promotions"].en)}</span>`);
    expect(matchRoute("/admin/promotions/7/edit")).toBeNull();
    expect(resolvePath("/admin/promotions/7/edit")).toBe("/");
  });
});

describe("AdminPromotionEditPage", () => {
  // UX「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：新建与编辑经路由打开都渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），
  // 没有前台页头、页脚与前台演示横幅。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    for (const path of EDIT_PATHS) {
      const html = renderApp(path, language);
      expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
      expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
      expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
      expect(html).not.toMatch(/<(header|footer)\b/);
    }
  });

  // UX 0.20「管理后台总体」「导航项依次为 …、[admin.nav_coupons]、[admin.nav_promotions]（0.20）、[admin.nav_shipping]、…」
  // 与 A12「入口：后台导航 [admin.nav_promotions]（排在 [admin.nav_coupons] 之后）」：新建与编辑页上，桌面导航与 ☰ 菜单都依次为这十一项，
  // 促销活动（指向列表 /admin/promotions）排在优惠券之后、运费之前并标 aria-current="page"，其他项不标。
  it.each(LANGUAGES)("marks promotions current, after coupons and before shipping, in both navigation menus in %s", (language) => {
    const pages = [
      ...EDIT_PATHS.map((path) => renderApp(path, language)),
      ...EDIT_PATHS.map((path) => renderToStaticMarkup(wrap(<AdminPromotionEditPage />, language, path))),
      renderSignedIn(language, true),
    ];
    for (const html of pages) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual(
          NAV_PATHS.map((path) => (path === PROMOTIONS_PATH ? "page" : undefined)),
        );
        const labels = textNodes(nav).slice(0, 11);
        expect(labels).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
        expect(labels.indexOf(COPY["admin.nav_coupons"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_promotions"][language]));
        expect(labels.indexOf(COPY["admin.nav_promotions"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_shipping"][language]));
      }
    }
  });

  // UX「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：新建与编辑页的手机顶栏名称都为 admin.nav_promotions。
  it.each(LANGUAGES)("names the promotions item in the phone bar in %s", (language) => {
    for (const path of EDIT_PATHS) {
      expect(element(renderApp(path, language), "span", "site-admin__bar-title")).toBe(
        `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_promotions"][language])}</span>`,
      );
    }
  });

  // SHOP-TASK-270 替换 SHOP-TASK-268 的「内容区只有标题」：UX 0.20 A12 编辑区「([admin.promo_back]) → 列表    <内部名称>  <状态>」与
  // 「+---- [admin.promo_basic] ----」「+---- [admin.promo_combine] ----」「+---- [admin.promo_texts] ----」：已登录而读取返回前，内容区只有
  // admin.promo_back（标 aria-busy）；取到后（新建与已有活动）为标题行与基本信息、同用开关、文案三个区块（details.acs-admin__panel，summary 为区块标题）。
  // 会话返回前内容区仍为空。SHOP-TASK-271 起另有「+---- [admin.promo_rules] ----」，排在基本信息之后、同用开关之前，共四个区块。
  it.each(LANGUAGES)("shows the title row and the three sections in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toContain('aria-busy="true"');
    expect(tags(main, "a").map((tag) => attributes(tag).get("href"))).toEqual([PROMOTIONS_PATH]);
    expect(textNodes(main)).toEqual([COPY["admin.promo_back"][language]]);
    for (const editor of [openEditor(null, "csrf-1"), openEditor(promotion(), "csrf-1")]) {
      const html = renderView(ready(editor), language);
      const details = tags(html, "details");
      expect(details).toHaveLength(4);
      for (const tag of details) {
        expect(classes(tag)).toEqual(["acs-admin__panel", "site-admin-promo-edit__panel"]);
      }
      expect([...html.matchAll(/<summary>([^<]*)<\/summary>/g)].map((m) => unescapeHtml(m[1] ?? ""))).toEqual([
        COPY["admin.promo_basic"][language],
        COPY["admin.promo_rules"][language],
        COPY["admin.promo_combine"][language],
        COPY["admin.promo_texts"][language],
      ]);
    }
    for (const path of EDIT_PATHS) {
      expect(element(renderApp(path, language), "main", "acs-admin__main")).not.toMatch(/<h1\b/);
    }
  });

  // 任务目的「不调用接口」：渲染本页内容（会话返回前与已登录）不发任何请求（后台框架自己的会话读取在挂载后才发，服务端渲染不跑）。
  it("renders without any request", () => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const language of LANGUAGES) {
      for (const path of EDIT_PATHS) {
        renderApp(path, language);
      }
      renderSignedIn(language);
    }
    expect(request).not.toHaveBeenCalled();
  });

  // UX「管理后台总体」「表格列头、状态值、按钮与区块标题同样都用 `[key]`」：会话返回前、已登录（菜单收起与展开）时，
  // 每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [...EDIT_PATHS.map((path) => renderApp(path, language)), renderSignedIn(language), renderSignedIn(language, true)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

describe("AdminPromotionEditPage title row and sections", () => {
  // UX A12 编辑区「([admin.promo_back]) → 列表    <内部名称>  <状态>」：标题行为 admin.promo_back（链接到列表）、已保存的内部名称（h1，按纯文本）
  // 与状态标签（沿用列表的文字与类名）。
  it.each(LANGUAGES)("shows the back link, the saved name and the state tag in %s", (language) => {
    for (const state of ["draft", "scheduled", "running", "ended"] as const) {
      let editor = openEditor(promotion({ state }), "csrf-1");
      editor = changeField(editor, "name", "Unsaved name");
      const title = element(renderView(ready(editor), language), "div", "site-admin-promo-edit__title");
      expect(title).toContain(`<a class="site-admin-promo-edit__back" href="${PROMOTIONS_PATH}">${escapeHtml(COPY["admin.promo_back"][language])}</a>`);
      expect(title).toContain(`<h1 class="acs-admin__h">${escapeHtml("Lunar <b>New</b> Year")}</h1>`);
      expect(title).not.toContain("Unsaved name");
      expect(title).toContain(`<span class="${PROMOTION_STATE_CLASS[state]}">${escapeHtml(COPY[PROMOTION_STATE_LABEL[state]][language])}</span>`);
    }
  });

  // UX A12「编辑区（…新建时同一版式）」与落实写法第（6）点「新建而尚未保存时名称与状态标签都不显示」：新建时标题行只有 admin.promo_back。
  it.each(LANGUAGES)("shows only the back link in the title row of a new promotion in %s", (language) => {
    const html = renderView(ready(changeField(openEditor(null, "csrf-1"), "name", "Typed name")), language);
    const title = element(html, "div", "site-admin-promo-edit__title");
    expect(textNodes(title)).toEqual([COPY["admin.promo_back"][language]]);
    expect(html).not.toMatch(/<h1\b/);
    expect(html).not.toContain("acs-tag");
  });

  // UX A12「[admin.promo_name] [____]」「[admin.promo_start] [____-__-__ __:__]  [admin.promo_end] [____-__-__ __:__]」「[admin.promo_time_note]」：
  // 基本信息区为内部名称（maxLength 100）、开始与结束（datetime-local）与时间说明。
  it.each(LANGUAGES)("shows the basics section in %s", (language) => {
    const html = renderView(ready(openEditor(promotion(), "csrf-1")), language);
    const basics = element(html, "details", "site-admin-promo-edit__panel");
    const labels = textNodes(basics);
    expect(labels).toEqual([
      COPY["admin.promo_basic"][language],
      COPY["admin.promo_name"][language],
      COPY["admin.promo_start"][language],
      COPY["admin.promo_end"][language],
      COPY["admin.promo_time_note"][language],
    ]);
    expect(input(basics, "name").get("maxlength")).toBe("100");
    expect(input(basics, "name").get("value")).toBe("Lunar <b>New</b> Year");
    expect(input(basics, "starts_at").get("type")).toBe("datetime-local");
    expect(input(basics, "starts_at").get("value")).toBe("2027-01-20T00:00");
    expect(input(basics, "ends_at").get("type")).toBe("datetime-local");
    expect(input(basics, "ends_at").get("value")).toBe("2027-02-12T23:59");
  });

  // UX A12「☐ [admin.promo_allow_coupon]   ☐ [admin.promo_allow_points]」「[admin.promo_combine_hint]」：两个勾选框按表单的值勾选，其下为说明。
  it.each(LANGUAGES)("shows the combining section in %s", (language) => {
    const html = renderView(ready(openEditor(promotion(), "csrf-1")), language);
    expect(input(html, "coupon_combinable").get("type")).toBe("checkbox");
    expect(input(html, "coupon_combinable").has("checked")).toBe(true);
    expect(input(html, "points_combinable").get("type")).toBe("checkbox");
    expect(input(html, "points_combinable").has("checked")).toBe(false);
    const combine = element(html, "div", "site-admin-promo-edit__checks");
    expect(textNodes(combine)).toEqual([COPY["admin.promo_allow_coupon"][language], COPY["admin.promo_allow_points"][language]]);
    expect(html).toContain(escapeHtml(COPY["admin.promo_combine_hint"][language]));
  });

  // UX A12「[common.lang_en] [common.lang_zh] [common.lang_ms]」「[admin.copy_title] … <已用>/40」…「[admin.copy_fallback]」与手机「文案表格改为按语言分三组」：
  // 只有一套输入框，三语各一组（依次英、中、马），每组为标题、副标题、角标短语、横条文字，标签旁为已用字数；其下为 admin.copy_fallback。
  it.each(LANGUAGES)("shows one set of copy inputs grouped by language in %s", (language) => {
    const html = renderView(ready(openEditor(promotion(), "csrf-1")), language);
    const names = tags(html, "input")
      .map((tag) => attributes(tag).get("name") ?? "")
      .filter((name) => COPY_INPUTS.includes(name));
    expect([...names].sort()).toEqual([...COPY_INPUTS].sort());
    const groups = tags(html, "fieldset");
    expect(groups.map((tag) => attributes(tag).get("lang"))).toEqual(["en", "zh-Hans", "ms"]);
    expect([...html.matchAll(/<legend>([^<]*)<\/legend>/g)].map((m) => m[1])).toEqual(
      (["common.lang_en", "common.lang_zh", "common.lang_ms"] as const).map((key) => escapeHtml(COPY[key][language])),
    );
    const english = element(html, "fieldset", "site-admin-promo-edit__lang");
    expect(textNodes(english)).toEqual([
      COPY["common.lang_en"][language],
      COPY["admin.copy_title"][language],
      "19/40",
      COPY["admin.copy_subtitle"][language],
      "0/80",
      COPY["admin.copy_badge"][language],
      "10/12",
      COPY["admin.copy_strip"][language],
      "0/60",
    ]);
    expect(tags(english, "input").map((tag) => attributes(tag).get("name"))).toEqual(["title_en", "subtitle_en", "badge_en", "strip_en"]);
    expect(input(html, "title_zh").get("value")).toBe("农历新年特卖");
    expect(html).toContain(escapeHtml(COPY["admin.copy_fallback"][language]));
  });

  // UX A12 说明「文案：…输入时显示已用字数，超出不能保存」：已用字数为去掉首尾空白后的字符数（copyLength），超出上限也照常显示。
  it("counts the used characters of each copy field", () => {
    expect(copyCount("title_en", "  🎉ab  ")).toBe("3/40");
    expect(copyCount("badge_ms", "x".repeat(13))).toBe("13/12");
    let editor = changeCopy(openEditor(null, "csrf-1"), "strip_zh", " 新年 ");
    editor = changeCopy(editor, "subtitle_ms", "Jualan");
    const html = renderView(ready(editor), "en");
    expect(html).toContain('<span class="acs-admin__muted">2/60</span>');
    expect(html).toContain('<span class="acs-admin__muted">6/80</span>');
  });

  // UX「管理后台总体」「表格列头、状态值、按钮与区块标题同样都用 `[key]`」：编辑区除内部名称与已用字数外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides the name and the counts in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const state of ["draft", "running", "ended"] as const) {
      for (const value of visibleTexts(renderView(ready(openEditor(promotion({ state }), "csrf-1")), language))) {
        if (value === "Lunar <b>New</b> Year" || /^\d+\/\d+$/.test(value)) {
          continue;
        }
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

describe("AdminPromotionEditPage states", () => {
  function locks(html: string): Record<string, boolean> {
    const found: Record<string, boolean> = {};
    for (const name of ["name", ...LOCKABLE, ...COPY_INPUTS]) {
      const attrs = input(html, name);
      found[name] = attrs.has("readonly") || attrs.has("disabled");
    }
    return found;
  }

  // UX A12「状态与可用操作」「草稿 | 全部 | 保存、…」「已发布未开始 | 全部（保存时重新检查发布条件） | 保存、…」：草稿、未开始与新建时全部可改，有 common.save，
  // 没有只读说明。
  it.each(LANGUAGES)("lets drafts, scheduled and new promotions change everything in %s", (language) => {
    for (const editor of [openEditor(null, "csrf-1"), openEditor(promotion({ state: "draft" }), "csrf-1"), openEditor(promotion({ state: "scheduled" }), "csrf-1")]) {
      const html = renderView(ready(editor), language);
      expect(Object.values(locks(html)).every((locked) => !locked)).toBe(true);
      expect(element(html, "div", "site-admin-promo-edit__actions")).toContain(`<button class="acs-admin__btn" type="submit" aria-busy="false">${escapeHtml(COPY["common.save"][language])}</button>`);
      expect(html).not.toContain(escapeHtml(COPY["admin.promo_rules_locked"][language]));
      expect(html).not.toContain(escapeHtml(COPY["admin.promo_ended_readonly"][language]));
    }
  });

  // UX A12「进行中 | 内部名称、文案、装饰、海报、AI（开始与结束时间、规则与同用开关只读，显示 [admin.promo_rules_locked]…）| 保存、…」：
  // 进行中时开始、结束与两个开关只读，内部名称与文案可改，显示 admin.promo_rules_locked 与 common.save；改只读的项不生效。
  it.each(LANGUAGES)("locks the time and combining options of a running promotion in %s", (language) => {
    const editor = openEditor(promotion({ state: "running" }), "csrf-1");
    const html = renderView(ready(editor), language);
    const found = locks(html);
    for (const [name, locked] of Object.entries(found)) {
      expect(locked, name).toBe((LOCKABLE as readonly string[]).includes(name));
    }
    expect(countOf(html, escapeHtml(COPY["admin.promo_rules_locked"][language]))).toBe(1);
    expect(html).toContain('type="submit"');
    expect(changeField(editor, "starts_at", "2027-01-01T00:00")).toBe(editor);
    expect(changeField(editor, "points_combinable", true)).toBe(editor);
    expect(changeField(editor, "name", "New name").form.name).toBe("New name");
    expect(changeCopy(editor, "title_ms", "Jualan").form.copy.title_ms).toBe("Jualan");
  });

  // UX A12「已结束 | 无（全部只读，显示 [admin.promo_ended_readonly]） | 复制」：已结束时全部只读、不显示 common.save，显示 admin.promo_ended_readonly；
  // 改值与点保存都不生效。
  it.each(LANGUAGES)("makes an ended promotion read-only without save in %s", (language) => {
    const editor = openEditor(promotion({ state: "ended" }), "csrf-1");
    const html = renderView(ready(editor), language);
    expect(Object.values(locks(html)).every((locked) => locked)).toBe(true);
    expect(html).not.toContain('type="submit"');
    expect(html).not.toContain(escapeHtml(COPY["common.save"][language]));
    expect(countOf(html, escapeHtml(COPY["admin.promo_ended_readonly"][language]))).toBe(1);
    expect(html).not.toContain(escapeHtml(COPY["admin.promo_rules_locked"][language]));
    expect(changeField(editor, "name", "x")).toBe(editor);
    expect(changeCopy(editor, "title_en", "x")).toBe(editor);
    const submit = vi.fn();
    expect(pressSave(editor, submit)).toBe(editor);
    expect(submit).not.toHaveBeenCalled();
  });
});

describe("AdminPromotionEditPage field check", () => {
  // UX A12 说明「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查（结束晚于开始、不超过 92 天、…、文案长度），不合格的字段旁显示
  // [admin.promo_field_invalid] 并不提交」：内部名称为空、全是空白或含控制字符时，点保存不发请求，只在名称旁提示（名称下不另加说明）。
  it.each(LANGUAGES)("keeps an invalid name from being submitted in %s", (language) => {
    for (const name of ["", "   ", "a\u0007b"]) {
      const submit = vi.fn();
      const editor = pressSave(changeField(openEditor(promotion(), "csrf-1"), "name", name), submit);
      expect(submit).not.toHaveBeenCalled();
      expect(editor.saving).toBe(false);
      const html = renderView(ready(editor), language);
      expect(fieldInvalid(html, "name", language)).toBe(true);
      expect(input(html, "name").get("aria-invalid")).toBe("true");
      expect(countOf(html, escapeHtml(COPY["admin.promo_field_invalid"][language]))).toBe(1);
    }
  });

  // 同上原句（「结束晚于开始」「文案长度」）：结束不晚于开始时提示在结束旁，文案超出上限时提示在该文案旁；整次不提交，不调用任何请求。
  it.each(LANGUAGES)("marks the end time and an over-long copy field without any request in %s", async (language) => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    let editor = changeField(openEditor(promotion(), "csrf-1"), "ends_at", "2027-01-19T00:00");
    editor = changeCopy(editor, "title_en", "x".repeat(41));
    const record = recorder(ready(editor));
    const pressed = pressSave(editor, (current) => {
      void submitPromotion(current, new AbortController().signal, record.moves);
    });
    await Promise.resolve();
    expect(request).not.toHaveBeenCalled();
    const html = renderView(ready(pressed), language);
    expect(fieldInvalid(html, "ends_at", language)).toBe(true);
    expect(fieldInvalid(html, "title_en", language)).toBe(true);
    expect(fieldInvalid(html, "starts_at", language)).toBe(false);
    expect(fieldInvalid(html, "name", language)).toBe(false);
    expect(countOf(html, escapeHtml(COPY["admin.promo_field_invalid"][language]))).toBe(2);
    // 改正之后提示随当前的值消失。
    const fixed = changeCopy(changeField(pressed, "ends_at", "2027-02-01T00:00"), "title_en", "Sale");
    expect(renderView(ready(fixed), language)).not.toContain(escapeHtml(COPY["admin.promo_field_invalid"][language]));
  });

  // 同上原句：字段都合格时点保存才提交一次，进入保存中（按钮禁用并标 aria-busy），先前的字段提示收起。
  it("submits once when every field passes", () => {
    const submit = vi.fn();
    const editor = pressSave(openEditor(promotion(), "csrf-1"), submit);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(editor.saving).toBe(true);
    expect(renderView(ready(editor), "en")).toContain('<button class="acs-admin__btn" type="submit" disabled="" aria-busy="true">');
    expect(pressSave(editor, submit)).toBe(editor);
    expect(submit).toHaveBeenCalledTimes(1);
  });
});

describe("AdminPromotionEditPage reading", () => {
  // UX 页面地图「/admin/promotions/<活动 ID>」与落实写法第（3）点「段值不是不超过 2147483647 的正整数即不存在」：new 为新建，其余段值合法时为该活动。
  it("reads the target from the route", () => {
    expect(editTarget("/admin/promotions/new", {})).toEqual({ kind: "new" });
    expect(editTarget("/admin/promotions/:id", { id: "7" })).toEqual({ kind: "existing", id: 7 });
    expect(editTarget("/admin/promotions/:id", { id: "2147483647" })).toEqual({ kind: "existing", id: 2147483647 });
    for (const id of ["abc", "0", "-1", "007", "1.5", "2147483648", "99999999999999999999"]) {
      expect(editTarget("/admin/promotions/:id", { id })).toEqual({ kind: "invalid" });
    }
  });

  // UX A12 去向「活动编辑区」与落实写法第（3）点：段值不合法时不发请求，只显示 common.error_retry 与 admin.promo_back，不渲染表单。
  it.each(LANGUAGES)("shows only the error and the back link for an invalid segment in %s", async (language) => {
    const record = recorder();
    await loadEditor({ kind: "invalid" }, new AbortController().signal, record.moves, null, fakeApi({}));
    expect(record.state).toEqual({ status: "gone", error: "common.error_retry" });
    const html = renderView(record.state, language);
    expect(html).not.toMatch(/<(form|details|input)\b/);
    expect(textNodes(html)).toEqual([COPY["admin.promo_back"][language], COPY["common.error_retry"][language]]);
  });

  // UX A12「编辑区（…新建时同一版式）」：新建先读列表只取 CSRF 令牌，取到后为空表单；401 回到 A01；网络中断只显示 common.network_check 与
  // admin.promo_back，其他失败只显示 common.error_retry 与 admin.promo_back。
  it("opens a new promotion after reading the CSRF token", async () => {
    const reads: [PromotionsRead, EditState | null, RoutePath[]][] = [
      [{ kind: "ok", promotions: [], csrf_token: "csrf-new" }, ready(openEditor(null, "csrf-new")), []],
      [{ kind: "none" }, null, ["/admin/login"]],
      [{ kind: "network" }, { status: "gone", error: "common.network_check" }, []],
      [{ kind: "failed" }, { status: "gone", error: "common.error_retry" }, []],
    ];
    for (const [read, state, replaced] of reads) {
      const readList = vi.fn(() => Promise.resolve(read));
      const record = recorder();
      await loadEditor({ kind: "new" }, new AbortController().signal, record.moves, null, fakeApi({ readList }));
      expect(readList).toHaveBeenCalledTimes(1);
      expect(record.state).toEqual(state ?? LOADING);
      expect(record.replaced).toEqual(replaced);
    }
    for (const language of LANGUAGES) {
      const html = renderView({ status: "gone", error: "common.network_check" }, language);
      expect(textNodes(html)).toEqual([COPY["admin.promo_back"][language], COPY["common.network_check"][language]]);
      expect(html).not.toMatch(/<(form|details|input)\b/);
    }
  });

  // UX A12「编辑区（桌面，/admin/promotions/<活动 ID>）」：已有活动按 ID 读取，取到后以它为基准；不存在只显示 common.error_retry 与 admin.promo_back；
  // 401 回到 A01；网络中断 common.network_check，其他失败 common.error_retry。
  it("opens an existing promotion", async () => {
    const found = promotion();
    const reads: [PromotionRead, EditState | null, RoutePath[]][] = [
      [{ kind: "ok", promotion: found, csrf_token: "csrf-7" }, ready(openEditor(found, "csrf-7")), []],
      [{ kind: "missing" }, { status: "gone", error: "common.error_retry" }, []],
      [{ kind: "none" }, null, ["/admin/login"]],
      [{ kind: "network" }, { status: "gone", error: "common.network_check" }, []],
      [{ kind: "failed" }, { status: "gone", error: "common.error_retry" }, []],
    ];
    for (const [read, state, replaced] of reads) {
      const readOne = vi.fn(() => Promise.resolve(read));
      const record = recorder();
      const signal = new AbortController().signal;
      await loadEditor({ kind: "existing", id: 7 }, signal, record.moves, null, fakeApi({ readOne }));
      expect(readOne).toHaveBeenCalledWith(7, signal);
      expect(record.state).toEqual(state ?? LOADING);
      expect(record.replaced).toEqual(replaced);
    }
    const editor = openEditor(found, "csrf-7");
    expect(editor.base).toEqual(formFromPromotion(found));
    expect(editor.form).toBe(editor.base);
  });

  // UX A12 说明「前台预览」末句「离开有未保存修改的编辑区时沿用 A04、A10 的离开确认」与落实写法第（3）（8）点（已有活动以 readAdminPromotion 读取；离开照 A10）：
  // 离开之后（读取被中止）读到的结果不再更新页面，也不回到 A01。
  it("ignores a read that finishes after leaving the page", async () => {
    const controller = new AbortController();
    const record = recorder();
    const readOne = vi.fn(() => {
      controller.abort();
      return Promise.resolve<PromotionRead>({ kind: "none" });
    });
    await loadEditor({ kind: "existing", id: 7 }, controller.signal, record.moves, null, fakeApi({ readOne }));
    expect(record.state).toBe(LOADING);
    expect(record.replaced).toEqual([]);
  });
});

describe("AdminPromotionEditPage saving", () => {
  // UX A12「操作栏：( [common.save] )」：已有活动以 saveAdminPromotion 提交表单的内容与读取到的 version，新建以 createAdminPromotion 提交，都带 CSRF 令牌。
  it("submits the form with the version read", async () => {
    const editor = editedEditor();
    const { api, signal } = await submitWith(editor, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(editor.form), version: 3 }, "csrf-1", signal);
    const draft = changeField(openEditor(null, "csrf-new"), "name", "New sale");
    const created = await submitWith(draft, { kind: "failed" });
    expect(created.api.create).toHaveBeenCalledWith(promotionInput(draft.form), "csrf-new", created.signal);
    expect(created.api.save).not.toHaveBeenCalled();
  });

  // UX A12「结果提示：[admin.promo_saved] / …」：保存成功后以返回的活动为新基准（新的 version，没有未保存修改），显示 admin.promo_saved。
  it.each(LANGUAGES)("shows saved and takes the returned promotion as the new base in %s", async (language) => {
    const saved = promotion({ version: 4, name: "Renamed", coupon_combinable: false, starts_at: "2027-01-21T09:30" });
    const { record } = await submitWith(editedEditor(), { kind: "ok", promotion: saved });
    const editor = editorOf(record.state);
    expect(editor.promotion).toEqual(saved);
    expect(editor.base).toEqual(formFromPromotion(saved));
    expect(hasUnsavedChanges(record.state)).toBe(false);
    expect(editor.saving).toBe(false);
    expect(renderView(record.state, language)).toContain(
      `<p class="site-admin-promo-edit__done" role="status">${escapeHtml(COPY["admin.promo_saved"][language])}</p>`,
    );
  });

  // 落实写法第（3）点「新建…成功后以 replace 换到 adminPromotionPath(新 ID)，新页面显示 admin.promo_saved（…挂载时只对同一 ID 取用一次）」：
  // 新建成功换到新 ID 的页面，读取后显示 admin.promo_saved；再次打开（含其他 ID）不再显示。
  it.each(LANGUAGES)("moves a created promotion to its own page and shows saved there once in %s", async (language) => {
    const created = promotion({ id: 42, version: 1, name: "New sale" });
    const { record } = await submitWith(changeField(openEditor(null, "csrf-new"), "name", "New sale"), { kind: "ok", promotion: created });
    expect(record.replaced).toEqual([adminPromotionPath(42)]);
    const page = recorder();
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: created, csrf_token: "csrf-42" }));
    await loadEditor({ kind: "existing", id: 42 }, new AbortController().signal, page.moves, takePromotionNotice(42), fakeApi({ readOne }));
    expect(editorOf(page.state).notice).toEqual({ kind: "done", key: "admin.promo_saved" });
    expect(renderView(page.state, language)).toContain(escapeHtml(COPY["admin.promo_saved"][language]));
    expect(takePromotionNotice(42)).toBeNull();
    stashPromotionNotice(42, "admin.promo_saved" satisfies CopyKey);
    expect(takePromotionNotice(43)).toBeNull();
    expect(takePromotionNotice(42)).toBeNull();
  });

  // UX A12 说明「规则」「取值超出范围、商品级规则重叠、发布条件不满足时整次不生效：[admin.promo_invalid] 下逐条列出问题（重叠为
  // [admin.promo_scope_overlap]，{product} 为商品名称；发布条件为 [admin.promo_publish_missing]）」与「…除商品级规则重叠…与发布条件之外，
  // [admin.promo_invalid] 下只显示 [admin.promo_invalid_other]」：重叠每件一行（名称按纯文本），发布条件与其他问题码各只一行。
  it.each(LANGUAGES)("lists the server's problems under promo_invalid in %s", async (language) => {
    const problems = [
      problem("scope_overlap", "Crew <Tee>"),
      problem("publish_missing"),
      problem("name"),
      problem("scope_overlap", "Soy Wax Candle"),
      problem("publish_missing"),
      problem("tiers"),
    ];
    const editor = editedEditor();
    const { record } = await submitWith(editor, { kind: "invalid", problems });
    expect(editorOf(record.state).form).toEqual(editor.form);
    const alert = element(renderView(record.state, language), "div", "site-admin-promo-edit__invalid");
    expect(attributes(tags(alert, "div")[0] ?? "").get("role")).toBe("alert");
    expect(textNodes(alert)).toEqual([
      COPY["admin.promo_invalid"][language],
      formatCopy(COPY["admin.promo_scope_overlap"][language], { product: "Crew <Tee>" }),
      formatCopy(COPY["admin.promo_scope_overlap"][language], { product: "Soy Wax Candle" }),
      COPY["admin.promo_publish_missing"][language],
      COPY["admin.promo_invalid_other"][language],
    ]);
    expect(alert).toContain(escapeHtml("Crew <Tee>"));
  });

  // 落实写法第（4）点「invalid 的 problems 里有 ended_readonly（页面打开期间活动结束）时按 changed 处理」与 UX A12「版本不符为
  // [admin.promo_changed] 与 ( [admin.reload] )，不自动合并」。
  it.each(LANGUAGES)("treats ended_readonly as changed in %s", async (language) => {
    const { record } = await submitWith(editedEditor(), { kind: "invalid", problems: [problem("name"), problem("ended_readonly")] });
    expect(editorOf(record.state).notice).toEqual({ kind: "changed" });
    const html = renderView(record.state, language);
    expect(element(html, "div", "site-admin-promo-edit__changed")).toBe(
      `<div class="acs-admin__alert site-admin-promo-edit__changed" role="alert"><span>${escapeHtml(COPY["admin.promo_changed"][language])}</span>` +
        `<button class="acs-admin__btn acs-admin__btn--secondary" type="button">${escapeHtml(COPY["admin.reload"][language])}</button></div>`,
    );
    expect(html).not.toContain(escapeHtml(COPY["admin.promo_invalid"][language]));
  });

  // UX A12「[admin.promo_rules_locked]（已开始时显示；时间、规则与同用开关只读）」与落实写法第（4）点 rules_locked：开始与结束、两个开关恢复为基准的值
  // 并只读，内部名称与文案的修改保留、可再保存；其他问题照常列在 admin.promo_invalid 下（不含 rules_locked）；之后保存成功也不解除，admin.reload 才解除。
  it.each(LANGUAGES)("restores and locks the time and combining options on rules_locked in %s", async (language) => {
    const editor = editedEditor({ state: "scheduled" });
    const { record } = await submitWith(editor, {
      kind: "invalid",
      problems: [problem("rules_locked"), problem("scope_overlap", "Crew Tee"), problem("name")],
    });
    const locked = editorOf(record.state);
    expect(locked.locked).toBe(true);
    expect(locked.saving).toBe(false);
    expect(locked.form).toEqual({ ...editor.form, starts_at: "2027-01-20T00:00", ends_at: "2027-02-12T23:59", coupon_combinable: true, points_combinable: false });
    expect(locked.form.name).toBe("Renamed");
    expect(locked.form.copy.title_en).toBe("Changed title");
    expect(hasUnsavedChanges(record.state)).toBe(true);
    let html = renderView(record.state, language);
    for (const name of LOCKABLE) {
      const attrs = input(html, name);
      expect(attrs.has("readonly") || attrs.has("disabled"), name).toBe(true);
    }
    expect(input(html, "name").has("readonly")).toBe(false);
    expect(input(html, "title_en").has("readonly")).toBe(false);
    expect(html).toContain('type="submit"');
    expect(countOf(html, escapeHtml(COPY["admin.promo_rules_locked"][language]))).toBe(1);
    expect(textNodes(element(html, "div", "site-admin-promo-edit__invalid"))).toEqual([
      COPY["admin.promo_invalid"][language],
      formatCopy(COPY["admin.promo_scope_overlap"][language], { product: "Crew Tee" }),
      COPY["admin.promo_invalid_other"][language],
    ]);
    expect(changeField(locked, "ends_at", "2027-03-01T00:00")).toBe(locked);

    // 只有 rules_locked 时 admin.promo_invalid 不出现。
    const only = await submitWith(editor, { kind: "invalid", problems: [problem("rules_locked")] });
    expect(editorOf(only.record.state).notice).toBeNull();
    expect(renderView(only.record.state, language)).not.toContain(escapeHtml(COPY["admin.promo_invalid"][language]));

    // 再保存成功：锁定保留。
    const again = await submitWith(locked, { kind: "ok", promotion: promotion({ state: "scheduled", version: 4, name: "Renamed" }) });
    expect(editorOf(again.record.state).locked).toBe(true);
    html = renderView(again.record.state, language);
    expect(input(html, "starts_at").has("readonly")).toBe(true);
    expect(html).toContain(escapeHtml(COPY["admin.promo_rules_locked"][language]));

    // admin.reload：重新读取，丢掉本页修改与锁定。
    const reload = recorder(again.record.state);
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: promotion({ state: "scheduled", version: 4 }), csrf_token: "csrf-9" }));
    await reloadEditor({ kind: "existing", id: 7 }, new AbortController().signal, reload.moves, fakeApi({ readOne }));
    expect(editorOf(reload.state).locked).toBe(false);
    expect(renderView(reload.state, language)).not.toContain(escapeHtml(COPY["admin.promo_rules_locked"][language]));
  });

  // UX A12「与其他已发布活动时间重叠为 [admin.promo_overlap]（{name} 为对方的内部名称）」：对方的内部名称按纯文本显示。
  it.each(LANGUAGES)("shows the overlapping promotion's name in %s", async (language) => {
    const { record } = await submitWith(editedEditor(), { kind: "overlap", name: "Hari <Raya>" });
    const html = renderView(record.state, language);
    expect(html).toContain(
      `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(formatCopy(COPY["admin.promo_overlap"][language], { name: "Hari <Raya>" }))}</span></div>`,
    );
  });

  // UX A12「版本不符为 [admin.promo_changed] 与 ( [admin.reload] )，不自动合并」与落实写法第（4）点「changed、state 与 ended 都显示 admin.promo_changed 与
  // admin.reload（重新读取并丢掉本页修改…）」：本页的修改保留到点 admin.reload，重新读取后为读到的版本。
  it.each(LANGUAGES)("shows changed with reload for changed, state and ended in %s", async (language) => {
    const writes: PromotionWrite[] = [{ kind: "changed" }, { kind: "state", state: "running" }, { kind: "ended" }];
    for (const write of writes) {
      const editor = editedEditor();
      const { record } = await submitWith(editor, write);
      expect(editorOf(record.state).notice).toEqual({ kind: "changed" });
      expect(editorOf(record.state).form).toEqual(editor.form);
      const html = renderView(record.state, language);
      expect(html).toContain(escapeHtml(COPY["admin.promo_changed"][language]));
      expect(html).toContain(escapeHtml(COPY["admin.reload"][language]));
    }
    const latest = promotion({ version: 8, name: "Changed elsewhere" });
    const reload = recorder(ready(editedEditor()));
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: latest, csrf_token: "csrf-8" }));
    await reloadEditor({ kind: "existing", id: 7 }, new AbortController().signal, reload.moves, fakeApi({ readOne }));
    expect(reload.state).toEqual(ready(openEditor(latest, "csrf-8")));
  });

  // 落实写法第（4）点「missing 与不存在时只显示 common.error_retry 与 admin.promo_back；401 回到 A01」。
  it.each(LANGUAGES)("handles missing and no session in %s", async (language) => {
    const missing = await submitWith(editedEditor(), { kind: "missing" });
    expect(missing.record.state).toEqual({ status: "gone", error: "common.error_retry" });
    expect(textNodes(renderView(missing.record.state, language))).toEqual([COPY["admin.promo_back"][language], COPY["common.error_retry"][language]]);
    const none = await submitWith(editedEditor(), { kind: "none" });
    expect(none.record.replaced).toEqual(["/admin/login"]);
  });

  // 落实写法第（4）点「网络中断 common.network_check，其他失败 common.error_retry」：本页的修改保留，可再保存。
  it.each(LANGUAGES)("shows network and other failures in %s", async (language) => {
    const cases: [PromotionWrite, "common.network_check" | "common.error_retry"][] = [
      [{ kind: "network" }, "common.network_check"],
      [{ kind: "failed" }, "common.error_retry"],
    ];
    for (const [write, key] of cases) {
      const editor = editedEditor();
      const { record } = await submitWith(editor, write);
      expect(editorOf(record.state)).toEqual({ ...editor, notice: { kind: "alert", key } });
      expect(renderView(record.state, language)).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`);
    }
  });

  // 落实写法第（4）点 csrf：显示 common.error_retry 并重新取 CSRF 令牌（已有活动用 readAdminPromotion、新建用 readAdminPromotions，只取 csrf_token，
  // 表单、基准与版本号都不变）；重取遇到 401 回到 A01，其他失败只保留 common.error_retry、不再重试。
  it("reads a new CSRF token after csrf", async () => {
    const editor = editedEditor();
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: promotion({ version: 9, name: "Elsewhere" }), csrf_token: "csrf-2" }));
    const existing = await submitWith(editor, { kind: "csrf" }, { readOne });
    expect(readOne).toHaveBeenCalledTimes(1);
    expect(readOne).toHaveBeenCalledWith(7, existing.signal);
    expect(editorOf(existing.record.state)).toEqual({ ...editor, csrfToken: "csrf-2", notice: { kind: "alert", key: "common.error_retry" } });

    const draft = changeField(openEditor(null, "csrf-1"), "name", "New sale");
    const readList = vi.fn(() => Promise.resolve<PromotionsRead>({ kind: "ok", promotions: [], csrf_token: "csrf-3" }));
    const created = await submitWith(draft, { kind: "csrf" }, { readList });
    expect(readList).toHaveBeenCalledTimes(1);
    expect(editorOf(created.record.state)).toEqual({ ...draft, csrfToken: "csrf-3", notice: { kind: "alert", key: "common.error_retry" } });

    const none = await submitWith(editor, { kind: "csrf" }, { readOne: vi.fn(() => Promise.resolve<PromotionRead>({ kind: "none" })) });
    expect(none.record.replaced).toEqual(["/admin/login"]);

    for (const read of [{ kind: "failed" }, { kind: "network" }, { kind: "missing" }] as PromotionRead[]) {
      const again = vi.fn(() => Promise.resolve(read));
      const failed = await submitWith(editor, { kind: "csrf" }, { readOne: again });
      expect(again).toHaveBeenCalledTimes(1);
      expect(failed.api.save).toHaveBeenCalledTimes(1);
      expect(editorOf(failed.record.state)).toEqual({ ...editor, notice: { kind: "alert", key: "common.error_retry" } });
    }
    expect(csrfStep({ kind: "none" })).toBe("login");
  });

  // UX A12 说明「规则」中「版本不符为 [admin.promo_changed] 与 ( [admin.reload] )，不自动合并」与落实写法第（4）点「admin.reload（重新读取并丢掉本页修改，不自动合并）」：
  // 点 admin.reload 重新读取（或离开、读取被中止）之后，先前那次保存的结果不覆盖重新读取的页面。
  it("ignores a save result once the editor is no longer saving", () => {
    const step = saveStep({ kind: "network" }, false);
    if (step.kind !== "change") {
      throw new Error("expected a change");
    }
    expect(step.change(LOADING)).toBe(LOADING);
    const idle = ready(openEditor(promotion(), "csrf-1"));
    expect(step.change(idle)).toBe(idle);
  });
});

describe("AdminPromotionEditPage panels and leaving", () => {
  // UX A12 手机「同一编辑区单列排列，各区块可折叠（默认展开 [admin.promo_basic] 与 [admin.promo_rules]）」：首次渲染（含服务端渲染）全部展开；
  // 挂载后在 (max-width: 767px) 成立时收起基本信息之外的区块，桌面不收起。
  it("starts with every section open and folds all but the basics on phones", () => {
    expect(tags(renderView(ready(openEditor(null, "csrf-1")), "en"), "details").every((tag) => attributes(tag).has("open"))).toBe(true);
    let panels: PanelsOpen = ALL_OPEN;
    const queries: string[] = [];
    collapseOnPhone(
      (query) => {
        queries.push(query);
        return true;
      },
      (next) => {
        panels = typeof next === "function" ? next(panels) : next;
      },
    );
    expect(queries).toEqual([PHONE_QUERY]);
    expect(PHONE_QUERY).toBe("(max-width: 767px)");
    expect(panels).toEqual({ basic: true, rules: true, combine: false, texts: false });
    expect(tags(renderView(ready(openEditor(null, "csrf-1")), "en", panels), "details").map((tag) => attributes(tag).has("open"))).toEqual([true, true, false, false]);
    expect(phonePanels(ALL_OPEN, false)).toBe(ALL_OPEN);
    const set = vi.fn();
    collapseOnPhone(null, set);
    expect(set).not.toHaveBeenCalled();
  });

  // UX A12 说明「前台预览」「离开有未保存修改的编辑区时沿用 A04、A10 的离开确认」：读取到的值与当前的值不同时才有未保存修改（交给 useUnsavedConfirm）。
  it("reports unsaved changes only when the form differs from the base", () => {
    expect(hasUnsavedChanges(LOADING)).toBe(false);
    expect(hasUnsavedChanges({ status: "gone", error: "common.error_retry" })).toBe(false);
    const editor = openEditor(promotion(), "csrf-1");
    expect(hasUnsavedChanges(ready(editor))).toBe(false);
    const changed = changeCopy(editor, "strip_ms", "Jualan");
    expect(hasUnsavedChanges(ready(changed))).toBe(true);
    expect(hasUnsavedChanges(ready(changeCopy(changed, "strip_ms", "")))).toBe(false);
    expect(hasUnsavedChanges(ready(changeField(openEditor(null, "csrf-1"), "points_combinable", true)))).toBe(true);
  });
});

describe("AdminPromotionEditPage rules", () => {
  function readRule(kind: string, overrides: Partial<AdminPromotionRule> = {}): AdminPromotionRule {
    return {
      rule_id: 1,
      kind,
      discount_mode: null,
      percent: null,
      amount_sen: null,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
      threshold_sen: null,
      product_ids: [],
      category_ids: [],
      tiers: [],
      products: [],
      ...overrides,
    };
  }

  // 读回的两条规则：每件直降 20%（分类 3）与免运费门槛 RM120。
  const READ_RULES = [
    readRule("item_discount", { rule_id: 31, discount_mode: "percent", percent: 20, category_ids: [3] }),
    readRule("free_shipping", { rule_id: 32, threshold_sen: 12000 }),
  ];
  const LISTS: ScopeLists = {
    status: "ok",
    categories: [{ id: 3, slug: "apparel", name_en: "Apparel", name_zh: "服装", name_ms: null, is_active: true, version: 1, visible_products: 2 }],
    products: [],
  };

  function renderWithLists(editor: Editor, language: Language): string {
    return renderToStaticMarkup(
      wrap(<AdminPromotionEditView state={ready(editor)} panels={ALL_OPEN} handlers={NO_HANDLERS} lists={LISTS} />, language, EDIT_PATH),
    );
  }

  // UX 0.20 A12 编辑区线框「+---- [admin.promo_basic] ----」…「+---- [admin.promo_rules] ----」「1 [admin.rule_item_discount] …」…「+---- [admin.promo_combine] ----」：
  // 规则区排在基本信息之后、同用开关与文案之前，按读取到的规则显示（金额为 RM 文字），分类名称按当前语言。
  it.each(LANGUAGES)("places the rules section after the basics and before combining in %s", (language) => {
    const html = renderWithLists(openEditor(promotion({ rules: READ_RULES }), "csrf-1"), language);
    const at = (text: string) => html.indexOf(text);
    const summary = (key: CopyKey) => at(`<summary>${escapeHtml(COPY[key][language])}</summary>`);
    expect(summary("admin.promo_basic")).toBeLessThan(summary("admin.promo_rules"));
    expect(summary("admin.promo_rules")).toBeLessThan(at('name="rules.1.percent"'));
    expect(at('name="rules.2.threshold_sen"')).toBeLessThan(summary("admin.promo_combine"));
    expect(summary("admin.promo_combine")).toBeLessThan(summary("admin.promo_texts"));
    expect(input(html, "rules.1.percent").get("value")).toBe("20");
    expect(input(html, "rules.2.threshold_sen").get("value")).toBe("120.00");
    expect(input(html, "rules.1.category_ids").get("checked")).toBe("");
    expect(html).toContain(escapeHtml(language === "zh" ? "服装" : "Apparel"));
    expect(html).toContain(`aria-label="${escapeHtml(COPY["admin.rule_add"][language])}"`);
  });

  // UX 0.20 A12 说明「规则」中「(↑)(↓) 调整规则排序」与「前台预览」「离开有未保存修改的编辑区时沿用 A04、A10 的离开确认」：规则的增删、排序与改值都算未保存的
  // 修改（换回原样即不再算），并照当前的顺序与值提交；规则不变时编辑区不变。
  it("counts rule changes as unsaved and submits them", async () => {
    const editor = openEditor(promotion({ rules: READ_RULES }), "csrf-1");
    expect(hasUnsavedChanges(ready(editor))).toBe(false);
    expect(changeRules(editor, (form) => form.rules)).toBe(editor);
    const added = changeRules(editor, (form) => addRule(form, "buy_x_get_y"));
    expect(added.form.rules.map((rule) => rule.kind)).toEqual(["item_discount", "free_shipping", "buy_x_get_y"]);
    expect(hasUnsavedChanges(ready(added))).toBe(true);
    const moved = changeRules(editor, (form) => moveRule(form.rules, 1, -1));
    expect(hasUnsavedChanges(ready(moved))).toBe(true);
    expect(promotionInput(moved.form).rules.map((rule) => rule.kind)).toEqual(["free_shipping", "item_discount"]);
    expect(hasUnsavedChanges(ready(changeRules(moved, (form) => moveRule(form.rules, 1, 1))))).toBe(false);
    expect(hasUnsavedChanges(ready(changeRules(editor, (form) => removeRule(form.rules, 0))))).toBe(true);
    const edited = changeRules(editor, (form) => mapRule(form.rules, 1, (rule) => (rule.kind === "free_shipping" ? { ...rule, threshold: "99.50" } : rule)));
    expect(hasUnsavedChanges(ready(edited))).toBe(true);
    expect(promotionInput(edited.form).rules[1]?.threshold_sen).toBe(9950);
    const { api, signal } = await submitWith(moved, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(moved.form), version: 3 }, "csrf-1", signal);
  });

  // UX 0.20 A12 说明「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查（…百分比与金额范围…范围至多 200 件商品与 30 个分类…），不合格的字段旁显示
  // [admin.promo_field_invalid] 并不提交」：新加的每件直降未填百分比、范围为空时点保存不提交，百分比与范围旁各提示一次；填好之后提示消失、才提交。
  it.each(LANGUAGES)("keeps invalid rules from being submitted in %s", (language) => {
    const editor = changeRules(changeField(openEditor(null, "csrf-1"), "name", "Sale"), (form) => addRule(form, "item_discount"));
    const submit = vi.fn();
    const pressed = pressSave(editor, submit);
    expect(submit).not.toHaveBeenCalled();
    expect(pressed.saving).toBe(false);
    const html = renderWithLists(pressed, language);
    expect(fieldInvalid(html, "rules.1.percent", language)).toBe(true);
    expect(input(html, "rules.1.percent").get("aria-invalid")).toBe("true");
    expect(countOf(html, escapeHtml(COPY["admin.promo_field_invalid"][language]))).toBe(2);
    const fixed = changeRules(pressed, (form) =>
      mapRule(form.rules, 0, (rule) => (rule.kind === "item_discount" ? { ...rule, discount: "20", category_ids: [3] } : rule)),
    );
    expect(renderWithLists(fixed, language)).not.toContain(escapeHtml(COPY["admin.promo_field_invalid"][language]));
    pressSave(fixed, submit);
    expect(submit).toHaveBeenCalledTimes(1);
  });

  // UX 0.20 A12「状态与可用操作」「进行中 | …（开始与结束时间、规则与同用开关只读，显示 [admin.promo_rules_locked]…）」「已结束 | 无（全部只读…）」与
  // 「[admin.promo_rules_locked]（已开始时显示；时间、规则与同用开关只读）」：进行中、已结束与保存被答 rules_locked 之后，规则恢复为读取时的值并只读
  // （不显示添加、排序与移除，改动不生效）；未开始时可改。
  it.each(LANGUAGES)("makes the rules read-only when running, ended or locked in %s", async (language) => {
    const readOnly = (html: string) => {
      expect(html).not.toContain('name="rule_add"');
      expect(html).not.toContain(`aria-label="${escapeHtml(COPY["admin.rule_move_up"][language])}"`);
      expect(html).not.toContain(`>${escapeHtml(COPY["admin.rule_remove"][language])}</button>`);
      expect(input(html, "rules.1.percent").has("readonly")).toBe(true);
      expect(input(html, "rules.2.threshold_sen").has("readonly")).toBe(true);
    };
    for (const state of ["running", "ended"] as const) {
      const editor = openEditor(promotion({ state, rules: READ_RULES }), "csrf-1");
      expect(changeRules(editor, (form) => addRule(form, "buy_x_get_y"))).toBe(editor);
      readOnly(renderWithLists(editor, language));
    }
    const scheduled = openEditor(promotion({ state: "scheduled", rules: READ_RULES }), "csrf-1");
    const open = renderWithLists(scheduled, language);
    expect(open).toContain('name="rule_add"');
    expect(input(open, "rules.1.percent").has("readonly")).toBe(false);

    const edited = changeRules(changeField(scheduled, "name", "Renamed"), (form) => removeRule(form.rules, 0));
    const { record } = await submitWith(edited, { kind: "invalid", problems: [problem("rules_locked")] });
    const locked = editorOf(record.state);
    expect(locked.form.rules).toEqual(scheduled.base.rules);
    expect(locked.form.name).toBe("Renamed");
    expect(changeRules(locked, (form) => removeRule(form.rules, 0))).toBe(locked);
    readOnly(renderWithLists(locked, language));
  });
});
