import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import SiteFrame from "./components/SiteFrame";
import { LanguageProvider } from "./i18n/language";
import { RouterProvider } from "./router";
import {
  DEFAULT_STORE_DESIGN,
  DEFAULT_THEME,
  HOME_BLOCKS,
  SHOP_THEMES,
  STORE_DESIGN_STORAGE_KEY,
  STORE_DESIGN_URL,
  StoreDesignProvider,
  THEME_ACCENTS,
  initialStoreDesign,
  loadStoreDesign,
  parseStoreDesignResponse,
  readRememberedDesign,
  rememberDesign,
  settledStoreDesign,
} from "./storeDesign";
import type { StoreDesign, StoreDesignStorage, StoreDesignValue } from "./storeDesign";

// 按字节读文件，写法同 styles/acuven-shop.test.ts：node:fs 以运行时字符串动态取得，tsconfig.app.json 不带 Node 类型。
interface ReadFileSync {
  readFileSync(path: URL): Uint8Array;
}

async function readText(relativePath: string): Promise<string> {
  const fsModuleName = "node:fs";
  const fs = (await import(/* @vite-ignore */ fsModuleName)) as ReadFileSync;
  return new TextDecoder().decode(fs.readFileSync(new URL(relativePath, import.meta.url)));
}

interface ThemesJson {
  default: string;
  themes: { id: string; accentOptions: { id: string }[] }[];
}

function memoryStorage(initial: Record<string, string> = {}): StoreDesignStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => data[key] ?? null,
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

const brokenStorage: StoreDesignStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
};

const DEFAULT_BLOCKS = HOME_BLOCKS.map((block) => ({ block, visible: true }));

const responseDesign: StoreDesign = {
  theme: "pasar",
  accent: "tomato",
  homeBlocks: [
    { block: "featured", visible: true },
    { block: "hero", visible: false },
    { block: "categories", visible: true },
    { block: "how", visible: true },
  ],
};

// 接口 GET /api/store-design 的一份合格响应（SHOP-TASK-045 的字段）。
const response = {
  theme: "pasar",
  accent: "tomato",
  home_blocks: responseDesign.homeBlocks,
  featured_slugs: ["mug", "tote-bag"],
};

function remembered(value: unknown): StoreDesignStorage & { data: Record<string, string> } {
  return memoryStorage({ [STORE_DESIGN_STORAGE_KEY]: JSON.stringify(value) });
}

