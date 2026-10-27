import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MEMBER_COUPONS_URL,
  MEMBER_DELETE_SEND_URL,
  MEMBER_DELETE_URL,
  MEMBER_DELETE_VERIFY_URL,
  MEMBER_PASSWORD_URL,
  MEMBER_POINTS_URL,
  confirmDeletion,
  readMemberCoupons,
  readMemberPoints,
  sendDeleteCode,
  setMemberPassword,
  verifyDeleteCode,
} from "./memberAccount";
import type {
  DeleteConfirmReply,
  DeleteSendReply,
  DeleteVerifyReply,
  MemberCoupons,
  MemberCouponsRead,
  MemberPoints,
  MemberPointsRead,
  SetPasswordReply,
} from "./memberAccount";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的密码、验证码与令牌都是虚构值。
const PASSWORD = "correct horse battery";
const CODE = "123456";
const CAPTCHA = "captcha-token-example";
const CSRF = "csrf-token-example";

const POINTS: MemberPoints = {
  balance: 1200,
  pending: 30,
  next_expiry: { points: 200, date: "2026-12-31" },
  history: [
    { type: "earned", points: 88, order_number: "B6TN2RJD8K4M0QXZ", created_at: "2026-10-05T10:02:00Z" },
    { type: "redeemed", points: -200, order_number: "K7Q29MXA00000000", created_at: "2026-10-01T09:00:00Z" },
    { type: "reversed", points: -27, order_number: "B6TN2RJD8K4M0QXZ", created_at: "2026-10-06T08:00:00Z" },
  ],
};

