import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_STRIPE_PENDING_URL,
  CHECKOUT_STATUSES,
  MAX_CHECKOUT_ID,
  readStripePending,
  retryLateRefund,
  stripeRefundRetryUrl,
  stripeVoidUrl,
  voidCheckout,
} from "./adminStripePending";
import { CSRF_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const TOKEN = "csrf-token-for-admin";
const OPEN = { checkout_id: 41, status: "open", order_number: "KS-20261108-0007", created_at: "2026-11-08T02:00:00Z", alerted_at: "2026-11-08T03:00:00Z" };
const CREATING = { checkout_id: 42, status: "creating", order_number: "KS-20261108-0008", created_at: "2026-11-08T02:10:00Z", alerted_at: "2026-11-08T03:10:00Z" };
const FAILED_REFUND = {
  checkout_id: 37,
  attempt_id: 9,
  order_number: "KS-20261107-0003",
  amount_sen: 12900,
  attempted_at: "2026-11-07T10:00:00Z",
  error_type: "invalid_request_error",
};
const UNTYPED_REFUND = { ...FAILED_REFUND, checkout_id: 38, attempt_id: 11, amount_sen: 0, error_type: null };
const PENDING = { checkouts: [OPEN, CREATING], refunds: [FAILED_REFUND, UNTYPED_REFUND], csrf_token: TOKEN };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
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

function stubConsole() {
  return (["log", "info", "warn", "error", "debug", "trace"] as const).map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
}

describe("reading the Stripe follow-ups", () => {
  // SHOP-TASK-225 记录段「GET ""…不要求 CSRF、不写审计」与 UX 0.19 A11「两个列表来自同一个接口，打开页面时读取一次；不分页」：
  // 固定地址、没有查询参数；GET、同源 cookie、不缓存、Accept 为 application/json，没有请求体与 CSRF 头，带上中止用的 signal。
  it("gets the page with the same-origin cookie and no cache", async () => {
    const calls = stubFetch(json(200, PENDING));
    const controller = new AbortController();
    await readStripePending(controller.signal);
    expect(ADMIN_STRIPE_PENDING_URL).toBe("/api/admin/stripe-pending");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stripe-pending");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(headers(calls[0])).toEqual({ Accept: "application/json" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-225 记录段「返回 checkouts、refunds（原样取自 StripePendingOut）与 csrf_token」与 UX 0.19 A11「各按服务端给出的顺序显示」：
  // 取到时按接口顺序原样返回两个列表与令牌；error_type 为 null、金额为 0 合格；多出的字段不带进结果。
  it("keeps the agreed fields in the server's order", async () => {
    stubFetch(
      json(200, {
        ...PENDING,
        extra: "ignored",
        checkouts: [{ ...OPEN, stripe_session_id: "cs_test_1", checkout_url: "https://checkout.stripe.com/x" }, CREATING],
        refunds: [{ ...FAILED_REFUND, payment_intent_id: "pi_1" }, UNTYPED_REFUND],
      }),
    );
    await expect(readStripePending()).resolves.toEqual({ kind: "ok", pending: PENDING });
  });

  // UX 0.19 A11「两类都没有记录时，各自标题下显示 [admin.stripe_pending_none]」：两个空列表合格。
  it("accepts two empty lists", async () => {
    const empty = { checkouts: [], refunds: [], csrf_token: TOKEN };
    stubFetch(json(200, empty));
    await expect(readStripePending()).resolves.toEqual({ kind: "ok", pending: empty });
  });

  // UX 0.19 A11「记录状态：creating…open…complete」：三种状态都合格。
  it.each(CHECKOUT_STATUSES)("accepts the status %s", async (status) => {
    const body = { ...PENDING, checkouts: [{ ...OPEN, status }] };
    stubFetch(json(200, body));
    await expect(readStripePending()).resolves.toEqual({ kind: "ok", pending: body });
  });

  // SHOP-TASK-225 记录段「require_admin（401 admin_session_required）」与 UX 0.19 A11「读取失败显示 [common.error_retry]」「后台会话失效时
  // 同其他后台页面回到 A01」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, { detail: "not_found" }), "failed"],
    [json(500, {}), "failed"],
    [json(503, { detail: "stripe_unavailable" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readStripePending()).resolves.toEqual({ kind });
  });

  // UX 0.19 A11「读取失败显示 [common.error_retry]」与 SHOP-TASK-225 记录段「响应只有结果字符串、两类列表的对账字段与订单号、CSRF 令牌」：
  // 令牌、两个列表与每项各字段缺少或类型不对都算 failed。
  it.each<[string, unknown]>([
    ["not an object", [PENDING]],
    ["null body", null],
    ["token missing", { checkouts: [], refunds: [] }],
    ["token empty", { ...PENDING, csrf_token: "" }],
    ["token as number", { ...PENDING, csrf_token: 1 }],
    ["token null", { ...PENDING, csrf_token: null }],
    ["checkouts missing", { refunds: [], csrf_token: TOKEN }],
    ["checkouts not an array", { ...PENDING, checkouts: {} }],
    ["checkouts null", { ...PENDING, checkouts: null }],
    ["refunds missing", { checkouts: [], csrf_token: TOKEN }],
    ["refunds not an array", { ...PENDING, refunds: "none" }],
    ["checkout not an object", { ...PENDING, checkouts: [null] }],
    ["checkout as array", { ...PENDING, checkouts: [[41]] }],
    ["checkout ID missing", { ...PENDING, checkouts: [{ ...OPEN, checkout_id: undefined }] }],
    ["checkout ID zero", { ...PENDING, checkouts: [{ ...OPEN, checkout_id: 0 }] }],
    ["checkout ID negative", { ...PENDING, checkouts: [{ ...OPEN, checkout_id: -1 }] }],
    ["checkout ID fractional", { ...PENDING, checkouts: [{ ...OPEN, checkout_id: 4.5 }] }],
    ["checkout ID as string", { ...PENDING, checkouts: [{ ...OPEN, checkout_id: "41" }] }],
    ["status missing", { ...PENDING, checkouts: [{ ...OPEN, status: undefined }] }],
    ["status expired", { ...PENDING, checkouts: [{ ...OPEN, status: "expired" }] }],
    ["status paid", { ...PENDING, checkouts: [{ ...OPEN, status: "paid" }] }],
    ["status in capitals", { ...PENDING, checkouts: [{ ...OPEN, status: "OPEN" }] }],
    ["checkout order number missing", { ...PENDING, checkouts: [{ ...OPEN, order_number: undefined }] }],
    ["checkout order number as number", { ...PENDING, checkouts: [{ ...OPEN, order_number: 7 }] }],
    ["created at missing", { ...PENDING, checkouts: [{ ...OPEN, created_at: undefined }] }],
    ["created at null", { ...PENDING, checkouts: [{ ...OPEN, created_at: null }] }],
    ["alerted at missing", { ...PENDING, checkouts: [{ ...OPEN, alerted_at: undefined }] }],
    ["alerted at as number", { ...PENDING, checkouts: [{ ...OPEN, alerted_at: 1762570800 }] }],
    ["refund not an object", { ...PENDING, refunds: [7] }],
    ["refund checkout ID zero", { ...PENDING, refunds: [{ ...FAILED_REFUND, checkout_id: 0 }] }],
    ["refund checkout ID as string", { ...PENDING, refunds: [{ ...FAILED_REFUND, checkout_id: "37" }] }],
    ["attempt ID missing", { ...PENDING, refunds: [{ ...FAILED_REFUND, attempt_id: undefined }] }],
    ["attempt ID zero", { ...PENDING, refunds: [{ ...FAILED_REFUND, attempt_id: 0 }] }],
    ["attempt ID fractional", { ...PENDING, refunds: [{ ...FAILED_REFUND, attempt_id: 9.5 }] }],
    ["refund order number missing", { ...PENDING, refunds: [{ ...FAILED_REFUND, order_number: undefined }] }],
    ["refund order number null", { ...PENDING, refunds: [{ ...FAILED_REFUND, order_number: null }] }],
    ["amount missing", { ...PENDING, refunds: [{ ...FAILED_REFUND, amount_sen: undefined }] }],
    ["amount negative", { ...PENDING, refunds: [{ ...FAILED_REFUND, amount_sen: -1 }] }],
    ["amount fractional", { ...PENDING, refunds: [{ ...FAILED_REFUND, amount_sen: 129.5 }] }],
    ["amount as string", { ...PENDING, refunds: [{ ...FAILED_REFUND, amount_sen: "12900" }] }],
    ["attempted at missing", { ...PENDING, refunds: [{ ...FAILED_REFUND, attempted_at: undefined }] }],
    ["attempted at null", { ...PENDING, refunds: [{ ...FAILED_REFUND, attempted_at: null }] }],
    ["error type missing", { ...PENDING, refunds: [{ ...FAILED_REFUND, error_type: undefined }] }],
    ["error type as number", { ...PENDING, refunds: [{ ...FAILED_REFUND, error_type: 402 }] }],
    ["one bad item among good ones", { ...PENDING, refunds: [FAILED_REFUND, { ...UNTYPED_REFUND, attempt_id: -2 }] }],
  ])("treats a malformed page as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readStripePending()).resolves.toEqual({ kind: "failed" });
  });

  // 同上「读取失败显示 [common.error_retry]」：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readStripePending()).resolves.toEqual({ kind: "failed" });
  });
});

