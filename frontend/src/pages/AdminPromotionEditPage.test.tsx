import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiProposal } from "../api/adminPromotionAi";
import type { AdminPromotion, AdminPromotionRule, PromotionDelete, PromotionProblem, PromotionRead, PromotionsRead, PromotionWrite } from "../api/adminPromotions";
import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PROMOTION_NEW_PATH, adminPromotionPath, matchRoute, resolvePath, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminPromotionEditPage, {
  ACTION_LABEL,
  actionStep,
  ALL_OPEN,
  AdminPromotionEditView,
  applyAiProposal,
  barActions,
  cancelConfirm,
  confirmAction,
  malaysiaMinute,
  openPreview,
  pressAction,
  previewNeedsTime,
  PromotionConfirm,
  runAction,
  STATE_ACTIONS,
  toggleMore,
  changeCopy,
  changeDecoration,
  changeField,
  changeRules,
  checkAiRules,
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
  replacePoster,
  rereadAiToken,
  rereadPosterToken,
  saveStep,
  shownProblems,
  startAction,
  stashPromotionNotice,
  startSaving,
  submitPromotion,
  takePromotionNotice,
} from "./AdminPromotionEditPage";
import type { ActionMoves, BarAction, Editor, EditHandlers, EditMoves, EditState, PanelsOpen, PromotionActionApi, PromotionApi, StateAction } from "./AdminPromotionEditPage";
import { PACK_LABEL, pickFestivalPalette, pickPack, pickVariant } from "./AdminPromotionDecoration";
import { pickPosterMode } from "./AdminPromotionPoster";
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
  onDecoration: () => undefined,
  onSave: () => undefined,
  onReload: () => undefined,
  onPanel: () => undefined,
  onPoster: () => undefined,
  onPosterReread: () => Promise.resolve("failed"),
  onLogin: () => undefined,
  onAiApply: () => undefined,
  onAiRulesChecked: () => undefined,
  onAiReread: () => Promise.resolve("failed"),
  onAction: () => undefined,
  onConfirm: () => undefined,
  onCancelConfirm: () => undefined,
  onMore: () => undefined,
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
  // 会话返回前内容区仍为空。SHOP-TASK-271 起另有「+---- [admin.promo_rules] ----」，排在基本信息之后、同用开关之前，共四个区块；
  // SHOP-TASK-273 起另有「+---- [admin.promo_decoration] ----」，排在文案之后，共五个区块；SHOP-TASK-275 起另有「+---- [admin.promo_poster] ----」，
  // 排在装饰之后，共六个区块；SHOP-TASK-276 起另有「+---- [admin.ai_title] ----」，排在海报之后，共七个区块。
  it.each(LANGUAGES)("shows the title row and the three sections in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toContain('aria-busy="true"');
    expect(tags(main, "a").map((tag) => attributes(tag).get("href"))).toEqual([PROMOTIONS_PATH]);
    expect(textNodes(main)).toEqual([COPY["admin.promo_back"][language]]);
    for (const editor of [openEditor(null, "csrf-1"), openEditor(promotion(), "csrf-1")]) {
      const html = renderView(ready(editor), language);
      const details = tags(html, "details");
      expect(details).toHaveLength(7);
      for (const tag of details) {
        expect(classes(tag)).toEqual(["acs-admin__panel", "site-admin-promo-edit__panel"]);
      }
      expect([...html.matchAll(/<summary>([^<]*)<\/summary>/g)].map((m) => unescapeHtml(m[1] ?? ""))).toEqual([
        COPY["admin.promo_basic"][language],
        COPY["admin.promo_rules"][language],
        COPY["admin.promo_combine"][language],
        COPY["admin.promo_texts"][language],
        COPY["admin.promo_decoration"][language],
        COPY["admin.promo_poster"][language],
        COPY["admin.ai_title"][language],
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
  // SHOP-TASK-273 起装饰区的 admin.variant_n 与 admin.palette_of 带变量，按填入序号与素材包名称之后的文字计（UX A12 说明「装饰」）。
  // SHOP-TASK-274 起装饰区末尾的编辑区预览另显示活动的文案（读到的标题与角标短语，缺译用英文）与 common.price_myr 填入的示意金额（UX A12 说明「编辑区预览」）。
  it.each(LANGUAGES)("shows only dictionary text besides the name and the counts in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const n of [1, 2, 3]) {
      allowed.add(formatCopy(COPY["admin.variant_n"][language], { n }));
    }
    for (const label of Object.values(PACK_LABEL)) {
      allowed.add(formatCopy(COPY["admin.palette_of"][language], { pack: COPY[label][language] }));
    }
    for (const text of ["Lunar New Year Sale", "农历新年特卖", "Lunar deal"]) {
      allowed.add(text);
    }
    for (const amount of ["120.00", "96.00", "60.00", "100.00"]) {
      allowed.add(formatCopy(COPY["common.price_myr"][language], { amount }));
    }
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
    // SHOP-TASK-273 起装饰区为第五个区块、SHOP-TASK-275 起海报区为第六个区块、SHOP-TASK-276 起 AI 区为第七个区块，手机上同样收起。
    expect(panels).toEqual({ basic: true, rules: true, combine: false, texts: false, decoration: false, poster: false, ai: false });
    expect(tags(renderView(ready(openEditor(null, "csrf-1")), "en", panels), "details").map((tag) => attributes(tag).has("open"))).toEqual([
      true,
      true,
      false,
      false,
      false,
      false,
      false,
    ]);
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

describe("AdminPromotionEditPage decoration", () => {
  // 读回的装饰：农历新年素材包、开斋节配色，各装饰位都不是第一个变体。
  const READ_DECORATION = {
    pack: "cny",
    palette: "raya",
    ambient: "fall",
    home_poster: "centered",
    list_strip: "ticket",
    card_badge: "seal",
    detail_ribbon: "panel",
    cart_progress: "steps",
    poster_mode: "template",
  };

  // 装饰区（div.site-admin-promo-deco）里的单选与选择。
  function decorationControls(html: string): Map<string, string>[] {
    const area = element(html, "div", "site-admin-promo-deco");
    return [...tags(area, "input"), ...tags(area, "select")].map(attributes);
  }

  // name 属性为 name 的 select 里选中的选项值。
  function selected(html: string, name: string): string | undefined {
    const start = [...html.matchAll(/<select\b[^>]*>/g)].find((m) => attributes(m[0]).get("name") === name)?.index;
    if (start === undefined) {
      throw new Error(`no select named ${name}`);
    }
    const body = html.slice(start, html.indexOf("</select>", start));
    return tags(body, "option")
      .map(attributes)
      .find((attrs) => attrs.has("selected"))
      ?.get("value");
  }

  // 页面装饰里选中的单选值。
  function checkedAmbient(html: string): string | undefined {
    return tags(html, "input")
      .map(attributes)
      .find((attrs) => attrs.get("name") === "ambient" && attrs.has("checked"))
      ?.get("value");
  }

  // UX 0.20 A12 编辑区线框「+---- [admin.promo_texts] ----」…「[admin.copy_fallback]」之后为「+---- [admin.promo_decoration] ----」「[admin.promo_pack] <素材包> ▾」…：
  // 装饰区排在文案之后，按读取到的素材包、配色与各装饰位显示（配色为节日配色时旁边为色块）。
  it.each(LANGUAGES)("places the decoration section after the copy and shows the read values in %s", (language) => {
    const html = renderView(ready(openEditor(promotion({ decoration: READ_DECORATION }), "csrf-1")), language);
    const at = (text: string) => html.indexOf(text);
    expect(at(escapeHtml(COPY["admin.copy_fallback"][language]))).toBeLessThan(at(`<summary>${escapeHtml(COPY["admin.promo_decoration"][language])}</summary>`));
    expect(at(`<summary>${escapeHtml(COPY["admin.promo_decoration"][language])}</summary>`)).toBeLessThan(at('name="pack"'));
    expect(selected(html, "pack")).toBe("cny");
    expect(selected(html, "palette")).toBe("raya");
    for (const slot of ["home_poster", "list_strip", "card_badge", "detail_ribbon", "cart_progress"] as const) {
      expect(selected(html, slot), slot).toBe(READ_DECORATION[slot]);
    }
    expect(input(html, "ambient").get("value")).toBe("garland");
    expect(checkedAmbient(html)).toBe("fall");
    expect(html).toContain('data-promo-palette="raya" aria-hidden="true"');
    expect(html).toContain(`>${escapeHtml(COPY[PACK_LABEL.cny][language])}</option>`);
  });

  // UX 0.20 A12 说明「前台预览」「离开有未保存修改的编辑区时沿用 A04、A10 的离开确认」与落实写法（4）「装饰的修改计入未保存与提交」：素材包、配色与装饰位的改动都算
  // 未保存的修改（换回原样即不再算），并照当前的值提交；装饰不变时编辑区不变；改动收起操作栏下方的提示。
  it("counts decoration changes as unsaved and submits them", async () => {
    const editor = openEditor(promotion(), "csrf-1");
    expect(changeDecoration(editor, (decoration) => decoration)).toBe(editor);
    const packed = changeDecoration(editor, (decoration) => pickPack(decoration, "cny"));
    expect(hasUnsavedChanges(ready(packed))).toBe(true);
    expect(hasUnsavedChanges(ready(changeDecoration(packed, (decoration) => pickPack(decoration, "generic"))))).toBe(false);
    expect(hasUnsavedChanges(ready(changeDecoration(editor, pickFestivalPalette)))).toBe(true);
    const varied = changeDecoration(editor, (decoration) => pickVariant(decoration, "ambient", "none"));
    expect(hasUnsavedChanges(ready(varied))).toBe(true);
    expect(promotionInput(varied.form).decoration.ambient).toBe("none");
    const noticed: Editor = { ...editor, notice: { kind: "done", key: "admin.promo_saved" } };
    expect(changeDecoration(noticed, (decoration) => pickPack(decoration, "raya")).notice).toBeNull();

    const festival = changeDecoration(packed, pickFestivalPalette);
    const { api, signal } = await submitWith(festival, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(festival.form), version: 3 }, "csrf-1", signal);
    expect(promotionInput(festival.form).decoration).toEqual({ ...newPromotionForm().decoration, pack: "cny", palette: "cny" });
    const draft = changeDecoration(changeField(openEditor(null, "csrf-new"), "name", "New sale"), (decoration) => pickPack(decoration, "merdeka"));
    const created = await submitWith(draft, { kind: "failed" });
    expect(created.api.create).toHaveBeenCalledWith(promotionInput(draft.form), "csrf-new", created.signal);
    expect(promotionInput(draft.form).decoration.pack).toBe("merdeka");
  });

  // UX 0.20 A12 说明「换素材包时各装饰位改为新素材包的第一个变体」与落实写法（3）「配色为节日配色时改为新素材包的配色，为 theme 时不变」：换素材包后页面上各装饰位
  // 都选第一项（页面装饰为 garland），节日配色跟着新素材包，沿用主题配色不变。
  it.each(LANGUAGES)("resets the slots and follows the palette after a pack change in %s", (language) => {
    const editor = openEditor(promotion({ decoration: READ_DECORATION }), "csrf-1");
    const festival = changeDecoration(editor, (decoration) => pickPack(decoration, "christmas"));
    let html = renderView(ready(festival), language);
    expect(selected(html, "pack")).toBe("christmas");
    expect(selected(html, "palette")).toBe("christmas");
    for (const slot of ["home_poster", "list_strip", "card_badge", "detail_ribbon", "cart_progress"] as const) {
      expect(selected(html, slot), slot).toBe(newPromotionForm().decoration[slot]);
    }
    expect(checkedAmbient(html)).toBe("garland");
    expect(html).toContain('data-promo-palette="christmas" aria-hidden="true"');

    const keep = changeDecoration(changeDecoration(editor, (decoration) => ({ ...decoration, palette: "theme" })), (decoration) => pickPack(decoration, "deepavali"));
    expect(keep.form.decoration.palette).toBe("theme");
    html = renderView(ready(keep), language);
    expect(selected(html, "palette")).toBe("deepavali");
    expect(html).not.toContain("acs-promo-swatch");
  });

  // UX 0.20 A12「状态与可用操作」「进行中 | 内部名称、文案、装饰、海报、AI …」与「已结束 | 无（全部只读，显示 [admin.promo_ended_readonly]） | 复制」：已结束时装饰区
  // 全部禁用、改动不生效；进行中（及保存被答 rules_locked 之后）与其他状态装饰可改。
  it.each(LANGUAGES)("makes the decoration read-only only when ended in %s", async (language) => {
    const ended = openEditor(promotion({ state: "ended", decoration: READ_DECORATION }), "csrf-1");
    const endedControls = decorationControls(renderView(ready(ended), language));
    expect(endedControls).toHaveLength(13);
    expect(endedControls.every((attrs) => attrs.has("disabled"))).toBe(true);
    expect(changeDecoration(ended, (decoration) => pickPack(decoration, "raya"))).toBe(ended);

    for (const state of ["draft", "scheduled", "running"] as const) {
      const editor = openEditor(promotion({ state, decoration: READ_DECORATION }), "csrf-1");
      expect(decorationControls(renderView(ready(editor), language)).some((attrs) => attrs.has("disabled")), state).toBe(false);
      expect(changeDecoration(editor, (decoration) => pickPack(decoration, "raya")).form.decoration.pack, state).toBe("raya");
    }

    const { record } = await submitWith(openEditor(promotion({ state: "scheduled", decoration: READ_DECORATION }), "csrf-1"), {
      kind: "invalid",
      problems: [problem("rules_locked")],
    });
    const locked = editorOf(record.state);
    expect(locked.locked).toBe(true);
    expect(decorationControls(renderView(ready(locked), language)).some((attrs) => attrs.has("disabled"))).toBe(false);
    expect(changeDecoration(locked, (decoration) => pickVariant(decoration, "card_badge", "tag")).form.decoration.card_badge).toBe("tag");
  });
});

describe("AdminPromotionEditPage poster", () => {
  const DESKTOP = `/api/promotion-posters/${"a".repeat(32)}.webp`;
  const PHONE = `/api/promotion-posters/${"b".repeat(32)}.webp`;
  const NEW_DESKTOP = `/api/promotion-posters/${"c".repeat(32)}.webp`;
  const IMAGE_DECORATION = { ...newPromotionForm().decoration, poster_mode: "image" };

  // 上传图模式、已有桌面图（没有手机图）与英文替代文字的活动。
  function imagePromotion(overrides: Partial<AdminPromotion> = {}): AdminPromotion {
    const base = promotion();
    return promotion({
      decoration: IMAGE_DECORATION,
      poster_desktop_url: DESKTOP,
      copy: { ...base.copy, poster_alt_en: "Lunar New Year Sale banner" },
      ...overrides,
    });
  }

  // 海报区（div.site-admin-promo-poster）。
  function posterArea(html: string): string {
    return element(html, "div", "site-admin-promo-poster");
  }

  // 两个位置的 admin.upload 按钮。
  function uploadButtons(html: string): Map<string, string>[] {
    return tags(posterArea(html), "button")
      .map(attributes)
      .filter((attrs) => (attrs.get("name") ?? "").startsWith("poster_upload_"));
  }

  function modeRadios(html: string): Map<string, string>[] {
    return tags(html, "input")
      .map(attributes)
      .filter((attrs) => attrs.get("name") === "poster_mode");
  }

  const ALT_INPUTS = ["poster_alt_en", "poster_alt_zh", "poster_alt_ms"];

  // UX 0.20 A12 编辑区线框「+---- [admin.promo_decoration] ----」…之后为「+---- [admin.promo_poster] ----」「( ) [admin.poster_template]   ( ) [admin.poster_image]」
  // 「上传图时：[admin.poster_desktop] <缩略图>( [admin.upload] )  [admin.poster_desktop_rules]」「[admin.poster_phone] <缩略图>( [admin.upload] )  [admin.poster_phone_rules]」
  // 「[admin.image_rules]」「[admin.poster_alt]  [common.lang_en][____] [common.lang_zh][____] [common.lang_ms][____]（各 150）」：海报区排在装饰区之后；模板模式下只有
  // 两个单选；上传图模式下另有两行（已保存的海报图为缩略图，没有时为占位）、admin.image_rules 与读取到的三语替代文字。
  it.each(LANGUAGES)("places the poster section after the decoration and shows both modes in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const template = renderView(ready(openEditor(promotion(), "csrf-1")), language);
    const at = (text: string) => template.indexOf(text);
    expect(at('name="cart_progress"')).toBeLessThan(at(`<summary>${escapeHtml(t("admin.promo_poster"))}</summary>`));
    expect(at(`<summary>${escapeHtml(t("admin.promo_poster"))}</summary>`)).toBeLessThan(at('name="poster_mode"'));
    expect(textNodes(posterArea(template))).toEqual([t("admin.poster_template"), t("admin.poster_image")]);
    expect(modeRadios(template).map((attrs) => [attrs.get("value"), attrs.has("checked")])).toEqual([
      ["template", true],
      ["image", false],
    ]);
    expect(template).not.toContain('name="poster_alt_en"');

    const html = renderView(ready(openEditor(imagePromotion(), "csrf-1")), language);
    expect(modeRadios(html).map((attrs) => attrs.has("checked"))).toEqual([false, true]);
    expect(textNodes(posterArea(html))).toEqual([
      t("admin.poster_template"),
      t("admin.poster_image"),
      t("admin.poster_desktop"),
      t("admin.upload"),
      t("admin.poster_desktop_rules"),
      t("admin.poster_phone"),
      t("admin.upload"),
      t("admin.poster_phone_rules"),
      t("admin.image_rules"),
      t("admin.poster_alt"),
      t("common.lang_en"),
      "26/150",
      t("common.lang_zh"),
      "0/150",
      t("common.lang_ms"),
      "0/150",
    ]);
    expect(tags(posterArea(html), "img").map(attributes).map((attrs) => attrs.get("src"))).toEqual([DESKTOP]);
    expect(input(html, "poster_alt_en").get("value")).toBe("Lunar New Year Sale banner");
    expect(ALT_INPUTS.map((name) => input(html, name).get("lang"))).toEqual(["en", "zh-Hans", "ms"]);
    expect(uploadButtons(html).map((attrs) => attrs.has("disabled"))).toEqual([false, false]);
  });

  // 落实写法（1）「两个单选改表单装饰的 poster_mode（计入未保存与提交）」与（2）「admin.poster_alt 三语输入（…计入未保存与提交）」，UX 0.20 A12 说明「海报」中
  // 「替代文字随本页的保存提交」：模式与替代文字的改动都算未保存的修改（换回原样即不再算），并照当前的值提交（保存与新建）；不变时编辑区不变。
  it("counts the poster mode and alt text as unsaved and submits them", async () => {
    const editor = openEditor(promotion(), "csrf-1");
    expect(changeDecoration(editor, (decoration) => pickPosterMode(decoration, "template"))).toBe(editor);
    const image = changeDecoration(editor, (decoration) => pickPosterMode(decoration, "image"));
    expect(hasUnsavedChanges(ready(image))).toBe(true);
    expect(hasUnsavedChanges(ready(changeDecoration(image, (decoration) => pickPosterMode(decoration, "template"))))).toBe(false);
    const alt = changeCopy(image, "poster_alt_ms", "Sepanduk jualan");
    expect(hasUnsavedChanges(ready(changeCopy(editor, "poster_alt_ms", "Sepanduk jualan")))).toBe(true);
    expect(promotionInput(alt.form).decoration.poster_mode).toBe("image");
    expect(promotionInput(alt.form).copy.poster_alt_ms).toBe("Sepanduk jualan");
    const { api, signal } = await submitWith(alt, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(alt.form), version: 3 }, "csrf-1", signal);

    const draft = changeCopy(
      changeDecoration(changeField(openEditor(null, "csrf-new"), "name", "New sale"), (decoration) => pickPosterMode(decoration, "image")),
      "poster_alt_en",
      "Sale banner",
    );
    const created = await submitWith(draft, { kind: "failed" });
    expect(created.api.create).toHaveBeenCalledWith(promotionInput(draft.form), "csrf-new", created.signal);
    expect(promotionInput(draft.form).copy.poster_alt_en).toBe("Sale banner");
  });

  // UX 0.20 A12 线框「[admin.poster_alt] …（各 150）」与说明「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查（…文案长度），不合格的字段旁显示
  // [admin.promo_field_invalid] 并不提交」：替代文字超过 150 个字符时已用字数照常显示，点保存不提交，只在该输入旁提示。
  it.each(LANGUAGES)("keeps an over-long alt text from being submitted in %s", (language) => {
    const submit = vi.fn();
    const editor = pressSave(changeCopy(openEditor(imagePromotion(), "csrf-1"), "poster_alt_zh", "字".repeat(151)), submit);
    expect(submit).not.toHaveBeenCalled();
    const html = renderView(ready(editor), language);
    expect(fieldInvalid(html, "poster_alt_zh", language)).toBe(true);
    expect(fieldInvalid(html, "poster_alt_en", language)).toBe(false);
    expect(textNodes(posterArea(html))).toContain("151/150");
  });

  // UX 0.20 A12 说明「海报」「上传成功即替换并立即生效…，不改变活动的版本号，所以不会让之后的保存撞上 [admin.promo_changed]」与落实写法（3）「成功即替换缩略图，
  // 不改版本、不算未保存的修改」：上传成功只换该位置的缩略图，表单、基准、版本号与提示都不变，未保存状态不变；之后的保存仍带读取到的版本；编辑区换成别的活动时不换。
  it.each(LANGUAGES)("replaces the thumbnail without unsaved changes or a new version in %s", async (language) => {
    const editor = openEditor(imagePromotion(), "csrf-1");
    const uploaded = replacePoster(editor, 7, "phone", PHONE);
    expect(uploaded.form).toBe(editor.form);
    expect(uploaded.base).toBe(editor.base);
    expect(uploaded.promotion?.version).toBe(3);
    expect(uploaded.promotion?.poster_phone_url).toBe(PHONE);
    expect(hasUnsavedChanges(ready(uploaded))).toBe(false);
    expect(tags(posterArea(renderView(ready(uploaded), language)), "img").map((tag) => attributes(tag).get("src"))).toEqual([DESKTOP, PHONE]);
    const replaced = replacePoster(uploaded, 7, "desktop", NEW_DESKTOP);
    expect(tags(posterArea(renderView(ready(replaced), language)), "img").map((tag) => attributes(tag).get("src"))).toEqual([NEW_DESKTOP, PHONE]);
    expect(replacePoster(uploaded, 7, "phone", PHONE)).toBe(uploaded);
    expect(replacePoster(uploaded, 8, "phone", NEW_DESKTOP)).toBe(uploaded);
    expect(replacePoster(openEditor(null, "csrf-1"), 7, "phone", PHONE).promotion).toBeNull();

    const edited = changeCopy(uploaded, "poster_alt_en", "Changed alt");
    const noticed = replacePoster({ ...edited, notice: { kind: "done", key: "admin.promo_saved" } }, 7, "desktop", NEW_DESKTOP);
    expect(noticed.notice).toEqual({ kind: "done", key: "admin.promo_saved" });
    expect(hasUnsavedChanges(ready(noticed))).toBe(true);
    const { api, signal } = await submitWith(replacePoster(edited, 7, "desktop", NEW_DESKTOP), { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(edited.form), version: 3 }, "csrf-1", signal);
  });

  // UX 0.20 A12 说明「海报」「新建而尚未保存的活动没有 ID，上传按钮禁用并显示 [admin.poster_save_first]；已结束的活动上传按钮禁用」与「状态与可用操作」「进行中 | 内部名称、
  // 文案、装饰、海报、AI …」「已结束 | 无（全部只读…）」：新建时两个按钮禁用并指向 admin.poster_save_first（单选与替代文字可改）；已结束时按钮与单选禁用、替代文字只读、
  // 改动不生效；进行中（及保存被答 rules_locked 之后）与其他状态都可用。
  it.each(LANGUAGES)("disables uploads before the first save and when ended in %s", async (language) => {
    const fresh = changeDecoration(openEditor(null, "csrf-1"), (decoration) => pickPosterMode(decoration, "image"));
    const unsaved = renderView(ready(fresh), language);
    const buttons = uploadButtons(unsaved);
    expect(buttons.map((attrs) => attrs.has("disabled"))).toEqual([true, true]);
    const describedBy = buttons[0]?.get("aria-describedby") ?? "";
    expect(describedBy).not.toBe("");
    expect(buttons[1]?.get("aria-describedby")).toBe(describedBy);
    expect(unsaved).toContain(`<span id="${escapeHtml(describedBy)}" class="acs-admin__muted">${escapeHtml(COPY["admin.poster_save_first"][language])}</span>`);
    expect(countOf(unsaved, escapeHtml(COPY["admin.poster_save_first"][language]))).toBe(1);
    expect(modeRadios(unsaved).some((attrs) => attrs.has("disabled"))).toBe(false);
    expect(ALT_INPUTS.some((name) => input(unsaved, name).has("readonly"))).toBe(false);
    expect(tags(posterArea(unsaved), "img")).toEqual([]);

    const ended = openEditor(imagePromotion({ state: "ended" }), "csrf-1");
    const endedHtml = renderView(ready(ended), language);
    expect(uploadButtons(endedHtml).map((attrs) => attrs.has("disabled"))).toEqual([true, true]);
    expect(modeRadios(endedHtml).every((attrs) => attrs.has("disabled"))).toBe(true);
    expect(ALT_INPUTS.every((name) => input(endedHtml, name).has("readonly"))).toBe(true);
    expect(endedHtml).not.toContain(escapeHtml(COPY["admin.poster_save_first"][language]));
    expect(changeDecoration(ended, (decoration) => pickPosterMode(decoration, "template"))).toBe(ended);
    expect(changeCopy(ended, "poster_alt_en", "x")).toBe(ended);

    for (const state of ["draft", "scheduled", "running"] as const) {
      const html = renderView(ready(openEditor(imagePromotion({ state }), "csrf-1")), language);
      expect(uploadButtons(html).map((attrs) => attrs.has("disabled")), state).toEqual([false, false]);
      expect(modeRadios(html).some((attrs) => attrs.has("disabled")), state).toBe(false);
      expect(html).not.toContain(escapeHtml(COPY["admin.poster_save_first"][language]));
    }
    const { record } = await submitWith(openEditor(imagePromotion({ state: "scheduled" }), "csrf-1"), { kind: "invalid", problems: [problem("rules_locked")] });
    expect(uploadButtons(renderView(record.state, language)).some((attrs) => attrs.has("disabled"))).toBe(false);
  });

  // 落实写法（3）「csrf 显示 common.error_retry 并重新读取 CSRF 令牌；401 回到 A01」（UX 0.20 A12 说明「海报」「上传结果提示沿用 A08 标志图的写法」）：重新读取取到时
  // 只换该活动编辑区的令牌（表单、海报图与版本号不变）；401 为 login、网络中断 network、不存在与其他失败 failed，都不改编辑区；编辑区已换成别的活动时不换。
  it("reads a new CSRF token for the poster after csrf", async () => {
    const editor = changeCopy(openEditor(imagePromotion(), "csrf-1"), "poster_alt_en", "Edited");
    const fresh = promotion({ version: 9, poster_phone_url: PHONE });
    const signal = new AbortController().signal;
    const record = recorder(ready(editor));
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: fresh, csrf_token: "csrf-2" }));
    await expect(rereadPosterToken(7, signal, record.moves.update, fakeApi({ readOne }))).resolves.toBe("ok");
    expect(readOne).toHaveBeenCalledWith(7, signal);
    expect(editorOf(record.state)).toEqual({ ...editor, csrfToken: "csrf-2" });

    for (const [read, outcome] of [
      [{ kind: "none" }, "login"],
      [{ kind: "network" }, "network"],
      [{ kind: "missing" }, "failed"],
      [{ kind: "failed" }, "failed"],
    ] as const) {
      const other = recorder(ready(editor));
      await expect(rereadPosterToken(7, signal, other.moves.update, fakeApi({ readOne: () => Promise.resolve<PromotionRead>(read) }))).resolves.toBe(outcome);
      expect(other.state).toEqual(ready(editor));
    }

    const elsewhere = recorder(ready(editor));
    await rereadPosterToken(8, signal, elsewhere.moves.update, fakeApi({ readOne }));
    expect(elsewhere.state).toEqual(ready(editor));
  });
});

