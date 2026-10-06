import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminSession } from "../api/admin";
import { CSRF_HEADER } from "../api/pay";
import App from "../App";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider, ROUTE_PATHS } from "../router";
import {
  ADMIN_LOGIN_PATH,
  adminMode,
  AdminLoginView,
  afterSubmit,
  canSubmitLogin,
  DARK_SCHEME_QUERY,
  EMPTY_LOGIN,
  LOGIN_ERROR,
  loginAndRead,
  logoutAndSettle,
  screenForSession,
  watchAdminMode,
} from "./AdminLoginPage";
import type { AdminLoginViewProps, AdminScreen, LoginFields, MatchMedia } from "./AdminLoginPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const USERNAME = "kelvin-admin";
const PASSWORD = "s3cret-Passw0rd!";
const TOKEN = "admin-csrf-token-1";
const SESSION: AdminSession = { username: USERNAME, expires_at: "2026-11-05T10:00:00Z", csrf_token: TOKEN };
const FILLED: LoginFields = { username: USERNAME, password: PASSWORD };
const LANGUAGE_LABEL = { en: "common.lang_en", zh: "common.lang_zh", ms: "common.lang_ms" } as const;

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<AdminLoginViewProps> = {}): AdminLoginViewProps {
  return {
    mode: "light",
    screen: { kind: "form", error: null },
    fields: EMPTY_LOGIN,
    busy: false,
    onChange: noop,
    onSubmit: noop,
    onLogout: noop,
    ...overrides,
  };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={ADMIN_LOGIN_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminLoginViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminLoginView {...props(overrides)} />, language));
}

// 经路由打开（首次渲染：会话请求未返回）；storage 为 null 即没有本浏览器存储。
function renderApp(path: string, language: Language | null = null): string {
  return renderToStaticMarkup(<App initialPath={path} storage={language === null ? null : languageStorage(language)} />);
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) => unescapeHtml(m[1] ?? ""));
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...textNodes, ...attributes].filter((value) => value !== "");
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

function inputs(html: string): string[] {
  return [...html.matchAll(/<input\b[^>]*>/g)].map((m) => m[0]);
}

const submitButton = (html: string) => part(html, /<button class="acs-admin__btn site-admin__submit"[^>]*>/);
const alertBox = (html: string) => part(html, /<div class="acs-admin__alert" role="alert">[\s\S]*?<\/div>/);

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

// fetch 替身：按顺序给出回答（"network" 则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | "network")[]) {
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
      return next === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

