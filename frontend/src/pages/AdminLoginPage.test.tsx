import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { readAdminSession } from "../api/admin";
import { CSRF_HEADER } from "../api/pay";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider, ROUTE_PATHS } from "../router";
import {
  AdminLoginView,
  beginLogIn,
  canLogIn,
  DARK_SCHEME_QUERY,
  EMPTY_LOGIN,
  INITIAL_STATE,
  LOGIN_ERROR,
  logIn,
  logOut,
  sessionState,
  watchColorScheme,
} from "./AdminLoginPage";
import type { AdminLoginViewProps, AdminState, ColorMode, SchemeQuery, SignedIn } from "./AdminLoginPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ADMIN_PATH = "/admin/login";
const USERNAME = "shop-admin";
const PASSWORD = "s3cret-Passw0rd!";
const TOKEN = "admin-csrf-1";
const FILLED = { username: USERNAME, password: PASSWORD };
const SESSION = { username: USERNAME, expires_at: "2026-11-05T08:00:00Z", csrf_token: TOKEN };
const SIGNED_IN: SignedIn = { status: "in", username: USERNAME, csrfToken: TOKEN };
const FORM_STATE: AdminState = { screen: { status: "form" }, error: null };
const LANGUAGE_LABEL = { en: "common.lang_en", zh: "common.lang_zh", ms: "common.lang_ms" } as const;
const HTML_LANG = { en: "en", zh: "zh-Hans", ms: "ms" } as const;

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

function props(overrides: Partial<AdminLoginViewProps> = {}): AdminLoginViewProps {
  return { mode: "light", state: FORM_STATE, fields: EMPTY_LOGIN, busy: false, onChange: noop, onLogIn: noop, onLogOut: noop, ...overrides };
}

function wrap(element: ReactElement, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={ADMIN_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminLoginViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminLoginView {...props(overrides)} />, language));
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

// 开始标签的属性：属性名不分大小写（react-dom/server 输出 autoComplete、noValidate 这类驼峰名），与顺序无关；没有值的属性为空串。
function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

// 页面根元素的开始标签。
function rootTag(html: string): string {
  return html.slice(0, html.indexOf(">") + 1);
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/).filter((name) => name !== "");
}

