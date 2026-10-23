import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PASSWORD_LOGIN_URL,
  PASSWORD_RESET_URL,
  PASSWORD_RESET_VERIFY_URL,
  SMS_LOGIN_URL,
  SMS_SEND_URL,
  passwordLogin,
  sendSmsCode,
  smsLogin,
  submitPasswordReset,
  verifyPasswordReset,
} from "./memberAuth";
import type { MemberPhone, PasswordLoginReply, ResetSubmitReply, ResetVerifyReply, SmsLoginReply, SmsSendReply } from "./memberAuth";

afterEach(() => {
  vi.unstubAllGlobals();
});

// 测试里的号码、验证码、密码与令牌都是虚构值。
const PHONE: MemberPhone = { input: "012-345 6789", region: "MY" };
const CODE = "123456";
const PASSWORD = "correct horse battery";
const CAPTCHA = "captcha-token-example";
const CSRF = "csrf-token-example";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

// 请求体不合格的 422（app/api/pay.py 的 _body_errors 与 app/api/orders.py 的 _error）：出错列表，每条有位置、类型与消息。
function invalid(type: string): Response {
  return json(422, { detail: [{ loc: ["body", "phone"], type, msg: "invalid" }] });
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
  return request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), init }));
}

function bodyOf(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

describe("member auth requests", () => {
  // 五个接口记录（SHOP-TASK-070、076、077、079）的请求模型都是 extra="forbid"，以及 DESIGN 1.12「权限与资料保护」第 1、3、5 条
  // 「手机号与密码只在请求体里」与本任务验收「都不带查询参数」「CSRF 令牌只在 X-CSRF-Token 请求头里」：
  // 地址恰好是接口路径，POST、同源 cookie、不缓存；请求头只有 Accept 与 Content-Type（重设提交另有 X-CSRF-Token）；请求体只有约定字段。
  const cases: [string, (signal: AbortSignal) => Promise<unknown>, string, Record<string, string>, unknown][] = [
    [
      "send code",
      (signal) => sendSmsCode(PHONE, "reset_password", CAPTCHA, signal),
      SMS_SEND_URL,
      {},
      { phone: PHONE.input, phone_region: PHONE.region, purpose: "reset_password", captcha_token: CAPTCHA },
    ],
    ["sms login", (signal) => smsLogin(PHONE, "register", CODE, signal), SMS_LOGIN_URL, {}, { phone: PHONE.input, phone_region: PHONE.region, purpose: "register", code: CODE }],
    ["password login", (signal) => passwordLogin(PHONE, PASSWORD, signal), PASSWORD_LOGIN_URL, {}, { phone: PHONE.input, phone_region: PHONE.region, password: PASSWORD }],
    ["reset verify", (signal) => verifyPasswordReset(PHONE, CODE, signal), PASSWORD_RESET_VERIFY_URL, {}, { phone: PHONE.input, phone_region: PHONE.region, code: CODE }],
    ["reset submit", (signal) => submitPasswordReset(PASSWORD, CSRF, signal), PASSWORD_RESET_URL, { "X-CSRF-Token": CSRF }, { password: PASSWORD }],
  ];
  it.each(cases)("%s posts only the agreed fields to a fixed address", async (name, call, url, extraHeaders, body) => {
    const request = stubFetch(() => empty(500));
    const controller = new AbortController();
    await call(controller.signal);
    const calls = requests(request);
    expect(calls.map((item) => [item.method, item.url]), name).toEqual([["POST", url]]);
    expect(calls[0]?.url, name).not.toContain("?");
    expect(calls[0]?.init?.credentials, name).toBe("same-origin");
    expect(calls[0]?.init?.cache, name).toBe("no-store");
    expect(calls[0]?.init?.signal, name).toBe(controller.signal);
    expect(calls[0]?.init?.headers, name).toEqual({ Accept: "application/json", "Content-Type": "application/json", ...extraHeaders });
    expect(bodyOf(calls[0]?.init), name).toEqual(body);
  });

  // 同上验收「都不进地址」：号码、验证码、密码、令牌都不出现在地址里（地址只与接口路径相同，上一条已守住；这里另查字面值）。
  it("keeps phone, code, password and tokens out of the address", async () => {
    const request = stubFetch(() => empty(500));
    await sendSmsCode(PHONE, "login", CAPTCHA);
    await smsLogin(PHONE, "login", CODE);
    await passwordLogin(PHONE, PASSWORD);
    await verifyPasswordReset(PHONE, CODE);
    await submitPasswordReset(PASSWORD, CSRF);
    for (const { url } of requests(request)) {
      for (const secret of [PHONE.input, CODE, PASSWORD, CAPTCHA, CSRF]) {
        expect(url).not.toContain(secret);
      }
    }
  });
});

describe("send SMS code", () => {
  // SHOP-TASK-070 接口记录（app/api/sms.py 文档字符串的结果表）：200 sent、403 sms_disabled、403 captcha_failed、422 not_whitelisted、
  // 422 sms_undeliverable、429 rate_limited、503 sms_suspended，以及请求体不合格的 422（类型 phone_invalid 时为号码不成立）。
  // 未列出的状态（409 等）、错误码不符与 422 不是 phone_invalid 的出错列表都归 failed；网络中断归 network。
  const replies: [string, Reply, SmsSendReply][] = [
    ["200", () => json(200, { status: "sent" }), "sent"],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), "disabled"],
    ["403 captcha_failed", () => json(403, { detail: "captcha_failed" }), "captcha_failed"],
    ["403 other code", () => json(403, { detail: "csrf_failed" }), "failed"],
    ["422 not_whitelisted", () => json(422, { detail: "not_whitelisted" }), "not_whitelisted"],
    ["422 sms_undeliverable", () => json(422, { detail: "sms_undeliverable" }), "undeliverable"],
    ["422 phone_invalid", () => invalid("phone_invalid"), "phone_invalid"],
    ["422 other validation", () => invalid("missing"), "failed"],
    ["422 other code", () => json(422, { detail: "code_wrong" }), "failed"],
    ["429", () => json(429, { detail: "rate_limited" }), "rate_limited"],
    ["503 sms_suspended", () => json(503, { detail: "sms_suspended" }), "suspended"],
    ["503 other code", () => json(503, { detail: "service_unavailable" }), "failed"],
    ["409", () => json(409, { detail: "sms_disabled" }), "failed"],
    ["413", () => json(413, { detail: "request body too large" }), "failed"],
    ["500 not JSON", () => new Response("<html>", { status: 500 }), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(sendSmsCode(PHONE, "login", CAPTCHA), name).resolves.toBe(expected);
  });
});

