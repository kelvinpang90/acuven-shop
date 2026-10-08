import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 相同），用来找出 UX-COPY 里全部 admin.nav_* 导航项。
import uxCopy from "../../../docs/UX-COPY.md?raw";
import App from "../App";
import { CSRF_HEADER } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, useCopy } from "../i18n/language";
import { RouterProvider } from "../router";
import {
  ADMIN_NAV,
  AdminFrameView,
  DARK_SCHEME_QUERY,
  INITIAL_FRAME,
  leaveFrame,
  logOut,
  openFrame,
  sessionStep,
  watchColorScheme,
} from "./AdminFrame";
import type { AdminFrameViewProps, ColorMode, FrameMoves, FrameState, SchemeQuery } from "./AdminFrame";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDERS_PATH = "/admin/orders";
const REFUNDS_PATH = "/admin/refunds";
const LOGIN_PATH = "/admin/login";
const USERNAME = "shop-admin";
const TOKEN = "admin-csrf-1";
const NEW_TOKEN = "admin-csrf-2";
const SESSION = { username: USERNAME, expires_at: "2026-11-05T08:00:00Z", csrf_token: TOKEN };
const SIGNED_IN: FrameState = { status: "in", csrfToken: TOKEN, error: null };
const LANGUAGE_LABEL = { en: "common.lang_en", zh: "common.lang_zh", ms: "common.lang_ms" } as const;
const HTML_LANG = { en: "en", zh: "zh-Hans", ms: "ms" } as const;

// UX-COPY 第 10 节里全部导航项的键（admin.nav_orders、admin.nav_refunds 与 admin.nav_stock_resets 之外的都是尚未上线页面的项）。
const DOC_NAV_KEYS = [...uxCopy.matchAll(/^\| `(admin\.nav_[a-z_]+)` \|/gm)].map((m) => m[1] ?? "");
const LIVE_NAV_KEYS = ["admin.nav_orders", "admin.nav_refunds", "admin.nav_stock_resets"];
const STOCK_RESETS_PATH = "/admin/stock-resets";
// 尚未上线页面的导航项在 UX-COPY 里的三语文字。
function otherNavLabels(language: Language): string[] {
  const column = { en: 2, zh: 3, ms: 4 }[language];
  return [...uxCopy.matchAll(/^\| `(admin\.nav_[a-z_]+)` \|(.*)$/gm)]
    .filter((m) => !LIVE_NAV_KEYS.includes(m[1] ?? ""))
    .map((m) => (`|${m[2] ?? ""}`.split("|")[column - 1] ?? "").trim());
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function renderApp(path: string, language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={path} storage={languageStorage(language)} />);
}

const noop = () => undefined;

// 框架内容的替身：只渲染订单页的标题 admin.nav_orders（h1）。订单页的列表由 pages/AdminOrdersPage.test.tsx 测试（SHOP-TASK-048）。
function AdminOrdersContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_orders")}</h1>;
}

function props(overrides: Partial<AdminFrameViewProps> = {}): AdminFrameViewProps {
  return {
    mode: "light",
    current: "orders",
    state: SIGNED_IN,
    busy: false,
    menuOpen: false,
    onToggleMenu: noop,
    onCloseMenu: noop,
    onLogOut: noop,
    children: <AdminOrdersContent />,
    ...overrides,
  };
}

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={ORDERS_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminFrameViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminFrameView {...props(overrides)} />, language));
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...textNodes, ...attributes].filter((value) => value !== "");
}

// 某种元素的全部开始标签。
function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

// 开始标签的属性：属性名不分大小写，与顺序无关；没有值的属性为空串。
function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/).filter((name) => name !== "");
}

function rootTag(html: string): string {
  return html.slice(0, html.indexOf(">") + 1);
}

