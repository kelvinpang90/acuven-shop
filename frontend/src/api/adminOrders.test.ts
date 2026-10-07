import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_ORDERS_QUERY_URL,
  advanceAdminOrder,
  MAX_ORDER_ID,
  ORDER_STATUSES,
  parseOrderId,
  queryAdminOrders,
  readAdminOrder,
} from "./adminOrders";
import type { AdminOrderDetail, OrdersQuery } from "./adminOrders";
import { CSRF_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "T4LW-6NQB";
const ROW = {
  id: 42,
  order_number: ORDER_NUMBER,
  created_at: "2026-09-10T03:15:00Z",
  status: "demo_shipped",
  total_sen: 7640,
  refunds_pending: 0,
};
const LIST = { total: 1, page: 1, page_size: 20, orders: [ROW] };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：给出一个回答（函数则抛出网络错误），记录请求。
function stubFetch(reply: Response | (() => never)) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      return typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

describe("querying the admin order list", () => {
  // SHOP-TASK-048 验收第 2 条「调用 POST /api/admin/orders/query（JSON 请求体只有 order_number、status 与 page；同源 cookie，cache 为 no-store）」：
  // 地址固定，凭同源 cookie、不缓存；请求体恰好是这三个字段（没有筛选时为 null），并带上中止用的 signal。
  it.each<OrdersQuery>([
    { order_number: null, status: null, page: 1 },
    { order_number: ` ${ORDER_NUMBER} `, status: "demo_paid", page: 3 },
  ])("posts only the order number, the status and the page (%#)", async (query) => {
    const calls = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await queryAdminOrders(query, controller.signal);
    expect(ADMIN_ORDERS_QUERY_URL).toBe("/api/admin/orders/query");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(ADMIN_ORDERS_QUERY_URL);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(body(calls[0])).toEqual({ order_number: query.order_number, status: query.status, page: query.page });
    expect(Object.keys(body(calls[0]) as object).sort()).toEqual(["order_number", "page", "status"]);
  });

  // 同一条「订单号只在请求体里，不进网址、查询参数或浏览器存储」（docs/UX.md A02 [K]，DESIGN「权限与资料保护」）：
  // 地址里没有订单号，查询过程不写 localStorage、sessionStorage、cookie 或历史记录。
  it("keeps the order number out of the address and browser storage", async () => {
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

    const calls = stubFetch(json(200, LIST));
    await queryAdminOrders({ order_number: ORDER_NUMBER, status: null, page: 1 });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain("?");
    expect(calls[0]?.init.body as string).toContain(ORDER_NUMBER);
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  // 同一条「结果归为 ok（只取 total、page、page_size 与每行的 id、order_number、created_at、status、total_sen、refunds_pending）」：
  // 响应里多出的字段（含行里的）不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(json(200, { ...LIST, extra: "ignored", orders: [{ ...ROW, recipient: { name: "Aminah" } }] }));
    await expect(queryAdminOrders({ order_number: null, status: null, page: 1 })).resolves.toEqual({ kind: "ok", list: LIST });
  });

  // 同一条「响应体不合格算失败」与「none（401）、failed 与 network」：401 为没有会话；其他状态、不合格的 200 响应体与网络中断分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "forbidden" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(200, { page: 1, page_size: 20, orders: [] }), "failed"],
    [json(200, { ...LIST, total: -1 }), "failed"],
    [json(200, { ...LIST, page: 0 }), "failed"],
    [json(200, { ...LIST, page_size: 0 }), "failed"],
    [json(200, { ...LIST, orders: null }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, id: "42" }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, order_number: 7 }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, created_at: "yesterday" }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, status: "shipped" }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, total_sen: 76.4 }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, total_sen: -1 }] }), "failed"],
    [json(200, { ...LIST, orders: [{ ...ROW, refunds_pending: null }] }), "failed"],
    [json(200, { ...LIST, orders: [ROW, "row"] }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(queryAdminOrders({ order_number: null, status: null, page: 1 })).resolves.toEqual({ kind });
  });

  // docs/UX.md A02「状态筛选选项为各 [order.status_*]」与 DESIGN「订单与退款状态」：六种订单状态，每种都是合格的行状态。
  it.each(ORDER_STATUSES)("accepts the order status %s", async (status) => {
    stubFetch(json(200, { ...LIST, orders: [{ ...ROW, status }] }));
    const read = await queryAdminOrders({ order_number: null, status, page: 1 });
    expect(read).toEqual({ kind: "ok", list: { ...LIST, orders: [{ ...ROW, status }] } });
    expect(ORDER_STATUSES).toHaveLength(6);
  });
});

