import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "../cart";
import { GUEST_ORDER_URL, IDEMPOTENCY_HEADER, guestOrderBody, orderAttemptFor, runGuestOrder, submitGuestOrder } from "./orders";
import type { GuestOrderBody, GuestRecipient } from "./orders";
import { RECHECK_DELAY_MS } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
});

const cart: CartLine[] = [
  { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
  { sku: "candle-std", slug: "soy-wax-candle", quantity: 1 },
];

const PHONE = "+44 7700 900123";
const KEY = "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60";

const london: GuestRecipient = {
  name: "Sam Taylor",
  address: "1 Example Street",
  postal_code: "AB1 2CD",
  country_code: "GB",
  state_code: null,
  region: "Greater London",
};

const selangor: GuestRecipient = {
  name: "Aina Rahman",
  address: "12 Jalan Contoh 3",
  postal_code: "47000",
  country_code: "MY",
  state_code: "MY-10",
  region: null,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | "network")[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const next = queue.shift();
      if (next === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      return next === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

const noDelay = () => Promise.resolve();

describe("guest order request body", () => {
  // SHOP-TASK-025 验收第 8 条「请求体只含购物车行（SKU 与件数）、第 1 步号码原文与所选地区代码、收货资料、国家、州属或地区（字段名以 SHOP-TASK-020 的请求模型为准），不带任何金额」：
  // 其他国家：字段恰好为 GuestOrderIn 的 lines、phone、phone_region、name、address、postal_code、country_code、region；号码是输入原文，不改写。
  it("has exactly the fields of the order model for another country", () => {
    const body = guestOrderBody(cart, { input: PHONE, region: "MY" }, london);
    expect(body).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle-std", quantity: 1 },
      ],
      phone: PHONE,
      phone_region: "MY",
      name: "Sam Taylor",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
      country_code: "GB",
      region: "Greater London",
    });
    expect(JSON.stringify(body)).not.toMatch(/price|sen|total|shipping|fx|amount|coupon|points|member|sms|slug/i);
  });

  // 同一条（「州属或地区」与 SHOP-TASK-020「马来西亚必填 state_code，其他国家不给」）：马来西亚带州属代码、不带地区；其他国家地区为空时不带。
  it("sends the state for Malaysia and leaves out an empty region", () => {
    const malaysia = guestOrderBody(cart, { input: "12-345 6789", region: "MY" }, selangor);
    expect(Object.keys(malaysia).sort()).toEqual(["address", "country_code", "lines", "name", "phone", "phone_region", "postal_code", "state_code"]);
    expect(malaysia.state_code).toBe("MY-10");
    const blank = guestOrderBody(cart, { input: PHONE, region: "GB" }, { ...london, region: "  " });
    expect(Object.keys(blank)).not.toContain("region");
    expect(Object.keys(blank)).not.toContain("state_code");
  });
});

describe("idempotency key", () => {
  const body = guestOrderBody(cart, { input: PHONE, region: "GB" }, london);
  const keys = () => {
    let count = 0;
    return () => {
      count += 1;
      return `key-${String(count)}-0000000000000`;
    };
  };

  // SHOP-TASK-025 验收第 8 条「Idempotency-Key 在提交时用 crypto.randomUUID 生成，表单或购物车改动后换新」：首次提交生成；请求体不变时沿用；改了收货资料、电话或件数都换新。
  it("keeps the key for the same request and renews it after a change", () => {
    const next = keys();
    const first = orderAttemptFor(null, body, next);
    expect(first.key).toBe("key-1-0000000000000");
    expect(orderAttemptFor(first, guestOrderBody(cart, { input: PHONE, region: "GB" }, london), next)).toBe(first);
    const changes: GuestOrderBody[] = [
      guestOrderBody(cart, { input: PHONE, region: "GB" }, { ...london, address: "2 Example Street" }),
      guestOrderBody(cart, { input: "+44 7700 900124", region: "GB" }, london),
      guestOrderBody(
        [
          { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 3 },
          { sku: "candle-std", slug: "soy-wax-candle", quantity: 1 },
        ],
        { input: PHONE, region: "GB" },
        london,
      ),
    ];
    for (const changed of changes) {
      expect(orderAttemptFor(first, changed, next).key).not.toBe(first.key);
    }
  });

  // 同一条「用 crypto.randomUUID 生成」：不给生成函数时用浏览器的 crypto.randomUUID。
  it("uses crypto.randomUUID by default", () => {
    vi.stubGlobal("crypto", { randomUUID: () => KEY });
    expect(orderAttemptFor(null, body).key).toBe(KEY);
  });
});