// 每次调用现做一个回答（响应体只能读一次；被拒绝的 Promise 不提前建出来）。
function stubFetch(answer: () => Promise<Response>) {
  const fetchMock = vi.fn(answer);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown, status = 200): () => Promise<Response> {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("store design constants", () => {
  // SHOP-TASK-054 验收「常量与 docs/design/tokens/themes.json 一致由测试守住」与 REQUIREMENTS「店铺装修」
  // 「从 10 款预设主题中选一款」「每款主题提供若干预先校过对比度的主色，管理员只能从中选一个」：主题与各自主色的 id 与顺序都与 themes.json 相同。
  it("lists the ten themes and their accents exactly as themes.json", async () => {
    const themes = JSON.parse(await readText("../../docs/design/tokens/themes.json")) as ThemesJson;
    expect(themes.themes).toHaveLength(10);
    expect(SHOP_THEMES).toEqual(themes.themes.map((theme) => theme.id));
    expect(Object.fromEntries(Object.entries(THEME_ACCENTS).map(([theme, accents]) => [theme, [...accents]]))).toEqual(
      Object.fromEntries(themes.themes.map((theme) => [theme.id, theme.accentOptions.map((option) => option.id)])),
    );
    expect(DEFAULT_THEME).toBe(themes.default);
  });

  // SHOP-TASK-054 验收「没有或不合格时用现有默认值（班兰、默认主色、四个区块按默认顺序全部显示）」与 UX P01「区块按 A08 的默认顺序」。
  it("defaults to Pandan with its default accent and all four blocks in order", () => {
    expect(DEFAULT_STORE_DESIGN).toEqual({ theme: "pandan", accent: null, homeBlocks: DEFAULT_BLOCKS });
    expect(HOME_BLOCKS).toEqual(["hero", "how", "categories", "featured"]);
  });
});

describe("reading the store design response", () => {
  // SHOP-TASK-045 的响应字段 {theme, accent, home_blocks, featured_slugs}：合格的响应原样取出，区块与精选保持给出的顺序。
  it("accepts a valid response", () => {
    expect(parseStoreDesignResponse(response)).toEqual({ design: responseDesign, featuredSlugs: ["mug", "tote-bag"] });
    expect(parseStoreDesignResponse({ ...response, accent: null, featured_slugs: [] })).toEqual({
      design: { ...responseDesign, accent: null },
      featuredSlugs: [],
    });
  });

  // SHOP-TASK-054 验收「主题须是 10 款主题之一、主色须为 null 或该主题的可选主色、区块须为 hero、how、categories、featured 的一个排列，
  // 否则整份视为不合格」与「响应体不合格算失败」。
  it.each([
    ["not an object", null],
    ["an array", [response]],
    ["an unknown theme", { ...response, theme: "neon" }],
    ["a theme in another case", { ...response, theme: "Pasar" }],
    ["a missing theme", { ...response, theme: undefined }],
    ["an accent of another theme", { ...response, accent: "pandan" }],
    ["an empty accent", { ...response, accent: "" }],
    ["a colour as accent", { ...response, accent: "#ff0000" }],
    ["a missing accent", { ...response, accent: undefined }],
    ["three blocks", { ...response, home_blocks: response.home_blocks.slice(0, 3) }],
    ["five blocks", { ...response, home_blocks: [...response.home_blocks, { block: "hero", visible: true }] }],
    ["a repeated block", { ...response, home_blocks: [...response.home_blocks.slice(0, 3), { block: "hero", visible: true }] }],
    ["an unknown block", { ...response, home_blocks: [...response.home_blocks.slice(0, 3), { block: "logo", visible: true }] }],
    ["a non-boolean visible", { ...response, home_blocks: [...response.home_blocks.slice(0, 3), { block: "how", visible: 1 }] }],
    ["blocks that are not a list", { ...response, home_blocks: "hero,how,categories,featured" }],
  ])("rejects a response with %s", (_label, body) => {
    expect(parseStoreDesignResponse(body)).toBeNull();
  });

  // 派生实现约束（实现选择）：守住验收「响应体不合格算失败」与 UX A08「精选商品…最多 4 件」——featured_slugs 须为最多 4 个非空字符串。
  it.each([
    ["missing", undefined],
    ["not a list", "mug"],
    ["a number inside", ["mug", 7]],
    ["an empty slug", [""]],
    ["more than four", ["a", "b", "c", "d", "e"]],
  ])("rejects featured slugs that are %s", (_label, featured) => {
    expect(parseStoreDesignResponse({ ...response, featured_slugs: featured })).toBeNull();
  });
});

describe("remembering the store design in this browser", () => {
  // Kelvin 2026-10-07 决定（HANDOFF 0.38）「本浏览器在 localStorage 记住上次取到的主题、主色与首页区块（不记精选商品）」：
  // 写在 acuven-shop.store-design 一个键下，读回同一份设置。
  it("stores theme, accent and blocks under one key without the featured products", () => {
    const storage = memoryStorage();
    const withFeatured: StoreDesignValue = { ...responseDesign, featuredSlugs: ["mug"] };
    rememberDesign(storage, withFeatured);
    expect(Object.keys(storage.data)).toEqual([STORE_DESIGN_STORAGE_KEY]);
    const stored = JSON.parse(storage.data[STORE_DESIGN_STORAGE_KEY] ?? "") as Record<string, unknown>;
    expect(stored).toEqual({ theme: "pasar", accent: "tomato", home_blocks: response.home_blocks });
    expect(readRememberedDesign(storage)).toEqual(responseDesign);
  });

  // SHOP-TASK-054 验收「读取时校验，不合格即忽略」。
  it.each([
    ["not JSON", "{theme"],
    ["an unknown theme", JSON.stringify({ ...response, theme: "neon" })],
    ["an accent of another theme", JSON.stringify({ ...response, accent: "teal" })],
    ["three blocks", JSON.stringify({ ...response, home_blocks: response.home_blocks.slice(0, 3) })],
    ["a string", JSON.stringify("pasar")],
  ])("ignores a remembered value that is %s", (_label, raw) => {
    expect(readRememberedDesign(memoryStorage({ [STORE_DESIGN_STORAGE_KEY]: raw }))).toBeNull();
  });

  // SHOP-TASK-054 验收「所有存储读写包在 try/catch 里，存储不可用时照常显示」。
  it("treats unavailable or failing storage as nothing remembered", () => {
    expect(readRememberedDesign(null)).toBeNull();
    expect(readRememberedDesign(memoryStorage())).toBeNull();
    expect(readRememberedDesign(brokenStorage)).toBeNull();
    expect(() => {
      rememberDesign(brokenStorage, DEFAULT_STORE_DESIGN);
    }).not.toThrow();
    expect(() => {
      rememberDesign(null, DEFAULT_STORE_DESIGN);
    }).not.toThrow();
  });
});

describe("first render", () => {
  // Kelvin 2026-10-07 决定「打开页面时先用它」与验收「请求返回前用本浏览器记住的设置」：精选商品等设置返回（null）。
  it("starts from the remembered design", () => {
    expect(initialStoreDesign(remembered(response))).toEqual({ ...responseDesign, featuredSlugs: null });
  });

  // Kelvin 2026-10-07 决定「没有记住的值时先用默认主题」与验收「没有或不合格时用现有默认值」。
  it.each([
    ["nothing remembered", memoryStorage()],
    ["an invalid remembered value", remembered({ ...response, theme: "neon" })],
    ["no storage", null],
    ["failing storage", brokenStorage],
  ])("starts from the defaults with %s", (_label, storage) => {
    expect(initialStoreDesign(storage)).toEqual({ ...DEFAULT_STORE_DESIGN, featuredSlugs: null });
  });
});

describe("loading the store design", () => {
  // SHOP-TASK-054 验收「页面加载时请求一次 GET /api/store-design（同源，cache 为 no-store）」：相对地址、GET、不缓存，不带 cookie。
  it("asks the same-origin endpoint once without caching", async () => {
    const fetchMock = stubFetch(jsonResponse(response));
    await loadStoreDesign(memoryStorage());
    expect(STORE_DESIGN_URL).toBe("/api/store-design");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/store-design", {
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: null,
    });
  });

  // Kelvin 2026-10-07 决定「取到新设置后更新」与验收「请求成功后把主题、主色与区块写入 localStorage 的 acuven-shop.store-design（不写精选商品）」。
  it("returns the new design and remembers it without the featured products", async () => {
    stubFetch(jsonResponse(response));
    const storage = remembered({ theme: "galeri", accent: null, home_blocks: response.home_blocks });
    const result = await loadStoreDesign(storage);
    expect(result).toEqual({ design: responseDesign, featuredSlugs: ["mug", "tote-bag"] });
    // home_blocks 本身含 { block: "featured" }，所以按属性检查：不记 featured_slugs，也不留下挑选的 slug。
    const stored = JSON.parse(storage.data[STORE_DESIGN_STORAGE_KEY] ?? "") as Record<string, unknown>;
    expect(stored).not.toHaveProperty("featured_slugs");
    expect(stored).not.toHaveProperty("featuredSlugs");
    expect(storage.data[STORE_DESIGN_STORAGE_KEY]).not.toContain("mug");
    expect(readRememberedDesign(storage)).toEqual(responseDesign);
    expect(settledStoreDesign(initialStoreDesign(memoryStorage()), result)).toEqual({
      ...responseDesign,
      featuredSlugs: ["mug", "tote-bag"],
    });
  });

  // SHOP-TASK-054 验收「请求失败时保持当前显示，不清除已记住的设置」与「响应体不合格算失败」：非 2xx、网络中断、不是 JSON、字段不合格都一样。
  it.each([
    ["an error status", jsonResponse(response, 503)],
    ["a network failure", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a body that is not JSON", () => Promise.resolve(new Response("<html>", { status: 200 }))],
    ["an invalid body", jsonResponse({ ...response, accent: "teal" })],
  ])("keeps the current design and the remembered one after %s", async (_label, answer) => {
    stubFetch(answer);
    const storage = remembered({ theme: "galeri", accent: "navy", home_blocks: response.home_blocks });
    const before = { ...storage.data };
    const result = await loadStoreDesign(storage);
    expect(result).toBeNull();
    expect(storage.data).toEqual(before);
    const current = initialStoreDesign(storage);
    expect(settledStoreDesign(current, result)).toEqual({ ...current, featuredSlugs: [] });
  });

  // SHOP-TASK-054 验收「存储不可用时照常显示」：写不进存储时仍用取到的新设置。
  it("still uses the new design when storage throws", async () => {
    stubFetch(jsonResponse(response));
    await expect(loadStoreDesign(brokenStorage)).resolves.toEqual({
      design: responseDesign,
      featuredSlugs: ["mug", "tote-bag"],
    });
  });
});

