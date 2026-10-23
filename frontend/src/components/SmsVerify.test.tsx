import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PASSWORD_RESET_VERIFY_URL, SMS_LOGIN_URL, SMS_SEND_URL } from "../api/memberAuth";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  canSend,
  canVerify,
  challengeLost,
  challengeToken,
  INITIAL_SMS,
  maskedPhone,
  maskPhone,
  mountChallenge,
  sendCode,
  SmsVerifyView,
  startSending,
  startVerifying,
  submitCode,
} from "./SmsVerify";
import type { ChallengeMoves, SmsMoves, SmsPhone, SmsPurpose, SmsState, SmsVerified, SmsVerifyViewProps } from "./SmsVerify";
import { TURNSTILE_SCRIPT_URL, turnstileLanguage } from "./turnstile";
import type { ChallengeHandlers, ChallengeLoader, ChallengeWidget } from "./turnstile";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的号码、验证码、令牌与公开 key 都是虚构值。
const SITE_KEY = "1x00000000000000000000AA";
const CAPTCHA = "captcha-token-example";
const CSRF = "csrf-token-example";
const CODE = "123456";
const PHONE: SmsPhone = { input: "012-345 6789", region: "MY" };
const MASKED = "+60*****6789";
const GUEST_TO: RoutePath = "/checkout";
const REGISTER_PURPOSES: readonly SmsPurpose[] = ["register", "login"];
const PURPOSES: readonly SmsPurpose[] = ["register", "login", "reset_password"];

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
      <RouterProvider initialPath="/">{element}</RouterProvider>
    </LanguageProvider>
  );
}

function guestFor(purpose: SmsPurpose): RoutePath | null {
  return purpose === "reset_password" ? null : GUEST_TO;
}

function render(overrides: Partial<SmsVerifyViewProps> = {}, language: Language = "en"): string {
  const purpose = overrides.purpose ?? "register";
  const props: SmsVerifyViewProps = {
    purpose,
    smsEnabled: true,
    siteKey: SITE_KEY,
    guestTo: guestFor(purpose),
    state: INITIAL_SMS,
    challengeRef: noop,
    onRegion: noop,
    onPhone: noop,
    onCode: noop,
    onSend: noop,
    onVerify: noop,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<SmsVerifyView {...props} />, language));
}

function t(key: CopyKey, vars?: Readonly<Record<string, string>>): string {
  return translate("en", key, vars);
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
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

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

// 每个按钮的开始标签与文字。
function buttons(html: string): { tag: string; text: string }[] {
  return [...html.matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)].map((m) => ({ tag: m[1] ?? "", text: textNodes(m[2] ?? "").join(" ") }));
}

function button(html: string, text: string): string {
  const found = buttons(html).find((item) => item.text === text);
  if (found === undefined) {
    throw new Error(`no button: ${text}`);
  }
  return found.tag;
}

function disabled(tag: string): boolean {
  return /\sdisabled=""/.test(tag);
}

function hrefs(html: string): string[] {
  return [...html.matchAll(/<a\b[^>]*href="([^"]*)"/g)].map((m) => m[1] ?? "");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// 请求体不合格的 422：出错列表里有 phone_invalid。
function phoneInvalid(): Response {
  return json(422, { detail: [{ loc: ["body", "phone"], type: "phone_invalid", msg: "invalid" }] });
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

function requests(request: ReturnType<typeof stubFetch>) {
  return request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), body: bodyOf(init) }));
}

