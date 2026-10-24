import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearMemberOrder,
  estimateMemberRefundPoints,
  handOffMemberOrder,
  MEMBER_CONFIRM_RECEIPT_URL,
  MEMBER_ORDER_VIEW_URL,
  MEMBER_ORDERS_URL,
  MEMBER_POINTS_ESTIMATE_URL,
  MEMBER_REFUNDS_URL,
  memberOrderInView,
  memberOrderViewUrl,
  readMemberOrder,
  readMemberOrders,
  recheckMemberOrder,
  runMemberConfirmReceipt,
  runMemberRefund,
  submitMemberConfirmReceipt,
  submitMemberRefund,
} from "./memberOrders";
import type { MemberOrderSummary } from "./memberOrders";
import { receiptKey, refundKey } from "./orderLookup";
import type { LookupOrder, LookupRefund, RefundLineRequest } from "./orderLookup";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearMemberOrder();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const OTHER_ORDER = "K7Q29MXA00000000";
const TOKEN = "csrf-token-1";
const KEY = "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60";
const LINES: RefundLineRequest[] = [{ line_index: 0, quantity: 1 }];

function memberOrder(overrides: Partial<LookupOrder> = {}): LookupOrder {
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
        unit_cash_paid_sen: [3700, 3600],
        refundable_quantity: 2,
        refund_estimates_sen: [3700, 7300],
      },
    ],
    subtotal_sen: 7800,
    placed_by_member: true,
    coupon_discount_sen: 300,
    points_redeemed: 200,
    shipping_fee_sen: 1500,
    total_sen: 8800,
    recipient: {
      name: "Sam Taylor",
      phone: "+60123456789",
      country_code: "MY",
      region: "Selangor",
      address: "1 Jalan Contoh",
      postal_code: "47300",
    },
    refund_deadline: "2026-11-04T10:02:00Z",
    refund_window_open: true,
    refunded_total_sen: 0,
    refundable_left_sen: 7300,
    fully_refunded: false,
    refund_requests: [],
    ...overrides,
  };
}

const REQUESTED: LookupRefund = {
  created_at: "2026-10-06T08:00:00Z",
  status: "requested",
  amount_sen: 3700,
  lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 1, amount_sen: 3700 }],
};

const SUMMARIES: MemberOrderSummary[] = [
  { order_number: ORDER_NUMBER, created_at: "2026-10-05T10:00:00Z", status: "demo_shipped", total_sen: 8800 },
  { order_number: OTHER_ORDER, created_at: "2026-10-01T09:00:00Z", status: "demo_completed", total_sen: 3900 },
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

const SIGNED_OUT = () => json(401, { detail: "member_session_required" });
const NOT_FOUND = () => json(404, { detail: "order_not_found" });
const CSRF_FAILED = () => json(403, { detail: "csrf_failed" });

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
  return JSON.parse(String(initOf(fetchMock, index).body));
}

function methods(fetchMock: FetchMock): string[] {
  return fetchMock.mock.calls.map((call) => String(call[1]?.method));
}

const noDelay = () => Promise.resolve();

describe("reading my orders", () => {
  // SHOP-TASK-104 的接口记录（GET /api/member/orders，服务端默认第 1 页、每页 20 张、按时间从新到旧）与 UX P13「我的订单」：
  // 同源 cookie、不缓存的 GET，地址不带 page 或任何查询参数；取 total 与 orders 原样交给页面。
  it("gets the first page without query parameters", async () => {
    const fetchMock = stubFetch(json(200, { total: 21, page: 1, page_size: 20, orders: SUMMARIES }));
    const controller = new AbortController();
    await expect(readMemberOrders(controller.signal)).resolves.toEqual({ kind: "ok", total: 21, orders: SUMMARIES });
    expect(urlOf(fetchMock)).toBe(MEMBER_ORDERS_URL);
    expect(urlOf(fetchMock)).not.toContain("?");
    expect(initOf(fetchMock)).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(initOf(fetchMock).body).toBeUndefined();
    expect(headersOf(fetchMock)).toEqual({ Accept: "application/json" });
  });

  // DESIGN 1.13「权限与资料保护」第 3 条（会话到期由服务端检查）与 SHOP-TASK-104 的接口记录（401 member_session_required）：
  // 401 为 signed_out；其他失败（含不是 JSON 与 200 以外的 2xx）为 failed，网络中断为 network。
  it.each<[Response | null, string]>([
    [SIGNED_OUT(), "signed_out"],
    [json(422, { detail: [{ type: "page_invalid" }] }), "failed"],
    [json(500, {}), "failed"],
    [json(201, { total: 0, orders: [] }), "failed"],
    [text(200, "not json"), "failed"],
    [null, "network"],
  ])("sorts the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readMemberOrders()).resolves.toEqual({ kind });
  });
});

