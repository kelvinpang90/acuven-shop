import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CONFIRM_RECEIPT_URL,
  LOOKUP_URL,
  lookupOrdersUrl,
  readLookupOrder,
  receiptKey,
  recheckOrder,
  runConfirmReceipt,
  submitConfirmReceipt,
  submitLookup,
} from "./orderLookup";
import type { LookupOrder } from "./orderLookup";
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
    lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 3900, line_subtotal_sen: 7800, unit_cash_paid_sen: [3900, 3900] }],
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
    ...overrides,
  };
}

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
});