function renderFrame(storage: StoreDesignStorage | null | undefined, withProvider = true): string {
  const frame = createElement(SiteFrame, { children: createElement("main") });
  const body = withProvider ? createElement(StoreDesignProvider, { storage, children: frame }) : frame;
  return renderToStaticMarkup(
    createElement(LanguageProvider, {
      storage: null,
      children: createElement(RouterProvider, { initialPath: "/", children: body }),
    }),
  );
}

describe("site frame root element", () => {
  // SHOP-TASK-054 验收「根元素 data-shop-theme 取当前主题，主色非 null 时加 data-accent」与「请求返回前用本浏览器记住的设置」，
  // 样式沿用 acuven-shop.css 的 [data-shop-theme][data-accent]；深浅色仍跟随访客设备（data-mode="auto"）。
  it("uses the remembered theme and accent before the request returns", () => {
    expect(renderFrame(remembered(response))).toMatch(
      /^<div class="acs site" data-shop-theme="pasar" data-accent="tomato" data-mode="auto">/,
    );
  });

  // SHOP-TASK-054 验收「为 null 时不设」：主色为 null 即该主题的默认主色。
  it("sets no accent when the accent is null", () => {
    const html = renderFrame(remembered({ ...response, theme: "litar", accent: null }));
    expect(html).toMatch(/^<div class="acs site" data-shop-theme="litar" data-mode="auto">/);
    expect(html).not.toContain("data-accent");
  });

  // SHOP-TASK-054 验收「没有提供者时取默认值」「没有或不合格时用现有默认值」「存储不可用时照常显示」。
  it("uses the default theme without a provider or a valid remembered design", () => {
    const pages = [
      renderFrame(undefined, false),
      renderFrame(memoryStorage()),
      renderFrame(remembered({ ...response, accent: "navy" })),
      renderFrame(brokenStorage),
      renderFrame(null),
    ];
    for (const html of pages) {
      expect(html).toMatch(/^<div class="acs site" data-shop-theme="pandan" data-mode="auto">/);
      expect(html).not.toContain("data-accent");
    }
  });
});