describe("reading one member order", () => {
  // SHOP-TASK-105 的接口记录（POST /api/member/orders/view，响应体与查单的单张订单相同、没有 csrf_token）与
  // HANDOFF 0.61「订单号只经本模块的变量交接，不进地址」：订单号只在请求体里，地址只带语言（写法同 lookupOrdersUrl）。
  it("posts only the order number and puts only the language in the address", async () => {
    const fetchMock = stubFetch(json(200, memberOrder()));
    const controller = new AbortController();
    await expect(readMemberOrder("zh", ORDER_NUMBER, controller.signal)).resolves.toEqual({ kind: "ok", order: memberOrder() });
    expect(urlOf(fetchMock)).toBe(`${MEMBER_ORDER_VIEW_URL}?lang=zh`);
    expect(urlOf(fetchMock)).not.toContain(ORDER_NUMBER);
    expect(memberOrderViewUrl("ms")).toBe("/api/member/orders/view?lang=ms");
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headersOf(fetchMock)).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
  });

  // DESIGN 1.13「权限与资料保护」第 5 条（会员仅能访问自己认领或下单的订单）与 SHOP-TASK-105 的接口记录（404 order_not_found）：
  // 401 为 signed_out，404 为 not_found；422 与其他失败为 failed。
  it.each<[Response | null, string]>([
    [SIGNED_OUT(), "signed_out"],
    [NOT_FOUND(), "not_found"],
    [json(422, { detail: [{ type: "string_too_long" }] }), "failed"],
    [json(404, { detail: "Not Found" }), "failed"],
    [json(500, {}), "failed"],
    [text(200, "not json"), "failed"],
    [null, "network"],
  ])("sorts the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readMemberOrder("en", ORDER_NUMBER)).resolves.toEqual({ kind });
  });
});

