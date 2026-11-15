import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CardPromotion } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse, RuleDisplay } from "../api/promotion";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromotionProvider } from "../promotion";
import { countdownText } from "../promoText";
import { ROUTE_PATHS, RouterProvider } from "../router";
import { PromoBadges, PromoPrices, PromoRuleList, PromoStrip, saleLinkPath, shownRule } from "./PromoParts";

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

describe("PromoRuleList", () => {
  const lines = ["20% off", "Spend RM 150.00, save RM 15.00", "Free sample shipping on orders from RM 120.00"];

  // UX 0.20 P03 线框「· <本商品适用的商品级规则说明>」「· <整单级规则说明>（满额减各档、免运费）」：说明行按传入的顺序列成 ul；可同用时没有说明。
  it("lists the rule texts in the given order", () => {
    const html = render(<PromoRuleList lines={lines} couponCombinable pointsCombinable />);
    expect(html).toBe(`<ul><li>20% off</li><li>Spend RM 150.00, save RM 15.00</li><li>Free sample shipping on orders from RM 120.00</li></ul>`);
  });

  // UX 0.20 P03「活动关闭了与优惠券或积分同用时，分别显示 [promo.coupon_not_combinable]、[promo.points_not_combinable]」。
  it.each(LANGUAGES)("adds the not-combinable notes after the list in %s", (language) => {
    const coupon = `<p class="acs-body-s">${escapeHtml(COPY["promo.coupon_not_combinable"][language])}</p>`;
    const points = `<p class="acs-body-s">${escapeHtml(COPY["promo.points_not_combinable"][language])}</p>`;
    const list = `<ul><li>20% off</li></ul>`;
    expect(render(<PromoRuleList lines={["20% off"]} couponCombinable={false} pointsCombinable={false} />, language)).toBe(`${list}${coupon}${points}`);
    expect(render(<PromoRuleList lines={["20% off"]} couponCombinable={false} pointsCombinable />, language)).toBe(`${list}${coupon}`);
    expect(render(<PromoRuleList lines={["20% off"]} couponCombinable pointsCombinable={false} />, language)).toBe(`${list}${points}`);
  });
});

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

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

const HOUR_MS = 3_600_000;
const SERVER_TIME = "2026-11-14T04:00:00Z";
// 服务端时间之后一小时结束：时钟替身固定为 0，倒计时文字为 countdownText(language, HOUR_MS)。
const ENDS_AT = "2026-11-14T05:00:00Z";

const CLOCK_ICON =
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">` +
  `<circle cx="12" cy="13" r="8"></circle><path d="M12 9v4l3 2M9 2h6"></path></svg>`;

function countBox(language: Language): string {
  return `<span class="acs-promo-count">${CLOCK_ICON}<span>${countdownText(language, HOUR_MS)}</span></span>`;
}

// 以给定的一次读取结果（PromotionProvider 的 initial）渲染；读取替身从不返回。
function renderWithPromotion(node: ReactNode, display: PromotionDisplay | null, language: Language = "en"): string {
  const initial: PromotionResponse = { promotion: display, server_time: SERVER_TIME };
  return render(
    <RouterProvider initialPath="/products">
      <PromotionProvider initial={initial} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
        {node}
      </PromotionProvider>
    </RouterProvider>,
    language,
  );
}

function shown(overrides: Partial<PromotionDisplay>): PromotionDisplay {
  return { ...promotion([rule(11)]), ends_at: ENDS_AT, ...overrides };
}

// promo.view_all 链接：P15 在路由表里时才有（按 saleLinkPath() 的返回值，不写死）。
function viewAllLink(language: Language): string {
  const sale = saleLinkPath();
  return sale === null ? "" : `<a href="${sale}">${COPY["promo.view_all"][language]}</a>`;
}

describe("saleLinkPath", () => {
  // UX 0.20 P01「整图是去 P15 的链接」、P02「活动横条：<横条文字> [promo.ends_in] ⏱ ( [promo.view_all] ) → P15」——
  // 去 P15 的链接只在 /sale 在路由表里时给出，否则为 null（HANDOFF 0.97）。
  it("gives /sale only while it is in the route table", () => {
    const routes: readonly string[] = ROUTE_PATHS;
    expect(saleLinkPath()).toBe(routes.includes("/sale") ? "/sale" : null);
  });
});

describe("PromoStrip", () => {
  // UX 0.20 P02「活动横条按 `list_strip` 的变体显示」与「活动横条：<横条文字> [promo.ends_in] ⏱ ( [promo.view_all] ) → P15」：
  // 两种变体都是图案、文字，末尾为倒计时与 promo.view_all（P15 不在路由表时不放）。
  it.each(LANGUAGES.flatMap((language) => ["band", "ticket"].map((variant): [Language, string] => [language, variant])))(
    "shows the %s %s strip with the text, the countdown and the view-all link",
    (language, variant) => {
      const html = renderWithPromotion(<PromoStrip text="Lunar New Year Sale" viewAll />, shown({ list_strip: variant }), language);
      expect(html).toBe(
        `<div class="acs-promo-strip acs-promo-strip--${variant}">` +
          `<span class="acs-promo-motif" aria-hidden="true"></span>` +
          `<span class="acs-promo-strip__title">Lunar New Year Sale</span>` +
          `<span class="acs-promo-strip__end">${countBox(language)}${viewAllLink(language)}</span>` +
          `</div>`,
      );
    },
  );

  // UX 0.20 P15「活动横条（list_strip）：<活动标题>   [promo.ends_in] ⏱」——P15 传入活动标题、不放 promo.view_all。
  it("leaves out the view-all link when asked to", () => {
    const html = renderWithPromotion(<PromoStrip text="Lunar New Year Sale" viewAll={false} />, shown({}));
    expect(html).not.toContain("<a ");
    expect(html).not.toContain(COPY["promo.view_all"].en);
    expect(html).toContain(`<span class="acs-promo-strip__end">${countBox("en")}</span>`);
  });

  // UX 0.20「节日促销活动」：「没有生效活动时各页与 0.19 完全相同」——没有活动时横条什么都不渲染。
  it("renders nothing without a promotion", () => {
    expect(renderWithPromotion(<PromoStrip text="Lunar New Year Sale" viewAll />, null)).toBe("");
  });

  // UX 0.20 P02「[promo.ends_in] ⏱」——ends_at 为 null（不计时）时不放空的倒计时框。
  it("leaves out the countdown when the promotion has no end", () => {
    const html = renderWithPromotion(<PromoStrip text="Lunar New Year Sale" viewAll />, shown({ ends_at: null }));
    expect(html).not.toContain("acs-promo-count");
    expect(html).toContain(`<span class="acs-promo-strip__end">${viewAllLink("en")}</span>`);
  });
});
