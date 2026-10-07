import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_ORDERS_QUERY_URL, ORDER_STATUSES, queryAdminOrders } from "./adminOrders";
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
