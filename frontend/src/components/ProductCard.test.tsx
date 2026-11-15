import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CardPromotion, ProductSummary } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse, RuleDisplay } from "../api/promotion";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromotionProvider } from "../promotion";
import { RouterProvider, matchRoute } from "../router";
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
    promotion: null,
    min_sale_price_sen: null,
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
      <RouterProvider initialPath="/products">
        <ProductCard product={item} />
      </RouterProvider>
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

  // 派生实现约束（实现选择）：守住 SHOP-TASK-015 验收第 4 条「商品卡显示首张图片」——没有图片时沿用第 3 条分类图片「为空时显示视觉稿的占位形状」的同一占位，不渲染空 img。
  it("shows a placeholder shape when there is no image", () => {
    const html = render(product({ image: null }));
    expect(html).not.toContain("<img");
    expect(html).toMatch(/<span class="acs-pcard__img"><svg viewBox="0 0 100 100" aria-hidden="true">/);
  });

  // UX P02「演示提示」：售罄显示 list.out_of_stock（示例库存每日重置）。
  it.each(LANGUAGES)("marks a product sold out today in %s", (language) => {
    const html = render(product({ sold_out_today: true }), language);
    expect(html).toContain(`<span class="acs-tag acs-tag--neutral">${COPY["list.out_of_stock"][language]}</span>`);
    expect(html).toMatch(/^<a class="acs-pcard acs-pcard--oos" href="\/products\/crew-neck-tee">/);
  });

  // SHOP-TASK-015 验收第 4 条「当日售罄的商品显示 list.out_of_stock」与 UX P02「演示提示」的反面：有货的卡片不带该标签。
  it("has no sold-out label while in stock", () => {
    const html = render(product());
    expect(html).not.toContain(COPY["list.out_of_stock"].en);
    expect(html).toMatch(/^<a class="acs-pcard" href="\/products\/crew-neck-tee">/);
  });

  // SHOP-TASK-017 验收第 2 条「本任务起商品卡与首页精选按路由规则链到详情页」：整张卡片是一个指向 /products/<slug> 的链接。
  it("links the whole card to the product detail page", () => {
    const html = render(product());
    expect(html).toMatch(/^<a class="acs-pcard" href="\/products\/crew-neck-tee">[\s\S]*<\/a>$/);
    expect(html.match(/<a\b/g)).toHaveLength(1);
  });

  // SHOP-TASK-017 验收第 2 条「站内链接…仍是实际路径」与「请求路径里只编码一次」：slug 在链接里编码一次，路由解码回原值。
  it("encodes the slug once in the link", () => {
    const html = render(product({ slug: "a b" }));
    expect(html).toMatch(/^<a class="acs-pcard" href="\/products\/a%20b">/);
    expect(matchRoute("/products/a%20b")?.params).toEqual({ slug: "a b" });
  });

  // UX-COPY「约定」：商品名称缺少当前语言时回退英文；回退的文字标明是英文，读屏按英文读。
  it("marks an English fallback name", () => {
    const html = render(product({ name: { text: "Crew Neck Tee", english_fallback: true } }), "zh");
    expect(html).toContain(`<span class="acs-pcard__name" lang="en">Crew Neck Tee</span>`);
  });
});

// 节日促销活动（SHOP-TASK-254）：有活动的状态以 PromotionProvider 的 initial 传入，挂载前即为 ready，不读取接口。
const SERVER_TIME = "2026-11-14T04:00:00Z";

const PERCENT: CardPromotion = {
  rule_id: 11,
  kind: "item_discount",
  discount_mode: "percent",
  percent: 20,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
};
const AMOUNT: CardPromotion = { ...PERCENT, rule_id: 12, discount_mode: "amount", percent: null, amount_sen: 500 };
const BXGY_FREE: CardPromotion = {
  ...PERCENT,
  rule_id: 13,
  kind: "buy_x_get_y",
  discount_mode: null,
  percent: null,
  buy_quantity: 2,
  get_quantity: 1,
  get_percent: 100,
};
const BXGY_PCT: CardPromotion = { ...BXGY_FREE, rule_id: 14, get_percent: 50 };

function ruleDisplay(rule: CardPromotion): RuleDisplay {
  return { ...rule, position: rule.rule_id, threshold_sen: null, tiers: [], categories: [], has_products: false };
}

