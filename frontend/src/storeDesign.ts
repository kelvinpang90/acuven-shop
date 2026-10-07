import { createContext, createElement, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { FEATURED_COUNT } from "./api/catalog";
import { browserStorage } from "./i18n/language";

// 店铺装修设置（docs/REQUIREMENTS.md「店铺装修」、docs/UX.md P01 与 A08），由全站框架的根元素与首页读取。
// 页面加载时向公开接口 GET /api/store-design（SHOP-TASK-045）请求一次：主题、主色、首页四个区块的顺序与显隐、
// 精选商品的 slug。深浅色仍跟随访客设备，页面不设切换。不含标志图。
//
// 本浏览器记住上次取到的主题、主色与区块（docs/HANDOFF.md 0.38，Kelvin 2026-10-07 决定）：打开页面时先用它，
// 取到新设置后更新并记住；不记精选商品。没有记住的值或不合格时用默认值（班兰、默认主色、四个区块按默认顺序全部显示）。
// 请求失败时保持当前显示，不清除已记住的值。存储不可用时照常显示，只是不记住。

// 10 款主题与各自可选的主色，顺序与 docs/design/tokens/themes.json 的 themes 与 accentOptions 相同（storeDesign.test.ts 守住）。
// 主色为 null 即该主题的默认主色（accentOptions 第一项），根元素不设 data-accent。
export const THEME_ACCENTS = {
  pandan: ["pandan", "teal", "clay", "aubergine", "charcoal"],
  pasar: ["green", "tomato", "blue", "plum", "ink"],
  receipt: ["ink", "cobalt", "forest", "oxblood"],
  kopitiam: ["kopi", "teh", "tile", "red"],
  batik: ["indigo", "sogan", "maroon", "teal"],
  malam: ["pink", "violet", "orange", "lime"],
  gula: ["pink", "mint", "grape", "orange"],
  galeri: ["black", "graphite", "navy", "bottle"],
  songket: ["maroon", "emerald", "royal", "black"],
  litar: ["teal", "violet", "red", "graphite"],
} as const;
export type ShopTheme = keyof typeof THEME_ACCENTS;
export const SHOP_THEMES = Object.keys(THEME_ACCENTS) as readonly ShopTheme[];

export const DEFAULT_THEME: ShopTheme = "pandan";

// 深浅色跟随访客设备（REQUIREMENTS「店铺装修」：访客不能自行切换主题或深浅色）。
export const STORE_MODE = "auto";

// 首页区块（docs/UX.md P01）：主视觉、演示怎么玩、按分类浏览、精选商品。★ home.demo_hint 不是区块，不可隐藏。
export const HOME_BLOCKS = ["hero", "how", "categories", "featured"] as const;
export type HomeBlock = (typeof HOME_BLOCKS)[number];

export interface HomeBlockSetting {
  readonly block: HomeBlock;
  readonly visible: boolean;
}

export interface StoreDesign {
  readonly theme: ShopTheme;
  readonly accent: string | null;
  // 恰好四个区块，按位置排序。
  readonly homeBlocks: readonly HomeBlockSetting[];
}

export const DEFAULT_STORE_DESIGN: StoreDesign = {
  theme: DEFAULT_THEME,
  accent: null,
  homeBlocks: HOME_BLOCKS.map((block) => ({ block, visible: true })),
};

export const STORE_DESIGN_URL = "/api/store-design";
export const STORE_DESIGN_STORAGE_KEY = "acuven-shop.store-design";

export type StoreDesignStorage = Pick<Storage, "getItem" | "setItem">;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isShopTheme(value: unknown): value is ShopTheme {
  return typeof value === "string" && Object.hasOwn(THEME_ACCENTS, value);
}

function isHomeBlock(value: unknown): value is HomeBlock {
  return typeof value === "string" && (HOME_BLOCKS as readonly string[]).includes(value);
}

// 区块须恰为四个区块的一个排列，每项 visible 为布尔值。
function parseHomeBlocks(value: unknown): HomeBlockSetting[] | null {
  if (!Array.isArray(value) || value.length !== HOME_BLOCKS.length) {
    return null;
  }
  const blocks: HomeBlockSetting[] = [];
  for (const entry of value as unknown[]) {
    if (!isRecord(entry) || !isHomeBlock(entry.block) || typeof entry.visible !== "boolean") {
      return null;
    }
    const block = entry.block;
    if (blocks.some((setting) => setting.block === block)) {
      return null;
    }
    blocks.push({ block, visible: entry.visible });
  }
  return blocks;
}

// 接口与本浏览器记住的值用同一形状 {theme, accent, home_blocks}；任一项不合格整份不用。
export function parseStoreDesign(value: unknown): StoreDesign | null {
  if (!isRecord(value) || !isShopTheme(value.theme)) {
    return null;
  }
  const theme = value.theme;
  const given = value.accent;
  let accent: string | null;
  if (given === null) {
    accent = null;
  } else if (typeof given === "string" && (THEME_ACCENTS[theme] as readonly string[]).includes(given)) {
    accent = given;
  } else {
    return null;
  }
  const homeBlocks = parseHomeBlocks(value.home_blocks);
  return homeBlocks === null ? null : { theme, accent, homeBlocks };
}

export interface StoreDesignResponse {
  readonly design: StoreDesign;
  // 按位置排序、只含前台目录可见的精选商品；可能为空。
  readonly featuredSlugs: readonly string[];
}

// 精选商品的 slug：最多 FEATURED_COUNT 个非空字符串（A08 最多挑 4 件）。
function parseFeaturedSlugs(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length > FEATURED_COUNT) {
    return null;
  }
  const slugs: string[] = [];
  for (const slug of value as unknown[]) {
    if (typeof slug !== "string" || slug === "") {
      return null;
    }
    slugs.push(slug);
  }
  return slugs;
}

