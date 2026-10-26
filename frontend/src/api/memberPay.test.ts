import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearMemberPayOrder,
  confirmMemberPayOrder,
  confirmMemberStripe,
  MEMBER_ATTEMPTS_URL,
  MEMBER_CANCEL_URL,
  MEMBER_PAY_ORDER_KEY,
  MEMBER_STRIPE_CONFIRM_URL,
  memberPayOrderUrl,
  memberStripeStartUrl,
  readMemberPayOrder,
  runMemberCancel,
  runMemberPayment,
  startMemberStripe,
  stashedMemberPayOrder,
  stashMemberPayOrder,
  submitMemberCancel,
  submitMemberPayment,
} from "./memberPay";
import { attemptFor, CSRF_HEADER, IDEMPOTENCY_HEADER, readPayOrder, RECHECK_DELAY_MS, runCancel, runPayment } from "./pay";
import type { PaymentAttempt, PayOrder } from "./pay";

afterEach(() => {
  clearMemberPayOrder();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const OTHER_ORDER = "OLDERORDER000000";
const TOKEN = "csrf-token-1";
const CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3";

function memberOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "awaiting_demo_payment",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:01:00Z",
    subtotal_sen: 12000,
    coupon_discount_sen: 300,
    points_redeemed: 500,
    shipping_fee_sen: 1500,
    total_sen: 12700,
    points_earned: 70,
    recipient: {
      name: "Aina Rahman",
      phone: "+60123456789",
      country_code: "MY",
      region: "MY-10",
      address: "12 Jalan Contoh 3",
      postal_code: "47000",
    },
    last_payment: null,
    cancelled_by: null,
    payment_route: "simulated",
    stripe_state: null,
    stripe_refunded: false,
    ...overrides,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const signedOut = () => json(401, { detail: "member_session_required" });
const notFound = () => json(404, { detail: "order_not_found" });
const csrfFailed = () => json(403, { detail: "csrf_failed" });

// fetch 替身：按顺序给出回答（null 则抛出网络错误）；参数带类型，请求从 mock.calls 取。
function stubFetch(...replies: (Response | null)[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    void input;
    void init;
    const next = queue.shift();
    if (next === undefined) {
      return Promise.reject(new Error("unexpected request"));
    }
    return next === null ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function urlOf(fetchMock: FetchMock, index = 0): string {
  return String(fetchMock.mock.calls[index]?.[0]);
}

function initOf(fetchMock: FetchMock, index = 0): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

function headersOf(fetchMock: FetchMock, index = 0): Record<string, string> {
  return (initOf(fetchMock, index).headers ?? {}) as Record<string, string>;
}

function bodyOf(fetchMock: FetchMock, index = 0): unknown {
  const body = initOf(fetchMock, index).body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return JSON.parse(body) as unknown;
}

const JSON_HEADERS = { Accept: "application/json", "Content-Type": "application/json" };
const attempt: PaymentAttempt = { key: "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60", method: "card", result: "success" };
const noDelay = () => Promise.resolve();

// 浏览器存储替身（测试在 node 环境运行）：以 Map 保存，记下每次读写。
function fakeStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    items,
    getItem: vi.fn((key: string) => items.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      items.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      items.delete(key);
    }),
    clear: vi.fn(() => {
      items.clear();
    }),
  };
}

// 每次读写都抛错的存储（隐私模式、禁用存储）。
function brokenStorage() {
  const fail = (): never => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  return { getItem: vi.fn(fail), setItem: vi.fn(fail), removeItem: vi.fn(fail), clear: vi.fn(fail) };
}

describe("reading the member order", () => {
  // 接口记录 app/api/member_pay.py「查看（只读，不要求幂等键与 CSRF）……语言参数同 GET /api/pay/orders……请求体只有订单号」与
  // 验收第 2 条「读取的请求体只有 order_number、带 lang 查询参数……不带 CSRF 令牌与幂等键、接收 AbortSignal」：
  // POST、同源 cookie、不缓存，请求头只有 Accept 与 Content-Type；订单号不进地址；200 的订单原样交回，含三个快照字段，没有令牌。
  it("posts only the order number in the current language without a token or a key", async () => {
    const fetchMock = stubFetch(json(200, memberOrder()));
    const controller = new AbortController();
    await expect(readMemberPayOrder("zh", ORDER_NUMBER, controller.signal)).resolves.toEqual({ kind: "ok", order: memberOrder() });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urlOf(fetchMock)).toBe("/api/member/pay/order?lang=zh");
    expect(memberPayOrderUrl("ms")).toBe("/api/member/pay/order?lang=ms");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headersOf(fetchMock)).toEqual(JSON_HEADERS);
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
    expect(urlOf(fetchMock)).not.toContain(ORDER_NUMBER);
  });

  // 接口记录「require_member（401 member_session_required）」「查订单（404 order_not_found）」与验收第 2 条「signed_out（401）、
  // not_found（404 order_not_found）、failed（其他，含 5xx）与 network」：其他 404、200 以外的 2xx 与 200 而不是 JSON 也归 failed。
  it.each<[Response | null, unknown]>([
    [signedOut(), { kind: "signed_out" }],
    [notFound(), { kind: "not_found" }],
    [json(404, { detail: "Not Found" }), { kind: "failed" }],
    [json(422, { detail: [] }), { kind: "failed" }],
    [json(500, {}), { kind: "failed" }],
    [json(503, { detail: "service_unavailable" }), { kind: "failed" }],
    [json(201, memberOrder()), { kind: "failed" }],
    [new Response("<html></html>", { status: 200 }), { kind: "failed" }],
    [null, { kind: "network" }],
  ])("classifies the read reply (%#)", async (reply, expected) => {
    stubFetch(reply);
    await expect(readMemberPayOrder("en", ORDER_NUMBER)).resolves.toEqual(expected);
  });
});

