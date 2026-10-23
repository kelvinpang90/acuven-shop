import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PASSWORD_LOGIN_URL } from "../api/memberAuth";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { guestPath, RouterProvider, VERIFIED_REDIRECT_MS } from "../router";
import type { RoutePath } from "../router";
import {
  canSubmit,
  finishSms,
  forgotPath,
  INITIAL_PASSWORD,
  LOADING_SETTINGS,
  loadSettings,
  LoginView,
  passwordStep,
  redirectLater,
  registerPath,
  smsEnabledOf,
  smsVerifiedCopy,
  startSubmitting,
  submitPassword,
  timerWait,
} from "./LoginPage";
import type { LoginMoves, LoginSettings, LoginViewProps, PasswordForm, Wait } from "./LoginPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// 测试里的号码、密码与公开 key 都是虚构值。
const SITE_KEY = "1x00000000000000000000AA";
const PHONE_INPUT = "12-345 6789";
const PASSWORD = "secret-pass-1";
const SMS_ON: LoginSettings = { status: "ready", smsEnabled: true, siteKey: SITE_KEY };
const SMS_OFF: LoginSettings = { status: "ready", smsEnabled: false, siteKey: SITE_KEY };
const FAILED: LoginSettings = { status: "failed" };
const FILLED: PasswordForm = { ...INITIAL_PASSWORD, input: PHONE_INPUT, password: PASSWORD };

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
      <RouterProvider initialPath="/login">{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<LoginViewProps> = {}, language: Language = "en"): string {
  const props: LoginViewProps = {
    settings: SMS_ON,
    tab: "password",
    form: INITIAL_PASSWORD,
    smsDone: null,
    guestTo: "/",
    forgotTo: null,
    registerTo: null,
    onTab: noop,
    onRegion: noop,
    onPhone: noop,
    onPassword: noop,
    onSubmit: noop,
    onSmsVerified: noop,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<LoginView {...props} />, language));
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

// 两个页签内容：依次为密码页签与短信验证码页签（各自从开始标签起，到下一个页签或页面结束）。
function panels(html: string): { password: string; sms: string } {
  const [, password = "", sms = ""] = html.split(`<div class="site-login__panel" `);
  return { password, sms };
}