// 某个元素（按 class 找到的第一个）从开始标签到对应结束标签的内容；不处理同名元素嵌套，本页用到的几处都没有嵌套。
function element(html: string, name: string, className: string): string {
  for (const tag of tags(html, name)) {
    if (classes(tag).includes(className)) {
      const start = html.indexOf(tag);
      const end = html.indexOf(`</${name}>`, start);
      return html.slice(start, end + `</${name}>`.length);
    }
  }
  throw new Error(`not found: <${name} class="${className}">`);
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

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
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
  // SHOP-TASK-038 验收第 3 条「首次渲染（会话请求未返回、没有本浏览器存储）即显示演示横幅（common.demo_badge 与 admin.demo_banner）、admin.login_title 与语言切换」，
  // 「会话请求返回前不显示表单与已登录内容」，以及 UX A01 桌面线框「[common.lang_en]|[common.lang_zh]|[common.lang_ms]」：
  // 经路由打开 /admin/login（服务端渲染，没有语言存储即英文），横幅、标题与三种语言的站内链接都在，链接指向本页、当前语言 aria-current 为 true；
  // 主体标 aria-busy，没有表单、输入框或已登录内容。
  it("shows the banner, the title and the language links before the session returns", () => {
    const html = renderToStaticMarkup(<App initialPath={ADMIN_PATH} storage={null} />);
    const banner = element(html, "div", "acs-admin__banner");
    expect(banner).toContain(`<span class="acs-tag acs-tag--demo">${COPY["common.demo_badge"].en}</span>`);
    expect(banner).toContain(COPY["admin.demo_banner"].en);
    expect(element(html, "h1", "acs-admin__h")).toContain(COPY["admin.login_title"].en);
    const links = element(html, "nav", "site-admin-login__lang-links");
    expect(classes(tags(links, "nav")[0] ?? "")).toContain("acs-admin__lang");
    const anchors = tags(links, "a").map(attributes);
    expect(anchors.map((a) => a.get("href"))).toEqual([ADMIN_PATH, ADMIN_PATH, ADMIN_PATH]);
    expect(anchors.map((a) => a.get("aria-current"))).toEqual(["true", undefined, undefined]);
    for (const language of LANGUAGES) {
      expect(links).toContain(`>${COPY[LANGUAGE_LABEL[language]].en}</a>`);
    }
    const main = tags(html, "main")[0] ?? "";
    expect(attributes(main).get("aria-busy")).toBe("true");
    expect(html).not.toMatch(/<(form|input)\b/);
    expect(html).not.toContain(COPY["admin.logout"].en);
    expect(html).not.toContain("site-admin-login__body");
    expect(html).not.toContain(`role="alert"`);
  });

  // 同一条「手机为显示当前语言名称的按钮、展开后列出三种语言…可访问名称即按钮上的语言名，不另加文案，按钮用 acs-admin__btn acs-admin__btn--secondary，
  // 展开的语言列表沿用 acs-admin__lang…两者都渲染」与 HANDOFF Kelvin 2026-10-05「A01 手机版的语言切换照前台页头手机版的当前语言按钮做」：
  // 每种语言下首次渲染都有显示当前语言名称的按钮（没有 aria-label，名称就是按钮文字），收起的列表里三种语言都指向本页。
  it.each(LANGUAGES)("renders the phone current-language button in %s", (language) => {
    const html = renderApp(ADMIN_PATH, language);
    const button = element(html, "button", "site-admin-login__lang-button");
    const buttonTag = tags(button, "button")[0] ?? "";
    expect(classes(buttonTag)).toContain("acs-admin__btn");
    expect(classes(buttonTag)).toContain("acs-admin__btn--secondary");
    expect(attributes(buttonTag).get("type")).toBe("button");
    expect(attributes(buttonTag).get("aria-expanded")).toBe("false");
    expect(attributes(buttonTag).has("aria-label")).toBe(false);
    expect(button).toContain(`<span>${COPY[LANGUAGE_LABEL[language]][language]}</span>`);
    const menuId = attributes(buttonTag).get("aria-controls") ?? "";
    const menuTag = tags(html, "nav").find((tag) => attributes(tag).get("id") === menuId) ?? "";
    expect(classes(menuTag)).toContain("acs-admin__lang");
    expect(attributes(menuTag).has("hidden")).toBe(true);
    const menu = element(html, "nav", "site-admin-login__lang-menu");
    const anchors = tags(menu, "a").map(attributes);
    expect(anchors.map((a) => a.get("href"))).toEqual([ADMIN_PATH, ADMIN_PATH, ADMIN_PATH]);
    expect(anchors.map((a) => a.get("lang"))).toEqual(LANGUAGES.map((option) => HTML_LANG[option]));
    expect(anchors.filter((a) => a.get("aria-current") === "true").map((a) => a.get("lang"))).toEqual([HTML_LANG[language]]);
    // 桌面链接同样标出当前语言。
    const desktop = tags(element(html, "nav", "site-admin-login__lang-links"), "a").map(attributes);
    expect(desktop.filter((a) => a.get("aria-current") === "true").map((a) => a.get("lang"))).toEqual([HTML_LANG[language]]);
    expect(element(html, "h1", "acs-admin__h")).toContain(escapeHtml(COPY["admin.login_title"][language]));
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
  });

  // SHOP-TASK-038 验收第 2 条「App.tsx 对以 /admin 开头的路由不套 SiteFrame，直接渲染页面」「页面根元素为 class acs-admin，data-mode 首次渲染为 light」
  // 与任务目的「后台页面不套前台的站点框架」：/admin/login 没有前台的横幅、页头、页脚与品牌字样，根元素就是 acs-admin、data-mode 为 light，没有行内样式。
  it("renders /admin/login without the storefront frame", () => {
    for (const language of LANGUAGES) {
      const html = renderApp(ADMIN_PATH, language);
      const root = attributes(rootTag(html));
      expect(html.startsWith("<div")).toBe(true);
      expect((root.get("class") ?? "").split(" ")).toContain("acs-admin");
      expect(root.get("data-mode")).toBe("light");
      expect(root.has("data-shop-theme")).toBe(false);
      expect(html).not.toMatch(/<(header|footer)\b/);
      expect(html).not.toContain(`class="acs-banner"`);
      expect(html).not.toContain(`class="acs site"`);
      expect(html).not.toContain(COPY["common.demo_banner"][language]);
      expect(html).not.toContain(COPY["common.footer_demo"][language]);
      expect(html).not.toContain(BRAND);
      expect(html).not.toContain("style=");
    }
  });

  // SHOP-TASK-038 验收第 2 条「前台页头、菜单与页脚不出现任何指向 /admin 的链接」与 UX A01「入口：直接访问后台路径（前台不放入口链接）」：
  // 路由表里每个前台页面、每种语言都没有 href 以 /admin 开头的链接，也没有后台文案。
  it.each(ROUTE_PATHS.filter((path) => !path.startsWith("/admin")))("has no link to /admin on %s", (path) => {
    for (const language of LANGUAGES) {
      const html = renderApp(path, language);
      expect(html).not.toMatch(/href="\/admin/);
      expect(html).not.toContain(escapeHtml(COPY["admin.login_title"][language]));
    }
  });

  // SHOP-TASK-038 验收第 2 条「data-mode 首次渲染为 light（服务端渲染与浏览器里的首次客户端渲染都是 light，挂载后才读设备设置）」：
  // 初始页面状态为读取中，展示部分按给定的 mode 原样写 data-mode。
  it("starts light and loading", () => {
    expect(INITIAL_STATE).toEqual({ screen: { status: "loading" }, error: null });
    expect(attributes(rootTag(render({ state: INITIAL_STATE }))).get("data-mode")).toBe("light");
  });
});

