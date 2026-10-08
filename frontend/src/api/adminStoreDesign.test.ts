import { afterEach, describe, expect, it, vi } from "vitest";

import { LANGUAGES } from "../i18n/copy";
import { ADMIN_STORE_DESIGN_URL, adminStoreDesignUrl, readAdminStoreDesign, saveAdminStoreDesign } from "./adminStoreDesign";
import type { StoreDesignInput } from "./adminStoreDesign";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BLOCKS = [
  { block: "featured", visible: true },
  { block: "hero", visible: true },
  { block: "how", visible: false },
  { block: "categories", visible: true },
];
const FEATURED = [
  { product_id: 7, slug: "soy-wax-candle", name: "Soy wax candle", published: true },
  { product_id: 3, slug: "old-tote", name: null, published: false },
];
const CHOICES = [
  { product_id: 5, slug: "crew-neck-tee", name: "Crew neck tee" },
  { product_id: 7, slug: "soy-wax-candle", name: "Soy wax candle" },
];
const SAVED = { theme: "batik", accent: "sogan", home_blocks: BLOCKS, featured: FEATURED };
const DETAIL = { ...SAVED, choices: CHOICES, csrf_token: "csrf-abc" };
const INPUT: StoreDesignInput = {
  theme: "batik",
  accent: "sogan",
  home_blocks: [
    { block: "featured", visible: true },
    { block: "hero", visible: true },
    { block: "how", visible: false },
    { block: "categories", visible: true },
  ],
  featured_product_ids: [7, 3],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      const next = queue.shift();
      if (next === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 两种响应体共有的不合格情形（主题、主色、区块与精选）：SHOP-TASK-063 验收第 2 条「响应体不合格算失败」逐项列出的各种。
function malformedDesigns(base: Record<string, unknown>): [string, unknown][] {
  return [
    ["not an object", [base]],
    ["null body", null],
    ["theme missing", { ...base, theme: undefined }],
    ["unknown theme", { ...base, theme: "neon" }],
    ["theme in another case", { ...base, theme: "Batik" }],
    ["accent of another theme", { ...base, accent: "lime" }],
    ["accent empty", { ...base, accent: "" }],
    ["accent missing", { ...base, accent: undefined }],
    ["accent as a colour", { ...base, accent: "#ff0000" }],
    ["blocks missing", { ...base, home_blocks: undefined }],
    ["blocks not an array", { ...base, home_blocks: {} }],
    ["a block repeated", { ...base, home_blocks: [BLOCKS[0], BLOCKS[0], BLOCKS[2], BLOCKS[3]] }],
    ["a block missing", { ...base, home_blocks: BLOCKS.slice(0, 3) }],
    ["a fifth block", { ...base, home_blocks: [...BLOCKS, { block: "hero", visible: true }] }],
    ["an unknown block", { ...base, home_blocks: [{ block: "banner", visible: true }, ...BLOCKS.slice(1)] }],
    ["visible as string", { ...base, home_blocks: [{ block: "featured", visible: "true" }, ...BLOCKS.slice(1)] }],
    ["visible missing", { ...base, home_blocks: [{ block: "featured" }, ...BLOCKS.slice(1)] }],
    ["featured missing", { ...base, featured: undefined }],
    ["featured not an array", { ...base, featured: {} }],
    ["featured item not an object", { ...base, featured: [null] }],
    [
      "five featured",
      {
        ...base,
        featured: [1, 2, 3, 4, 5].map((id) => ({ product_id: id, slug: `p-${String(id)}`, name: "P", published: true })),
      },
    ],
    ["featured repeated", { ...base, featured: [FEATURED[0], FEATURED[0]] }],
    ["featured id zero", { ...base, featured: [{ ...FEATURED[0], product_id: 0 }] }],
    ["featured id negative", { ...base, featured: [{ ...FEATURED[0], product_id: -7 }] }],
    ["featured id fractional", { ...base, featured: [{ ...FEATURED[0], product_id: 7.5 }] }],
    ["featured id as string", { ...base, featured: [{ ...FEATURED[0], product_id: "7" }] }],
    ["featured id missing", { ...base, featured: [{ ...FEATURED[0], product_id: undefined }] }],
    ["featured slug empty", { ...base, featured: [{ ...FEATURED[0], slug: "" }] }],
    ["featured slug as number", { ...base, featured: [{ ...FEATURED[0], slug: 7 }] }],
    ["featured slug missing", { ...base, featured: [{ ...FEATURED[0], slug: undefined }] }],
    ["featured name as number", { ...base, featured: [{ ...FEATURED[0], name: 1 }] }],
    ["featured name missing", { ...base, featured: [{ ...FEATURED[0], name: undefined }] }],
    ["published as string", { ...base, featured: [{ ...FEATURED[0], published: "true" }] }],
    ["published as number", { ...base, featured: [{ ...FEATURED[0], published: 1 }] }],
    ["published missing", { ...base, featured: [{ ...FEATURED[0], published: undefined }] }],
  ];
}

describe("reading the store design", () => {
  // SHOP-TASK-063 验收第 1 条「调用 GET /api/admin/store-design?lang=<语言>（同源 cookie，cache 为 no-store）」：
  // 地址为固定路径加 lang（唯一的查询参数），GET、凭同源 cookie、不缓存、没有请求体与 CSRF 头，并带上调用方的 signal。
  it.each(LANGUAGES)("gets the design in %s with the same-origin cookie and no cache", async (language) => {
    const calls = stubFetch(json(200, DETAIL));
    const controller = new AbortController();
    await readAdminStoreDesign(language, controller.signal);
    expect(ADMIN_STORE_DESIGN_URL).toBe("/api/admin/store-design");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/store-design?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(new Headers(calls[0]?.init.headers).has("X-CSRF-Token")).toBe(false);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    const url = new URL(adminStoreDesignUrl(language), "http://localhost");
    expect([...url.searchParams.entries()]).toEqual([["lang", language]]);
  });

  // SHOP-TASK-063 验收第 2 条「只取 SHOP-TASK-051 记录段列出的字段——读取为 theme、accent、home_blocks、featured（product_id、slug、
  // name 或 null、published）、choices（product_id、slug、name）与 csrf_token」：多出的字段不带进结果；SHOP-TASK-051「已不满足
  // published() 的也列出、published 为 false」与「name 在请求语言与英文都缺少时为 null」的精选合格。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...DETAIL,
        logo: "ignored",
        home_blocks: BLOCKS.map((setting) => ({ ...setting, position: 1 })),
        featured: FEATURED.map((item) => ({ ...item, price_sen: 1000 })),
        choices: CHOICES.map((item) => ({ ...item, published: true })),
      }),
    );
    await expect(readAdminStoreDesign("en")).resolves.toEqual({ kind: "ok", design: DETAIL });
  });

  // SHOP-TASK-051「主色（为空时 null）」与 SHOP-TASK-061「之后下架的商品仍留在列表中」：主色为 null、精选为空、choices 为空都合格。
  it("accepts a default accent and empty lists", async () => {
    const plain = { ...DETAIL, theme: "pandan", accent: null, featured: [], choices: [] };
    stubFetch(json(200, plain));
    await expect(readAdminStoreDesign("zh")).resolves.toEqual({ kind: "ok", design: plain });
  });

  // SHOP-TASK-051「featured（按位置排序）」：恰好 4 件精选合格，顺序原样保留。
  it("accepts four featured products in their order", async () => {
    const four = [9, 2, 7, 4].map((id) => ({ product_id: id, slug: `p-${String(id)}`, name: `P${String(id)}`, published: true }));
    stubFetch(json(200, { ...DETAIL, featured: four }));
    const read = await readAdminStoreDesign("ms");
    expect(read.kind === "ok" ? read.design.featured.map((item) => item.product_id) : null).toEqual([9, 2, 7, 4]);
  });

  // SHOP-TASK-063 验收第 3 条「读取结果归为 ok、none（401）、failed 与 network」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, {}), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminStoreDesign("en")).resolves.toEqual({ kind });
  });

  // SHOP-TASK-063 验收第 3 条「调用方可传入 AbortSignal，中止时按现有模块的做法处理」：中止后 fetch 抛错，与 adminStockResets.ts
  // 相同归为 network（页面按 signal 已中止不再更新）。
  it("treats an aborted request like the existing modules", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
        init?.signal?.aborted === true ? Promise.reject(new DOMException("Aborted", "AbortError")) : Promise.resolve(json(200, DETAIL)),
      ),
    );
    controller.abort();
    await expect(readAdminStoreDesign("en", controller.signal)).resolves.toEqual({ kind: "network" });
  });

  // SHOP-TASK-063 验收第 2 条「响应体不合格算失败」：主题、主色、区块与精选的各种不合格，以及 choices 的 product_id 重复、
  // 任一 product_id 不是正整数、slug 不是非空字符串、choices 的 name 不是字符串、csrf_token 不是非空字符串、字段缺少或类型不对。
  it.each<[string, unknown]>([
    ...malformedDesigns(DETAIL),
    ["choices missing", { ...DETAIL, choices: undefined }],
    ["choices not an array", { ...DETAIL, choices: "all" }],
    ["choice not an object", { ...DETAIL, choices: [5] }],
    ["choices repeated", { ...DETAIL, choices: [CHOICES[0], CHOICES[0]] }],
    ["choice id zero", { ...DETAIL, choices: [{ ...CHOICES[0], product_id: 0 }] }],
    ["choice id as string", { ...DETAIL, choices: [{ ...CHOICES[0], product_id: "5" }] }],
    ["choice id fractional", { ...DETAIL, choices: [{ ...CHOICES[0], product_id: 5.5 }] }],
    ["choice slug empty", { ...DETAIL, choices: [{ ...CHOICES[0], slug: "" }] }],
    ["choice slug missing", { ...DETAIL, choices: [{ ...CHOICES[0], slug: undefined }] }],
    ["choice name null", { ...DETAIL, choices: [{ ...CHOICES[0], name: null }] }],
    ["choice name as number", { ...DETAIL, choices: [{ ...CHOICES[0], name: 5 }] }],
    ["csrf token missing", { ...DETAIL, csrf_token: undefined }],
    ["csrf token empty", { ...DETAIL, csrf_token: "" }],
    ["csrf token null", { ...DETAIL, csrf_token: null }],
    ["csrf token as number", { ...DETAIL, csrf_token: 123 }],
  ])("treats a malformed design as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminStoreDesign("en")).resolves.toEqual({ kind: "failed" });
  });

  // 同一条：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readAdminStoreDesign("en")).resolves.toEqual({ kind: "failed" });
  });
});

