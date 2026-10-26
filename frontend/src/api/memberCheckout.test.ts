import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "../cart";
import {
  fetchMemberQuote,
  MEMBER_ORDER_URL,
  MEMBER_PHONE_URL,
  MEMBER_QUOTE_URL,
  memberOrderAttemptFor,
  memberOrderBody,
  memberQuoteBody,
  memberQuoteUrl,
  parseMemberPhone,
  readMemberPhone,
  runMemberOrder,
  submitMemberOrder,
} from "./memberCheckout";
import type { MemberOrderBody, MemberQuote } from "./memberCheckout";
import type { GuestRecipient } from "./orders";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const cart: CartLine[] = [
  { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
  { sku: "candle-std", slug: "soy-wax-candle", quantity: 1 },
];

const MEMBER_PHONE = "+60123456789";
const PHONE = "012-345 6789";
const COUPON = "WELCOME10";
const TOKEN = "csrf-token-1";
const KEY = "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60";
const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

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

const QUOTE: MemberQuote = {
  lines: [
    {
      sku: "tee-black-m",
      quantity: 2,
      status: "ok",
      available_stock: null,
      max_per_order: 5,
      product_slug: "crew-neck-tee",
      name: { text: "Crew Neck Tee", english_fallback: false },
      options: [],
      image: null,
      unit_price_sen: 3900,
      line_subtotal_sen: 7800,
    },
  ],
  subtotal_sen: 7800,
  shipping: { fee_sen: 1500, zone_code: "MY-WEST", version: 1 },
  total_sen: 8500,
  fx_reference: null,
  can_place_order: true,
  coupon_applied: true,
  coupon_discount_sen: 300,
  points_available: 1200,
  points_max: 1200,
  points_accepted: true,
  points_redeemed: 500,
  points_earned: 70,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

const SIGNED_OUT = () => json(401, { detail: "member_session_required" });
const PLACED = { order_number: ORDER_NUMBER, status: "awaiting_demo_payment", points_earned: 70 };

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

const noDelay = () => Promise.resolve();

describe("member quote", () => {
  // DESIGN 1.14「计价、优惠、积分与库存」第 1 条与 UX P05 第 3 步会员表单（优惠券、使用积分）：请求体是 checkoutQuoteBody 的结果另加 points，
  // 给了券代码才带 coupon_code；不带任何金额。
  it("adds points and the coupon code to the checkout quote body", () => {
    expect(memberQuoteBody(cart, { country_code: "MY", state_code: "MY-10" }, COUPON, 500)).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle-std", quantity: 1 },
      ],
      country_code: "MY",
      state_code: "MY-10",
      points: 500,
      coupon_code: COUPON,
    });
    const plain = memberQuoteBody(cart, null, null, 0);
    expect(plain).toEqual({ lines: [{ sku: "tee-black-m", quantity: 2 }, { sku: "candle-std", quantity: 1 }], points: 0 });
    expect(Object.keys(plain)).not.toContain("coupon_code");
    expect(JSON.stringify(plain)).not.toMatch(/price|sen|total|slug/i);
  });

  // SHOP-TASK-088 的接口记录（POST /api/member/checkout/quote，凭会员会话、只读、lang 同计价接口）与 SHOP-TASK-157 验收第 2 条
  // 「不带 CSRF 令牌与幂等键，接收 AbortSignal」：同源 cookie、不缓存、带 lang；取 200 的计价原样交给页面。
  it("posts with the session cookie and lang, without CSRF token or idempotency key", async () => {
    const fetchMock = stubFetch(json(200, QUOTE));
    const controller = new AbortController();
    const read = await fetchMemberQuote("ms", cart, { country_code: "GB", state_code: null }, COUPON, 500, controller.signal);
    expect(read).toEqual({ kind: "ok", quote: QUOTE });
    expect(urlOf(fetchMock)).toBe(`${MEMBER_QUOTE_URL}?lang=ms`);
    expect(memberQuoteUrl("zh")).toBe(`${MEMBER_QUOTE_URL}?lang=zh`);
    const init = initOf(fetchMock);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBe(controller.signal);
    expect(headersOf(fetchMock)).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
    expect(bodyOf(fetchMock)).toEqual(memberQuoteBody(cart, { country_code: "GB", state_code: null }, COUPON, 500));
    expect(urlOf(fetchMock)).not.toContain(COUPON);
  });

  // 同一条「结果归为 ok、signed_out、failed（其他，含 413、415、422、429 与 503）与 network」，401 为 member_session_required。
  it.each([
    [SIGNED_OUT(), "signed_out"],
    [json(413, { detail: "request body too large" }), "failed"],
    [json(415, { detail: "unsupported media type" }), "failed"],
    [json(422, { detail: "invalid country or state code" }), "failed"],
    [json(429, { detail: "rate_limited" }), "failed"],
    [json(503, { detail: "shipping is unavailable" }), "failed"],
    [json(201, QUOTE), "failed"],
    [text(200, "not json"), "failed"],
    [null, "network"],
  ] as const)("maps %o to %s", async (reply, expected) => {
    stubFetch(reply);
    await expect(fetchMemberQuote("en", cart, null, null, 0)).resolves.toEqual({ kind: expected });
  });
});

