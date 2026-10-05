import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canConfirmReceipt,
  clampQuantities,
  clearSubmittedRefund,
  CONFIRM_RECEIPT_URL,
  handOffSubmittedRefund,
  LOOKUP_URL,
  lookupOrdersUrl,
  readLookupOrder,
  receiptKey,
  recheckOrder,
  refundEstimate,
  refundKey,
  refundLines,
  REFUNDS_URL,
  runConfirmReceipt,
  runRefund,
  showsRefundEntry,
  submitConfirmReceipt,
  submitLookup,
  submitRefund,
  submittedRefundOrder,
} from "./orderLookup";
import type { LookupLine, LookupOrder, LookupRefund } from "./orderLookup";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const OTHER_ORDER = "K7Q29MXA00000000";
const PHONE = "+447700900123";
const TOKEN = "csrf-token-1";
const KEY = "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60";

function lookupOrder(overrides: Partial<LookupOrder> = {}): LookupOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_shipped",
    created_at: "2026-10-05T10:00:00Z",
    paid_at: "2026-10-05T10:02:00Z",
    server_time: "2026-10-05T11:00:00Z",
    lines: [
      {
        line_index: 0,
        name: "Crew Neck Tee",
        variant_label: "Black, M",
        quantity: 2,
        unit_price_sen: 3900,
        line_subtotal_sen: 7800,
        unit_cash_paid_sen: [3900, 3900],
        refundable_quantity: 2,
        refund_estimates_sen: [3900, 7800],
      },
    ],
    subtotal_sen: 7800,
    shipping_fee_sen: 1500,
    total_sen: 9300,
    recipient: {
      name: "Sam Taylor",
      phone: PHONE,
      country_code: "GB",
      region: "Greater London",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
    },
    refund_deadline: "2026-11-04T10:02:00Z",
    refund_window_open: true,
    refunded_total_sen: 0,
    refundable_left_sen: 7800,
    fully_refunded: false,
    refund_requests: [],
    ...overrides,
  };
}

// 一行商品（退款用）：行序、可退件数与预计金额列表可另给。
function line(overrides: Partial<LookupLine> = {}): LookupLine {
  return {
    line_index: 0,
    name: "Crew Neck Tee",
    variant_label: "Black, M",
    quantity: 3,
    unit_price_sen: 3900,
    line_subtotal_sen: 11700,
    unit_cash_paid_sen: [1000, 1500, 1],
    refundable_quantity: 3,
    refund_estimates_sen: [1000, 2500, 2501],
    ...overrides,
  };
}

const REQUESTED: LookupRefund = {
  created_at: "2026-10-06T08:00:00Z",
  status: "requested",
  amount_sen: 3900,
  lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 1, amount_sen: 3900 }],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