function hidden(panel: string): boolean {
  return /^[^>]*\shidden=""/.test(panel);
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

// 页面对结果的处理的替身：按顺序记下事件，并把改动套在表单上。
function fakeMoves(start: PasswordForm = FILLED) {
  const events: string[] = [];
  let form = start;
  const moves: LoginMoves = {
    update: (change) => {
      form = change(form);
      events.push("update");
    },
    smsVerified: (key) => {
      events.push(`sms ${key}`);
    },
    replace: (path) => {
      events.push(`replace ${path}`);
    },
  };
  return { moves, events, form: () => form };
}

// 计时替身：记下等待的毫秒数，不真的等待；run 在完成前执行（如模拟期间离开页面）。
function fakeWait(events: string[], run: () => void = noop): Wait {
  return vi.fn((ms: number, signal: AbortSignal) => {
    events.push(`wait ${String(ms)}${signal.aborted ? " aborted" : ""}`);
    run();
    return Promise.resolve();
  });
}

async function login(reply: Reply, smsEnabled: boolean, source: RoutePath | null = null) {
  const request = stubFetch(reply);
  const target = fakeMoves();
  await submitPassword({ input: PHONE_INPUT, region: "MY" }, PASSWORD, smsEnabled, source, new AbortController().signal, fakeWait(target.events), target.moves);
  return { request, ...target };
}

describe("page and tabs", () => {
  // SHOP-TASK-146 验收第 2 条「标题 auth.login_title、★ auth.login_demo_hint、两个页签 auth.login_method_password 与 auth.login_method_sms（默认密码页签）」
  // 与 UX P12 线框「([auth.login_method_password]) | ([auth.login_method_sms])」：经路由打开 /login，页签依次为密码与短信验证码，密码页签选中。
  it.each(LANGUAGES)("opens /login with the title, the demo hint and the password tab selected in %s", (language) => {
    const html = renderToStaticMarkup(<App initialPath="/login" storage={languageStorage(language)} />);
    expect(html).toContain(`<h1 class="acs-display-l">${copy("auth.login_title", language)}</h1>`);
    expect(html).toContain(copy("auth.login_demo_hint", language));
    const tabs = [...html.matchAll(/<button class="acs-tab"[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*>([^<]*)<\/button>/g)].map((m) => [m[2], m[1]]);
    expect(tabs).toEqual([
      [copy("auth.login_method_password", language), "true"],
      [copy("auth.login_method_sms", language), "false"],
    ]);
    expect(html).toContain(`<div class="acs-tabs" role="tablist">`);
  });

  // 同一条「读取完成之前只显示标题、★ 提示与两个页签，页签内容区标 aria-busy、不渲染表单与 V1」（App.test.tsx 按路由表检查每页初始渲染只有字典文字）：
  // 读取中页面只有这四段文字，内容区为空并标 aria-busy，页签不指向尚不存在的内容。
  it("shows only the title, the hint and the tabs while the site settings load", () => {
    const html = render({ settings: LOADING_SETTINGS });
    expect(visibleTexts(html)).toEqual([COPY["auth.login_title"].en, COPY["auth.login_demo_hint"].en, COPY["auth.login_method_password"].en, COPY["auth.login_method_sms"].en]);
    expect(html).toContain(`<div class="site-login__panels" aria-busy="true"></div>`);
    expect(html).not.toMatch(/<(form|input|select)\b/);
    expect(html).not.toContain("aria-controls");
  });

  // 同一条「切换页签不清空另一页签已填的内容」：两个页签的内容都在页面上，未选的 hidden；选短信页签时密码页签已填的号码仍在。
  // 页签与内容互相指向（role="tab" 的 aria-controls 与 role="tabpanel" 的 aria-labelledby）。
  it("keeps both tab panels on the page and hides the one not selected", () => {
    const onPassword = render({ form: { ...INITIAL_PASSWORD, input: PHONE_INPUT } });
    expect(hidden(panels(onPassword).password)).toBe(false);
    expect(hidden(panels(onPassword).sms)).toBe(true);
    const onSms = render({ tab: "sms", form: { ...INITIAL_PASSWORD, input: PHONE_INPUT } });
    const { password, sms } = panels(onSms);
    expect(hidden(password)).toBe(true);
    expect(hidden(sms)).toBe(false);
    expect(password).toContain(`value="${PHONE_INPUT}"`);
    expect(sms).toContain(copy("auth.same_flow_note"));
    expect(onSms).toMatch(/<div class="site-login__panels" aria-busy="false">/);
    for (const match of onSms.matchAll(/role="tab" type="button" aria-selected="(?:true|false)" aria-controls="([^"]+)"/g)) {
      expect(onSms).toContain(`id="${match[1] ?? "?"}" role="tabpanel"`);
    }
  });
});

describe("site settings", () => {
  // 同一条「页面打开时读取一次站点设置…取得开关的值与人机挑战公开 key」：只发一次 GET /api/site-settings，结果交给页面。
  it("reads the switch and the challenge site key once", async () => {
    const request = stubFetch(() => json(200, { sms_verification_enabled: false, turnstile_site_key: SITE_KEY, whatsapp_contact_url: null }));
    const shown: LoginSettings[] = [];
    await loadSettings(new AbortController().signal, (settings) => {
      shown.push(settings);
    });
    expect(shown).toEqual([{ status: "ready", smsEnabled: false, siteKey: SITE_KEY }]);
    expect(request.mock.calls.map(([input, init]) => `${String(init?.method)} ${String(input)}`)).toEqual(["GET /api/site-settings"]);
  });

  // 同一条「读取失败时密码页签照常可用…短信验证码页签只显示 common.error_retry」：失败归为 failed；离开页面（中止）之后不再更新。
  it("reports a failed read and stays silent after leaving the page", async () => {
    stubFetch(() => json(500, {}));
    const shown: LoginSettings[] = [];
    await loadSettings(new AbortController().signal, (settings) => {
      shown.push(settings);
    });
    expect(shown).toEqual([FAILED]);

    stubFetch(() => "network");
    const controller = new AbortController();
    controller.abort();
    await loadSettings(controller.signal, (settings) => {
      shown.push(settings);
    });
    expect(shown).toEqual([FAILED]);
  });

  // 同一条「读取失败时密码页签照常可用（登录失败文案取 auth.login_failed_sms_off，不提示改用短信），短信验证码页签只显示 common.error_retry」
  // 与 HANDOFF 0.58 第（g）条：密码表单与登录按钮照常（可提交），短信页签只有 common.error_retry，没有 V1 与 common.nav_register。
  it.each(LANGUAGES)("keeps the password tab usable when the settings fail in %s", (language) => {
    const html = render({ settings: FAILED, form: FILLED, registerTo: "/privacy" }, language);
    const { password, sms } = panels(html);
    // 属性名不分大小写（react-dom/server 输出 noValidate 这类驼峰名）。
    expect(password).toMatch(/<form class="site-login__form" novalidate="">/i);
    expect(password).toMatch(new RegExp(`<button class="acs-btn acs-btn--primary acs-btn--block" type="submit" aria-busy="false">${copy("auth.login_submit", language)}</button>`));
    expect(canSubmit(FILLED)).toBe(true);
    expect(smsEnabledOf(FAILED)).toBe(false);
    expect(textNodes(sms)).toEqual([COPY["common.error_retry"][language]]);
    expect(sms).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">`);
  });

  // 同一条「登录失败文案取 auth.login_failed_sms_off」：读取失败时提交得到 login_failed，显示不提示改用短信的一条。
  it("shows auth.login_failed_sms_off for a failed login when the settings could not be read", async () => {
    const { form } = await login(() => json(401, { detail: "login_failed" }), smsEnabledOf(FAILED));
    expect(form().notice).toBe("auth.login_failed_sms_off");
  });
});

describe("password tab", () => {
  // 同一条「国家码选择（+60、+65，默认 +60）与号码（auth.phone）、密码（auth.password，输入类型为 password）、auth.login_submit」与视觉稿 P12-desktop：
  // 国家码下拉只有两项、默认 +60，以 auth.phone 的标签作读屏名称；号码为 tel 输入；密码为 password 输入；登录按钮为整宽主按钮。
  it.each(LANGUAGES)("has the country code, the mobile number, the password and the log in button in %s", (language) => {
    const html = panels(render({}, language)).password;
    const select = /<select[\s\S]*?<\/select>/.exec(html)?.[0] ?? "";
    expect([...select.matchAll(/<option value="([A-Z]{2})"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]])).toEqual([
      ["MY", "+60"],
      ["SG", "+65"],
    ]);
    expect(select).toContain(`<option value="MY" selected="">+60</option>`);
    const labelId = /<label class="acs-field__label" id="([^"]+)" for="[^"]+">/.exec(html)?.[1] ?? "?";
    expect(select).toContain(`aria-labelledby="${labelId}"`);
    expect(html).toContain(`${copy("auth.phone", language)}</label>`);
    // 属性名不分大小写（react-dom/server 输出 inputMode、autoComplete 这类驼峰名）。
    expect(html).toMatch(/<input class="acs-input" id="[^"]+" type="tel" inputmode="tel" autocomplete="tel" value=""\/>/i);
    expect(html).toMatch(new RegExp(`<label class="acs-field__label" for="([^"]+)">${copy("auth.password", language)}</label><input class="acs-input" id="\\1" type="password" autocomplete="current-password" value=""/>`, "i"));
    expect(html).toContain(`<button class="acs-btn acs-btn--primary acs-btn--block" type="submit" aria-busy="false">${copy("auth.login_submit", language)}</button>`);
  });

  // 同一条「提交进行中按钮禁用」：startSubmitting 之后按钮禁用并标 aria-busy，先前的提示收起；已登录后也不再提交。
  it("disables the button while a login is in progress", () => {
    const submitting = startSubmitting({ ...FILLED, notice: "auth.login_failed", phoneInvalid: true });
    expect(submitting).toEqual({ ...FILLED, submitting: true });
    expect(canSubmit(submitting)).toBe(false);
    expect(canSubmit({ ...FILLED, verified: true })).toBe(false);
    expect(render({ form: submitting })).toContain(`type="submit" disabled="" aria-busy="true">`);
  });

  // 同一条「经 SHOP-TASK-144 调用密码登录」与「密码只在页面状态与请求体里」：一次 POST /api/member/password-login，不带查询参数，
  // 请求体只有号码原文、地区与密码。
  it("posts the number and the password in the request body only", async () => {
    const { request } = await login(() => new Response(null, { status: 204 }), true);
    expect(request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), body: bodyOf(init) }))).toEqual([
      { url: PASSWORD_LOGIN_URL, method: "POST", body: { phone: PHONE_INPUT, phone_region: "MY", password: PASSWORD } },
    ]);
  });

  // 同一条「login_failed 在开关开启时显示 auth.login_failed、关闭时显示 auth.login_failed_sms_off」与 DESIGN 1.12「权限与资料保护」第 3 条
  // 「密码登录对未设密码账号与错误密码同一失败提示」、UX P12「短信验证开关关闭时…登录失败改显示 [auth.login_failed_sms_off]」。
  it.each([
    [true, "auth.login_failed"],
    [false, "auth.login_failed_sms_off"],
  ] as const)("shows the login failure with SMS on = %s as %s", async (smsEnabled, key) => {
    const { form } = await login(() => json(401, { detail: "login_failed" }), smsEnabled);
    expect(form()).toEqual({ ...FILLED, password: "", notice: key });
  });

  // 同一条「phone_invalid 显示 checkout.phone_invalid；unavailable 显示 common.service_unavailable…failed 显示 common.error_retry；network 显示 common.network_check」
  // 与「提交失败后保留号码、清空密码」。
  it.each([
    ["phone_invalid", () => json(422, { detail: [{ loc: ["body", "phone"], type: "phone_invalid", msg: "invalid" }] }), { phoneInvalid: true, notice: null }],
    ["unavailable", () => json(503, { detail: "service_unavailable" }), { phoneInvalid: false, notice: "common.service_unavailable" }],
    ["failed", () => json(500, {}), { phoneInvalid: false, notice: "common.error_retry" }],
    ["network", () => "network" as const, { phoneInvalid: false, notice: "common.network_check" }],
  ] as const)("handles %s, keeping the number and clearing the password", async (_name, reply, expected) => {
    const { form, events } = await login(reply, true);
    expect(form()).toEqual({ ...FILLED, password: "", ...expected });
    expect(events).toEqual(["update"]);
  });

  // HANDOFF 0.58 第（g）条「密码登录连续失败锁定时服务端仍回 401，UX 线框里的 common.rate_limited 在密码页签用不到」：
  // 锁定时同样是 401 login_failed，页面显示登录失败而不是 common.rate_limited。
  it("treats a locked-out login like any failed login", () => {
    const form = passwordStep("login_failed", true)({ ...FILLED, submitting: true });
    expect(form.notice).toBe("auth.login_failed");
    expect(form.notice).not.toBe("common.rate_limited");
  });

  // 视觉稿 P12-phone：登录失败为登录按钮之前的 danger 提示条；phone_invalid 照 V1 在号码下显示 checkout.phone_invalid；清空后的密码不在页面上。
  it.each(LANGUAGES)("shows the failure above the button and the invalid number under the field in %s", (language) => {
    const failed = render({ form: passwordStep("login_failed", true)(FILLED) }, language);
    expect(failed).toMatch(new RegExp(`<div class="acs-alert acs-alert--danger" role="alert"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.login_failed", language)}</span></div><button class="acs-btn acs-btn--primary acs-btn--block" type="submit"`));
    expect(failed).toContain(`value="${PHONE_INPUT}"`);
    expect(failed).not.toContain(PASSWORD);
    const invalid = render({ form: passwordStep("phone_invalid", true)(FILLED) }, language);
    expect(invalid).toContain(`<div class="acs-field acs-field--invalid">`);
    expect(invalid).toMatch(new RegExp(`aria-invalid="true" aria-describedby="([^"]+)"/>[\\s\\S]*<span class="acs-field__error" id="\\1"><svg[^>]*>[\\s\\S]*?</svg>${copy("checkout.phone_invalid", language)}</span>`));
  });

  // 同一条「auth.forgot（/forgot-password 进入路由表之前不渲染，照 CheckoutPage.tsx 的 loginPath 写法以 isRoutePath 判断）」：
  // 目前不在路由表，不渲染；给出路径时（以已有的 /privacy 代替）为登录按钮之后的链接。
  it("renders auth.forgot only when the reset page exists", () => {
    expect(forgotPath()).toBeNull();
    const without = render();
    expect(without).not.toContain(copy("auth.forgot"));
    expect(without).not.toContain("/forgot-password");
    const withLink = render({ forgotTo: "/privacy" });
    expect(withLink).toContain(`</button><a class="acs-btn acs-btn--quiet site-login__forgot" href="/privacy">${copy("auth.forgot")}</a></form>`);
  });
});

