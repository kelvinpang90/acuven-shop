import { createElement, isValidElement } from "react";
import type { FormEvent, ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_PASSWORD_URL } from "../api/memberAccount";
import type { SetPasswordReply } from "../api/memberAccount";
import type { MemberSessionInfo, MemberSessionState } from "../api/memberSession";
import { CSRF_HEADER } from "../api/pay";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, Link, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  loadSmsSwitch,
  passwordChangePath,
  passwordLongEnough,
  passwordStep,
  SETTINGS_START,
  SettingsPanel,
  smsEnabledOf,
  startSetting,
  submitPassword,
  typePassword,
  withSmsSwitch,
} from "./AccountSettingsPanel";
import type { SettingsChange, SettingsMoves, SettingsPanelProps, SettingsState } from "./AccountSettingsPanel";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的手机号、令牌与密码都是虚构值。
const MASKED = "+60 12•••• 678";
const TOKEN = "csrf-token-1";
const NEW_TOKEN = "csrf-token-2";
const PASSWORD = "s3cret-pass";
const SITE_SETTINGS_URL = "/api/site-settings";

// account.password_change 的去向：测试只把它当作 View 的去向。
const CHANGE_TO = "/forgot-password" as RoutePath;

const noop = () => undefined;

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function panelProps(overrides: Partial<SettingsPanelProps> = {}): SettingsPanelProps {
  return {
    settings: SETTINGS_START,
    hasPassword: false,
    phoneMasked: MASKED,
    changeTo: CHANGE_TO,
    logoutDisabled: false,
    loggingOut: false,
    onPassword: noop,
    onSubmit: noop,
    onLogout: noop,
    ...overrides,
  };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/account">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<SettingsPanelProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<SettingsPanel {...panelProps(overrides)} />, language));
}

type AnyElement = ReactElement<Record<string, unknown>>;

// 元素树：展开 View 里的子组件（直接调用，仍在语言与路由的上下文里），在 Link 处停下，用来取输入、表单与按钮的处理函数。
function expand(node: ReactNode): AnyElement[] {
  if (Array.isArray(node)) {
    return node.flatMap((child: ReactNode) => expand(child));
  }
  if (!isValidElement(node)) {
    return [];
  }
  const element = node as AnyElement;
  if (typeof element.type === "function" && element.type !== Link) {
    const component = element.type as (props: Record<string, unknown>) => ReactNode;
    return [element, ...expand(component(element.props))];
  }
  return [element, ...expand(element.props.children as ReactNode)];
}

