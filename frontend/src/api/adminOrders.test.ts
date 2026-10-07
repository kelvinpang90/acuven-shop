import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ACTOR_TYPES,
  ADMIN_ORDERS_QUERY_URL,
  adminOrderStatusUrl,
  adminOrderUrl,
  advanceAdminOrder,
  MAX_ORDER_ID,
  ORDER_STATUSES,
  orderIdOf,
  queryAdminOrders,
  readAdminOrder,
} from "./adminOrders";
import type { OrdersQuery } from "./adminOrders";

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

// localStorage、sessionStorage、document.cookie 与 history 的替身：记录全部写入。
function stubBrowserStorage() {
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
  return () => {
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  };
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
  // （SHOP-TASK-055：浏览器存储替身移到文件内共用的 stubBrowserStorage，断言不变。）
  it("keeps the order number out of the address and browser storage", async () => {
    const checkStorage = stubBrowserStorage();
    const calls = stubFetch(json(200, LIST));
    await queryAdminOrders({ order_number: ORDER_NUMBER, status: null, page: 1 });
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    expect(calls[0]?.url).not.toContain("?");
    expect(calls[0]?.init.body as string).toContain(ORDER_NUMBER);
    checkStorage();
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

const ORDER_ID = 42;
const TOKEN = "csrf-token-for-cookie";
const RECIPIENT = {
  name: "Aina Rahman",
  phone: "+60123456789",
  country_code: "MY",
  region: "MY-10",
  address: "12 Jalan Contoh 3, Taman Demo",
  postal_code: "47000",
};
// SHOP-TASK-037 记录段列出的详情字段（CSRF 令牌另放）。
const DETAIL = {
  id: ORDER_ID,
  order_number: ORDER_NUMBER,
  status: "demo_paid",
  created_at: "2026-09-30T06:02:00Z",
  paid_at: "2026-09-30T06:05:00Z",
  lines: [
    { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 4000, line_subtotal_sen: 6737, unit_cash_paid_sen: [3369, 3368] },
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
};
const DETAIL_BODY = { ...DETAIL, csrf_token: TOKEN };

describe("reading an admin order", () => {
  // SHOP-TASK-055 验收第 3 条「读取详情（GET /api/admin/orders/{内部 ID}，lang 为当前界面语言）…同源 cookie、cache 为 no-store」：
  // 地址只含内部 ID 与语言，方法 GET，凭同源 cookie、不缓存，并带上中止用的 signal；没有请求体。
  it.each(["en", "zh", "ms"] as const)("gets the order by its internal ID in %s", async (language) => {
    const calls = stubFetch(json(200, DETAIL_BODY));
    const controller = new AbortController();
    await readAdminOrder(String(ORDER_ID), language, controller.signal);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/orders/42?lang=${language}`);
    expect(adminOrderUrl(ORDER_ID, language)).toBe(`/api/admin/orders/42?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(calls[0]?.init.body).toBeUndefined();
  });

  // 同一条「详情只取 SHOP-TASK-037 记录段列出的字段」：响应里多出的字段（含行、收货资料与事件里的）不带进结果，CSRF 令牌另放。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...DETAIL_BODY,
        extra: "ignored",
        lines: DETAIL.lines.map((line) => ({ ...line, sku: "TEE-BLK-M" })),
        recipient: { ...RECIPIENT, email: "a@example.com" },
        events: DETAIL.events.map((event) => ({ ...event, actor_id: 7 })),
      }),
    );
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order: DETAIL, csrfToken: TOKEN });
  });

  // SHOP-TASK-037「recipient…没有收货资料记录时为 null」「paid_at（未支付为 null）」「region」可为空：这几处为 null 时照样合格。
  it("accepts null recipient, payment time and region", async () => {
    const body = { ...DETAIL_BODY, status: "awaiting_demo_payment", paid_at: null, recipient: null };
    stubFetch(json(200, body));
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind: "ok", order: { ...DETAIL, status: "awaiting_demo_payment", paid_at: null, recipient: null }, csrfToken: TOKEN });
    stubFetch(json(200, { ...DETAIL_BODY, recipient: { ...RECIPIENT, country_code: "SG", region: null } }));
    const read = await readAdminOrder("42", "en");
    expect(read.kind === "ok" && read.order.recipient).toEqual({ ...RECIPIENT, country_code: "SG", region: null });
  });

  // UX A02 [admin.actor_*] 与服务端的操作者类别（guest、member、admin、system）：四种都合格。
  it.each(ACTOR_TYPES)("accepts the actor type %s", async (actor) => {
    stubFetch(json(200, { ...DETAIL_BODY, events: [{ ...DETAIL.events[0], actor_type: actor }] }));
    const read = await readAdminOrder("42", "en");
    expect(read.kind).toBe("ok");
  });

  // 同一条「响应体不合格算失败」与 Kelvin 2026-10-07「打开不存在的订单…显示 common.error_retry 与 common.back」：
  // 401 为没有会话，404 为订单不存在；其他状态、不合格的 200 响应体与网络中断分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "missing"],
    [json(403, { detail: "forbidden" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(200, null), "failed"],
    [json(200, { ...DETAIL_BODY, id: 43 }), "failed"],
    [json(200, { ...DETAIL_BODY, id: "42" }), "failed"],
    [json(200, { ...DETAIL_BODY, order_number: 7 }), "failed"],
    [json(200, { ...DETAIL_BODY, status: "shipped" }), "failed"],
    [json(200, { ...DETAIL_BODY, created_at: "yesterday" }), "failed"],
    [json(200, { ...DETAIL_BODY, paid_at: "soon" }), "failed"],
    [json(200, { ...DETAIL_BODY, lines: null }), "failed"],
    [json(200, { ...DETAIL_BODY, lines: [{ ...DETAIL.lines[0], quantity: 0 }] }), "failed"],
    [json(200, { ...DETAIL_BODY, lines: [{ ...DETAIL.lines[0], name: null }] }), "failed"],
    [json(200, { ...DETAIL_BODY, lines: [{ ...DETAIL.lines[0], line_subtotal_sen: 67.37 }] }), "failed"],
    [json(200, { ...DETAIL_BODY, lines: [{ ...DETAIL.lines[0], unit_cash_paid_sen: [-1] }] }), "failed"],
    [json(200, { ...DETAIL_BODY, subtotal_sen: -1 }), "failed"],
    [json(200, { ...DETAIL_BODY, coupon_discount_sen: "10" }), "failed"],
    [json(200, { ...DETAIL_BODY, points_discount_sen: null }), "failed"],
    [json(200, { ...DETAIL_BODY, shipping_fee_sen: 8.5 }), "failed"],
    [json(200, { ...DETAIL_BODY, total_sen: undefined }), "failed"],
    [json(200, { ...DETAIL_BODY, recipient: { ...RECIPIENT, phone: 60123456789 } }), "failed"],
    [json(200, { ...DETAIL_BODY, recipient: "Aina" }), "failed"],
    [json(200, { ...DETAIL_BODY, events: [{ ...DETAIL.events[0], actor_type: "robot" }] }), "failed"],
    [json(200, { ...DETAIL_BODY, events: [{ ...DETAIL.events[0], status: "refunded" }] }), "failed"],
    [json(200, { ...DETAIL_BODY, events: [{ ...DETAIL.events[0], created_at: 0 }] }), "failed"],
    [json(200, { ...DETAIL_BODY, refunds_pending: -1 }), "failed"],
    [json(200, { ...DETAIL_BODY, fully_refunded: "false" }), "failed"],
    [json(200, { ...DETAIL_BODY, csrf_token: "" }), "failed"],
    [json(200, DETAIL), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminOrder("42", "en")).resolves.toEqual({ kind });
  });

  // 同一条「路径里的 ID 不是不带符号与前导零的正整数时不发请求」与 Kelvin 2026-10-07「网址里的内部 ID…不合法时」显示同不存在：
  // 不发请求，按订单不存在报告；上限与服务端相同（2147483647）。
  it.each(["0", "-1", "+1", "042", "1.5", "1e3", "abc", " 42", "42 ", "", ":id", "２", "2147483648", "99999999999999999999"])(
    "sends nothing for the order ID %j",
    async (raw) => {
      const calls = stubFetch(json(200, DETAIL_BODY));
      expect(orderIdOf(raw)).toBeNull();
      await expect(readAdminOrder(raw, "en")).resolves.toEqual({ kind: "missing" });
      await expect(advanceAdminOrder(raw, "demo_packed", TOKEN)).resolves.toBe("failed");
      expect(calls).toEqual([]);
    },
  );

  // 同一条：合格的 ID 原样取整数（1 与上限 2147483647 都合格）。
  it("accepts plain positive order IDs", () => {
    expect(orderIdOf("1")).toBe(1);
    expect(orderIdOf("42")).toBe(42);
    expect(orderIdOf(String(MAX_ORDER_ID))).toBe(2147483647);
  });

  // 同一条「订单号、收货资料与令牌只在页面内存与请求里，不进网址、浏览器存储或日志」（DESIGN 1.11「权限与资料保护」）：
  // 地址里只有内部 ID 与语言，读取过程不写浏览器存储、cookie 或历史记录，也不写控制台。
  it("keeps the order number, the recipient and the token out of the address, browser storage and logs", async () => {
    const checkStorage = stubBrowserStorage();
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((name) => vi.spyOn(console, name).mockImplementation(() => undefined));
    const calls = stubFetch(json(200, DETAIL_BODY));
    await readAdminOrder("42", "en");
    for (const secret of [ORDER_NUMBER, RECIPIENT.name, RECIPIENT.phone, TOKEN]) {
      expect(calls[0]?.url).not.toContain(secret);
    }
    checkStorage();
    for (const log of logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });
});