describe("member payment and cancel requests", () => {
  // 接口记录「幂等键（只有支付要……）……require_member_csrf（403 csrf_failed）」与验收第 2 条「支付的请求体只有 order_number、method 与 result，
  // 带幂等键……与 CSRF 令牌」「订单号只在请求体里，CSRF 令牌只在请求头里」：地址不带查询参数。
  it("posts the payment with the idempotency key and the CSRF token", async () => {
    const fetchMock = stubFetch(json(201, { method: "card", result: "success", status: "demo_paid", paid_at: "2026-10-05T10:02:00Z" }));
    await expect(submitMemberPayment(ORDER_NUMBER, TOKEN, attempt)).resolves.toBe("done");
    expect(urlOf(fetchMock)).toBe(MEMBER_ATTEMPTS_URL);
    expect(MEMBER_ATTEMPTS_URL).toBe("/api/member/pay/attempts");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headersOf(fetchMock)).toEqual({ ...JSON_HEADERS, [IDEMPOTENCY_HEADER]: attempt.key, [CSRF_HEADER]: TOKEN });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER, method: "card", result: "success" });
  });

  // 接口记录「不要求幂等键：重复取消已取消的订单直接返回当前状态」与验收第 2 条「取消的请求体只有 order_number、带 CSRF 令牌」。
  it("posts the cancel with only the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, { status: "demo_cancelled", cancelled_by: "self" }));
    await expect(submitMemberCancel(ORDER_NUMBER, TOKEN)).resolves.toBe("done");
    expect(urlOf(fetchMock)).toBe("/api/member/pay/cancel");
    expect(MEMBER_CANCEL_URL).toBe("/api/member/pay/cancel");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headersOf(fetchMock)).toEqual({ ...JSON_HEADERS, [CSRF_HEADER]: TOKEN });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
  });

  // 接口记录「成功与 409 的状态码、响应体与 detail 和游客接口相同……PaymentPathMismatch 回 409 method_changed，StripeUnavailable 回 503 stripe_unavailable」
  // 与验收第 2 条「两者的回答照 pay.ts 的 WriteReply 归类，其中 expired 换成 signed_out，另有 not_found」。
  it.each<[() => Response | null, string]>([
    [() => json(201, {}), "done"],
    [() => json(200, {}), "done"],
    [signedOut, "signed_out"],
    [csrfFailed, "csrf"],
    [notFound, "not_found"],
    [() => json(404, { detail: "Not Found" }), "failed"],
    [() => json(409, { detail: "order_expired", status: "demo_cancelled" }), "closed"],
    [() => json(409, { detail: "order_not_payable", status: "demo_paid" }), "closed"],
    [() => json(409, { detail: "order_not_cancellable", status: "demo_paid" }), "closed"],
    [() => json(409, { detail: "method_changed" }), "method_changed"],
    [() => json(409, { detail: "idempotency_conflict" }), "failed"],
    [() => json(409, {}), "failed"],
    [() => json(503, { detail: "stripe_unavailable" }), "unavailable"],
    [() => json(503, { detail: "service_unavailable" }), "failed"],
    [() => json(422, { detail: [] }), "failed"],
    [() => json(500, {}), "failed"],
    [() => null, "network"],
  ])("classifies the write reply (%#)", async (reply, expected) => {
    stubFetch(reply());
    await expect(submitMemberPayment(ORDER_NUMBER, TOKEN, attempt)).resolves.toBe(expected);
    stubFetch(reply());
    await expect(submitMemberCancel(ORDER_NUMBER, TOKEN)).resolves.toBe(expected);
  });
});

