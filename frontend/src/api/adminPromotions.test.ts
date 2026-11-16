import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_PROMOTIONS_URL,
  MAX_PROMOTION_ID,
  copyAdminPromotion,
  createAdminPromotion,
  deleteAdminPromotion,
  endAdminPromotion,
  publishAdminPromotion,
  readAdminPromotion,
  readAdminPromotions,
  saveAdminPromotion,
  withdrawAdminPromotion,
} from "./adminPromotions";
import type { PromotionDelete, PromotionInput, PromotionSave, PromotionWrite } from "./adminPromotions";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 文案（CopyIn 的 15 个字段，字符串或 null）。
const COPY = {
  title_en: "Raya Sale",
  title_zh: "开斋节特卖",
  title_ms: "Jualan Raya",
  subtitle_en: "Up to 20% off",
  subtitle_zh: null,
  subtitle_ms: null,
  badge_en: "-20%",
  badge_zh: null,
  badge_ms: null,
  strip_en: "Raya deals now on",
  strip_zh: null,
  strip_ms: null,
  poster_alt_en: "Raya poster",
  poster_alt_zh: null,
  poster_alt_ms: null,
};

// 装饰（DecorationIn 的 9 个字段，都为字符串）。
const DECORATION = {
  pack: "raya",
  palette: "raya",
  ambient: "none",
  home_poster: "v1",
  list_strip: "v2",
  card_badge: "v1",
  detail_ribbon: "v1",
  cart_progress: "v1",
  poster_mode: "image",
};

// RuleOut 的一条商品级规则与一条满额规则。
const ITEM_RULE = {
  rule_id: 11,
  kind: "item_discount",
  discount_mode: "percent",
  percent: 20,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [5],
  category_ids: [2, 3],
  tiers: [],
  products: [{ id: 5, slug: "kopi", name_en: "Kopi" }],
};

const TIER_RULE = {
  rule_id: 12,
  kind: "spend_discount",
  discount_mode: "amount",
  percent: null,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [],
  category_ids: [],
  tiers: [
    { threshold_sen: 10000, amount_sen: 1000, percent: null },
    { threshold_sen: 20000, amount_sen: 2500, percent: null },
  ],
  products: [],
};

// PromotionOut 的全部字段。
const PROMOTION = {
  id: 7,
  version: 3,
  state: "scheduled",
  ended_early_at: null,
  name: "Raya 2026",
  starts_at: "2026-03-20T00:00",
  ends_at: "2026-04-05T23:59",
  coupon_combinable: true,
  points_combinable: false,
  rules: [ITEM_RULE, TIER_RULE],
  decoration: DECORATION,
  copy: COPY,
  poster_desktop_url: "/api/promotion-posters/7-desktop.webp",
  poster_phone_url: null,
};

// PromotionSummaryOut 的两项。
const RUNNING = { id: 9, name: "Merdeka", state: "running", starts_at: "2026-08-25T00:00", ends_at: "2026-09-01T23:59", rule_count: 2, updated_at: "2026-08-20T03:04:05.123456" };
const DRAFT = { id: 4, name: "Deepavali", state: "draft", starts_at: null, ends_at: null, rule_count: 0, updated_at: "2026-10-01T00:00:00" };

const LIST = { promotions: [RUNNING, DRAFT], csrf_token: "csrf-abc" };
const PAGE = { ...PROMOTION, csrf_token: "csrf-abc" };

// 新建的内容：规则带上读回的 rule_id 与 products，以检查它们不带进请求体。
const INPUT: PromotionInput = {
  name: "Raya 2026",
  starts_at: "2026-03-20T00:00",
  ends_at: null,
  coupon_combinable: true,
  points_combinable: false,
  rules: [ITEM_RULE, TIER_RULE],
  decoration: DECORATION,
  copy: COPY,
};