describe("colour mode", () => {
  // docs/UX.md「管理后台总体」「深浅色随管理员设备设置」与 SHOP-TASK-038 验收第 2 条「在浏览器里按 prefers-color-scheme 设为 light 或 dark 并随设备设置变化」：
  // 挂载后读一次设备设置（dark 报告 dark），设置变化时再报告；取消订阅后不再报告；页面按报告的值写 data-mode。
  it("follows prefers-color-scheme and its changes", () => {
    const media = fakeMatchMedia(true);
    const modes: ColorMode[] = [];
    const stop = watchColorScheme(media.matchMedia, (mode) => modes.push(mode));
    expect(media.queries).toEqual([DARK_SCHEME_QUERY]);
    expect(DARK_SCHEME_QUERY).toBe("(prefers-color-scheme: dark)");
    expect(modes).toEqual(["dark"]);
    media.change(false);
    media.change(true);
    expect(modes).toEqual(["dark", "light", "dark"]);
    stop();
    expect(media.listeners.size).toBe(0);
    media.change(false);
    expect(modes).toEqual(["dark", "light", "dark"]);

    const light = fakeMatchMedia(false);
    const lightModes: ColorMode[] = [];
    watchColorScheme(light.matchMedia, (mode) => lightModes.push(mode));
    expect(lightModes).toEqual(["light"]);

    for (const mode of ["light", "dark"] as const) {
      expect(attributes(rootTag(render({ mode }))).get("data-mode")).toBe(mode);
    }
  });

  // 派生实现约束（实现选择）：守住同一条「首次渲染为 light」——浏览器没有 matchMedia 时保持浅色，不报告。
  it("stays light without matchMedia", () => {
    const report = vi.fn();
    const stop = watchColorScheme(null, report);
    stop();
    expect(report).not.toHaveBeenCalled();
  });
});

