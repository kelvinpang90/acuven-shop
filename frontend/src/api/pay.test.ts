import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attemptFor,
  ATTEMPTS_URL,
  CANCEL_URL,
  confirmOrder,
  confirmStripe,
  CSRF_HEADER,
  fetchMyStates,
  IDEMPOTENCY_HEADER,
  minutesLeft,
  nextTickDelay,
  payOrdersUrl,
  readPayOrder,
  RECHECK_DELAY_MS,
  remainingAtRead,
  rememberPaidNotCancelled,
  resultKind,
  runCancel,
  runPayment,
  startStripe,
  STRIPE_CONFIRM_URL,
  stripeStartUrl,
  submitCancel,
  submitPayment,
  takePaidNotCancelled,
} from "./pay";
import type { PayOrder, PaymentAttempt } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const TOKEN = "csrf-token-1";

function payOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "awaiting_demo_payment",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:01:00Z",
    subtotal_sen: 12000,
    shipping_fee_sen: 1500,
    total_sen: 13500,
    recipient: {
      name: "Sam Taylor",
      phone: "+447700900123",
      country_code: "GB",
      region: "Greater London",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
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

function ordersBody(order: PayOrder = payOrder(), csrf_token = TOKEN) {
  return { orders: [order, payOrder({ order_number: "OLDERORDER000000" })], csrf_token };
}

interface Call {
  url: string;
  init: RequestInit;
}

const requestUrl = (input: RequestInfo | URL): string =>
  typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: requestUrl(input), init: init ?? {} });
    const next = queue.shift();
    if (next === undefined) {
      return Promise.reject(new Error("unexpected request"));
    }
    if (typeof next === "function") {
      return Promise.reject(new TypeError("Failed to fetch"));
    }
    return Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

const attempt: PaymentAttempt = { key: "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60", method: "card", result: "success" };
const noDelay = () => Promise.resolve();

describe("reading the order", () => {
  // SHOP-TASK-024 验收第 3 条「两页打开时都调用 SHOP-TASK-021 的 GET /api/pay/orders（随当前语言），取返回的第一张订单（最近签发的授权）」：
  // 地址只带语言，凭本浏览器 cookie（同源）读取、不缓存；取第一张订单与接口给的 CSRF 令牌。
  it("reads the first order in the current language", async () => {
    const calls = stubFetch(json(200, ordersBody()));
    const read = await readPayOrder("zh");
    expect(calls[0]?.url).toBe("/api/pay/orders?lang=zh");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(read).toEqual({ kind: "ok", order: payOrder(), csrfToken: TOKEN });
    expect(payOrdersUrl("ms")).toBe("/api/pay/orders?lang=ms");
  });

  // SHOP-TASK-024 验收第 3 条「接口 401 时整页显示 pay.session_expired」：401（以及没有订单）为凭据过期；其他失败与网络中断分开报告。
  it("reports 401 and an empty list as expired, other failures separately", async () => {
    stubFetch(json(401, { detail: "access_expired" }));
    await expect(readPayOrder("en")).resolves.toEqual({ kind: "expired" });
    stubFetch(json(200, { orders: [], csrf_token: TOKEN }));
    await expect(readPayOrder("en")).resolves.toEqual({ kind: "expired" });
    stubFetch(json(500, {}));
    await expect(readPayOrder("en")).resolves.toEqual({ kind: "failed" });
    stubFetch(networkDown);
    await expect(readPayOrder("en")).resolves.toEqual({ kind: "network" });
  });
});

