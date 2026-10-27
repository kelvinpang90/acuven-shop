import { createElement, isValidElement } from "react";
import type { FormEvent, ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_DELETE_URL, MEMBER_PASSWORD_URL } from "../api/memberAccount";
import type { DeleteConfirmReply, SetPasswordReply } from "../api/memberAccount";
import { clearMemberOrder, handOffMemberOrder, memberOrderInView } from "../api/memberOrders";
import { clearMemberPayOrder, MEMBER_PAY_ORDER_KEY, stashedMemberPayOrder, stashMemberPayOrder } from "../api/memberPay";
import type { MemberSessionInfo, MemberSessionState } from "../api/memberSession";
import { CSRF_HEADER } from "../api/pay";
import type { DeleteSmsStep } from "../components/SmsVerify";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, Link, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  askDeletion,
  canConfirmDeletion,
  DELETED_DELAY_MS,
  deleteAccount,
  DELETION_START,
  deletionMode,
  deletionPassword,
  deletionStep,
  keepAccount,
  loadSmsSwitch,
  passwordChangePath,
  passwordLongEnough,
  passwordStep,
  SETTINGS_START,
  SettingsPanel,
  SITE_LOADING,
  smsEnabledOf,
  startConfirming,
  startSetting,
  submitPassword,
  takeDeleteSmsStep,
  typeDeletePassword,
  typePassword,
  withSmsSwitch,
} from "./AccountSettingsPanel";
import type { DeletionMoves, DeletionState, SettingsChange, SettingsMoves, SettingsPanelProps, SettingsState, SiteState } from "./AccountSettingsPanel";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // SHOP-TASK-167 的注销测试会写入会员订单号的交接与当前订单暂存（模块变量），每条之后复原。
  clearMemberOrder();
  clearMemberPayOrder();
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

// SHOP-TASK-167：设置段末尾多了注销部分，View 另带 CSRF 令牌、站点设置、注销部分的状态与五个处理（随 props 扩展补上初始值：站点设置读取中）。
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
    csrfToken: TOKEN,
    site: SITE_LOADING,
    deletion: DELETION_START,
    onDeletePassword: noop,
    onDeleteAsk: noop,
    onDeleteKeep: noop,
    onDeleteConfirm: noop,
    onDeleteStep: noop,
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
      // SHOP-TASK-167：末尾补上注销部分的初始状态（站点设置读取中，只有标题与说明）。
      text("account.delete", language),
      text("account.delete_warning", language),
    ]);
    // 服务端渲染的属性顺序不在意图之内：先取出整个密码输入标签，再逐项断言属性。
    const input = /<input[^>]*type="password"[^>]*\/>/.exec(html)?.[0] ?? "";
    expect(input).toContain(`class="acs-input"`);
    expect(input).toMatch(/autocomplete="new-password"/i);
    expect(input).toContain(`value=""`);
    const id = /\sid="([^"]+)"/.exec(input)?.[1];
    const describedBy = /\saria-describedby="([^"]+)"/.exec(input)?.[1];
    expect(id).toBeDefined();
    expect(describedBy).toBeDefined();
    expect(html).toContain(`<label class="acs-field__label" for="${id ?? ""}">${copy("auth.new_password", language)}</label>`);
    expect(html).toContain(`<span class="acs-field__hint" id="${describedBy ?? ""}">${copy("auth.password_rule", language)}</span>`);
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
  // SHOP-TASK-167：同时交出注销部分的站点设置（第二个值：读到时为开关与人机挑战公开 key，读取失败为 failed），只补了这一项的期望。
  it.each<[string, Reply, boolean, SiteState]>([
    ["off", json(200, { sms_verification_enabled: false }), false, { status: "ready", smsEnabled: false, siteKey: null }],
    ["on", json(200, { sms_verification_enabled: true, turnstile_site_key: "site-key-1" }), true, { status: "ready", smsEnabled: true, siteKey: "site-key-1" }],
    ["a server error", new Response(null, { status: 500 }), true, { status: "failed" }],
    ["a network failure", "network", true, { status: "failed" }],
    ["a malformed switch", json(200, { sms_verification_enabled: "no" }), true, { status: "failed" }],
  ])("hands over the switch when the read is %s", async (_name, reply, enabled, site) => {
    const calls = stubFetch({ [SITE_SETTINGS_URL]: [reply] });
    const signal = new AbortController().signal;
    const settle = vi.fn((smsEnabled: boolean, read: SiteState) => {
      expect(typeof smsEnabled).toBe("boolean");
      expect(typeof read.status).toBe("string");
    });
    await loadSmsSwitch(signal, settle);
    expect(calls[0]?.init.signal).toBe(signal);
    expect(settle.mock.calls).toEqual([[enabled, site]]);
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新」：读取开关时页面已离开，就不再把开关交给页面。
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
      // SHOP-TASK-167：末尾补上注销部分的初始状态（站点设置读取中，只有标题与说明）。
      text("account.delete", language),
      text("account.delete_warning", language),
    ]);
    expect(html).toContain(`<a class="site-account__change" href="/forgot-password">${copy("account.password_change", language)}</a>`);
    expect(html).not.toContain(`type="password"`);
    const without = render({ hasPassword: true, changeTo: null }, language);
    // 中文的 account.password_exists 本身含 account.password_change 的文字，所以只断言没有链接元素。
    expect(without).not.toContain(`>${copy("account.password_change", language)}</a>`);
    expect(without).not.toContain("site-account__change");
    expect(without).not.toContain("href=");
  });

  // 验收第 3 条「指向 /forgot-password 的 account.password_change（照 CheckoutPage.tsx 的 loginPath 写法以 isRoutePath 判断）」：
  // 去向取自路由表，不在路由表里时为 null。
  it("derives the change destination from the route table", () => {
    expect(passwordChangePath()).toBe(isRoutePath("/forgot-password") ? "/forgot-password" : null);
  });
});