describe("placing the order", () => {
  const body = guestOrderBody(cart, { input: PHONE, region: "GB" }, london);

  // SHOP-TASK-025 验收第 8 条「调用 SHOP-TASK-020 的 POST /api/orders/guest…不带任何金额；Idempotency-Key…」：固定地址（不含电话或资料）、POST、同源 cookie（接收订单访问凭据）、不缓存，
  // 幂等键只在请求头，请求体就是 guestOrderBody 的结果。
  it("posts the body with the idempotency key header", async () => {
    const calls = stubFetch(json(201, {}));
    await expect(submitGuestOrder(body, KEY)).resolves.toBe("placed");
    const [call] = calls;
    expect(call?.url).toBe(GUEST_ORDER_URL);
    expect(call?.init.method).toBe("POST");
    expect(call?.init.credentials).toBe("same-origin");
    expect(call?.init.cache).toBe("no-store");
    expect(headers(call)[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(JSON.parse(call?.init.body as string)).toEqual(body);
    expect(call?.init.body).not.toContain(KEY);
  });

  // SHOP-TASK-025 验收第 9 条各错误码：201、200 为成功；409 两种、403、422 phone_invalid、503 各有其回答；其余（含其他 403、409、422 与 500）为一般失败。
  it.each([
    [json(201, {}), "placed"],
    [json(200, {}), "placed"],
    [json(409, { detail: "order_not_placeable" }), "not_placeable"],
    [json(403, { detail: "sms_verification_required" }), "sms_required"],
    [json(422, { detail: [{ type: "phone_invalid", loc: ["body", "phone"], msg: "invalid phone number" }] }), "phone_invalid"],
    [json(409, { detail: "idempotency_conflict" }), "conflict"],
    [json(503, { detail: "shipping is unavailable" }), "unavailable"],
    [json(422, { detail: [{ type: "missing", loc: ["body", "name"], msg: "Field required" }] }), "failed"],
    [json(403, { detail: "forbidden" }), "failed"],
    [json(409, { detail: "other" }), "failed"],
    [json(500, {}), "failed"],
    [new Response("not json", { status: 409 }), "failed"],
    ["network", "network"],
  ] as const)("maps %o to %s", async (reply, expected) => {
    stubFetch(reply);
    await expect(submitGuestOrder(body, KEY)).resolves.toBe(expected);
  });

  // SHOP-TASK-025 验收第 9 条「网络中断时显示 common.network_check，并以同一幂等键重试（重放即查询原订单，不会重复下单）」：
  // 中断后先通知页面，隔 RECHECK_DELAY_MS 再以同一键、同一请求体重试，直到得到回答；重放的 200 即成功。
  it("retries with the same key after the network drops", async () => {
    const calls = stubFetch("network", "network", json(200, {}));
    const onChecking = vi.fn();
    const delay = vi.fn(noDelay);
    await expect(runGuestOrder(body, KEY, { delay, onChecking })).resolves.toBe("placed");
    expect(calls).toHaveLength(3);
    for (const call of calls) {
      expect(headers(call)[IDEMPOTENCY_HEADER]).toBe(KEY);
      expect(call.init.body).toBe(calls[0]?.init.body);
    }
    expect(onChecking).toHaveBeenCalledTimes(2);
    expect(delay).toHaveBeenCalledWith(RECHECK_DELAY_MS, undefined);
  });

  // 派生实现约束（实现选择）：守住同一条——离开页面（中止）后不再重试，也不交出结果。
  it("stops retrying after the page is left", async () => {
    const calls = stubFetch("network", json(201, {}));
    const controller = new AbortController();
    const delay = () => {
      controller.abort();
      return Promise.resolve();
    };
    await expect(runGuestOrder(body, KEY, { signal: controller.signal, delay })).resolves.toBeNull();
    expect(calls).toHaveLength(1);
  });

  // 同一条：得到回答后不再重试，一般失败原样交给页面（显示 common.error_retry）。
  it("returns the first answer", async () => {
    const calls = stubFetch(json(500, {}));
    await expect(runGuestOrder(body, KEY, { delay: noDelay })).resolves.toBe("failed");
    expect(calls).toHaveLength(1);
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-025 验收第 2 条「收货资料与电话只保存在页面内存，不写进网址、localStorage、sessionStorage 或 cookie」：
  // 下单（含网络中断后的重试）全程不写任何浏览器存储、cookie 或历史记录，请求地址里没有电话或收货资料。
  it("keeps the phone and the shipping details out of addresses and browser storage", async () => {
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

    const calls = stubFetch("network", json(201, {}));
    const body = guestOrderBody(cart, { input: PHONE, region: "GB" }, london);
    await runGuestOrder(body, orderAttemptFor(null, body, () => KEY).key, { delay: noDelay });

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    for (const call of calls) {
      expect(call.url).toBe(GUEST_ORDER_URL);
    }
  });
});