// 列表里第一张是最近查询的；另放一张较早查询的订单。
function ordersBody(order: LookupOrder = lookupOrder(), csrf_token = TOKEN) {
  return { orders: [order, lookupOrder({ order_number: OTHER_ORDER })], csrf_token };
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

const noDelay = () => Promise.resolve();

describe("looking up an order", () => {
  // SHOP-TASK-028 验收第 4 条「提交调用 SHOP-TASK-027 的 POST /api/orders/lookup」与 UX P08 说明「订单号与电话只以请求体提交，不进路径或查询参数」：
  // 请求体只有订单号与电话，原样提交（格式由服务端判定）；地址里没有二者。
  it("posts the order number and phone in the body only", async () => {
    const calls = stubFetch(empty(204));
    await expect(submitLookup(" b6tn-2rjd ", "+44 7700 900123")).resolves.toBe("found");
    expect(calls[0]?.url).toBe(LOOKUP_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(body(calls[0])).toEqual({ order_number: " b6tn-2rjd ", phone: "+44 7700 900123" });
  });

  // SHOP-TASK-028 验收第 4 条「404 显示 lookup.not_found，429 显示 common.rate_limited，503 显示 common.service_unavailable，网络中断显示 common.network_check」：
  // 四种回答各自分开报告（页面按它选文案，见 TrackPage.test.tsx）；其他意外回答为 failed。
  it.each<[Response | (() => never), string]>([
    [json(404, { detail: "not_found" }), "not_found"],
    [json(429, { detail: "rate_limited" }), "rate_limited"],
    [json(503, { detail: "service_unavailable" }), "unavailable"],
    [networkDown, "network"],
    [json(500, {}), "failed"],
    [json(422, { detail: [] }), "failed"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitLookup(ORDER_NUMBER, PHONE)).resolves.toBe(kind);
  });

  // SHOP-TASK-028 验收第 4 条「204 时转到 /track/order」：只有 204 算查到；其他 2xx 不是约定的回答，按 failed 留在本页。
  it.each([json(200, {}), empty(201), empty(202)])("treats only 204 as found (%#)", async (reply) => {
    stubFetch(reply);
    await expect(submitLookup(ORDER_NUMBER, PHONE)).resolves.toBe("failed");
  });
});

describe("reading the order", () => {
  // SHOP-TASK-028 验收第 5 条「P09 打开时调用 GET /api/orders/lookup（随当前语言），取返回的第一张订单（最近查询的）」：
  // 地址只带语言，凭本浏览器 cookie（同源）读取、不缓存；取第一张订单与接口给的 CSRF 令牌。
  it("reads the first order in the current language", async () => {
    const calls = stubFetch(json(200, ordersBody()));
    await expect(readLookupOrder("zh", null)).resolves.toEqual({ kind: "ok", order: lookupOrder(), csrfToken: TOKEN });
    expect(calls[0]?.url).toBe("/api/orders/lookup?lang=zh");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(lookupOrdersUrl("ms")).toBe("/api/orders/lookup?lang=ms");
  });

  // 派生实现约束（实现选择）：守住 UX P09「查单授权…仅针对该单」——重新读取时取已显示的那张订单，不换成之后查询的另一单；
  // 列表里已没有该单（本浏览器对它的授权已结束）时按过期处理。
  it("keeps to the order already shown", async () => {
    stubFetch(json(200, { orders: [lookupOrder({ order_number: OTHER_ORDER }), lookupOrder()], csrf_token: TOKEN }));
    await expect(readLookupOrder("en", ORDER_NUMBER)).resolves.toEqual({ kind: "ok", order: lookupOrder(), csrfToken: TOKEN });
    stubFetch(json(200, { orders: [lookupOrder({ order_number: OTHER_ORDER })], csrf_token: TOKEN }));
    await expect(readLookupOrder("en", ORDER_NUMBER)).resolves.toEqual({ kind: "expired" });
  });

  // SHOP-TASK-028 验收第 5 条「401 时整页替换为 order.session_expired」：401 为过期；其他失败与网络中断分开报告。
  it("reports 401 as expired, other failures separately", async () => {
    stubFetch(json(401, { detail: "access_expired" }));
    await expect(readLookupOrder("en", null)).resolves.toEqual({ kind: "expired" });
    stubFetch(json(200, { orders: [], csrf_token: TOKEN }));
    await expect(readLookupOrder("en", null)).resolves.toEqual({ kind: "expired" });
    stubFetch(json(500, {}));
    await expect(readLookupOrder("en", null)).resolves.toEqual({ kind: "failed" });
    stubFetch(networkDown);
    await expect(readLookupOrder("en", null)).resolves.toEqual({ kind: "network" });
  });

  // SHOP-TASK-028 验收第 5 条「金额原样来自接口」：小计、运费、合计与逐件实付不经重算，和接口给的一样（即使彼此对不上）。
  it("keeps the amounts exactly as returned", async () => {
    const odd = lookupOrder({ subtotal_sen: 10000, shipping_fee_sen: 1000, total_sen: 12345 });
    stubFetch(json(200, ordersBody(odd)));
    const read = await readLookupOrder("en", null);
    expect(read.kind === "ok" ? read.order : null).toEqual(odd);
  });
});

describe("confirm receipt requests", () => {
  // SHOP-TASK-028 验收第 6 条「点击调用 POST /api/orders/confirm-receipt，带…Idempotency-Key…与接口返回的 X-CSRF-Token」：
  // 请求体只有订单号，订单号与令牌不进地址。
  it("posts the order number with the idempotency key and the CSRF token", async () => {
    const calls = stubFetch(json(200, { status: "demo_completed" }));
    await expect(submitConfirmReceipt(ORDER_NUMBER, TOKEN, KEY)).resolves.toBe("done");
    expect(calls[0]?.url).toBe(CONFIRM_RECEIPT_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headers(calls[0])[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // SHOP-TASK-028 验收第 6 条「带每次点击新生成的 Idempotency-Key（crypto.randomUUID）」与「仍为 demo_shipped 时才允许以同一幂等键重试」：
  // 没有待重试的确认时每次新生成；有时沿用它。
  it("generates a new key for every click unless retrying", () => {
    const first = receiptKey(null);
    const second = receiptKey(null);
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(second).not.toBe(first);
    expect(receiptKey(first)).toBe(first);
    expect(receiptKey(null, () => "k-1")).toBe("k-1");
  });

  // SHOP-TASK-028 验收第 6 条「200 或 409 order_not_confirmable 时重新读取订单并按新状态显示」：POST 之后紧接着读取该单。
  it.each([json(200, { status: "demo_completed" }), json(409, { detail: "order_not_confirmable", status: "demo_completed" })])(
    "reads the order again after the reply %#",
    async (reply) => {
      const completed = lookupOrder({ status: "demo_completed" });
      const calls = stubFetch(reply, json(200, ordersBody(completed)));
      await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({
        kind: "read",
        read: { kind: "ok", order: completed, csrfToken: TOKEN },
      });
      expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
      expect(calls[1]?.url).toBe("/api/orders/lookup?lang=en");
    },
  );

  // SHOP-TASK-028 验收第 6 条「200 或 409 order_not_confirmable 时重新读取订单」：只有 200 算确认成功；其他 2xx 不是约定的回答，
  // 按其他失败显示 common.error_retry，不当作已确认。
  it.each([json(201, { status: "demo_completed" }), empty(204)])("treats only 200 as confirmed (%#)", async (reply) => {
    const again = reply.clone();
    stubFetch(reply);
    await expect(submitConfirmReceipt(ORDER_NUMBER, TOKEN, KEY)).resolves.toBe("failed");
    stubFetch(again);
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "error", read: null });
  });

  // SHOP-TASK-028 验收第 6 条「403 csrf_failed 时重新读取订单取得新令牌并显示 common.error_retry」：POST 之后紧接着 GET，结果带新令牌并要求提示重试。
  it("reads the order again for a new token after csrf_failed", async () => {
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, ordersBody(lookupOrder(), "csrf-token-2")));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({
      kind: "error",
      read: { kind: "ok", order: lookupOrder(), csrfToken: "csrf-token-2" },
    });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
  });

  // SHOP-TASK-028 验收第 6 条「401 时整页显示 order.session_expired」；派生实现约束（实现选择）：其他失败（含 409 idempotency_conflict 与 5xx）显示 common.error_retry。
  it("settles 401 and other failures", async () => {
    stubFetch(json(401, { detail: "access_expired" }));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "expired" });
    stubFetch(json(409, { detail: "idempotency_conflict" }));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "error", read: null });
    stubFetch(json(500, {}));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "error", read: null });
    stubFetch(json(200, {}), json(401, { detail: "access_expired" }));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "expired" });
  });

  // UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」与 SHOP-TASK-028 验收第 6 条「网络中断时显示 common.network_check，
  // 先重新读取订单，仍为 demo_shipped 时才允许以同一幂等键重试」：POST 中断 → 通知页面显示 network_check → GET 确认仍已发货 →
  // 结果允许重试并带原来的键；再次提交时用同一个幂等键与同样的请求体。
  it("checks the order after a lost connection and retries with the same key", async () => {
    const calls = stubFetch(networkDown, json(200, ordersBody()), json(200, { status: "demo_completed" }), json(200, ordersBody(lookupOrder({ status: "demo_completed" }))));
    const checking = vi.fn();
    const outcome = await runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { onChecking: checking, delay: noDelay });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", read: { kind: "ok", order: lookupOrder(), csrfToken: TOKEN }, key: KEY });
    if (outcome?.kind !== "retry") {
      throw new Error("expected a retry");
    }
    const again = await runConfirmReceipt("en", ORDER_NUMBER, outcome.read.csrfToken, receiptKey(outcome.key));
    expect(again?.kind).toBe("read");
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET", "POST", "GET"]);
    expect(headers(calls[2])[IDEMPOTENCY_HEADER]).toBe(headers(calls[0])[IDEMPOTENCY_HEADER]);
    expect(body(calls[2])).toEqual(body(calls[0]));
  });

  // 同一条：重新读取发现上一步已完成（不再是 demo_shipped）时按新状态显示，不再允许重试；授权已过期时整页提示。
  it("shows the new status when the lost request went through", async () => {
    const completed = lookupOrder({ status: "demo_completed" });
    stubFetch(networkDown, json(200, ordersBody(completed)));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { delay: noDelay })).resolves.toEqual({
      kind: "read",
      read: { kind: "ok", order: completed, csrfToken: TOKEN },
    });
    stubFetch(networkDown, json(401, { detail: "access_expired" }));
    await expect(runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { delay: noDelay })).resolves.toEqual({ kind: "expired" });
  });

  // 同一条「先重新读取订单…才允许…重试」：重新读取本身也中断或失败时隔 RECHECK_DELAY_MS 再读，得到回答之前不给出可重试的结果。
  it("keeps checking until the order can be read", async () => {
    const calls = stubFetch(networkDown, networkDown, json(503, {}), json(200, ordersBody()));
    const delays: number[] = [];
    const outcome = await runConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, {
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
    const outcome = await recheckOrder("en", ORDER_NUMBER, {
      signal: controller.signal,
      delay: () => {
        controller.abort();
        return Promise.resolve();
      },
    });
    expect(outcome).toBeNull();
  });
});

