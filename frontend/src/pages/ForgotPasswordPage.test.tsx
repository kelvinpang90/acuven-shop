import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PASSWORD_RESET_URL } from "../api/memberAuth";
import { CSRF_HEADER } from "../api/pay";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import {
  canReset,
  editPassword,
  INITIAL_RESET,
  passwordLength,
  resetStep,
  ResetView,
  smsEnabledFor,
  startResetting,
  submitReset,
  verifiedStep,
} from "./ForgotPasswordPage";
import type { ResetChange, ResetForm, ResetSettings, ResetViewProps } from "./ForgotPasswordPage";
import { LOADING_SETTINGS, registerPath } from "./LoginPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的公开 key、CSRF 令牌与密码都是虚构值。
const SITE_KEY = "1x00000000000000000000AA";
const CSRF_TOKEN = "csrf-token-from-verify";
const NEW_PASSWORD = "new-pass-123";
const SMS_ON: ResetSettings = { status: "ready", smsEnabled: true, siteKey: SITE_KEY };
const SMS_OFF: ResetSettings = { status: "ready", smsEnabled: false, siteKey: SITE_KEY };
const FAILED: ResetSettings = { status: "failed" };
const REGISTERED: ResetForm = verifiedStep({ kind: "registered", csrfToken: CSRF_TOKEN })(INITIAL_RESET);
const FILLED: ResetForm = editPassword(REGISTERED, NEW_PASSWORD);
const NOT_REGISTERED: ResetForm = verifiedStep({ kind: "not_registered" })(INITIAL_RESET);

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/forgot-password">{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<ResetViewProps> = {}, language: Language = "en"): string {
  const props: ResetViewProps = {
    settings: SMS_ON,
    form: INITIAL_RESET,
    registerTo: null,
    onVerified: noop,
    onPassword: noop,
    onSubmit: noop,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<ResetView {...props} />, language));
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 字典某一条在页面里的样子（HTML 转义后）。
function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

// 文本节点（不含属性），依出现顺序。
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
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