describe("first render", () => {
  // SHOP-TASK-038 验收第 3 条「首次渲染（会话请求未返回、没有本浏览器存储）即显示演示横幅（common.demo_badge 与 admin.demo_banner）、admin.login_title 与语言切换」
  // 与 UX「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：没有存储时为默认英文；横幅在最前，标题在其后。
  it("shows the banner, the title and the language switch before the session returns", () => {
    const html = renderApp(ADMIN_LOGIN_PATH);
    const banner = part(html, /<div class="acs-admin__banner">[\s\S]*?<\/div>/);
    expect(banner).toBe(`<div class="acs-admin__banner"><span class="acs-tag acs-tag--demo">${copy("common.demo_badge")}</span><span>${copy("admin.demo_banner")}</span></div>`);
    expect(html).toContain(`<h1 class="acs-admin__h">${copy("admin.login_title")}</h1>`);
    expect(html.indexOf("acs-admin__banner")).toBeLessThan(html.indexOf("<h1"));
    expect(html).toMatch(/<nav class="acs-admin__lang site-desktop-only">/);
    expect(html).toMatch(/<button class="acs-admin__btn acs-admin__btn--secondary site-phone-only site-admin__lang-button"/);
  });

  // SHOP-TASK-038 验收第 3 条「会话请求返回前不显示表单与已登录内容」：只有横幅、标题与语言切换，主体标 aria-busy。
  it("shows neither the form nor the signed-in state before the session returns", () => {
    const html = renderApp(ADMIN_LOGIN_PATH);
    expect(html).not.toMatch(/<(form|input)\b/);
    expect(html).not.toContain(copy("auth.login_submit"));
    expect(html).not.toContain(copy("admin.logout"));
    expect(html).not.toContain(escapeHtml(COPY["admin.logged_in"].en.split("{")[0] ?? ""));
    expect(html).toContain(`<main class="site-admin__main" aria-busy="true">`);
    expect(render({ screen: { kind: "loading" } })).not.toMatch(/<(form|input)\b/);
  });

  // SHOP-TASK-038 验收第 2 条「页面根元素为 class acs-admin，data-mode 首次渲染为 light…沿用 LanguageProvider 的界面语言（默认英文）」：
  // 首次渲染的根元素；保存了其他语言时按它显示。
  it.each(LANGUAGES)("renders the acs-admin root in light mode in %s", (language) => {
    const html = renderApp(ADMIN_LOGIN_PATH, language);
    expect(html).toMatch(/^<div class="acs-admin site-admin" data-mode="light">/);
    expect(html).toContain(copy("admin.demo_banner", language));
    expect(html).toContain(copy("admin.login_title", language));
    expect(renderApp(ADMIN_LOGIN_PATH)).toContain(`lang="en" aria-current="true"`);
  });

  // SHOP-TASK-038 验收第 2 条「App.tsx 对以 /admin 开头的路由不套 SiteFrame，直接渲染页面」与 Kelvin 2026-10-05 过渡规则「后台框架（导航、☰ 菜单）留给 A02 页面任务」：
  // 没有前台根元素、横幅、页头、菜单与页脚，也没有后台导航。
  it("renders no storefront frame and no admin navigation", () => {
    const html = renderApp(ADMIN_LOGIN_PATH);
    expect(html).not.toMatch(/<(header|footer)\b/);
    expect(html).not.toContain("acs-banner");
    expect(html).not.toContain(`class="acs site"`);
    expect(html).not.toContain(copy("common.demo_banner"));
    expect(html).not.toContain(BRAND);
    expect(html).not.toContain("acs-admin__nav");
    expect(html).not.toContain(copy("common.nav_menu"));
  });

  // UX A01「入口：直接访问后台路径（前台不放入口链接）」与 SHOP-TASK-038 验收第 2 条「前台页头、菜单与页脚不出现任何指向 /admin 的链接」：
  // 每个前台页面、每种语言都没有指向 /admin 的链接。
  it("is not linked from any storefront page", () => {
    for (const path of ROUTE_PATHS.filter((candidate) => !candidate.startsWith("/admin"))) {
      for (const language of LANGUAGES) {
        expect(renderApp(path, language), path).not.toContain(`href="/admin`);
      }
    }
  });
});