function tree(overrides: Partial<SettingsPanelProps> = {}): AnyElement[] {
  let found: AnyElement[] = [];
  const probe = () => {
    found = expand(SettingsPanel(panelProps(overrides)));
    return null;
  };
  renderToStaticMarkup(wrap(createElement(probe), "en"));
  return found;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
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

function copyPattern(template: string): RegExp {
  const parts = template.split(/\{\w+\}/).map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${parts.join(".+")}$`);
}

function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

function text(key: CopyKey, language: Language = "en"): string {
  return COPY[key][language];
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type Reply = Response | "network";

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按地址各取一串回答，依次用掉；没有准备的请求一律失败。
function stubFetch(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const next = routes[url]?.shift();
      if (next === undefined) {
        return Promise.reject(new Error(`unexpected request ${url}`));
      }
      return next === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function bodyOf(init: RequestInit): unknown {
  const body: unknown = JSON.parse(typeof init.body === "string" ? init.body : "null");
  return body;
}

const MEMBER: MemberSessionState = { kind: "member", phoneMasked: MASKED, hasPassword: true, csrfToken: NEW_TOKEN };

// 页面的处理替身：按顺序记下 update、reread、session 与 login，并按改动推演设置段；reread 依次给出 rereads 里的状态。
function fakeMoves(initial: SettingsState, rereads: MemberSessionState[] = []) {
  const events: string[] = [];
  const sessions: MemberSessionInfo[] = [];
  let settings = initial;
  const moves: SettingsMoves = {
    update: (change: SettingsChange) => {
      settings = change(settings);
      events.push("update");
    },
    reread: () => {
      events.push("reread");
      return Promise.resolve(rereads.shift() ?? { kind: "failed" });
    },
    session: (info) => {
      sessions.push(info);
      events.push("session");
    },
    login: () => {
      events.push("login");
    },
  };
  return { moves, events, sessions, current: () => settings };
}

const TYPED: SettingsState = { ...SETTINGS_START, password: PASSWORD };

async function submit(reply: Reply, rereads: MemberSessionState[] = []) {
  const calls = stubFetch({ [MEMBER_PASSWORD_URL]: [reply] });
  const fake = fakeMoves(TYPED, rereads);
  await submitPassword(PASSWORD, TOKEN, new AbortController().signal, fake.moves);
  return { calls, ...fake };
}

describe("password content", () => {
  // UX P13 桌面线框「[account.password_title] / 未设密码：[account.password_none] / [auth.new_password] [P2 ______] [auth.password_rule] /
  // ( [account.password_set_submit] )」与验收第 3 条「未设密码时为 account.password_none、auth.new_password 密码输入框（照 ForgotPasswordPage.tsx
  // 的新密码输入写法）、auth.password_rule 提示与 account.password_set_submit 按钮」：依次为这几项，没有已设密码的内容；输入为 new-password 的密码框。
  it.each(LANGUAGES)("offers to set a first password when none is set in %s", (language) => {
    const html = render({}, language);
    expect(textNodes(html)).toEqual([
      text("account.settings", language),
      MASKED,
      text("account.password_title", language),
      text("account.password_none", language),
      text("auth.new_password", language),
      text("auth.password_rule", language),
      text("account.password_set_submit", language),
      text("common.nav_logout", language),
    ]);
    const input = /<input class="acs-input" id="([^"]+)" type="password" autoComplete="new-password" value="" aria-describedby="([^"]+)"\/>/i.exec(html);
    expect(input).not.toBeNull();
    expect(html).toContain(`<label class="acs-field__label" for="${input?.[1] ?? ""}">${copy("auth.new_password", language)}</label>`);
    expect(html).toContain(`<span class="acs-field__hint" id="${input?.[2] ?? ""}">${copy("auth.password_rule", language)}</span>`);
    expect(html).toContain(`<button class="acs-btn acs-btn--primary site-account__submit" type="submit" aria-busy="false">${copy("account.password_set_submit", language)}</button>`);
    expect(html).not.toContain(copy("account.password_exists", language));
    expect(html).not.toContain(copy("account.sms_paused_set_password", language));
  });

  // UX P13「未设密码时密码区另显示 [account.sms_paused_set_password]」与视觉稿 P13-desktop-settings-sms-off（acs-alert--info，在 account.password_none
  // 之后、新密码之前）：开关关闭且未设密码时显示；已设密码时不显示（验收第 3 条）。
  it.each(LANGUAGES)("explains the SMS pause only when no password is set in %s", (language) => {
    const off = { ...SETTINGS_START, smsEnabled: false };
    const html = render({ settings: off }, language);
    expect(html).toMatch(
      new RegExp(`<div class="acs-alert acs-alert--info site-account__paused" role="status"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg><span>${escapeRegExp(copy("account.sms_paused_set_password", language))}</span></div>`),
    );
    const texts = textNodes(html);
    expect(texts.indexOf(text("account.sms_paused_set_password", language))).toBe(texts.indexOf(text("account.password_none", language)) + 1);
    expect(texts.indexOf(text("auth.new_password", language))).toBe(texts.indexOf(text("account.sms_paused_set_password", language)) + 1);
    expect(render({ settings: off, hasPassword: true }, language)).not.toContain(copy("account.sms_paused_set_password", language));
  });

  // HANDOFF 0.63 第（7）条「设置段读取站点设置失败时按开关开启处理」与验收「站点设置读取完成前与读取失败时都按开关开启处理（不显示 account.sms_paused_set_password）」：
  // 初始状态为开启，读取失败（null）也是开启，读到关闭才关闭。
  it("treats the switch as on before and when reading it fails", () => {
    expect(SETTINGS_START.smsEnabled).toBe(true);
    expect(render()).not.toContain(copy("account.sms_paused_set_password"));
    expect(smsEnabledOf(null)).toBe(true);
    expect(smsEnabledOf({ sms_verification_enabled: false, whatsapp_contact_url: null, turnstile_site_key: null })).toBe(false);
    expect(smsEnabledOf({ sms_verification_enabled: true, whatsapp_contact_url: null, turnstile_site_key: null })).toBe(true);
    expect(withSmsSwitch(false)(SETTINGS_START)).toEqual({ ...SETTINGS_START, smsEnabled: false });
  });

  // 同一条：经 fetchSiteSettings 读取（带页面的 signal），读到的值交给页面；500、网络中断与开关不是布尔值都按开启交出；离开页面之后不交出。
  it.each<[string, Reply, boolean]>([
    ["off", json(200, { sms_verification_enabled: false }), false],
    ["on", json(200, { sms_verification_enabled: true }), true],
    ["a server error", new Response(null, { status: 500 }), true],
    ["a network failure", "network", true],
    ["a malformed switch", json(200, { sms_verification_enabled: "no" }), true],
  ])("hands over the switch when the read is %s", async (_name, reply, enabled) => {
    const calls = stubFetch({ [SITE_SETTINGS_URL]: [reply] });
    const signal = new AbortController().signal;
    const settle = vi.fn((smsEnabled: boolean) => {
      expect(typeof smsEnabled).toBe("boolean");
    });
    await loadSmsSwitch(signal, settle);
    expect(calls[0]?.init.signal).toBe(signal);
    expect(settle.mock.calls).toEqual([[enabled]]);
  });

  it("hands over nothing after leaving the page", async () => {
    stubFetch({ [SITE_SETTINGS_URL]: [json(200, { sms_verification_enabled: false })] });
    const gone = new AbortController();
    gone.abort();
    const settle = vi.fn((smsEnabled: boolean) => {
      expect(typeof smsEnabled).toBe("boolean");
    });
    await loadSmsSwitch(gone.signal, settle);
    expect(settle).not.toHaveBeenCalled();
  });

  // UX P13「已设密码：[account.password_exists] / ([account.password_change]) → P12 忘记密码流程」与验收第 3 条「指向 /forgot-password 的
  // account.password_change（照 CheckoutPage.tsx 的 loginPath 写法以 isRoutePath 判断）」：没有新密码输入与设置按钮；去向为 null 时不渲染链接。
  it.each(LANGUAGES)("points to the forgot-password flow when a password is set in %s", (language) => {
    const html = render({ hasPassword: true }, language);
    expect(textNodes(html)).toEqual([
      text("account.settings", language),
      MASKED,
      text("account.password_title", language),
      text("account.password_exists", language),
      text("account.password_change", language),
      text("common.nav_logout", language),
    ]);
    expect(html).toContain(`<a class="site-account__change" href="/forgot-password">${copy("account.password_change", language)}</a>`);
    expect(html).not.toContain(`type="password"`);
    const without = render({ hasPassword: true, changeTo: null }, language);
    expect(without).not.toContain(copy("account.password_change", language));
    expect(without).not.toContain("href=");
  });

  it("derives the change destination from the route table", () => {
    expect(passwordChangePath()).toBe(isRoutePath("/forgot-password") ? "/forgot-password" : null);
  });
});

describe("phone number and log-out", () => {
  // 验收第 5 条「设置段在手机顶部另显示遮掩号码（acs-num）与 common.nav_logout 按钮（照 P13-phone-settings），桌面设置段不显示」与
  // UX P13 手机线框「| [P1] <手机号部分遮盖> | [account.password_title] … | ([common.nav_logout]) |」：号码在段首、为手机限定；段标题 h2 只在桌面；
  // 退出按钮为手机限定的整宽次要按钮，在密码内容之后。
  it.each(LANGUAGES)("shows the masked number first and log-out after the password on phones in %s", (language) => {
    const html = render({}, language);
    expect(html).toMatch(
      new RegExp(`^<div class="site-account__settings"><h2 class="acs-display-m site-desktop-only">${escapeRegExp(copy("account.settings", language))}</h2><span class="acs-num acs-muted site-phone-only">${escapeRegExp(MASKED)}</span><h3`),
    );
    expect(html).toMatch(
      new RegExp(`</form><button class="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-account__signout" type="button" aria-busy="false">${escapeRegExp(copy("common.nav_logout", language))}</button></div>$`),
    );
  });

  // 同一条「两个退出按钮共用现有的退出处理与禁用条件」：按钮的禁用与 aria-busy 取自 props，点击交给 onLogout。
  it("shares the log-out handler and its disabled state", () => {
    const onLogout = vi.fn();
    const button = tree({ onLogout }).find((element) => element.type === "button" && element.props.type === "button");
    expect(button?.props.onClick).toBe(onLogout);
    expect(button?.props.disabled).toBe(false);
    expect(render({ logoutDisabled: true, loggingOut: true })).toContain(
      `<button class="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-account__signout" type="button" disabled="" aria-busy="true">`,
    );
  });
});

describe("setting a first password", () => {
  // 验收第 4 条「提交前长度不足 8 个字符时不发请求、以 auth.password_rule 作错误提示」与 HANDOFF 0.63「提交前按 auth.password_rule 检查长度不少于 8」：
  // 7 个字符不发请求、字段显示错误；按码点计数（8 个表情符号够，7 个不够）。
  it("checks the length before sending anything", async () => {
    expect(passwordLongEnough("1234567")).toBe(false);
    expect(passwordLongEnough("12345678")).toBe(true);
    expect(passwordLongEnough("😀".repeat(8))).toBe(true);
    expect(passwordLongEnough("😀".repeat(7))).toBe(false);
    const calls = stubFetch({});
    const { moves, events, current } = fakeMoves({ ...SETTINGS_START, password: "1234567" });
    await submitPassword("1234567", TOKEN, new AbortController().signal, moves);
    expect(calls).toHaveLength(0);
    expect(events).toEqual(["update"]);
    expect(current()).toEqual({ ...SETTINGS_START, password: "1234567", tooShort: true });
  });

  // 同一条「too_short 时以 auth.password_rule 作错误提示」：字段为错误状态，auth.password_rule 换成带图形的字段错误，输入标 aria-invalid。
  it.each(LANGUAGES)("shows auth.password_rule as the field error in %s", (language) => {
    const html = render({ settings: { ...SETTINGS_START, tooShort: true } }, language);
    expect(html).toContain(`<div class="acs-field acs-field--invalid site-account__field">`);
    expect(html).toContain(`aria-invalid="true"`);
    expect(html).toMatch(new RegExp(`<span class="acs-field__error" id="[^"]+"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg>${escapeRegExp(copy("auth.password_rule", language))}</span>`));
    expect(html).not.toContain("acs-field__hint");
  });

  // 同一条「以会话里的 CSRF 令牌经 SHOP-TASK-164 提交，进行中按钮禁用」与 DESIGN 1.14「权限与资料保护」第 5 条（写操作另须 CSRF 令牌）：
  // 请求头带令牌，请求体只有密码，地址里没有密码；进行中按钮禁用并标 aria-busy。
  it("sends the password with the session token and disables the button meanwhile", async () => {
    const { calls } = await submit(new Response(null, { status: 204 }), [MEMBER]);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(MEMBER_PASSWORD_URL);
    expect(calls[0]?.init.method).toBe("POST");
    expect(headerOf(calls[0]?.init ?? {}, CSRF_HEADER)).toBe(TOKEN);
    expect(bodyOf(calls[0]?.init ?? {})).toEqual({ password: PASSWORD });
    const busy = startSetting({ ...TYPED, notice: "common.error_retry", tooShort: true, done: true });
    expect(busy).toEqual({ ...TYPED, submitting: true });
    expect(render({ settings: busy })).toContain(`<button class="acs-btn acs-btn--primary site-account__submit" type="submit" disabled="" aria-busy="true">`);
  });

  // 同一条「done 时显示 account.password_set_done、清空输入并经读取对象重读会话（hasPassword 变为真后显示已设密码的内容，account.password_set_done
  // 留在其上方）」与 HANDOFF 0.63 第（7）条「设密码成功先显示 account.password_set_done 再重读会话」：先改动（已设置、输入清空），再重读并换上会话。
  it("shows account.password_set_done before rereading the session", async () => {
    const { events, sessions, current } = await submit(new Response(null, { status: 204 }), [MEMBER]);
    expect(events).toEqual(["update", "update", "reread", "session"]);
    expect(sessions).toEqual([MEMBER]);
    expect(current()).toEqual({ ...SETTINGS_START, done: true });
    for (const language of LANGUAGES) {
      const html = render({ settings: current(), hasPassword: true }, language);
      expect(html).toMatch(
        new RegExp(`<div class="acs-alert acs-alert--success" role="status"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg><span>${escapeRegExp(copy("account.password_set_done", language))}</span></div><p>${escapeRegExp(copy("account.password_exists", language))}</p>`),
      );
      // 重读完成之前（hasPassword 仍为假）提示同样在密码内容上方，输入已清空。
      const before = render({ settings: current() }, language);
      expect(before.indexOf(copy("account.password_set_done", language))).toBeLessThan(before.indexOf(copy("account.password_none", language)));
      expect(before).toContain(`value=""`);
    }
  });

  // 同一条「done 与 already_set 后的重读得到 guest 时用 replace 进入 /login，得到 failed 时保留当前内容并显示 common.error_retry」。
  it.each<[string, () => Response]>([
    ["done", () => new Response(null, { status: 204 })],
    ["already_set", () => json(409, { detail: "password_already_set" })],
  ])("leaves for /login or asks to retry when the reread after %s says so", async (_name, reply) => {
    const guest = await submit(reply(), [{ kind: "guest" }]);
    expect(guest.events.slice(-2)).toEqual(["reread", "login"]);
    expect(guest.sessions).toEqual([]);
    const failed = await submit(reply(), [{ kind: "failed" }]);
    expect(failed.events.slice(-2)).toEqual(["reread", "update"]);
    expect(failed.sessions).toEqual([]);
    expect(failed.current().notice).toBe("common.error_retry");
    expect(failed.current().submitting).toBe(false);
  });

  // 同一条「already_set 时重读会话转为已设密码的内容」与 HANDOFF 0.63「409 password_already_set 时重读会话（转为 account.password_exists）」：
  // 重读之后换上会话（hasPassword 为真），按钮恢复、输入清空，不显示 account.password_set_done。
  it("rereads the session when a password is already set", async () => {
    const { events, sessions, current } = await submit(json(409, { detail: "password_already_set" }), [MEMBER]);
    expect(events).toEqual(["update", "reread", "session", "update"]);
    expect(sessions).toEqual([MEMBER]);
    expect(current()).toEqual(SETTINGS_START);
  });

  // 同一条「too_short 时以 auth.password_rule 作错误提示」与 HANDOFF 0.63「422 password_too_short 时以 auth.password_rule 作错误提示」：不重读，按钮恢复，输入保留。
  it("marks the field when the server says the password is too short", async () => {
    const { events, current } = await submit(json(422, { detail: "password_too_short" }));
    expect(events).toEqual(["update", "update"]);
    expect(current()).toEqual({ ...TYPED, tooShort: true });
  });

  // 同一条「unavailable 显示 common.service_unavailable」「failed 显示 common.error_retry」「network 显示 common.network_check」与 HANDOFF 0.63
  // 「503 显示 common.service_unavailable」：不重读、不跳转，按钮恢复、输入保留，提示为对应的字典文字。
  it.each<[SetPasswordReply, CopyKey, Reply]>([
    ["unavailable", "common.service_unavailable", new Response(null, { status: 503 })],
    ["failed", "common.error_retry", new Response(null, { status: 500 })],
    ["network", "common.network_check", "network"],
  ])("shows the notice when setting is %s", async (reply, key, response) => {
    expect(passwordStep(reply)).toBe(key);
    const { events, current } = await submit(response);
    expect(events).toEqual(["update", "update"]);
    expect(current()).toEqual({ ...TYPED, notice: key });
    expect(render({ settings: current() })).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p>`);
  });

  // 同一条「signed_out 时重读会话并用 replace 进入 /login」：不论重读结果，重读之后都去 /login，不换上会话。
  it("rereads the session and leaves for /login when signed out", async () => {
    const { events, sessions } = await submit(json(401, { detail: "member_session_required" }), [{ kind: "guest" }]);
    expect(events).toEqual(["update", "reread", "login"]);
    expect(sessions).toEqual([]);
  });

  // 同一条「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时用 replace 进入 /login，重读得到 failed 时保留原令牌并显示 common.error_retry」。
  it("rereads the session after a csrf failure", async () => {
    const csrf = () => json(403, { detail: "csrf_failed" });
    const member = await submit(csrf(), [MEMBER]);
    expect(member.events).toEqual(["update", "reread", "session", "update"]);
    expect(member.sessions).toEqual([MEMBER]);
    expect(member.current()).toEqual({ ...TYPED, notice: "common.error_retry" });
    const guest = await submit(csrf(), [{ kind: "guest" }]);
    expect(guest.events).toEqual(["update", "reread", "login"]);
    const failed = await submit(csrf(), [{ kind: "failed" }]);
    expect(failed.events).toEqual(["update", "reread", "update"]);
    expect(failed.sessions).toEqual([]);
    expect(failed.current()).toEqual({ ...TYPED, notice: "common.error_retry" });
  });

  it("maps every reply", () => {
    expect(passwordStep("done")).toBe("done");
    expect(passwordStep("already_set")).toBe("reread");
    expect(passwordStep("signed_out")).toBe("login");
    expect(passwordStep("csrf")).toBe("csrf");
    expect(passwordStep("too_short")).toBe("too_short");
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：
  // 请求带页面的 signal，回答到达前已离开时不再改动、不重读；重读期间离开时不换上会话、不跳转。
  it("stops after leaving the page", async () => {
    const calls = stubFetch({ [MEMBER_PASSWORD_URL]: [new Response(null, { status: 204 })] });
    const gone = new AbortController();
    gone.abort();
    const left = fakeMoves(TYPED, [MEMBER]);
    await submitPassword(PASSWORD, TOKEN, gone.signal, left.moves);
    expect(calls[0]?.init.signal).toBe(gone.signal);
    expect(left.events).toEqual(["update"]);

    stubFetch({ [MEMBER_PASSWORD_URL]: [json(401, { detail: "member_session_required" })] });
    const page = new AbortController();
    const leaving = fakeMoves(TYPED);
    leaving.moves.reread = () => {
      page.abort();
      return Promise.resolve({ kind: "guest" });
    };
    await submitPassword(PASSWORD, TOKEN, page.signal, leaving.moves);
    expect(leaving.events).toEqual(["update"]);
  });

  // 验收第 4 条「密码只在输入框与请求体里，不进地址、日志或浏览器存储」：提交前后不写 localStorage 与 sessionStorage、不写日志，地址里没有密码。
  it("keeps the password out of storage, logs and the address", async () => {
    const setItem = vi.fn((key: string, value: string) => {
      expect(`${key}${value}`).not.toContain(PASSWORD);
    });
    vi.stubGlobal("localStorage", { getItem: () => null, setItem, removeItem: noop });
    vi.stubGlobal("sessionStorage", { getItem: () => null, setItem, removeItem: noop });
    const log = vi.spyOn(console, "log");
    const error = vi.spyOn(console, "error");
    const { calls } = await submit(new Response(null, { status: 204 }), [MEMBER]);
    expect(setItem).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    expect(calls.map((call) => call.url)).toEqual([MEMBER_PASSWORD_URL]);
    expect(calls[0]?.url).not.toContain(PASSWORD);
  });

  // 验收第 6 条「传给事件属性的处理函数不返回 Promise」与 ForgotPasswordPage.tsx 的写法：输入交出新密码，表单提交先 preventDefault 再交给页面，
  // 两个处理函数都返回 undefined；输入的改动只换密码。
  it("hands typing and submitting to the page", () => {
    const onPassword = vi.fn((password: string) => {
      expect(password).toBe(PASSWORD);
    });
    const onSubmit = vi.fn(noop);
    const elements = tree({ onPassword, onSubmit });
    const input = elements.find((element) => element.type === "input");
    const form = elements.find((element) => element.type === "form");
    const change = input?.props.onChange as (event: { target: { value: string } }) => unknown;
    expect(change({ target: { value: PASSWORD } })).toBeUndefined();
    const preventDefault = vi.fn(noop);
    const send = form?.props.onSubmit as (event: FormEvent<HTMLFormElement>) => unknown;
    expect(send({ preventDefault } as unknown as FormEvent<HTMLFormElement>)).toBeUndefined();
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(onPassword).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(typePassword(PASSWORD)({ ...SETTINGS_START, tooShort: true })).toEqual({ ...SETTINGS_START, password: PASSWORD, tooShort: true });
  });
});

describe("dictionary", () => {
  // 验收第 1 条「copy.ts 按 UX-COPY 0.14 逐字加入」与验收「页面文字全部来自字典」：各状态下，除遮掩后的手机号外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides the masked number in %s", (language) => {
    const states: Partial<SettingsPanelProps>[] = [
      {},
      { settings: { ...SETTINGS_START, smsEnabled: false } },
      { settings: { ...SETTINGS_START, tooShort: true } },
      { settings: { ...SETTINGS_START, submitting: true }, logoutDisabled: true, loggingOut: true },
      { settings: { ...SETTINGS_START, done: true } },
      { settings: { ...SETTINGS_START, notice: "common.service_unavailable" } },
      { settings: { ...SETTINGS_START, done: true }, hasPassword: true },
      { hasPassword: true, changeTo: null },
    ];
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        expect(value === MASKED || patterns.some((pattern) => pattern.test(value)), value).toBe(true);
      }
    }
  });

  // UX「阅读说明」不写死文字：设置段用到的键在字典里，三语都有（逐字核对由 copy.test.ts 读 UX-COPY 守住）。
  it("has every key this section uses", () => {
    const keys: CopyKey[] = [
      "account.settings",
      "account.password_title",
      "account.password_none",
      "account.password_set_submit",
      "account.password_set_done",
      "account.password_exists",
      "account.password_change",
      "account.sms_paused_set_password",
      "auth.new_password",
      "auth.password_rule",
      "common.nav_logout",
      "common.service_unavailable",
      "common.error_retry",
      "common.network_check",
    ];
    for (const key of keys) {
      for (const language of LANGUAGES) {
        expect(COPY[key][language], key).not.toBe("");
      }
    }
    expect(formatCopy(COPY["account.password_set_done"].en)).toBe("Password set. You can now log in with your password or an SMS code.");
  });
});