describe("member write outcomes", () => {
  // 验收第 3 条「照 pay.ts 的 runPayment 与 runCancel 各写一个会员版……结果里 expired 换成 signed_out，另有 not_found……其余去向……与游客版相同」：
  // 完成与 409 不能再支付或取消转结果页；401、404、method_changed、unavailable 各自成为结果；其他失败显示 common.error_retry，不再请求。
  it.each<[() => Response, unknown]>([
    [() => json(201, {}), { kind: "result" }],
    [() => json(409, { detail: "order_not_payable", status: "demo_paid" }), { kind: "result" }],
    [() => json(409, { detail: "order_not_cancellable", status: "demo_paid" }), { kind: "result" }],
    [signedOut, { kind: "signed_out" }],
    [notFound, { kind: "not_found" }],
    [() => json(409, { detail: "method_changed" }), { kind: "method_changed" }],
    [() => json(503, { detail: "stripe_unavailable" }), { kind: "unavailable" }],
    [() => json(409, { detail: "idempotency_conflict" }), { kind: "error" }],
    [() => json(500, {}), { kind: "error" }],
  ])("settles a reply without reading again (%#)", async (reply, expected) => {
    let fetchMock = stubFetch(reply());
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual(expected);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock = stubFetch(reply());
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual(expected);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 验收第 3 条「403 csrf 时会员读取不给新令牌，归一个单独的结果（带重新读取到的订单，没有则为空），由页面重读会员会话取得新令牌后提示重试」：
  // 写之后紧接着以会员读取重读同一张订单（请求体只有订单号，不带令牌）；读不到时订单为空。
  it("reads the order again after csrf_failed and leaves the new token to the page", async () => {
    let fetchMock = stubFetch(csrfFailed(), json(200, memberOrder()));
    await expect(runMemberPayment("zh", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "csrf", order: memberOrder() });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(urlOf(fetchMock, 1)).toBe("/api/member/pay/order?lang=zh");
    expect(headersOf(fetchMock, 1)).toEqual(JSON_HEADERS);
    expect(bodyOf(fetchMock, 1)).toEqual({ order_number: ORDER_NUMBER });
    fetchMock = stubFetch(csrfFailed(), json(500, {}));
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "csrf", order: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    stubFetch(csrfFailed(), null);
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "csrf", order: null });
  });

  // 同一条「其余去向……与游客版相同」（游客版 csrf 重读到 expired 为 expired、订单不再待支付转结果页）：
  // 重读到 401 为 signed_out、404 为 not_found、已不再待支付时转结果页。
  it("follows the reread after csrf_failed", async () => {
    stubFetch(csrfFailed(), signedOut());
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "signed_out" });
    stubFetch(csrfFailed(), notFound());
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "not_found" });
    stubFetch(csrfFailed(), json(200, memberOrder({ status: "demo_paid" })));
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "result" });
  });

  // UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单……再允许重试」与验收第 3 条「网络中断后的确认照 confirmOrder 写法重读……
  // 网络中断后沿用同一幂等键的条件……与游客版相同」：仍待支付时允许重试并带原来的支付，再次提交用同一个幂等键与同一请求体。
  it("checks the order after a lost connection and retries with the same key", async () => {
    const fetchMock = stubFetch(null, json(200, memberOrder()), json(201, {}));
    const onChecking = vi.fn();
    const outcome = await runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt, { onChecking, delay: noDelay });
    expect(onChecking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", order: memberOrder(), attempt });
    if (outcome?.kind !== "retry" || outcome.attempt === null) {
      throw new Error("expected a retry");
    }
    const again = attemptFor(outcome.attempt, "card", "success");
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, again)).resolves.toEqual({ kind: "result" });
    expect(urlOf(fetchMock, 1)).toBe("/api/member/pay/order?lang=en");
    expect(headersOf(fetchMock, 2)[IDEMPOTENCY_HEADER]).toBe(headersOf(fetchMock, 0)[IDEMPOTENCY_HEADER]);
    expect(bodyOf(fetchMock, 2)).toEqual(bodyOf(fetchMock, 0));
  });

  // 同一条：取消中断后仍待支付时允许再次取消（没有待重试的支付）。
  it("offers the cancel again after a lost connection", async () => {
    const onChecking = vi.fn();
    stubFetch(null, json(200, memberOrder()));
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN, { onChecking, delay: noDelay })).resolves.toEqual({ kind: "retry", order: memberOrder(), attempt: null });
    expect(onChecking).toHaveBeenCalledTimes(1);
  });

  // 同一条「重读到 ok、signed_out 或 not_found 为止」：上一步已完成时转结果页，会话已结束或订单不存在时各自报告，不允许重试。
  it.each<[() => Response, unknown]>([
    [() => json(200, memberOrder({ status: "demo_paid" })), { kind: "result" }],
    [() => json(200, memberOrder({ status: "demo_cancelled", cancelled_by: "self" })), { kind: "result" }],
    [signedOut, { kind: "signed_out" }],
    [notFound, { kind: "not_found" }],
  ])("settles the check after a lost connection (%#)", async (reread, expected) => {
    stubFetch(null, reread());
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt, { delay: noDelay })).resolves.toEqual(expected);
    stubFetch(null, reread());
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN, { delay: noDelay })).resolves.toEqual(expected);
  });

  // 同一条「间隔可注入」与 pay.ts confirmOrder「读取本身也中断或失败时，隔一段时间再读，直到得到回答」：
  // 中断与失败（含 5xx）都隔 RECHECK_DELAY_MS 再读，得到回答之前不给出可重试的结果。
  it("keeps checking until the order can be read", async () => {
    const fetchMock = stubFetch(null, null, json(503, {}), json(200, memberOrder()));
    const delays: number[] = [];
    const outcome = await runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt, {
      delay: (ms) => {
        delays.push(ms);
        return Promise.resolve();
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(delays).toEqual([RECHECK_DELAY_MS, RECHECK_DELAY_MS]);
    expect(outcome?.kind).toBe("retry");
  });

  // 验收第 3 条「页面已离开（中止）时返回 null」：写请求返回时已中止不再重读；确认中途中止不再继续读取。
  it("returns null once aborted", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
      void input;
      void init;
      controller.abort();
      return Promise.resolve(csrfFailed());
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt, { signal: controller.signal })).resolves.toBeNull();
    await expect(runMemberCancel("en", ORDER_NUMBER, TOKEN, { signal: controller.signal })).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const checking = new AbortController();
    const checks = stubFetch(null, null, json(200, memberOrder()));
    const outcome = await runMemberPayment("en", ORDER_NUMBER, TOKEN, attempt, {
      signal: checking.signal,
      delay: () => {
        checking.abort();
        return Promise.resolve();
      },
    });
    expect(outcome).toBeNull();
    expect(checks).toHaveBeenCalledTimes(2);

    const reading = new AbortController();
    stubFetch(null);
    await expect(
      confirmMemberPayOrder("en", ORDER_NUMBER, {
        signal: reading.signal,
        delay: () => {
          reading.abort();
          return Promise.resolve();
        },
      }),
    ).resolves.toBeNull();
  });
});

