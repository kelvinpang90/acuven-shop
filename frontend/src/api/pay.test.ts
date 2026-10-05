import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attemptFor,
  ATTEMPTS_URL,
  CANCEL_URL,
  confirmOrder,
  CSRF_HEADER,
  fetchMyStates,
  IDEMPOTENCY_HEADER,
  minutesLeft,
  nextTickDelay,
  payOrdersUrl,
  readPayOrder,
  RECHECK_DELAY_MS,
  remainingAtRead,
  resultKind,
  runCancel,
  runPayment,
  submitPayment,
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

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push({ url, init: init ?? {} });
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