describe("confirming receipt as a member", () => {
  // SHOP-TASK-105 的接口记录（POST /api/member/orders/confirm-receipt，写操作另须 CSRF 令牌，DESIGN 1.13「权限与资料保护」第 5 条）
  // 与「失败、并发与重试」（确认收货幂等）：请求体只有订单号，请求头带 Idempotency-Key 与 X-CSRF-Token；地址不带查询参数与订单号。
  it("posts the order number with the key and token", async () => {
    const fetchMock = stubFetch(json(200, { status: "demo_completed" }));
    await expect(submitMemberConfirmReceipt(ORDER_NUMBER, TOKEN, receiptKey(null, () => KEY))).resolves.toBe("done");
    expect(urlOf(fetchMock)).toBe(MEMBER_CONFIRM_RECEIPT_URL);
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headersOf(fetchMock)).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
      [IDEMPOTENCY_HEADER]: KEY,
      [CSRF_HEADER]: TOKEN,
    });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER });
  });

  // SHOP-TASK-105 的接口记录（409 order_not_confirmable、fulfilment_frozen、idempotency_conflict；403 csrf_failed；401；404）：
  // 只有 order_not_confirmable 为 closed，其余 409、422 与意外回答为 failed。
  it.each<[Response | null, string]>([
    [json(409, { detail: "order_not_confirmable", status: "demo_completed" }), "closed"],
    [json(409, { detail: "fulfilment_frozen", status: "demo_shipped" }), "failed"],
    [json(409, { detail: "idempotency_conflict" }), "failed"],
    [SIGNED_OUT(), "signed_out"],
    [NOT_FOUND(), "not_found"],
    [CSRF_FAILED(), "csrf"],
    [json(422, { detail: [{ type: "missing" }] }), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [json(500, {}), "failed"],
    [null, "network"],
  ])("sorts the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitMemberConfirmReceipt(ORDER_NUMBER, TOKEN, KEY)).resolves.toBe(kind);
  });

  // UX P09「确认收货」与查单版 runConfirmReceipt 的同一去向：200 与 409 order_not_confirmable 都改用会员详情重新读取订单再按新状态显示。
  it("re-reads the order through the member view after done or closed", async () => {
    const completed = memberOrder({ status: "demo_completed" });
    const fetchMock = stubFetch(json(200, { status: "demo_completed" }), json(200, completed));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "read", order: completed });
    expect(urlOf(fetchMock, 1)).toBe(`${MEMBER_ORDER_VIEW_URL}?lang=en`);
    expect(bodyOf(fetchMock, 1)).toEqual({ order_number: ORDER_NUMBER });
    stubFetch(json(409, { detail: "order_not_confirmable" }), json(200, completed));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "read", order: completed });
    stubFetch(json(200, {}), json(500, {}));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "error", order: null });
  });

  // 本任务验收「403 csrf 归一个单独的结果（带重新读取到的订单，没有则为空），由页面重读会员会话取得新令牌后提示重试」。
  it("reports a stale token separately with the re-read order", async () => {
    stubFetch(CSRF_FAILED(), json(200, memberOrder()));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "csrf", order: memberOrder() });
    stubFetch(CSRF_FAILED(), null);
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "csrf", order: null });
  });

  // 本任务验收「查单版的 expired 换成 signed_out，另有 not_found」与 DESIGN 1.13「权限与资料保护」第 3、5 条：
  // 提交本身或之后的重新读取得到 401、404 都直接报告，不再显示订单；其他提交失败为 error（不重新读取）。
  it("reports signed out and not found from the submit or the re-read", async () => {
    stubFetch(SIGNED_OUT());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "signed_out" });
    stubFetch(NOT_FOUND());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "not_found" });
    stubFetch(json(200, {}), SIGNED_OUT());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "signed_out" });
    stubFetch(CSRF_FAILED(), NOT_FOUND());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "not_found" });
    const fetchMock = stubFetch(json(409, { detail: "fulfilment_frozen" }));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY)).resolves.toEqual({ kind: "error", order: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // DESIGN 1.13「失败、并发与重试」（确认收货幂等）与查单版的同一条件：网络中断后先重新读取订单，仍为 demo_shipped 时才以同一幂等键重试；
  // 重试时 receiptKey 沿用原来的键。
  it("checks the order after a lost connection and retries with the same key", async () => {
    const fetchMock = stubFetch(null, json(200, memberOrder()), json(200, {}), json(200, memberOrder({ status: "demo_completed" })));
    const checking = vi.fn();
    const outcome = await runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { onChecking: checking, delay: noDelay });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", order: memberOrder(), key: KEY });
    if (outcome?.kind !== "retry") {
      return;
    }
    await runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, receiptKey(outcome.key, () => "fresh-key"));
    expect(headersOf(fetchMock, 2)[IDEMPOTENCY_HEADER]).toBe(KEY);
  });

  // 同一条：重新读取到的订单已不是 demo_shipped 时按新状态显示；读到 401、404 时直接报告。
  it("shows the new status or the session end after a lost connection", async () => {
    const completed = memberOrder({ status: "demo_completed" });
    stubFetch(null, json(200, completed));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { delay: noDelay })).resolves.toEqual({ kind: "read", order: completed });
    stubFetch(null, SIGNED_OUT());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { delay: noDelay })).resolves.toEqual({ kind: "signed_out" });
    stubFetch(null, NOT_FOUND());
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { delay: noDelay })).resolves.toEqual({ kind: "not_found" });
  });

  // 本任务验收「网络中断后的确认照 recheckOrder 写法重读到 ok、signed_out 或 not_found 为止，间隔可注入」：
  // 重新读取中断或失败时隔 RECHECK_DELAY_MS 再读，得到回答之前不给出可重试的结果。
  it("keeps checking until the order can be read", async () => {
    const fetchMock = stubFetch(null, null, json(503, {}), json(200, memberOrder()));
    const delays: number[] = [];
    const outcome = await runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, {
      delay: (ms) => {
        delays.push(ms);
        return Promise.resolve();
      },
    });
    expect(methods(fetchMock)).toEqual(["POST", "POST", "POST", "POST"]);
    expect(urlOf(fetchMock, 3)).toBe(`${MEMBER_ORDER_VIEW_URL}?lang=en`);
    expect(delays).toEqual([RECHECK_DELAY_MS, RECHECK_DELAY_MS]);
    expect(outcome?.kind).toBe("retry");
  });

  // 本任务验收「页面已离开（中止）时返回 null」：中止后不再重读，也不报告结果。
  it("reports nothing once aborted", async () => {
    stubFetch(null, null);
    const controller = new AbortController();
    const checked = await recheckMemberOrder("en", ORDER_NUMBER, {
      signal: controller.signal,
      delay: () => {
        controller.abort();
        return Promise.resolve();
      },
    });
    expect(checked).toBeNull();
    const aborted = new AbortController();
    aborted.abort();
    stubFetch(json(200, {}));
    await expect(runMemberConfirmReceipt("en", ORDER_NUMBER, TOKEN, KEY, { signal: aborted.signal })).resolves.toBeNull();
  });
});