describe("member order request body", () => {
  // DESIGN 1.14「权限与资料保护」第 1、2 条（收货电话默认会员手机号、可改，服务端按收货国家补默认区号）与 SHOP-TASK-088 的 MemberOrderIn：
  // 字段恰好为 lines、phone（原文）、name、address、postal_code、country_code、region、coupon_code、points；没有 phone_region，不带金额。
  it("has exactly the member order fields and no phone region", () => {
    const body = memberOrderBody(cart, PHONE, london, COUPON, 500);
    expect(body).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle-std", quantity: 1 },
      ],
      phone: PHONE,
      name: "Sam Taylor",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
      country_code: "GB",
      region: "Greater London",
      coupon_code: COUPON,
      points: 500,
    });
    expect(Object.keys(body)).not.toContain("phone_region");
    expect(JSON.stringify(body)).not.toMatch(/price|sen|total|shipping|slug|sms/i);
  });

  // SHOP-TASK-157 验收第 3 条「马来西亚另有 state_code，其他国家的地区去掉首尾空白后非空时另有 region，coupon_code 只在调用方给出时带」。
  it("sends the state for Malaysia, leaves out an empty region and a missing coupon", () => {
    const malaysia = memberOrderBody(cart, MEMBER_PHONE, selangor, null, 0);
    expect(Object.keys(malaysia).sort()).toEqual(["address", "country_code", "lines", "name", "phone", "points", "postal_code", "state_code"]);
    expect(malaysia.state_code).toBe("MY-10");
    const blank = memberOrderBody(cart, MEMBER_PHONE, { ...london, region: "  " }, null, 0);
    expect(Object.keys(blank)).not.toContain("region");
    expect(Object.keys(blank)).not.toContain("state_code");
    expect(Object.keys(blank)).not.toContain("coupon_code");
  });
});