describe("SMS code tab", () => {
  // 同一条「短信验证码页签——auth.same_flow_note 与 V1（用途 login，传入开关的值与公开 key…）」与 UX P12「短信验证码页签与 P11 走同一 V1 流程」：
  // 说明在前、V1 在后；V1 为注册 / 短信登录的样子（有 auth.claim_notice 与 auth.send_code，公开 key 已给出所以不是替换提示）。
  it.each(LANGUAGES)("embeds V1 after auth.same_flow_note in %s", (language) => {
    const sms = panels(render({ tab: "sms" }, language)).sms;
    expect(sms).toMatch(new RegExp(`^[^>]*><p class="acs-body-s site-login__text">${copy("auth.same_flow_note", language)}</p><div class="site-sms">`));
    expect(sms).toContain(copy("auth.sms_scope", language));
    expect(sms).toContain(copy("auth.claim_notice", language));
    expect(sms).toContain(`>${copy("auth.send_code", language)}</button>`);
    expect(sms).not.toContain(copy("auth.sms_paused", language));
  });

  // 同一条「开关关闭时由 V1 显示 auth.sms_paused」与「auth.continue_guest 的去向为来源页是 /checkout 时 /checkout、否则首页 P01」（router.tsx 的 guestPath）：
  // 开关关闭时 V1 换成 auth.sms_paused，auth.continue_guest 指向页面给出的去向。
  it.each([
    ["/checkout", "/checkout"],
    ["/cart", "/"],
    [null, "/"],
  ] as const)("pauses V1 with SMS off and sends continue as guest from %s to %s", (source, expected) => {
    const sms = panels(render({ tab: "sms", settings: SMS_OFF, guestTo: guestPath(source) })).sms;
    expect(sms).toContain(copy("auth.sms_paused"));
    expect(sms).toContain(`<a class="acs-btn acs-btn--secondary site-sms__guest" href="${expected}">${copy("auth.continue_guest")}</a>`);
    expect(sms).not.toContain(copy("auth.send_code"));
  });

  // 同一条「传入…公开 key」：开关开启而公开 key 为 null 时 V1 自己换成 auth.sms_failed 与 auth.continue_guest（SHOP-TASK-145）。
  it("passes a missing site key on to V1", () => {
    const sms = panels(render({ tab: "sms", settings: { status: "ready", smsEnabled: true, siteKey: null }, guestTo: "/checkout" })).sms;
    expect(sms).toContain(copy("auth.sms_failed"));
    expect(sms).toContain(`href="/checkout"`);
  });

  // 同一条「页签下方 common.nav_register（/register 进入路由表之前不渲染，写法同上）」：目前不渲染；给出路径时（以 /privacy 代替）在 V1 之后。
  it("renders common.nav_register only when the register page exists", () => {
    expect(registerPath()).toBeNull();
    const without = render({ tab: "sms" });
    expect(without).not.toContain(copy("common.nav_register"));
    expect(without).not.toContain("/register");
    const withLink = panels(render({ tab: "sms", registerTo: "/privacy" })).sms;
    expect(withLink).toMatch(new RegExp(`<a class="site-login__register" href="/privacy">${copy("common.nav_register")}</a></div></div></section>`));
  });
});

