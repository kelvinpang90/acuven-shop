import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { isRoutePath } from "../router";
import { COPY } from "./copy";
import {
  LANGUAGE_STORAGE_KEY,
  applyDocumentLanguage,
  htmlLang,
  readStoredLanguage,
  saveLanguage,
} from "./language";
import type { LanguageStorage } from "./language";

function memoryStorage(initial: Record<string, string> = {}): LanguageStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => data[key] ?? null,
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

const brokenStorage: LanguageStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reading the saved language", () => {
  // UX-COPY「约定」：默认语言英文。
  it("defaults to English when nothing is saved or storage is unavailable", () => {
    expect(readStoredLanguage(memoryStorage())).toBe("en");
    expect(readStoredLanguage(null)).toBe("en");
  });

  // UX-COPY「约定」：访客的选择保存在本浏览器。
  it("returns a saved choice", () => {
    expect(readStoredLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "zh" }))).toBe("zh");
    expect(readStoredLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "ms" }))).toBe("ms");
  });

  // 非法值回退英文：只认 en、zh、ms 三个值，不做大小写或地区码转换。
  it.each(["fr", "ZH", "zh-CN", "", " en", "null"])("falls back to English for %j", (stored) => {
    expect(readStoredLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: stored }))).toBe("en");
  });

  // 验收：存储读写失败时照常工作。
  it("falls back to English when reading throws", () => {
    expect(readStoredLanguage(brokenStorage)).toBe("en");
  });
});

describe("saving the language", () => {
  // UX-COPY「约定」：选择只保存在本浏览器（localStorage），下次读回同一语言。
  it("stores the choice under one key and reads it back", () => {
    const storage = memoryStorage();
    saveLanguage(storage, "ms");
    expect(storage.data).toEqual({ [LANGUAGE_STORAGE_KEY]: "ms" });
    expect(readStoredLanguage(storage)).toBe("ms");
  });

  // 验收：写入失败时照常工作，只是不记住。
  it("does not throw when writing fails or storage is unavailable", () => {
    expect(() => {
      saveLanguage(brokenStorage, "zh");
    }).not.toThrow();
    expect(() => {
      saveLanguage(null, "zh");
    }).not.toThrow();
  });
});

describe("html lang attribute", () => {
  // 验收：html 元素的 lang 属性随当前语言变化。
  it("follows the current language", () => {
    const doc = { documentElement: { lang: "en" } };
    applyDocumentLanguage(doc, "zh");
    expect(doc.documentElement.lang).toBe("zh-Hans");
    applyDocumentLanguage(doc, "ms");
    expect(doc.documentElement.lang).toBe("ms");
    applyDocumentLanguage(doc, "en");
    expect(doc.documentElement.lang).toBe("en");
    expect(htmlLang("en")).toBe("en");
  });
});

describe("rendering in the chosen language", () => {
  // UX-COPY「约定」：页头切换后整页按所选语言显示。
  it("renders the frame in the saved language", () => {
    const html = renderToStaticMarkup(
      <App initialPath="/" storage={memoryStorage({ [LANGUAGE_STORAGE_KEY]: "zh" })} />,
    );
    expect(html).toContain(COPY["common.demo_banner"].zh);
    expect(html).not.toContain(COPY["common.demo_banner"].en);
  });

  // UX-COPY「约定」：不按 IP 或浏览器语言自动切换——浏览器语言是中文、没有保存的选择时仍是英文。
  it("ignores the browser language", () => {
    vi.stubGlobal("navigator", { language: "zh-CN", languages: ["zh-CN", "ms-MY"] });
    const html = renderToStaticMarkup(<App initialPath="/" storage={memoryStorage()} />);
    expect(html).toContain(COPY["common.demo_banner"].en);
    expect(html).not.toContain(COPY["common.demo_banner"].zh);
  });

  // 验收：存储读取失败时照常显示（英文）。
  it("renders in English when storage throws", () => {
    const html = renderToStaticMarkup(<App initialPath="/privacy" storage={brokenStorage} />);
    expect(html).toContain(COPY["privacy.title"].en);
  });

  // 验收：语言不放进网址——语言选项链接指向当前页面本身，每个链接都是路由表里的路径，不带查询参数或语言前缀。
  it("keeps the language out of links", () => {
    const html = renderToStaticMarkup(
      <App initialPath="/privacy" storage={memoryStorage({ [LANGUAGE_STORAGE_KEY]: "ms" })} />,
    );
    const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(isRoutePath(href), href).toBe(true);
      expect(href).not.toContain("?");
    }
  });
});