describe("member order idempotency key", () => {
  const body = memberOrderBody(cart, MEMBER_PHONE, london, COUPON, 500);
  const keys = () => {
    let count = 0;
    return () => {
      count += 1;
      return `key-${String(count)}-0000000000000`;
    };
  };

  // DESIGN 1.14「失败、并发与重试」第 1 条（相同键相同请求返回原结果，同键不同内容报冲突）与验收第 3 条「请求体不变时沿用同一个键，变了新生成」：
  // 改了电话、收货资料、券、积分或件数都换新。
  it("keeps the key for the same request and renews it after a change", () => {
    const next = keys();
    const first = memberOrderAttemptFor(null, body, next);
    expect(first.key).toBe("key-1-0000000000000");
    expect(memberOrderAttemptFor(first, memberOrderBody(cart, MEMBER_PHONE, london, COUPON, 500), next)).toBe(first);
    const changes: MemberOrderBody[] = [
      memberOrderBody(cart, "+60123456780", london, COUPON, 500),
      memberOrderBody(cart, MEMBER_PHONE, { ...london, address: "2 Example Street" }, COUPON, 500),
      memberOrderBody(cart, MEMBER_PHONE, london, null, 500),
      memberOrderBody(cart, MEMBER_PHONE, london, COUPON, 400),
      memberOrderBody([{ sku: "tee-black-m", slug: "crew-neck-tee", quantity: 3 }], MEMBER_PHONE, london, COUPON, 500),
    ];
    for (const changed of changes) {
      expect(memberOrderAttemptFor(first, changed, next).key).not.toBe(first.key);
    }
  });

  // 同一条（规则同 orders.ts 的 orderAttemptFor）：不给生成函数时用浏览器的 crypto.randomUUID。
  it("uses crypto.randomUUID by default", () => {
    vi.stubGlobal("crypto", { randomUUID: () => KEY });
    expect(memberOrderAttemptFor(null, body).key).toBe(KEY);
  });
});

