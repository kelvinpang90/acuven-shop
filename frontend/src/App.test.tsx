import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";
import { BRAND, COPY, LANGUAGES } from "./i18n/copy";
import type { Language } from "./i18n/copy";
import { LANGUAGE_STORAGE_KEY } from "./i18n/language";
import { ROUTE_PATHS } from "./router";

function render(path: string, language: Language): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  return renderToStaticMarkup(<App initialPath={path} storage={storage} />);
}

function unescapeHtml(text: string): string {
  return text
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) =>
    unescapeHtml(m[1] ?? ""),
  );
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((text) => unescapeHtml(text).trim())
    .filter((text) => text !== "");
  return [...textNodes, ...attributes];
}

const cases = ROUTE_PATHS.flatMap((path) => LANGUAGES.map((language): [string, Language] => [path, language]));

describe("App", () => {
  // 需求：所有商品、金额、支付与发货都是演示，页面须持续、清楚地标注这一点（原外壳页测试的意图，改由全站框架守住）。
  it("labels every page as a demo", () => {
    for (const [path, language] of cases) {
      expect(render(path, language)).toContain(COPY["common.demo_banner"][language]);
    }
  });

  // 验收：界面上不出现字典以外的文字（品牌字样 ACUVEN SHOP 除外），且只出现当前语言那一列。
  it.each(cases)("shows only dictionary text on %s in %s", (path, language) => {
    const allowed = new Set<string>([BRAND, ...Object.values(COPY).map((entry) => entry[language])]);
    const texts = visibleTexts(render(path, language));
    expect(texts.length).toBeGreaterThan(5);
    for (const text of texts) {
      expect(allowed.has(text), text).toBe(true);
    }
  });

  // UX P01：★ home.demo_hint 位于页头下方、所有区块之前；默认设置下四个区块按主视觉、演示怎么玩、按分类浏览、精选商品的顺序显示。
  it("renders the fixed demo hint after the header and before the four default blocks", () => {
    const html = render("/", "en");
    const main = /<main class="site-home">([\s\S]*)<\/main>/.exec(html)?.[1] ?? "";
    expect(main).toMatch(/^<p class="acs-hint">/);
    const positions = [
      html.indexOf("</header>"),
      html.indexOf(COPY["home.demo_hint"].en),
      html.indexOf(COPY["home.hero_title"].en),
      html.indexOf(COPY["home.how_title"].en),
      html.indexOf(COPY["home.categories"].en),
      html.indexOf(COPY["home.featured"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });
});