const TOKEN = "csrf-token-for-admin";
const RECIPIENT = {
  name: "Aina Rahman",
  phone: "+60 12-345 6789",
  country_code: "MY",
  region: "MY-10",
  address: "12 Jalan Contoh 3, Taman Demo",
  postal_code: "47000",
};
const DETAIL: AdminOrderDetail = {
  id: 42,
  order_number: ORDER_NUMBER,
  status: "demo_paid",
  created_at: "2026-09-30T06:02:00Z",
  paid_at: "2026-09-30T06:05:00Z",
  lines: [
    { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 4000, line_subtotal_sen: 6737, unit_cash_paid_sen: [3368, 3369] },
    { name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3000, line_subtotal_sen: 2763, unit_cash_paid_sen: [2763] },
  ],
  subtotal_sen: 11000,
  coupon_discount_sen: 1000,
  points_discount_sen: 500,
  shipping_fee_sen: 800,
  total_sen: 10300,
  recipient: RECIPIENT,
  events: [
    { created_at: "2026-09-30T06:02:00Z", status: "awaiting_demo_payment", actor_type: "guest" },
    { created_at: "2026-09-30T06:05:00Z", status: "demo_paid", actor_type: "guest" },
  ],
  refunds_pending: 0,
  fully_refunded: false,
  csrf_token: TOKEN,
};

describe("parsing the order ID in the path", () => {
  // 验收第 3 条「路径里的 ID 不是不带符号与前导零的正整数或大于 2147483647（与后端 app/api/admin_orders.py 的 MAX_ORDER_ID 相同）时不发请求」。
  it("accepts only positive decimal integers up to the backend limit", () => {
    expect(MAX_ORDER_ID).toBe(2147483647);
    expect(parseOrderId("1")).toBe(1);
    expect(parseOrderId("42")).toBe(42);
    expect(parseOrderId("2147483647")).toBe(2147483647);
    for (const bad of ["0", "-1", "+1", "01", "007", "2147483648", "99999999999999999999999", "1.0", "1e3", "0x10", " 1", "1 ", "", "abc", "４２", ":id"]) {
      expect(parseOrderId(bad), bad).toBeNull();
    }
  });
});