const COUPONS: MemberCoupons = {
  available: [
    { code: "WELCOME10", type: "fixed", amount_sen: 1000, percent: null, min_spend_sen: 5000, valid_until: "2026-12-31T15:59:59Z" },
    { code: "SAVE15", type: "percent", amount_sen: null, percent: 15, min_spend_sen: 0, valid_until: "2026-11-30T15:59:59Z" },
  ],
  used: [{ code: "FIRST5", order_number: "K7Q29MXA00000000", discount_sen: 500 }],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

// 请求体不合格的 422（app/api/pay.py 的 _body_errors）：出错列表，每条有位置、类型与消息。
function invalid(type: string): Response {
  return json(422, { detail: [{ loc: ["body", "password"], type, msg: "invalid" }] });
}

type Reply = () => Response | "network";

// fetch 替身：每次请求调用 reply 取回答（"network" 时抛出网络错误）；参数带类型，请求从 mock.calls 取。
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

// 把 localStorage、sessionStorage、document.cookie 与 console 换成替身，返回检查用的记录。
function stubBrowser() {
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
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
  vi.stubGlobal("document", doc);
  const logs = (["log", "info", "warn", "error", "debug", "trace"] as const).map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
  return { local, session, cookieWrites, logs };
}

describe("member account requests", () => {
  // 六个接口记录（app/api/member_account.py；app/api/member_pw.py「首次设置密码」与 app/api/member_deletion.py「请求体结构」，请求模型都是
  // extra="forbid"、号码一律取当前会员的）、DESIGN 1.14「权限与资料保护」第 5 条「写操作另须 CSRF 令牌」与本任务验收「都不带查询参数」：
  // 地址恰好是接口路径，同源 cookie、不缓存、带上调用方的 signal；读取是 GET、请求头只有 Accept、没有请求体；
  // 写操作是 POST、请求头只有 Accept、Content-Type 与 X-CSRF-Token，请求体只有约定字段。
  const reads: [string, (signal: AbortSignal) => Promise<unknown>, string][] = [
    ["points", (signal) => readMemberPoints(signal), MEMBER_POINTS_URL],
    ["coupons", (signal) => readMemberCoupons(signal), MEMBER_COUPONS_URL],
  ];
  it.each(reads)("%s reads a fixed address without a CSRF token or body", async (name, call, url) => {
    const request = stubFetch(() => empty(500));
    const controller = new AbortController();
    await call(controller.signal);
    const calls = requests(request);
    expect(calls.map((item) => [item.method, item.url]), name).toEqual([["GET", url]]);
    expect(calls[0]?.url, name).not.toContain("?");
    expect(calls[0]?.init?.credentials, name).toBe("same-origin");
    expect(calls[0]?.init?.cache, name).toBe("no-store");
    expect(calls[0]?.init?.signal, name).toBe(controller.signal);
    expect(calls[0]?.init?.headers, name).toEqual({ Accept: "application/json" });
    expect(calls[0]?.init?.body, name).toBeUndefined();
  });

  const writes: [string, (signal: AbortSignal) => Promise<unknown>, string, unknown][] = [
    ["set password", (signal) => setMemberPassword(PASSWORD, CSRF, signal), MEMBER_PASSWORD_URL, { password: PASSWORD }],
    ["send delete code", (signal) => sendDeleteCode(CAPTCHA, CSRF, signal), MEMBER_DELETE_SEND_URL, { captcha_token: CAPTCHA }],
    ["verify delete code", (signal) => verifyDeleteCode(CODE, CSRF, signal), MEMBER_DELETE_VERIFY_URL, { code: CODE }],
    ["confirm deletion with password", (signal) => confirmDeletion(PASSWORD, CSRF, signal), MEMBER_DELETE_URL, { password: PASSWORD }],
    // member_deletion.py「确认注销只有可省略的 password（显式 null 也是 422）」：没有密码时请求体为空对象，不带 password 键。
    ["confirm deletion without password", (signal) => confirmDeletion(null, CSRF, signal), MEMBER_DELETE_URL, {}],
  ];
  it.each(writes)("%s posts only the agreed fields with the CSRF header", async (name, call, url, body) => {
    const request = stubFetch(() => empty(500));
    const controller = new AbortController();
    await call(controller.signal);
    const calls = requests(request);
    expect(calls.map((item) => [item.method, item.url]), name).toEqual([["POST", url]]);
    expect(calls[0]?.url, name).not.toContain("?");
    expect(calls[0]?.init?.credentials, name).toBe("same-origin");
    expect(calls[0]?.init?.cache, name).toBe("no-store");
    expect(calls[0]?.init?.signal, name).toBe(controller.signal);
    expect(calls[0]?.init?.headers, name).toEqual({ Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": CSRF });
    expect(bodyOf(calls[0]?.init), name).toEqual(body);
  });

  // 本任务验收「密码、验证码、人机挑战令牌与 CSRF 令牌只在请求体与请求头里，不进地址、日志或浏览器存储」与「整个过程不写 localStorage、
  // sessionStorage 与 cookie」：六个请求的地址里都没有这些值，过程中不调用存储与控制台、不写 cookie。
  it("keeps secrets out of the address, logs and browser storage", async () => {
    const browser = stubBrowser();
    const request = stubFetch(() => empty(204));
    await readMemberPoints();
    await readMemberCoupons();
    await setMemberPassword(PASSWORD, CSRF);
    await sendDeleteCode(CAPTCHA, CSRF);
    await verifyDeleteCode(CODE, CSRF);
    await confirmDeletion(PASSWORD, CSRF);
    await confirmDeletion(null, CSRF);
    const calls = requests(request);
    expect(calls).toHaveLength(7);
    for (const { url } of calls) {
      for (const secret of [PASSWORD, CODE, CAPTCHA, CSRF]) {
        expect(url).not.toContain(secret);
      }
    }
    for (const store of [browser.local, browser.session]) {
      expect(store.setItem).not.toHaveBeenCalled();
      expect(store.getItem).not.toHaveBeenCalled();
      expect(store.removeItem).not.toHaveBeenCalled();
      expect(store.clear).not.toHaveBeenCalled();
    }
    expect(browser.cookieWrites).toEqual([]);
    for (const log of browser.logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });
});

describe("reading points", () => {
  // app/api/member_account.py 的 PointsOut 与 UX 0.13 P13「我的积分」：200 给出 balance、pending、next_expiry（points 与马来西亚日期，或 null）
  // 与 history（type 五种之一，points 带正负号）；照 memberOrders.ts 以类型断言读取。401 member_session_required 归 signed_out；
  // 别的 401 错误码、403 csrf_failed（读取没有 CSRF）、503、200 以外的 2xx 与不是 JSON 归 failed；网络中断归 network。
  const replies: [string, Reply, MemberPointsRead][] = [
    ["200", () => json(200, POINTS), { kind: "ok", ...POINTS }],
    ["200 without next expiry", () => json(200, { ...POINTS, next_expiry: null, history: [] }), { kind: "ok", ...POINTS, next_expiry: null, history: [] }],
    ["200 not JSON", () => new Response("<html>", { status: 200 }), { kind: "failed" }],
    ["204", () => empty(204), { kind: "failed" }],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), { kind: "signed_out" }],
    ["401 other code", () => json(401, { detail: "login_failed" }), { kind: "failed" }],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), { kind: "failed" }],
    ["503", () => json(503, { detail: "service_unavailable" }), { kind: "failed" }],
    ["network", () => "network", { kind: "network" }],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(readMemberPoints(), name).resolves.toEqual(expected);
  });

  // 同上：结果只取接口记录的四个字段，响应体里多出的字段不带进结果。
  it("takes only the recorded fields", async () => {
    stubFetch(() => json(200, { ...POINTS, member_id: 7 }));
    await expect(readMemberPoints()).resolves.toEqual({ kind: "ok", ...POINTS });
  });
});

