import type { CardPromotion } from "../api/catalog";
import type { PromotionDisplay } from "../api/promotion";
import { useCopy } from "../i18n/language";
import { PromotionCountdown, usePromotion } from "../promotion";
import { isRoutePath, Link, ROUTE_PATHS } from "../router";
import type { RoutePath } from "../router";

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

// 活动专题页 P15 的路径。P15 在 SHOP-TASK-257 才加入路由表：/sale 在 ROUTE_PATHS 里时返回它，否则为 null，
// 调用方不渲染去 P15 的按钮与链接（HANDOFF 0.97）；路由表加入后自动出现。
const SALE_PATH: string = "/sale";

export function saleLinkPath(): RoutePath | null {
  const routes: readonly string[] = ROUTE_PATHS;
  return routes.includes(SALE_PATH) && isRoutePath(SALE_PATH) ? SALE_PATH : null;
}

// 倒计时框：时钟图形与 PromotionCountdown 的文字（视觉稿的 .acs-promo-count）。没有活动或不计时（ends_at 为 null）时不渲染。
export function PromoCount() {
  const { promotion } = usePromotion();
  if (promotion === null || promotion.ends_at === null) {
    return null;
  }
  return (
    <span className="acs-promo-count">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="13" r="8" />
        <path d="M12 9v4l3 2M9 2h6" />
      </svg>
      <span>
        <PromotionCountdown />
      </span>
    </span>
  );
}

interface PromoRuleListProps {
  // 已套好 promo.rule_* 模板的说明行，按要显示的顺序。
  lines: readonly string[];
  // 活动的 coupon_combinable、points_combinable：为假时分别显示 promo.coupon_not_combinable、promo.points_not_combinable。
  couponCombinable: boolean;
  pointsCombinable: boolean;
}

// 规则列表（UX 0.20 P03、P15，COMPONENTS「FestivalPromotion」Price ribbon 的 rules area）：说明行的 ul，其后是不同用说明。
// P03 的活动规则区与 P15（SHOP-TASK-257）共用；标题、倒计时与链接由调用方放。
export function PromoRuleList({ lines, couponCombinable, pointsCombinable }: PromoRuleListProps) {
  const t = useCopy();
  return (
    <>
      <ul>
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      {!couponCombinable && <p className="acs-body-s">{t("promo.coupon_not_combinable")}</p>}
      {!pointsCombinable && <p className="acs-body-s">{t("promo.points_not_combinable")}</p>}
    </>
  );
}

// list_strip 的取值：ticket 为虚线票券，其余（band）为整条色带。
const STRIP_TICKET = "ticket";

interface PromoStripProps {
  // 横条显示的文字：P02 为活动的横条文字（为空时用标题），P15 为活动标题。
  text: string;
  // 末尾是否放 promo.view_all（P02 放，P15 不放）；saleLinkPath() 为 null 时一律不放。
  viewAll: boolean;
}

// 活动横条（UX 0.20 P02、P15，COMPONENTS「FestivalPromotion」Sale strip）：活动图案、文字，末尾为倒计时与 promo.view_all。
// 变体按活动的 list_strip；没有活动时不渲染。
export function PromoStrip({ text, viewAll }: PromoStripProps) {
  const t = useCopy();
  const { promotion } = usePromotion();
  if (promotion === null) {
    return null;
  }
  const variant = promotion.list_strip === STRIP_TICKET ? "ticket" : "band";
  const sale = viewAll ? saleLinkPath() : null;
  return (
    <div className={`acs-promo-strip acs-promo-strip--${variant}`}>
      <span className="acs-promo-motif" aria-hidden="true" />
      <span className="acs-promo-strip__title">{text}</span>
      <span className="acs-promo-strip__end">
        <PromoCount />
        {sale !== null && <Link to={sale}>{t("promo.view_all")}</Link>}
      </span>
    </div>
  );
}