// 某个元素（按 class 找到的第一个）从开始标签到与之配对的结束标签的内容（数同名元素的嵌套）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  const pattern = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  pattern.lastIndex = start;
  let depth = 0;
  for (let m = pattern.exec(html); m !== null; m = pattern.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed: <${name} class="${className}">`);
}

// 带某个 class 的全部按钮的开始标签。
function buttons(html: string, className: string): string[] {
  return tags(html, "button").filter((tag) => classes(tag).includes(className));
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      const next = queue.shift();
      if (next === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

// 路由替身：记录框架显示的状态与经 replace 去的地址。
function fakeMoves() {
  const events: string[] = [];
  const shown: FrameState[] = [];
  const target: FrameMoves = {
    show: (state) => {
      shown.push(state);
      events.push(`show ${state.status}`);
    },
    replace: (path) => {
      events.push(`replace ${path}`);
    },
  };
  return { target, events, shown };
}

// matchMedia 替身：可改变 matches 并通知已注册的监听。
function fakeMatchMedia(dark: boolean) {
  const listeners = new Set<() => void>();
  const queries: string[] = [];
  let matches = dark;
  const query: SchemeQuery = {
    get matches() {
      return matches;
    },
    addEventListener: (_type, listener) => {
      listeners.add(listener);
    },
    removeEventListener: (_type, listener) => {
      listeners.delete(listener);
    },
  };
  const matchMedia = (text: string) => {
    queries.push(text);
    return query;
  };
  const change = (nextDark: boolean) => {
    matches = nextDark;
    for (const listener of listeners) {
      listener();
    }
  };
  return { matchMedia, change, listeners, queries };
}

describe("first render", () => {
  // SHOP-TASK-047 验收第 3 条「请求返回前即显示横幅、导航与语言切换，内容区标 aria-busy，退出按钮禁用」与第 2 条「根元素 class acs-admin，data-mode 首次渲染为 light」
  // 「顶部演示横幅（common.demo_badge 与 admin.demo_banner）」；UX「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：
  // 经路由打开 /admin/orders（服务端渲染，会话未返回），根元素与横幅都在，内容区为空并标 aria-busy，两个退出按钮（桌面、手机）都禁用；
  // 不套前台框架（App.tsx 对 /admin 开头的路由不套 SiteFrame）。
  it.each(LANGUAGES)("shows the banner, the navigation, the languages and a disabled logout before the session returns in %s", (language) => {
    const html = renderApp(ORDERS_PATH, language);
    const root = attributes(rootTag(html));
    expect(classes(rootTag(html))).toContain("acs-admin");
    expect(root.get("data-mode")).toBe("light");
    expect(html).not.toMatch(/<(header|footer)\b/);
    expect(html).not.toContain(BRAND);
    expect(html).not.toContain(COPY["common.demo_banner"][language]);
    const banner = element(html, "div", "acs-admin__banner");
    expect(banner).toContain(`<span class="acs-tag acs-tag--demo">${escapeHtml(COPY["common.demo_badge"][language])}</span>`);
    expect(banner).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    const main = tags(html, "main")[0] ?? "";
    expect(attributes(main).get("aria-busy")).toBe("true");
    expect(element(html, "main", "acs-admin__main")).toBe(`${main}</main>`);
    expect(html).not.toMatch(/<h1\b/);
    expect(html).not.toContain(`role="alert"`);
    const logouts = buttons(html, "site-admin__logout");
    expect(logouts).toHaveLength(2);
    for (const logout of logouts) {
      expect(attributes(logout).has("disabled")).toBe(true);
      expect(classes(logout)).toEqual(expect.arrayContaining(["acs-admin__btn", "acs-admin__btn--secondary"]));
    }
    expect(html).toContain(`>${escapeHtml(COPY["admin.logout"][language])}</button>`);
  });

  // UX「管理后台总体」「每页右上角常驻 [admin.logout]：桌面在 [admin.demo_banner] 右侧，手机在顶栏右端」：
  // 桌面的退出按钮与横幅同在顶部一行、排在横幅之后；手机的退出按钮在顶栏里，排在 ☰ 与当前导航项名称之后。
  it("places the logout button after the banner and at the end of the phone bar", () => {
    const html = render();
    const top = element(html, "div", "site-admin__top");
    expect(top.indexOf("acs-admin__banner")).toBeLessThan(top.indexOf("site-admin__logout"));
    expect(buttons(top, "site-admin__logout")).toHaveLength(1);
    const bar = element(html, "div", "site-admin__bar");
    expect(bar.indexOf("site-admin__menu-button")).toBeLessThan(bar.indexOf("site-admin__bar-title"));
    expect(bar.indexOf("site-admin__bar-title")).toBeLessThan(bar.indexOf("site-admin__logout"));
  });

  // Kelvin 2026-10-06（docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」与验收第 2 条「当前项标 aria-current="page"」
  // 「nav 元素不加 aria-label」；SHOP-TASK-056 改动：原来断言导航只含订单一项，现按 UX「管理后台总体」「导航项依次为 [admin.nav_orders]、
  // [admin.nav_refunds]、…」与 SHOP-TASK-056 验收第 2 条改为「按 UX 顺序只含订单与退款两项、其他 admin.nav_* 不出现」：
  // 桌面左侧导航依次为 admin.nav_orders（指向 /admin/orders，当前页标 aria-current="page"）与 admin.nav_refunds（指向 /admin/refunds）；
  // UX-COPY 里其他 admin.nav_* 的文字在内容区以外都不出现（A02 的列头 admin.col_refunds 与 admin.nav_refunds 文字相同，SHOP-TASK-048）；
  // 页面上任何 nav 都没有 aria-label。
  // SHOP-TASK-059 改动：按 SHOP-TASK-059 验收第 3 条把「按 UX 顺序只含订单与退款两项」改为「按 UX 顺序只含订单、退款与库存重置三项、
  // 其他 admin.nav_* 不出现」：第三项为 admin.nav_stock_resets（指向 /admin/stock-resets），在 UX-COPY 的导航项里排在退款之后。
  it.each(LANGUAGES)("lists only the orders, refunds and stock resets items in UX order in the navigation in %s", (language) => {
    expect(ADMIN_NAV).toEqual(["orders", "refunds", "stockResets"]);
    expect(DOC_NAV_KEYS.filter((key) => LIVE_NAV_KEYS.includes(key))).toEqual(LIVE_NAV_KEYS);
    expect(DOC_NAV_KEYS.length).toBeGreaterThan(LIVE_NAV_KEYS.length);
    for (const html of [renderApp(ORDERS_PATH, language), render({}, language), render({ menuOpen: true }, language)]) {
      const nav = element(html, "nav", "site-admin__nav");
      const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
      expect(items.map((tag) => attributes(tag).get("href"))).toEqual([ORDERS_PATH, REFUNDS_PATH, STOCK_RESETS_PATH]);
      expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual(["page", undefined, undefined]);
      expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_orders"][language])}</a>`);
      expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_refunds"][language])}</a>`);
      expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_stock_resets"][language])}</a>`);
      expect(nav.indexOf(COPY["admin.nav_orders"][language])).toBeLessThan(nav.indexOf(escapeHtml(COPY["admin.nav_refunds"][language])));
      expect(nav.indexOf(escapeHtml(COPY["admin.nav_refunds"][language]))).toBeLessThan(
        nav.indexOf(escapeHtml(COPY["admin.nav_stock_resets"][language])),
      );
      for (const tag of tags(html, "nav")) {
        expect(attributes(tag).has("aria-label")).toBe(false);
      }
      const texts = visibleTexts(html.replace(element(html, "main", "acs-admin__main"), ""));
      for (const label of otherNavLabels(language)) {
        expect(label).not.toBe("");
        expect(texts).not.toContain(label);
      }
    }
  });

  // 验收第 2 条「导航底部为三种语言的站内链接（沿用 A01 的语言切换，点击只切换界面语言）」与 UX「界面语言切换用 [common.lang_*]」：
  // 桌面导航里、导航项之后为三种语言的链接，都指向本页（网址里不带语言），当前语言 aria-current 为 true。
  it.each(LANGUAGES)("ends the navigation with the three language links in %s", (language) => {
    const nav = element(renderApp(ORDERS_PATH, language), "nav", "site-admin__nav");
    const langs = element(nav, "div", "site-admin__lang");
    expect(nav.indexOf(langs)).toBeGreaterThan(nav.indexOf(COPY["admin.nav_orders"][language]));
    const anchors = tags(langs, "a").map(attributes);
    expect(anchors.map((a) => a.get("href"))).toEqual([ORDERS_PATH, ORDERS_PATH, ORDERS_PATH]);
    expect(anchors.map((a) => a.get("lang"))).toEqual(LANGUAGES.map((option) => HTML_LANG[option]));
    expect(anchors.filter((a) => a.get("aria-current") === "true").map((a) => a.get("lang"))).toEqual([HTML_LANG[language]]);
    for (const option of LANGUAGES) {
      expect(langs).toContain(`>${escapeHtml(COPY[LANGUAGE_LABEL[option]][language])}</a>`);
    }
  });
});