describe("payment requests", () => {
  // SHOP-TASK-024 验收第 6 条「成功、失败按钮调用 POST /api/pay/attempts，带…Idempotency-Key…与接口返回的 X-CSRF-Token」与
  // UX P06「不输入任何银行卡资料」：请求体只有订单号、支付方式与结果，订单号与令牌不进地址。
  it("posts the order number, method and result with the idempotency key and the CSRF token", async () => {
    const calls = stubFetch(json(201, { method: "card", result: "success", status: "demo_paid", paid_at: "2026-10-05T10:02:00Z" }));
    await expect(submitPayment(ORDER_NUMBER, TOKEN, attempt)).resolves.toBe("done");
    expect(calls[0]?.url).toBe(ATTEMPTS_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin" });
    expect(headers(calls[0])[IDEMPOTENCY_HEADER]).toBe(attempt.key);
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER, method: "card", result: "success" });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // SHOP-TASK-024 验收第 6 条「带每次点击新生成的 Idempotency-Key（crypto.randomUUID）」与「订单仍待支付时才允许以同一幂等键重试」：
  // 没有待重试的支付时每次点击都新生成；有时只在方式与结果都相同时沿用它的键。
  it("generates a new key for every click unless retrying the same payment", () => {
    const first = attemptFor(null, "card", "success");
    const second = attemptFor(null, "card", "success");
    expect(first.key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(second.key).not.toBe(first.key);
    expect(attemptFor(first, "card", "success")).toBe(first);
    expect(attemptFor(first, "card", "failure").key).not.toBe(first.key);
    expect(attemptFor(first, "bank", "success").key).not.toBe(first.key);
    const keys = ["k-1", "k-2"];
    expect(attemptFor(null, "ewallet", "failure", () => keys.shift() ?? "")).toEqual({ key: "k-1", method: "ewallet", result: "failure" });
  });

  // SHOP-TASK-024 验收第 6 条「完成后转到结果页…409 order_expired 或 order_not_payable 时转到结果页…401 时显示 pay.session_expired」。
  it.each<[Response, "result" | "expired"]>([
    [json(201, {}), "result"],
    [json(200, {}), "result"],
    [json(409, { detail: "order_expired", status: "demo_cancelled" }), "result"],
    [json(409, { detail: "order_not_payable", status: "demo_paid" }), "result"],
    [json(401, { detail: "access_expired" }), "expired"],
  ])("settles a reply to the payment (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind });
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-024 验收第 6 条的错误处理——其他失败（含 409 idempotency_conflict 与 5xx）显示 common.error_retry，不转页。
  it.each([json(409, { detail: "idempotency_conflict" }), json(500, {}), json(422, { detail: [] })])("shows a retry message for other failures (%#)", async (reply) => {
    stubFetch(reply);
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "error", read: null });
  });

  // SHOP-TASK-024 验收第 6 条「403 csrf_failed 时重新读取订单取得新令牌并显示 common.error_retry」：POST 之后紧接着 GET，结果带新令牌。
  it("reads the order again for a new token after csrf_failed", async () => {
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, ordersBody(payOrder(), "csrf-token-2")));
    const outcome = await runPayment("en", ORDER_NUMBER, TOKEN, attempt);
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
    expect(outcome).toEqual({ kind: "error", read: { kind: "ok", order: payOrder(), csrfToken: "csrf-token-2" } });
  });

  // UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」与 SHOP-TASK-024 验收第 6 条「网络中断时显示 common.network_check，
  // 先重新读取订单确认结果，订单仍待支付时才允许以同一幂等键重试」：POST 中断 → 通知页面显示 network_check → GET 确认仍待支付 →
  // 结果允许重试并带原来的支付；再次提交时用同一个幂等键。
  it("checks the order after a lost connection and retries with the same key", async () => {
    const calls = stubFetch(networkDown, json(200, ordersBody()), json(201, {}));
    const checking = vi.fn();
    const outcome = await runPayment("en", ORDER_NUMBER, TOKEN, attempt, { onChecking: checking, delay: noDelay });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", read: { kind: "ok", order: payOrder(), csrfToken: TOKEN }, attempt });
    if (outcome?.kind !== "retry" || outcome.attempt === null) {
      throw new Error("expected a retry");
    }
    const again = attemptFor(outcome.attempt, "card", "success");
    await expect(runPayment("en", ORDER_NUMBER, outcome.read.csrfToken, again)).resolves.toEqual({ kind: "result" });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET", "POST"]);
    expect(headers(calls[2])[IDEMPOTENCY_HEADER]).toBe(headers(calls[0])[IDEMPOTENCY_HEADER]);
    expect(body(calls[2])).toEqual(body(calls[0]));
  });

  // 同一条：重新读取发现上一步已完成（订单不再待支付）时直接转到结果页，不再允许重试；凭据已过期时整页提示。
  it("goes to the result page when the lost request went through", async () => {
    stubFetch(networkDown, json(200, ordersBody(payOrder({ status: "demo_paid" }))));
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt, { delay: noDelay })).resolves.toEqual({ kind: "result" });
    stubFetch(networkDown, json(401, { detail: "access_expired" }));
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt, { delay: noDelay })).resolves.toEqual({ kind: "expired" });
  });

  // 同一条「先重新读取订单确认结果…才允许…重试」：重新读取本身也中断或失败时隔 RECHECK_DELAY_MS 再读，得到回答之前不给出可重试的结果。
  it("keeps checking until the order can be read", async () => {
    const calls = stubFetch(networkDown, networkDown, json(503, {}), json(200, ordersBody()));
    const delays: number[] = [];
    const outcome = await runPayment("en", ORDER_NUMBER, TOKEN, attempt, {
      delay: (ms) => {
        delays.push(ms);
        return Promise.resolve();
      },
    });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET", "GET", "GET"]);
    expect(delays).toEqual([RECHECK_DELAY_MS, RECHECK_DELAY_MS]);
    expect(outcome?.kind).toBe("retry");
  });

  // 派生实现约束（实现选择）：离开页面（中止）后不再继续确认，也不报告结果。
  it("stops checking once aborted", async () => {
    stubFetch(networkDown, networkDown);
    const controller = new AbortController();
    const outcome = await confirmOrder("en", {
      signal: controller.signal,
      delay: () => {
        controller.abort();
        return Promise.resolve();
      },
    });
    expect(outcome).toBeNull();
  });
});

