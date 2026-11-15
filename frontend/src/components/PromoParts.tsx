import type { CardPromotion } from "../api/catalog";
import type { PromotionDisplay } from "../api/promotion";
import { useCopy } from "../i18n/language";

// 节日促销活动的角标与划线价（UX 0.20「1. 全局框架」「商品卡价格与角标」，COMPONENTS「FestivalPromotion」的 Badges 与 Prices）。
// 商品卡（P01 精选、P02、P15）与之后的 P03、P04 共用。只显示目录接口给的金额与文案，不计算金额（DESIGN 1.23「节日促销活动」第 10 条）：
// 文字由调用方套好模板后传入，这里只负责结构与读屏标签。

// card_badge 的取值：seal 以活动图案开头，其余（tag）为纯文字。
const BADGE_SEAL = "seal";

// 商品命中的规则只在正在显示的活动里有这条规则时才算数：没有活动或倒计时到 0 后，目录接口的旧数据不再显示活动。
export function shownRule(promotion: PromotionDisplay | null, rule: CardPromotion | null): CardPromotion | null {
  if (promotion === null || rule === null) {
    return null;
  }
  return promotion.rules.some((item) => item.rule_id === rule.rule_id) ? rule : null;
}

function PromoBadge({ text, style }: { text: string; style: string }) {
  if (style === BADGE_SEAL) {
    return (
      <span className="acs-promo-badge acs-promo-badge--seal">
        <span className="acs-promo-motif" aria-hidden="true" />
        <span>{text}</span>
      </span>
    );
  }
  return (
    <span className="acs-promo-badge acs-promo-badge--tag">
      <span>{text}</span>
    </span>
  );
}

interface PromoBadgesProps {
  // 活动的角标短语（PromotionDisplay 的 badge）；为空不放。
  phrase: string | null;
  // 规则角标（promoText.ts 的 badgeText）；为空不放。
  rule: string | null;
  // 活动的 card_badge：tag 或 seal。
  style: string;
}

// 角标框：先放角标短语，再放规则角标；两者都为空时不渲染。
export function PromoBadges({ phrase, rule, style }: PromoBadgesProps) {
  const texts = [phrase, rule].filter((text): text is string => text !== null && text !== "");
  if (texts.length === 0) {
    return null;
  }
  return (
    <span className="acs-promo-badges">
      {texts.map((text, index) => (
        <PromoBadge key={index} text={text} style={style} />
      ))}
    </span>
  );
}

interface PromoPricesProps {
  // 已套好 common.price_myr 或 list.price_from 的价格文字。
  original: string;
  sale: string;
}

// 划线原价在前、活动价在后，各以读屏标签开头。
export function PromoPrices({ original, sale }: PromoPricesProps) {
  const t = useCopy();
  return (
    <span className="acs-prices">
      <span className="acs-price-was">
        <span className="acs-vh">{t("promo.a11y_original")}</span>
        {original}
      </span>
      <span className="acs-price-sale">
        <span className="acs-vh">{t("promo.a11y_sale")}</span>
        {sale}
      </span>
    </span>
  );
}