describe("member Stripe start", () => {
  // 接口记录「请求体、查询参数 lang、成功响应、结果映射与错误都与 app/api/stripe_pay.py 的游客接口相同」与验收第 2 条「Stripe 发起（带 lang）」：
  // 查询参数只有 lang，请求头为 JSON 与 X-CSRF-Token（没有幂等键），请求体只有订单号，中止信号交给 fetch。
  it("posts only the order number with the CSRF token in the current language", async () => {
    const fetchMock = stubFetch(json(200, { result: "redirect", checkout_url: CHECKOUT_URL }));
    const controller = new AbortController();
    await expect(startMemberStripe("ms", ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "redirect", url: CHECKOUT_URL });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urlOf(fetchMock)).toBe("/api/member/pay/stripe/start?lang=ms");
    expect(memberStripeStartUrl("zh")).toBe("/api/member/pay/stripe/start?lang=zh");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headersOf(fetchMock)).toEqual({ ...JSON_HEADERS, [CSRF_HEADER]: TOKEN });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
  });

  // 接口记录「请求体 413 → 415 → 422 → 401 member_session_required → 403 csrf_failed → 404 order_not_found」与 DESIGN 1.14「Stripe 测试环境支付」
  // 第 3 条（发起，按会员登录会话与 CSRF 校验），验收第 2 条「照 pay.ts 的 startStripe……401 归 signed_out，另有 not_found」。
  it.each<[Response | null, unknown]>([
    [json(200, { result: "redirect", checkout_url: CHECKOUT_URL }), { kind: "redirect", url: CHECKOUT_URL }],
    [json(200, { result: "complete", checkout_url: null }), { kind: "complete" }],
    [json(409, { detail: "order_not_payable", status: "demo_cancelled" }), { kind: "closed" }],
    [json(409, { detail: "method_changed" }), { kind: "method_changed" }],
    [json(409, { detail: "stripe_under_review" }), { kind: "under_review" }],
    [json(503, { detail: "stripe_unavailable" }), { kind: "unavailable" }],
    [signedOut(), { kind: "signed_out" }],
    [csrfFailed(), { kind: "csrf" }],
    [notFound(), { kind: "not_found" }],
    [null, { kind: "network" }],
  ])("classifies the start reply (%#)", async (reply, expected) => {
    stubFetch(reply);
    await expect(startMemberStripe("en", ORDER_NUMBER, TOKEN)).resolves.toEqual(expected);
  });

  // 派生实现约束（实现选择）：守住 DESIGN 1.14「Stripe 测试环境支付」第 3 条「服务端只把 Stripe 返回的付款页地址交给前台跳转」——同游客版只跳转到 https；
  // 内容不合格、其他 404、409 与 503 的 detail、其他状态都归 failed。
  it.each([
    json(200, { result: "redirect", checkout_url: "http://checkout.stripe.com/c/pay/cs_test_a1b2c3" }),
    json(200, { result: "redirect", checkout_url: "javascript:alert(1)" }),
    json(200, { result: "redirect", checkout_url: null }),
    json(200, { result: "complete", checkout_url: CHECKOUT_URL }),
    json(200, null),
    new Response("<html></html>", { status: 200 }),
    json(201, { result: "redirect", checkout_url: CHECKOUT_URL }),
    json(404, { detail: "Not Found" }),
    json(409, { detail: "idempotency_conflict" }),
    json(503, { detail: "service_unavailable" }),
    json(500, {}),
    json(422, { detail: [] }),
  ])("treats anything else as a failure (%#)", async (reply) => {
    stubFetch(reply);
    await expect(startMemberStripe("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "failed" });
  });
});

