import type { RuleDisplay } from "./api/promotion";
import { formatSen } from "./format";
import { translate } from "./i18n/copy";
import type { Language } from "./i18n/copy";

// 节日促销活动的规则说明、角标与倒计时文字（UX 0.20「1. 全局框架」「节日促销活动」「商品卡价格与角标」、P03、P15）。
// 只把接口给的规则参数与金额套进 docs/UX-COPY.md 第 11 节的 promo.* 模板（DESIGN 1.23「节日促销活动」第 10 条）：
// 不计算金额，只格式化接口给的整数仙；不读组件上下文，语言由调用方传入。

export const KIND_ITEM_DISCOUNT = "item_discount";
export const KIND_BUY_X_GET_Y = "buy_x_get_y";
export const KIND_SPEND_DISCOUNT = "spend_discount";
export const KIND_FREE_SHIPPING = "free_shipping";

const MODE_PERCENT = "percent";
const MODE_AMOUNT = "amount";

// 买 X 享 Y 享受的件 100% 即「送」。
const FREE_PERCENT = 100;

// 角标所需的规则参数：RuleDisplay 与商品卡的规则都可直接传入。不属于该类别的参数为 null。
export interface PromoRuleParams {
  kind: string;
  discount_mode: string | null;
  percent: number | null;
  amount_sen: number | null;
  buy_quantity: number | null;
  get_quantity: number | null;
  get_percent: number | null;
}

// 商品卡与 P03 的规则角标；满额与免运费（整单级）不显示角标，返回 null。
export function badgeText(language: Language, rule: PromoRuleParams): string | null {
  if (rule.kind === KIND_ITEM_DISCOUNT) {
    if (rule.discount_mode === MODE_PERCENT && rule.percent !== null) {
      return translate(language, "promo.badge_percent", { percent: rule.percent });
    }
    if (rule.discount_mode === MODE_AMOUNT && rule.amount_sen !== null) {
      return translate(language, "promo.badge_amount", { amount: formatSen(rule.amount_sen) });
    }
    return null;
  }
  if (rule.kind === KIND_BUY_X_GET_Y) {
    const { buy_quantity: x, get_quantity: y, get_percent: percent } = rule;
    if (x === null || y === null || percent === null) {
      return null;
    }
    return percent === FREE_PERCENT
      ? translate(language, "promo.badge_bxgy_free", { x, y })
      : translate(language, "promo.badge_bxgy_pct", { x, y, percent });
  }
  return null;
}

// 一条规则的说明行：满额减或满额折每档一行，其余类别一行；类别不认识时为空列表。
export function ruleTexts(language: Language, rule: RuleDisplay): string[] {
  switch (rule.kind) {
    case KIND_ITEM_DISCOUNT:
      if (rule.discount_mode === MODE_PERCENT && rule.percent !== null) {
        return [translate(language, "promo.rule_item_percent", { percent: rule.percent })];
      }
      if (rule.discount_mode === MODE_AMOUNT && rule.amount_sen !== null) {
        return [translate(language, "promo.rule_item_amount", { amount: formatSen(rule.amount_sen) })];
      }
      return [];
    case KIND_BUY_X_GET_Y: {
      const { buy_quantity: x, get_quantity: y, get_percent: percent } = rule;
      if (x === null || y === null || percent === null) {
        return [];
      }
      return [
        percent === FREE_PERCENT
          ? translate(language, "promo.rule_bxgy_free", { x, y })
          : translate(language, "promo.rule_bxgy_pct", { x, y, percent }),
      ];
    }
    case KIND_SPEND_DISCOUNT:
      return rule.tiers.flatMap((tier) => {
        const threshold = formatSen(tier.threshold_sen);
        if (tier.amount_sen !== null) {
          return [translate(language, "promo.rule_tier_amount", { threshold, amount: formatSen(tier.amount_sen) })];
        }
        if (tier.percent !== null) {
          return [translate(language, "promo.rule_tier_percent", { threshold, percent: tier.percent })];
        }
        return [];
      });
    case KIND_FREE_SHIPPING:
      if (rule.threshold_sen === null) {
        return [];
      }
      return [
        rule.threshold_sen === 0
          ? translate(language, "promo.rule_free_shipping_all")
          : translate(language, "promo.rule_free_shipping", { threshold: formatSen(rule.threshold_sen) }),
      ];
    default:
      return [];
  }
}

// 分类名称的分隔符：中文用顿号，英文与马来文用逗号加空格（UX-COPY promo.rule_scope 的提示）。
const NAME_SEPARATORS: Readonly<Record<Language, string>> = { en: ", ", zh: "、", ms: ", " };

// P15 商品级规则说明后附的范围：分类名称按接口顺序；另选了上架的单件商品时用 rule_scope_with_items。
// 没有分类时返回 null（只含单件商品时不附范围）。
export function scopeText(language: Language, rule: RuleDisplay): string | null {
  if (rule.categories.length === 0) {
    return null;
  }
  const names = rule.categories.map((category) => category.name).join(NAME_SEPARATORS[language]);
  return translate(language, rule.has_products ? "promo.rule_scope_with_items" : "promo.rule_scope", { names });
}

const SECONDS_PER_DAY = 86_400;

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}

// 倒计时文字：剩余毫秒按整秒向下取整，小于 0 按 0；剩余 1 天以上时带天数。
export function countdownText(language: Language, remainingMs: number): string {
  const total = remainingMs > 0 ? Math.floor(remainingMs / 1000) : 0;
  const days = Math.floor(total / SECONDS_PER_DAY);
  const rest = total % SECONDS_PER_DAY;
  const clock = {
    hh: twoDigits(Math.floor(rest / 3600)),
    mm: twoDigits(Math.floor((rest % 3600) / 60)),
    ss: twoDigits(rest % 60),
  };
  const time =
    days >= 1
      ? translate(language, "promo.countdown_days", { d: days, ...clock })
      : translate(language, "promo.countdown", clock);
  return translate(language, "promo.ends_in", { time });
}