describe("AdminPromotionEditPage editor preview", () => {
  const DESKTOP = `/api/promotion-posters/${"d".repeat(32)}.webp`;

  // 装饰区末尾的编辑区预览（div.site-admin-promo-deco__preview）。
  function previewArea(html: string): string {
    return element(html, "div", "site-admin-promo-deco__preview");
  }

  // 预览里 .acs-promo 的开始标签。
  function promoTag(html: string): string {
    return tags(previewArea(html), "div").find((tag) => attributes(tag).get("class") === "acs-promo") ?? "";
  }

  // UX 0.20 A12 线框「[admin.slot_cart_progress] <变体> ▾」「[admin.editor_preview]（编辑区预览，随本页未保存的输入即时更新）」之后为「+---- [admin.promo_poster] ----」：
  // 预览在装饰区里、五个装饰位之后、海报区之前（新建与已有活动都有），小标题为 admin.editor_preview，其下为切换按钮、缩样与 admin.editor_preview_note；
  // 首次渲染不发请求（店铺装修在挂载后才读，首次为默认主题）。
  it.each(LANGUAGES)("places the editor preview at the end of the decoration section in %s", (language) => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const editor of [openEditor(null, "csrf-1"), openEditor(promotion(), "csrf-1")]) {
      const html = renderView(ready(editor), language);
      const at = (text: string) => html.indexOf(text);
      expect(at('name="cart_progress"')).toBeLessThan(at('class="site-admin-promo-deco__preview"'));
      expect(at('class="site-admin-promo-deco__preview"')).toBeLessThan(at(`<summary>${escapeHtml(COPY["admin.promo_poster"][language])}</summary>`));
      const area = previewArea(html);
      expect(element(html, "div", "site-admin-promo-deco")).toContain(area);
      const texts = textNodes(area);
      expect(texts.slice(0, 3)).toEqual([COPY["admin.editor_preview"][language], COPY["admin.preview_light"][language], COPY["admin.preview_dark"][language]]);
      expect(texts[texts.length - 1]).toBe(COPY["admin.editor_preview_note"][language]);
      expect(attributes(tags(area, "div").find((tag) => classes(tag).includes("site-admin-promo-preview__shop")) ?? "").get("data-shop-theme")).toBe("pandan");
    }
    expect(request).not.toHaveBeenCalled();
  });

  // UX 0.20 A12 说明「编辑区预览」「按本页当前（含未保存）的文案、素材包、配色、各装饰位变体与海报模式…画出…的缩样」：改了（尚未保存的）标题、角标短语、素材包、配色、
  // 变体与规则后，预览随之改变，读到的旧值不再显示。
  it.each(LANGUAGES)("follows unsaved copy, decoration and rules in %s", (language) => {
    let editor = openEditor(promotion(), "csrf-1");
    const before = previewArea(renderView(ready(editor), language));
    expect(before).toContain(language === "zh" ? "农历新年特卖" : "Lunar New Year Sale");
    expect(promoTag(before)).toBe('<div class="acs-promo" data-promo-pack="generic">');

    editor = changeCopy(editor, `title_${language}`, "Unsaved title");
    editor = changeCopy(editor, "badge_en", "Hot");
    editor = changeDecoration(editor, (decoration) => pickFestivalPalette(pickPack(decoration, "deepavali")));
    editor = changeDecoration(editor, (decoration) => pickVariant(pickVariant(decoration, "ambient", "fall"), "cart_progress", "steps"));
    editor = changeRules(editor, (form) => addRule(form, "item_discount"));
    editor = changeRules(editor, (form) => mapRule(form.rules, 0, (rule) => (rule.kind === "item_discount" ? { ...rule, discount: "15" } : rule)));
    expect(hasUnsavedChanges(ready(editor))).toBe(true);
    const after = previewArea(renderView(ready(editor), language));
    expect(after).toContain(">Unsaved title</span>");
    expect(after).not.toContain("Lunar New Year Sale");
    expect(after).toContain(">Hot</span>");
    expect(after).toContain(`>${escapeHtml(formatCopy(COPY["promo.badge_percent"][language], { percent: 15 }))}</span>`);
    expect(promoTag(after)).toBe('<div class="acs-promo" data-promo-pack="deepavali" data-promo-palette="deepavali" data-promo-ambient="fall">');
    expect(after).toContain("acs-promo-progress__steps");
  });

  // UX 0.20 A12 说明「海报」「编辑区预览在上传图模式下显示已上传的图，没有图的位置显示占位」与「编辑区预览」「…与海报模式」：上传图模式下为已保存的桌面海报图（上传成功换图后
  // 随之更换），没有桌面图时为占位块；切回（未保存的）模板模式时为模板海报。
  it.each(LANGUAGES)("shows the saved desktop poster in image mode in %s", (language) => {
    const image = { ...newPromotionForm().decoration, poster_mode: "image" };
    const saved = openEditor(promotion({ decoration: image, poster_desktop_url: DESKTOP }), "csrf-1");
    expect(tags(previewArea(renderView(ready(saved), language)), "img").map((tag) => attributes(tag).get("src"))).toEqual([DESKTOP]);
    const replaced = replacePoster(saved, 7, "desktop", `/api/promotion-posters/${"e".repeat(32)}.webp`);
    expect(tags(previewArea(renderView(ready(replaced), language)), "img").map((tag) => attributes(tag).get("src"))).toEqual([`/api/promotion-posters/${"e".repeat(32)}.webp`]);

    for (const editor of [
      openEditor(promotion({ decoration: image }), "csrf-1"),
      changeDecoration(openEditor(null, "csrf-1"), (decoration) => pickPosterMode(decoration, "image")),
    ]) {
      const area = previewArea(renderView(ready(editor), language));
      expect(tags(area, "img")).toEqual([]);
      expect(area).toContain('<div class="acs-promo-poster-img site-admin-promo-preview__poster" aria-hidden="true"></div>');
    }

    const template = changeDecoration(saved, (decoration) => pickPosterMode(decoration, "template"));
    const area = previewArea(renderView(ready(template), language));
    expect(tags(area, "img")).toEqual([]);
    expect(area).toContain("acs-promo-poster--split");
  });

  // UX 0.20 A12 说明「编辑区预览」「它在任何状态下都可用（已结束的只读显示）」：草稿、未开始、进行中与已结束都显示预览，已结束时装饰区禁用而切换按钮可用，缩样为已保存的样子。
  it.each(LANGUAGES)("shows the preview in every state in %s", (language) => {
    for (const state of ["draft", "scheduled", "running", "ended"] as const) {
      const area = previewArea(renderView(ready(openEditor(promotion({ state }), "csrf-1")), language));
      expect(tags(area, "button").map((tag) => attributes(tag).get("aria-pressed")), state).toEqual(["true", "false"]);
      expect(tags(area, "button").some((tag) => attributes(tag).has("disabled")), state).toBe(false);
      expect(promoTag(area), state).toBe('<div class="acs-promo" data-promo-pack="generic">');
      expect(area, state).toContain('<div class="acs-promo-garland" aria-hidden="true"></div>');
    }
  });
});