// 页面里全部链接的去向。
function hrefs(html: string): string[] {
  return [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Reply = () => Response | "network";

// fetch 替身：每次请求调用 reply 取回答（"network" 时抛出网络错误）。
function stubFetch(reply: Reply) {
  const request = vi.fn((input: string | URL, init?: RequestInit) => {
    const next = reply();
    return next === "network" ? Promise.reject(new TypeError(`Failed to fetch: ${String(init?.method)} ${String(input)}`)) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", request);
  return request;
}

function bodyOf(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

// 以 start 为提交时的状态提交一次，返回 fetch 替身与套上全部改动之后的状态。
async function reset(reply: Reply, start: ResetForm = FILLED, signal: AbortSignal = new AbortController().signal) {
  const request = stubFetch(reply);
  let form = startResetting(start);
  const changes: ResetChange[] = [];
  await submitReset(start.password, CSRF_TOKEN, signal, (change) => {
    changes.push(change);
    form = change(form);
  });
  return { request, changes, form };
}

describe("page", () => {
  // SHOP-TASK-147 验收第 2 条「路由 /forgot-password…套前台框架」「标题 auth.reset_title…读取完成前不渲染 V1、标 aria-busy」
  // 与 UX P12 线框「[auth.reset_title]」「<V1：用途「重设密码」>」：经路由打开时只有标题，内容区为空并标 aria-busy。
  it.each(LANGUAGES)("opens /forgot-password with the title and a busy body in %s", (language) => {
    const html = renderToStaticMarkup(<App initialPath="/forgot-password" storage={languageStorage(language)} />);
    expect(html).toContain(`<main class="site-reset"><section class="acs-card site-reset__card"><h1 class="acs-display-l">${copy("auth.reset_title", language)}</h1><div class="site-reset__body" aria-busy="true"></div></section></main>`);
  });

  // 同一条「读取完成前不渲染 V1」：读取中只有标题，没有号码输入与发送按钮。
  it("shows only the title while the site settings load", () => {
    const html = render({ settings: LOADING_SETTINGS });
    expect(visibleTexts(html)).toEqual([COPY["auth.reset_title"].en]);
    expect(html).not.toMatch(/<(form|input|select|button)\b/);
  });

  // 同一条「读取失败只显示 common.error_retry」：标题之下只有一条 common.error_retry，没有 V1。
  it.each(LANGUAGES)("shows only common.error_retry when the settings fail in %s", (language) => {
    const html = render({ settings: FAILED }, language);
    expect(textNodes(html)).toEqual([COPY["auth.reset_title"][language], COPY["common.error_retry"][language]]);
    expect(html).toContain(`<div class="site-reset__body" aria-busy="false"><p class="acs-alert acs-alert--danger site-notice" role="alert">`);
    expect(html).not.toContain(`class="site-sms"`);
  });

  // 同一条「初始只有 V1」与 UX P12「忘记密码显示 [auth.sms_scope]」「重设密码：无游客文字与按钮」（V1 用途 reset_password）：
  // 读到开关开启后标题之下只有 V1（号码、auth.sms_scope、挑战与发送按钮，没有 auth.claim_notice 与 auth.continue_guest），没有新密码与未注册的文字。
  it.each(LANGUAGES)("starts with V1 only in %s", (language) => {
    const html = render({}, language);
    expect(html).toContain(`<div class="site-reset__body" aria-busy="false"><div class="site-sms">`);
    expect(html).toContain(copy("auth.sms_scope", language));
    expect(html).toContain(`>${copy("auth.send_code", language)}</button>`);
    expect(html).not.toContain(copy("auth.claim_notice", language));
    expect(html).not.toContain(copy("auth.continue_guest", language));
    expect(html).not.toContain(copy("auth.new_password", language));
    expect(html).not.toContain(copy("auth.reset_not_registered", language));
    expect(html).not.toContain(`type="password"`);
    expect(html).not.toContain("<form");
    expect(hrefs(html)).toEqual([]);
  });

  // 同一条「开关关闭时由 V1 显示 auth.sms_paused，不显示 auth.sms_scope」与 DESIGN 1.12「权限与资料保护」第 3 条「开关关闭时重设暂停」：
  // V1 整个换成 auth.sms_paused，没有号码输入、发送按钮与游客按钮。
  it.each(LANGUAGES)("pauses V1 when SMS is switched off in %s", (language) => {
    const html = render({ settings: SMS_OFF }, language);
    expect(textNodes(html)).toEqual([COPY["auth.reset_title"][language], COPY["auth.sms_paused"][language]]);
    expect(html).not.toContain(copy("auth.sms_scope", language));
    expect(html).not.toMatch(/<(input|select|button)\b/);
    expect(smsEnabledFor(SMS_OFF, INITIAL_RESET)).toBe(false);
    expect(smsEnabledFor(SMS_ON, INITIAL_RESET)).toBe(true);
  });
});

describe("number registered", () => {
  // 同一条「registered 时 V1 之下显示新密码（auth.new_password，输入类型为 password）、auth.password_rule 与 auth.reset_submit」
  // 与视觉稿 P12-desktop 的重设部分（新密码字段其下 auth.password_rule 为字段说明，整宽的主按钮）：表单在 V1 之后，开始时按钮禁用。
  it.each(LANGUAGES)("shows the new password form under V1 in %s", (language) => {
    const html = render({ form: REGISTERED }, language);
    const form = html.slice(html.indexOf(`<form class="site-reset__form"`));
    expect(html.indexOf(`<div class="site-sms">`)).toBeLessThan(html.indexOf(`<form class="site-reset__form"`));
    expect(form).toMatch(
      new RegExp(
        `<label class="acs-field__label" for="([^"]+)">${copy("auth.new_password", language)}</label><input class="acs-input" id="\\1" type="password" autocomplete="new-password" aria-describedby="([^"]+)" value=""/><span class="acs-field__hint" id="\\2">${copy("auth.password_rule", language)}</span>`,
        "i",
      ),
    );
    expect(form).toContain(`<button class="acs-btn acs-btn--primary acs-btn--block" type="submit" disabled="" aria-busy="false">${copy("auth.reset_submit", language)}</button>`);
    expect(html).not.toContain(copy("auth.reset_not_registered", language));
  });

  // 同一条「新密码按 Unicode 码点计数（与服务端 Python 的 len 相同，不按 UTF-16 长度），不足 8 个或超过 256 个时按钮禁用」
  // 与 DESIGN 1.12「权限与资料保护」第 3 条「密码至少 8 位」：一个表情符号算一个字符（UTF-16 长度为 2）。
  it.each([
    ["1234567", 7, false],
    ["12345678", 8, true],
    ["123456😀", 7, false],
    ["1234567😀", 8, true],
    ["😀".repeat(256), 256, true],
    ["😀".repeat(257), 257, false],
    ["a".repeat(256), 256, true],
    ["a".repeat(257), 257, false],
  ] as const)("counts %j as %i code points (submittable: %s)", (password, length, submittable) => {
    expect(passwordLength(password)).toBe(length);
    const form = editPassword(REGISTERED, password);
    expect(canReset(form)).toBe(submittable);
    expect(render({ form }).includes(`type="submit" disabled=""`)).toBe(!submittable);
  });

  // 同一条「服务端仍会判定」的另一侧与「提交进行中」：未验证、未注册、提交进行中、已重设与暂停时都不可提交。
  it("does not submit before verification, while submitting, after success or when paused", () => {
    expect(canReset(editPassword(INITIAL_RESET, NEW_PASSWORD))).toBe(false);
    expect(canReset(editPassword(NOT_REGISTERED, NEW_PASSWORD))).toBe(false);
    expect(canReset(startResetting(FILLED))).toBe(false);
    expect(canReset({ ...FILLED, done: true })).toBe(false);
    expect(canReset({ ...FILLED, paused: true })).toBe(false);
    expect(render({ form: startResetting(FILLED) })).toContain(`type="submit" disabled="" aria-busy="true">`);
  });

  // 同一条「以 V1 交回的 csrf_token 提交」与「新密码与 csrf_token 只在页面状态与请求里，不进地址」、接口记录 SHOP-TASK-079
  // 「POST /api/member/password-reset：请求体只有 password，请求头带 CSRF 令牌」：一次 POST，不带查询参数，令牌只在请求头里。
  it("posts the new password with the token V1 handed back", async () => {
    expect(FILLED.verified).toEqual({ kind: "registered", csrfToken: CSRF_TOKEN });
    const { request } = await reset(() => new Response(null, { status: 204 }));
    expect(request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), token: new Headers(init?.headers).get(CSRF_HEADER), body: bodyOf(init) }))).toEqual([
      { url: PASSWORD_RESET_URL, method: "POST", token: CSRF_TOKEN, body: { password: NEW_PASSWORD } },
    ]);
  });

  // 同一条「新密码与 csrf_token…不进地址、日志或浏览器存储」：页面上不出现 CSRF 令牌，新密码只作为输入框的值。
  it("keeps the token out of the page", () => {
    const html = render({ form: FILLED });
    expect(html).not.toContain(CSRF_TOKEN);
    expect(html).toContain(`value="${NEW_PASSWORD}"`);
    expect(hrefs(html).join(" ")).not.toContain(NEW_PASSWORD);
  });
});

describe("submit results", () => {
  // 同一条「ok 显示 auth.reset_done 与去 /login 的 common.nav_login 链接…密码输入收起」与视觉稿 P12-reset-states 的成功提示：
  // 新密码与令牌清空；页面在 V1 之后只有成功提示与登录链接，没有密码输入。
  it.each(LANGUAGES)("shows auth.reset_done and the login link after success in %s", async (language) => {
    const { form } = await reset(() => new Response(null, { status: 204 }));
    expect(form).toEqual({ ...INITIAL_RESET, done: true });
    const html = render({ form }, language);
    expect(html).toMatch(
      new RegExp(
        `<div class="acs-alert acs-alert--success" role="status"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.reset_done", language)}</span></div><a class="acs-btn acs-btn--secondary site-reset__link" href="/login">${copy("common.nav_login", language)}</a></div></section>`,
      ),
    );
    expect(html).not.toContain(`type="password"`);
    expect(html).not.toContain(CSRF_TOKEN);
  });

  // 同一条「too_short 留在表单并显示 auth.password_rule」：表单仍在、密码保留，auth.password_rule 改为字段错误并与输入关联。
  it.each(LANGUAGES)("keeps the form and flags auth.password_rule on too_short in %s", async (language) => {
    const { form } = await reset(() => json(422, { detail: "password_too_short" }));
    expect(form).toEqual({ ...FILLED, tooShort: true });
    const html = render({ form }, language);
    expect(html).toContain(`<div class="acs-field acs-field--invalid">`);
    expect(html).toMatch(new RegExp(`aria-invalid="true" aria-describedby="([^"]+)" value="[^"]*"/><span class="acs-field__error" id="\\1"><svg[^>]*>[\\s\\S]*?</svg>${copy("auth.password_rule", language)}</span>`));
    expect(canReset(form)).toBe(true);
  });

  // 同一条「expired 与 csrf 以新的 key 重新挂载 V1、回到验证前的初始状态，并在 V1 之上显示 common.error_retry」与 HANDOFF 0.58 第（f）条
  // 「重设提交遇到 csrf_failed 与 reset_expired 都回到 V1 重新验证」：状态回到初始（令牌与新密码都清空），V1 的 key 换了一个，
  // 页面为 common.error_retry 与其后初始状态的 V1（有发送按钮，没有 auth.code_sent），没有密码表单。
  it.each([
    ["expired", () => json(401, { detail: "reset_expired" })],
    ["csrf", () => json(403, { detail: "csrf_failed" })],
  ] as const)("goes back to a fresh V1 with common.error_retry on %s", async (_name, reply) => {
    const { form } = await reset(reply);
    expect(form).toEqual({ ...INITIAL_RESET, round: 1, retry: true });
    const html = render({ form });
    expect(html).toContain(`<div class="site-reset__body" aria-busy="false"><p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry")}</p><div class="site-sms">`);
    expect(html).toContain(`>${copy("auth.send_code")}</button>`);
    expect(html).not.toContain(copy("auth.code_sent").split("{")[0] ?? "?");
    expect(html).not.toContain(`type="password"`);
    // 再一次验证通过后提示收起，表单以新的令牌出现。
    const again = verifiedStep({ kind: "registered", csrfToken: "second-token" })(form);
    expect(again).toEqual({ ...INITIAL_RESET, round: 1, verified: { kind: "registered", csrfToken: "second-token" } });
  });

  // 同一条「disabled 时页面把开关当作关闭，V1 显示 auth.sms_paused，不另显示 common.error_retry」与 DESIGN 1.12「开关关闭时重设暂停」。
  it("treats the switch as off on disabled", async () => {
    const { form } = await reset(() => json(403, { detail: "sms_disabled" }));
    expect(form).toEqual({ ...INITIAL_RESET, paused: true });
    expect(smsEnabledFor(SMS_ON, form)).toBe(false);
    const html = render({ form });
    expect(textNodes(html)).toEqual([COPY["auth.reset_title"].en, COPY["auth.sms_paused"].en]);
    expect(html).not.toContain(copy("common.error_retry"));
  });

  // 同一条「unavailable 显示 common.service_unavailable、failed 显示 common.error_retry，都留在表单可再提交；network 显示 common.network_check」
  // 与视觉稿 P12-phone 登录部分的失败提示写法（重设部分没有画，照同页按钮之前的 danger 提示条）：新密码保留，按钮可再点。
  const failures: [string, Reply, CopyKey][] = [
    ["unavailable", () => json(503, { detail: "service_unavailable" }), "common.service_unavailable"],
    ["failed", () => json(500, {}), "common.error_retry"],
    ["network", () => "network", "common.network_check"],
  ];
  it.each(failures)("keeps the form on %s", async (_name, reply, key) => {
    const { form } = await reset(reply);
    expect(form).toEqual({ ...FILLED, notice: key });
    expect(canReset(form)).toBe(true);
    const html = render({ form });
    expect(html).toMatch(new RegExp(`<div class="acs-alert acs-alert--danger" role="alert"><svg[^>]*>[\\s\\S]*?</svg><span>${copy(key)}</span></div><button class="acs-btn acs-btn--primary acs-btn--block" type="submit" aria-busy="false">`));
    expect(html).toContain(`value="${NEW_PASSWORD}"`);
  });

  // 同一条「离开页面时中止请求，之后不再更新」：回答到达前已离开，不交出改动。
  it("stays silent after leaving the page", async () => {
    const gone = new AbortController();
    gone.abort();
    const { changes } = await reset(() => new Response(null, { status: 204 }), FILLED, gone.signal);
    expect(changes).toEqual([]);
  });

  // 验收第 4 条「too_short 留在表单并显示 auth.password_rule」与「unavailable 显示 common.service_unavailable、failed 显示 common.error_retry，都留在表单可再提交」：
  // 再次提交时先前的提示收起，表单留着；这次的回答只显示这次的提示，不叠上一次的 auth.password_rule。
  it("clears earlier notices when a new submit starts", () => {
    const submitting = startResetting({ ...FILLED, tooShort: true, notice: "common.error_retry" });
    expect(submitting).toEqual({ ...FILLED, submitting: true });
    expect(resetStep("failed")(submitting)).toEqual({ ...FILLED, notice: "common.error_retry" });
  });
});

describe("number not registered", () => {
  // 同一条「not_registered 时显示 auth.reset_not_registered 与去 /register 的 common.nav_register（/register 进入路由表之前只显示文案、
  // 不渲染链接…），不显示密码输入」与 DESIGN 1.12「权限与资料保护」第 3 条「未注册号码不创建账号」、视觉稿 P12-reset-states：
  // 目前 /register 不在路由表，只有 info 提示；给出路径时（以已有的 /privacy 代替）提示之后为次要按钮样式的链接。
  it.each(LANGUAGES)("shows auth.reset_not_registered and the register link only when the page exists in %s", (language) => {
    expect(NOT_REGISTERED.verified).toEqual({ kind: "not_registered" });
    expect(registerPath()).toBeNull();
    const without = render({ form: NOT_REGISTERED, registerTo: registerPath() }, language);
    expect(without).toMatch(new RegExp(`<div class="acs-alert acs-alert--info" role="status"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.reset_not_registered", language)}</span></div></div></section>`));
    expect(without).not.toContain(copy("common.nav_register", language) + "</a></div></section>");
    expect(hrefs(without)).toEqual([]);
    expect(without).not.toContain(`type="password"`);
    const withLink = render({ form: NOT_REGISTERED, registerTo: "/privacy" }, language);
    expect(withLink).toContain(`</span></div><a class="acs-btn acs-btn--secondary site-reset__link" href="/privacy">${copy("common.nav_register", language)}</a></div></section>`);
  });

  // 任务目的「嵌入 SHOP-TASK-145 的 V1（用途 reset_password），号码已注册时显示新密码输入…未注册时显示 auth.reset_not_registered」
  // 与 DESIGN 1.12「权限与资料保护」第 3 条「未注册号码不创建账号」：页面只按 registered 与 not_registered 两种结果改变，
  // 注册 / 短信登录用途的 member 结果（含 created）不属于这一用途，页面不因它显示表单或已登录状态。
  it("ignores a member result", () => {
    expect(verifiedStep({ kind: "member", created: true })(INITIAL_RESET)).toBe(INITIAL_RESET);
  });
});

describe("page text", () => {
  // 同一条「页面文字（国家码选项 +60、+65 除外）全部来自字典」与验收第 1 条「需要 UX-COPY 里没有的界面文字时停下」：
  // 读取中、读取失败、初始、开关关闭、已注册（含各提示）、未注册、重新验证与已重设下，三种语言的文字都在当前语言的字典里。
  const states: Partial<ResetViewProps>[] = [
    { settings: LOADING_SETTINGS },
    { settings: FAILED },
    {},
    { settings: SMS_OFF },
    { form: FILLED },
    { form: resetStep("too_short")(FILLED) },
    { form: resetStep("unavailable")(FILLED) },
    { form: resetStep("network")(FILLED) },
    { form: resetStep("expired")(FILLED) },
    { form: resetStep("disabled")(FILLED) },
    { form: resetStep("ok")(FILLED) },
    { form: NOT_REGISTERED, registerTo: "/privacy" },
  ];
  it.each(LANGUAGES)("uses only dictionary text in %s", (language) => {
    const allowed = new Set<string>([...Object.values(COPY).map((entry) => entry[language]), "+60", "+65"]);
    for (const state of states) {
      for (const text of visibleTexts(render(state, language))) {
        expect(allowed.has(text), text).toBe(true);
      }
    }
  });
});