describe("refund display rules", () => {
  // SHOP-TASK-030 验收第 3 条「P09 在接口判定退款期内且剩余可退大于零时显示 order.request_refund」与 UX P09「已支付且在退款期内」：
  // 两个条件都来自接口，缺一不显示（不在浏览器按截止时间自行判断）。
  it("shows the refund entry only inside the window with something left", () => {
    expect(showsRefundEntry({ refund_window_open: true, refundable_left_sen: 1 })).toBe(true);
    expect(showsRefundEntry({ refund_window_open: false, refundable_left_sen: 7800 })).toBe(false);
    expect(showsRefundEntry({ refund_window_open: true, refundable_left_sen: 0 })).toBe(false);
  });

  // SHOP-TASK-030 验收第 3 条「接口判定全部已退时显示 order.fulfilment_frozen 且不显示确认收货按钮」与 UX P09「确认收货：仅 demo_shipped 可点…全退后冻结」。
  it("freezes confirm receipt once everything is refunded", () => {
    expect(canConfirmReceipt({ status: "demo_shipped", fully_refunded: false })).toBe(true);
    expect(canConfirmReceipt({ status: "demo_shipped", fully_refunded: true })).toBe(false);
    expect(canConfirmReceipt({ status: "demo_paid", fully_refunded: false })).toBe(false);
  });
});