describe("member Stripe confirm", () => {
  // DESIGN 1.14「Stripe 测试环境支付」第 4 条「服务端按授权找到该单的会话向 Stripe 查询一次」与验收第 2 条「确认……照 pay.ts 的 confirmStripe」：
  // 不带任何查询参数，请求头为 JSON 与 X-CSRF-Token，请求体只有订单号。
  it("posts only the order number with the CSRF token and no query", async () => {
    const fetchMock = stubFetch(json(200, { result: "paid" }));
    const controller = new AbortController();
    await expect(confirmMemberStripe(ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "paid" });
    expect(urlOf(fetchMock)).toBe("/api/member/pay/stripe/confirm");
    expect(MEMBER_STRIPE_CONFIRM_URL).toBe("/api/member/pay/stripe/confirm");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headersOf(fetchMock)).toEqual({ ...JSON_HEADERS, [CSRF_HEADER]: TOKEN });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
  });

  // 接口记录「结果同 POST /api/pay/stripe/confirm：paid、confirming、not_paid 或 refunded」与验收第 2 条「401 归 signed_out，另有 not_found」；
  // 其他结果值、内容不合格、其他 404 与其他状态都归 failed，网络中断归 network。
  it.each<[Response | null, unknown]>([
    [json(200, { result: "paid" }), { kind: "paid" }],
    [json(200, { result: "confirming" }), { kind: "confirming" }],
    [json(200, { result: "not_paid" }), { kind: "not_paid" }],
    [json(200, { result: "refunded" }), { kind: "refunded" }],
    [signedOut(), { kind: "signed_out" }],
    [csrfFailed(), { kind: "csrf" }],
    [notFound(), { kind: "not_found" }],
    [json(200, { result: "needs_review" }), { kind: "failed" }],
    [json(200, null), { kind: "failed" }],
    [json(201, { result: "paid" }), { kind: "failed" }],
    [json(404, { detail: "Not Found" }), { kind: "failed" }],
    [json(503, { detail: "stripe_unavailable" }), { kind: "failed" }],
    [null, { kind: "network" }],
  ])("classifies the confirm reply (%#)", async (reply, expected) => {
    stubFetch(reply);
    await expect(confirmMemberStripe(ORDER_NUMBER, TOKEN)).resolves.toEqual(expected);
  });
});