function bodyOf(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

// 组件对结果的处理的替身：按顺序记下事件，并把改动套在 state 上。
function fakeMoves(start: SmsState) {
  const events: string[] = [];
  const verified: SmsVerified[] = [];
  let state = start;
  const target: SmsMoves = {
    update: (change) => {
      state = change(state);
      events.push("update");
    },
    resetChallenge: () => {
      events.push("reset");
    },
    verified: (value) => {
      verified.push(value);
      events.push("verified");
    },
  };
  return {
    target,
    events,
    verified,
    state: () => state,
  };
}

// 发送进行中（已完成挑战、点了发送）。
const SENDING: SmsState = startSending({ ...INITIAL_SMS, input: PHONE.input, token: CAPTCHA });
// 已发出验证码、填了验证码、点了验证。
const SENT: SmsState = { ...INITIAL_SMS, input: PHONE.input, sent: { phone: PHONE, masked: MASKED }, code: CODE };
const VERIFYING: SmsState = startVerifying(SENT);

function flush(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("initial view", () => {
  // UX V1「[auth.phone] [<国家码> ▾][______]（结账以外只列 +60、+65）」「[auth.claim_notice]（仅结账验证、注册、短信登录显示）」与
  // 验收「国家码选择只有 +60（地区 MY）与 +65（地区 SG）、默认 +60」「完成挑战前发送按钮禁用」。
  it.each(PURPOSES)("shows the number, the scope, the check and a disabled send button for %s", (purpose) => {
    const html = render({ purpose });
    const select = /<select\b[\s\S]*?<\/select>/.exec(html)?.[0] ?? "";
    expect(tags(select, "option")).toHaveLength(2);
    expect(select).toContain(`<option value="MY" selected="">+60</option>`);
    expect(select).toContain(`<option value="SG">+65</option>`);
    const texts = textNodes(html);
    expect(texts).toContain(t("auth.phone"));
    expect(texts).toContain(t("auth.sms_scope"));
    expect(texts).toContain(t("auth.challenge"));
    expect(texts.includes(t("auth.claim_notice"))).toBe(purpose !== "reset_password");
    expect(tags(html, "div").filter((tag) => tag.includes("site-sms__challenge"))).toHaveLength(1);
    expect(buttons(html).map((item) => item.text)).toEqual([t("auth.send_code")]);
    expect(disabled(button(html, t("auth.send_code")))).toBe(true);
    expect(texts).not.toContain(t("auth.code"));
    expect(hrefs(html)).toEqual([]);
  });

  // 验收「完成挑战前发送按钮禁用」「发送进行中按钮禁用」：取得令牌后可发送；进行中禁用并标 aria-busy；挑战失效后再次禁用并显示 auth.challenge_failed。
  it("enables sending only with a token and while nothing is in progress", () => {
    const ready = challengeToken(INITIAL_SMS, CAPTCHA);
    expect(canSend(ready)).toBe(true);
    expect(disabled(button(render({ state: ready }), t("auth.send_code")))).toBe(false);
    const sending = startSending(ready);
    const tag = button(render({ state: sending }), t("auth.send_code"));
    expect(disabled(tag)).toBe(true);
    expect(tag).toContain(`aria-busy="true"`);
    const lost = challengeLost(ready);
    expect(canSend(lost)).toBe(false);
    expect(textNodes(render({ state: lost }))).toContain(t("auth.challenge_failed"));
    expect(challengeToken(lost, "next-token").challengeLost).toBe(false);
  });
});

describe("replaced component", () => {
  // UX V1「短信验证开关关闭时……整个组件替换为下面的暂停提示，不显示号码输入、人机挑战与发送按钮」「([auth.continue_guest])（仅注册 / 短信登录）」，
  // 与 UX V1「开关在流程途中被关闭时……组件改显示 [auth.sms_paused]」（disabled 之后的状态）。
  it.each(PURPOSES)("shows only the paused notice when SMS is off or switched off mid-flow (%s)", (purpose) => {
    for (const html of [render({ purpose, smsEnabled: false }), render({ purpose, state: { ...SENT, paused: true } })]) {
      const expected = purpose === "reset_password" ? [t("auth.sms_paused")] : [t("auth.sms_paused"), t("auth.continue_guest")];
      expect(textNodes(html)).toEqual(expected);
      expect(hrefs(html)).toEqual(purpose === "reset_password" ? [] : [GUEST_TO]);
      expect(tags(html, "input")).toEqual([]);
      expect(tags(html, "select")).toEqual([]);
      expect(tags(html, "button")).toEqual([]);
    }
  });

  // 验收「公开 key 为 null 时与开关关闭一样整个组件替换——注册 / 短信登录只显示 auth.sms_failed 与 auth.continue_guest，重设密码只显示
  // auth.sms_not_sent_no_change，不显示号码输入、auth.sms_scope 与 auth.claim_notice」。
  it.each(PURPOSES)("replaces the component when there is no challenge key (%s)", (purpose) => {
    const html = render({ purpose, siteKey: null });
    const expected = purpose === "reset_password" ? [t("auth.sms_not_sent_no_change")] : [t("auth.sms_failed"), t("auth.continue_guest")];
    expect(textNodes(html)).toEqual(expected);
    expect(hrefs(html)).toEqual(purpose === "reset_password" ? [] : [GUEST_TO]);
    expect(tags(html, "input")).toEqual([]);
    expect(tags(html, "button")).toEqual([]);
  });
});

describe("code entry", () => {
  // 验收「sent 后显示 auth.code_sent、验证码输入（auth.code）、auth.verify_submit 与 auth.resend_code」「验证码为空时验证按钮禁用」「提交进行中按钮禁用」。
  it("disables verify while the code is empty or a request is in progress", () => {
    const empty = { ...SENT, code: "  " };
    expect(canVerify(empty)).toBe(false);
    const html = render({ state: empty });
    expect(textNodes(html)).toContain(t("auth.code_sent", { phoneMasked: MASKED }));
    expect(textNodes(html)).toContain(t("auth.code"));
    expect(disabled(button(html, t("auth.verify_submit")))).toBe(true);
    expect(canVerify(SENT)).toBe(true);
    expect(disabled(button(render({ state: SENT }), t("auth.verify_submit")))).toBe(false);
    const busy = button(render({ state: VERIFYING }), t("auth.verify_submit"));
    expect(disabled(busy)).toBe(true);
    expect(busy).toContain(`aria-busy="true"`);
  });

  // 验收「auth.resend_code（重发要重新完成挑战）」：发出之后发送按钮换成重发，令牌已作废时重发禁用，重新取得令牌后可用。
  it("asks for a new check before resending", () => {
    const html = render({ state: SENT });
    expect(buttons(html).map((item) => item.text)).toEqual([t("auth.verify_submit"), t("auth.resend_code")]);
    expect(disabled(button(html, t("auth.resend_code")))).toBe(true);
    expect(disabled(button(render({ state: challengeToken(SENT, CAPTCHA) }), t("auth.resend_code")))).toBe(false);
  });

  // 验收「验证通过（调用回调之后）组件进入已验证状态——不再显示发送、重发与验证按钮，号码与验证码输入收起，遮掩后的号码只在保留的
  // auth.code_sent 一行里出现（不单独显示号码），结果文字由嵌入的页面显示」。
  it.each(PURPOSES)("keeps only the code-sent line once verified (%s)", (purpose) => {
    const html = render({ purpose, state: { ...SENT, verified: true } });
    expect(textNodes(html)).toEqual([t("auth.code_sent", { phoneMasked: MASKED })]);
    expect(tags(html, "button")).toEqual([]);
    expect(tags(html, "input")).toEqual([]);
    expect(tags(html, "select")).toEqual([]);
    expect(hrefs(html)).toEqual([]);
    expect(html.split(MASKED)).toHaveLength(2);
    expect(html).not.toContain(PHONE.input);
  });
});

describe("phoneMasked", () => {
  // 验收「{phoneMasked}——输入以 + 开头时为 + 与其后的全部数字，否则为国家码加输入里的数字（去掉开头的一个 0）；再按 mask_phone 规则
  // 保留前 3 个与最后 4 个字符、中间换成星号」。
  it.each<[SmsPhone, string]>([
    [{ input: "012-345 6789", region: "MY" }, "+60*****6789"],
    [{ input: "12 345 6789", region: "MY" }, "+60*****6789"],
    [{ input: "8123 4567", region: "SG" }, "+65****4567"],
    [{ input: " +65 8123-4567 ", region: "MY" }, "+65****4567"],
    [{ input: "+60 12 345 6789", region: "SG" }, "+60*****6789"],
    // 只去掉开头的一个 0：+600123，7 个字符，整串换成星号。
    [{ input: "00123", region: "MY" }, "*******"],
  ])("masks %j as %s", (phone, masked) => {
    expect(maskedPhone(phone)).toBe(masked);
  });

  // app/api/member_auth.py mask_phone「不足 8 个字符时……整串换成星号」，8 个字符起只露出前 3 与后 4 个。
  it("follows the server's mask rule", () => {
    expect(maskPhone("+6012")).toBe("*****");
    expect(maskPhone("+601234")).toBe("*******");
    expect(maskPhone("+6012345")).toBe("+60*2345");
  });
});

describe("sending", () => {
  // 验收「register 与 login 以同名 purpose 发送……reset_password 以同名 purpose 发送」「号码以输入原文与地区提交、由服务端规范化」
  // 「每次发送（无论结果）之后重置挑战、取新令牌」。
  it.each(PURPOSES)("posts the raw number, region, purpose and token for %s, then resets the check", async (purpose) => {
    const request = stubFetch(() => json(200, {}));
    const moves = fakeMoves(SENDING);
    await sendCode(purpose, PHONE, CAPTCHA, new AbortController().signal, moves.target);
    expect(requests(request)).toEqual([
      { url: SMS_SEND_URL, method: "POST", body: { phone: PHONE.input, phone_region: "MY", purpose, captcha_token: CAPTCHA } },
    ]);
    expect(moves.events).toEqual(["update", "reset"]);
    expect(moves.state()).toMatchObject({ sending: false, token: null, sent: { phone: PHONE, masked: MASKED }, notice: null });
  });

  // 各发送结果的提示（UX V1「号码不在白名单」「短信无法送达或停发」、验收「captcha_failed 显示 auth.challenge_failed；rate_limited 显示
  // common.rate_limited；……network 显示 common.network_check，其他 failed 显示 common.error_retry」「phone_invalid 显示 checkout.phone_invalid」），
  // 与 UX V1「重设密码 / 注销确认 → [auth.sms_not_sent_no_change]（无游客文字与按钮）」：continue_guest 只随 not_supported_country 与 sms_failed 出现。
  const SEND_CASES: [string, Reply, (purpose: SmsPurpose) => CopyKey][] = [
    ["captcha_failed", () => json(403, { detail: "captcha_failed" }), () => "auth.challenge_failed"],
    ["rate_limited", () => json(429, { detail: "rate_limited" }), () => "common.rate_limited"],
    ["not_whitelisted", () => json(422, { detail: "not_whitelisted" }), (p) => (p === "reset_password" ? "auth.sms_not_sent_no_change" : "auth.not_supported_country")],
    ["undeliverable", () => json(422, { detail: "sms_undeliverable" }), (p) => (p === "reset_password" ? "auth.sms_not_sent_no_change" : "auth.sms_failed")],
    ["suspended", () => json(503, { detail: "sms_suspended" }), (p) => (p === "reset_password" ? "auth.sms_not_sent_no_change" : "auth.sms_failed")],
    ["network", () => "network", () => "common.network_check"],
    ["failed", () => json(500, {}), () => "common.error_retry"],
  ];
  const GUEST_KEYS: readonly CopyKey[] = ["auth.not_supported_country", "auth.sms_failed"];

  for (const purpose of PURPOSES) {
    it.each(SEND_CASES)(`shows the notice for %s (${purpose})`, async (_name, reply, expected) => {
      stubFetch(reply);
      const moves = fakeMoves(SENDING);
      await sendCode(purpose, PHONE, CAPTCHA, new AbortController().signal, moves.target);
      expect(moves.events).toEqual(["update", "reset"]);
      const key = expected(purpose);
      expect(moves.state()).toMatchObject({ sending: false, token: null, sent: null, notice: key });
      const html = render({ purpose, state: moves.state() });
      expect(textNodes(html)).toContain(t(key));
      // 发送失败后号码输入仍在，可改号或重试。
      expect(tags(html, "input")).toHaveLength(1);
      const guest = GUEST_KEYS.includes(key);
      expect(guest && !REGISTER_PURPOSES.includes(purpose)).toBe(false);
      expect(textNodes(html).includes(t("auth.continue_guest"))).toBe(guest);
      expect(hrefs(html)).toEqual(guest ? [GUEST_TO] : []);
    });
  }

  // 验收「号码格式由服务端判定，phone_invalid 显示 checkout.phone_invalid」。
  it("marks the number invalid when the server rejects its format", async () => {
    stubFetch(phoneInvalid);
    const moves = fakeMoves(SENDING);
    await sendCode("register", PHONE, CAPTCHA, new AbortController().signal, moves.target);
    expect(moves.state()).toMatchObject({ phoneInvalid: true, notice: null, token: null });
    const html = render({ state: moves.state() });
    expect(textNodes(html)).toContain(t("checkout.phone_invalid"));
    expect(tags(html, "input")[0]).toContain(`aria-invalid="true"`);
  });

  // 验收「disabled（流程中开关被关闭，发送或验证都可能出现）与开关关闭状态相同——整个组件替换为 auth.sms_paused」。
  it.each(PURPOSES)("switches to the paused notice when sending finds SMS switched off (%s)", async (purpose) => {
    stubFetch(() => json(403, { detail: "sms_disabled" }));
    const moves = fakeMoves(SENDING);
    await sendCode(purpose, PHONE, CAPTCHA, new AbortController().signal, moves.target);
    expect(moves.state().paused).toBe(true);
    expect(textNodes(render({ purpose, state: moves.state() }))[0]).toBe(t("auth.sms_paused"));
  });

  // 验收「卸载时中止进行中的请求，之后的结果不再更新」。
  it("does not update after the component is gone", async () => {
    stubFetch(() => json(200, {}));
    const moves = fakeMoves(SENDING);
    const controller = new AbortController();
    const pending = sendCode("register", PHONE, CAPTCHA, controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("verifying", () => {
  // 验收「register 与 login 以同名 purpose……提交短信登录（服务端对已注册号码登录、未注册号码创建会员，回调收到 created）」与
  // 「验证通过（调用回调之后）组件进入已验证状态」。
  it.each(REGISTER_PURPOSES)("logs in with the sent number for %s and reports created", async (purpose) => {
    for (const created of [true, false]) {
      const request = stubFetch(() => json(200, { created }));
      const moves = fakeMoves(VERIFYING);
      await submitCode(purpose, PHONE, CODE, new AbortController().signal, moves.target);
      expect(requests(request)).toEqual([{ url: SMS_LOGIN_URL, method: "POST", body: { phone: PHONE.input, phone_region: "MY", purpose, code: CODE } }]);
      expect(moves.verified).toEqual([{ kind: "member", created }]);
      expect(moves.events).toEqual(["verified", "update"]);
      expect(moves.state()).toMatchObject({ verifying: false, verified: true });
    }
  });

  // 验收「reset_password 以同名 purpose 发送并提交重设验证（回调收到 registered 或 not_registered 与 csrf_token）」。
  it("verifies a password reset and reports whether the number is registered", async () => {
    const request = stubFetch(() => json(200, { registered: true, csrf_token: CSRF }));
    const moves = fakeMoves(VERIFYING);
    await submitCode("reset_password", PHONE, CODE, new AbortController().signal, moves.target);
    expect(requests(request)).toEqual([{ url: PASSWORD_RESET_VERIFY_URL, method: "POST", body: { phone: PHONE.input, phone_region: "MY", code: CODE } }]);
    expect(moves.verified).toEqual([{ kind: "registered", csrfToken: CSRF }]);
    expect(moves.events).toEqual(["verified", "update"]);
    expect(moves.state().verified).toBe(true);

    stubFetch(() => json(200, { registered: false }));
    const other = fakeMoves(VERIFYING);
    await submitCode("reset_password", PHONE, CODE, new AbortController().signal, other.target);
    expect(other.verified).toEqual([{ kind: "not_registered" }]);
    expect(other.state().verified).toBe(true);
  });

  // UX V1「验证码错误或超过尝试次数不降级为游客」与验收「验证码 wrong 显示 auth.code_wrong」「验证码错误不出现 auth.continue_guest」。
  it.each(PURPOSES)("shows a wrong code under the code field without a guest option (%s)", async (purpose) => {
    stubFetch(() => json(422, { detail: "code_wrong" }));
    const moves = fakeMoves(VERIFYING);
    await submitCode(purpose, PHONE, CODE, new AbortController().signal, moves.target);
    expect(moves.verified).toEqual([]);
    expect(moves.state()).toMatchObject({ verifying: false, codeWrong: true, verified: false });
    const html = render({ purpose, state: moves.state() });
    expect(textNodes(html)).toContain(t("auth.code_wrong"));
    expect(textNodes(html)).not.toContain(t("auth.continue_guest"));
    expect(hrefs(html)).toEqual([]);
  });

  // 验收「验证时的 unavailable（验证码已发出）显示 common.service_unavailable（UX「限流/服务不可用」），留在验证码输入，可再提交或重发」与
  // 「network 显示 common.network_check，其他 failed 显示 common.error_retry」「phone_invalid 显示 checkout.phone_invalid」。
  it.each<[string, Reply, CopyKey]>([
    ["unavailable", () => json(503, { detail: "verify_unavailable" }), "common.service_unavailable"],
    ["network", () => "network", "common.network_check"],
    ["failed", () => json(500, {}), "common.error_retry"],
    ["phone_invalid", phoneInvalid, "checkout.phone_invalid"],
  ])("stays on the code entry after %s", async (_name, reply, key) => {
    stubFetch(reply);
    const moves = fakeMoves(VERIFYING);
    await submitCode("login", PHONE, CODE, new AbortController().signal, moves.target);
    expect(moves.verified).toEqual([]);
    expect(moves.state().verifying).toBe(false);
    const ready = challengeToken(moves.state(), CAPTCHA);
    const html = render({ purpose: "login", state: ready });
    expect(textNodes(html)).toContain(t(key));
    expect(disabled(button(html, t("auth.verify_submit")))).toBe(false);
    expect(disabled(button(html, t("auth.resend_code")))).toBe(false);
    expect(textNodes(html)).not.toContain(t("auth.continue_guest"));
  });

  // 验收「disabled（流程中开关被关闭，发送或验证都可能出现）与开关关闭状态相同」与 UX V1「服务端拒绝之后提交的验证码……组件改显示 [auth.sms_paused]」。
  it.each(PURPOSES)("switches to the paused notice when verifying finds SMS switched off (%s)", async (purpose) => {
    stubFetch(() => json(403, { detail: "sms_disabled" }));
    const moves = fakeMoves(VERIFYING);
    await submitCode(purpose, PHONE, CODE, new AbortController().signal, moves.target);
    expect(moves.verified).toEqual([]);
    const html = render({ purpose, state: moves.state() });
    expect(textNodes(html)[0]).toBe(t("auth.sms_paused"));
    expect(tags(html, "input")).toEqual([]);
  });

  // 验收「卸载时中止进行中的请求，之后的结果不再更新」：回调也不再调用。
  it("does not report or update after the component is gone", async () => {
    stubFetch(() => json(200, { created: true }));
    const moves = fakeMoves(VERIFYING);
    const controller = new AbortController();
    const pending = submitCode("register", PHONE, CODE, controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
    expect(moves.verified).toEqual([]);
  });
});

describe("challenge", () => {
  function fakeWidget() {
    const calls: string[] = [];
    const widget: ChallengeWidget = {
      reset: () => {
        calls.push("reset");
      },
      remove: () => {
        calls.push("remove");
      },
    };
    return { widget, calls };
  }

  function fakeChallengeMoves() {
    const events: string[] = [];
    const target: ChallengeMoves = {
      token: (token) => {
        events.push(`token ${token}`);
      },
      lost: () => {
        events.push("lost");
      },
      widget: (widget) => {
        events.push(widget === null ? "widget none" : "widget");
      },
    };
    return { target, events };
  }

  const CONTAINER = {} as HTMLElement;

  // 验收「以 Cloudflare Turnstile 官方的浏览器脚本渲染（脚本地址写成代码里的常量）、按当前界面语言请求对应语言」。
  it("uses the official script and asks for the interface language", () => {
    expect(TURNSTILE_SCRIPT_URL.startsWith("https://challenges.cloudflare.com/turnstile/v0/api.js")).toBe(true);
    expect(LANGUAGES.map(turnstileLanguage)).toEqual(["en", "zh-CN", "ms"]);
  });

  // 验收「加载与渲染经一个可注入的小接口……测试以替身代替」与「挑战加载失败或过期显示 auth.challenge_failed」：渲染时带公开 key 与界面语言，
  // 令牌与过期经回调送回；移除之后不再处理。
  it("renders with the key and language and stops listening once removed", async () => {
    const { widget, calls } = fakeWidget();
    const rendered: { siteKey: string; language: Language; handlers: ChallengeHandlers }[] = [];
    const loader: ChallengeLoader = {
      render: (_container, siteKey, language, handlers) => {
        rendered.push({ siteKey, language, handlers });
        return Promise.resolve(widget);
      },
    };
    const moves = fakeChallengeMoves();
    const remove = mountChallenge(loader, CONTAINER, SITE_KEY, "ms", moves.target);
    await flush();
    const [first] = rendered;
    expect(first?.siteKey).toBe(SITE_KEY);
    expect(first?.language).toBe("ms");
    first?.handlers.onToken(CAPTCHA);
    first?.handlers.onLost();
    remove();
    first?.handlers.onToken("late-token");
    expect(moves.events).toEqual(["widget", `token ${CAPTCHA}`, "lost", "widget none"]);
    expect(calls).toEqual(["remove"]);
  });

  // 验收「挑战加载失败……显示 auth.challenge_failed」：脚本加载或渲染失败归为失效。
  it("reports a failed load as a lost check", async () => {
    const moves = fakeChallengeMoves();
    mountChallenge({ render: () => Promise.reject(new Error("blocked")) }, CONTAINER, SITE_KEY, "en", moves.target);
    await flush();
    expect(moves.events).toEqual(["lost"]);
  });

  // 卸载之后才渲染好的挑战随即移除，不再交给组件。
  it("removes a check that finishes rendering after removal", async () => {
    const { widget, calls } = fakeWidget();
    let finish: (value: ChallengeWidget) => void = noop;
    const loader: ChallengeLoader = {
      render: () =>
        new Promise<ChallengeWidget>((resolve) => {
          finish = resolve;
        }),
    };
    const moves = fakeChallengeMoves();
    const remove = mountChallenge(loader, CONTAINER, SITE_KEY, "en", moves.target);
    remove();
    finish(widget);
    await flush();
    expect(calls).toEqual(["remove"]);
    expect(moves.events).toEqual(["widget none"]);
  });
});

describe("dictionary", () => {
  // 验收「页面文字（国家码选项 +60、+65 除外）全部来自字典，带变量的条目按模板匹配」与「需要 UX-COPY 里没有的界面文字时停下……不自行编写文案」：
  // 三种用途的初始、发出验证码、各种提示、已验证与整体替换，每段文字（含 aria-label）都是当前语言的字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const plain = new Set<string>(["+60", "+65"]);
    const templates: RegExp[] = [];
    for (const entry of Object.values(COPY)) {
      const text = entry[language];
      if (/\{\w+\}/.test(text)) {
        const pattern = text
          .split(/\{\w+\}/)
          .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
          .join(".+");
        templates.push(new RegExp(`^${pattern}$`));
      } else {
        plain.add(text);
      }
    }
    const notices: CopyKey[] = [
      "auth.challenge_failed",
      "auth.not_supported_country",
      "auth.sms_failed",
      "auth.sms_not_sent_no_change",
      "common.rate_limited",
      "common.service_unavailable",
      "common.network_check",
      "common.error_retry",
    ];
    const states: SmsState[] = [
      INITIAL_SMS,
      challengeLost(INITIAL_SMS),
      { ...INITIAL_SMS, phoneInvalid: true },
      SENT,
      { ...SENT, codeWrong: true },
      { ...SENT, verified: true },
      { ...SENT, paused: true },
      ...notices.map((notice) => ({ ...SENT, notice })),
    ];
    for (const purpose of PURPOSES) {
      const pages = [
        ...states.map((state) => render({ purpose, state }, language)),
        render({ purpose, smsEnabled: false }, language),
        render({ purpose, siteKey: null }, language),
      ];
      for (const html of pages) {
        expect(html).not.toMatch(/\s(title|placeholder)=/i);
        for (const value of visibleTexts(html)) {
          expect(plain.has(value) || templates.some((pattern) => pattern.test(value)), value).toBe(true);
        }
      }
    }
    expect(textNodes(render({ state: SENT }, language))).toContain(translate(language, "auth.code_sent", { phoneMasked: MASKED }));
  });
});