describe("after logging in", () => {
  // 同一条「密码登录 ok 显示 auth.verified_login…1.5 秒后用路由的 replace 前往来源页」与「来源为后台页、登录页本身与没有来源」：
  // 先显示已登录，再等 VERIFIED_REDIRECT_MS，然后以 replace 前往 router.tsx 的 verifiedPath（来源为前台页时回到来源，其余去首页）。
  it.each([
    ["/checkout", "/checkout"],
    ["/products/crew-neck-tee", "/products/crew-neck-tee"],
    ["/admin/orders", "/"],
    ["/login", "/"],
    [null, "/"],
  ] as const)("logs in with a password and goes from source %s to %s", async (source, expected) => {
    const { form, events } = await login(() => new Response(null, { status: 204 }), true, source);
    expect(form()).toEqual({ ...FILLED, password: "", verified: true });
    expect(events).toEqual(["update", `wait ${String(VERIFIED_REDIRECT_MS)}`, `replace ${expected}`]);
  });

  // 视觉稿 V1-verified 与 UX P12「成功：[auth.verified_login]」：已登录时密码页签只剩成功提示，表单收起。
  it.each(LANGUAGES)("replaces the password form with auth.verified_login in %s", (language) => {
    const html = panels(render({ form: { ...INITIAL_PASSWORD, input: PHONE_INPUT, verified: true } }, language)).password;
    expect(html).toMatch(new RegExp(`<div class="acs-alert acs-alert--success" role="status"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.verified_login", language)}</span></div>`));
    expect(html).not.toContain("<form");
  });

  // 同一条「短信验证通过按 created 显示 auth.verified_registered 或 auth.verified_login（照视觉稿 V1-verified）」：先显示文字，等 1.5 秒后同样以 replace 前往来源页。
  it.each([
    [true, "auth.verified_registered"],
    [false, "auth.verified_login"],
  ] as const)("finishes an SMS login with created = %s as %s", async (created, key) => {
    expect(smsVerifiedCopy({ kind: "member", created })).toBe(key);
    const { moves, events } = fakeMoves();
    await finishSms({ kind: "member", created }, "/cart", new AbortController().signal, fakeWait(events), moves);
    expect(events).toEqual([`sms ${key}`, `wait ${String(VERIFIED_REDIRECT_MS)}`, "replace /cart"]);
  });

  // 视觉稿 V1-verified：短信页签在 V1 之后显示成功提示。
  it.each(LANGUAGES)("shows the SMS result after V1 in %s", (language) => {
    const sms = panels(render({ tab: "sms", smsDone: "auth.verified_registered" }, language)).sms;
    expect(sms).toMatch(new RegExp(`</div><div class="acs-alert acs-alert--success" role="status"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.verified_registered", language)}</span></div>`));
  });

  // 同一条「离开页面时中止请求与计时，之后不再更新」：回答到达前已离开不更新；计时期间离开不跳转。
  it("stops after leaving the page", async () => {
    stubFetch(() => new Response(null, { status: 204 }));
    const before = fakeMoves();
    const gone = new AbortController();
    gone.abort();
    await submitPassword({ input: PHONE_INPUT, region: "MY" }, PASSWORD, true, "/cart", gone.signal, fakeWait(before.events), before.moves);
    expect(before.events).toEqual([]);

    const during = fakeMoves();
    const leaving = new AbortController();
    await redirectLater("/cart", leaving.signal, fakeWait(during.events, () => {
      leaving.abort();
    }), during.moves.replace);
    expect(during.events).toEqual([`wait ${String(VERIFIED_REDIRECT_MS)}`]);

    const late = fakeMoves();
    await finishSms({ kind: "member", created: false }, "/cart", gone.signal, fakeWait(late.events), late.moves);
    expect(late.events).toEqual([]);
  });

  // 同一条「计时可注入，测试不真的等待」：页面用的计时在 1500 毫秒后才完成，中止时取消（以替身计时器推进，不真的等待）。
  it("waits with a timer that leaving the page cancels", async () => {
    vi.useFakeTimers();
    let done = false;
    void timerWait(VERIFIED_REDIRECT_MS, new AbortController().signal).then(() => {
      done = true;
    });
    vi.advanceTimersByTime(VERIFIED_REDIRECT_MS - 1);
    await Promise.resolve();
    expect(done).toBe(false);
    vi.advanceTimersByTime(1);
    await Promise.resolve();
    expect(done).toBe(true);

    let cancelled = true;
    const controller = new AbortController();
    void timerWait(VERIFIED_REDIRECT_MS, controller.signal).then(() => {
      cancelled = false;
    });
    controller.abort();
    expect(vi.getTimerCount()).toBe(0);
    await Promise.resolve();
    expect(cancelled).toBe(true);
  });
});

describe("page text", () => {
  // SHOP-TASK-146 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…」与第 6 条「页面文字（国家码选项 +60、+65 除外）全部来自字典」：
  // 读取中、两个页签、读取失败、各提示与成功状态下，三种语言的文字都在当前语言的字典里。
  const states: Partial<LoginViewProps>[] = [
    { settings: LOADING_SETTINGS },
    { form: FILLED, forgotTo: "/privacy" },
    { tab: "sms", registerTo: "/privacy" },
    { tab: "sms", settings: SMS_OFF, guestTo: "/checkout" },
    { settings: FAILED, tab: "sms" },
    { form: passwordStep("login_failed", true)(FILLED) },
    { form: passwordStep("login_failed", false)(FILLED) },
    { form: passwordStep("phone_invalid", true)(FILLED) },
    { form: passwordStep("unavailable", true)(FILLED) },
    { form: passwordStep("network", true)(FILLED) },
    { form: passwordStep("ok", true)(FILLED), smsDone: "auth.verified_registered", tab: "sms" },
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