describe("AdminPromotionEditPage AI", () => {
  // 提案：农历新年素材包与配色、各装饰位换了变体；文案有 null。
  const PROPOSAL: AiProposal = {
    pack: "cny",
    palette: "cny",
    ambient: "fall",
    home_poster: "centered",
    list_strip: "ticket",
    card_badge: "seal",
    detail_ribbon: "panel",
    cart_progress: "steps",
    title_en: "Lunar Sale",
    title_zh: "新年特卖",
    title_ms: null,
    subtitle_en: null,
    subtitle_zh: "红金好礼",
    subtitle_ms: null,
    badge_en: "Hot",
    badge_zh: null,
    badge_ms: null,
    strip_en: "Up to 20% off",
    strip_zh: null,
    strip_ms: null,
  };
  // 读回的免运费规则（门槛 RM120）。
  const READ_RULE: AdminPromotionRule = {
    rule_id: 41,
    kind: "free_shipping",
    discount_mode: null,
    percent: null,
    amount_sen: null,
    buy_quantity: null,
    get_quantity: null,
    get_percent: null,
    threshold_sen: 12000,
    product_ids: [],
    category_ids: [],
    tiers: [],
    products: [],
  };
  const LISTS: ScopeLists = { status: "ok", categories: [], products: [] };

  // AI 区（div.site-admin-promo-ai）。
  function aiArea(html: string): string {
    return element(html, "div", "site-admin-promo-ai");
  }

  function generateButton(html: string): Map<string, string> | undefined {
    const tag = tags(aiArea(html), "button").find((item) => attributes(item).get("name") === "ai_generate");
    return tag === undefined ? undefined : attributes(tag);
  }

  // UX 0.20 A12 编辑区线框「+---- [admin.promo_poster] ----」…之后为「+---- [admin.ai_title] ----」「[admin.ai_describe] [<占位 admin.ai_placeholder>______]  <已用>/500」
  // 「[admin.ai_privacy]」「( [admin.ai_generate] )」：AI 区排在海报区之后（区块标题 admin.ai_title），新建与已有活动都有；首次渲染不发请求。
  it.each(LANGUAGES)("places the AI section after the poster section in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const editor of [openEditor(null, "csrf-1"), openEditor(promotion(), "csrf-1")]) {
      const html = renderView(ready(editor), language);
      const at = (text: string) => html.indexOf(text);
      expect(at('name="poster_mode"')).toBeLessThan(at(`<summary>${escapeHtml(t("admin.ai_title"))}</summary>`));
      expect(at(`<summary>${escapeHtml(t("admin.ai_title"))}</summary>`)).toBeLessThan(at('name="ai_description"'));
      expect(textNodes(aiArea(html))).toEqual([t("admin.ai_describe"), "0/500", t("admin.ai_privacy"), t("admin.ai_generate")]);
      expect(input(html, "ai_description").get("placeholder")).toBe(t("admin.ai_placeholder"));
      expect(generateButton(html)?.get("type")).toBe("button");
      expect(generateButton(html)?.has("disabled")).toBe(false);
    }
    expect(request).not.toHaveBeenCalled();
  });

  // DESIGN 1.23 第 13 条「描述不保存」与落实写法（2）「描述只在页面内存里，不进表单、不随保存提交」：表单与提交内容里没有描述，保存与新建的请求照旧只带表单的值。
  it("keeps the description out of the form and the save", async () => {
    const editor = changeCopy(openEditor(promotion(), "csrf-1"), "title_en", "Changed");
    expect(Object.keys(editor.form)).not.toContain("description");
    expect(Object.keys(promotionInput(editor.form))).not.toContain("description");
    expect(JSON.stringify(promotionInput(editor.form))).not.toContain("ai_");
    const { api, signal } = await submitWith(editor, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...promotionInput(editor.form), version: 3 }, "csrf-1", signal);
  });

  // 落实写法（3）「点 admin.ai_apply 才把提案填回表单：装饰的 pack、palette 与六个装饰位，文案的标题、副标题、角标短语与横条文字共 12 个字段（null 填为空串）；poster_mode、
  // 替代文字、规则、时间与开关不变；…不保存（计入未保存的修改）」与 UX 0.20 A12 说明「AI」「AI 结果只在点 [admin.ai_apply] 后填回本页的文案、装饰与配色，不保存，也不改规则、
  // 时间与开关」：填回后表单与页面换成提案的值、算未保存的修改、基准与版本不变，之后照当前的值经普通保存提交（null 的文案提交 null）。
  it.each(LANGUAGES)("fills the proposal into the form as unsaved changes in %s", async (language) => {
    const base = promotion({
      rules: [READ_RULE],
      decoration: { ...newPromotionForm().decoration, poster_mode: "image" },
      copy: { ...promotion().copy, title_ms: "Jualan lama", poster_alt_en: "Banner" },
    });
    const editor: Editor = { ...openEditor(base, "csrf-1"), notice: { kind: "done", key: "admin.promo_saved" } };
    const applied = applyAiProposal(editor, PROPOSAL);
    expect(applied.notice).toBeNull();
    expect(applied.base).toBe(editor.base);
    expect(applied.promotion).toBe(editor.promotion);
    expect(hasUnsavedChanges(ready(applied))).toBe(true);
    expect(applied.form.decoration).toEqual({
      pack: "cny",
      palette: "cny",
      ambient: "fall",
      home_poster: "centered",
      list_strip: "ticket",
      card_badge: "seal",
      detail_ribbon: "panel",
      cart_progress: "steps",
      poster_mode: "image",
    });
    expect(applied.form.copy).toEqual({
      ...editor.form.copy,
      title_en: "Lunar Sale",
      title_zh: "新年特卖",
      title_ms: "",
      subtitle_en: "",
      subtitle_zh: "红金好礼",
      subtitle_ms: "",
      badge_en: "Hot",
      badge_zh: "",
      badge_ms: "",
      strip_en: "Up to 20% off",
      strip_zh: "",
      strip_ms: "",
    });
    expect(applied.form.copy.poster_alt_en).toBe("Banner");
    for (const field of ["name", "starts_at", "ends_at", "coupon_combinable", "points_combinable", "rules"] as const) {
      expect(applied.form[field], field).toEqual(editor.form[field]);
    }

    const html = renderView(ready(applied), language);
    expect(input(html, "title_en").get("value")).toBe("Lunar Sale");
    expect(input(html, "title_ms").get("value")).toBe("");
    expect(input(html, "subtitle_zh").get("value")).toBe("红金好礼");
    expect(input(html, "poster_alt_en").get("value")).toBe("Banner");
    expect(input(html, "rules.1.threshold_sen").get("value")).toBe("120.00");

    const input0 = promotionInput(applied.form);
    expect(input0.copy.title_ms).toBeNull();
    expect(input0.rules).toEqual(promotionInput(editor.form).rules);
    const { api, signal } = await submitWith(applied, { kind: "failed" });
    expect(api.save).toHaveBeenCalledWith(7, { ...input0, version: 3 }, "csrf-1", signal);

    // 新建尚未保存时同样只填回表单，经新建提交。
    const draft = applyAiProposal(changeField(openEditor(null, "csrf-new"), "name", "New sale"), PROPOSAL);
    expect(hasUnsavedChanges(ready(draft))).toBe(true);
    const created = await submitWith(draft, { kind: "failed" });
    expect(created.api.create).toHaveBeenCalledWith(promotionInput(draft.form), "csrf-new", created.signal);
  });

  // DESIGN 1.23 第 13 条「送出的内容只有：这段描述、活动的规则类别与参数（只作为写文案的参考，模型的回答不能改动它们）」与
  // 落实写法（3）「有不合格时不发请求，规则区照保存时的写法在字段旁显示 admin.promo_field_invalid」（规则参数之外的不查）：点 admin.ai_generate 而规则参数不合格后，
  // 只在规则参数旁提示（名称、范围不提示），随当前的值消失；规则参数合格或开始保存时收起。
  it.each(LANGUAGES)("marks invalid rule parameters beside the rules after generate in %s", (language) => {
    const renderWithLists = (editor: Editor) =>
      renderToStaticMarkup(wrap(<AdminPromotionEditView state={ready(editor)} panels={ALL_OPEN} handlers={NO_HANDLERS} lists={LISTS} />, language, EDIT_PATH));
    const editor = changeRules(openEditor(null, "csrf-1"), (form) => addRule(form, "item_discount"));
    expect(renderWithLists(editor)).not.toContain(escapeHtml(COPY["admin.promo_field_invalid"][language]));
    const checked = checkAiRules(editor, true);
    expect(checked.aiChecked).toBe(true);
    expect(checkAiRules(checked, true)).toBe(checked);
    expect(shownProblems(checked).map((problem) => problem.field)).toEqual(["rules.1.percent"]);
    const html = renderWithLists(checked);
    expect(fieldInvalid(html, "rules.1.percent", language)).toBe(true);
    expect(fieldInvalid(html, "name", language)).toBe(false);
    expect(countOf(html, escapeHtml(COPY["admin.promo_field_invalid"][language]))).toBe(1);

    const fixed = changeRules(checked, (form) => mapRule(form.rules, 0, (rule) => (rule.kind === "item_discount" ? { ...rule, discount: "20" } : rule)));
    expect(renderWithLists(fixed)).not.toContain(escapeHtml(COPY["admin.promo_field_invalid"][language]));
    expect(checkAiRules(checked, false).aiChecked).toBe(false);
    expect(startSaving(checked).aiChecked).toBe(false);
    // 点保存后照保存的写法全部检查。
    expect(shownProblems(pressSave(checked, vi.fn())).map((problem) => problem.field)).toContain("name");
  });

  // UX 0.20 A12「状态与可用操作」「进行中 | 内部名称、文案、装饰、海报、AI …」「已结束 | 无（全部只读…）」与说明「AI」「进行中的活动也可用 AI 改装饰与文案；已结束的不可用」，
  // 落实写法（5）「已结束的活动不显示生成按钮（区块只读）」：已结束时没有 admin.ai_generate 与 admin.ai_privacy、描述只读、填回不生效；草稿、未开始、进行中与 rules_locked 之后都可用。
  it.each(LANGUAGES)("makes the AI section read-only only when ended in %s", async (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const ended = openEditor(promotion({ state: "ended" }), "csrf-1");
    const endedHtml = renderView(ready(ended), language);
    expect(generateButton(endedHtml)).toBeUndefined();
    expect(input(endedHtml, "ai_description").has("readonly")).toBe(true);
    expect(textNodes(aiArea(endedHtml))).toEqual([t("admin.ai_describe"), "0/500"]);
    expect(applyAiProposal(ended, PROPOSAL)).toBe(ended);

    for (const state of ["draft", "scheduled", "running"] as const) {
      const editor = openEditor(promotion({ state }), "csrf-1");
      const html = renderView(ready(editor), language);
      expect(generateButton(html)?.has("disabled"), state).toBe(false);
      expect(input(html, "ai_description").has("readonly"), state).toBe(false);
      expect(applyAiProposal(editor, PROPOSAL).form.decoration.pack, state).toBe("cny");
    }
    const { record } = await submitWith(openEditor(promotion({ state: "scheduled", rules: [READ_RULE] }), "csrf-1"), {
      kind: "invalid",
      problems: [problem("rules_locked")],
    });
    const locked = editorOf(record.state);
    expect(locked.locked).toBe(true);
    expect(generateButton(renderView(ready(locked), language))?.has("disabled")).toBe(false);
    const filled = applyAiProposal(locked, PROPOSAL);
    expect(filled.form.copy.title_en).toBe("Lunar Sale");
    expect(filled.form.rules).toBe(locked.form.rules);
  });

  // DESIGN 1.23 第 13 条「管理员在活动编辑页输入一段描述…请求 AI 给出一份装饰与文案的提案」（重取令牌后在同一编辑页再请求）与
  // 落实写法（4）「csrf 显示 common.error_retry 并重新读取 CSRF 令牌」：已有活动同海报区以 readAdminPromotion 只换令牌；新建尚未保存时以 readAdminPromotions 只换令牌
  // （表单不变）；401 为 login、网络中断 network、其他失败 failed，都不改编辑区；编辑区已换成别的活动时不换。
  it("reads a new CSRF token for AI after csrf", async () => {
    const signal = new AbortController().signal;
    const fresh = changeCopy(openEditor(null, "csrf-1"), "title_en", "Draft title");
    const record = recorder(ready(fresh));
    const readList = vi.fn(() => Promise.resolve<PromotionsRead>({ kind: "ok", promotions: [], csrf_token: "csrf-2" }));
    await expect(rereadAiToken(null, signal, record.moves.update, fakeApi({ readList }))).resolves.toBe("ok");
    expect(readList).toHaveBeenCalledWith(signal);
    expect(editorOf(record.state)).toEqual({ ...fresh, csrfToken: "csrf-2" });

    for (const [read, outcome] of [
      [{ kind: "none" }, "login"],
      [{ kind: "network" }, "network"],
      [{ kind: "failed" }, "failed"],
    ] as const) {
      const other = recorder(ready(fresh));
      await expect(rereadAiToken(null, signal, other.moves.update, fakeApi({ readList: () => Promise.resolve<PromotionsRead>(read) }))).resolves.toBe(outcome);
      expect(other.state).toEqual(ready(fresh));
    }
    const existing = openEditor(promotion(), "csrf-1");
    const elsewhere = recorder(ready(existing));
    await rereadAiToken(null, signal, elsewhere.moves.update, fakeApi({ readList }));
    expect(elsewhere.state).toEqual(ready(existing));

    const edited = changeCopy(existing, "badge_en", "Edited");
    const saved = recorder(ready(edited));
    const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: promotion({ version: 9 }), csrf_token: "csrf-3" }));
    await expect(rereadAiToken(7, signal, saved.moves.update, fakeApi({ readOne }))).resolves.toBe("ok");
    expect(readOne).toHaveBeenCalledWith(7, signal);
    expect(editorOf(saved.state)).toEqual({ ...edited, csrfToken: "csrf-3" });
  });
});