describe("language switch", () => {
  // UX A01 桌面线框「[common.lang_en]|[common.lang_zh]|[common.lang_ms]」与 SHOP-TASK-038 验收第 3 条「桌面为三种语言的站内链接，链接指向本页」
  // 与验收第 1 条「桌面语言链接用 acs-admin__lang（当前语言 aria-current 为 true）」：网址里不带语言。
  it.each(LANGUAGES)("links all three languages to this page and marks %s as current", (language) => {
    const nav = part(render({}, language), /<nav class="acs-admin__lang site-desktop-only">[\s\S]*?<\/nav>/);
    for (const option of LANGUAGES) {
      expect(nav).toContain(`>${copy(LANGUAGE_LABEL[option], language)}</a>`);
    }
    const hrefs = [...nav.matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
    expect(hrefs).toEqual([ADMIN_LOGIN_PATH, ADMIN_LOGIN_PATH, ADMIN_LOGIN_PATH]);
    const current = [...nav.matchAll(/lang="([^"]+)" aria-current="true"/g)].map((m) => m[1]);
    expect(current).toEqual([language === "zh" ? "zh-Hans" : language]);
  });

  // UX A01 手机线框「[admin.login_title] <当前语言>▾」与 Kelvin 2026-10-05「A01 手机版的语言切换照前台页头手机版的当前语言按钮做…不另加文案」、
  // 验收第 3 条「按钮用 acs-admin__btn acs-admin__btn--secondary，展开的语言列表沿用 acs-admin__lang」：按钮上只有当前语言名（即其可访问名称，没有 aria-label），
  // 收起时 aria-expanded 为 false，控制的列表默认隐藏、列出三种语言并链到本页。
  it.each(LANGUAGES)("has the phone current-language button and its hidden list in %s", (language) => {
    const html = render({}, language);
    const button = part(html, /<button class="acs-admin__btn acs-admin__btn--secondary site-phone-only site-admin__lang-button"[\s\S]*?<\/button>/);
    expect(button).toContain(`<span>${copy(LANGUAGE_LABEL[language], language)}</span><svg`);
    expect(button).toContain(`aria-expanded="false"`);
    expect(button).not.toContain("aria-label");
    const controls = escapeRegExp(/aria-controls="([^"]+)"/.exec(button)?.[1] ?? "");
    const list = part(html, new RegExp(`<div id="${controls}" class="site-phone-only site-admin__lang-panel" hidden="">[\\s\\S]*?</div>`));
    expect(list).toContain(`<nav class="acs-admin__lang">`);
    expect([...list.matchAll(/href="([^"]*)"/g)]).toHaveLength(3);
    for (const option of LANGUAGES) {
      expect(list).toContain(`href="${ADMIN_LOGIN_PATH}" lang="${option === "zh" ? "zh-Hans" : option}"`);
    }
  });
});

describe("login form", () => {
  // UX A01 线框「[admin.username] [____] / [auth.password] [____] / ( [auth.login_submit] ) / 错误」与 SHOP-TASK-038 验收第 4 条
  // 「admin.username、auth.password（autocomplete 分别为 username 与 current-password）与 auth.login_submit」与验收第 1 条「表单字段用 acs-admin__field」：
  // 两个字段在标题与语言切换之后，按线框顺序，各由 label 包住输入框。
  it.each(LANGUAGES)("shows the fields in wireframe order in %s", (language) => {
    const html = render({ screen: { kind: "form", error: "admin.login_failed" } }, language);
    const positions = [
      html.indexOf(copy("admin.login_title", language)),
      html.indexOf(`<label class="acs-admin__field">${copy("admin.username", language)}<input`),
      html.indexOf(`<label class="acs-admin__field">${copy("auth.password", language)}<input`),
      html.indexOf(`>${copy("auth.login_submit", language)}</button>`),
      html.indexOf(copy("admin.login_failed", language)),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    const [username, password] = inputs(html);
    expect(username).toContain(`autocomplete="username"`);
    expect(username).not.toContain("type=");
    expect(password).toContain(`type="password"`);
    expect(password).toContain(`autocomplete="current-password"`);
  });

  // SHOP-TASK-038 验收第 4 条「不用浏览器自带的必填校验提示（那不是 UX-COPY 文字）」与「用户名与密码不写进网址」：
  // 输入框不标 required、不设 name，表单 novalidate 且以 POST 提交（不用脚本时也不把二者放进查询参数）。
  it("uses no browser validation and never puts the fields into an address", () => {
    const html = render({ fields: FILLED });
    expect(html).toMatch(/<form class="site-admin__form" method="post" novalidate="">/);
    for (const field of inputs(html)) {
      expect(field).not.toMatch(/\s(required|name|pattern|minlength|maxlength)=/);
    }
    for (const href of html.matchAll(/(?:href|action)="([^"]*)"/g)) {
      expect(href[1]).not.toContain(USERNAME);
      expect(href[1]).not.toContain(PASSWORD);
    }
  });

  // SHOP-TASK-038 验收第 4 条「用户名或密码任一为空时提交按钮禁用…提交期间按钮禁用」。
  it("disables the submit button while a field is empty or a request is running", () => {
    expect(submitButton(render())).toContain(`disabled=""`);
    expect(submitButton(render({ fields: { username: USERNAME, password: "" } }))).toContain(`disabled=""`);
    expect(submitButton(render({ fields: { username: "", password: PASSWORD } }))).toContain(`disabled=""`);
    expect(submitButton(render({ fields: FILLED, busy: true }))).toContain(`disabled=""`);
    expect(submitButton(render({ fields: FILLED }))).not.toContain("disabled");
    expect(canSubmitLogin(FILLED, false)).toBe(true);
    expect(canSubmitLogin(EMPTY_LOGIN, false)).toBe(false);
    expect(canSubmitLogin({ username: USERNAME, password: "" }, false)).toBe(false);
    expect(canSubmitLogin({ username: "", password: PASSWORD }, false)).toBe(false);
    expect(canSubmitLogin(FILLED, true)).toBe(false);
  });

  // SHOP-TASK-038 验收第 4 条「每次提交后清空密码框、保留用户名」：提交后的输入为空密码、原用户名，渲染出来密码框为空。
  it("clears the password and keeps the username after a submission", () => {
    const next = afterSubmit(FILLED);
    expect(next).toEqual({ username: USERNAME, password: "" });
    const [username, password] = inputs(render({ fields: next, screen: { kind: "form", error: "admin.login_failed" } }));
    expect(username).toContain(`value="${USERNAME}"`);
    expect(password).toContain(`value=""`);
  });
});

describe("session", () => {
  // SHOP-TASK-038 验收第 3 条「200 显示已登录状态，401 显示登录表单，其他结果显示 common.error_retry 与登录表单」。
  it("chooses the screen from the session reply", () => {
    expect(screenForSession({ kind: "ok", session: SESSION })).toEqual({ kind: "signed_in", session: SESSION, error: null });
    expect(screenForSession({ kind: "signed_out" })).toEqual({ kind: "form", error: null });
    expect(screenForSession({ kind: "failed" })).toEqual({ kind: "form", error: "common.error_retry" });
    const failed = render({ screen: screenForSession({ kind: "failed" }) });
    expect(failed).toContain("<form");
    expect(alertBox(failed)).toContain(copy("common.error_retry"));
  });

  // Kelvin 2026-10-05 过渡规则「登录成功后留在本页，面板改为显示 admin.logged_in（含用户名）与 admin.logout 按钮」与 SHOP-TASK-038 验收第 5 条
  // 「面板保留 admin.login_title 与语言切换，其下显示 admin.logged_in（{username} 为会话接口返回的用户名）与 admin.logout 按钮（acs-admin__btn acs-admin__btn--secondary），不显示表单」。
  it.each(LANGUAGES)("shows the signed-in state with the username in %s", (language) => {
    const html = render({ screen: { kind: "signed_in", session: SESSION, error: null } }, language);
    const text = escapeHtml(formatCopy(COPY["admin.logged_in"][language], { username: USERNAME }));
    expect(html).toContain(`<p class="site-admin__text">${text}</p>`);
    expect(html).toContain(`<button class="acs-admin__btn acs-admin__btn--secondary site-admin__logout" type="button">${copy("admin.logout", language)}</button>`);
    expect(html).not.toMatch(/<(form|input)\b/);
    expect(html).toContain(copy("admin.login_title", language));
    expect(html).toContain(`<nav class="acs-admin__lang site-desktop-only">`);
    expect(html.indexOf("site-admin__lang-panel")).toBeLessThan(html.indexOf(text));
    expect(html.indexOf(text)).toBeLessThan(html.indexOf(copy("admin.logout", language)));
    expect(html).not.toContain(`role="alert"`);
  });

  // SHOP-TASK-038 验收第 4 条「提交期间按钮禁用」同理用于退出：退出进行中时退出按钮禁用。
  it("disables the logout button while a request is running", () => {
    expect(render({ screen: { kind: "signed_in", session: SESSION, error: null }, busy: true })).toMatch(/site-admin__logout" type="button" disabled="">/);
  });
});

describe("logging in", () => {
  // SHOP-TASK-038 验收第 4 条「提交调用 POST /api/admin/login（JSON 请求体只有用户名与密码）…204 后再取会话并显示已登录状态」：
  // 请求依次为 POST 登录与 GET 会话，结果为已登录与会话接口给的用户名。
  it("reads the session after 204 and shows the signed-in state", async () => {
    const calls = stubFetch(empty(204), json(200, SESSION));
    await expect(loginAndRead(USERNAME, PASSWORD)).resolves.toEqual({ kind: "signed_in", session: SESSION, error: null });
    expect(calls.map((call) => `${call.init.method ?? ""} ${call.url}`)).toEqual(["POST /api/admin/login", "GET /api/admin/session"]);
    const sent: unknown = JSON.parse(calls[0]?.init.body as string);
    expect(sent).toEqual({ username: USERNAME, password: PASSWORD });
  });

  // 派生实现约束（实现选择）：守住验收第 3 条「其他结果显示 common.error_retry 与登录表单」——登录 204 后会话却取不到时留在表单并提示重试。
  it.each([json(401, { detail: "admin_session_required" }), json(500, {}), "network" as const])("stays on the form when the session cannot be read (%#)", async (reply) => {
    stubFetch(empty(204), reply);
    await expect(loginAndRead(USERNAME, PASSWORD)).resolves.toEqual({ kind: "form", error: "common.error_retry" });
  });

  // SHOP-TASK-038 验收第 4 条「401 显示 admin.login_failed，429 显示 admin.locked，503 显示 common.service_unavailable，网络中断显示 common.network_check，其他显示 common.error_retry」
  // 与 UX A01 说明「后台登录失败改用 [admin.login_failed]」：各自的文案，留在登录表单，不再取会话。
  it.each<[Response | "network", CopyKey]>([
    [json(401, { detail: "login_failed" }), "admin.login_failed"],
    [json(429, { detail: "login_locked" }), "admin.locked"],
    [json(503, { detail: "service_unavailable" }), "common.service_unavailable"],
    ["network", "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(422, { detail: [] }), "common.error_retry"],
  ])("shows the right message for each failure (%#)", async (reply, key) => {
    const calls = stubFetch(reply);
    await expect(loginAndRead(USERNAME, PASSWORD)).resolves.toEqual({ kind: "form", error: key });
    expect(calls).toHaveLength(1);
  });

  // 同一条与验收第 1 条「错误提示用 acs-admin__alert」、UX A01 线框错误在提交按钮之后：每种提示以 role="alert" 读出，
  // 且提示里没有用户名或密码（验收第 4 条「也不出现在任何错误提示里」）。
  it.each(LANGUAGES)("shows each message after the button without the credentials in %s", (language) => {
    for (const key of Object.values(LOGIN_ERROR)) {
      const html = render({ screen: { kind: "form", error: key }, fields: { username: USERNAME, password: "" } }, language);
      const alert = alertBox(html);
      expect(alert).toBe(`<div class="acs-admin__alert" role="alert"><span>${copy(key, language)}</span></div>`);
      expect(alert).not.toContain(USERNAME);
      expect(alert).not.toContain(PASSWORD);
      expect(html.indexOf(alert)).toBeGreaterThan(html.indexOf(copy("auth.login_submit", language)));
    }
    expect(render({}, language)).not.toContain(`role="alert"`);
  });
});

describe("logging out", () => {
  // SHOP-TASK-038 验收第 5 条「退出调用 POST /api/admin/logout 并带会话接口返回的 X-CSRF-Token，204 或 401 后回到登录表单」
  // 与 UX「管理后台总体」「退出后撤销当前后台会话、回到 A01」：请求头带令牌，结果为没有提示的登录表单。
  it.each([empty(204), json(401, { detail: "admin_session_required" })])("returns to the form after the reply %#", async (reply) => {
    const calls = stubFetch(reply);
    await expect(logoutAndSettle(SESSION)).resolves.toEqual({ kind: "form", error: null });
    expect(calls.map((call) => `${call.init.method ?? ""} ${call.url}`)).toEqual(["POST /api/admin/logout"]);
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
  });

  // SHOP-TASK-038 验收第 5 条「403 时重新取会话并显示 common.error_retry」：POST 之后紧接着 GET 会话；会话仍有效时保持已登录并换用新令牌（下次退出带新令牌），
  // 会话已不存在时回到表单，取不到时保持原来的已登录状态，三者都提示重试。
  it("reads the session again after 403", async () => {
    const fresh: AdminSession = { ...SESSION, csrf_token: "admin-csrf-token-2" };
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, fresh), empty(204));
    const outcome = await logoutAndSettle(SESSION);
    expect(outcome).toEqual({ kind: "signed_in", session: fresh, error: "common.error_retry" });
    expect(calls.map((call) => `${call.init.method ?? ""} ${call.url}`)).toEqual(["POST /api/admin/logout", "GET /api/admin/session"]);
    if (outcome.kind !== "signed_in") {
      throw new Error("expected the signed-in state");
    }
    await expect(logoutAndSettle(outcome.session)).resolves.toEqual({ kind: "form", error: null });
    expect(headers(calls[2])[CSRF_HEADER]).toBe("admin-csrf-token-2");

    stubFetch(json(403, { detail: "csrf_failed" }), json(401, { detail: "admin_session_required" }));
    await expect(logoutAndSettle(SESSION)).resolves.toEqual({ kind: "form", error: "common.error_retry" });
    stubFetch(json(403, { detail: "csrf_failed" }), "network");
    await expect(logoutAndSettle(SESSION)).resolves.toEqual({ kind: "signed_in", session: SESSION, error: "common.error_retry" });
  });

  // SHOP-TASK-038 验收第 5 条「网络中断显示 common.network_check，其他结果显示 common.error_retry 并保持已登录状态」：不再取会话。
  it.each<[Response | "network", CopyKey]>([
    ["network", "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(200, {}), "common.error_retry"],
  ])("stays signed in for other replies (%#)", async (reply, key) => {
    const calls = stubFetch(reply);
    await expect(logoutAndSettle(SESSION)).resolves.toEqual({ kind: "signed_in", session: SESSION, error: key });
    expect(calls).toHaveLength(1);
    const html = render({ screen: { kind: "signed_in", session: SESSION, error: key } });
    expect(alertBox(html)).toContain(copy(key));
    expect(html.indexOf(alertBox(html))).toBeGreaterThan(html.indexOf(copy("admin.logout")));
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-038 验收第 4 条「用户名与密码不写进网址、localStorage、sessionStorage 或 cookie」与目的「密码只在页面内存里，提交后清空」：
  // 登录（失败与成功）、取会话与退出（含 403 后重取）的整个过程不写任何浏览器存储、cookie 或历史记录，请求地址只是三个接口，不含用户名、密码或令牌。
  it("keeps the credentials and the token out of addresses and browser storage", async () => {
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
      json(401, { detail: "login_failed" }),
      empty(204),
      json(200, SESSION),
      json(403, { detail: "csrf_failed" }),
      json(200, SESSION),
      empty(204),
    );
    await loginAndRead(USERNAME, PASSWORD);
    const screen: AdminScreen = await loginAndRead(USERNAME, PASSWORD);
    if (screen.kind !== "signed_in") {
      throw new Error("expected the signed-in state");
    }
    const retried = await logoutAndSettle(screen.session);
    if (retried.kind !== "signed_in") {
      throw new Error("expected the signed-in state");
    }
    await logoutAndSettle(retried.session);

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls.map((call) => call.url)).toEqual([
      "/api/admin/login",
      "/api/admin/login",
      "/api/admin/session",
      "/api/admin/logout",
      "/api/admin/session",
      "/api/admin/logout",
    ]);
    for (const call of calls) {
      expect(call.url).not.toContain(USERNAME);
      expect(call.url).not.toContain(PASSWORD);
      expect(call.url).not.toContain(TOKEN);
    }
  });
});

describe("colour mode", () => {
  // matchMedia 替身：记录查询与监听器，可改变结果并通知。
  function fakeMatchMedia(dark: boolean) {
    const listeners = new Set<() => void>();
    const queries: string[] = [];
    const state = { dark };
    const matchMedia: MatchMedia = (query) => {
      queries.push(query);
      return {
        get matches() {
          return state.dark;
        },
        addEventListener: (_type, listener) => {
          listeners.add(listener);
        },
        removeEventListener: (_type, listener) => {
          listeners.delete(listener);
        },
      };
    };
    const change = (next: boolean) => {
      state.dark = next;
      for (const listener of listeners) {
        listener();
      }
    };
    return { matchMedia, queries, listeners, change };
  }

  // UX「管理后台总体」「深浅色随管理员设备设置」与 SHOP-TASK-038 验收第 2 条「在浏览器里按 prefers-color-scheme 设为 light 或 dark 并随设备设置变化」：
  // 取值按 (prefers-color-scheme: dark)；订阅后设备设置改变时通知页面重新取值，取消订阅后不再通知；没有 matchMedia 时为 light。
  it("follows prefers-color-scheme and its changes", () => {
    const device = fakeMatchMedia(false);
    expect(adminMode(device.matchMedia)).toBe("light");
    const onChange = vi.fn();
    const stop = watchAdminMode(device.matchMedia, onChange);
    device.change(true);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(adminMode(device.matchMedia)).toBe("dark");
    device.change(false);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(adminMode(device.matchMedia)).toBe("light");
    stop();
    expect(device.listeners.size).toBe(0);
    device.change(true);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(new Set(device.queries)).toEqual(new Set([DARK_SCHEME_QUERY]));
    expect(DARK_SCHEME_QUERY).toBe("(prefers-color-scheme: dark)");
    expect(adminMode(null)).toBe("light");
    expect(() => watchAdminMode(null, onChange)()).not.toThrow();
  });

  // 同一条：根元素的 data-mode 即取到的值（acuven-shop.css 的 .acs-admin[data-mode="dark"] 切换深色变量）；首次渲染为 light 见「first render」。
  it("puts the mode on the root element", () => {
    expect(render({ mode: "dark" })).toMatch(/^<div class="acs-admin site-admin" data-mode="dark">/);
    expect(render({ mode: "light" })).toMatch(/^<div class="acs-admin site-admin" data-mode="light">/);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-038 验收第 7 条「页面文字全部来自字典」与验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 各状态下每段文字都是当前语言的某条字典文案（admin.logged_in 的 {username} 为会话接口给的用户名）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    allowed.add(formatCopy(COPY["admin.logged_in"][language], { username: USERNAME }));
    const states: Partial<AdminLoginViewProps>[] = [
      { screen: { kind: "loading" } },
      { screen: { kind: "form", error: null } },
      { screen: { kind: "form", error: null }, fields: FILLED, busy: true },
      ...Object.values(LOGIN_ERROR).map((error) => ({ screen: { kind: "form" as const, error } })),
      { screen: { kind: "signed_in", session: SESSION, error: null } },
      { screen: { kind: "signed_in", session: SESSION, error: "common.network_check" } },
      { screen: { kind: "signed_in", session: SESSION, error: "common.error_retry" } },
    ];
    for (const state of states) {
      const texts = visibleTexts(render(state, language));
      expect(texts.length).toBeGreaterThan(5);
      for (const value of texts) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