describe("SMS login or register", () => {
  // SHOP-TASK-076 接口记录（app/api/member_auth.py 文档字符串的结果表）：200 {"created": 布尔}、422 code_wrong、422 code_expired（过期与没有待核验记录）、
  // 403 sms_disabled、503（sms_unavailable 与数据库未配置等，不论错误码）。code_wrong 与 code_expired 都归 wrong；
  // 200 而 created 不是布尔算 failed；422 不是 phone_invalid 的出错列表与未列出的状态归 failed。
  const replies: [string, Reply, SmsLoginReply][] = [
    ["200 created", () => json(200, { created: true }), { kind: "ok", created: true }],
    ["200 existing", () => json(200, { created: false }), { kind: "ok", created: false }],
    ["200 created not boolean", () => json(200, { created: "true" }), { kind: "failed" }],
    ["200 created missing", () => json(200, {}), { kind: "failed" }],
    ["200 not JSON", () => new Response("ok", { status: 200 }), { kind: "failed" }],
    ["422 code_wrong", () => json(422, { detail: "code_wrong" }), { kind: "wrong" }],
    ["422 code_expired", () => json(422, { detail: "code_expired" }), { kind: "wrong" }],
    ["422 phone_invalid", () => invalid("phone_invalid"), { kind: "phone_invalid" }],
    ["422 other validation", () => invalid("string_too_long"), { kind: "failed" }],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), { kind: "disabled" }],
    ["403 other code", () => json(403, { detail: "captcha_failed" }), { kind: "failed" }],
    ["503 sms_unavailable", () => json(503, { detail: "sms_unavailable" }), { kind: "unavailable" }],
    ["503 other code", () => json(503, { detail: "database is not configured" }), { kind: "unavailable" }],
    ["409", () => json(409, { detail: "code_wrong" }), { kind: "failed" }],
    ["429", () => json(429, { detail: "rate_limited" }), { kind: "failed" }],
    ["network", () => "network", { kind: "network" }],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(smsLogin(PHONE, "login", CODE), name).resolves.toEqual(expected);
  });
});