describe("cancel requests", () => {
  // SHOP-TASK-024 验收第 7 条「确认后调用 POST /api/pay/cancel（带 X-CSRF-Token），完成后转到结果页」：请求体只有订单号，不带幂等键。
  it("posts only the order number with the CSRF token", async () => {
    const calls = stubFetch(json(200, { status: "demo_cancelled", cancelled_by: "self" }));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "result" });
    expect(calls[0]?.url).toBe(CANCEL_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin" });
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(headers(calls[0])).not.toHaveProperty(IDEMPOTENCY_HEADER);
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER });
  });

  // 派生实现约束（实现选择）：守住 UX P07「按订单状态显示」——已支付的订单取消被拒（409 order_not_cancellable）时也转到结果页按当前状态显示；401 整页提示。
  it("settles the other cancel replies", async () => {
    stubFetch(json(409, { detail: "order_not_cancellable", status: "demo_paid" }));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "result" });
    stubFetch(json(401, { detail: "access_expired" }));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "expired" });
  });

  // SHOP-TASK-024 验收第 7 条「网络中断时同样先重新读取订单」：仍待支付时允许再次取消（没有待重试的支付），已取消时转到结果页。
  it("checks the order after a lost connection", async () => {
    const checking = vi.fn();
    const calls = stubFetch(networkDown, json(200, ordersBody()));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN, { onChecking: checking, delay: noDelay })).resolves.toEqual({
      kind: "retry",
      read: { kind: "ok", order: payOrder(), csrfToken: TOKEN },
      attempt: null,
    });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
    stubFetch(networkDown, json(200, ordersBody(payOrder({ status: "demo_cancelled", cancelled_by: "self" }))));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN, { delay: noDelay })).resolves.toEqual({ kind: "result" });
  });
});