describe("reading the order detail", () => {
  // 验收第 3 条「读取详情（GET /api/admin/orders/{内部 ID}，lang 为当前界面语言）…同源 cookie、cache 为 no-store」：
  // 地址只有内部 ID 与 lang，方法 GET，凭同源 cookie、不缓存，带上中止用的 signal；没有请求体。
  it.each(["en", "zh", "ms"] as const)("gets the detail by internal ID in %s", async (language) => {
    const calls = stubFetch(json(200, DETAIL));
    const controller = new AbortController();
    await expect(readAdminOrder("42", language, controller.signal)).resolves.toEqual({ kind: "ok", order: DETAIL });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/orders/42?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(calls[0]?.init.body).toBeUndefined();
  });

  // 验收第 3 条「详情只取 SHOP-TASK-037 记录段列出的字段」：响应里多出的字段（含商品行、收货资料与事件里的）不带进结果；
  // 没有收货资料记录（recipient 为 null）、未支付（paid_at 为 null，逐件实付为空）与非马来西亚地址的空地区也合格。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...DETAIL,
        extra: "ignored",
        lines: DETAIL.lines.map((line) => ({ ...line, sku: "TEE-BLK-M" })),
        recipient: { ...RECIPIENT, email: "x@example.com" },
        events: DETAIL.events.map((event) => ({ ...event, id: 7, note: "n" })),
      }),
    );
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order: DETAIL });
    const unpaid = {
      ...DETAIL,
      status: "awaiting_demo_payment",
      paid_at: null,
      lines: DETAIL.lines.map((line) => ({ ...line, unit_cash_paid_sen: [] })),
      recipient: null,
    };
    stubFetch(json(200, unpaid));
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order: unpaid });
    const abroad = { ...DETAIL, recipient: { ...RECIPIENT, country_code: "SG", region: null } };
    stubFetch(json(200, abroad));
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order: abroad });
  });

  // 验收第 3 条「事件的 actor_type 只接受 guest、member、admin 与 system 四种」。
  it.each(["guest", "member", "admin", "system"])("accepts the actor type %s", async (actor) => {
    const order = { ...DETAIL, events: [{ ...DETAIL.events[0], actor_type: actor }] };
    stubFetch(json(200, order));
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order });
  });

  // 验收第 3 条「响应体不合格算失败（时间字段沿用本文件 readRow 对 created_at 的写法…事件的 actor_type…其他值算不合格）」，
  // 以及 401 用路由进入 /admin/login、404 为订单不存在（Kelvin 2026-10-07）所需的归类：401 为 none、404 为 missing、
  // 其他状态与不合格的 200 为 failed，网络中断为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "missing"],
    [json(403, {}), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(200, null), "failed"],
    [json(200, { ...DETAIL, id: 0 }), "failed"],
    [json(200, { ...DETAIL, id: "42" }), "failed"],
    [json(200, { ...DETAIL, order_number: null }), "failed"],
    [json(200, { ...DETAIL, status: "paid" }), "failed"],
    [json(200, { ...DETAIL, created_at: "yesterday" }), "failed"],
    [json(200, { ...DETAIL, created_at: 1727676120 }), "failed"],
    [json(200, { ...DETAIL, paid_at: "soon" }), "failed"],
    [json(200, { ...DETAIL, lines: null }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], quantity: 0 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], variant_label: null }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], line_subtotal_sen: 67.37 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], unit_cash_paid_sen: [3368, -1] }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], unit_cash_paid_sen: null }] }), "failed"],
    [json(200, { ...DETAIL, coupon_discount_sen: -100 }), "failed"],
    [json(200, { ...DETAIL, points_discount_sen: "500" }), "failed"],
    [json(200, { ...DETAIL, shipping_fee_sen: null }), "failed"],
    [json(200, { ...DETAIL, total_sen: 103.5 }), "failed"],
    [json(200, { ...DETAIL, recipient: "Aina" }), "failed"],
    [json(200, { ...DETAIL, recipient: { ...RECIPIENT, phone: 60123 } }), "failed"],
    [json(200, { ...DETAIL, recipient: { ...RECIPIENT, region: 10 } }), "failed"],
    [json(200, { ...DETAIL, events: [{ ...DETAIL.events[0], actor_type: "customer" }] }), "failed"],
    [json(200, { ...DETAIL, events: [{ ...DETAIL.events[0], actor_type: "ADMIN" }] }), "failed"],
    [json(200, { ...DETAIL, events: [{ ...DETAIL.events[0], status: "requested" }] }), "failed"],
    [json(200, { ...DETAIL, events: [{ ...DETAIL.events[0], created_at: "later" }] }), "failed"],
    [json(200, { ...DETAIL, events: "none" }), "failed"],
    [json(200, { ...DETAIL, refunds_pending: -1 }), "failed"],
    [json(200, { ...DETAIL, fully_refunded: "false" }), "failed"],
    [json(200, { ...DETAIL, csrf_token: null }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind });
  });

  // 验收第 3 条「路径里的 ID 不是…时不发请求」与 Kelvin 2026-10-07「网址里的内部 ID 不存在或不合法时显示 common.error_retry 与 common.back」：
  // 不合法的 ID 不发任何请求，与 404 同样归为 missing。
  it.each(["0", "01", "-5", "2147483648", "abc", ":id"])("sends no request for the ID %j", async (id) => {
    const calls = stubFetch(json(200, DETAIL));
    await expect(readAdminOrder(id, "en")).resolves.toEqual({ kind: "missing" });
    expect(calls).toEqual([]);
  });
});