describe("refund amounts and lines", () => {
  // SHOP-TASK-030 验收第 5 条「refund.estimate 的金额取各行预计金额列表中对应件数的那一项再相加（只做整数仙加法，不做乘除或分摊）」与 UX P10 M2「由服务端按逐件快照计算」：
  // 列表各项故意不是单件的整数倍（1000、2500、2501），取第 k 项而不是单件乘 k；两行相加。
  it("adds the listed estimate for each chosen quantity", () => {
    const tee = line();
    const candle = line({ line_index: 1, name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_cash_paid_sen: [2763], refundable_quantity: 1, refund_estimates_sen: [2763] });
    expect(refundEstimate([tee, candle], [0, 0])).toBe(0);
    expect(refundEstimate([tee, candle], [1, 0])).toBe(1000);
    expect(refundEstimate([tee, candle], [2, 0])).toBe(2500);
    expect(refundEstimate([tee, candle], [3, 0])).toBe(2501);
    expect(refundEstimate([tee, candle], [3, 1])).toBe(2501 + 2763);
    expect(refundEstimate([tee, candle], [])).toBe(0);
  });

  // 派生实现约束（实现选择）：守住第 5 条「只做整数仙加法」——件数超出接口给的列表时没有对应金额，不自行推算，返回 null（页面不显示金额、不可提交）。
  it("gives no estimate beyond the listed quantities", () => {
    expect(refundEstimate([line({ refund_estimates_sen: [1000] })], [2])).toBeNull();
  });

  // SHOP-TASK-030 验收第 5 条「提交调用 POST /api/orders/refunds」与 SHOP-TASK-029 的请求体：只放件数大于 0 的行，行序取订单行的 line_index（不是列表下标）。
  it("sends only the chosen lines with their order line index", () => {
    const lines = [line({ line_index: 3 }), line({ line_index: 5 }), line({ line_index: 7 })];
    expect(refundLines(lines, [0, 2, 1])).toEqual([
      { line_index: 5, quantity: 2 },
      { line_index: 7, quantity: 1 },
    ]);
    expect(refundLines(lines, [0, 0, 0])).toEqual([]);
  });

  // SHOP-TASK-030 验收第 4 条「detail.quantity 加减控件（0 到该行可退件数）」：重新读取订单后已选件数不超过新的可退件数，可退件数为 0 的行为 0。
  it("keeps the chosen quantities within what is refundable", () => {
    const lines = [line({ refundable_quantity: 1 }), line({ refundable_quantity: 0 }), line({ refundable_quantity: 3 })];
    expect(clampQuantities(lines, [2, 1, 2])).toEqual([1, 0, 2]);
    expect(clampQuantities(lines, [])).toEqual([0, 0, 0]);
  });
});