describe("Stripe replies to the existing writes", () => {
  // HANDOFF 0.54「模拟支付遇到 Stripe 途径回 409 method_changed」与 DESIGN 1.12 第 2 条「同一订单的两条支付途径互斥……不接受模拟支付」：
  // 支付与取消遇到 409 method_changed 时回答 method_changed，settle 给出 kind 为 method_changed 的结果。
  it("reports method_changed for payment and cancel", async () => {
    stubFetch(json(409, { detail: "method_changed" }));
    await expect(submitPayment(ORDER_NUMBER, TOKEN, attempt)).resolves.toBe("method_changed");
    stubFetch(json(409, { detail: "method_changed" }));
    await expect(submitCancel(ORDER_NUMBER, TOKEN)).resolves.toBe("method_changed");
    stubFetch(json(409, { detail: "method_changed" }));
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "method_changed" });
    stubFetch(json(409, { detail: "method_changed" }));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "method_changed" });
  });

  // DESIGN 1.12 第 5 条「Stripe 不可用时本人取消提示稍后再试」与 HANDOFF 0.54「Stripe 不可用时接口回 503 stripe_unavailable」：
  // 支付与取消遇到 503 stripe_unavailable 时回答 unavailable，settle 给出 kind 为 unavailable 的结果。
  it("reports unavailable for payment and cancel", async () => {
    stubFetch(json(503, { detail: "stripe_unavailable" }));
    await expect(submitPayment(ORDER_NUMBER, TOKEN, attempt)).resolves.toBe("unavailable");
    stubFetch(json(503, { detail: "stripe_unavailable" }));
    await expect(submitCancel(ORDER_NUMBER, TOKEN)).resolves.toBe("unavailable");
    stubFetch(json(503, { detail: "stripe_unavailable" }));
    await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "unavailable" });
    stubFetch(json(503, { detail: "stripe_unavailable" }));
    await expect(runCancel("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "unavailable" });
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-024 验收第 6 条的错误处理——只有这两个 detail 是新回答，其他 409 与 503 照旧显示 common.error_retry。
  it.each([json(409, { detail: "idempotency_conflict" }), json(503, { detail: "service_unavailable" }), json(503, {}), new Response("down", { status: 503 })])(
    "keeps other 409 and 503 replies as failures (%#)",
    async (reply) => {
      stubFetch(reply.clone());
      await expect(submitCancel(ORDER_NUMBER, TOKEN)).resolves.toBe("failed");
      stubFetch(reply.clone());
      await expect(runPayment("en", ORDER_NUMBER, TOKEN, attempt)).resolves.toEqual({ kind: "error", read: null });
    },
  );
});