const INPUT_BODY = {
  name: "Raya 2026",
  starts_at: "2026-03-20T00:00",
  ends_at: null,
  coupon_combinable: true,
  points_combinable: false,
  rules: [
    {
      kind: "item_discount",
      discount_mode: "percent",
      percent: 20,
      amount_sen: null,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
      threshold_sen: null,
      product_ids: [5],
      category_ids: [2, 3],
      tiers: [],
    },
    {
      kind: "spend_discount",
      discount_mode: "amount",
      percent: null,
      amount_sen: null,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
      threshold_sen: null,
      product_ids: [],
      category_ids: [],
      tiers: [
        { threshold_sen: 10000, amount_sen: 1000, percent: null },
        { threshold_sen: 20000, amount_sen: 2500, percent: null },
      ],
    },
  ],
  decoration: DECORATION,
  copy: COPY,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不在本接口下时拒绝。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    const url = String(input);
    if (next === undefined || (url !== ADMIN_PROMOTIONS_URL && !url.startsWith(`${ADMIN_PROMOTIONS_URL}/`))) {
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

function sentHeaders(fetchMock: FetchMock, index: number): Record<string, string> {
  return Object.fromEntries(new Headers(sentInit(fetchMock, index).headers).entries());
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

// 在 base 上换掉一项（值为 undefined 时 JSON 里没有这一项）。
function changed(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  return { ...base, ...patch };
}

// 读取一个与写操作共用的不合格活动。
const MALFORMED_PROMOTIONS: [string, Record<string, unknown>][] = [
  // 文档字符串「响应：PromotionDetail 与 PromotionSummary 的同名字段」（PromotionOut 的 id: int、version: int）：缺少、0、小数、字符串都不合格。
  ["id missing", changed(PROMOTION, { id: undefined })],
  ["id zero", changed(PROMOTION, { id: 0 })],
  ["id as string", changed(PROMOTION, { id: "7" })],
  ["version missing", changed(PROMOTION, { version: undefined })],
  ["version fractional", changed(PROMOTION, { version: 1.5 })],
  ["version negative", changed(PROMOTION, { version: -1 })],
  // UX 0.20 A12「状态值 [admin.promo_draft] 草稿、[admin.promo_scheduled] 已发布未开始、[admin.promo_running] 进行中、[admin.promo_ended] 已结束」：只认四个值。
  ["state missing", changed(PROMOTION, { state: undefined })],
  ["state unknown", changed(PROMOTION, { state: "paused" })],
  ["state upper case", changed(PROMOTION, { state: "DRAFT" })],
  ["state null", changed(PROMOTION, { state: null })],
  // 文档字符串「时间以马来西亚本地「YYYY-MM-DDTHH:MM」字符串或 null 收发」：时间缺少或为数字都不合格。
  ["starts_at missing", changed(PROMOTION, { starts_at: undefined })],
  ["ends_at as number", changed(PROMOTION, { ends_at: 1 })],
  ["ended_early_at missing", changed(PROMOTION, { ended_early_at: undefined })],
  ["name missing", changed(PROMOTION, { name: undefined })],
  ["name null", changed(PROMOTION, { name: null })],
  ["coupon_combinable as string", changed(PROMOTION, { coupon_combinable: "true" })],
  ["points_combinable missing", changed(PROMOTION, { points_combinable: undefined })],
  ["poster_desktop_url missing", changed(PROMOTION, { poster_desktop_url: undefined })],
  ["poster_phone_url as number", changed(PROMOTION, { poster_phone_url: 0 })],
  // RuleOut 的 rule_id、参数、product_ids、category_ids、tiers 与 products：任一项缺少或类型不对都不合格。
  ["rules missing", changed(PROMOTION, { rules: undefined })],
  ["rules not an array", changed(PROMOTION, { rules: {} })],
  ["rule not an object", changed(PROMOTION, { rules: [null] })],
  ["rule_id missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { rule_id: undefined })] })],
  ["kind null", changed(PROMOTION, { rules: [changed(ITEM_RULE, { kind: null })] })],
  ["discount_mode missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { discount_mode: undefined })] })],
  ["percent missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { percent: undefined })] })],
  ["percent as string", changed(PROMOTION, { rules: [changed(ITEM_RULE, { percent: "20" })] })],
  ["get_percent fractional", changed(PROMOTION, { rules: [changed(ITEM_RULE, { get_percent: 0.5 })] })],
  ["threshold_sen missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { threshold_sen: undefined })] })],
  ["product_ids missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { product_ids: undefined })] })],
  ["product_ids with zero", changed(PROMOTION, { rules: [changed(ITEM_RULE, { product_ids: [0] })] })],
  ["category_ids with string", changed(PROMOTION, { rules: [changed(ITEM_RULE, { category_ids: ["2"] })] })],
  ["tiers missing", changed(PROMOTION, { rules: [changed(TIER_RULE, { tiers: undefined })] })],
  ["tier without percent", changed(PROMOTION, { rules: [changed(TIER_RULE, { tiers: [{ threshold_sen: 10000, amount_sen: 1000 }] })] })],
  ["tier threshold null", changed(PROMOTION, { rules: [changed(TIER_RULE, { tiers: [{ threshold_sen: null, amount_sen: 1000, percent: null }] })] })],
  ["products missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { products: undefined })] })],
  ["product without slug", changed(PROMOTION, { rules: [changed(ITEM_RULE, { products: [{ id: 5, name_en: "Kopi" }] })] })],
  ["product name_en missing", changed(PROMOTION, { rules: [changed(ITEM_RULE, { products: [{ id: 5, slug: "kopi" }] })] })],
  // DecorationIn 的 9 个字段都为字符串。
  ["decoration missing", changed(PROMOTION, { decoration: undefined })],
  ["decoration without pack", changed(PROMOTION, { decoration: changed(DECORATION, { pack: undefined }) })],
  ["decoration poster_mode null", changed(PROMOTION, { decoration: changed(DECORATION, { poster_mode: null }) })],
  // CopyIn 的 15 个字段，字符串或 null。
  ["copy missing", changed(PROMOTION, { copy: undefined })],
  ["copy without poster_alt_ms", changed(PROMOTION, { copy: changed(COPY, { poster_alt_ms: undefined }) })],
  ["copy title_en as number", changed(PROMOTION, { copy: changed(COPY, { title_en: 1 }) })],
];

describe("reading the list", () => {
  // 文档字符串「列表（GET）：require_admin（401）→ promotions（list_promotions 的全部项）与由 cookie 算出的 CSRF 令牌」：固定路径、GET、同源 cookie、
  // 不缓存、请求头只有 Accept、没有请求体，并带上调用方的 signal。
  it("gets the list with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await readAdminPromotions(controller.signal);
    expect(ADMIN_PROMOTIONS_URL).toBe("/api/admin/promotions");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json" });
    expect(init.signal).toBe(controller.signal);
  });

  // UX 0.20 A12「列表按进行中、未开始……、草稿……、已结束……排列」由服务端排好：按接口顺序带进结果，多出的字段不带进；时间原样带回。
  it("keeps the order and only the listed fields", async () => {
    stubFetch(json(200, { ...LIST, extra: 1, promotions: [{ ...RUNNING, secret: "x" }, DRAFT] }));
    expect(await readAdminPromotions()).toEqual({ kind: "ok", promotions: [RUNNING, DRAFT], csrf_token: "csrf-abc" });
  });

  // UX 0.20 A12「空：[admin.promo_empty]」：空列表合格。
  it("accepts an empty list", async () => {
    stubFetch(json(200, { promotions: [], csrf_token: "t" }));
    expect(await readAdminPromotions()).toEqual({ kind: "ok", promotions: [], csrf_token: "t" });
  });

  // 文档字符串「全部接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required」。
  it("reports no session on 401", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }));
    expect(await readAdminPromotions()).toEqual({ kind: "none" });
  });

  // 文档字符串「列表（GET）……」只有 200 与 401：其他状态都是失败。
  it.each([403, 404, 422, 500, 503])("reports %d as failed", async (status) => {
    stubFetch(json(status, { detail: "x" }));
    expect(await readAdminPromotions()).toEqual({ kind: "failed" });
  });

  // 文档字符串「PromotionListOut：promotions……csrf_token」与 PromotionSummaryOut 的字段：任一项不合格整份为 failed。
  it.each<[string, unknown]>([
    ["not JSON", undefined],
    ["not an object", [RUNNING]],
    ["csrf_token missing", { promotions: [RUNNING] }],
    ["csrf_token empty", { promotions: [RUNNING], csrf_token: "" }],
    ["promotions missing", { csrf_token: "t" }],
    ["promotions not an array", { promotions: RUNNING, csrf_token: "t" }],
    ["id zero", { promotions: [{ ...RUNNING, id: 0 }], csrf_token: "t" }],
    ["name missing", { promotions: [{ ...RUNNING, name: undefined }], csrf_token: "t" }],
    ["state unknown", { promotions: [{ ...RUNNING, state: "archived" }], csrf_token: "t" }],
    ["starts_at as number", { promotions: [{ ...RUNNING, starts_at: 1 }], csrf_token: "t" }],
    ["ends_at missing", { promotions: [{ ...RUNNING, ends_at: undefined }], csrf_token: "t" }],
    ["rule_count negative", { promotions: [{ ...RUNNING, rule_count: -1 }], csrf_token: "t" }],
    ["rule_count as string", { promotions: [{ ...RUNNING, rule_count: "2" }], csrf_token: "t" }],
    ["updated_at null", { promotions: [{ ...RUNNING, updated_at: null }], csrf_token: "t" }],
    ["one good one bad", { promotions: [RUNNING, { ...DRAFT, state: "Draft" }], csrf_token: "t" }],
  ])("reports a malformed body (%s) as failed", async (_name, body) => {
    stubFetch(body === undefined ? text(200, "<html>") : json(200, body));
    expect(await readAdminPromotions()).toEqual({ kind: "failed" });
  });

  // 落实写法「网络中断与调用方中止为 network」。
  it("reports a network failure and an abort as network", async () => {
    stubFetch(networkDown);
    expect(await readAdminPromotions()).toEqual({ kind: "network" });
    const controller = new AbortController();
    controller.abort();
    stubFetch(json(200, LIST));
    expect(await readAdminPromotions(controller.signal)).toEqual({ kind: "network" });
  });
});

describe("reading one promotion", () => {
  // 文档字符串「读取（GET /{promotion_id}）：require_admin（401）→ 路径 ID……→ read_promotion 的 PromotionDetail 与 CSRF 令牌」：路径段为活动 ID、GET、
  // 同源 cookie、不缓存、请求头只有 Accept、没有请求体，并带上 signal。
  it("gets one promotion by its ID", async () => {
    const fetchMock = stubFetch(json(200, PAGE));
    const controller = new AbortController();
    await readAdminPromotion(7, controller.signal);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json" });
    expect(init.signal).toBe(controller.signal);
  });

  // PromotionPageOut「PromotionOut 的字段另加 csrf_token」：活动的全部字段原样带回（时间不换算），令牌另放，多出的字段（含嵌套的）不带进。
  it("returns the promotion and the CSRF token", async () => {
    stubFetch(json(200, PAGE));
    expect(await readAdminPromotion(7)).toEqual({ kind: "ok", promotion: PROMOTION, csrf_token: "csrf-abc" });
    stubFetch(
      json(200, {
        ...PAGE,
        extra: true,
        decoration: { ...DECORATION, extra: "x" },
        copy: { ...COPY, extra: "x" },
        rules: [{ ...ITEM_RULE, extra: 1, products: [{ id: 5, slug: "kopi", name_en: "Kopi", price_sen: 100 }] }, { ...TIER_RULE, tiers: TIER_RULE.tiers.map((tier) => ({ ...tier, extra: 1 })) }],
      }),
    );
    expect(await readAdminPromotion(7)).toEqual({ kind: "ok", promotion: PROMOTION, csrf_token: "csrf-abc" });
  });

  // UX 0.20 A12 的状态表（草稿、已发布未开始、进行中、已结束）与文档字符串「提前结束」：四个状态都合格，提前结束的时刻原样带回。
  it.each(["draft", "scheduled", "running", "ended"])("accepts the state %s", async (state) => {
    const promotion = { ...PROMOTION, state, ended_early_at: state === "ended" ? "2026-04-01T12:30" : null };
    stubFetch(json(200, { ...promotion, csrf_token: "t" }));
    expect(await readAdminPromotion(7)).toEqual({ kind: "ok", promotion, csrf_token: "t" });
  });

  // 文档字符串「全部接口都经……require_admin：没有有效后台会话时 401」与「PromotionNotFound：404 promotion_not_found」。
  it("reports 401 as none and 404 as missing", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }), json(404, { detail: "promotion_not_found" }));
    expect(await readAdminPromotion(7)).toEqual({ kind: "none" });
    expect(await readAdminPromotion(7)).toEqual({ kind: "missing" });
  });

  // 文档字符串「读取……两者都不写审计」只有上述回答：其他状态都是失败。
  it.each([403, 409, 422, 500])("reports %d as failed", async (status) => {
    stubFetch(json(status, { detail: "x" }));
    expect(await readAdminPromotion(7)).toEqual({ kind: "failed" });
  });

  // 文档字符串「活动 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限」：路径 ID 不合法时不发请求，为 missing。
  it.each([0, -1, 1.5, MAX_PROMOTION_ID + 1, Number.NaN, Number.POSITIVE_INFINITY])("sends nothing for the ID %d", async (id) => {
    const fetchMock = stubFetch(json(200, PAGE));
    expect(MAX_PROMOTION_ID).toBe(2147483647);
    expect(await readAdminPromotion(id)).toEqual({ kind: "missing" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // PromotionPageOut 的 csrf_token「由 cookie 重新算出的 CSRF 令牌」：缺少或为空不合格。
  it.each<[string, unknown]>([
    ["csrf_token missing", PROMOTION],
    ["csrf_token empty", { ...PROMOTION, csrf_token: "" }],
    ["csrf_token as number", { ...PROMOTION, csrf_token: 1 }],
  ])("reports a malformed token (%s) as failed", async (_name, body) => {
    stubFetch(json(200, body));
    expect(await readAdminPromotion(7)).toEqual({ kind: "failed" });
  });

  // 验收「响应缺字段、类型不对或 state 不在四个值里时为 failed」。
  it.each(MALFORMED_PROMOTIONS)("reports a malformed promotion (%s) as failed", async (_name, promotion) => {
    stubFetch(json(200, { ...promotion, csrf_token: "t" }));
    expect(await readAdminPromotion(7)).toEqual({ kind: "failed" });
  });

  // 落实写法「网络中断与调用方中止为 network」。
  it("reports a network failure and an abort as network", async () => {
    stubFetch(networkDown);
    expect(await readAdminPromotion(7)).toEqual({ kind: "network" });
    const controller = new AbortController();
    controller.abort();
    stubFetch(json(200, PAGE));
    expect(await readAdminPromotion(7, controller.signal)).toEqual({ kind: "network" });
  });
});

describe("creating a promotion", () => {
  // 文档字符串「新建为 PromotionInput 的字段……多出字段、缺字段或类型不对 422」与 TierIn 不许多出字段：POST 固定路径，JSON 请求体只有规定字段，
  // 规则不带 rule_id 与 products，满额档恰为三个键；请求头为 Accept、Content-Type 与 CSRF 令牌。
  it("posts only the PromotionIn fields with the CSRF header", async () => {
    const fetchMock = stubFetch(json(201, PROMOTION));
    const controller = new AbortController();
    await createAdminPromotion({ ...INPUT, extra: "x" } as PromotionInput, "csrf-abc", controller.signal);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions");
    expect(sentInit(fetchMock, 0)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json", "content-type": "application/json", "x-csrf-token": "csrf-abc" });
    expect(sentBody(fetchMock, 0)).toEqual(INPUT_BODY);
    expect(sentInit(fetchMock, 0).signal).toBe(controller.signal);
  });

  // 落实写法「参数没有值时提交 null，tiers 每项恰为 threshold_sen、amount_sen 与 percent 三个键，没有值的为 null」：调用方漏给的参数与档位键以 null 提交。
  it("sends null for parameters without a value", async () => {
    const fetchMock = stubFetch(json(201, PROMOTION));
    const loose = {
      ...INPUT,
      rules: [{ kind: "spend_discount", discount_mode: "percent", product_ids: [], category_ids: [], tiers: [{ threshold_sen: 5000, percent: 5 }] }],
      copy: { title_en: "Raya" },
    } as unknown as PromotionInput;
    await createAdminPromotion(loose, "t");
    const body = sentBody(fetchMock, 0) as typeof INPUT_BODY;
    expect(body.rules).toEqual([
      {
        kind: "spend_discount",
        discount_mode: "percent",
        percent: null,
        amount_sen: null,
        buy_quantity: null,
        get_quantity: null,
        get_percent: null,
        threshold_sen: null,
        product_ids: [],
        category_ids: [],
        tiers: [{ threshold_sen: 5000, amount_sen: null, percent: 5 }],
      },
    ]);
    expect(Object.keys(body.copy)).toHaveLength(15);
    expect(body.copy).toMatchObject({ title_en: "Raya", title_zh: null, poster_alt_ms: null });
  });

  // 文档字符串「新建成功 201……都回 PromotionDetail」：201 带回合格的活动为 ok；200 不是新建的成功状态。
  it("returns the new draft on 201 only", async () => {
    stubFetch(json(201, { ...PROMOTION, state: "draft" }), json(200, PROMOTION));
    expect(await createAdminPromotion(INPUT, "t")).toEqual({ kind: "ok", promotion: { ...PROMOTION, state: "draft" } });
    expect(await createAdminPromotion(INPUT, "t")).toEqual({ kind: "failed" });
  });
});

describe("saving a promotion", () => {
  // 文档字符串「保存另有 version」与路由 PUT /{promotion_id}：请求体为新建的各项另加 version，多出的属性不带进。
  it("puts the PromotionSaveIn fields to the promotion's path", async () => {
    const fetchMock = stubFetch(json(200, PROMOTION));
    const input: PromotionSave = { ...INPUT, version: 3 };
    expect(await saveAdminPromotion(7, { ...input, id: 7 } as PromotionSave, "csrf-abc")).toEqual({ kind: "ok", promotion: PROMOTION });
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7");
    expect(sentInit(fetchMock, 0)).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json", "content-type": "application/json", "x-csrf-token": "csrf-abc" });
    expect(sentBody(fetchMock, 0)).toEqual({ ...INPUT_BODY, version: 3 });
  });

  // 文档字符串「都与当前值相同时不加版本、不写审计」：未变也为 200；201 不是保存的成功状态。
  it("treats 201 as failed", async () => {
    stubFetch(json(201, PROMOTION));
    expect(await saveAdminPromotion(7, { ...INPUT, version: 3 }, "t")).toEqual({ kind: "failed" });
  });
});

// 发布、撤回与提前结束：文档字符串「发布、撤回、提前结束与删除只有 version」「其余 200」。
type StateCall = (promotionId: number, version: number, csrfToken: string, signal?: AbortSignal) => Promise<PromotionWrite>;

describe.each<[string, StateCall]>([
  ["publish", publishAdminPromotion],
  ["withdraw", withdrawAdminPromotion],
  ["end", endAdminPromotion],
])("the %s action", (action, call) => {
  // 路由 POST /{promotion_id}/{action}：请求体恰为 {"version": …}，请求头为 Accept、Content-Type 与 CSRF 令牌。
  it("posts only the version", async () => {
    const fetchMock = stubFetch(json(200, PROMOTION));
    const controller = new AbortController();
    expect(await call(7, 3, "csrf-abc", controller.signal)).toEqual({ kind: "ok", promotion: PROMOTION });
    expect(sentUrl(fetchMock, 0)).toBe(`/api/admin/promotions/7/${action}`);
    expect(sentInit(fetchMock, 0)).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json", "content-type": "application/json", "x-csrf-token": "csrf-abc" });
    expect(sentBody(fetchMock, 0)).toEqual({ version: 3 });
    expect(sentInit(fetchMock, 0).signal).toBe(controller.signal);
  });

  // 「其余 200」：201 不是这些操作的成功状态。
  it("treats 201 as failed", async () => {
    stubFetch(json(201, PROMOTION));
    expect(await call(7, 3, "t")).toEqual({ kind: "failed" });
  });
});

describe("copying a promotion", () => {
  // 文档字符串「复制（POST /{promotion_id}/copy）没有请求体、不读请求体……成功 201 与新草稿」：没有请求体与 Content-Type，请求头只有 Accept 与 CSRF 令牌。
  it("posts no body and returns the new draft on 201", async () => {
    const fetchMock = stubFetch(json(201, { ...PROMOTION, id: 8, state: "draft" }));
    const controller = new AbortController();
    expect(await copyAdminPromotion(7, "csrf-abc", controller.signal)).toEqual({ kind: "ok", promotion: { ...PROMOTION, id: 8, state: "draft" } });
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7/copy");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json", "x-csrf-token": "csrf-abc" });
    expect(init.signal).toBe(controller.signal);
  });

  // 「成功 201」：200 不是复制的成功状态。
  it("treats 200 as failed", async () => {
    stubFetch(json(200, PROMOTION));
    expect(await copyAdminPromotion(7, "t")).toEqual({ kind: "failed" });
  });
});

describe("deleting a promotion", () => {
  // 路由 DELETE /{promotion_id} 的文档字符串「请求体只有 version，成功 204 且没有响应体」：请求体恰为 {"version": …}，204 为 ok，不读响应体。
  it("deletes with only the version and accepts 204 without a body", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    const controller = new AbortController();
    expect(await deleteAdminPromotion(7, 3, "csrf-abc", controller.signal)).toEqual({ kind: "ok" });
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7");
    expect(sentInit(fetchMock, 0)).toMatchObject({ method: "DELETE", credentials: "same-origin", cache: "no-store" });
    expect(sentHeaders(fetchMock, 0)).toEqual({ accept: "application/json", "content-type": "application/json", "x-csrf-token": "csrf-abc" });
    expect(sentBody(fetchMock, 0)).toEqual({ version: 3 });
    expect(sentInit(fetchMock, 0).signal).toBe(controller.signal);
  });

  // 「成功 204」：200 不是删除的成功状态。
  it("treats 200 as failed", async () => {
    stubFetch(json(200, PROMOTION));
    expect(await deleteAdminPromotion(7, 3, "t")).toEqual({ kind: "failed" });
  });
});

type WriteCall = (signal?: AbortSignal) => Promise<PromotionWrite | PromotionDelete>;

// 九个接口中的七个写操作，各以合格的参数调用。
const WRITES: [string, WriteCall][] = [
  ["create", (signal) => createAdminPromotion(INPUT, "t", signal)],
  ["save", (signal) => saveAdminPromotion(7, { ...INPUT, version: 3 }, "t", signal)],
  ["publish", (signal) => publishAdminPromotion(7, 3, "t", signal)],
  ["withdraw", (signal) => withdrawAdminPromotion(7, 3, "t", signal)],
  ["end", (signal) => endAdminPromotion(7, 3, "t", signal)],
  ["copy", (signal) => copyAdminPromotion(7, "t", signal)],
  ["delete", (signal) => deleteAdminPromotion(7, 3, "t", signal)],
];

// 带回活动的写操作（除删除外），用来检查成功响应体的校验。
const RETURNING: [string, number, WriteCall][] = [
  ["create", 201, (signal) => createAdminPromotion(INPUT, "t", signal)],
  ["save", 200, (signal) => saveAdminPromotion(7, { ...INPUT, version: 3 }, "t", signal)],
  ["publish", 200, (signal) => publishAdminPromotion(7, 3, "t", signal)],
  ["withdraw", 200, (signal) => withdrawAdminPromotion(7, 3, "t", signal)],
  ["end", 200, (signal) => endAdminPromotion(7, 3, "t", signal)],
  ["copy", 201, (signal) => copyAdminPromotion(7, "t", signal)],
];

const PROBLEMS = [
  { code: "scope_overlap", field: "rules[1].product_ids", product_id: 5, product_name: "Kopi" },
  { code: "publish_missing", field: "copy.title_en", product_id: null, product_name: null },
];

describe.each(WRITES)("the %s write", (_name, call) => {
  // 文档字符串「服务层的拒绝」各项与「2. require_admin（401）；3. X-CSRF-Token（403 csrf_failed）」：按状态码与 detail 归类，问题与对方名称、当前状态带进结果。
  it.each<[string, Response, PromotionWrite]>([
    ["401", json(401, { detail: "admin_session_required" }), { kind: "none" }],
    ["403", json(403, { detail: "csrf_failed" }), { kind: "csrf" }],
    ["404", json(404, { detail: "promotion_not_found" }), { kind: "missing" }],
    ["422 promotion_invalid", json(422, { detail: { code: "promotion_invalid", problems: PROBLEMS } }), { kind: "invalid", problems: PROBLEMS }],
    ["409 promotion_overlap", json(409, { detail: { code: "promotion_overlap", name: "Merdeka" } }), { kind: "overlap", name: "Merdeka" }],
    ["409 promotion_changed", json(409, { detail: "promotion_changed" }), { kind: "changed" }],
    ["409 promotion_state_invalid", json(409, { detail: { code: "promotion_state_invalid", state: "running" } }), { kind: "state", state: "running" }],
    ["409 promotion_ended", json(409, { detail: "promotion_ended" }), { kind: "ended" }],
  ])("maps %s", async (_status, reply, expected) => {
    stubFetch(reply);
    expect(await call()).toEqual(expected);
  });

  // 落实写法「failed（其他，含 413、415、其他 422 与响应体不合格）」，以及 422 的 problems 每项缺字段时整份为 failed、409 的 detail 不合格为 failed。
  it.each<[string, Response]>([
    ["413", text(413, "too large")],
    ["415", json(415, { detail: "request body must be JSON" })],
    ["422 body validation", json(422, { detail: [{ loc: ["body", "version"], type: "missing", msg: "Field required" }] })],
    ["422 other code", json(422, { detail: { code: "promotion_other", problems: PROBLEMS } })],
    ["422 promotion_invalid as string", json(422, { detail: "promotion_invalid" })],
    ["422 problems missing", json(422, { detail: { code: "promotion_invalid" } })],
    ["422 problems not an array", json(422, { detail: { code: "promotion_invalid", problems: PROBLEMS[0] } })],
    ["422 problem without code", json(422, { detail: { code: "promotion_invalid", problems: [{ field: "name", product_id: null, product_name: null }] } })],
    ["422 problem without field", json(422, { detail: { code: "promotion_invalid", problems: [{ code: "x", product_id: null, product_name: null }] } })],
    ["422 problem without product_id", json(422, { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "name", product_name: null }] } })],
    ["422 problem without product_name", json(422, { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "name", product_id: null }] } })],
    ["422 problem product_id as string", json(422, { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "name", product_id: "5", product_name: null }] } })],
    ["422 one good one bad problem", json(422, { detail: { code: "promotion_invalid", problems: [PROBLEMS[0], { code: "x" }] } })],
    ["422 not JSON", text(422, "bad")],
    ["409 overlap without name", json(409, { detail: { code: "promotion_overlap" } })],
    ["409 overlap name null", json(409, { detail: { code: "promotion_overlap", name: null } })],
    ["409 state without state", json(409, { detail: { code: "promotion_state_invalid" } })],
    ["409 state unknown", json(409, { detail: { code: "promotion_state_invalid", state: "paused" } })],
    ["409 other code", json(409, { detail: "category_changed" })],
    ["409 not JSON", text(409, "conflict")],
    ["500", json(500, { detail: "x" })],
  ])("reports %s as failed", async (_status, reply) => {
    stubFetch(reply);
    expect(await call()).toEqual({ kind: "failed" });
  });

  // 落实写法「网络中断与调用方中止为 network」。
  it("reports a network failure and an abort as network", async () => {
    stubFetch(networkDown);
    expect(await call()).toEqual({ kind: "network" });
    const controller = new AbortController();
    controller.abort();
    const fetchMock = stubFetch(json(200, PROMOTION));
    expect(await call(controller.signal)).toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe.each(RETURNING)("the %s result", (_name, okStatus, call) => {
  // 验收「响应缺字段、类型不对或 state 不在四个值里时为 failed」：成功状态的响应体不合格或不是 JSON 为 failed。
  it.each(MALFORMED_PROMOTIONS)("reports a malformed promotion (%s) as failed", async (_case, promotion) => {
    stubFetch(json(okStatus, promotion));
    expect(await call()).toEqual({ kind: "failed" });
  });

  it("reports a body that is not JSON as failed", async () => {
    stubFetch(text(okStatus, "<html>"));
    expect(await call()).toEqual({ kind: "failed" });
  });
});

// 文档字符串「4. 路径里的活动 ID 照 _category_id 校验，不合法 422」与落实写法「路径 ID 不是正整数时不发请求」：带路径 ID 的写操作都不发请求。
describe.each<[string, (id: number) => Promise<PromotionWrite | PromotionDelete>]>([
  ["save", (id) => saveAdminPromotion(id, { ...INPUT, version: 3 }, "t")],
  ["publish", (id) => publishAdminPromotion(id, 3, "t")],
  ["withdraw", (id) => withdrawAdminPromotion(id, 3, "t")],
  ["end", (id) => endAdminPromotion(id, 3, "t")],
  ["copy", (id) => copyAdminPromotion(id, "t")],
  ["delete", (id) => deleteAdminPromotion(id, 3, "t")],
])("the %s write with a bad ID", (_name, call) => {
  it.each([0, -1, 1.5, MAX_PROMOTION_ID + 1, Number.NaN, Number.POSITIVE_INFINITY])("sends nothing for the ID %d", async (id) => {
    const fetchMock = stubFetch(json(200, PROMOTION));
    expect(await call(id)).toEqual({ kind: "missing" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // 上限本身合格（与服务端 MAX_PROMOTION_ID 相同）。
  it("sends the largest ID", async () => {
    const fetchMock = stubFetch(json(500, {}));
    await call(MAX_PROMOTION_ID);
    expect(sentUrl(fetchMock, 0)).toMatch(/^\/api\/admin\/promotions\/2147483647(\/|$)/);
  });
});