describe("advancing an admin order", () => {
  // SHOP-TASK-055 验收第 3 条「推进（POST /api/admin/orders/{内部 ID}/status，JSON 请求体只有 status，请求头 X-CSRF-Token
  // 取详情返回的令牌，沿用 api/pay.ts 的请求头名），同源 cookie、cache 为 no-store」。
  it.each(["demo_packed", "demo_shipped"] as const)("posts only the target status %s with the CSRF header", async (status) => {
    const calls = stubFetch(json(200, { status }));
    const controller = new AbortController();
    await expect(advanceAdminOrder("42", status, TOKEN, controller.signal)).resolves.toBe("done");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/orders/42/status");
    expect(adminOrderStatusUrl(ORDER_ID)).toBe("/api/admin/orders/42/status");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe(TOKEN);
    expect(headers["Content-Type"]).toBe("application/json");
    expect(body(calls[0])).toEqual({ status });
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // 同一条与验收第 5 条「200 后重新取详情；409（order_not_advanceable 或 fulfilment_frozen）与 403 重新取详情并显示 common.error_retry；
  // 网络中断显示 common.network_check…其他失败 common.error_retry；401…进入 /admin/login」：各回答的归类。
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

  // 同一条「令牌只在页面内存与请求里，不进网址、浏览器存储或日志」：推进不写浏览器存储、cookie、历史记录或控制台。
  it("keeps the token out of browser storage and logs", async () => {
    const checkStorage = stubBrowserStorage();
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((name) => vi.spyOn(console, name).mockImplementation(() => undefined));
    stubFetch(json(200, { status: "demo_packed" }));
    await advanceAdminOrder("42", "demo_packed", TOKEN);
    checkStorage();
    for (const log of logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });
});