describe("Stripe start", () => {
  const CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3";

  // DESIGN 1.12 第 3 条「访客在支付页按现有授权……与 CSRF 校验后发起」与 UX「阅读说明」订单号与令牌不进地址：
  // POST /api/pay/stripe/start，查询参数只有 lang；同源 cookie、不缓存、JSON 请求头与 X-CSRF-Token；请求体只有订单号；中止信号交给 fetch。
  it("posts only the order number with the CSRF token in the current language", async () => {
    const calls = stubFetch(json(200, { result: "redirect", checkout_url: CHECKOUT_URL }));
    const controller = new AbortController();
    await expect(startStripe("ms", ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "redirect", url: CHECKOUT_URL });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/pay/stripe/start?lang=ms");
    expect(stripeStartUrl("zh")).toBe("/api/pay/stripe/start?lang=zh");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headers(calls[0])).toEqual({ Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: TOKEN });
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // DESIGN 1.12 第 3 条「服务端只把 Stripe 返回的付款页地址交给前台跳转」「已有 open 就直接返回它的付款地址」与 SHOP-TASK-120 的回答表
  // （complete 回 200、地址为 null，前端随后调用确认接口；not_payable 409 order_not_payable；409 method_changed；409 stripe_under_review；
  // 503 stripe_unavailable）；DESIGN 1.12 第 9 条「Stripe 不可用，支付页提示稍后再试……不自动改用模拟支付」。
  it.each<[Response, unknown]>([
    [json(200, { result: "redirect", checkout_url: CHECKOUT_URL }), { kind: "redirect", url: CHECKOUT_URL }],
    [json(200, { result: "complete", checkout_url: null }), { kind: "complete" }],
    [json(409, { detail: "order_not_payable", status: "demo_cancelled" }), { kind: "closed" }],
    [json(409, { detail: "method_changed" }), { kind: "method_changed" }],
    [json(409, { detail: "stripe_under_review" }), { kind: "under_review" }],
    [json(503, { detail: "stripe_unavailable" }), { kind: "unavailable" }],
    [json(401, { detail: "access_expired" }), { kind: "expired" }],
    [json(403, { detail: "csrf_failed" }), { kind: "csrf" }],
  ])("classifies the start reply (%#)", async (reply, expected) => {
    stubFetch(reply);
    await expect(startStripe("en", ORDER_NUMBER, TOKEN)).resolves.toEqual(expected);
  });

  // 派生实现约束（实现选择）：守住 DESIGN 1.12 第 3 条「服务端只把 Stripe 返回的付款页地址交给前台跳转」——只跳转到 https 地址；
  // 地址缺少、不是字符串、解析不了或不是 https，回答与内容不合格，其他 409 与 503 的 detail、其他状态都归 failed，不跳转。
  it.each([
    json(200, { result: "redirect", checkout_url: "http://checkout.stripe.com/c/pay/cs_test_a1b2c3" }),
    json(200, { result: "redirect", checkout_url: "javascript:alert(1)" }),
    json(200, { result: "redirect", checkout_url: "/relative/path" }),
    json(200, { result: "redirect", checkout_url: "not a url" }),
    json(200, { result: "redirect", checkout_url: null }),
    json(200, { result: "redirect", checkout_url: 42 }),
    json(200, { result: "redirect" }),
    json(200, { result: "complete", checkout_url: CHECKOUT_URL }),
    json(200, { result: "other", checkout_url: CHECKOUT_URL }),
    json(200, null),
    new Response("<html></html>", { status: 200 }),
    json(201, { result: "redirect", checkout_url: CHECKOUT_URL }),
    json(409, { detail: "idempotency_conflict" }),
    json(409, {}),
    json(503, { detail: "service_unavailable" }),
    new Response("down", { status: 503 }),
    json(500, {}),
    json(422, { detail: [] }),
  ])("treats anything else as a failure (%#)", async (reply) => {
    stubFetch(reply);
    await expect(startStripe("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "failed" });
  });

  // UX「全局框架」网络中断与 SHOP-TASK-132 验收第 2 条「中止与网络中断一样归 network」：fetch 被拒或请求被中止都回答 network。
  it("reports a lost connection or an abort as network", async () => {
    stubFetch(networkDown);
    await expect(startStripe("en", ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "network" });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      init?.signal?.aborted
        ? Promise.reject(new DOMException("The operation was aborted.", "AbortError"))
        : Promise.reject(new Error(`unexpected request ${requestUrl(input)}`)),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();
    await expect(startStripe("en", ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "network" });
    await expect(confirmStripe(ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock.mock.calls.map((call) => requestUrl(call[0]))).toEqual(["/api/pay/stripe/start?lang=en", STRIPE_CONFIRM_URL]);
  });
});

describe("Stripe confirm", () => {
  // DESIGN 1.12 第 4 条「访客从 Stripe 返回后前台调用本站接口，服务端按授权找到该单的会话向 Stripe 查询一次」：
  // POST /api/pay/stripe/confirm 不带任何查询参数；同源 cookie、不缓存、JSON 请求头与 X-CSRF-Token；请求体只有订单号。
  it("posts only the order number with the CSRF token and no query", async () => {
    const calls = stubFetch(json(200, { result: "paid" }));
    const controller = new AbortController();
    await expect(confirmStripe(ORDER_NUMBER, TOKEN, controller.signal)).resolves.toEqual({ kind: "paid" });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/pay/stripe/confirm");
    expect(calls[0]?.url).not.toContain("?");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headers(calls[0])).toEqual({ Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: TOKEN });
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER });
  });

  // HANDOFF 0.54「结果页的确认结果为 paid、confirming、not_paid、refunded 四种，Stripe 不可用与确认返回 needs_review 都按『确认中』显示」
  // 与 DESIGN 1.12 第 4 条「尚未确认时结果页显示『确认中』并稍后重查」、第 6 条迟到付款全额退款；401 与 403 分开报告。
  it.each<[Response, unknown]>([
    [json(200, { result: "paid" }), { kind: "paid" }],
    [json(200, { result: "confirming" }), { kind: "confirming" }],
    [json(200, { result: "not_paid" }), { kind: "not_paid" }],
    [json(200, { result: "refunded" }), { kind: "refunded" }],
    [json(401, { detail: "access_expired" }), { kind: "expired" }],
    [json(403, { detail: "csrf_failed" }), { kind: "csrf" }],
  ])("classifies the confirm reply (%#)", async (reply, expected) => {
    stubFetch(reply);
    await expect(confirmStripe(ORDER_NUMBER, TOKEN)).resolves.toEqual(expected);
  });

  // 派生实现约束（实现选择）：守住同一条——只认这四种结果；200 而结果为其他值（含 needs_review，服务端已归为 confirming）、内容不合格、其他状态都归 failed。
  it.each([
    json(200, { result: "needs_review" }),
    json(200, { result: "PAID" }),
    json(200, {}),
    json(200, null),
    new Response("ok", { status: 200 }),
    json(201, { result: "paid" }),
    json(409, { detail: "method_changed" }),
    json(503, { detail: "stripe_unavailable" }),
    json(500, {}),
  ])("treats anything else as a failure (%#)", async (reply) => {
    stubFetch(reply);
    await expect(confirmStripe(ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "failed" });
  });

  // UX「全局框架」网络中断：确认请求中断时回答 network，不当作失败。
  it("reports a lost connection as network", async () => {
    stubFetch(networkDown);
    await expect(confirmStripe(ORDER_NUMBER, TOKEN)).resolves.toEqual({ kind: "network" });
  });
});

describe("paid-not-cancelled handoff", () => {
  afterEach(() => {
    takePaidNotCancelled("");
  });

  // HANDOFF 0.55 第（5）项「『取消时会话已付款』的提示只放在请求模块的内存里、读一次即失效」：记下后第一次读到 true，之后再读为 false。
  it("is read once", () => {
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(false);
    rememberPaidNotCancelled(ORDER_NUMBER);
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(true);
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(false);
  });

  // 同一条：只对记下的那张订单显示；订单号不同时返回 false，并同样清除记录（之后用原订单号读也为 false）。
  it("only matches the remembered order and clears it either way", () => {
    rememberPaidNotCancelled(ORDER_NUMBER);
    expect(takePaidNotCancelled("OLDERORDER000000")).toBe(false);
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(false);
    rememberPaidNotCancelled("OLDERORDER000000");
    rememberPaidNotCancelled(ORDER_NUMBER);
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(true);
  });

  // 同一条「不进地址、历史状态或浏览器存储」与 UX「阅读说明」订单号与令牌只在页面内存里：交接与两个 Stripe 接口都不写
  // localStorage、sessionStorage、cookie 或 history，请求地址里没有订单号或令牌。
  it("keeps the handoff and the Stripe requests out of browser storage", async () => {
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

    const calls = stubFetch(json(200, { result: "complete", checkout_url: null }), json(200, { result: "paid" }));
    await startStripe("en", ORDER_NUMBER, TOKEN);
    await confirmStripe(ORDER_NUMBER, TOKEN);
    rememberPaidNotCancelled(ORDER_NUMBER);
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(true);

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls.map((call) => call.url)).toEqual(["/api/pay/stripe/start?lang=en", "/api/pay/stripe/confirm"]);
    for (const call of calls) {
      expect(call.url).not.toContain(ORDER_NUMBER);
      expect(call.url).not.toContain(TOKEN);
    }
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-024 验收第 2 条「订单号、电话与 CSRF 令牌不进任何路径、查询参数、localStorage、sessionStorage 或 cookie，只在页面内存里」：
  // 读取、支付（含网络中断后的确认与重试）与取消的整个过程不写任何浏览器存储、cookie 或历史记录，请求地址里没有订单号、电话或令牌。
  it("keeps the order number, phone and token out of addresses and browser storage", async () => {
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

    const calls = stubFetch(json(200, ordersBody()), networkDown, json(200, ordersBody()), json(201, {}), json(200, {}));
    const read = await readPayOrder("en");
    if (read.kind !== "ok") {
      throw new Error("expected an order");
    }
    const first = await runPayment("en", read.order.order_number, read.csrfToken, attempt, { delay: noDelay });
    if (first?.kind !== "retry" || first.attempt === null) {
      throw new Error("expected a retry");
    }
    await runPayment("en", read.order.order_number, first.read.csrfToken, first.attempt);
    await runCancel("en", read.order.order_number, read.csrfToken);

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls).toHaveLength(5);
    for (const call of calls) {
      expect(call.url).not.toContain(ORDER_NUMBER);
      expect(call.url).not.toContain("7700900123");
      expect(call.url).not.toContain(TOKEN);
      expect(call.url).toMatch(/^\/api\/pay\/(orders\?lang=en|attempts|cancel)$/);
    }
  });
});

describe("payment countdown", () => {
  // SHOP-TASK-024 验收第 5 条「倒计时 {minutes} 用接口返回的支付到期时间与服务器当前时间之差计算（不依赖访客电脑的时钟）」：
  // 访客电脑的时钟无论快慢，剩余时间都只由 payment_expires_at − server_time 决定。
  it("uses the server time, not the visitor's clock", () => {
    const order = payOrder({ payment_expires_at: "2026-10-05T10:15:00Z", server_time: "2026-10-05T10:01:00Z" });
    vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2031, 0, 1));
    expect(remainingAtRead(order)).toBe(14 * 60_000);
    vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2001, 0, 1));
    expect(remainingAtRead(order)).toBe(14 * 60_000);
    expect(minutesLeft(remainingAtRead(order))).toBe(14);
  });

  // 同一条：接口时间带微秒或时区偏移时照样相减；已过期为 0，不为负。
  it("parses microseconds and offsets and never goes below zero", () => {
    expect(remainingAtRead({ payment_expires_at: "2026-10-05T10:15:00.250000Z", server_time: "2026-10-05T10:14:59.750000Z" })).toBe(500);
    expect(remainingAtRead({ payment_expires_at: "2026-10-05T18:15:00+08:00", server_time: "2026-10-05T10:05:00Z" })).toBe(10 * 60_000);
    expect(remainingAtRead({ payment_expires_at: "2026-10-05T10:15:00Z", server_time: "2026-10-05T10:16:00Z" })).toBe(0);
  });

  // UX-COPY pay.expires「Complete within {minutes} min」：剩余不足整分钟时按向上取整（不足一分钟显示 1），到时为 0。
  it.each([
    [15 * 60_000, 15],
    [14 * 60_000 + 1, 15],
    [14 * 60_000, 14],
    [30_000, 1],
    [1, 1],
    [0, 0],
    [-5, 0],
  ])("shows %i ms as %i minutes", (ms, minutes) => {
    expect(minutesLeft(ms)).toBe(minutes);
  });

  // SHOP-TASK-024 验收第 5 条「每分钟更新；到 0 时重新读取订单」：下一次更新恰在分钟数变化时（最多一分钟），已到 0 立即。
  it.each([
    [15 * 60_000, 60_000],
    [14 * 60_000 + 1, 1],
    [90_000, 30_000],
    [30_000, 30_000],
    [0, 0],
  ])("schedules the next update for %i ms after %i ms", (ms, delay) => {
    expect(nextTickDelay(ms)).toBe(delay);
    expect(minutesLeft(ms - delay)).toBe(Math.max(0, minutesLeft(ms) - 1));
  });
});