describe("phone menu", () => {
  // 验收第 2 条「手机顶栏为 ☰ 按钮（图形为 aria-hidden 的 SVG，按钮里没有字典以外的文字，可访问名称 common.nav_menu，带 aria-expanded）与当前导航项名称」
  // 与 UX「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：收起时按钮 aria-expanded 为 false，里面只有 aria-hidden 的 SVG，
  // 名称为 admin.nav_orders，按钮控制的菜单带 hidden。
  it.each(LANGUAGES)("shows the closed menu button and the current item name in %s", (language) => {
    const html = renderApp(ORDERS_PATH, language);
    const bar = element(html, "div", "site-admin__bar");
    const button = element(bar, "button", "site-admin__menu-button");
    const buttonTag = tags(button, "button")[0] ?? "";
    expect(attributes(buttonTag).get("type")).toBe("button");
    expect(attributes(buttonTag).get("aria-label")).toBe(COPY["common.nav_menu"][language]);
    expect(attributes(buttonTag).get("aria-expanded")).toBe("false");
    expect(tags(button, "svg").map((tag) => attributes(tag).get("aria-hidden"))).toEqual(["true"]);
    expect(visibleTexts(button.replace(/\saria-label="[^"]*"/, ""))).toEqual([]);
    expect(element(bar, "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_orders"][language])}</span>`,
    );
    const menuId = attributes(buttonTag).get("aria-controls") ?? "";
    expect(menuId).not.toBe("");
    const menuTag = tags(html, "div").find((tag) => attributes(tag).get("id") === menuId) ?? "";
    expect(classes(menuTag)).toContain("site-admin__menu");
    expect(attributes(menuTag).has("hidden")).toBe(true);
  });

  // 验收第 2 条「点开后按 A00-phone-menu 显示导航项、语言链接与退出按钮」：展开时按钮 aria-expanded 为 true，菜单不带 hidden，
  // 依次为导航与三种语言的链接；退出按钮仍在顶栏右端；顶栏名称按 A00-phone-menu 为 common.nav_menu。
  // SHOP-TASK-056 改动：原来断言菜单的导航只含订单一项，现按验收第 2 条「☰ 菜单同样」改为按 UX 顺序只含订单（aria-current="page"）与退款两项。
  // SHOP-TASK-059 改动：按 SHOP-TASK-059 验收第 3 条「☰ 菜单同样」改为按 UX 顺序只含订单（aria-current="page"）、退款与库存重置三项。
  it.each(LANGUAGES)("opens the menu with the navigation, the languages and the logout button in %s", (language) => {
    const html = render({ menuOpen: true }, language);
    const bar = element(html, "div", "site-admin__bar");
    const buttonTag = buttons(bar, "site-admin__menu-button")[0] ?? "";
    expect(attributes(buttonTag).get("aria-expanded")).toBe("true");
    expect(bar).toContain(`<span class="site-admin__bar-title">${escapeHtml(COPY["common.nav_menu"][language])}</span>`);
    expect(buttons(bar, "site-admin__logout")).toHaveLength(1);
    const menu = element(html, "div", "site-admin__menu");
    const menuTag = tags(menu, "div")[0] ?? "";
    expect(attributes(menuTag).has("hidden")).toBe(false);
    expect(attributes(menuTag).get("id")).toBe(attributes(buttonTag).get("aria-controls"));
    const nav = element(menu, "nav", "site-admin__menu-nav");
    expect(tags(nav, "a").map((tag) => [attributes(tag).get("href"), attributes(tag).get("aria-current")])).toEqual([
      [ORDERS_PATH, "page"],
      [REFUNDS_PATH, undefined],
      [STOCK_RESETS_PATH, undefined],
    ]);
    expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_orders"][language])}</a>`);
    expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_refunds"][language])}</a>`);
    expect(nav).toContain(`>${escapeHtml(COPY["admin.nav_stock_resets"][language])}</a>`);
    const langs = element(menu, "div", "site-admin__lang");
    expect(menu.indexOf(langs)).toBeGreaterThan(menu.indexOf(nav));
    expect(tags(langs, "a").map((tag) => attributes(tag).get("lang"))).toEqual(LANGUAGES.map((option) => HTML_LANG[option]));
  });
});