describe("AdminPromotionEditPage state actions", () => {
  // 渲染时的马来西亚当前时刻：早于替身活动的结束时间（2027-02-12T23:59）。
  const NOW = "2027-01-01T00:00";
  const STATES = ["draft", "scheduled", "running", "ended"] as const;
  // 每个状态操作用来测试的状态（该状态提供这个操作）与它的请求替身。
  const STATE_OF: Record<StateAction, (typeof STATES)[number]> = { publish: "draft", withdraw: "scheduled", end: "running", copy: "ended", delete: "draft" };
  const API_OF: Record<StateAction, keyof PromotionActionApi> = { publish: "publish", withdraw: "withdraw", end: "end", copy: "copy", delete: "remove" };
  const STATE_ACTION_LIST = ["publish", "withdraw", "end", "copy", "delete"] as const;

  type AnyElement = ReactElement<Record<string, unknown>>;

  // 元素树：展开子组件（直接调用，仍在语言的上下文里），用来取按钮的处理函数与属性（不模拟点击）。照 A08 标志段测试的写法。
  function expand(node: ReactNode): AnyElement[] {
    if (Array.isArray(node)) {
      return node.flatMap((child: ReactNode) => expand(child));
    }
    if (!isValidElement(node)) {
      return [];
    }
    const found = node as AnyElement;
    if (typeof found.type === "function") {
      const component = found.type as (props: Record<string, unknown>) => ReactNode;
      return [found, ...expand(component(found.props))];
    }
    return [found, ...expand(found.props.children as ReactNode)];
  }

  function confirmTree(props: Parameters<typeof PromotionConfirm>[0]): AnyElement[] {
    let found: AnyElement[] = [];
    const probe = () => {
      found = expand(createElement(PromotionConfirm, props));
      return null;
    };
    renderToStaticMarkup(wrap(createElement(probe), "en", EDIT_PATH));
    return found;
  }

  function renderAt(state: EditState, language: Language, now = NOW): string {
    return renderToStaticMarkup(wrap(<AdminPromotionEditView state={state} panels={ALL_OPEN} handlers={NO_HANDLERS} now={now} />, language, EDIT_PATH));
  }

  function actionBar(html: string): string {
    return element(html, "div", "site-admin-promo-edit__actions");
  }

  // 某个文字的按钮的开始标签。
  function buttonTag(html: string, text: string): Map<string, string> {
    const found = [...html.matchAll(/(<button\b[^>]*>)([^<]*)<\/button>/g)].find((m) => unescapeHtml(m[2] ?? "") === text);
    if (found === undefined) {
      throw new Error(`no button ${text}`);
    }
    return attributes(found[1] ?? "");
  }

  function editorIn(state: (typeof STATES)[number], overrides: Partial<AdminPromotion> = {}): Editor {
    return openEditor(promotion({ state, ...overrides }), "csrf-1");
  }

  function fakeActionApi(overrides: Partial<PromotionActionApi>): PromotionActionApi {
    const unexpected = () => Promise.reject(new Error("unexpected request"));
    return { readOne: unexpected, publish: unexpected, withdraw: unexpected, end: unexpected, copy: unexpected, remove: unexpected, ...overrides };
  }

  // 以 editor 发出一次状态操作（结果以替身给出），返回记录、替身与 signal。
  async function runWith(editor: Editor, action: StateAction, result: PromotionWrite | PromotionDelete, extra: Partial<PromotionActionApi> = {}) {
    const record = recorder(ready(startAction(editor, action)));
    const navigated: RoutePath[] = [];
    const moves: ActionMoves = {
      ...record.moves,
      navigate: (path) => {
        navigated.push(path);
      },
    };
    const reply = vi.fn(() => Promise.resolve(result));
    const api = fakeActionApi({ [API_OF[action]]: reply, ...extra });
    const signal = new AbortController().signal;
    await runAction(editor, action, signal, moves, api);
    return { record, navigated, reply, api, signal };
  }

  function effects() {
    return { run: vi.fn<(editor: Editor, action: StateAction) => void>(), open: vi.fn<(path: RoutePath) => void>() };
  }

  // UX 0.20 A12「状态与可用操作」表「草稿 | 全部 | 保存、发布、复制、删除、预览」「已发布未开始 | … | 保存、撤回为草稿、复制、预览」
  // 「进行中 | … | 保存、提前结束、复制」「已结束 | … | 复制」与线框「（按下表的状态显示，不可用的不显示）」：操作栏按已保存的状态只显示这些操作
  // （admin.promo_more 之后依次排列）；已结束没有 common.save，admin.promo_copy 为主要按钮；新建尚未保存时只有 common.save。按已保存的状态，不按未保存的输入。
  it.each(LANGUAGES)("shows the actions of each saved state in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    expect(STATE_ACTIONS).toEqual({
      draft: ["publish", "copy", "delete", "preview"],
      scheduled: ["withdraw", "copy", "preview"],
      running: ["end", "copy"],
      ended: ["copy"],
    });
    const expected: Record<(typeof STATES)[number], CopyKey[]> = {
      draft: ["common.save", "admin.promo_more", "admin.promo_publish", "admin.promo_copy", "admin.promo_delete", "admin.promo_preview"],
      scheduled: ["common.save", "admin.promo_more", "admin.promo_withdraw", "admin.promo_copy", "admin.promo_preview"],
      running: ["common.save", "admin.promo_more", "admin.promo_end_now", "admin.promo_copy"],
      ended: ["admin.promo_more", "admin.promo_copy"],
    };
    for (const state of STATES) {
      const editor = changeField(editorIn(state), "name", "Unsaved name");
      const bar = actionBar(renderAt(ready(editor), language));
      expect(textNodes(bar), state).toEqual(expected[state].map(t));
      const secondary = ["acs-admin__btn", "acs-admin__btn--secondary"];
      const kinds = tags(bar, "button").map((tag) => classes(tag));
      const actions = state === "ended" ? [["acs-admin__btn"]] : barActions(editor).map(() => secondary);
      expect(kinds, state).toEqual([...(state === "ended" ? [] : [["acs-admin__btn"]]), [...secondary, "site-admin-promo-edit__more"], ...actions]);
    }
    expect(barActions(openEditor(null, "csrf-1"))).toEqual([]);
    expect(textNodes(actionBar(renderAt(ready(openEditor(null, "csrf-1")), language)))).toEqual([t("common.save")]);
    expect(Object.keys(ACTION_LABEL).sort()).toEqual(["copy", "delete", "end", "preview", "publish", "withdraw"]);
  });

  // UX 0.20 A12 手机「操作栏固定在底部，[common.save] 之外的操作收在 [admin.promo_more] ▾ 里」与落实写法（1）「手机上保存之外的操作收在 admin.promo_more 的
  // 菜单里（按钮切换显隐）」：admin.promo_more 以 aria-controls 指向操作的菜单、aria-expanded 标明显隐；点它切换菜单的显隐，点菜单里的操作后菜单收起。
  it.each(LANGUAGES)("toggles the more menu on phones in %s", (language) => {
    const closed = editorIn("draft");
    let bar = actionBar(renderAt(ready(closed), language));
    const more = buttonTag(bar, COPY["admin.promo_more"][language]);
    expect(more.get("type")).toBe("button");
    expect(more.get("aria-expanded")).toBe("false");
    const menu = element(bar, "div", "site-admin-promo-edit__menu");
    const menuTag = attributes(tags(menu, "div")[0] ?? "");
    expect(menuTag.get("id")).toBe(more.get("aria-controls"));
    expect(menuTag.get("class")).toBe("acs-admin__panel site-admin-promo-edit__menu");
    expect(textNodes(menu)).toEqual(["admin.promo_publish", "admin.promo_copy", "admin.promo_delete", "admin.promo_preview"].map((key) => COPY[key as CopyKey][language]));

    const open = toggleMore(closed);
    expect(open.more).toBe(true);
    bar = actionBar(renderAt(ready(open), language));
    expect(buttonTag(bar, COPY["admin.promo_more"][language]).get("aria-expanded")).toBe("true");
    expect(attributes(tags(element(bar, "div", "site-admin-promo-edit__menu"), "div")[0] ?? "").get("class")).toBe(
      "acs-admin__panel site-admin-promo-edit__menu site-admin-promo-edit__menu--open",
    );
    expect(toggleMore(open)).toEqual(closed);

    for (const action of ["publish", "delete", "preview"] as const) {
      expect(pressAction(open, action, NOW, effects()).more, action).toBe(false);
    }
    const ended = toggleMore(editorIn("ended"));
    expect(pressAction(ended, "copy", NOW, effects()).more).toBe(false);
  });

  // UX 0.20 A12 说明「前台预览」「发布、撤回为草稿、提前结束与复制都作用于已保存的版本：有未保存修改时这些按钮同样先提示 [admin.promo_save_first]，
  // 不连同未保存的输入一起提交」与「有未保存修改时先提示 [admin.promo_save_first]」：有未保存修改时点发布、撤回、提前结束、复制与预览只显示
  // admin.promo_save_first，不发请求、不打开预览、不弹确认框；保存之后（没有未保存修改）照常。
  it.each(LANGUAGES)("only asks to save first when there are unsaved changes in %s", async (language) => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    // 已结束全部只读、不会有未保存修改，复制与预览用草稿。
    for (const action of ["publish", "withdraw", "end", "copy", "preview"] as const) {
      const state = action === "preview" || action === "copy" ? "draft" : STATE_OF[action];
      const editor = changeCopy(editorIn(state), "badge_en", "Edited");
      const effect = effects();
      const pressed = pressAction(editor, action, NOW, effect);
      expect(effect.run, action).not.toHaveBeenCalled();
      expect(effect.open, action).not.toHaveBeenCalled();
      expect(pressed, action).toEqual({ ...editor, notice: { kind: "saveFirst" } });
      const html = renderAt(ready(pressed), language);
      expect(html).toContain(`<p class="site-admin-promo-edit__save-first" role="status">${escapeHtml(COPY["admin.promo_save_first"][language])}</p>`);
      expect(html).not.toContain('role="dialog"');
      // 改值之后提示收起。
      expect(changeCopy(pressed, "badge_en", "Edited again").notice).toBeNull();
    }
    await Promise.resolve();
    expect(request).not.toHaveBeenCalled();
    const clean = effects();
    pressAction(editorIn("draft"), "publish", NOW, clean);
    expect(clean.run).toHaveBeenCalledTimes(1);
  });

  // UX 0.20 A12 说明「提前结束、撤回、删除都先弹确认框：[admin.promo_end_confirm]、[admin.promo_withdraw_confirm]、[admin.promo_delete_confirm]，按钮为该操作本身
  // （[admin.promo_end_now]、[admin.promo_withdraw]、[admin.promo_delete]）与 ( [common.cancel] )，与 A08 移除标志的确认框写法相同」：点这三个只打开确认框、
  // 不发请求（删除有未保存修改时同样只弹确认框）；确认框为 role dialog、aria-modal、aria-labelledby 指向说明，按钮依次为 common.cancel（次要）与该操作（主要）。
  it.each(LANGUAGES)("asks before withdrawing, ending and deleting in %s", (language) => {
    const cases = [
      ["withdraw", "admin.promo_withdraw_confirm", "admin.promo_withdraw"],
      ["end", "admin.promo_end_confirm", "admin.promo_end_now"],
      ["delete", "admin.promo_delete_confirm", "admin.promo_delete"],
    ] as const;
    for (const [action, text, label] of cases) {
      const editor = editorIn(STATE_OF[action]);
      expect(renderAt(ready(editor), language)).not.toContain('role="dialog"');
      const effect = effects();
      const asking = pressAction(editor, action, NOW, effect);
      expect(effect.run, action).not.toHaveBeenCalled();
      expect(asking, action).toEqual({ ...editor, confirm: action });
      const html = renderAt(ready(asking), language);
      const dialogTag = tags(html, "div").find((tag) => attributes(tag).get("role") === "dialog") ?? "";
      expect(classes(dialogTag)).toEqual(["acs-admin__panel", "site-admin__confirm"]);
      expect(attributes(dialogTag).get("aria-modal")).toBe("true");
      const labelId = attributes(dialogTag).get("aria-labelledby") ?? "";
      expect(labelId).not.toBe("");
      const dialog = element(html, "div", "site-admin__confirm");
      expect(dialog).toContain(`<p id="${escapeHtml(labelId)}">${escapeHtml(COPY[text][language])}</p>`);
      expect(tags(dialog, "button").map((tag) => [attributes(tag).get("type"), classes(tag)])).toEqual([
        ["button", ["acs-admin__btn", "acs-admin__btn--secondary"]],
        ["button", ["acs-admin__btn"]],
      ]);
      expect(textNodes(dialog)).toEqual([COPY[text][language], COPY["common.cancel"][language], COPY[label][language]]);
    }
    // 删除有未保存修改时同样只弹确认框（删除丢掉本页修改）。
    const edited = changeCopy(editorIn("draft"), "badge_en", "Edited");
    const effect = effects();
    expect(pressAction(edited, "delete", NOW, effect)).toEqual({ ...edited, confirm: "delete" });
    expect(effect.run).not.toHaveBeenCalled();
    // 发布不弹确认框，直接提交。
    const publish = effects();
    expect(pressAction(editorIn("draft"), "publish", NOW, publish).confirm).toBeNull();
    expect(publish.run).toHaveBeenCalledTimes(1);
  });

  // 同上原句「与 A08 移除标志的确认框写法相同」（落实写法（3）「common.cancel 自动聚焦、Escape 等于取消」）：common.cancel 带 autoFocus，取消与 Escape 只关闭
  // 确认框、不发请求；点该操作本身才提交一次（带打开确认框时的编辑区），之后确认框关闭、按钮都禁用、该操作标 aria-busy。
  it("cancels or confirms the dialog", () => {
    const onCancel = vi.fn(() => undefined);
    const onConfirm = vi.fn(() => undefined);
    const tree = confirmTree({ action: "end", onCancel, onConfirm });
    const buttons = tree.filter((node) => node.type === "button");
    expect(buttons.map((node) => [node.props.onClick, node.props.autoFocus])).toEqual([
      [onCancel, true],
      [onConfirm, undefined],
    ]);
    const overlay = tree.find((node) => node.props.className === "site-admin__overlay");
    const keyDown = overlay?.props.onKeyDown as (event: { key: string }) => unknown;
    keyDown({ key: "Enter" });
    expect(onCancel).not.toHaveBeenCalled();
    keyDown({ key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    for (const action of ["withdraw", "end", "delete"] as const) {
      const editor = editorIn(STATE_OF[action]);
      const asking = pressAction(editor, action, NOW, effects());
      expect(cancelConfirm(asking)).toEqual(editor);
      expect(cancelConfirm(editor)).toBe(editor);
      const run = vi.fn<(editor: Editor, action: StateAction) => void>();
      expect(confirmAction(editor, run)).toBe(editor);
      const confirmed = confirmAction(asking, run);
      expect(run).toHaveBeenCalledTimes(1);
      expect(run).toHaveBeenCalledWith(asking, action);
      expect(confirmed).toEqual({ ...editor, saving: true, acting: action });
      expect(confirmAction(confirmed, run)).toBe(confirmed);
      expect(pressAction(confirmed, action, NOW, effects())).toBe(confirmed);
      const bar = actionBar(renderAt(ready(confirmed), "en"));
      expect(renderAt(ready(confirmed), "en")).not.toContain('role="dialog"');
      expect(buttonTag(bar, COPY[ACTION_LABEL[action]].en).get("aria-busy")).toBe("true");
      expect(buttonTag(bar, COPY["common.save"].en).get("aria-busy")).toBe("false");
      expect(tags(bar, "button").filter((tag) => !classes(tag).includes("site-admin-promo-edit__more")).every((tag) => attributes(tag).has("disabled"))).toBe(true);
    }
  });

  // UX 0.20 A12「结果提示：[admin.promo_saved] / …」与 UX-COPY admin.promo_saved「保存、发布、撤回、提前结束成功」，落实写法（4）「发布、撤回与提前结束成功后
  // 以返回的活动为新基准（状态标签与可改范围随之更新），显示 admin.promo_saved」：请求带读取到的 version 与 CSRF 令牌；成功后状态标签、操作与只读随返回的状态更新。
  it.each(LANGUAGES)("takes the returned promotion after publishing, withdrawing and ending in %s", async (language) => {
    const cases = [
      ["publish", "scheduled"],
      ["withdraw", "draft"],
      ["end", "ended"],
    ] as const;
    for (const [action, next] of cases) {
      const editor = { ...editorIn(STATE_OF[action]), locked: true };
      const returned = promotion({ state: next, version: 4 });
      const { record, reply, signal, navigated } = await runWith(editor, action, { kind: "ok", promotion: returned });
      expect(reply).toHaveBeenCalledWith(7, 3, "csrf-1", signal);
      expect(record.state).toEqual(ready(openEditor(returned, "csrf-1", { kind: "done", key: "admin.promo_saved" })));
      expect(navigated).toEqual([]);
      expect(record.replaced).toEqual([]);
      const html = renderAt(record.state, language);
      expect(html).toContain(`<p class="site-admin-promo-edit__done" role="status">${escapeHtml(COPY["admin.promo_saved"][language])}</p>`);
      expect(element(html, "div", "site-admin-promo-edit__title")).toContain(
        `<span class="${PROMOTION_STATE_CLASS[next]}">${escapeHtml(COPY[PROMOTION_STATE_LABEL[next]][language])}</span>`,
      );
      expect(textNodes(element(html, "div", "site-admin-promo-edit__menu"))).toEqual(STATE_ACTIONS[next].map((item: BarAction) => COPY[ACTION_LABEL[item]][language]));
    }
  });

  // UX 0.20 A12 说明「复制成功后进入新草稿的编辑区，显示 [admin.promo_copied]」与落实写法（4）「复制成功后以站内跳转进入 adminPromotionPath(新草稿 ID)，新页面显示
  // admin.promo_copied（沿用 SHOP-TASK-270 的一次性提示）」：复制只带 ID 与 CSRF 令牌；成功后以 navigate（不是 replace）进入新草稿，读取后显示一次 admin.promo_copied。
  it.each(LANGUAGES)("moves to the copied draft and shows copied there once in %s", async (language) => {
    for (const state of STATES) {
      const copied = promotion({ id: 42, version: 1, state: "draft", starts_at: null, ends_at: null });
      const { record, reply, signal, navigated } = await runWith(editorIn(state), "copy", { kind: "ok", promotion: copied });
      expect(reply).toHaveBeenCalledWith(7, "csrf-1", signal);
      expect(navigated).toEqual([adminPromotionPath(42)]);
      expect(record.replaced).toEqual([]);
      const page = recorder();
      const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: copied, csrf_token: "csrf-42" }));
      await loadEditor({ kind: "existing", id: 42 }, new AbortController().signal, page.moves, takePromotionNotice(42), fakeApi({ readOne }));
      expect(editorOf(page.state).notice).toEqual({ kind: "done", key: "admin.promo_copied" });
      expect(renderAt(page.state, language)).toContain(
        `<p class="site-admin-promo-edit__done" role="status">${escapeHtml(COPY["admin.promo_copied"][language])}</p>`,
      );
      expect(takePromotionNotice(42)).toBeNull();
    }
  });

  // 落实写法（4）「删除成功后以 replace 回到列表」（UX 0.20 A12「去向」之外不另设页面）：删除带读取到的 version 与 CSRF 令牌，成功后以 replace 回到 /admin/promotions。
  it("goes back to the list after deleting", async () => {
    const { record, reply, signal, navigated } = await runWith(editorIn("draft"), "delete", { kind: "ok" });
    expect(reply).toHaveBeenCalledWith(7, 3, "csrf-1", signal);
    expect(record.replaced).toEqual([PROMOTIONS_PATH]);
    expect(navigated).toEqual([]);
    expect(actionStep("delete", { kind: "ok" })).toEqual({ kind: "deleted" });
  });

  // UX 0.20 A12 说明「规则」中「发布条件不满足时整次不生效：[admin.promo_invalid] 下逐条列出问题（…发布条件为 [admin.promo_publish_missing]），与其他已发布活动
  // 时间重叠为 [admin.promo_overlap]…；版本不符为 [admin.promo_changed] 与 ( [admin.reload] )」与落实写法（4）「失败的提示与保存相同」：每种状态操作失败时的提示同保存，
  // 本页不变、按钮恢复可用；401 回到 A01；不存在只剩 common.error_retry 与 admin.promo_back。
  it.each(LANGUAGES)("shows the same failures as saving for every state action in %s", async (language) => {
    const failures: [PromotionWrite, Editor["notice"]][] = [
      [{ kind: "invalid", problems: [problem("publish_missing")] }, { kind: "invalid", products: [], publishMissing: true, other: false }],
      [{ kind: "overlap", name: "Hari <Raya>" }, { kind: "overlap", name: "Hari <Raya>" }],
      [{ kind: "changed" }, { kind: "changed" }],
      [{ kind: "state", state: "running" }, { kind: "changed" }],
      [{ kind: "ended" }, { kind: "changed" }],
      [{ kind: "network" }, { kind: "alert", key: "common.network_check" }],
      [{ kind: "failed" }, { kind: "alert", key: "common.error_retry" }],
    ];
    for (const action of STATE_ACTION_LIST) {
      const editor = editorIn(STATE_OF[action]);
      for (const [write, notice] of failures) {
        const { record, navigated } = await runWith(editor, action, write);
        expect(editorOf(record.state), `${action} ${write.kind}`).toEqual({ ...editor, notice });
        expect(navigated).toEqual([]);
        expect(record.replaced).toEqual([]);
      }
      const none = await runWith(editor, action, { kind: "none" });
      expect(none.record.replaced).toEqual(["/admin/login"]);
      const missing = await runWith(editor, action, { kind: "missing" });
      expect(missing.record.state).toEqual({ status: "gone", error: "common.error_retry" });
    }
    const publish = await runWith(editorIn("draft"), "publish", { kind: "invalid", problems: [problem("publish_missing")] });
    expect(textNodes(element(renderAt(publish.record.state, language), "div", "site-admin-promo-edit__invalid"))).toEqual([
      COPY["admin.promo_invalid"][language],
      COPY["admin.promo_publish_missing"][language],
    ]);
    const overlap = await runWith(editorIn("scheduled"), "withdraw", { kind: "overlap", name: "Hari <Raya>" });
    expect(renderAt(overlap.record.state, language)).toContain(escapeHtml(formatCopy(COPY["admin.promo_overlap"][language], { name: "Hari <Raya>" })));
    const changed = await runWith(editorIn("running"), "end", { kind: "changed" });
    const changedHtml = renderAt(changed.record.state, language);
    expect(changedHtml).toContain(escapeHtml(COPY["admin.promo_changed"][language]));
    expect(changedHtml).toContain(escapeHtml(COPY["admin.reload"][language]));
  });

  // 落实写法（4）「失败的提示与保存相同」中 SHOP-TASK-270 的 csrf（显示 common.error_retry 并重新取 CSRF 令牌，只换令牌）：状态操作被答 403 时以 readAdminPromotion
  // 重取令牌，不再重试该操作；离开页面（中止）之后的结果不再更新页面。
  it("reads a new CSRF token after csrf and ignores results after leaving", async () => {
    for (const action of STATE_ACTION_LIST) {
      const editor = editorIn(STATE_OF[action]);
      const readOne = vi.fn(() => Promise.resolve<PromotionRead>({ kind: "ok", promotion: promotion({ version: 9 }), csrf_token: "csrf-2" }));
      const { record, reply, signal } = await runWith(editor, action, { kind: "csrf" }, { readOne });
      expect(reply).toHaveBeenCalledTimes(1);
      expect(readOne).toHaveBeenCalledWith(7, signal);
      expect(editorOf(record.state)).toEqual({ ...editor, csrfToken: "csrf-2", notice: { kind: "alert", key: "common.error_retry" } });
    }
    const controller = new AbortController();
    const editor = editorIn("draft");
    const record = recorder(ready(startAction(editor, "publish")));
    const publish = vi.fn(() => {
      controller.abort();
      return Promise.resolve<PromotionWrite>({ kind: "ok", promotion: promotion({ state: "scheduled" }) });
    });
    await runAction(editor, "publish", controller.signal, { ...record.moves, navigate: () => undefined }, fakeActionApi({ publish }));
    expect(record.state).toEqual(ready(startAction(editor, "publish")));
  });

  // UX 0.20 A12 说明「前台预览」「[admin.promo_preview] 在新标签页打开前台预览，显示的是已保存的内容」与落实写法（5）「以新标签页打开 SHOP-TASK-280 的
  // adminPromotionPreviewPath(活动 ID)（window.open，带 noopener）」：点预览不发请求，打开 /admin/promotions/<活动 ID>/preview；打开时带 _blank 与 noopener。
  it("opens the storefront preview in a new tab", () => {
    for (const state of ["draft", "scheduled"] as const) {
      const editor = editorIn(state);
      const effect = effects();
      expect(pressAction(editor, "preview", NOW, effect)).toBe(editor);
      expect(effect.open).toHaveBeenCalledWith("/admin/promotions/7/preview");
      expect(effect.run).not.toHaveBeenCalled();
    }
    // 进行中与已结束不提供预览。
    for (const state of ["running", "ended"] as const) {
      const effect = effects();
      pressAction(editorIn(state), "preview", NOW, effect);
      expect(effect.open).not.toHaveBeenCalled();
    }
    const target = { open: vi.fn<(url?: string | URL, name?: string, features?: string) => null>(() => null) };
    openPreview("/admin/promotions/7/preview", target);
    expect(target.open).toHaveBeenCalledWith("/admin/promotions/7/preview", "_blank", "noopener");
  });

  // UX 0.20 A12 说明「前台预览」「前台预览的唯一禁用条件是：已保存的内容里没有开始时间或结束时间，或结束时间已过——这时 [admin.promo_preview] 禁用并显示
  // [admin.promo_preview_needs_time]」与落实写法（5）「马来西亚当前时刻（当前 UTC 时间加 8 小时取到分钟…按字符串比较）」：开始为空、结束为空、结束不晚于当前时
  // 禁用并在旁显示说明（按钮以 aria-describedby 指向它），点它不打开；结束晚于当前时可用、没有说明。按已保存的时间，不按未保存的输入。
  it.each(LANGUAGES)("disables the preview without a saved time or after the end in %s", (language) => {
    expect(malaysiaMinute(Date.UTC(2026, 10, 19, 16, 30, 59))).toBe("2026-11-20T00:30");
    expect(malaysiaMinute(Date.UTC(2027, 0, 1, 0, 0, 0))).toBe("2027-01-01T08:00");
    const label = COPY["admin.promo_preview"][language];
    const note = escapeHtml(COPY["admin.promo_preview_needs_time"][language]);
    const blocked: [string, Partial<AdminPromotion>][] = [
      ["no start", { starts_at: null }],
      ["no end", { ends_at: null }],
      ["end now", { ends_at: NOW }],
      ["end passed", { ends_at: "2026-12-31T23:59" }],
    ];
    for (const [name, overrides] of blocked) {
      for (const state of ["draft", "scheduled"] as const) {
        const editor = editorIn(state, overrides);
        expect(previewNeedsTime(editor.promotion ?? promotion(), NOW), name).toBe(true);
        const bar = actionBar(renderAt(ready(editor), language));
        const button = buttonTag(bar, label);
        expect(button.has("disabled"), name).toBe(true);
        const preview = element(bar, "span", "site-admin-promo-edit__preview");
        expect(preview).toContain(`<span id="${escapeHtml(button.get("aria-describedby") ?? "")}" class="acs-admin__muted">${note}</span>`);
        const effect = effects();
        expect(pressAction(editor, "preview", NOW, effect), name).toBe(editor);
        expect(effect.open, name).not.toHaveBeenCalled();
      }
    }
    // 结束晚于当前：可用，没有说明；未保存的空时间不影响。
    const later = changeField(editorIn("draft", { ends_at: "2027-01-01T00:01" }), "starts_at", "");
    const bar = actionBar(renderAt(ready(later), language));
    expect(buttonTag(bar, label).has("disabled")).toBe(false);
    expect(buttonTag(bar, label).has("aria-describedby")).toBe(false);
    expect(bar).not.toContain(note);
    expect(previewNeedsTime(promotion({ ends_at: "2027-01-01T00:01" }), NOW)).toBe(false);
  });
});