describe("requesting a refund as a member", () => {
  // SHOP-TASK-105 的接口记录（POST /api/member/orders/refunds，请求体只有订单号与申请行，写操作另须 CSRF 令牌）与
  // DESIGN 1.13「失败、并发与重试」（退款申请幂等）：请求头带 Idempotency-Key 与 X-CSRF-Token，没有任何金额字段，地址不带查询参数。
  it("posts the order number and lines with the key and token", async () => {
    const fetchMock = stubFetch(json(201, {}));
    await expect(submitMemberRefund(ORDER_NUMBER, TOKEN, refundKey(null, LINES, () => KEY), LINES)).resolves.toBe("done");
    expect(urlOf(fetchMock)).toBe(MEMBER_REFUNDS_URL);
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headersOf(fetchMock)).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
      [IDEMPOTENCY_HEADER]: KEY,
      [CSRF_HEADER]: TOKEN,
    });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER, lines: LINES });
  });

  // SHOP-TASK-105 的接口记录（201 新写、200 重放；409 五个错误码；401；404；403）与查单版 submitRefund 的 409 分法：
  // 未列出的 409、422 与意外回答为 failed。
  it.each<[Response | null, string]>([
    [json(200, {}), "done"],
    [json(409, { detail: "refund_duplicate" }), "duplicate"],
    [json(409, { detail: "refund_nothing_left" }), "nothing_left"],
    [json(409, { detail: "refund_window_closed" }), "window_closed"],
    [json(409, { detail: "order_not_refundable" }), "not_refundable"],
    [json(409, { detail: "idempotency_conflict" }), "conflict"],
    [json(409, { detail: "fulfilment_frozen" }), "failed"],
    [text(409, "conflict"), "failed"],
    [SIGNED_OUT(), "signed_out"],
    [NOT_FOUND(), "not_found"],
    [CSRF_FAILED(), "csrf"],
    [json(422, { detail: [{ type: "value_error" }] }), "failed"],
    [json(500, {}), "failed"],
    [null, "network"],
  ])("sorts the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitMemberRefund(ORDER_NUMBER, TOKEN, KEY, LINES)).resolves.toBe(kind);
  });

  // UX P10 与查单版 runRefund 的同一去向：done 为 submitted（回到 P09），order_not_refundable 为 leave；都不重新读取。
  it("goes back to the order after done or not refundable", async () => {
    let fetchMock = stubFetch(json(201, {}));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "submitted" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock = stubFetch(json(409, { detail: "order_not_refundable" }));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "leave" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    stubFetch(json(422, { detail: [] }));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "error", order: null });
  });

  // UX P10「refund.duplicate、refund.nothing_left、refund.window_closed」与查单版的同一去向：被拒时改用会员详情重新读取订单；
  // 幂等键冲突为 error（下次提交另生成键）；重新读取失败时 order 为空。
  it("re-reads the order through the member view when refused", async () => {
    const left = memberOrder({ refundable_left_sen: 0 });
    const fetchMock = stubFetch(json(409, { detail: "refund_nothing_left" }), json(200, left));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "refused", reason: "nothing_left", order: left });
    expect(urlOf(fetchMock, 1)).toBe(`${MEMBER_ORDER_VIEW_URL}?lang=en`);
    expect(bodyOf(fetchMock, 1)).toEqual({ order_number: ORDER_NUMBER });
    stubFetch(json(409, { detail: "refund_duplicate" }), json(500, {}));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "refused", reason: "duplicate", order: null });
    stubFetch(json(409, { detail: "refund_window_closed" }), json(200, left));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "refused", reason: "window_closed", order: left });
    stubFetch(json(409, { detail: "idempotency_conflict" }), json(200, left));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "error", order: left });
  });

  // 本任务验收「403 csrf 归一个单独的结果（带重新读取到的订单，没有则为空）」与「查单版的 expired 换成 signed_out，另有 not_found」。
  it("reports a stale token, signed out and not found", async () => {
    stubFetch(CSRF_FAILED(), json(200, memberOrder()));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "csrf", order: memberOrder() });
    stubFetch(CSRF_FAILED(), json(503, {}));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "csrf", order: null });
    stubFetch(SIGNED_OUT());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "signed_out" });
    stubFetch(NOT_FOUND());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "not_found" });
    stubFetch(json(409, { detail: "refund_duplicate" }), SIGNED_OUT());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "signed_out" });
    stubFetch(CSRF_FAILED(), NOT_FOUND());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES)).resolves.toEqual({ kind: "not_found" });
  });

  // DESIGN 1.13「失败、并发与重试」（退款申请幂等）与查单版的同一条件：网络中断后重新读取，申请记录没有多一笔时才以同一幂等键与同一请求内容重试；
  // 重试时 refundKey 沿用原来的键。
  it("checks the order after a lost connection and retries with the same key and body", async () => {
    const fetchMock = stubFetch(null, json(200, memberOrder()), json(201, {}));
    const checking = vi.fn();
    const outcome = await runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, { onChecking: checking, delay: noDelay });
    expect(checking).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ kind: "retry", order: memberOrder(), retry: { key: KEY, lines: LINES } });
    if (outcome?.kind !== "retry") {
      return;
    }
    await runMemberRefund("en", outcome.order, TOKEN, refundKey(outcome.retry, LINES, () => "fresh-key"), LINES);
    expect(headersOf(fetchMock, 2)[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(bodyOf(fetchMock, 2)).toEqual({ order_number: ORDER_NUMBER, lines: LINES });
  });

  // 同一条：重新读取到的申请多了一笔即算已提交；读到 401、404 时直接报告；读取也中断时按间隔重读。
  it("treats a new request on the re-read order as submitted", async () => {
    stubFetch(null, json(200, memberOrder({ refund_requests: [REQUESTED] })));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, { delay: noDelay })).resolves.toEqual({ kind: "submitted" });
    stubFetch(null, SIGNED_OUT());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, { delay: noDelay })).resolves.toEqual({ kind: "signed_out" });
    stubFetch(null, NOT_FOUND());
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, { delay: noDelay })).resolves.toEqual({ kind: "not_found" });
    const delays: number[] = [];
    const fetchMock = stubFetch(null, null, json(200, memberOrder()));
    const outcome = await runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, {
      delay: (ms) => {
        delays.push(ms);
        return Promise.resolve();
      },
    });
    expect(outcome?.kind).toBe("retry");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(delays).toEqual([RECHECK_DELAY_MS]);
  });

  // 本任务验收「页面已离开（中止）时返回 null」。
  it("reports nothing once aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    stubFetch(json(409, { detail: "refund_duplicate" }));
    await expect(runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, { signal: controller.signal })).resolves.toBeNull();
    const leaving = new AbortController();
    stubFetch(null, null);
    await expect(
      runMemberRefund("en", memberOrder(), TOKEN, KEY, LINES, {
        signal: leaving.signal,
        delay: () => {
          leaving.abort();
          return Promise.resolve();
        },
      }),
    ).resolves.toBeNull();
  });
});