describe("result page state", () => {
  // SHOP-TASK-024 验收第 8 条「P07 按重新读取的订单显示：demo_paid 显示 result.success_*…；最近一次支付失败且仍待支付时显示 result.failure_*…；
  // demo_cancelled 按取消方显示 result.cancelled（超时）或 result.cancelled_by_you」。
  it.each([
    [{ status: "demo_paid" }, "success"],
    [{ status: "awaiting_demo_payment", last_payment: { method: "card", result: "failure", created_at: "2026-10-05T10:02:00Z" } }, "failure"],
    [{ status: "demo_cancelled", cancelled_by: "timeout" }, "cancelled"],
    [{ status: "demo_cancelled", cancelled_by: "self" }, "cancelled_by_you"],
  ] as const)("shows %j as %s", (fields, kind) => {
    expect(resultKind(payOrder(fields))).toBe(kind);
  });

  // 派生实现约束（实现选择）：守住同一条——取消方未知时按超时的 result.cancelled，不说是访客本人取消；仍待支付而最近一次不是失败时回到支付页；
  // 已模拟支付之后的状态（管理员已模拟发货等）按成功显示。
  it("handles the remaining states", () => {
    expect(resultKind(payOrder({ status: "demo_cancelled", cancelled_by: null }))).toBe("cancelled");
    expect(resultKind(payOrder({ status: "awaiting_demo_payment", last_payment: null }))).toBe("pay");
    expect(resultKind(payOrder({ status: "demo_shipped" }))).toBe("success");
  });
});

describe("Malaysian state names", () => {
  // DESIGN 1.11 依赖的 SHOP-TASK-023「州属名称…三种界面语言都用这一名称」：收货资料的州属代码换成接口给的名称；读取失败返回 null（页面显示代码）。
  it("reads the state names without cookies", async () => {
    const calls = stubFetch(json(200, { regions: [], my_states: [{ code: "MY-10", name: "Selangor" }], default_phone_region: "MY" }));
    const states = await fetchMyStates();
    expect(states?.get("MY-10")).toBe("Selangor");
    expect(calls[0]?.url).toBe("/api/checkout/regions");
    expect(calls[0]?.init.credentials).toBe("omit");
    stubFetch(json(500, {}));
    await expect(fetchMyStates()).resolves.toBeNull();
    stubFetch(networkDown);
    await expect(fetchMyStates()).resolves.toBeNull();
  });
});