describe("reading coupons", () => {
  // app/api/member_account.py 的 CouponsOut 与 UX 0.13 P13「我的优惠券」：200 给出 available（code、type 为 fixed 或 percent、amount_sen、
  // percent、min_spend_sen 与 valid_until）与 used（code、order_number 与 discount_sen）；照 memberOrders.ts 以类型断言读取。
  // 401 member_session_required 归 signed_out；其余同积分。
  const replies: [string, Reply, MemberCouponsRead][] = [
    ["200", () => json(200, COUPONS), { kind: "ok", ...COUPONS }],
    ["200 empty", () => json(200, { available: [], used: [] }), { kind: "ok", available: [], used: [] }],
    ["200 not JSON", () => new Response("<html>", { status: 200 }), { kind: "failed" }],
    ["204", () => empty(204), { kind: "failed" }],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), { kind: "signed_out" }],
    ["401 other code", () => json(401, { detail: "login_failed" }), { kind: "failed" }],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), { kind: "failed" }],
    ["503", () => json(503, { detail: "service_unavailable" }), { kind: "failed" }],
    ["network", () => "network", { kind: "network" }],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(readMemberCoupons(), name).resolves.toEqual(expected);
  });
});

describe("setting the first password", () => {
  // app/api/member_pw.py「首次设置密码的处理顺序」与 DESIGN 1.14「权限与资料保护」第 3 条（首次设置密码在已登录会话内、至少 8 位）：
  // 204 成功；401 member_session_required；403 csrf_failed；422 password_too_short；409 password_already_set；数据库出错 503。
  // 结构不合格的 422（出错列表）、错误码与状态码对不上与未列出的状态归 failed。
  const replies: [string, Reply, SetPasswordReply][] = [
    ["204", () => empty(204), "done"],
    ["422 password_too_short", () => json(422, { detail: "password_too_short" }), "too_short"],
    ["422 validation list", () => invalid("string_too_long"), "failed"],
    ["409 password_already_set", () => json(409, { detail: "password_already_set" }), "already_set"],
    ["409 other code", () => json(409, { detail: "idempotency_conflict" }), "failed"],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), "signed_out"],
    ["401 other code", () => json(401, { detail: "login_failed" }), "failed"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "sms_disabled" }), "failed"],
    ["503", () => json(503, { detail: "service_unavailable" }), "unavailable"],
    ["413", () => json(413, { detail: "request body too large" }), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(setMemberPassword(PASSWORD, CSRF), name).resolves.toBe(expected);
  });
});