function sale(overrides: Partial<PromotionDisplay> = {}): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar Sale",
    subtitle: null,
    badge: null,
    strip: null,
    pack: "lunar",
    palette: "lunar",
    ambient: "none",
    home_poster: "split",
    list_strip: "band",
    card_badge: "tag",
    detail_ribbon: "ribbon",
    cart_progress: "bar",
    poster_mode: "template",
    poster_desktop: null,
    poster_phone: null,
    poster_alt: null,
    // 比服务端时间晚一小时结束。
    ends_at: "2026-11-14T05:00:00Z",
    coupon_combinable: true,
    points_combinable: true,
    rules: [PERCENT, AMOUNT, BXGY_FREE, BXGY_PCT].map(ruleDisplay),
    ...overrides,
  };
}

function renderOnSale(item: ProductSummary, promotion: PromotionDisplay | null, language: Language = "en"): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  const initial: PromotionResponse = { promotion, server_time: SERVER_TIME };
  return renderToStaticMarkup(
    <LanguageProvider storage={storage}>
      <PromotionProvider initial={initial}>
        <RouterProvider initialPath="/products">
          <ProductCard product={item} />
        </RouterProvider>
      </PromotionProvider>
    </LanguageProvider>,
  );
}

function tag(text: string): string {
  return `<span class="acs-promo-badge acs-promo-badge--tag"><span>${text}</span></span>`;
}

// 图片角的角标框：在图片之前、紧接 .acs-pcard__img 开始。
function badges(inner: string): string {
  return `<span class="acs-pcard__img"><span class="acs-promo-badges">${inner}</span><img`;
}

// 名称之后的价格：划线原价在前、活动价在后。
function prices(language: Language, original: string, salePrice: string): string {
  return (
    `</span><span class="acs-prices">` +
    `<span class="acs-price-was"><span class="acs-vh">${COPY["promo.a11y_original"][language]}</span>${original}</span>` +
    `<span class="acs-price-sale"><span class="acs-vh">${COPY["promo.a11y_sale"][language]}</span>${salePrice}</span>` +
    `</span>`
  );
}