describe("advancing the order", () => {
  // 验收第 3 条「推进（POST /api/admin/orders/{内部 ID}/status，JSON 请求体只有 status，请求头 X-CSRF-Token 取详情返回的令牌，
  // 沿用 api/pay.ts 的请求头名），同源 cookie、cache 为 no-store」。
  it.each(["demo_packed", "demo_shipped"] as const)("posts only the target status %s with the CSRF token", async (status) => {
    const calls = stubFetch(json(200, { status }));
    await expect(advanceAdminOrder("42", status, TOKEN)).resolves.toBe("done");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/orders/42/status");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(body(calls[0])).toEqual({ status });
    expect(Object.keys(body(calls[0]) as object)).toEqual(["status"]);
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(headers[CSRF_HEADER]).toBe(TOKEN);
    expect(headers["Content-Type"]).toBe("application/json");
  });

  // 验收第 5 条「200 后重新取详情；409（order_not_advanceable 或 fulfilment_frozen）与 403 重新取详情…；网络中断…；其他失败…；401…」
  // 所需的归类：200 为 done，两种 409 为 conflict，别的 409 与其他状态为 failed，403 为 csrf，401 为 none，网络中断为 network。
  it.each<[Response | (() => never), string]>([
    [json(200, { status: "demo_packed" }), "done"],
    [json(409, { detail: "order_not_advanceable", status: "demo_shipped" }), "conflict"],
    [json(409, { detail: "fulfilment_frozen", status: "demo_paid" }), "conflict"],
    [json(409, { detail: "something_else" }), "failed"],
    [new Response("not json", { status: 409 }), "failed"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(advanceAdminOrder("42", "demo_packed", TOKEN)).resolves.toBe(kind);
  });

  // 验收第 3 条「路径里的 ID 不是…时不发请求」。
  it.each(["0", "007", "2147483648", "x"])("sends no request for the ID %j", async (id) => {
    const calls = stubFetch(json(200, { status: "demo_packed" }));
    await expect(advanceAdminOrder(id, "demo_packed", TOKEN)).resolves.toBe("failed");
    expect(calls).toEqual([]);
  });
});

describe("keeping personal data out of addresses, storage and logs", () => {
  // 验收第 3 条「订单号、收货资料与令牌只在页面内存与请求里，不进网址、浏览器存储或日志」（DESIGN 1.11「权限与资料保护」，UX A02 [K] 与 [P1]）：
  // 读取与推进的地址只有内部 ID 与语言，不含订单号、收货资料或令牌；令牌只在请求头里；
  // 过程中不写 localStorage、sessionStorage、cookie 或历史记录，也不写控制台。
  it("keeps the order number, the recipient and the token in memory and requests only", async () => {
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
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));

    const readCalls = stubFetch(json(200, DETAIL));
    await readAdminOrder("42", "en");
    const advanceCalls = stubFetch(json(200, { status: "demo_packed" }));
    await advanceAdminOrder("42", "demo_packed", TOKEN);
    for (const call of [...readCalls, ...advanceCalls]) {
      for (const secret of [ORDER_NUMBER, TOKEN, RECIPIENT.name, RECIPIENT.phone, RECIPIENT.address, RECIPIENT.postal_code]) {
        expect(call.url).not.toContain(secret);
        expect(call.url).not.toContain(encodeURIComponent(secret));
      }
    }
    expect(advanceCalls[0]?.init.body as string).not.toContain(TOKEN);
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    for (const log of logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });
});