export function parseStoreDesignResponse(value: unknown): StoreDesignResponse | null {
  if (!isRecord(value)) {
    return null;
  }
  const design = parseStoreDesign(value);
  const featuredSlugs = parseFeaturedSlugs(value.featured_slugs);
  return design === null || featuredSlugs === null ? null : { design, featuredSlugs };
}

// 本浏览器记住的设置；没有、不合格或读取失败时为 null。
export function readRememberedDesign(storage: StoreDesignStorage | null): StoreDesign | null {
  if (!storage) {
    return null;
  }
  try {
    const raw = storage.getItem(STORE_DESIGN_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const stored: unknown = JSON.parse(raw);
    return parseStoreDesign(stored);
  } catch {
    return null;
  }
}

// 只记主题、主色与区块，不记精选商品；写入失败时照常显示，只是下次不记得。
export function rememberDesign(storage: StoreDesignStorage | null, design: StoreDesign): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(
      STORE_DESIGN_STORAGE_KEY,
      JSON.stringify({
        theme: design.theme,
        accent: design.accent,
        home_blocks: design.homeBlocks.map(({ block, visible }) => ({ block, visible })),
      }),
    );
  } catch {
    // 存不下就不记住。
  }
}

// 读一次设置：同源、不带 cookie、不缓存。非 2xx、网络错误或响应体不合格时抛错。
export async function fetchStoreDesign(signal?: AbortSignal): Promise<StoreDesignResponse> {
  const response = await fetch(STORE_DESIGN_URL, {
    method: "GET",
    credentials: "omit",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new Error(`store design request failed with status ${String(response.status)}`);
  }
  const body: unknown = await response.json();
  const parsed = parseStoreDesignResponse(body);
  if (parsed === null) {
    throw new Error("store design response is not valid");
  }
  return parsed;
}

// 取一次并在成功时记住；失败时返回 null，不动已记住的值。
export async function loadStoreDesign(
  storage: StoreDesignStorage | null,
  signal?: AbortSignal,
): Promise<StoreDesignResponse | null> {
  try {
    const result = await fetchStoreDesign(signal);
    rememberDesign(storage, result.design);
    return result;
  } catch {
    return null;
  }
}

export interface StoreDesignValue extends StoreDesign {
  // 精选商品的 slug；null 表示设置还没返回（首页先不取精选）。设置请求失败时为空，即显示最新 4 件。
  readonly featuredSlugs: readonly string[] | null;
}

// 打开页面时：先用本浏览器记住的设置，没有时用默认值；精选等设置返回。
export function initialStoreDesign(storage: StoreDesignStorage | null): StoreDesignValue {
  return { ...(readRememberedDesign(storage) ?? DEFAULT_STORE_DESIGN), featuredSlugs: null };
}

// 请求返回后：成功时换成新设置；失败时主题、主色与区块保持当前显示，精选按取不到处理。
export function settledStoreDesign(current: StoreDesignValue, result: StoreDesignResponse | null): StoreDesignValue {
  return result === null ? { ...current, featuredSlugs: [] } : { ...result.design, featuredSlugs: result.featuredSlugs };
}

// 没有提供者时（如单独渲染的组件）用默认值，精选按未挑选处理。
const StoreDesignContext = createContext<StoreDesignValue>({ ...DEFAULT_STORE_DESIGN, featuredSlugs: [] });

interface StoreDesignProviderProps {
  children: ReactNode;
  // 测试传入假存储；不传时用本浏览器的 localStorage。
  storage?: StoreDesignStorage | null | undefined;
}

export function StoreDesignProvider({ children, storage }: StoreDesignProviderProps) {
  const store = storage === undefined ? browserStorage() : storage;
  const [value, setValue] = useState<StoreDesignValue>(() => initialStoreDesign(store));

  useEffect(() => {
    const controller = new AbortController();
    void loadStoreDesign(store, controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setValue((current) => settledStoreDesign(current, result));
      }
    });
    return () => {
      controller.abort();
    };
  }, [store]);

  return createElement(StoreDesignContext.Provider, { value }, children);
}

export function useStoreDesign(): StoreDesignValue {
  return useContext(StoreDesignContext);
}