describe("product card on sale", () => {
  // UX 0.20「商品卡价格与角标」：「商品命中打折规则时，价格处先显示划线原价（读屏标签 [promo.a11y_original]），再显示活动价（读屏标签 [promo.a11y_sale]）」
  // 「打折为 [promo.badge_percent]」。价格处由 .acs-prices 取代 .acs-pcard__price，不嵌套。
  it.each(LANGUAGES)("shows the percent badge and the sale price in %s", (language) => {
    const html = renderOnSale(product({ promotion: PERCENT, min_sale_price_sen: 3120 }), sale(), language);
    expect(html).toContain(badges(tag(translate(language, "promo.badge_percent", { percent: 20 }))));
    expect(html).toContain(`${prices(language, "RM 39.00", "RM 31.20")}</a>`);
    expect(html).not.toContain("acs-pcard__price");
  });

  // UX 0.20「商品卡价格与角标」：「打折为 [promo.badge_percent] 或 [promo.badge_amount]」——每件直降的角标与活动价。
  it.each(LANGUAGES)("shows the amount-off badge and the sale price in %s", (language) => {
    const html = renderOnSale(product({ promotion: AMOUNT, min_sale_price_sen: 3400 }), sale(), language);
    expect(html).toContain(badges(tag(translate(language, "promo.badge_amount", { amount: "5.00" }))));
    expect(html).toContain(`${prices(language, "RM 39.00", "RM 34.00")}</a>`);
  });

  // UX 0.20「商品卡价格与角标」：「多于一个启用规格时，活动价取最低活动单价并照原规则加「起」；划线原价取该 SKU 的原价、不加「起」」。
  it.each(LANGUAGES)("adds the from-price only to the sale price in %s", (language) => {
    const html = renderOnSale(
      product({ has_multiple_variants: true, promotion: PERCENT, min_sale_price_sen: 3120 }),
      sale(),
      language,
    );
    expect(html).toContain(prices(language, "RM 39.00", translate(language, "list.price_from", { amount: "31.20" })));
  });

  // UX 0.20「商品卡价格与角标」：「买 X 享 Y 为 [promo.badge_bxgy_free]（享受的件 100%）或 [promo.badge_bxgy_pct]，价格不变」。
  it.each(LANGUAGES)("shows buy-x-get-y badges with the price unchanged in %s", (language) => {
    const free = renderOnSale(product({ promotion: BXGY_FREE }), sale(), language);
    expect(free).toContain(badges(tag(translate(language, "promo.badge_bxgy_free", { x: 2, y: 1 }))));
    expect(price(free)).toBe("RM 39.00");
    expect(free).not.toContain("acs-prices");

    const pct = renderOnSale(product({ promotion: BXGY_PCT }), sale(), language);
    expect(pct).toContain(badges(tag(translate(language, "promo.badge_bxgy_pct", { x: 2, y: 1, percent: 50 }))));
    expect(price(pct)).toBe("RM 39.00");
    expect(pct).not.toContain("acs-prices");
  });

  // UX 0.20「商品卡价格与角标」：「活动的角标短语（管理员或 AI 写的文案，可为空）显示在规则角标之前」——不为空时在前。
  it("puts the badge phrase before the rule badge", () => {
    const html = renderOnSale(product({ promotion: PERCENT, min_sale_price_sen: 3120 }), sale({ badge: "Lunar deal" }));
    expect(html).toContain(badges(tag("Lunar deal") + tag(translate("en", "promo.badge_percent", { percent: 20 }))));
  });

  // UX 0.20「商品卡价格与角标」：角标短语「可为空」——为空时只有规则角标。
  it.each([null, ""])("shows only the rule badge when the phrase is %j", (phrase) => {
    const html = renderOnSale(product({ promotion: PERCENT, min_sale_price_sen: 3120 }), sale({ badge: phrase }));
    expect(html).toContain(badges(tag(translate("en", "promo.badge_percent", { percent: 20 }))));
  });

  // UX 0.20「节日促销活动」：「`card_badge` 角标样式（商品卡与 P03）……每位的变体由视觉稿给出」——seal 的每个角标以活动图案开头。
  it("draws seal badges starting with the promotion motif", () => {
    const html = renderOnSale(
      product({ promotion: BXGY_FREE }),
      sale({ badge: "Lunar deal", card_badge: "seal" }),
    );
    const motif = `<span class="acs-promo-motif" aria-hidden="true"></span>`;
    expect(html).toContain(
      badges(
        `<span class="acs-promo-badge acs-promo-badge--seal">${motif}<span>Lunar deal</span></span>` +
          `<span class="acs-promo-badge acs-promo-badge--seal">${motif}<span>Buy 2 get 1 free</span></span>`,
      ),
    );
  });

  // UX 0.20「商品卡价格与角标」：「售罄商品照样显示活动价与 [list.out_of_stock]」。
  it.each(LANGUAGES)("keeps the sale price on a sold-out product in %s", (language) => {
    const html = renderOnSale(
      product({ sold_out_today: true, promotion: PERCENT, min_sale_price_sen: 3120 }),
      sale(),
      language,
    );
    expect(html).toMatch(/^<a class="acs-pcard acs-pcard--oos" href="\/products\/crew-neck-tee">/);
    expect(html).toContain(badges(tag(translate(language, "promo.badge_percent", { percent: 20 }))));
    expect(html).toContain(
      `${prices(language, "RM 39.00", "RM 31.20")}<span><span class="acs-tag acs-tag--neutral">${COPY["list.out_of_stock"][language]}</span></span></a>`,
    );
  });
});

describe("product card without a shown promotion", () => {
  const hit = product({ promotion: PERCENT, min_sale_price_sen: 3120 });

  // UX 0.20「节日促销活动」：「没有生效活动时各页与 0.19 完全相同」。
  it.each(LANGUAGES)("is unchanged without a promotion in %s", (language) => {
    expect(renderOnSale(hit, null, language)).toBe(render(hit, language));
  });

  // UX 0.20「节日促销活动」：「到 0 时页面重新读取活动数据并去掉全部活动内容」——卡片规则不在当前活动里（目录的旧数据）时不显示。
  it.each(LANGUAGES)("is unchanged when the rule is not in the current promotion in %s", (language) => {
    const other = sale({ rules: [ruleDisplay({ ...PERCENT, rule_id: 99 })] });
    expect(renderOnSale(hit, other, language)).toBe(render(hit, language));
  });

  // 同上「到 0 时……去掉全部活动内容」：读到的活动已到期（剩余不大于 0）时按没有活动处理。
  it("is unchanged when the promotion has already ended", () => {
    expect(renderOnSale(hit, sale({ ends_at: SERVER_TIME }))).toBe(render(hit));
  });

  // UX 0.20「商品卡价格与角标」：只有「商品命中」商品级规则时才显示活动内容——没有命中的商品在活动期间与现在相同。
  it.each(LANGUAGES)("is unchanged for a product without a hit in %s", (language) => {
    const plain = product();
    expect(renderOnSale(plain, sale({ badge: "Lunar deal" }), language)).toBe(render(plain, language));
  });
});