describe("phone number and log-out", () => {
  // 验收第 5 条「设置段在手机顶部另显示遮掩号码（acs-num）与 common.nav_logout 按钮（照 P13-phone-settings），桌面设置段不显示」与
  // UX P13 手机线框「| [P1] <手机号部分遮盖> | [account.password_title] … | ([common.nav_logout]) |」：号码在段首、为手机限定；段标题 h2 只在桌面；
  // 退出按钮为手机限定的整宽次要按钮，在密码内容之后。
  // SHOP-TASK-167：UX P13 手机线框里注销部分排在 ([common.nav_logout]) 之后，退出按钮之后改为接注销部分（原先以退出按钮结尾）。
  it.each(LANGUAGES)("shows the masked number first and log-out after the password on phones in %s", (language) => {
    const html = render({}, language);
    expect(html).toMatch(
      new RegExp(`^<div class="site-account__settings"><h2 class="acs-display-m site-desktop-only">${escapeRegExp(copy("account.settings", language))}</h2><span class="acs-num acs-muted site-phone-only">${escapeRegExp(MASKED)}</span><h3`),
    );
    expect(html).toMatch(
      new RegExp(
        `</form><button class="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-account__signout" type="button" aria-busy="false">${escapeRegExp(copy("common.nav_logout", language))}</button><div class="site-account__delete"[^>]*><h3 class="acs-display-s">${escapeRegExp(copy("account.delete", language))}</h3>`,
      ),
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

  // 验收第 4 条首次设置的各结果（done 显示 account.password_set_done 并重读、already_set 重读、signed_out 去 /login、csrf 重读换令牌、
  // too_short 以 auth.password_rule 提示）：每种回答归到对应的下一步。
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

// SHOP-TASK-167 注销部分用到的站点设置、当前密码与状态（虚构值）。
const SITE_KEY = "site-key-1";
const SITE_ON: SiteState = { status: "ready", smsEnabled: true, siteKey: SITE_KEY };
const SITE_OFF: SiteState = { status: "ready", smsEnabled: false, siteKey: SITE_KEY };
const CURRENT_PASSWORD = "current-pass-1";
const APPROVED: DeletionState = { ...DELETION_START, approved: true };
const TYPED_CURRENT: DeletionState = { ...DELETION_START, password: CURRENT_PASSWORD };
const siteOn = () => json(200, { sms_verification_enabled: true, turnstile_site_key: SITE_KEY });
const siteOff = () => json(200, { sms_verification_enabled: false, turnstile_site_key: SITE_KEY });

// 注销部分：设置段的最后一项（site-account__delete 起，到设置段的结束标签之前）。
function deletionPart(html: string): string {
  const start = html.indexOf(`<div class="site-account__delete"`);
  if (start < 0) {
    throw new Error("no deletion part");
  }
  return html.slice(start, html.length - "</div>".length);
}

function deletionTexts(overrides: Partial<SettingsPanelProps>, language: Language = "en"): string[] {
  return textNodes(deletionPart(render(overrides, language)));
}

// 注销部分的处理替身：按顺序记下 update、site、reread、session、login 与 home，并按改动推演注销部分；reread 依次给出 rereads 里的状态。
function fakeDeletionMoves(initial: DeletionState, rereads: MemberSessionState[] = []) {
  const events: string[] = [];
  const sessions: MemberSessionInfo[] = [];
  const sites: SiteState[] = [];
  let deletion = initial;
  const moves: DeletionMoves = {
    update: (change) => {
      deletion = change(deletion);
      events.push("update");
    },
    site: (site) => {
      sites.push(site);
      events.push("site");
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
    home: () => {
      events.push("home");
    },
  };
  return { moves, events, sessions, sites, current: () => deletion };
}

// 5 秒等待的替身：记下等待的毫秒数，立即结束。
function fakeDelay() {
  const waits: number[] = [];
  const delay = vi.fn((ms: number, signal: AbortSignal) => {
    waits.push(ms);
    expect(signal.aborted).toBe(false);
    return Promise.resolve();
  });
  return { delay, waits };
}

// 确认注销一次：确认的回答为 reply，重读站点设置依次取 sites 里的回答。
async function confirm(reply: Reply, password: string | null, rereads: MemberSessionState[] = [], sites: Reply[] = [], start: DeletionState = APPROVED) {
  const calls = stubFetch({ [MEMBER_DELETE_URL]: [reply], [SITE_SETTINGS_URL]: sites });
  const fake = fakeDeletionMoves(start, rereads);
  const { delay, waits } = fakeDelay();
  await deleteAccount(password, TOKEN, new AbortController().signal, fake.moves, delay);
  return { calls, waits, ...fake };
}

// 本标签页 sessionStorage 的替身：只记当前订单暂存这类键值。
function stubSessionStorage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  });
  return store;
}

describe("deletion part (SHOP-TASK-167)", () => {
  // UX P13 桌面线框「[account.delete] / [account.delete_warning] / [account.delete_verify] / <V1：用途「注销确认」，号码固定为本账号>」与验收
  // 「开关开启时的注销——account.delete、account.delete_warning、account.delete_verify 与注销确认用途的 V1」：V1 显示本账号的遮掩号码与 auth.sms_scope，
  // 验证通过之前没有 account.delete_confirm。
  it.each(LANGUAGES)("asks for SMS verification when the switch is on in %s", (language) => {
    expect(deletionTexts({ site: SITE_ON }, language)).toEqual([
      text("account.delete", language),
      text("account.delete_warning", language),
      text("account.delete_verify", language),
      text("auth.phone", language),
      MASKED,
      text("auth.sms_scope", language),
      text("auth.challenge", language),
      text("auth.send_code", language),
    ]);
    // 中文的 account.delete_verify 本身含 account.delete_confirm 的文字，所以只断言没有确认按钮。
    expect(deletionPart(render({ site: SITE_ON }, language))).not.toContain("site-account__confirm");
  });

  // 同一条「验证通过后出现 account.delete_confirm 按钮」、UX P13「验证通过后才出现 [account.delete_confirm]」与 HANDOFF 0.63（Kelvin 2026-10-24 第（2）项）
  // 「注销的短信验证通过后不加『验证成功』提示，只出现 account.delete_confirm」：V1 收起，只剩视觉稿的 danger 按钮；进行中禁用。
  it.each(LANGUAGES)("shows only account.delete_confirm once the SMS check passes in %s", (language) => {
    expect(deletionTexts({ site: SITE_ON, deletion: APPROVED }, language)).toEqual([
      text("account.delete", language),
      text("account.delete_warning", language),
      text("account.delete_verify", language),
      text("account.delete_confirm", language),
    ]);
    expect(deletionPart(render({ site: SITE_ON, deletion: APPROVED }, language))).toContain(
      `<button class="acs-btn acs-btn--danger site-account__confirm" type="button" aria-busy="false">${copy("account.delete_confirm", language)}</button>`,
    );
    expect(canConfirmDeletion("sms", DELETION_START)).toBe(false);
    expect(canConfirmDeletion("sms", APPROVED)).toBe(true);
    expect(deletionPart(render({ site: SITE_ON, deletion: startConfirming(APPROVED) }, language))).toContain(
      `<button class="acs-btn acs-btn--danger site-account__confirm" type="button" disabled="" aria-busy="true">`,
    );
  });

  // 验收「开关关闭时的注销——不显示 account.delete_verify 与 V1；已设密码时为 account.delete_verify_password、auth.password 密码输入框与
  // account.delete_confirm（密码为空时按钮禁用），确认时带密码」与视觉稿 P13-desktop-settings-sms-off：当前密码为 current-password 的密码框。
  it.each(LANGUAGES)("asks for the current password when the switch is off and a password is set in %s", (language) => {
    const html = deletionPart(render({ site: SITE_OFF, hasPassword: true }, language));
    expect(textNodes(html)).toEqual([
      text("account.delete", language),
      text("account.delete_warning", language),
      text("account.delete_verify_password", language),
      text("auth.password", language),
      text("account.delete_confirm", language),
    ]);
    expect(html).not.toContain(copy("account.delete_verify", language));
    expect(html).not.toContain("site-sms");
    const input = /<input[^>]*type="password"[^>]*\/>/.exec(html)?.[0] ?? "";
    expect(input).toMatch(/autocomplete="current-password"/i);
    expect(input).toContain(`value=""`);
    expect(html).toContain(`<button class="acs-btn acs-btn--danger site-account__confirm" type="submit" disabled="" aria-busy="false">`);
    const typed = deletionPart(render({ site: SITE_OFF, hasPassword: true, deletion: TYPED_CURRENT }, language));
    expect(typed).toContain(`<button class="acs-btn acs-btn--danger site-account__confirm" type="submit" aria-busy="false">`);
    expect(deletionPassword("password", TYPED_CURRENT)).toBe(CURRENT_PASSWORD);
    expect(deletionPassword("sms", TYPED_CURRENT)).toBeNull();
    expect(deletionPassword("session", TYPED_CURRENT)).toBeNull();
  });

  // 同一条「password_wrong 时在密码框下方显示 account.delete_password_wrong，rate_limited 时显示 common.rate_limited」与 HANDOFF 0.63（Kelvin 2026-10-24
  // 第（3）项）「照画板显示在密码框下方，不改为与 P12 相同的整条提示」：错误在密码字段之内、输入之后，输入标 aria-invalid 并指向它。
  it.each(LANGUAGES)("shows a wrong password under the field in %s", (language) => {
    const html = deletionPart(render({ site: SITE_OFF, hasPassword: true, deletion: { ...TYPED_CURRENT, passwordWrong: true } }, language));
    const field = /<div class="acs-field acs-field--invalid site-account__field">[\s\S]*?<\/div>/.exec(html)?.[0] ?? "";
    const errorId = /<span class="acs-field__error" id="([^"]+)">/.exec(field)?.[1] ?? "missing";
    expect(field).toMatch(new RegExp(`<input[^>]*aria-invalid="true"[^>]*/><span class="acs-field__error" id="${escapeRegExp(errorId)}"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg>${escapeRegExp(copy("account.delete_password_wrong", language))}</span></div>$`));
    expect(field).toContain(`aria-describedby="${errorId}"`);
    expect(html).not.toContain(`role="alert"`);
    const limited = deletionPart(render({ site: SITE_OFF, hasPassword: true, deletion: { ...TYPED_CURRENT, notice: "common.rate_limited" } }, language));
    expect(limited).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.rate_limited", language)}</p>`);
  });

  // 验收「未设密码时为 account.delete_verify_session 与 account.delete_confirm，点后出现确认框（role 为 alertdialog，account.delete_session_confirm、
  // account.delete_confirm 与 account.delete_keep）」与视觉稿 P13-desktop-settings-sms-off 的确认框（account.delete_keep 在前、danger 的 account.delete_confirm 在后）。
  it.each(LANGUAGES)("confirms in a dialog when the switch is off and no password is set in %s", (language) => {
    const closed = deletionPart(render({ site: SITE_OFF }, language));
    expect(textNodes(closed)).toEqual([
      text("account.delete", language),
      text("account.delete_warning", language),
      text("account.delete_verify_session", language),
      text("account.delete_confirm", language),
    ]);
    expect(closed).not.toContain("alertdialog");
    expect(closed).not.toContain("<input");
    const open = deletionPart(render({ site: SITE_OFF, deletion: askDeletion(DELETION_START) }, language));
    const dialog = /<div class="acs-dialog" role="alertdialog" aria-modal="true" aria-labelledby="([^"]+)">([\s\S]*)$/.exec(open);
    const labelId = dialog?.[1] ?? "missing";
    expect(dialog?.[2]).toMatch(
      new RegExp(
        `^<p id="${escapeRegExp(labelId)}">${escapeRegExp(copy("account.delete_session_confirm", language))}</p><div class="acs-dialog__actions"><button class="acs-btn acs-btn--secondary" type="button"[^>]*>${escapeRegExp(copy("account.delete_keep", language))}</button><button class="acs-btn acs-btn--danger" type="button" aria-busy="false">${escapeRegExp(copy("account.delete_confirm", language))}</button></div></div>`,
      ),
    );
    // 确认框打开时，原先的 account.delete_confirm 禁用（确认框里的才确认注销）。
    expect(open).toContain(`<button class="acs-btn acs-btn--danger site-account__confirm" type="button" disabled="">`);
  });

  // 同一条「确认框里的 account.delete_confirm 才不带密码确认注销，account.delete_keep 关闭确认框」：确认框外的按钮只打开确认框，能否确认只看确认框是否打开。
  it("only confirms from inside the dialog", () => {
    expect(canConfirmDeletion("session", DELETION_START)).toBe(false);
    const asking = askDeletion(DELETION_START);
    expect(canConfirmDeletion("session", asking)).toBe(true);
    expect(keepAccount(asking)).toEqual(DELETION_START);
    expect(canConfirmDeletion("session", startConfirming(asking))).toBe(false);
    expect(canConfirmDeletion("password", DELETION_START)).toBe(false);
    expect(canConfirmDeletion("password", TYPED_CURRENT)).toBe(true);
    expect(canConfirmDeletion("loading", APPROVED)).toBe(false);
    expect(canConfirmDeletion("failed", APPROVED)).toBe(false);
    expect(canConfirmDeletion("sms", { ...APPROVED, deleted: true })).toBe(false);
  });

  // 验收「传给事件属性的处理函数不返回 Promise」：确认框的两个按钮直接用页面的处理，Escape 也关闭确认框（视觉稿标注「Keep my account 或 Escape 关闭」），
  // 其他按键不处理；打开确认框的按钮用 onDeleteAsk。
  it("hands the dialog buttons and Escape to the page", () => {
    const onDeleteAsk = vi.fn(noop);
    const onDeleteKeep = vi.fn(noop);
    const onDeleteConfirm = vi.fn(noop);
    const elements = tree({ site: SITE_OFF, deletion: askDeletion(DELETION_START), onDeleteAsk, onDeleteKeep, onDeleteConfirm });
    const buttons = elements.filter((element) => element.type === "button" && element.props.type === "button");
    // 退出、打开确认框、account.delete_keep 与 account.delete_confirm。
    expect(buttons.map((element) => element.props.onClick)).toEqual([noop, onDeleteAsk, onDeleteKeep, onDeleteConfirm]);
    const overlay = elements.find((element) => element.props.className === "site-account__overlay");
    const keyDown = overlay?.props.onKeyDown as (event: { key: string }) => unknown;
    expect(keyDown({ key: "Enter" })).toBeUndefined();
    expect(onDeleteKeep).not.toHaveBeenCalled();
    expect(keyDown({ key: "Escape" })).toBeUndefined();
    expect(onDeleteKeep).toHaveBeenCalledTimes(1);
  });

  // 同一条：当前密码的输入交出新值，表单提交先 preventDefault 再交给页面确认，两个处理函数都返回 undefined；输入的改动只换当前密码。
  it("hands typing and submitting the current password to the page", () => {
    const onDeletePassword = vi.fn((password: string) => {
      expect(password).toBe(CURRENT_PASSWORD);
    });
    const onDeleteConfirm = vi.fn(noop);
    const elements = tree({ site: SITE_OFF, hasPassword: true, onDeletePassword, onDeleteConfirm });
    const input = elements.find((element) => element.type === "input" && element.props.autoComplete === "current-password");
    const form = elements.filter((element) => element.type === "form").at(-1);
    const change = input?.props.onChange as (event: { target: { value: string } }) => unknown;
    expect(change({ target: { value: CURRENT_PASSWORD } })).toBeUndefined();
    const preventDefault = vi.fn(noop);
    const send = form?.props.onSubmit as (event: FormEvent<HTMLFormElement>) => unknown;
    expect(send({ preventDefault } as unknown as FormEvent<HTMLFormElement>)).toBeUndefined();
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(onDeletePassword).toHaveBeenCalledTimes(1);
    expect(onDeleteConfirm).toHaveBeenCalledTimes(1);
    expect(typeDeletePassword(CURRENT_PASSWORD)({ ...DELETION_START, passwordWrong: true })).toEqual({ ...DELETION_START, password: CURRENT_PASSWORD, passwordWrong: true });
  });

  // 验收「SmsVerify 的注销确认用途——号码固定为本账号」「发送以……调用方给出的 CSRF 令牌」与「回到 V1 起点重新验证」：嵌入的 V1 用途为 delete_account，
  // 号码为会话里的遮掩号码，令牌为会话的 CSRF 令牌，公开 key 取自站点设置，结果交给 onDeleteStep；每一轮以 round 为 key 重新挂载。
  it("embeds the deletion purpose of V1 with the session's number and token", () => {
    const onDeleteStep = vi.fn((step: DeleteSmsStep) => {
      expect(step).toBe("verified");
    });
    const elements = tree({ site: SITE_ON, deletion: { ...DELETION_START, round: 2 }, onDeleteStep });
    const sms = elements.find((element) => element.props.purpose === "delete_account");
    expect(sms?.key).toBe("2");
    expect(sms?.props).toMatchObject({ smsEnabled: true, siteKey: SITE_KEY, phoneMasked: MASKED, csrfToken: TOKEN, onDeleteStep });
  });

  // HANDOFF 0.63 第（7）条「注销时站点设置读取失败只显示 common.error_retry」与验收「站点设置读取失败时注销部分只显示 common.error_retry」：没有标题、说明与任何按钮。
  it.each(LANGUAGES)("shows only common.error_retry when the site settings failed in %s", (language) => {
    expect(deletionPart(render({ site: { status: "failed" } }, language))).toBe(
      `<div class="site-account__delete"><p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry", language)}</p></div>`,
    );
  });

  // 站点设置读取完成前（确认方式未定）只有标题与说明，注销部分标 aria-busy；不按开启显示 V1（读到之前不知道开关与公开 key）。
  it("waits for the site settings before choosing how to confirm", () => {
    const html = deletionPart(render({ site: SITE_LOADING }));
    expect(html).toMatch(/^<div class="site-account__delete" aria-busy="true"><h3/);
    expect(textNodes(html)).toEqual([text("account.delete"), text("account.delete_warning")]);
    expect(deletionMode(SITE_LOADING, true)).toBe("loading");
    expect(deletionMode({ status: "failed" }, true)).toBe("failed");
    expect(deletionMode(SITE_ON, true)).toBe("sms");
    expect(deletionMode(SITE_ON, false)).toBe("sms");
    expect(deletionMode(SITE_OFF, true)).toBe("password");
    expect(deletionMode(SITE_OFF, false)).toBe("session");
  });

  // 验收「deleted 时注销部分换成 account.deleted（其余各段不再可操作）」与视觉稿 P13-desktop-settings「注销完成」的 success 提示：注销部分只剩 account.deleted，
  // 密码部分的表单与 account.password_change 为 inert。
  it.each(LANGUAGES)("replaces the deletion part with account.deleted in %s", (language) => {
    const deleted: DeletionState = { ...APPROVED, deleted: true };
    const html = render({ site: SITE_ON, deletion: deleted }, language);
    expect(deletionPart(html)).toMatch(
      new RegExp(`^<div class="site-account__delete"><div class="acs-alert acs-alert--success" role="status"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg><span>${escapeRegExp(copy("account.deleted", language))}</span></div></div>$`),
    );
    expect(html).toContain(`<form class="site-account__password" inert=""`);
    expect(render({ site: SITE_ON, deletion: deleted, hasPassword: true }, language)).toContain(`<a class="site-account__change" inert="" href="/forgot-password">`);
    expect(render({ site: SITE_ON })).not.toContain("inert");
  });

  // 验收「页面文字全部来自字典」：注销部分各状态下，除遮掩后的手机号外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in the deletion part in %s", (language) => {
    const states: Partial<SettingsPanelProps>[] = [
      { site: SITE_LOADING },
      { site: { status: "failed" } },
      { site: SITE_ON },
      { site: { status: "ready", smsEnabled: true, siteKey: null } },
      { site: SITE_ON, deletion: APPROVED },
      { site: SITE_ON, deletion: { ...APPROVED, notice: "common.service_unavailable" } },
      { site: SITE_OFF, hasPassword: true },
      { site: SITE_OFF, hasPassword: true, deletion: { ...TYPED_CURRENT, passwordWrong: true } },
      { site: SITE_OFF, hasPassword: true, deletion: { ...TYPED_CURRENT, notice: "common.rate_limited" } },
      { site: SITE_OFF },
      { site: SITE_OFF, deletion: askDeletion(DELETION_START) },
      { site: SITE_OFF, deletion: { ...DELETION_START, notice: "common.network_check" } },
      { site: SITE_ON, deletion: { ...APPROVED, deleted: true } },
    ];
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      const html = render(state, language);
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(value === MASKED || patterns.some((pattern) => pattern.test(value)), value).toBe(true);
      }
    }
  });
});

