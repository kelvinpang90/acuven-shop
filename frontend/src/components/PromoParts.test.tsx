import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CardPromotion } from "../api/catalog";
import type { PromotionDisplay, RuleDisplay } from "../api/promotion";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromoBadges, PromoPrices, shownRule } from "./PromoParts";

function render(node: ReactNode, language: Language = "en"): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  return renderToStaticMarkup(<LanguageProvider storage={storage}>{node}</LanguageProvider>);
}

function tag(text: string): string {
  return `<span class="acs-promo-badge acs-promo-badge--tag"><span>${text}</span></span>`;
}

function seal(text: string): string {
  return `<span class="acs-promo-badge acs-promo-badge--seal"><span class="acs-promo-motif" aria-hidden="true"></span><span>${text}</span></span>`;
}

describe("PromoBadges", () => {
  // UX 0.20「商品卡价格与角标」：「活动的角标短语（管理员或 AI 写的文案，可为空）显示在规则角标之前。」
  it("puts the badge phrase before the rule badge", () => {
    const html = render(<PromoBadges phrase="Lunar deal" rule="−20%" style="tag" />);
    expect(html).toBe(`<span class="acs-promo-badges">${tag("Lunar deal")}${tag("−20%")}</span>`);
  });

  // UX 0.20「商品卡价格与角标」：角标短语「可为空」——为 null 或空串时只放规则角标。
  it.each([null, ""])("leaves out an empty badge phrase (%j)", (phrase) => {
    const html = render(<PromoBadges phrase={phrase} rule="−20%" style="tag" />);
    expect(html).toBe(`<span class="acs-promo-badges">${tag("−20%")}</span>`);
  });

  // UX 0.20「节日促销活动」：「`card_badge` 角标样式（商品卡与 P03）……每位的变体由视觉稿给出」——seal 的每个角标以活动图案开头（COMPONENTS「FestivalPromotion」Badges）。
  it("starts every seal badge with the promotion motif", () => {
    const html = render(<PromoBadges phrase="Lunar deal" rule="Buy 2 get 1 free" style="seal" />);
    expect(html).toBe(`<span class="acs-promo-badges">${seal("Lunar deal")}${seal("Buy 2 get 1 free")}</span>`);
  });

  // UX 0.20「商品卡价格与角标」：「只命中整单级规则的商品不显示角标。」——没有规则角标也没有短语时不留空框。
  it("renders nothing without any badge text", () => {
    expect(render(<PromoBadges phrase={null} rule={null} style="tag" />)).toBe("");
  });
});

describe("PromoPrices", () => {
  // UX 0.20「商品卡价格与角标」：「价格处先显示划线原价（读屏标签 [promo.a11y_original]），再显示活动价（读屏标签 [promo.a11y_sale]）」。
  it.each(LANGUAGES)("shows the struck original price before the sale price in %s", (language) => {
    const html = render(<PromoPrices original="RM 39.00" sale="From RM 31.20" />, language);
    expect(html).toBe(
      `<span class="acs-prices">` +
        `<span class="acs-price-was"><span class="acs-vh">${COPY["promo.a11y_original"][language]}</span>RM 39.00</span>` +
        `<span class="acs-price-sale"><span class="acs-vh">${COPY["promo.a11y_sale"][language]}</span>From RM 31.20</span>` +
        `</span>`,
    );
  });
});

function rule(ruleId: number): RuleDisplay {
  return {
    rule_id: ruleId,
    position: 1,
    kind: "item_discount",
    discount_mode: "percent",
    percent: 20,
    amount_sen: null,
    buy_quantity: null,
    get_quantity: null,
    get_percent: null,
    threshold_sen: null,
    tiers: [],
    categories: [],
    has_products: false,
  };
}

const CARD_RULE: CardPromotion = {
  rule_id: 11,
  kind: "item_discount",
  discount_mode: "percent",
  percent: 20,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
};

function promotion(rules: RuleDisplay[]): PromotionDisplay {
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
    ends_at: null,
    coupon_combinable: true,
    points_combinable: true,
    rules,
  };
}

describe("shownRule", () => {
  // UX 0.20「商品卡价格与角标」：「商品命中打折规则时……卡片角上显示角标」——命中的规则在正在显示的活动里时保留（HANDOFF 0.97）。
  it("keeps a rule of the promotion being shown", () => {
    expect(shownRule(promotion([rule(10), rule(11)]), CARD_RULE)).toBe(CARD_RULE);
  });

  // UX 0.20「节日促销活动」：「到 0 时页面重新读取活动数据并去掉全部活动内容」「没有生效活动时各页与 0.19 完全相同」——
  // 没有活动、商品没有命中，或规则不在当前活动里（倒计时到 0 后目录的旧数据）时不显示活动。
  it("drops the rule without a promotion, without a hit or for another promotion", () => {
    expect(shownRule(null, CARD_RULE)).toBeNull();
    expect(shownRule(promotion([rule(11)]), null)).toBeNull();
    expect(shownRule(promotion([rule(12)]), CARD_RULE)).toBeNull();
  });
});