describe("voiding a checkout record", () => {
  // SHOP-TASK-225 记录段「POST /checkouts/{checkout_id}/void…require_admin_csrf（403）…请求体 VoidIn（extra="forbid"、strict=True、
  // verified: Literal[True]）」与 UX 0.19 A11「[admin.stripe_void_verified] 未勾选时 [admin.stripe_void_submit] 禁用」：地址只带结账记录 ID；
  // POST、同源 cookie、不缓存；请求头为 Accept、Content-Type 与列表给的 X-CSRF-Token；JSON 请求体恰为 {"verified": true}。
  it("posts verified true with the CSRF token", async () => {
    const calls = stubFetch(json(200, { result: "voided" }));
    await expect(voidCheckout(41, TOKEN)).resolves.toBe("voided");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stripe-pending/checkouts/41/void");
    expect(stripeVoidUrl(MAX_CHECKOUT_ID)).toBe("/api/admin/stripe-pending/checkouts/2147483647/void");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(headers(calls[0])).toEqual({ Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: TOKEN });
    expect(typeof calls[0]?.init.body).toBe("string");
    expect(JSON.parse(String(calls[0]?.init.body))).toStrictEqual({ verified: true });
  });

  // SHOP-TASK-225 记录段「路径里的记录 ID（[1-9][0-9]{0,9} 且不超过 2³¹−1）」：ID 不是不超过上限的正整数时不发请求，归为失败。
  it.each([0, -1, 1.5, MAX_CHECKOUT_ID + 1, Number.NaN, Number.POSITIVE_INFINITY])("sends nothing for the ID %d", async (id) => {
    const calls = stubFetch(json(200, { result: "voided" }));
    await expect(voidCheckout(id, TOKEN)).resolves.toBe("failed");
    expect(calls).toEqual([]);
  });

  // SHOP-TASK-225 记录段「CheckoutNotFound 404 not_found；changed 409 stripe_pending_changed；unavailable 503 stripe_unavailable；confirmed、
  // cancelled、voided 200 {"result": 结果}」与 UX 0.19 A11 作废结果（「记录在此期间已变化，或记录不存在（404）：[admin.stripe_pending_changed]」
  // 「其他失败：[common.error_retry]」）：200 按 result 归类，结果不是三种之一或读不出为 failed；404 与 409 stripe_pending_changed 为 changed，
  // 503 stripe_unavailable 为 unavailable；别的 409、503、422 与不是 JSON 的错误体为 failed；网络中断为 network。
  it.each<[Response | (() => never), string]>([
    [json(200, { result: "confirmed" }), "confirmed"],
    [json(200, { result: "cancelled" }), "cancelled"],
    [json(200, { result: "voided" }), "voided"],
    [json(200, { result: "changed" }), "failed"],
    [json(200, { result: "unavailable" }), "failed"],
    [json(200, { result: "retried" }), "failed"],
    [json(200, { result: "VOIDED" }), "failed"],
    [json(200, { result: null }), "failed"],
    [json(200, {}), "failed"],
    [json(200, ["voided"]), "failed"],
    [json(200, "voided"), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(404, { detail: "not_found" }), "changed"],
    [new Response("not json", { status: 404 }), "changed"],
    [json(409, { detail: "stripe_pending_changed" }), "changed"],
    [json(409, { detail: "stripe_refund_not_retryable" }), "failed"],
    [json(409, { detail: ["stripe_pending_changed"] }), "failed"],
    [new Response("not json", { status: 409 }), "failed"],
    [json(503, { detail: "stripe_unavailable" }), "unavailable"],
    [json(503, { detail: "service_unavailable" }), "failed"],
    [new Response("not json", { status: 503 }), "failed"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(422, { detail: [{ loc: ["body", "verified"] }] }), "failed"],
    [json(413, {}), "failed"],
    [json(415, {}), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(voidCheckout(41, TOKEN)).resolves.toBe(kind);
  });
});

describe("retrying a late refund", () => {
  // SHOP-TASK-225 记录段「POST /checkouts/{checkout_id}/refund-retry：照 admin_refunds.py 的 stripe-retry，不读请求体、不要幂等键；
  // require_admin（401）→ require_admin_csrf（403）」：地址只带结账记录 ID；POST、同源 cookie、不缓存、没有请求体；请求头只有 Accept 与
  // 列表给的 X-CSRF-Token，没有 Content-Type 与幂等键。
  it("posts nothing but the CSRF token", async () => {
    const calls = stubFetch(json(200, { result: "retried" }));
    await expect(retryLateRefund(37, TOKEN)).resolves.toBe("done");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stripe-pending/checkouts/37/refund-retry");
    expect(stripeRefundRetryUrl(MAX_CHECKOUT_ID)).toBe("/api/admin/stripe-pending/checkouts/2147483647/refund-retry");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(headers(calls[0])).toEqual({ Accept: "application/json", [CSRF_HEADER]: TOKEN });
  });

  // SHOP-TASK-225 记录段「require_admin_csrf（403）→ 路径（422）」与路径 ID 的规则「[1-9][0-9]{0,9} 且不超过 2³¹−1」：
  // ID 不是不超过上限的正整数时不发请求，归为失败。
  it.each([0, -1, 1.5, MAX_CHECKOUT_ID + 1, Number.NaN, Number.POSITIVE_INFINITY])("sends nothing for the ID %d", async (id) => {
    const calls = stubFetch(json(200, { result: "retried" }));
    await expect(retryLateRefund(id, TOKEN)).resolves.toBe("failed");
    expect(calls).toEqual([]);
  });

  // SHOP-TASK-225 记录段「仍回 200 {"result": "retried"}」与 UX 0.19 A11「成功显示 [admin.stripe_late_refund_retried] 并重新读取」：
  // 200 即为 done，不读响应体（响应体不是 JSON 也一样）。
  it("does not read the body of a success", async () => {
    const reply = new Response("not json", { status: 200 });
    stubFetch(reply);
    await expect(retryLateRefund(37, TOKEN)).resolves.toBe("done");
    expect(reply.bodyUsed).toBe(false);
  });

  // SHOP-TASK-225 记录段「RetryCheckoutNotFound 404 not_found，NoRefundAttempt 与 RetryNotAllowed 409 stripe_refund_not_retryable，
  // RefundCapExceeded 409 stripe_refund_cap」与 UX 0.19 A11「不能重试…或记录不存在（404）显示 [admin.stripe_pending_changed]…会超过付款金额
  // 显示…[admin.stripe_refund_blocked]；其他失败显示 [common.error_retry]」：404 与 409 stripe_refund_not_retryable 为 changed，
  // 409 stripe_refund_cap 为 cap；别的 409、不是 JSON 的 409、422 与其他状态为 failed；网络中断为 network。
  it.each<[Response | (() => never), string]>([
    [json(200, { result: "retried" }), "done"],
    [json(404, { detail: "not_found" }), "changed"],
    [new Response("not json", { status: 404 }), "changed"],
    [json(409, { detail: "stripe_refund_not_retryable" }), "changed"],
    [json(409, { detail: "stripe_refund_cap" }), "cap"],
    [json(409, { detail: "stripe_pending_changed" }), "failed"],
    [json(409, { detail: "refund_not_approved" }), "failed"],
    [json(409, { detail: ["stripe_refund_cap"] }), "failed"],
    [new Response("not json", { status: 409 }), "failed"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(422, { detail: "invalid_checkout_id" }), "failed"],
    [json(503, { detail: "stripe_unavailable" }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(retryLateRefund(37, TOKEN)).resolves.toBe(kind);
  });
});

describe("keeping the token and the replies to themselves", () => {
  // SHOP-TASK-225 记录段「响应只有结果字符串、两类列表的对账字段与订单号、CSRF 令牌」与 UX 0.19 A11「不显示 Stripe 的任何 ID」：
  // 三个函数都不写 console；令牌只在请求头、不进地址；写操作的结果只是归类字符串，不带令牌与响应内容；网络中断时也不抛出。
  it("logs nothing and returns only the kind", async () => {
    const logs = stubConsole();
    const calls = stubFetch(
      json(200, PENDING),
      json(500, { detail: "boom", trace: TOKEN }),
      networkDown,
      json(200, { result: "voided", secret: "pi_123" }),
      json(409, { detail: "stripe_pending_changed", trace: "pi_123" }),
      networkDown,
      json(200, { result: "retried", payment_intent_id: "pi_123" }),
      json(409, { detail: "stripe_refund_cap", trace: "pi_123" }),
      networkDown,
    );
    await readStripePending();
    await expect(readStripePending()).resolves.toEqual({ kind: "failed" });
    await expect(readStripePending()).resolves.toEqual({ kind: "network" });
    const replies = [
      await voidCheckout(41, TOKEN),
      await voidCheckout(41, TOKEN),
      await voidCheckout(41, TOKEN),
      await retryLateRefund(37, TOKEN),
      await retryLateRefund(37, TOKEN),
      await retryLateRefund(37, TOKEN),
    ];
    expect(replies).toEqual(["voided", "changed", "network", "done", "cap", "network"]);
    expect(calls).toHaveLength(9);
    for (const call of calls) {
      expect(call.url).not.toContain(TOKEN);
    }
    for (const log of logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });
});