describe("confirming the deletion (SHOP-TASK-167)", () => {
  // 验收「点后经 SHOP-TASK-164 确认注销（不带密码）」、DESIGN 1.14「权限与资料保护」第 5 条（写操作另须 CSRF 令牌）与第 7 条（注销时清除暂存）、
  // HANDOFF 0.63（Kelvin 2026-10-26 第（1）项）「account.deleted 留在 P13 显示 5 秒，再用 replace 跳到首页」与验收「deleted 时……同时清除 memberOrders.ts
  // 交接的订单号与 SHOP-TASK-158 的当前订单暂存、经读取对象重读会话……5 秒后用 replace 前往首页」：请求体为空对象、请求头带令牌；先换成 account.deleted，
  // 交接与暂存（模块变量与 sessionStorage）都已清除，重读会话之后等待 5 秒再前往首页。
  it("confirms without a password, clears the hand-off and the stash and leaves after 5 seconds", async () => {
    const store = stubSessionStorage();
    handOffMemberOrder("K7Q29MXAB6TN2RJD");
    stashMemberPayOrder("K7Q29MXAB6TN2RJD");
    expect(store.get(MEMBER_PAY_ORDER_KEY)).toBe("K7Q29MXAB6TN2RJD");
    const { calls, events, waits, current } = await confirm(new Response(null, { status: 204 }), null, [{ kind: "guest" }]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_DELETE_URL]);
    expect(calls[0]?.init.method).toBe("POST");
    expect(headerOf(calls[0]?.init ?? {}, CSRF_HEADER)).toBe(TOKEN);
    expect(bodyOf(calls[0]?.init ?? {})).toEqual({});
    expect(events).toEqual(["update", "update", "reread", "home"]);
    expect(DELETED_DELAY_MS).toBe(5000);
    expect(waits).toEqual([5000]);
    expect(current()).toMatchObject({ deleted: true, confirming: false });
    expect(memberOrderInView()).toBeNull();
    expect(stashedMemberPayOrder()).toBeNull();
    expect(store.has(MEMBER_PAY_ORDER_KEY)).toBe(false);
  });

  // 验收「已设密码时……确认时带密码」与「密码与验证码只在输入框与请求体里」：请求体只有 password，地址里没有密码；不写浏览器存储与日志。
  it("sends the current password only in the request body", async () => {
    const setItem = vi.fn((key: string, value: string) => {
      expect(`${key}${value}`).not.toContain(CURRENT_PASSWORD);
    });
    vi.stubGlobal("localStorage", { getItem: () => null, setItem, removeItem: noop });
    vi.stubGlobal("sessionStorage", { getItem: () => null, setItem, removeItem: noop });
    const log = vi.spyOn(console, "log");
    const error = vi.spyOn(console, "error");
    const { calls, current } = await confirm(new Response(null, { status: 204 }), CURRENT_PASSWORD, [{ kind: "guest" }], [], TYPED_CURRENT);
    expect(bodyOf(calls[0]?.init ?? {})).toEqual({ password: CURRENT_PASSWORD });
    expect(calls[0]?.url).not.toContain(CURRENT_PASSWORD);
    expect(setItem).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    // 已注销后不再留着当前密码。
    expect(current().password).toBe("");
  });

  // 验收「password_wrong 时在密码框下方显示 account.delete_password_wrong」：不重读，按钮恢复，输入保留。
  it("marks the password wrong", async () => {
    const { events, current } = await confirm(json(403, { detail: "password_wrong" }), CURRENT_PASSWORD, [], [], TYPED_CURRENT);
    expect(events).toEqual(["update", "update"]);
    expect(current()).toEqual({ ...TYPED_CURRENT, passwordWrong: true });
  });

  // 验收「unavailable 显示 common.service_unavailable；rate_limited 显示 common.rate_limited；failed 显示 common.error_retry；network 显示 common.network_check」
  // 与 HANDOFF 0.63「503 显示 common.service_unavailable；429 显示 common.rate_limited」：不重读、不跳转，按钮恢复，提示为对应的字典文字。
  it.each<[DeleteConfirmReply, CopyKey, () => Reply]>([
    ["rate_limited", "common.rate_limited", () => json(429, { detail: "rate_limited" })],
    ["unavailable", "common.service_unavailable", () => new Response(null, { status: 503 })],
    ["failed", "common.error_retry", () => new Response(null, { status: 500 })],
    ["network", "common.network_check", () => "network"],
  ])("shows the notice when confirming is %s", async (reply, key, response) => {
    expect(deletionStep(reply)).toBe(key);
    const { events, current } = await confirm(response(), null);
    expect(events).toEqual(["update", "update"]);
    expect(current()).toEqual({ ...APPROVED, notice: key });
    expect(deletionPart(render({ site: SITE_ON, deletion: current() }))).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p>`);
  });

  // HANDOFF 0.63（运营者落实写法）「403 delete_not_verified 时重读站点设置，开关开启则回到 V1 起点重新验证（开关关闭则显示 common.error_retry）」与验收
  // 「含页面原先处于开关关闭的确认方式、途中开关被重新打开的情况」：开关开启时 V1 换一轮、批准与密码都收起；仍关闭时显示 common.error_retry；
  // 读取失败时换上 failed（注销部分只显示 common.error_retry）。
  it.each<[string, Reply, SiteState, DeletionState]>([
    ["on", siteOn(), SITE_ON, { ...DELETION_START, round: 1 }],
    ["off", siteOff(), SITE_OFF, { ...TYPED_CURRENT, notice: "common.error_retry" }],
    ["failing", new Response(null, { status: 500 }), { status: "failed" }, { ...TYPED_CURRENT, notice: "common.error_retry" }],
  ])("rereads the switch after delete_not_verified when it is %s", async (_name, site, expectedSite, expected) => {
    const { calls, events, sites, current } = await confirm(json(403, { detail: "delete_not_verified" }), CURRENT_PASSWORD, [], [site], TYPED_CURRENT);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_DELETE_URL, SITE_SETTINGS_URL]);
    expect(events).toEqual(["update", "site", "update"]);
    expect(sites).toEqual([expectedSite]);
    expect(current()).toEqual(expected);
  });

  // HANDOFF 0.63「sms_disabled 与 password_required 时重读站点设置与会员会话，按开关与 hasPassword 改用开关关闭时的确认方式」与验收「开关在流程途中被关闭
  // （……确认回 password_required）时重读站点设置与会员会话（hasPassword 可能已在别的标签页改变）」：两个读取同时发出，换上站点设置与会话
  // （这里 hasPassword 已为真，随后为当前密码确认），再回到起点。
  it("switches to the off-switch confirmation after password_required", async () => {
    const { calls, events, sites, sessions, current } = await confirm(json(422, { detail: "password_required" }), null, [MEMBER], [siteOff()]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_DELETE_URL, SITE_SETTINGS_URL]);
    expect(events).toEqual(["update", "reread", "site", "session", "update"]);
    expect(sites).toEqual([SITE_OFF]);
    expect(sessions).toEqual([MEMBER]);
    expect(current()).toEqual({ ...DELETION_START, round: 1 });
    expect(deletionMode(SITE_OFF, MEMBER.kind === "member" && MEMBER.hasPassword)).toBe("password");
  });

  // 同一条「会话重读得到 guest 时用 replace 进入 /login，得到 failed 时显示 common.error_retry」。
  it("leaves for /login or asks to retry when the reread after password_required says so", async () => {
    const guest = await confirm(json(422, { detail: "password_required" }), null, [{ kind: "guest" }], [siteOff()]);
    expect(guest.events).toEqual(["update", "reread", "login"]);
    expect(guest.sessions).toEqual([]);
    const failed = await confirm(json(422, { detail: "password_required" }), null, [{ kind: "failed" }], [siteOff()]);
    expect(failed.events).toEqual(["update", "reread", "site", "update"]);
    expect(failed.sessions).toEqual([]);
    expect(failed.current()).toEqual({ ...APPROVED, notice: "common.error_retry" });
  });

  // 验收「signed_out 时重读会话并用 replace 进入 /login」：不论重读结果，重读之后都去 /login，不换上会话。
  it("rereads the session and leaves for /login when signed out", async () => {
    const { events, sessions } = await confirm(json(401, { detail: "member_session_required" }), null, [{ kind: "guest" }]);
    expect(events).toEqual(["update", "reread", "login"]);
    expect(sessions).toEqual([]);
  });

  // 验收「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时用 replace 进入 /login，重读得到 failed 时保留原令牌并显示 common.error_retry」。
  it("rereads the session after a csrf failure", async () => {
    const csrf = () => json(403, { detail: "csrf_failed" });
    const member = await confirm(csrf(), null, [MEMBER]);
    expect(member.events).toEqual(["update", "reread", "session", "update"]);
    expect(member.sessions).toEqual([MEMBER]);
    expect(member.current()).toEqual({ ...APPROVED, notice: "common.error_retry" });
    const guest = await confirm(csrf(), null, [{ kind: "guest" }]);
    expect(guest.events).toEqual(["update", "reread", "login"]);
    const failed = await confirm(csrf(), null, [{ kind: "failed" }]);
    expect(failed.events).toEqual(["update", "reread", "update"]);
    expect(failed.sessions).toEqual([]);
    expect(failed.current()).toEqual({ ...APPROVED, notice: "common.error_retry" });
  });

  // 验收「进行中按钮禁用」：确认开始时先禁用按钮、收起先前的提示与密码错误。
  it("disables the buttons while confirming", () => {
    expect(startConfirming({ ...TYPED_CURRENT, passwordWrong: true, notice: "common.error_retry" })).toEqual({ ...TYPED_CURRENT, confirming: true });
  });

  // 验收「离开页面或组件卸载时中止请求与计时，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：请求带页面的 signal，
  // 回答到达前已离开时不再改动；5 秒等待带同一 signal，等待期间离开时不前往首页。
  it("stops after leaving the page", async () => {
    const calls = stubFetch({ [MEMBER_DELETE_URL]: [new Response(null, { status: 204 })] });
    const gone = new AbortController();
    gone.abort();
    const left = fakeDeletionMoves(APPROVED, [{ kind: "guest" }]);
    await deleteAccount(null, TOKEN, gone.signal, left.moves, fakeDelay().delay);
    expect(calls[0]?.init.signal).toBe(gone.signal);
    expect(left.events).toEqual(["update"]);

    stubFetch({ [MEMBER_DELETE_URL]: [new Response(null, { status: 204 })] });
    const page = new AbortController();
    const leaving = fakeDeletionMoves(APPROVED, [{ kind: "guest" }]);
    const delay = vi.fn((ms: number, signal: AbortSignal) => {
      expect(ms).toBe(DELETED_DELAY_MS);
      expect(signal).toBe(page.signal);
      page.abort();
      return Promise.resolve();
    });
    await deleteAccount(null, TOKEN, page.signal, leaving.moves, delay);
    expect(leaving.events).toEqual(["update", "update", "reread"]);
  });
});

describe("results from the deletion purpose of V1 (SHOP-TASK-167)", () => {
  // 验收「核验……verified 时经回调告知页面」与「验证通过后出现 account.delete_confirm 按钮」：页面换上批准，不发请求。
  it("shows account.delete_confirm after verified", async () => {
    const calls = stubFetch({});
    const { moves, events, current } = fakeDeletionMoves(DELETION_START);
    await takeDeleteSmsStep("verified", new AbortController().signal, moves);
    expect(calls).toEqual([]);
    expect(events).toEqual(["update"]);
    expect(current()).toEqual(APPROVED);
  });

  // 验收「开关在流程途中被关闭（V1 回 disabled……）时重读站点设置与会员会话，按开关与 hasPassword 改用下一条的确认方式」：这里会话仍未设密码，随后为确认框。
  it("switches to the off-switch confirmation after disabled", async () => {
    const unset: MemberSessionState = { kind: "member", phoneMasked: MASKED, hasPassword: false, csrfToken: NEW_TOKEN };
    const calls = stubFetch({ [SITE_SETTINGS_URL]: [siteOff()] });
    const { moves, events, sites, sessions, current } = fakeDeletionMoves(DELETION_START, [unset]);
    await takeDeleteSmsStep("disabled", new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([SITE_SETTINGS_URL]);
    expect(events).toEqual(["reread", "site", "session", "update"]);
    expect(sites).toEqual([SITE_OFF]);
    expect(sessions).toEqual([unset]);
    expect(current()).toEqual({ ...DELETION_START, round: 1 });
    expect(deletionMode(SITE_OFF, false)).toBe("session");
  });

  // 验收「signed_out 时重读会话并用 replace 进入 /login」「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry」：V1 交来的这两种结果同确认注销。
  it.each<[DeleteSmsStep, MemberSessionState, string[]]>([
    ["signed_out", { kind: "guest" }, ["reread", "login"]],
    ["csrf", MEMBER, ["reread", "session", "update"]],
  ])("handles %s from V1", async (step, reread, expected) => {
    stubFetch({});
    const { moves, events } = fakeDeletionMoves(DELETION_START, [reread]);
    await takeDeleteSmsStep(step, new AbortController().signal, moves);
    expect(events).toEqual(expected);
  });

  // 验收「离开页面……之后不再更新」：已离开时不再处理 V1 交来的结果。
  it("does nothing after leaving the page", async () => {
    stubFetch({});
    const gone = new AbortController();
    gone.abort();
    const { moves, events } = fakeDeletionMoves(DELETION_START, [MEMBER]);
    await takeDeleteSmsStep("verified", gone.signal, moves);
    await takeDeleteSmsStep("disabled", gone.signal, moves);
    expect(events).toEqual([]);
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
      // SHOP-TASK-167：注销部分用到的键。
      "account.delete",
      "account.delete_warning",
      "account.delete_verify",
      "account.delete_confirm",
      "account.deleted",
      "account.delete_verify_password",
      "account.delete_password_wrong",
      "account.delete_verify_session",
      "account.delete_session_confirm",
      "account.delete_keep",
      "auth.password",
      "common.rate_limited",
    ];
    for (const key of keys) {
      for (const language of LANGUAGES) {
        expect(COPY[key][language], key).not.toBe("");
      }
    }
    expect(formatCopy(COPY["account.password_set_done"].en)).toBe("Password set. You can now log in with your password or an SMS code.");
  });
});