describe("saving the store design", () => {
  // SHOP-TASK-063 验收第 1 条「PUT /api/admin/store-design?lang=<语言>（同源 cookie，cache 为 no-store；PUT 带 X-CSRF-Token 为调用方
  // 给出的 csrf_token，Content-Type 为 application/json）」：地址只带 lang，并带上调用方的 signal。
  it.each(LANGUAGES)("puts the design in %s with the CSRF token", async (language) => {
    const calls = stubFetch(json(200, SAVED));
    const controller = new AbortController();
    await saveAdminStoreDesign(INPUT, "csrf-abc", language, controller.signal);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/store-design?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(calls[0]?.init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(calls[0]?.init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-063 验收第 1 条「JSON 请求体只有 theme、accent、home_blocks 与 featured_product_ids 四个字段」与 SHOP-TASK-051
  // 「数组顺序即位置」：调用方对象上多出的字段（含区块项上的）不进请求体，区块与精选的顺序原样保留，主色 null 原样发出。
  it("sends only the four fields in their order", async () => {
    const calls = stubFetch(json(200, SAVED), json(200, { ...SAVED, theme: "pandan", accent: null, featured: [] }));
    const extra = {
      ...INPUT,
      choices: CHOICES,
      csrf_token: "leak",
      home_blocks: INPUT.home_blocks.map((setting) => ({ ...setting, label: "x" })),
    };
    await saveAdminStoreDesign(extra, "csrf-abc", "en");
    expect(Object.keys(JSON.parse(calls[0]?.init.body as string) as object)).toEqual(["theme", "accent", "home_blocks", "featured_product_ids"]);
    expect(JSON.parse(calls[0]?.init.body as string)).toEqual({
      theme: "batik",
      accent: "sogan",
      home_blocks: BLOCKS,
      featured_product_ids: [7, 3],
    });
    await saveAdminStoreDesign({ ...INPUT, theme: "pandan", accent: null, featured_product_ids: [] }, "csrf-abc", "en");
    expect(JSON.parse(calls[1]?.init.body as string)).toEqual({ theme: "pandan", accent: null, home_blocks: BLOCKS, featured_product_ids: [] });
  });

  // SHOP-TASK-063 验收第 2 条「保存为 theme、accent、home_blocks 与 featured」：保存的响应体只取这四个字段（不要求也不带进
  // choices 与 csrf_token）。
  it("keeps only the saved fields", async () => {
    stubFetch(json(200, { ...SAVED, choices: CHOICES, csrf_token: "csrf-abc", extra: 1 }), json(200, SAVED));
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en")).resolves.toEqual({ kind: "ok", design: SAVED });
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en")).resolves.toEqual({ kind: "ok", design: SAVED });
  });

  // SHOP-TASK-063 验收第 3 条「保存结果归为 ok、none（401）、csrf（403）、failed（含 422 与其他状态）与 network」。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "accent_invalid" }), "failed"],
    [json(422, { detail: "featured_unavailable" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(413, {}), "failed"],
    [json(415, {}), "failed"],
    [json(409, {}), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en")).resolves.toEqual({ kind });
  });

  // SHOP-TASK-063 验收第 3 条「调用方可传入 AbortSignal，中止时按现有模块的做法处理」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request like the existing modules", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
        init?.signal?.aborted === true ? Promise.reject(new DOMException("Aborted", "AbortError")) : Promise.resolve(json(200, SAVED)),
      ),
    );
    controller.abort();
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en", controller.signal)).resolves.toEqual({ kind: "network" });
  });

  // SHOP-TASK-063 验收第 2 条「响应体不合格算失败」：保存的响应体里主题、主色、区块与精选的各种不合格都算 failed。
  it.each<[string, unknown]>(malformedDesigns(SAVED))("treats a malformed saved design as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en")).resolves.toEqual({ kind: "failed" });
  });

  // 同一条：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("", { status: 200 }));
    await expect(saveAdminStoreDesign(INPUT, "csrf-abc", "en")).resolves.toEqual({ kind: "failed" });
  });
});