describe("password login", () => {
  // SHOP-TASK-077 接口记录（app/api/member_pw.py 文档字符串）：成功 204 且响应体为空；锁定、未注册、未设密码、密码错误与已注销一律 401 login_failed；
  // Redis 或数据库不可用 503；号码不成立 422 phone_invalid。其余（含 401 别的错误码、422 别的出错与 409）归 failed。
  const replies: [string, Reply, PasswordLoginReply][] = [
    ["204", () => empty(204), "ok"],
    ["401 login_failed", () => json(401, { detail: "login_failed" }), "login_failed"],
    ["401 other code", () => json(401, { detail: "member_session_required" }), "failed"],
    ["503", () => json(503, { detail: "service_unavailable" }), "unavailable"],
    ["422 phone_invalid", () => invalid("phone_invalid"), "phone_invalid"],
    ["422 other validation", () => invalid("string_too_short"), "failed"],
    ["409", () => json(409, { detail: "password_already_set" }), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(passwordLogin(PHONE, PASSWORD), name).resolves.toBe(expected);
  });
});

describe("password reset verify", () => {
  // SHOP-TASK-079 接口记录（app/api/member_pw_reset.py 文档字符串「验证的处理顺序」）与 UX P12 忘记密码「验证通过后号码已注册才显示新密码输入，未注册不创建账号」：
  // 200 {"registered": true, "csrf_token": …} 与 200 {"registered": false}；错误同 sms-login（code_wrong、code_expired 422，sms_disabled 403，503 不论错误码）。
  // 200 而 registered 为真却没有非空字符串令牌、registered 缺少或不是布尔都算 failed。
  const replies: [string, Reply, ResetVerifyReply][] = [
    ["200 registered", () => json(200, { registered: true, csrf_token: CSRF }), { kind: "registered", csrfToken: CSRF }],
    ["200 not registered", () => json(200, { registered: false }), { kind: "not_registered" }],
    ["200 registered without token", () => json(200, { registered: true }), { kind: "failed" }],
    ["200 registered with empty token", () => json(200, { registered: true, csrf_token: "" }), { kind: "failed" }],
    ["200 registered with numeric token", () => json(200, { registered: true, csrf_token: 42 }), { kind: "failed" }],
    ["200 registered not boolean", () => json(200, { registered: "true", csrf_token: CSRF }), { kind: "failed" }],
    ["200 not an object", () => json(200, [true]), { kind: "failed" }],
    ["200 not JSON", () => new Response("ok", { status: 200 }), { kind: "failed" }],
    ["422 code_wrong", () => json(422, { detail: "code_wrong" }), { kind: "wrong" }],
    ["422 code_expired", () => json(422, { detail: "code_expired" }), { kind: "wrong" }],
    ["422 phone_invalid", () => invalid("phone_invalid"), { kind: "phone_invalid" }],
    ["422 other validation", () => invalid("extra_forbidden"), { kind: "failed" }],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), { kind: "disabled" }],
    ["403 other code", () => json(403, { detail: "csrf_failed" }), { kind: "failed" }],
    ["503 sms_unavailable", () => json(503, { detail: "sms_unavailable" }), { kind: "unavailable" }],
    ["503 service_unavailable", () => json(503, { detail: "service_unavailable" }), { kind: "unavailable" }],
    ["409", () => json(409, { detail: "code_wrong" }), { kind: "failed" }],
    ["network", () => "network", { kind: "network" }],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(verifyPasswordReset(PHONE, CODE), name).resolves.toEqual(expected);
  });
});

describe("password reset submit", () => {
  // SHOP-TASK-079 接口记录「提交新密码的处理顺序」：成功 204；sms_disabled 403；没有凭据或凭据已过期、已取用 401 reset_expired；
  // CSRF 不符 403 csrf_failed；密码不足 8 位 422 password_too_short；Redis 或数据库不可用 503。
  // 错误码不符（含 422 的出错列表）与未列出的状态归 failed。
  const replies: [string, Reply, ResetSubmitReply][] = [
    ["204", () => empty(204), "ok"],
    ["401 reset_expired", () => json(401, { detail: "reset_expired" }), "expired"],
    ["401 other code", () => json(401, { detail: "member_session_required" }), "failed"],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), "disabled"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "captcha_failed" }), "failed"],
    ["422 password_too_short", () => json(422, { detail: "password_too_short" }), "too_short"],
    ["422 validation list", () => invalid("string_too_long"), "failed"],
    ["503", () => json(503, { detail: "service_unavailable" }), "unavailable"],
    ["409", () => json(409, { detail: "reset_expired" }), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(submitPasswordReset(PASSWORD, CSRF), name).resolves.toBe(expected);
  });
});