describe("placing the member order", () => {
  const body = memberOrderBody(cart, MEMBER_PHONE, london, null, 0);

  // SHOP-TASK-088 的接口记录（POST /api/member/orders：会员会话、X-CSRF-Token、Idempotency-Key，新订单 201）与验收第 3 条：
  // 固定地址（不带查询参数）、POST、同源 cookie、不缓存；幂等键与令牌只在请求头；请求体就是 memberOrderBody 的结果，没有 phone_region 与 coupon_code。
  it("posts the body with the idempotency key and the CSRF token", async () => {
    const fetchMock = stubFetch(json(201, PLACED));
    await expect(submitMemberOrder(body, KEY, TOKEN)).resolves.toEqual({ kind: "placed", orderNumber: ORDER_NUMBER, pointsEarned: 70 });
    expect(urlOf(fetchMock)).toBe(MEMBER_ORDER_URL);
    const init = initOf(fetchMock);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(headersOf(fetchMock)).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
      [IDEMPOTENCY_HEADER]: KEY,
      [CSRF_HEADER]: TOKEN,
    });
    const sent = bodyOf(fetchMock);
    expect(sent).toEqual(body);
    expect(Object.keys(sent as Record<string, unknown>)).not.toContain("phone_region");
    expect(Object.keys(sent as Record<string, unknown>)).not.toContain("coupon_code");
    expect(JSON.stringify(sent)).not.toContain(KEY);
    expect(JSON.stringify(sent)).not.toContain(TOKEN);
  });

  // SHOP-TASK-088 的错误码（409 idempotency_conflict、order_not_placeable、coupon_invalid、points_unavailable；401；403 csrf_failed；
  // 422 phone_invalid 与无效目的地；503）与验收第 3 条的归类；201 与 200（重放）的响应体不合格时归 failed。
  it.each([
    [json(200, PLACED), { kind: "placed", orderNumber: ORDER_NUMBER, pointsEarned: 70 }],
    [json(201, { ...PLACED, points_earned: 0 }), { kind: "placed", orderNumber: ORDER_NUMBER, pointsEarned: 0 }],
    [json(201, { ...PLACED, order_number: "" }), { kind: "failed" }],
    [json(201, { ...PLACED, order_number: 7 }), { kind: "failed" }],
    [json(200, { ...PLACED, points_earned: -1 }), { kind: "failed" }],
    [json(200, { ...PLACED, points_earned: 1.5 }), { kind: "failed" }],
    [json(201, { order_number: ORDER_NUMBER }), { kind: "failed" }],
    [text(201, "not json"), { kind: "failed" }],
    [json(409, { detail: "order_not_placeable" }), { kind: "not_placeable" }],
    [json(409, { detail: "coupon_invalid" }), { kind: "coupon_invalid" }],
    [json(409, { detail: "points_unavailable" }), { kind: "points_unavailable" }],
    [json(409, { detail: "idempotency_conflict" }), { kind: "conflict" }],
    [json(409, { detail: "other" }), { kind: "failed" }],
    [SIGNED_OUT(), { kind: "signed_out" }],
    [json(403, { detail: "csrf_failed" }), { kind: "csrf" }],
    [json(403, { detail: "forbidden" }), { kind: "failed" }],
    [json(422, { detail: [{ type: "phone_invalid", loc: ["body", "phone"], msg: "invalid phone number" }] }), { kind: "phone_invalid" }],
    [json(422, { detail: "invalid country or state code" }), { kind: "failed" }],
    [json(422, { detail: [{ type: "missing", loc: ["body", "name"], msg: "Field required" }] }), { kind: "failed" }],
    [json(503, { detail: "service_unavailable" }), { kind: "unavailable" }],
    [json(500, {}), { kind: "failed" }],
    [null, { kind: "network" }],
  ] as const)("maps %o to %o", async (reply, expected) => {
    stubFetch(reply);
    await expect(submitMemberOrder(body, KEY, TOKEN)).resolves.toEqual(expected);
  });

  // DESIGN 1.14「失败、并发与重试」第 1 条与验收第 4 条「网络中断时隔 RECHECK_DELAY_MS 以同一幂等键与同一请求体重试，直到得到接口的回答」：
  // 每次中断都先通知页面；重放的 200 即成功。
  it("retries with the same key and body after the network drops", async () => {
    const fetchMock = stubFetch(null, null, json(200, PLACED));
    const onChecking = vi.fn();
    const delay = vi.fn(noDelay);
    await expect(runMemberOrder(body, KEY, TOKEN, { delay, onChecking })).resolves.toEqual({ kind: "placed", orderNumber: ORDER_NUMBER, pointsEarned: 70 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (let index = 0; index < 3; index += 1) {
      expect(headersOf(fetchMock, index)[IDEMPOTENCY_HEADER]).toBe(KEY);
      expect(headersOf(fetchMock, index)[CSRF_HEADER]).toBe(TOKEN);
      expect(initOf(fetchMock, index).body).toBe(initOf(fetchMock).body);
    }
    expect(onChecking).toHaveBeenCalledTimes(2);
    expect(delay).toHaveBeenCalledWith(RECHECK_DELAY_MS, undefined);
  });

  // 同一条「页面已离开（中止）时返回 null」：中止后不再重试，也不交出结果。
  it("stops retrying after the page is left", async () => {
    const fetchMock = stubFetch(null, json(201, PLACED));
    const controller = new AbortController();
    const delay = () => {
      controller.abort();
      return Promise.resolve();
    };
    await expect(runMemberOrder(body, KEY, TOKEN, { signal: controller.signal, delay })).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 同一条「直到得到接口的回答」：得到回答后不再重试，拒绝原样交给页面。
  it("returns the first answer", async () => {
    const fetchMock = stubFetch(json(409, { detail: "coupon_invalid" }));
    await expect(runMemberOrder(body, KEY, TOKEN, { delay: noDelay })).resolves.toEqual({ kind: "coupon_invalid" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("reading the member phone", () => {
  // DESIGN 1.14「权限与资料保护」第 8 条（只认会员会话、GET、不要求 CSRF）与 SHOP-TASK-156 的接口记录：
  // 同源 cookie、不缓存的 GET，不带查询参数、CSRF 令牌与请求体，接收 AbortSignal；号码原样交给调用方。
  it("gets the phone without query parameters or CSRF token", async () => {
    const fetchMock = stubFetch(json(200, { phone: MEMBER_PHONE }));
    const controller = new AbortController();
    await expect(readMemberPhone(controller.signal)).resolves.toEqual({ kind: "ok", phone: MEMBER_PHONE });
    expect(urlOf(fetchMock)).toBe(MEMBER_PHONE_URL);
    const init = initOf(fetchMock);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBe(controller.signal);
    expect(init.body).toBeUndefined();
    expect(headersOf(fetchMock)).toEqual({ Accept: "application/json" });
  });

  // 验收第 5 条「结果归为 ok、signed_out（401）、failed（其他，含 200 而响应体不合格）与 network」。
  it.each([
    [SIGNED_OUT(), "signed_out"],
    [json(200, { phone: "0123456789" }), "failed"],
    [json(200, { phone: "" }), "failed"],
    [json(200, {}), "failed"],
    [text(200, "not json"), "failed"],
    [json(503, { detail: "service_unavailable" }), "failed"],
    [json(202, { phone: MEMBER_PHONE }), "failed"],
    [null, "network"],
  ] as const)("maps %o to %s", async (reply, expected) => {
    stubFetch(reply);
    await expect(readMemberPhone()).resolves.toEqual({ kind: expected });
  });

  // 同一条「响应体的检查写成导出的纯函数」：phone 为以加号开头的非空字符串才取。
  it("checks the reply body", () => {
    expect(parseMemberPhone({ phone: MEMBER_PHONE })).toBe(MEMBER_PHONE);
    for (const body of [null, "x", [], {}, { phone: 60123456789 }, { phone: "" }, { phone: "+" }, { phone: "60123456789" }]) {
      expect(parseMemberPhone(body)).toBeNull();
    }
  });
});

describe("browser storage and addresses", () => {
  // DESIGN 1.14「权限与资料保护」第 8 条与 HANDOFF 0.63「本人号码只返回给调用方，模块不缓存、不写任何浏览器存储」，
  // 以及验收第 1 条「电话、收货资料、券代码与 CSRF 令牌只在内存、请求体与请求头里，不进地址或浏览器存储」：
  // 取号、计价与下单（含网络中断后的重试）全程不写 localStorage、sessionStorage 与 cookie，请求地址里没有这些值；
  // 每次取号都重新请求（不缓存）。
  it("keeps the phone, the details, the coupon and the token out of addresses and browser storage", async () => {
    // 浏览器存储与 cookie 以替身代替（测试在 node 环境运行）；任何一次写入都会被记下。
    const storageWrite = vi.fn((key: string, value: string) => {
      void key;
      void value;
    });
    const cookieWrites: string[] = [];
    const doc = {};
    Object.defineProperty(doc, "cookie", {
      get: () => "",
      set: (value: string) => {
        cookieWrites.push(value);
      },
    });
    vi.stubGlobal("localStorage", { setItem: storageWrite });
    vi.stubGlobal("sessionStorage", { setItem: storageWrite });
    vi.stubGlobal("document", doc);

    const fetchMock = stubFetch(json(200, { phone: MEMBER_PHONE }), json(200, { phone: MEMBER_PHONE }), json(200, QUOTE), null, json(201, PLACED));
    await readMemberPhone();
    await readMemberPhone();
    await fetchMemberQuote("en", cart, { country_code: "GB", state_code: null }, COUPON, 500);
    const body = memberOrderBody(cart, MEMBER_PHONE, london, COUPON, 500);
    await runMemberOrder(body, memberOrderAttemptFor(null, body, () => KEY).key, TOKEN, { delay: noDelay });

    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(storageWrite).not.toHaveBeenCalled();
    expect(cookieWrites).toEqual([]);
    for (const call of fetchMock.mock.calls) {
      const url = String(call[0]);
      for (const secret of [MEMBER_PHONE, "60123456789", london.name, london.address, london.postal_code, COUPON, TOKEN, KEY]) {
        expect(url).not.toContain(secret);
      }
    }
  });
});