describe("estimating refund points as a member", () => {
  // SHOP-TASK-105 的接口记录（POST /api/member/orders/refunds/points-estimate 只读，不要求幂等键与 CSRF）与 UX「阅读说明」订单号只在请求体里：
  // 请求体只有订单号与申请行；没有 X-CSRF-Token 与 Idempotency-Key，地址不带查询参数与订单号；signal 传给 fetch。
  it("posts only the order number and lines, without token or key", async () => {
    const fetchMock = stubFetch(json(200, { member_order: true, points_back: 120, points_reversed: 45 }));
    const controller = new AbortController();
    await expect(estimateMemberRefundPoints(ORDER_NUMBER, LINES, controller.signal)).resolves.toEqual({
      kind: "ok",
      memberOrder: true,
      pointsBack: 120,
      pointsReversed: 45,
    });
    expect(urlOf(fetchMock)).toBe(MEMBER_POINTS_ESTIMATE_URL);
    expect(urlOf(fetchMock)).not.toContain("?");
    expect(urlOf(fetchMock)).not.toContain(ORDER_NUMBER);
    expect(initOf(fetchMock)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(headersOf(fetchMock)).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
    expect(bodyOf(fetchMock)).toEqual({ order_number: ORDER_NUMBER, lines: LINES });
  });

  // SHOP-TASK-105 的接口记录（401、404，409 order_not_refundable、refund_window_closed、refund_nothing_left，422）与
  // SHOP-TASK-149 的 parsePointsEstimate：401 为 signed_out，404 为 not_found；409、422、其他 2xx 与响应体不合格为 failed。
  it.each<[Response | null, string]>([
    [SIGNED_OUT(), "signed_out"],
    [NOT_FOUND(), "not_found"],
    [json(409, { detail: "order_not_refundable" }), "failed"],
    [json(409, { detail: "refund_window_closed" }), "failed"],
    [json(409, { detail: "refund_nothing_left" }), "failed"],
    [json(422, { detail: [{ type: "value_error" }] }), "failed"],
    [json(201, { member_order: true, points_back: 1, points_reversed: 1 }), "failed"],
    [json(200, { member_order: true, points_back: -1, points_reversed: 0 }), "failed"],
    [json(200, { member_order: "yes", points_back: 0, points_reversed: 0 }), "failed"],
    [text(200, "not json"), "failed"],
    [null, "network"],
  ])("sorts the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(estimateMemberRefundPoints(ORDER_NUMBER, LINES)).resolves.toEqual({ kind });
  });
});