describe("member current order stash", () => {
  // DESIGN 1.14「权限与资料保护」第 7 条「前台可把当前这一笔订单的订单号暂存在本标签页的 sessionStorage：只存这一个值」与验收第 4 条
  // 「写入同时记在模块变量与本标签页 sessionStorage 的一个固定键里（键名为常量、不含订单号）」「读取不清除」。
  it("writes only the order number under a fixed key and keeps it when read", () => {
    const session = fakeStorage();
    vi.stubGlobal("sessionStorage", session);
    expect(stashedMemberPayOrder()).toBeNull();
    stashMemberPayOrder(ORDER_NUMBER);
    expect(session.setItem.mock.calls).toEqual([[MEMBER_PAY_ORDER_KEY, ORDER_NUMBER]]);
    expect(MEMBER_PAY_ORDER_KEY).not.toContain(ORDER_NUMBER);
    expect([...session.items.entries()]).toEqual([[MEMBER_PAY_ORDER_KEY, ORDER_NUMBER]]);
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
    expect(session.removeItem).not.toHaveBeenCalled();
  });

  // UX P06「会员的当前订单」「P06、P07 先取内存、再取 sessionStorage」与 DESIGN 同一条「写入……（覆盖之前暂存的值）」：
  // 模块变量在时不读 sessionStorage；整页加载（模块重新载入、变量为空）后从 sessionStorage 读到；再次写入覆盖两处。
  it("reads memory first, then sessionStorage, and overwrites both", async () => {
    const session = fakeStorage();
    vi.stubGlobal("sessionStorage", session);
    stashMemberPayOrder(ORDER_NUMBER);
    session.items.set(MEMBER_PAY_ORDER_KEY, OTHER_ORDER);
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
    expect(session.getItem).not.toHaveBeenCalled();

    vi.resetModules();
    const reloaded = await import("./memberPay");
    expect(reloaded.stashedMemberPayOrder()).toBe(OTHER_ORDER);
    expect(session.getItem).toHaveBeenCalledWith(MEMBER_PAY_ORDER_KEY);

    reloaded.stashMemberPayOrder(ORDER_NUMBER);
    expect(reloaded.stashedMemberPayOrder()).toBe(ORDER_NUMBER);
    expect(session.items.get(MEMBER_PAY_ORDER_KEY)).toBe(ORDER_NUMBER);
    reloaded.clearMemberPayOrder();
  });

  // DESIGN 同一条「清除：该单支付结果确定……后，会员退出登录或注销时，以及会员接口对它回答会话无效或订单不存在时」与验收第 4 条「清除同时清两处」。
  it("clears both places", () => {
    const session = fakeStorage();
    vi.stubGlobal("sessionStorage", session);
    stashMemberPayOrder(ORDER_NUMBER);
    clearMemberPayOrder();
    expect(stashedMemberPayOrder()).toBeNull();
    expect(session.removeItem).toHaveBeenCalledWith(MEMBER_PAY_ORDER_KEY);
    expect(session.items.size).toBe(0);
  });

  // 验收第 4 条「sessionStorage 不存在或读写抛错时以 try/catch 当作没有暂存，写入与清除静默跳过」：写入后仍可从页面内存读到，
  // 清除后为空；内存为空而读取抛错时为空。
  it.each<[string, () => unknown]>([
    ["missing", () => undefined],
    ["throwing", brokenStorage],
  ])("falls back to memory when sessionStorage is %s", (name, storage) => {
    void name;
    vi.stubGlobal("sessionStorage", storage());
    expect(stashedMemberPayOrder()).toBeNull();
    expect(() => {
      stashMemberPayOrder(ORDER_NUMBER);
    }).not.toThrow();
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
    expect(() => {
      clearMemberPayOrder();
    }).not.toThrow();
    expect(stashedMemberPayOrder()).toBeNull();
  });

  // DESIGN 同一条「不进地址、查询参数、localStorage、cookie……」与验收第 4、5 条「整个过程不写 localStorage 与 cookie」：
  // 暂存、会员读取、支付（含网络中断后的确认与重试）、取消与 Stripe 发起和确认的全程只写 sessionStorage 的那一个键，
  // 不写 localStorage、cookie 与历史状态；请求地址里没有订单号、电话或令牌。
  it("keeps the order number out of addresses, localStorage and cookies", async () => {
    const local = fakeStorage();
    const session = fakeStorage();
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

    const fetchMock = stubFetch(
      json(200, memberOrder()),
      null,
      json(200, memberOrder()),
      json(201, {}),
      json(200, {}),
      json(200, { result: "complete", checkout_url: null }),
      json(200, { result: "paid" }),
    );
    stashMemberPayOrder(ORDER_NUMBER);
    const orderNumber = stashedMemberPayOrder();
    if (orderNumber === null) {
      throw new Error("expected a stashed order");
    }
    await readMemberPayOrder("en", orderNumber);
    const first = await runMemberPayment("en", orderNumber, TOKEN, attempt, { delay: noDelay });
    if (first?.kind !== "retry" || first.attempt === null) {
      throw new Error("expected a retry");
    }
    await runMemberPayment("en", orderNumber, TOKEN, first.attempt);
    await runMemberCancel("en", orderNumber, TOKEN);
    await startMemberStripe("en", orderNumber, TOKEN);
    await confirmMemberStripe(orderNumber, TOKEN);
    clearMemberPayOrder();

    expect(local.setItem).not.toHaveBeenCalled();
    expect(local.removeItem).not.toHaveBeenCalled();
    expect(session.setItem.mock.calls).toEqual([[MEMBER_PAY_ORDER_KEY, ORDER_NUMBER]]);
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(7);
    for (const call of fetchMock.mock.calls) {
      const url = String(call[0]);
      for (const secret of [ORDER_NUMBER, "60123456789", TOKEN, attempt.key]) {
        expect(url).not.toContain(secret);
      }
      expect(url).toMatch(/^\/api\/member\/pay\/(order\?lang=en|attempts|cancel|stripe\/start\?lang=en|stripe\/confirm)$/);
    }
  });

  // DESIGN 同一条「只有会员流程写入，游客流程不读也不写」与 HANDOFF 0.63（运营者落实写法：会员支付请求放新文件 memberPay.ts）：
  // 已有暂存时，游客的读取、支付与取消不读、不写也不清 sessionStorage，暂存原样保留。
  it("is not touched by the guest flow", async () => {
    const session = fakeStorage();
    vi.stubGlobal("sessionStorage", session);
    stashMemberPayOrder(ORDER_NUMBER);
    session.setItem.mockClear();

    stubFetch(json(200, { orders: [memberOrder({ order_number: OTHER_ORDER })], csrf_token: TOKEN }), json(201, {}), json(200, {}));
    await readPayOrder("en");
    await runPayment("en", OTHER_ORDER, TOKEN, attempt);
    await runCancel("en", OTHER_ORDER, TOKEN);

    expect(session.getItem).not.toHaveBeenCalled();
    expect(session.setItem).not.toHaveBeenCalled();
    expect(session.removeItem).not.toHaveBeenCalled();
    expect(session.items.get(MEMBER_PAY_ORDER_KEY)).toBe(ORDER_NUMBER);
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
  });
});