describe("sending the deletion code", () => {
  // app/api/member_deletion.py「发送」：结果与 POST /api/sms/send 相同（app/api/sms.py 的 _ERRORS：403 sms_disabled、403 captcha_failed、
  // 422 not_whitelisted、429 rate_limited、422 sms_undeliverable、503 sms_suspended），另有 401 member_session_required 与 403 csrf_failed；
  // 号码由服务端取本账号的，没有 phone_invalid（结构不合格的 422 归 failed）。错误码不符与未列出的状态归 failed。
  const replies: [string, Reply, DeleteSendReply][] = [
    ["200", () => json(200, { status: "sent" }), "sent"],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), "disabled"],
    ["403 captcha_failed", () => json(403, { detail: "captcha_failed" }), "captcha_failed"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "delete_not_verified" }), "failed"],
    ["422 not_whitelisted", () => json(422, { detail: "not_whitelisted" }), "not_whitelisted"],
    ["422 sms_undeliverable", () => json(422, { detail: "sms_undeliverable" }), "undeliverable"],
    ["422 validation list", () => invalid("phone_invalid"), "failed"],
    ["429", () => json(429, { detail: "rate_limited" }), "rate_limited"],
    ["503 sms_suspended", () => json(503, { detail: "sms_suspended" }), "suspended"],
    ["503 other code", () => json(503, { detail: "service_unavailable" }), "failed"],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), "signed_out"],
    ["401 other code", () => json(401, { detail: "login_failed" }), "failed"],
    ["409", () => json(409, { detail: "sms_disabled" }), "failed"],
    ["500 not JSON", () => new Response("<html>", { status: 500 }), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(sendDeleteCode(CAPTCHA, CSRF), name).resolves.toBe(expected);
  });
});

describe("verifying the deletion code", () => {
  // app/api/member_deletion.py「核验」与 HANDOFF 0.45（核验通过后服务端签发 10 分钟一次性批准）：204 通过；422 code_wrong 与 code_expired
  // （二者同样处理）；开关关闭 403 sms_disabled（DESIGN 1.14「边界与原则」第 4 条：按当时读取的开关判定）；Redis 或数据库出错 503
  // （service_unavailable 与 sms_unavailable，不论错误码）；401 member_session_required；403 csrf_failed。其余归 failed。
  const replies: [string, Reply, DeleteVerifyReply][] = [
    ["204", () => empty(204), "verified"],
    ["422 code_wrong", () => json(422, { detail: "code_wrong" }), "wrong"],
    ["422 code_expired", () => json(422, { detail: "code_expired" }), "wrong"],
    ["422 validation list", () => invalid("string_too_long"), "failed"],
    ["403 sms_disabled", () => json(403, { detail: "sms_disabled" }), "disabled"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "captcha_failed" }), "failed"],
    ["503 sms_unavailable", () => json(503, { detail: "sms_unavailable" }), "unavailable"],
    ["503 service_unavailable", () => json(503, { detail: "service_unavailable" }), "unavailable"],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), "signed_out"],
    ["401 other code", () => json(401, { detail: "reset_expired" }), "failed"],
    ["429", () => json(429, { detail: "rate_limited" }), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(verifyDeleteCode(CODE, CSRF), name).resolves.toBe(expected);
  });
});

describe("confirming deletion", () => {
  // app/api/member_deletion.py「确认注销」与 DESIGN 1.14「权限与资料保护」第 3 条（先以短信验证码确认，开关关闭时以当前密码或会话内二次确认）：
  // 204 注销并清除会话 cookie；开关开启而取不到批准 403 delete_not_verified；开关关闭且已设密码而未给密码 422 password_required、
  // 密码不对 403 password_wrong、锁定中 429 rate_limited；数据库或 Redis 出错 503；401 member_session_required（含已不是 active）；
  // 403 csrf_failed。结构不合格的 422 与其余归 failed。
  const replies: [string, Reply, DeleteConfirmReply][] = [
    ["204", () => empty(204), "deleted"],
    ["403 delete_not_verified", () => json(403, { detail: "delete_not_verified" }), "not_verified"],
    ["403 password_wrong", () => json(403, { detail: "password_wrong" }), "password_wrong"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "sms_disabled" }), "failed"],
    ["422 password_required", () => json(422, { detail: "password_required" }), "password_required"],
    ["422 validation list", () => invalid("string_type"), "failed"],
    ["429", () => json(429, { detail: "rate_limited" }), "rate_limited"],
    ["503", () => json(503, { detail: "service_unavailable" }), "unavailable"],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), "signed_out"],
    ["401 other code", () => json(401, { detail: "login_failed" }), "failed"],
    ["409", () => json(409, { detail: "password_wrong" }), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(confirmDeletion(PASSWORD, CSRF), name).resolves.toBe(expected);
  });
});