describe("handing over the member order in view", () => {
  // HANDOFF 0.61（运营者落实写法）「订单号只经本模块的变量交接，不进地址、历史状态与浏览器存储」：
  // 交出后可读取，读取不清除（P09 与 P10 的会员模式先后读同一个值）；不写 localStorage、sessionStorage，不改地址与历史状态。
  it("keeps the order number in module memory only, and reading does not clear it", () => {
    // 浏览器存储与历史记录以替身代替（测试在 node 环境运行）；任何一次写入都会被记下。
    const storageWrite = vi.fn((key: string, value: string) => {
      void key;
      void value;
    });
    const historyWrite = vi.fn((state: unknown, unused: string, url?: string | URL | null) => {
      void state;
      void unused;
      void url;
    });
    vi.stubGlobal("localStorage", { setItem: storageWrite });
    vi.stubGlobal("sessionStorage", { setItem: storageWrite });
    vi.stubGlobal("history", { pushState: historyWrite, replaceState: historyWrite });
    expect(memberOrderInView()).toBeNull();
    handOffMemberOrder(ORDER_NUMBER);
    expect(memberOrderInView()).toBe(ORDER_NUMBER);
    expect(memberOrderInView()).toBe(ORDER_NUMBER);
    expect(storageWrite).not.toHaveBeenCalled();
    expect(historyWrite).not.toHaveBeenCalled();
  });

  // 同一决定「P13 交出新值时覆盖；退出登录时清除」。
  it("overwrites on a new hand-off and clears on sign-out", () => {
    handOffMemberOrder(ORDER_NUMBER);
    handOffMemberOrder(OTHER_ORDER);
    expect(memberOrderInView()).toBe(OTHER_ORDER);
    clearMemberOrder();
    expect(memberOrderInView()).toBeNull();
  });
});