describe("refund requests", () => {
  const LINES = [{ line_index: 0, quantity: 1 }];

  // SHOP-TASK-030 验收第 5 条「带每次提交新生成的 Idempotency-Key（crypto.randomUUID）」与「网络中断时…允许以同一幂等键与同一请求内容重试」：
  // 没有待重试的申请时每次新生成；有待重试的申请且请求内容相同时沿用它的键；内容改了就是新的申请，另生成。
  it("generates a new key for every submission unless retrying the same request", () => {
    const first = refundKey(null, LINES);
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(refundKey(null, LINES)).not.toBe(first);
    const retry = { key: first, lines: LINES };
    expect(refundKey(retry, [{ line_index: 0, quantity: 1 }])).toBe(first);
    expect(refundKey(retry, [{ line_index: 0, quantity: 2 }], () => "k-2")).toBe("k-2");
    expect(refundKey(retry, [...LINES, { line_index: 1, quantity: 1 }], () => "k-3")).toBe("k-3");
  });

  // SHOP-TASK-030 验收第 5 条「提交调用 POST /api/orders/refunds，带…Idempotency-Key…与接口返回的 X-CSRF-Token」与验收第 2 条「订单号与 CSRF 令牌不进任何路径、查询参数」：
  // 请求体只有订单号与申请行（没有金额），订单号与令牌不进地址。
  it("posts the order number and lines with the idempotency key and the CSRF token", async () => {
    const calls = stubFetch(json(201, { status: "requested", amount_sen: 3900, lines: LINES }));
    await expect(submitRefund(ORDER_NUMBER, TOKEN, KEY, LINES)).resolves.toBe("done");
    expect(calls[0]?.url).toBe(REFUNDS_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headers(calls[0])[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(body(calls[0])).toEqual({ order_number: ORDER_NUMBER, lines: LINES });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // SHOP-TASK-030 验收第 5 条「201 或 200 时…；409 refund_duplicate、refund_nothing_left、refund_window_closed…；409 order_not_refundable…；409 idempotency_conflict…；
  // 403 csrf_failed…；401…；网络中断…」：每种回答各自分开报告；其他回答（含 201、200 以外的 2xx 与未知的 409）为 failed。
  it.each<[Response | (() => never), string]>([
    [json(201, {}), "done"],
    [json(200, {}), "done"],
    [json(409, { detail: "refund_duplicate" }), "duplicate"],
    [json(409, { detail: "refund_nothing_left" }), "nothing_left"],
    [json(409, { detail: "refund_window_closed" }), "window_closed"],
    [json(409, { detail: "order_not_refundable" }), "not_refundable"],
    [json(409, { detail: "idempotency_conflict" }), "conflict"],
    [json(409, { detail: "something_else" }), "failed"],
    [json(409, { detail: "constructor" }), "failed"],
    [json(401, { detail: "access_expired" }), "expired"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [empty(204), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitRefund(ORDER_NUMBER, TOKEN, KEY, LINES)).resolves.toBe(kind);
  });

  // SHOP-TASK-030 验收第 5 条「201 或 200 时回到 /track/order 并显示 refund.submitted」「409 order_not_refundable 时回到 /track/order」「401 时整页显示 order.session_expired」：
  // 这三种不再读取订单（只有一次 POST）。
  it.each<[Response, string]>([
    [json(201, {}), "submitted"],
    [json(200, {}), "submitted"],
    [json(409, { detail: "order_not_refundable" }), "leave"],
    [json(401, { detail: "access_expired" }), "expired"],
  ])("settles without reading the order again (%#)", async (reply, kind) => {
    const calls = stubFetch(reply);
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind });
    expect(calls.map((call) => call.init.method)).toEqual(["POST"]);
  });

  // SHOP-TASK-030 验收第 5 条「409 refund_duplicate、refund_nothing_left、refund_window_closed 分别显示 refund.duplicate、refund.nothing_left、refund.window_closed 并重新读取订单」：
  // POST 之后紧接着 GET 该单，结果带被拒的原因与重新读取到的订单。
  it.each(["duplicate", "nothing_left", "window_closed"] as const)("reads the order again after refund_%s", async (reason) => {
    const fresh = lookupOrder({ refundable_left_sen: 3900 });
    const calls = stubFetch(json(409, { detail: `refund_${reason}` }), json(200, ordersBody(fresh)));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({
      kind: "refused",
      reason,
      read: { kind: "ok", order: fresh, csrfToken: TOKEN },
    });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
    expect(calls[1]?.url).toBe("/api/orders/lookup?lang=en");
  });

  // SHOP-TASK-030 验收第 5 条「409 idempotency_conflict 时重新读取订单并显示 common.error_retry，下次提交改用新的幂等键」：
  // 结果为 error（页面显示 common.error_retry）且不带可沿用的键；下次提交没有待重试的申请，refundKey 另生成。
  it("reads the order again after idempotency_conflict and offers no key to reuse", async () => {
    const calls = stubFetch(json(409, { detail: "idempotency_conflict" }), json(200, ordersBody()));
    const outcome = await runRefund("en", lookupOrder(), TOKEN, KEY, LINES);
    expect(outcome).toEqual({ kind: "error", read: { kind: "ok", order: lookupOrder(), csrfToken: TOKEN } });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
    expect(refundKey(null, LINES)).not.toBe(KEY);
  });

  // SHOP-TASK-030 验收第 5 条「403 csrf_failed 时重新读取订单取得新令牌并显示 common.error_retry」：POST 之后紧接着 GET，结果带新令牌并要求提示重试。
  it("reads the order again for a new token after csrf_failed", async () => {
    const calls = stubFetch(json(403, { detail: "csrf_failed" }), json(200, ordersBody(lookupOrder(), "csrf-token-2")));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({
      kind: "error",
      read: { kind: "ok", order: lookupOrder(), csrfToken: "csrf-token-2" },
    });
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET"]);
  });

  // 同一条「重新读取订单」：重新读取得到 401 时整页过期；重新读取失败时沿用原来的订单（read 为 null）；其他失败直接提示重试、不读取。
  it("settles when the re-read fails or for other failures", async () => {
    stubFetch(json(409, { detail: "refund_duplicate" }), json(401, { detail: "access_expired" }));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "expired" });
    stubFetch(json(409, { detail: "refund_nothing_left" }), json(500, {}));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "refused", reason: "nothing_left", read: null });
    const calls = stubFetch(json(500, {}));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "error", read: null });
    expect(calls).toHaveLength(1);
  });

  // UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单或申请状态，再允许重试」与 SHOP-TASK-030 验收第 5 条「网络中断时显示 common.network_check，
  // 允许以同一幂等键与同一请求内容重试」：POST 中断 → 通知页面显示 network_check → GET 确认申请没有写入 → 结果带原来的键与请求行；
  // 再次提交时 refundKey 沿用该键，请求头与请求体都与第一次相同。
  it("checks the order after a lost connection and retries with the same key and body", async () => {
    const calls = stubFetch(networkDown, json(200, ordersBody()), json(200, { status: "requested" }));
    const checking = vi.fn();
    const outcome = await runRefund("en", lookupOrder(), TOKEN, KEY, LINES, { onChecking: checking, delay: noDelay });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", read: { kind: "ok", order: lookupOrder(), csrfToken: TOKEN }, retry: { key: KEY, lines: LINES } });
    if (outcome?.kind !== "retry") {
      throw new Error("expected a retry");
    }
    const lines = refundLines(outcome.read.order.lines, [1]);
    await expect(submitRefund(ORDER_NUMBER, outcome.read.csrfToken, refundKey(outcome.retry, lines), lines)).resolves.toBe("done");
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET", "POST"]);
    expect(headers(calls[2])[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(body(calls[2])).toEqual(body(calls[0]));
  });

  // 同一条「查询原订单或申请状态」：重新读取到的订单多了一笔申请时视为已提交（回到 P09 显示 refund.submitted），不再重试；授权已过期时整页提示；
  // 读取也中断时按间隔重读。
  it("treats a new request on the re-read order as submitted", async () => {
    stubFetch(networkDown, json(200, ordersBody(lookupOrder({ refund_requests: [REQUESTED] }))));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES, { delay: noDelay })).resolves.toEqual({ kind: "submitted" });
    stubFetch(networkDown, json(401, { detail: "access_expired" }));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES, { delay: noDelay })).resolves.toEqual({ kind: "expired" });
    const calls = stubFetch(networkDown, networkDown, json(200, ordersBody()));
    const outcome = await runRefund("en", lookupOrder(), TOKEN, KEY, LINES, { delay: noDelay });
    expect(outcome?.kind).toBe("retry");
    expect(calls.map((call) => call.init.method)).toEqual(["POST", "GET", "GET"]);
  });

  // 派生实现约束（实现选择）：离开页面（中止）后不报告结果。
  it("reports nothing once aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    stubFetch(json(409, { detail: "refund_duplicate" }));
    await expect(runRefund("en", lookupOrder(), TOKEN, KEY, LINES, { signal: controller.signal })).resolves.toBeNull();
  });

  // SHOP-TASK-030 验收第 2 条「订单号…只在页面内存里」与第 5 条「201 或 200 时回到 /track/order 并显示 refund.submitted」：交给 P09 的订单号只在模块变量里，取了之后可清除。
  it("hands the submitted order to the order page in memory only", () => {
    expect(submittedRefundOrder()).toBeNull();
    handOffSubmittedRefund(ORDER_NUMBER);
    expect(submittedRefundOrder()).toBe(ORDER_NUMBER);
    clearSubmittedRefund();
    expect(submittedRefundOrder()).toBeNull();
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-028 验收第 2 条「订单号、电话与 CSRF 令牌不进任何路径、查询参数、localStorage、sessionStorage 或 cookie，只在页面内存里」：
  // 查单、读取、确认收货（含网络中断后的确认与重试）的整个过程不写任何浏览器存储、cookie 或历史记录，请求地址里没有订单号、电话或令牌。
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

    const calls = stubFetch(empty(204), json(200, ordersBody()), networkDown, json(200, ordersBody()), json(200, { status: "demo_completed" }), json(200, ordersBody()));
    await submitLookup(ORDER_NUMBER, PHONE);
    const read = await readLookupOrder("en", null);
    if (read.kind !== "ok") {
      throw new Error("expected an order");
    }
    const first = await runConfirmReceipt("en", read.order.order_number, read.csrfToken, receiptKey(null), { delay: noDelay });
    if (first?.kind !== "retry") {
      throw new Error("expected a retry");
    }
    await runConfirmReceipt("en", read.order.order_number, first.read.csrfToken, receiptKey(first.key));

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls).toHaveLength(6);
    for (const call of calls) {
      expect(call.url).not.toContain(ORDER_NUMBER);
      expect(call.url).not.toContain("7700900123");
      expect(call.url).not.toContain(TOKEN);
      expect(call.url).toMatch(/^\/api\/orders\/(lookup|lookup\?lang=en|confirm-receipt)$/);
    }
  });

  // SHOP-TASK-030 验收第 2 条「订单号与 CSRF 令牌不进任何路径、查询参数、localStorage、sessionStorage 或 cookie，只在页面内存里」：
  // 读取、退款申请（含 409 后重新读取、网络中断后的确认与重试）与交给 P09 的整个过程不写任何浏览器存储、cookie 或历史记录，请求地址里没有订单号或令牌。
  it("keeps the order number and token out of addresses and browser storage when requesting a refund", async () => {
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

    const calls = stubFetch(
      json(200, ordersBody()),
      json(409, { detail: "refund_duplicate" }),
      json(200, ordersBody()),
      networkDown,
      json(200, ordersBody()),
      json(201, { status: "requested" }),
    );
    const read = await readLookupOrder("en", null);
    if (read.kind !== "ok") {
      throw new Error("expected an order");
    }
    const lines = refundLines(read.order.lines, [1]);
    await runRefund("en", read.order, read.csrfToken, refundKey(null, lines), lines);
    const lost = await runRefund("en", read.order, read.csrfToken, refundKey(null, lines), lines, { delay: noDelay });
    if (lost?.kind !== "retry") {
      throw new Error("expected a retry");
    }
    await expect(runRefund("en", lost.read.order, lost.read.csrfToken, refundKey(lost.retry, lines), lines)).resolves.toEqual({ kind: "submitted" });
    handOffSubmittedRefund(read.order.order_number);
    clearSubmittedRefund();

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(calls).toHaveLength(6);
    for (const call of calls) {
      expect(call.url).not.toContain(ORDER_NUMBER);
      expect(call.url).not.toContain(TOKEN);
      expect(call.url).toMatch(/^\/api\/orders\/(lookup\?lang=en|refunds)$/);
    }
  });
});
