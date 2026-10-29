import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_SHIPPING_RATES_URL, readAdminShippingRates, saveAdminShippingRates } from "./adminShippingRates";
import type { AdminRatesSave } from "./adminShippingRates";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 整页数据（SHOP-TASK-171 记录的字段，马来西亚一组按 SHOP-TASK-172 为 malaysia 的两档，西马在前）。
const MY_W = { id: 1, version: 1, zone_code: "MY-W", fee_sen: 800 };
const MY_E = { id: 2, version: 3, zone_code: "MY-E", fee_sen: 1500 };
const OTHER = { id: 3, version: 1, zone_code: "OTHER", fee_sen: 3000 };
const SG = { id: 4, version: 2, zone_code: "SG", fee_sen: 2000 };
const SGD = { id: 1, version: 1, country_code: "SG", currency_code: "SGD", currency_decimals: 2, rate: "0.31" };

const RATES = { malaysia: [MY_W, MY_E], other: OTHER, countries: [SG], fx: [SGD] };
const PAGE = { ...RATES, csrf_token: "csrf-abc" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不是本接口时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || String(input) !== ADMIN_SHIPPING_RATES_URL) {
      return Promise.reject(new Error("unexpected request"));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function sentUrl(fetchMock: FetchMock, index: number): string {
  return String(fetchMock.mock.calls[index]?.[0]);
}

function sentInit(fetchMock: FetchMock, index: number): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

function sentBody(fetchMock: FetchMock, index: number): unknown {
  const body = sentInit(fetchMock, index).body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return JSON.parse(body) as unknown;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 读取与保存 200 共用的不合格响应体：在合格的整页上换掉一项（值为 undefined 时 JSON 里没有这一项）。
const MALFORMED_RATES: [string, Record<string, unknown>][] = [
  // HANDOFF 0.72 第（3）点「马来西亚行的区域代码只收 `MY-W` 与 `MY-E`」与 SHOP-TASK-172 记录「新增……`MY_WEST_ZONE_CODE = "MY-W"`、
  // `MY_EAST_ZONE_CODE = "MY-E"`」：州属代码、小写、兜底代码与同一档出现两次都不合格。
  ["malaysia missing", { malaysia: undefined }],
  ["malaysia not an array", { malaysia: MY_W }],
  ["malaysia with a state code", { malaysia: [{ ...MY_W, zone_code: "MY-01" }, MY_E] }],
  ["malaysia code lower case", { malaysia: [{ ...MY_W, zone_code: "my-w" }, MY_E] }],
  ["malaysia code OTHER", { malaysia: [MY_W, { ...MY_E, zone_code: "OTHER" }] }],
  ["malaysia code repeated", { malaysia: [MY_W, { ...MY_E, zone_code: "MY-W" }] }],
  ["malaysia row null", { malaysia: [MY_W, null] }],
  // DESIGN 1.17 第 9 条「读取时每行带行 ID 与版本号」：行 ID 与版本号缺少、为 0、为小数或字符串都不合格。
  ["malaysia id zero", { malaysia: [{ ...MY_W, id: 0 }, MY_E] }],
  ["malaysia id as string", { malaysia: [{ ...MY_W, id: "1" }, MY_E] }],
  ["malaysia version zero", { malaysia: [{ ...MY_W, version: 0 }, MY_E] }],
  ["malaysia version fractional", { malaysia: [{ ...MY_W, version: 1.5 }, MY_E] }],
  // DESIGN 1.17 第 9 条「运费以整仙提交，0 到 999,999 仙」：1000000、小数、负数与字符串都不合格。
  ["malaysia fee 1000000", { malaysia: [{ ...MY_W, fee_sen: 1_000_000 }, MY_E] }],
  ["malaysia fee fractional", { malaysia: [{ ...MY_W, fee_sen: 800.5 }, MY_E] }],
  ["malaysia fee negative", { malaysia: [{ ...MY_W, fee_sen: -1 }, MY_E] }],
  ["malaysia fee as string", { malaysia: [{ ...MY_W, fee_sen: "800" }, MY_E] }],
  ["malaysia fee missing", { malaysia: [{ ...MY_W, fee_sen: undefined }, MY_E] }],
  // HANDOFF 0.72 第（3）点「请求模块读取时兜底行为 null 算失败（迁移 0005 已写入兜底行，保存必须带它）」与 SHOP-TASK-171 记录「`other`
  // 为一个对象」：null、缺少、数组、代码不是 OTHER、字段不合格都不合格。
  ["other null", { other: null }],
  ["other missing", { other: undefined }],
  ["other as an array", { other: [OTHER] }],
  ["other code MY-W", { other: { ...OTHER, zone_code: "MY-W" } }],
  ["other code lower case", { other: { ...OTHER, zone_code: "other" } }],
  ["other fee 1000000", { other: { ...OTHER, fee_sen: 1_000_000 } }],
  ["other id zero", { other: { ...OTHER, id: 0 } }],
  // DESIGN 1.17 第 9 条「新增运费或汇率的国家须在结账页的收货国家列表里（即 `phonenumbers` 支持的两位地区代码），且不是马来西亚」：
  // 国家行的代码为 MY、小写、三位或 OTHER 都不合格，运费与版本号同上。
  ["countries missing", { countries: undefined }],
  ["countries not an array", { countries: SG }],
  ["countries code MY", { countries: [{ ...SG, zone_code: "MY" }] }],
  ["countries code lower case", { countries: [{ ...SG, zone_code: "sg" }] }],
  ["countries code three letters", { countries: [{ ...SG, zone_code: "SGP" }] }],
  ["countries code OTHER", { countries: [{ ...SG, zone_code: "OTHER" }] }],
  ["countries fee 1000000", { countries: [{ ...SG, fee_sen: 1_000_000 }] }],
  ["countries fee fractional", { countries: [{ ...SG, fee_sen: 0.5 }] }],
  ["countries version missing", { countries: [{ ...SG, version: undefined }] }],
  // DESIGN 1.17 第 9 条「币种代码（三位大写字母……）与小数位（0–3 的整数）」与「汇率……以十进制字符串提交」：国家为 MY、币种为两位或小写、
  // 小数位为 4、负数、小数或字符串、汇率为空串或数字都不合格。
  ["fx missing", { fx: undefined }],
  ["fx not an array", { fx: SGD }],
  ["fx id zero", { fx: [{ ...SGD, id: 0 }] }],
  ["fx version as string", { fx: [{ ...SGD, version: "1" }] }],
  ["fx country MY", { fx: [{ ...SGD, country_code: "MY" }] }],
  ["fx country lower case", { fx: [{ ...SGD, country_code: "sg" }] }],
  ["fx currency two letters", { fx: [{ ...SGD, currency_code: "SG" }] }],
  ["fx currency lower case", { fx: [{ ...SGD, currency_code: "sgd" }] }],
  ["fx currency four letters", { fx: [{ ...SGD, currency_code: "SGDX" }] }],
  ["fx decimals 4", { fx: [{ ...SGD, currency_decimals: 4 }] }],
  ["fx decimals negative", { fx: [{ ...SGD, currency_decimals: -1 }] }],
  ["fx decimals fractional", { fx: [{ ...SGD, currency_decimals: 1.5 }] }],
  ["fx decimals as string", { fx: [{ ...SGD, currency_decimals: "2" }] }],
  ["fx rate empty", { fx: [{ ...SGD, rate: "" }] }],
  ["fx rate as number", { fx: [{ ...SGD, rate: 0.31 }] }],
  ["fx rate missing", { fx: [{ ...SGD, rate: undefined }] }],
];

describe("reading the shipping rates", () => {
  // SHOP-TASK-171 记录「`GET /api/admin/shipping-rates`：`require_admin`……后返回 `load_admin_rates` 的整页数据与由 cookie 算出的 CSRF 令牌」：
  // 地址为固定路径、不带查询参数、GET、凭同源 cookie、不缓存、Accept 为 JSON、没有请求体与 CSRF 头，并带上调用方的 signal。
  it("gets the page with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, PAGE));
    const controller = new AbortController();
    await readAdminShippingRates(controller.signal);
    expect(ADMIN_SHIPPING_RATES_URL).toBe("/api/admin/shipping-rates");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/shipping-rates");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-172 记录「读取响应……这一组的字段改名为 `malaysia`（字段顺序 `malaysia`、`other`、`countries`、`fx`，读取另带 `csrf_token`）」：
  // 整页数据与 CSRF 令牌带进结果，顶层与行上多出的字段（如旧的 states、zone_type）不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...PAGE,
        states: [],
        updated_at: "2026-10-29",
        malaysia: [{ ...MY_W, zone_type: "my_region" }, MY_E],
        other: { ...OTHER, zone_type: "other" },
        countries: [{ ...SG, name: "Singapore" }],
        fx: [{ ...SGD, symbol: "S$" }],
      }),
    );
    await expect(readAdminShippingRates()).resolves.toEqual({ kind: "ok", rates: RATES, csrf_token: "csrf-abc" });
  });

  // DESIGN 1.17 第 9 条「运费以整仙提交，0 到 999,999 仙（RM0.00–RM9,999.99），0 即该区免运费」与「小数位（0–3 的整数）」：两端都合格；
  // 国家运费与汇率可以一行都没有。
  it("accepts the edges of the ranges and empty groups", async () => {
    const rates = {
      malaysia: [
        { ...MY_W, fee_sen: 0 },
        { ...MY_E, fee_sen: 999_999 },
      ],
      other: { ...OTHER, fee_sen: 0 },
      countries: [],
      fx: [
        { ...SGD, currency_decimals: 0 },
        { ...SGD, id: 2, country_code: "ID", currency_code: "IDR", currency_decimals: 3, rate: "3500" },
      ],
    };
    stubFetch(json(200, { ...rates, csrf_token: "t" }));
    await expect(readAdminShippingRates()).resolves.toEqual({ kind: "ok", rates, csrf_token: "t" });
  });

  // 各例守住的 HANDOFF 0.72、SHOP-TASK-171、172 记录与 DESIGN 1.17 第 9 条原句见 MALFORMED_RATES 各组的注释：任一项不合格，整个响应体归为 failed。
  it.each(MALFORMED_RATES)("treats a malformed page as a failure: %s", async (_name, change) => {
    stubFetch(json(200, { ...PAGE, ...change }));
    await expect(readAdminShippingRates()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-171 记录「由 cookie 算出的 CSRF 令牌」（保存要带它）：csrf_token 缺少、为空、为数字或 null 都不合格；整体不是对象也不合格。
  it.each<[string, unknown]>([
    ["csrf token missing", { ...PAGE, csrf_token: undefined }],
    ["csrf token empty", { ...PAGE, csrf_token: "" }],
    ["csrf token as number", { ...PAGE, csrf_token: 1 }],
    ["csrf token null", { ...PAGE, csrf_token: null }],
    ["not an object", [PAGE]],
    ["null body", null],
  ])("treats a malformed page as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminShippingRates()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-171 记录「返回 `load_admin_rates` 的整页数据与由 cookie 算出的 CSRF 令牌」「`other` 为一个对象」：200 的响应体须为这样一个
  // JSON 对象，不是 JSON 时读不出整页与令牌，算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readAdminShippingRates()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-171 记录「没有有效后台会话时 401 `admin_session_required`」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(422, { detail: "rates_invalid" }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminShippingRates()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-171、172 记录、DESIGN 1.17 第 9 条与 HANDOFF 0.72 都没有关于中止的原句（记录的偏离一节已列明）；守住的是本任务验收第 1 条
  // 「调用方可传入 AbortSignal，中止时按现有模块的做法归为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, PAGE));
    controller.abort();
    await expect(readAdminShippingRates(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// 保存内容：各种行都带一个多出的属性（变量先成形再放进去，绕开字面量的多余属性检查，模拟调用方从读取结果改出来的对象）。
const keepMyW = { id: 1, version: 1, fee_sen: 900, zone_code: "MY-W" };
const keepMyE = { id: 2, version: 3, fee_sen: 1500, zone_code: "MY-E" };
const keepOther = { id: 3, version: 1, fee_sen: 3000, zone_code: "OTHER" };
const keepSg = { id: 4, version: 2, fee_sen: 2100, zone_code: "SG" };
const removeTh = { id: 5, version: 1, delete: true as const, fee_sen: 1800 };
const addNz = { country_code: "NZ", fee_sen: 2500, name: "New Zealand" };
const keepSgd = { id: 1, version: 1, rate: "0.32", currency_code: "SGD", currency_decimals: 2 };
const removeThb = { id: 2, version: 4, delete: true as const, rate: "7.5" };
const addIdr = { country_code: "ID", currency_code: "IDR", currency_decimals: 0, rate: "3500", id: 9, version: 1 };

const pageWithExtras = {
  malaysia: [keepMyW, keepMyE],
  other: keepOther,
  countries: [keepSg, removeTh, addNz],
  fx: [keepSgd, removeThb, addIdr],
  states: [],
  csrf_token: "csrf-abc",
};
const SAVE: AdminRatesSave = pageWithExtras;

describe("saving the shipping rates", () => {
  // SHOP-TASK-171 记录「`PUT /api/admin/shipping-rates`」与检查顺序「4. 403 `csrf_failed`」、「2. 415 `request body must be JSON`」：
  // 地址为固定路径、不带查询参数、PUT、凭同源 cookie、不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("puts the page with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, RATES));
    const controller = new AbortController();
    await saveAdminShippingRates(SAVE, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/shipping-rates");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-171 记录「四项……都必填」「州属与兜底的行只接受 `{id, version, fee_sen}`」「国家运费的行三选一：已有行 `{id, version, fee_sen}`、
  // 删除 `{id, version, delete: true}`……、新增 `{country_code, fee_sen}`」「汇率的行三选一：已有行 `{id, version, rate}`、删除
  // `{id, version, delete: true}`、新增 `{country_code, currency_code, currency_decimals, rate}`」「多出字段……422」，SHOP-TASK-172 记录
  // 「请求体仍带 `states` 时由 `extra="forbid"` 照多出字段回 422」与 DESIGN 1.17 第 9 条「新增的行不带行 ID 与版本号……已有汇率行只给新汇率，
  // 不给币种与小数位」：请求体恰为四项，各行按种类只有规定的字段，删除行的 delete 为 true，调用方对象上多出的属性（含 states 与 csrf_token）不进请求体。
  it("sends only the four groups and the fields of each kind of row", async () => {
    const fetchMock = stubFetch(json(200, RATES));
    await saveAdminShippingRates(SAVE, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(["malaysia", "other", "countries", "fx"]);
    expect(body).toEqual({
      malaysia: [
        { id: 1, version: 1, fee_sen: 900 },
        { id: 2, version: 3, fee_sen: 1500 },
      ],
      other: { id: 3, version: 1, fee_sen: 3000 },
      countries: [
        { id: 4, version: 2, fee_sen: 2100 },
        { id: 5, version: 1, delete: true },
        { country_code: "NZ", fee_sen: 2500 },
      ],
      fx: [
        { id: 1, version: 1, rate: "0.32" },
        { id: 2, version: 4, delete: true },
        { country_code: "ID", currency_code: "IDR", currency_decimals: 0, rate: "3500" },
      ],
    });
  });

  // DESIGN 1.17 第 9 条「马来西亚与兜底的行只有保留」与 SHOP-TASK-171 记录「四项……都必填」：国家运费与汇率都没有行时照样发出四项。
  it("sends empty groups as empty arrays", async () => {
    const fetchMock = stubFetch(json(200, RATES));
    await saveAdminShippingRates({ malaysia: [], other: { id: 3, version: 1, fee_sen: 0 }, countries: [], fx: [] }, "csrf-abc");
    expect(sentBody(fetchMock, 0)).toEqual({ malaysia: [], other: { id: 3, version: 1, fee_sen: 0 }, countries: [], fx: [] });
  });

  // SHOP-TASK-171 记录「成功回 200 与 `save_admin_rates` 返回的保存后整页数据，字段同 GET、不含 `csrf_token`」：结果带保存后的整页（以响应为准），
  // 多出的字段（含 csrf_token）不带进结果。
  it("returns the saved page only", async () => {
    stubFetch(json(200, { ...RATES, csrf_token: "x", countries: [{ ...SG, zone_type: "country" }], audit: [] }));
    await expect(saveAdminShippingRates(SAVE, "csrf-abc")).resolves.toEqual({ kind: "ok", rates: RATES });
  });

  // SHOP-TASK-171 记录「成功回 200 与 `save_admin_rates` 返回的保存后整页数据，字段同 GET、不含 `csrf_token`」：没有 csrf_token 照样合格（上一条），
  // 字段同 GET，所以不合格的例子与读取相同（各例守住的设计原句见 MALFORMED_RATES 的注释）。
  it.each(MALFORMED_RATES)("treats a malformed saved page as a failure: %s", async (_name, change) => {
    stubFetch(json(200, { ...RATES, ...change }));
    await expect(saveAdminShippingRates(SAVE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-171 记录「成功回 200 与 `save_admin_rates` 返回的保存后整页数据，字段同 GET」：200 的响应体须为带这些字段的 JSON 对象，
  // 整体不是对象、为 null 或不是 JSON 时读不出保存后的整页，算 failed。
  it.each<[string, Response]>([
    ["not an object", json(200, [RATES])],
    ["null body", json(200, null)],
    ["not JSON", new Response("", { status: 200 })],
  ])("treats a malformed saved page as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(saveAdminShippingRates(SAVE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-171 记录的检查顺序「1. 413」「2. 415」「3. 401 `admin_session_required`」「4. 403 `csrf_failed`」「5. 422 请求体」
  // 「6. 服务层 `RatesInvalid`：422 `{"detail":"rates_invalid"}`」「7. 服务层 `RatesChanged`：409 `{"detail":"rates_changed"}`」：
  // 只有 detail 恰为 rates_invalid 的 422 为 invalid、恰为 rates_changed 的 409 为 changed；detail 对调、大小写不同、请求体 422 的错误列表、
  // 不是 JSON、其他状态带同样的 detail 都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "rates_invalid" }), "invalid"],
    [json(409, { detail: "rates_changed" }), "changed"],
    [json(422, { detail: "rates_changed" }), "failed"],
    [json(409, { detail: "rates_invalid" }), "failed"],
    [json(422, { detail: "RATES_INVALID" }), "failed"],
    [json(409, { detail: "RATES_CHANGED" }), "failed"],
    [json(422, { detail: [{ loc: ["body", "other"], type: "model_type", msg: "invalid" }] }), "failed"],
    [json(422, {}), "failed"],
    [json(409, {}), "failed"],
    [new Response("rates_invalid", { status: 422 }), "failed"],
    [new Response("rates_changed", { status: 409 }), "failed"],
    [json(413, { detail: "request body too large" }), "failed"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(500, { detail: "rates_invalid" }), "failed"],
    [json(400, { detail: "rates_changed" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(saveAdminShippingRates(SAVE, "csrf-abc")).resolves.toEqual({ kind });
  });

  // SHOP-TASK-171、172 记录、DESIGN 1.17 第 9 条与 HANDOFF 0.72 都没有关于中止的原句（记录的偏离一节已列明）；守住的是本任务验收第 1 条
  // 「调用方可传入 AbortSignal，中止时按现有模块的做法归为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, RATES));
    controller.abort();
    await expect(saveAdminShippingRates(SAVE, "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