describe("login form", () => {
  // SHOP-TASK-038 验收第 4 条「登录表单：admin.username、auth.password（autocomplete 分别为 username 与 current-password）与 auth.login_submit」
  // 与 UX A01 线框的顺序：两个带标签的输入框（acs-admin__field）、提交按钮；输入框不设 name，表单 method 为 post，不用脚本时也提交不出用户名或密码。
  it.each(LANGUAGES)("has the username and password fields and the submit button in %s", (language) => {
    const html = render({ fields: FILLED }, language);
    const form = tags(html, "form")[0] ?? "";
    expect(attributes(form).get("method")).toBe("post");
    expect(attributes(form).has("action")).toBe(false);
    const labels = tags(html, "label");
    expect(labels).toHaveLength(2);
    for (const label of labels) {
      expect(classes(label)).toContain("acs-admin__field");
    }
    const [username, password] = tags(html, "input").map(attributes);
    expect(tags(html, "input")).toHaveLength(2);
    expect(username?.get("autocomplete")).toBe("username");
    expect(username?.get("class")).toBe("acs-admin__input");
    expect(username?.get("type") ?? "text").toBe("text");
    expect(password?.get("autocomplete")).toBe("current-password");
    expect(password?.get("type")).toBe("password");
    expect(password?.get("class")).toBe("acs-admin__input");
    for (const input of [username, password]) {
      expect(input?.has("name")).toBe(false);
    }
    const positions = [
      html.indexOf(`>${escapeHtml(COPY["admin.username"][language])}<input`),
      html.indexOf(`>${escapeHtml(COPY["auth.password"][language])}<input`),
      html.indexOf(`>${escapeHtml(COPY["auth.login_submit"][language])}</button>`),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThan(html.indexOf(escapeHtml(COPY["admin.login_title"][language])));
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    const submit = tags(html, "button").find((tag) => attributes(tag).get("type") === "submit") ?? "";
    expect(classes(submit)).toContain("acs-admin__btn");
    expect(classes(submit)).not.toContain("acs-admin__btn--secondary");
  });

  // SHOP-TASK-038 验收第 4 条「用户名或密码任一为空时提交按钮禁用，不用浏览器自带的必填校验提示（那不是 UX-COPY 文字）」与「提交期间按钮禁用」：
  // 任一为空或提交中时按钮带 disabled；两项都有值时可提交；输入框不标 required、不设格式约束，表单标 novalidate。
  it("disables the submit button until both fields are filled and while submitting", () => {
    const submit = (fields: typeof FILLED, busy = false) =>
      attributes(tags(render({ fields, busy }), "button").find((tag) => attributes(tag).get("type") === "submit") ?? "");
    expect(submit(EMPTY_LOGIN).has("disabled")).toBe(true);
    expect(submit({ username: USERNAME, password: "" }).has("disabled")).toBe(true);
    expect(submit({ username: "", password: PASSWORD }).has("disabled")).toBe(true);
    expect(submit(FILLED).has("disabled")).toBe(false);
    expect(submit(FILLED, true).has("disabled")).toBe(true);
    expect(canLogIn(FILLED, false)).toBe(true);
    expect(canLogIn({ username: " ", password: " " }, false)).toBe(true);
    expect(canLogIn(FILLED, true)).toBe(false);
    expect(canLogIn(EMPTY_LOGIN, false)).toBe(false);
    const html = render();
    expect(attributes(tags(html, "form")[0] ?? "").has("novalidate")).toBe(true);
    for (const input of tags(html, "input").map(attributes)) {
      for (const name of ["required", "pattern", "minlength", "maxlength"]) {
        expect(input.has(name), name).toBe(false);
      }
    }
  });

  // SHOP-TASK-038 验收第 4 条各提示与 UX A01 线框「( [auth.login_submit] ) 错误：[admin.login_failed] / [admin.locked]」（手机「<错误>」也在按钮之后）：
  // 每种提示都以 acs-admin__alert（role="alert"）显示在提交按钮之后；没有提示时没有提示条。
  it.each(LANGUAGES)("shows each message after the submit button in %s", (language) => {
    for (const key of Object.values(LOGIN_ERROR)) {
      const html = render({ state: { screen: { status: "form" }, error: key }, fields: { username: USERNAME, password: "" } }, language);
      const alert = element(html, "div", "acs-admin__alert");
      expect(attributes(tags(alert, "div")[0] ?? "").get("role")).toBe("alert");
      expect(alert).toContain(`<span>${escapeHtml(COPY[key][language])}</span>`);
      expect(html.indexOf(alert)).toBeGreaterThan(html.indexOf(`${escapeHtml(COPY["auth.login_submit"][language])}</button>`));
      // 提示里不出现用户名或密码（验收第 4 条）。
      expect(alert).not.toContain(USERNAME);
      expect(alert).not.toContain(PASSWORD);
    }
    expect(render({}, language)).not.toContain(`role="alert"`);
  });
});

describe("session", () => {
  // SHOP-TASK-038 验收第 3 条「200 显示已登录状态，401 显示登录表单，其他结果显示 common.error_retry 与登录表单」。
  it("chooses the panel from the session reply", () => {
    expect(sessionState({ kind: "ok", session: SESSION })).toEqual({ screen: SIGNED_IN, error: null });
    expect(sessionState({ kind: "none" })).toEqual({ screen: { status: "form" }, error: null });
    expect(sessionState({ kind: "failed" })).toEqual({ screen: { status: "form" }, error: "common.error_retry" });
    expect(sessionState({ kind: "network" })).toEqual({ screen: { status: "form" }, error: "common.error_retry" });
    const html = render({ state: sessionState({ kind: "failed" }) });
    expect(tags(html, "input")).toHaveLength(2);
    expect(element(html, "div", "acs-admin__alert")).toContain(COPY["common.error_retry"].en);
  });

  // SHOP-TASK-038 验收第 5 条与 HANDOFF Kelvin 2026-10-05 过渡规则「面板保留标题与语言切换，其下显示 admin.logged_in 与次要样式的 admin.logout」：
  // 已登录时标题、两种语言切换照旧，admin.logged_in 的 {username} 为会话接口返回的用户名，admin.logout 为 acs-admin__btn acs-admin__btn--secondary，没有表单与输入框。
  it.each(LANGUAGES)("shows the logged-in state with the username and the logout button in %s", (language) => {
    const html = render({ state: { screen: SIGNED_IN, error: null } }, language);
    expect(element(html, "h1", "acs-admin__h")).toContain(escapeHtml(COPY["admin.login_title"][language]));
    expect(html).toContain("site-admin-login__lang-links");
    expect(html).toContain("site-admin-login__lang-button");
    const text = escapeHtml(formatCopy(COPY["admin.logged_in"][language], { username: USERNAME }));
    expect(html).toContain(`<p>${text}</p>`);
    const logoutButton = (source: string) => tags(source, "button").find((tag) => classes(tag).includes("site-admin-login__logout")) ?? "";
    const logout = logoutButton(html);
    expect(classes(logout)).toContain("acs-admin__btn");
    expect(classes(logout)).toContain("acs-admin__btn--secondary");
    expect(attributes(logout).get("type")).toBe("button");
    expect(attributes(logout).has("disabled")).toBe(false);
    expect(html.indexOf(logout)).toBeGreaterThan(html.indexOf(text));
    expect(html).toContain(`>${escapeHtml(COPY["admin.logout"][language])}</button>`);
    expect(html).not.toMatch(/<(form|input)\b/);
    expect(html).not.toContain(escapeHtml(COPY["auth.login_submit"][language]) + "</button>");
    expect(attributes(tags(html, "main")[0] ?? "").get("aria-busy")).toBe("false");
    // 退出请求进行中按钮禁用（派生实现约束：不重复提交）。
    expect(attributes(logoutButton(render({ state: { screen: SIGNED_IN, error: null }, busy: true }, language))).has("disabled")).toBe(true);
  });
});

describe("logging in", () => {
  // SHOP-TASK-038 验收第 4 条「JSON 请求体只有用户名与密码…204 后再取会话并显示已登录状态」与「每次提交后清空密码框、保留用户名」：
  // 提交时先把密码框清空、保留用户名，请求体恰好是提交时的用户名与密码；204 之后紧接着 GET 会话，结果为已登录状态与用户名。
  it("clears the password, posts both fields and reads the session after 204", async () => {
    const calls = stubFetch(empty(204), json(200, SESSION));
    const setFields = vi.fn();
    const outcome = await beginLogIn(FILLED, setFields);
    expect(setFields.mock.calls).toEqual([[{ username: USERNAME, password: "" }]]);
    expect(calls.map((call) => `${String(call.init.method)} ${call.url}`)).toEqual(["POST /api/admin/login", "GET /api/admin/session"]);
    expect(body(calls[0])).toEqual({ username: USERNAME, password: PASSWORD });
    expect(outcome).toEqual({ screen: SIGNED_IN, error: null });
    // 页面按清空后的值重新渲染：密码框为空、用户名保留。
    const html = render({ fields: { username: USERNAME, password: "" } });
    const [username, password] = tags(html, "input").map(attributes);
    expect(username?.get("value")).toBe(USERNAME);
    expect(password?.get("value")).toBe("");
  });

  // SHOP-TASK-038 验收第 4 条「401 显示 admin.login_failed，429 显示 admin.locked，503 显示 common.service_unavailable，网络中断显示 common.network_check，
  // 其他显示 common.error_retry」与「每次提交后清空密码框」：每种失败留在表单并显示对应提示，不读取会话；失败时同样先清空密码框。
  it.each<[Response | (() => never), CopyKey]>([
    [json(401, { detail: "login_failed" }), "admin.login_failed"],
    [json(429, { detail: "login_locked" }), "admin.locked"],
    [json(503, { detail: "service_unavailable" }), "common.service_unavailable"],
    [networkDown, "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(422, { detail: [] }), "common.error_retry"],
  ])("stays on the form with the right message (%#)", async (reply, key) => {
    const calls = stubFetch(reply);
    const setFields = vi.fn();
    await expect(beginLogIn(FILLED, setFields)).resolves.toEqual({ screen: { status: "form" }, error: key });
    expect(setFields.mock.calls).toEqual([[{ username: USERNAME, password: "" }]]);
    expect(calls).toHaveLength(1);
  });

  // 派生实现约束（实现选择）：守住第 4 条「204 后再取会话并显示已登录状态」——登录 204 之后会话取不到（401、其他失败或网络中断）时不显示已登录，
  // 留在表单并显示 common.error_retry。
  it.each([json(401, { detail: "admin_session_required" }), json(500, {}), networkDown])("stays on the form when the session cannot be read after 204 (%#)", async (reply) => {
    stubFetch(empty(204), reply);
    await expect(logIn(FILLED)).resolves.toEqual({ screen: { status: "form" }, error: "common.error_retry" });
  });
});

describe("logging out", () => {
  // SHOP-TASK-038 验收第 5 条「退出调用 POST /api/admin/logout 并带会话接口返回的 X-CSRF-Token，204 或 401 后回到登录表单」：
  // 请求头带当前会话的令牌；204 与 401 都回到表单，不显示提示，也不再读取会话。
  it.each([empty(204), json(401, { detail: "admin_session_required" })])("goes back to the form after the reply %#", async (reply) => {
    const calls = stubFetch(reply);
    await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: { status: "form" }, error: null });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/logout");
    expect(calls[0]?.init.method).toBe("POST");
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
  });

  // SHOP-TASK-038 验收第 5 条「403 时重新取会话并显示 common.error_retry」：POST 之后紧接着 GET 会话，按新会话（新令牌）保持已登录并提示重试；
  // 重新取会话得到 401 时回到表单并提示重试，其他失败时保持原来的已登录状态并提示重试。
  it("reads the session again after 403", async () => {
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, { ...SESSION, csrf_token: "admin-csrf-2" }));
    await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: { ...SIGNED_IN, csrfToken: "admin-csrf-2" }, error: "common.error_retry" });
    expect(calls.map((call) => `${String(call.init.method)} ${call.url}`)).toEqual(["POST /api/admin/logout", "GET /api/admin/session"]);
    stubFetch(json(403, { detail: "csrf_failed" }), json(401, { detail: "admin_session_required" }));
    await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: { status: "form" }, error: "common.error_retry" });
    stubFetch(json(403, { detail: "csrf_failed" }), json(500, {}));
    await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: SIGNED_IN, error: "common.error_retry" });
  });

  // SHOP-TASK-038 验收第 5 条「网络中断显示 common.network_check，其他结果显示 common.error_retry 并保持已登录状态」：两种情况都不读取会话，令牌不变；
  // 提示在退出按钮之后。
  it("keeps the logged-in state on other failures", async () => {
    stubFetch(networkDown);
    await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: SIGNED_IN, error: "common.network_check" });
    for (const reply of [json(500, {}), json(200, {}), json(503, {})]) {
      const calls = stubFetch(reply);
      await expect(logOut(SIGNED_IN)).resolves.toEqual({ screen: SIGNED_IN, error: "common.error_retry" });
      expect(calls).toHaveLength(1);
    }
    const html = render({ state: { screen: SIGNED_IN, error: "common.network_check" } });
    expect(html.indexOf(element(html, "div", "acs-admin__alert"))).toBeGreaterThan(html.indexOf(`${COPY["admin.logout"].en}</button>`));
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-038 验收第 4 条「用户名与密码不写进网址、localStorage、sessionStorage 或 cookie，也不出现在任何错误提示里」与任务目的「密码只在页面内存里，提交后清空」：
  // 读取会话、各种登录回答（含网络中断）、登录成功与退出（含 403 后重读）的整个过程不写任何浏览器存储、cookie 或历史记录；
  // 请求地址固定，不含用户名、密码或令牌；各状态下的错误提示不含用户名或密码。
  it("keeps the username, password and token out of addresses and browser storage", async () => {
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

    const calls = stubFetch(
      json(401, { detail: "admin_session_required" }),
      json(401, { detail: "login_failed" }),
      json(429, { detail: "login_locked" }),
      json(503, { detail: "service_unavailable" }),
      networkDown,
      json(500, {}),
      empty(204),
      json(200, SESSION),
      json(403, { detail: "csrf_failed" }),
      json(200, SESSION),
      empty(204),
    );
    const states: AdminState[] = [];
    states.push(sessionState(await readAdminSession()));
    for (let attempt = 0; attempt < 5; attempt += 1) {
      states.push(await beginLogIn(FILLED, noop));
    }
    const signedInState = await beginLogIn(FILLED, noop);
    states.push(signedInState);
    if (signedInState.screen.status !== "in") {
      throw new Error("expected to be logged in");
    }
    states.push(await logOut(signedInState.screen));
    states.push(await logOut(SIGNED_IN));

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls).toHaveLength(11);
    for (const call of calls) {
      expect(call.url).toMatch(/^\/api\/admin\/(session|login|logout)$/);
      expect(call.url).not.toContain(USERNAME);
      expect(call.url).not.toContain(TOKEN);
    }
    for (const state of states) {
      for (const language of LANGUAGES) {
        const html = render({ state, fields: { username: USERNAME, password: "" } }, language);
        expect(html).not.toContain(PASSWORD);
        if (state.error !== null) {
          const alert = element(html, "div", "acs-admin__alert");
          expect(alert).not.toContain(USERNAME);
          expect(alert).not.toContain(PASSWORD);
        }
      }
    }
  });
});

describe("dictionary", () => {
  // SHOP-TASK-038 验收第 7 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 读取中、登录表单（含提交中与每种提示）、已登录（含每种提示）各状态下，每段文字都是当前语言的某条字典文案，
  // admin.logged_in 只把 {username} 换成会话接口给的用户名；手机语言按钮的可访问名称就是语言名，没有另加的 aria-label 或 title。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>([
      ...Object.values(COPY).map((entry) => entry[language]),
      formatCopy(COPY["admin.logged_in"][language], { username: USERNAME }),
    ]);
    const errors: (CopyKey | null)[] = [null, ...Object.values(LOGIN_ERROR)];
    const states: Partial<AdminLoginViewProps>[] = [
      { state: INITIAL_STATE },
      { fields: FILLED, busy: true },
      ...errors.map((error) => ({ state: { screen: { status: "form" as const }, error }, fields: FILLED })),
      ...errors.map((error) => ({ state: { screen: SIGNED_IN, error } })),
    ];
    for (const state of states) {
      const html = render(state, language);
      expect(html).not.toMatch(/\s(aria-label|title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