describe("session", () => {
  // 验收第 3 条「框架打开时调用 GET /api/admin/session…200 显示页面内容；401 用路由的 replace 进入 /admin/login；其他结果在内容区显示 common.error_retry」。
  it("chooses what to show from the session reply", () => {
    expect(INITIAL_FRAME).toEqual({ status: "loading" });
    expect(sessionStep({ kind: "ok", session: SESSION })).toEqual({ status: "in", csrfToken: TOKEN, error: null });
    expect(sessionStep({ kind: "none" })).toBe("login");
    expect(sessionStep({ kind: "failed" })).toEqual({ status: "failed" });
    expect(sessionStep({ kind: "network" })).toEqual({ status: "failed" });
  });

  // 同一条：打开时恰好请求一次 GET /api/admin/session 并带上离开页面时中止用的 signal；200 显示内容（令牌取自会话），401 以 replace 进入 /admin/login，
  // 其他结果（500、网络中断、200 但响应体不合格）显示失败状态。
  it.each<[Response | (() => never), string[]]>([
    [json(200, SESSION), ["show in"]],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`]],
    [json(500, {}), ["show failed"]],
    [networkDown, ["show failed"]],
    [json(200, { username: USERNAME }), ["show failed"]],
  ])("opens the frame from the session reply (%#)", async (reply, events) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openFrame(controller.signal, moves.target);
    expect(calls.map((call) => `${String(call.init.method)} ${call.url}`)).toEqual(["GET /api/admin/session"]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (events[0] === "show in") {
      expect(moves.shown).toEqual([SIGNED_IN]);
    }
  });

  // 验收第 3 条「离开页面时中止请求，之后不再更新」：signal 中止后，不论会话回答什么，都不再显示或跳转。
  it.each([json(200, SESSION), json(401, { detail: "admin_session_required" }), json(500, {})])("does nothing after leaving the page (%#)", async (reply) => {
    stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = openFrame(controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });

  // 验收第 3 条「200 显示页面内容」与第 5 条「内容区只有标题 admin.nav_orders（h1）」：已登录时内容区不再标 aria-busy，
  // 只有订单页的 h1 标题，退出按钮可用。
  it.each(LANGUAGES)("shows the page content after 200 in %s", (language) => {
    const html = render({}, language);
    const main = element(html, "main", "acs-admin__main");
    expect(attributes(tags(main, "main")[0] ?? "").get("aria-busy")).toBe("false");
    expect(main).toBe(
      `${tags(main, "main")[0] ?? ""}<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_orders"][language])}</h1></main>`,
    );
    for (const logout of buttons(html, "site-admin__logout")) {
      expect(attributes(logout).has("disabled")).toBe(false);
    }
  });

  // 验收第 3 条「其他结果在内容区显示 common.error_retry」：失败时内容区只有 common.error_retry 提示（role="alert"），没有页面内容；
  // 没有会话令牌，退出按钮禁用；横幅与导航照常。
  it.each(LANGUAGES)("shows common.error_retry in the content area when the session cannot be read in %s", (language) => {
    const html = render({ state: { status: "failed" } }, language);
    const main = element(html, "main", "acs-admin__main");
    expect(main).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY["common.error_retry"][language])}</span></div>`);
    expect(main).not.toMatch(/<h1\b/);
    expect(attributes(tags(main, "main")[0] ?? "").get("aria-busy")).toBe("false");
    for (const logout of buttons(html, "site-admin__logout")) {
      expect(attributes(logout).has("disabled")).toBe(true);
    }
    expect(html).toContain("site-admin__nav");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
  });
});

describe("logging out", () => {
  // 验收第 4 条「退出调用 POST /api/admin/logout 并带会话接口返回的 X-CSRF-Token…204 或 401 用 replace 进入 /admin/login」：
  // 请求头带会话给的令牌、地址固定；204 与 401 都以 replace 进入 /admin/login，不再读取会话。
  it.each([empty(204), json(401, { detail: "admin_session_required" })])("goes to /admin/login after the reply %#", async (reply) => {
    const calls = stubFetch(reply);
    const moves = fakeMoves();
    await leaveFrame(TOKEN, new AbortController().signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/logout");
    expect(calls[0]?.init.method).toBe("POST");
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // 验收第 4 条「403 重新取会话并显示 common.error_retry（取到 401 时进入 /admin/login）」：POST 之后紧接着 GET 会话，
  // 取到时换成新令牌并提示重试；401 时以 replace 进入 /admin/login；其他失败时保留原令牌并提示重试。
  it("reads the session again after 403", async () => {
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, { ...SESSION, csrf_token: NEW_TOKEN }));
    await expect(logOut(TOKEN)).resolves.toEqual({ status: "in", csrfToken: NEW_TOKEN, error: "common.error_retry" });
    expect(calls.map((call) => `${String(call.init.method)} ${call.url}`)).toEqual(["POST /api/admin/logout", "GET /api/admin/session"]);

    stubFetch(json(403, { detail: "csrf_failed" }), json(401, { detail: "admin_session_required" }));
    const moves = fakeMoves();
    await leaveFrame(TOKEN, new AbortController().signal, moves.target);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);

    stubFetch(json(403, { detail: "csrf_failed" }), json(500, {}));
    await expect(logOut(TOKEN)).resolves.toEqual({ status: "in", csrfToken: TOKEN, error: "common.error_retry" });
  });

  // 验收第 4 条「网络中断显示 common.network_check，其他显示 common.error_retry」：两种情况都留在本页、令牌不变、不读取会话；
  // 提示在内容区（role="alert"），页面内容照常显示。
  it("shows a message on other failures", async () => {
    stubFetch(networkDown);
    await expect(logOut(TOKEN)).resolves.toEqual({ status: "in", csrfToken: TOKEN, error: "common.network_check" });
    for (const reply of [json(500, {}), json(200, {}), json(503, {})]) {
      const calls = stubFetch(reply);
      await expect(logOut(TOKEN)).resolves.toEqual({ status: "in", csrfToken: TOKEN, error: "common.error_retry" });
      expect(calls).toHaveLength(1);
    }
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const main = element(render({ state: { status: "in", csrfToken: TOKEN, error: key } }), "main", "acs-admin__main");
      expect(main).toContain(`<div class="acs-admin__alert" role="alert"><span>${COPY[key].en}</span></div>`);
      expect(main).toContain(`<h1 class="acs-admin__h">${COPY["admin.nav_orders"].en}</h1>`);
    }
  });

  // 验收第 4 条「进行中按钮禁用」：退出请求进行中两个退出按钮都带 disabled。
  it("disables the logout buttons while logging out", () => {
    const logouts = buttons(render({ busy: true }), "site-admin__logout");
    expect(logouts).toHaveLength(2);
    for (const logout of logouts) {
      expect(attributes(logout).has("disabled")).toBe(true);
    }
  });

  // 验收第 3 条「离开页面时中止请求，之后不再更新」（派生实现约束：退出同样适用）：signal 中止后退出的结果不再显示或跳转。
  it("does nothing after leaving the page", async () => {
    stubFetch(empty(204));
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = leaveFrame(TOKEN, controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });

  // 验收第 4 条「CSRF 令牌只在 React 状态与退出请求头里，不写网址、localStorage、sessionStorage 或 cookie」：
  // 打开、退出（含 403 后重读与成功）的整个过程不写任何浏览器存储、cookie 或历史记录；请求地址固定、不含令牌；页面标记里也没有令牌。
  it("keeps the CSRF token out of addresses, markup and browser storage", async () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const cookieWrites: string[] = [];
    const doc = {};
    Object.defineProperty(doc, "cookie", {
      get: () => "",
      set: (value: string) => {
        cookieWrites.push(value);
      },
    });
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("document", doc);
    vi.stubGlobal("history", history);

    const calls = stubFetch(json(200, SESSION), json(403, { detail: "csrf_failed" }), json(200, { ...SESSION, csrf_token: NEW_TOKEN }), empty(204));
    const signal = new AbortController().signal;
    const moves = fakeMoves();
    await openFrame(signal, moves.target);
    await leaveFrame(TOKEN, signal, moves.target);
    await leaveFrame(NEW_TOKEN, signal, moves.target);
    expect(moves.events).toEqual(["show in", "show in", `replace ${LOGIN_PATH}`]);

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(headers(calls[3])[CSRF_HEADER]).toBe(NEW_TOKEN);
    for (const call of calls) {
      expect(call.url).toMatch(/^\/api\/admin\/(session|logout)$/);
      expect(call.url).not.toContain(TOKEN);
    }
    for (const state of moves.shown) {
      for (const menuOpen of [false, true]) {
        const html = render({ state, menuOpen });
        expect(html).not.toContain(TOKEN);
        expect(html).not.toContain(NEW_TOKEN);
      }
    }
  });
});

describe("colour mode", () => {
  // 验收第 2 条「data-mode 首次渲染为 light，挂载后按 SHOP-TASK-038 的 watchColorScheme 随设备深浅色变化」与 UX「深浅色随管理员设备设置」：
  // 挂载后读一次设备设置，设置变化时再报告，取消订阅后不再报告；框架按报告的值写 data-mode。
  it("follows prefers-color-scheme and its changes", () => {
    const media = fakeMatchMedia(true);
    const modes: ColorMode[] = [];
    const stop = watchColorScheme(media.matchMedia, (mode) => modes.push(mode));
    expect(media.queries).toEqual([DARK_SCHEME_QUERY]);
    expect(modes).toEqual(["dark"]);
    media.change(false);
    expect(modes).toEqual(["dark", "light"]);
    stop();
    expect(media.listeners.size).toBe(0);
    media.change(true);
    expect(modes).toEqual(["dark", "light"]);
    for (const mode of ["light", "dark"] as const) {
      expect(attributes(rootTag(render({ mode }))).get("data-mode")).toBe(mode);
    }
    expect(attributes(rootTag(renderApp(ORDERS_PATH))).get("data-mode")).toBe("light");
  });
});

describe("dictionary", () => {
  // 验收第 6 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 读取中、已登录（含退出中与每种退出提示）、读取失败，菜单收起与展开，每段文字（含 aria-label）都是当前语言的某条字典文案；
  // title 与 placeholder 的值也必须是当前语言的字典文案（A02 视觉稿的搜索框以 admin.search_order 作占位文字，SHOP-TASK-048）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    const errors: (CopyKey | null)[] = [null, "common.error_retry", "common.network_check"];
    const states: FrameState[] = [INITIAL_FRAME, { status: "failed" }, ...errors.map((error): FrameState => ({ status: "in", csrfToken: TOKEN, error }))];
    const pages = [renderApp(ORDERS_PATH, language)];
    for (const state of states) {
      for (const menuOpen of [false, true]) {
        pages.push(render({ state, menuOpen }, language), render({ state, menuOpen, busy: true }, language));
      }
    }
    for (const html of pages) {
      for (const m of html.matchAll(/\s(?:title|placeholder)="([^"]*)"/gi)) {
        expect(allowed.has(unescapeHtml(m[1] ?? "")), m[0]).toBe(true);
      }
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
