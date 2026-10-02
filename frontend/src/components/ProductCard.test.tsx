import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ProductSummary } from "../api/catalog";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import ProductCard from "./ProductCard";

function product(overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    slug: "crew-neck-tee",
    name: { text: "Crew Neck Tee", english_fallback: false },
    category: { slug: "apparel", name: { text: "Apparel", english_fallback: false } },
    image: "/demo-images/apparel.svg",
    min_price_sen: 3900,
    has_multiple_variants: false,
    sold_out_today: false,
    ...overrides,
  };
}

function render(item: ProductSummary, language: Language = "en"): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  return renderToStaticMarkup(
    <LanguageProvider storage={storage}>
      <ProductCard product={item} />
    </LanguageProvider>,
  );
}

function price(html: string): string {
  return /<span class="acs-pcard__price">([^<]*)<\/span>/.exec(html)?.[1] ?? "";
}

describe("product card price", () => {
  // UX P01、P02 M1：商品有多于一个启用规格时一律显示 list.price_from，即使各规格同价（REQUIREMENTS「访客与会员流程」第 1 条）。
  // 接口的 has_multiple_variants 在两个同价规格时也为真，卡片只看这个标记，不比较价格。
  it.each(LANGUAGES)("uses the from-price for several variants in %s", (language) => {
    const html = render(product({ has_multiple_variants: true, min_price_sen: 2190 }), language);
    expect(price(html)).toBe(COPY["list.price_from"][language].replace("{amount}", "21.90"));
  });

  // UX-COPY list.price_from 提示：只有一个规格时用 common.price_myr，不带「起」。
  it.each(LANGUAGES)("uses the plain price for a single variant in %s", (language) => {
    const html = render(product({ has_multiple_variants: false, min_price_sen: 2190 }), language);
    expect(price(html)).toBe("RM 21.90");
  });

  // UX Q9：商品卡只显示 MYR，不显示参考外币。
  it("shows no reference currency", () => {
    const html = render(product());
    expect(html).not.toContain("≈");
    expect(html).not.toMatch(/SGD|USD|CNY/);
  });
});

describe("product card content", () => {
  // UX P02：首张图片、名称与价格；图片旁有名称，读屏不重复读图片。
  it("shows the first image, the name and the price", () => {
    const html = render(product());
    expect(html).toContain(`<img class="site-img" src="/demo-images/apparel.svg" alt=""`);
    expect(html).toContain(`<span class="acs-pcard__name">Crew Neck Tee</span>`);
  });

  // 没有图片时显示占位形状，不渲染空的 img。
  it("shows a placeholder shape when there is no image", () => {
    const html = render(product({ image: null }));
    expect(html).not.toContain("<img");
    expect(html).toMatch(/<span class="acs-pcard__img"><svg viewBox="0 0 100 100" aria-hidden="true">/);
  });

  // UX P02「演示提示」：售罄显示 list.out_of_stock（示例库存每日重置）。
  it.each(LANGUAGES)("marks a product sold out today in %s", (language) => {
    const html = render(product({ sold_out_today: true }), language);
    expect(html).toContain(`<span class="acs-tag acs-tag--neutral">${COPY["list.out_of_stock"][language]}</span>`);
    expect(html).toMatch(/^<div class="acs-pcard acs-pcard--oos">/);
  });

  it("has no sold-out label while in stock", () => {
    const html = render(product());
    expect(html).not.toContain(COPY["list.out_of_stock"].en);
    expect(html).toMatch(/^<div class="acs-pcard">/);
  });

  // 验收：指向商品详情 P03 的链接按路由规则在详情页实现前不渲染。
  it("is not a link while the detail page does not exist", () => {
    expect(render(product())).not.toMatch(/<a\b|href=/);
  });

  // UX-COPY「约定」：商品名称缺少当前语言时回退英文；回退的文字标明是英文，读屏按英文读。
  it("marks an English fallback name", () => {
    const html = render(product({ name: { text: "Crew Neck Tee", english_fallback: true } }), "zh");
    expect(html).toContain(`<span class="acs-pcard__name" lang="en">Crew Neck Tee</span>`);
  });
});
