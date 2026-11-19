import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

import { fetchPreviewQuote } from "../api/adminPromotionPreview";
import { fetchQuote, latestQuoter, quoteRequestBody } from "../api/checkout";
import type { CheckoutQuote, PricedQuoteLine, PromotionQuote, QuoteLine, QuoteOutcome } from "../api/checkout";
import type { LocalizedText } from "../api/catalog";
import { useMemberSession } from "../api/memberSession";
import type { PromotionDisplay } from "../api/promotion";
import {
  MAX_CART_UNITS,
  MAX_LINE_QUANTITY,
  MIN_LINE_QUANTITY,
  cartItemCount,
  parseQuantityInput,
  removeLine,
  saveCart,
  setLineQuantity,
  useCartLines,
} from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { CatalogImage, PlaceholderShape } from "../components/ProductCard";
import { PromoBadges, PromoPrices } from "../components/PromoParts";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey, CopyVars, Language } from "../i18n/copy";
import { browserStorage, useCopy, useLanguage } from "../i18n/language";
import { usePreview } from "../preview";
import type { StorefrontPreview } from "../preview";
import { badgeText } from "../promoText";
import { usePromotion } from "../promotion";
import { isRoutePath, Link, PRODUCTS_PATH, productPath } from "../router";
import type { RoutePath } from "../router";

// 购物车页 P04（docs/UX.md P04）：读本浏览器购物车（cart.ts），用各行的 SKU 与件数调用计价接口（api/checkout.ts），
// 显示接口返回的每行图片、名称与规格、行小计和商品小计；可改件数、移除。金额只格式化接口返回的整数仙，
// 不在浏览器相加或相乘；不给收货国家（运费在结账时算）。
// 件数可在 (-)(+) 之间直接输入（UX P04「整单上限」，0.17）；计价返回超出整单件数或金额上限时在 cart.item_changed 之下提示，
// cart.checkout 照旧显示（上限判定与下单校验在服务端）。
// 页面打开、改件数、移除与切换语言后重新计价，连续修改时只采用最后一次请求的结果；
// 新结果返回之前仍显示上一次的结果（件数显示购物车里的值），主体标 aria-busy。
// 结账页 P05 已进路由表（/checkout，SHOP-TASK-025）：cart.checkout 按路由规则渲染并链到 /checkout；是否渲染仍只由路由表决定。
// 节日促销活动（UX 0.20 P04「活动」，DESIGN 1.23「节日促销活动」第 4、5、10 条，HANDOFF 0.97，SHOP-TASK-258）：
// 金额行（cart.promo_discount、cart.after_promo、打折行的划线行小计与活动价行小计）与凑单提示照计价接口的暂算结果显示，
// 页面只做显示所需的减法与进度比例，不判定是否参加活动，不碰个人资料。进度条与行角标要用共享读取（usePromotion）的装饰与规则，
// 只在它的活动与计价结果是同一个活动时显示。共享读取的 generation 变化（含倒计时到 0）时重新计价。
// 前台预览（UX 0.20 A12「前台预览」，DESIGN 1.23「节日促销活动」第 11 条，HANDOFF 0.98，SHOP-TASK-279）：在预览上下文里（usePreview 不为 null）
// 计价改经后台预览计价（fetchPreviewQuote，活动 ID 与 CSRF 令牌取自预览上下文），重新计价与 pending 的写法不变；
// 桌面小计栏与手机底部固定栏的 cart.checkout 为禁用的按钮（不跳转），各自之下显示 promo.preview_no_checkout。预览之外一切照旧。

// 购物车的计价请求：预览之外为公开计价接口；在预览上下文里改经后台预览计价，不下单、不碰个人资料。
export function cartQuoteRequest(preview: StorefrontPreview | null): typeof fetchQuote {
  if (preview === null) {
    return fetchQuote;
  }
  const { promotionId, csrfToken } = preview;
  return (language, lines, signal) => fetchPreviewQuote(promotionId, csrfToken, language, lines, signal);
}

function checkoutPath(): RoutePath | null {
  const path: string = "/checkout";
  return isRoutePath(path) ? path : null;
}

function fallbackLang(text: LocalizedText): "en" | undefined {
  return text.english_fallback ? "en" : undefined;
}

function MinusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <path d="M5 12h14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function SlashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M15 4L9 20" />
    </svg>
  );
}

// cart.item_changed 提示条的图形（取自视觉稿 P04-states），读屏忽略。
function AlertIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

// 页面看到的计价结果：首次请求中、失败、已返回（pending 为更新的请求尚未返回、显示的是上一次的结果）。
export type QuoteState = { status: "loading" } | { status: "error" } | { status: "ready"; quote: CheckoutQuote; pending: boolean };

// 最近一次被采用的请求结果；last 是最近一次成功的计价，换了请求、新结果返回之前继续显示。
export interface SettledQuote {
  key: string;
  outcome: QuoteOutcome;
  last: CheckoutQuote | null;
}

// 计价请求的标识：语言、共享读取活动的 generation 与请求体（只有 SKU 与件数）。购物车为空时不请求，为 null。
// generation 变化（活动读取完成或倒计时到 0）时标识随之变化，按现有写法重新计价，新结果返回之前显示上一份结果。
export function quoteKey(language: string, lines: readonly CartLine[], generation = 0): string | null {
  return lines.length === 0 ? null : `${language}|${String(generation)}|${JSON.stringify(quoteRequestBody(lines))}`;
}

export function quoteState(key: string | null, settled: SettledQuote | null): QuoteState {
  if (settled !== null && settled.key === key) {
    return settled.outcome.status === "ready" ? { status: "ready", quote: settled.outcome.quote, pending: false } : { status: "error" };
  }
  return settled?.last ? { status: "ready", quote: settled.last, pending: true } : { status: "loading" };
}

// 任何一行不是正常状态（不可购买、超出限购、库存不足）时显示 cart.item_changed。
export function hasChangedLine(lines: readonly QuoteLine[]): boolean {
  return lines.some((line) => line.status !== "ok");
}

// 同一商品（按接口返回的 product_slug）各行在本浏览器购物车里的件数合计。
export function productTotal(rows: readonly QuoteLine[], lines: readonly CartLine[], productSlug: string): number {
  const quantities = new Map(lines.map((line) => [line.sku, line.quantity]));
  let total = 0;
  for (const row of rows) {
    if (row.status !== "unavailable" && row.product_slug === productSlug) {
      total += quantities.get(row.sku) ?? 0;
    }
  }
  return total;
}

// 数量 (+)：同一商品各行件数合计达到接口返回的 max_per_order、该行已到 1000 件，或购物车全部行合计达到 2,000 件时禁用。
export function canIncrease(rows: readonly QuoteLine[], lines: readonly CartLine[], row: PricedQuoteLine, quantity: number): boolean {
  return (
    quantity < MAX_LINE_QUANTITY && productTotal(rows, lines, row.product_slug) < row.max_per_order && cartItemCount(lines) < MAX_CART_UNITS
  );
}

// 直接输入件数时该行的上限（UX P04「整单上限」）：现有件数 + max(0, min(限购减去该商品各行合计, 2,000 减去全部行合计))。
// 后台调低限购后购物车可能已超出，这时上限就是现有件数。
export function quantityLimit(rows: readonly QuoteLine[], lines: readonly CartLine[], row: PricedQuoteLine, quantity: number): number {
  const productRemaining = row.max_per_order - productTotal(rows, lines, row.product_slug);
  const unitsRemaining = MAX_CART_UNITS - cartItemCount(lines);
  return quantity + Math.max(0, Math.min(productRemaining, unitsRemaining));
}

// 数量输入框生效（回车或离开输入框）后的件数，以及是否显示 common.qty_capped：
// 为空、不全是数字或小于 1 时恢复为原来的件数；不大于现有件数或不超过上限时照收；
// 超过上限时改为上限并显示提示，上限等于现有件数时只恢复为原件数、不提示（原因由 cart.over_limit 说明）。
export interface LineQuantityInput {
  quantity: number;
  capped: boolean;
}

export function lineQuantityInput(text: string, current: number, limit: number): LineQuantityInput {
  const parsed = parseQuantityInput(text);
  if (parsed === null) {
    return { quantity: current, capped: false };
  }
  if (parsed <= current || parsed <= limit) {
    return { quantity: parsed, capped: false };
  }
  return limit > current ? { quantity: limit, capped: true } : { quantity: current, capped: false };
}

// 活动优惠 M8：计价结果的商品级与整单级活动优惠之和；没有活动时为 0。
export function promoDiscount(quote: CheckoutQuote): number {
  const { promotion } = quote;
  return promotion === null ? 0 : promotion.item_discount_sen + promotion.order_discount_sen;
}

// 进度条与行角标所用的活动：共享读取的活动与计价结果的活动相同（promotion_id 一致）时为它；读取中、没有活动或不一致时为 null。
export function matchedPromotion(display: PromotionDisplay | null, quote: PromotionQuote | null): PromotionDisplay | null {
  return display !== null && quote !== null && display.promotion_id === quote.promotion_id ? display : null;
}

// 满额规则的方式（PromotionQuote 的 spend_mode）。
const SPEND_AMOUNT = "amount";
const SPEND_PERCENT = "percent";

// 一条凑单提示：文案键与变量。
export type HintCopy = [CopyKey, CopyVars];

// 凑单提示，每条一行，依次为满额下一档、免运费、买 X 享 Y（按接口顺序）：
// 还有下一档时按 spend_mode 用 cart.hint_tier_amount 或 cart.hint_tier_percent；免运费门槛大于 0 时未达用 cart.hint_free_shipping、已达用 cart.free_shipping_reached；
// 范围内已有商品的买 X 享 Y 规则用 cart.hint_bxgy，{rule} 为共享读取里同 rule_id 规则的角标文字，找不到该规则时这一条不显示。
export function cartHints(language: Language, quote: PromotionQuote, display: PromotionDisplay | null): HintCopy[] {
  const hints: HintCopy[] = [];
  if (quote.spend_next_gap_sen !== null && quote.spend_next_value !== null) {
    const amount = formatSen(quote.spend_next_gap_sen);
    if (quote.spend_mode === SPEND_AMOUNT) {
      hints.push(["cart.hint_tier_amount", { amount, save: formatSen(quote.spend_next_value) }]);
    } else if (quote.spend_mode === SPEND_PERCENT) {
      hints.push(["cart.hint_tier_percent", { amount, percent: quote.spend_next_value }]);
    }
  }
  const shipping = quote.free_shipping_threshold_sen;
  if (shipping !== null && shipping > 0) {
    if (quote.free_shipping_reached) {
      hints.push(["cart.free_shipping_reached", {}]);
    } else if (quote.free_shipping_gap_sen !== null) {
      hints.push(["cart.hint_free_shipping", { amount: formatSen(quote.free_shipping_gap_sen) }]);
    }
  }
  for (const offer of quote.buy_x_get_y) {
    if (offer.units_in_scope <= 0) {
      continue;
    }
    const rule = display?.rules.find((item) => item.rule_id === offer.rule_id);
    const badge = rule === undefined ? null : badgeText(language, rule);
    if (badge !== null) {
      hints.push(["cart.hint_bxgy", { rule: badge, n: offer.units_to_next, y: offer.get_quantity }]);
    }
  }
  return hints;
}

// 进度条（COMPONENTS「FestivalPromotion」Cart progress）：percent 为进度百分比；labels 为已达金额与门槛（仙），满格时为 null。
export interface CartProgress {
  percent: number;
  labels: { reached: number; threshold: number } | null;
}

interface ProgressTarget {
  threshold: number;
  gap: number;
}

// 只画离得最近的一个未达门槛：候选为满额下一档与未达的免运费（门槛大于 0），取差额较小的，相等时取满额；
// 进度百分比＝（门槛 − 差额）× 100 ÷ 门槛，向下取整。没有候选而有满额规则或门槛大于 0 的免运费规则时满格；都没有时为 null（不画）。
export function cartProgress(quote: PromotionQuote): CartProgress | null {
  const targets: ProgressTarget[] = [];
  if (quote.spend_next_gap_sen !== null && quote.spend_next_threshold_sen !== null) {
    targets.push({ threshold: quote.spend_next_threshold_sen, gap: quote.spend_next_gap_sen });
  }
  const shipping = quote.free_shipping_threshold_sen;
  if (shipping !== null && shipping > 0 && !quote.free_shipping_reached && quote.free_shipping_gap_sen !== null) {
    targets.push({ threshold: shipping, gap: quote.free_shipping_gap_sen });
  }
  const nearest = targets.reduce<ProgressTarget | null>((best, target) => (best === null || target.gap < best.gap ? target : best), null);
  if (nearest !== null) {
    const reached = nearest.threshold - nearest.gap;
    return { percent: Math.floor((reached * 100) / nearest.threshold), labels: { reached, threshold: nearest.threshold } };
  }
  if (quote.spend_mode !== null || (shipping !== null && shipping > 0)) {
    return { percent: 100, labels: null };
  }
  return null;
}

// cart_progress 的取值：steps 为 10 格，其余（bar）为进度条。
const PROGRESS_STEPS = "steps";
const STEP_COUNT = 10;

// 进度条与其下的已达金额、门槛（读屏忽略，意思由凑单提示的文字说明）。
function PromoProgress({ variant, progress }: { variant: string; progress: CartProgress }) {
  const t = useCopy();
  const lit = Math.floor(progress.percent / 10);
  return (
    <div className="acs-promo-progress">
      {variant === PROGRESS_STEPS ? (
        <div className="acs-promo-progress__steps" aria-hidden="true">
          {Array.from({ length: STEP_COUNT }, (_, index) => (
            <span key={index} data-on={index < lit ? "" : undefined} />
          ))}
        </div>
      ) : (
        <div className="acs-promo-progress__track" aria-hidden="true">
          <span className="acs-promo-progress__fill" style={{ "--progress": `${String(progress.percent)}%` } as CSSProperties} />
        </div>
      )}
      {progress.labels !== null && (
        <div className="acs-promo-progress__labels" aria-hidden="true">
          <span>{t("common.price_myr", { amount: formatSen(progress.labels.reached) })}</span>
          <span>{t("common.price_myr", { amount: formatSen(progress.labels.threshold) })}</span>
        </div>
      )}
    </div>
  );
}

// 凑单提示的一行：活动图案（读屏忽略）与文字。
function PromoHint({ hint }: { hint: HintCopy }) {
  const t = useCopy();
  return (
    <p className="acs-promo-progress__hint">
      <span className="acs-promo-motif" aria-hidden="true" />
      <span>{t(...hint)}</span>
    </p>
  );
}

// 改件数并写回本浏览器购物车；件数低于 1、高于 1000 或写入失败时返回 false，购物车不变。
export function changeQuantity(storage: CartStorage | null, lines: readonly CartLine[], sku: string, quantity: number): boolean {
  const next = setLineQuantity(lines, sku, quantity);
  return next !== null && saveCart(storage, next);
}

// 移除一行并写回；写入失败时返回 false。
export function removeFromCart(storage: CartStorage | null, lines: readonly CartLine[], sku: string): boolean {
  return saveCart(storage, removeLine(lines, sku));
}

interface LineProps {
  row: QuoteLine;
  rows: readonly QuoteLine[];
  lines: readonly CartLine[];
  // 该行输入框里尚未生效的文字；null 时显示件数。
  draft: string | null;
  // 该行上一次输入超过上限、已改为上限时的上限；null 时不显示 common.qty_capped。
  capped: number | null;
  // 与计价结果一致的共享读取活动（matchedPromotion）；null 时不显示行角标。
  promotion: PromotionDisplay | null;
  onQuantity: (sku: string, quantity: number) => void;
  onDraft: (sku: string, text: string) => void;
  onQuantityInput: (sku: string, result: LineQuantityInput) => void;
  onRemove: (sku: string) => void;
}

// 行下的角标：该行命中规则（promotion_rule_id）且共享读取的活动里有同 rule_id 的规则时，为活动的角标短语与该规则的角标。
function LineBadges({ promotion, ruleId }: { promotion: PromotionDisplay | null; ruleId: number | null }) {
  const { language } = useLanguage();
  const rule = ruleId === null ? undefined : promotion?.rules.find((item) => item.rule_id === ruleId);
  if (promotion === null || rule === undefined) {
    return null;
  }
  return <PromoBadges phrase={promotion.badge} rule={badgeText(language, rule)} style={promotion.card_badge} />;
}

function RemoveButton({ sku, onRemove }: { sku: string; onRemove: (sku: string) => void }) {
  const t = useCopy();
  return (
    <button
      className="acs-btn acs-btn--quiet site-cart__remove"
      type="button"
      onClick={() => {
        onRemove(sku);
      }}
    >
      {t("cart.remove")}
    </button>
  );
}

// 一行：图片、名称与规格、数量（(-) 输入框 (+)）、行小计、移除；输入超过上限时行下 common.qty_capped，超出限购时行下 cart.over_limit。
// 不可购买的行只显示接口返回的 SKU 与件数，只能移除。
// 有活动时（UX 0.20）：该行商品级优惠大于 0 时行小计处先显示划线的行小计、再显示活动价行小计（行小计减该行优惠）；名称与规格之下显示角标。
function CartLineItem({ row, rows, lines, draft, capped, promotion, onQuantity, onDraft, onQuantityInput, onRemove }: LineProps) {
  const t = useCopy();
  const quantity = lines.find((line) => line.sku === row.sku)?.quantity ?? row.quantity;

  if (row.status === "unavailable") {
    return (
      <div className="site-cart__item">
        <div className="site-cart__line">
          <span className="acs-cat__img site-cart__img">
            <PlaceholderShape />
          </span>
          <span className="site-cart__info">
            <span>{row.sku}</span>
          </span>
          <span className="site-cart__qty">
            <output className="acs-num">{quantity}</output>
          </span>
          <RemoveButton sku={row.sku} onRemove={onRemove} />
        </div>
      </div>
    );
  }

  const commitDraft = () => {
    if (draft !== null) {
      onQuantityInput(row.sku, lineQuantityInput(draft, quantity, quantityLimit(rows, lines, row, quantity)));
    }
  };

  return (
    <div className="site-cart__item">
      <div className="site-cart__line">
        <span className="acs-cat__img site-cart__img">
          <CatalogImage src={row.image} />
        </span>
        <span className="site-cart__info">
          <Link to={productPath(row.product_slug)} lang={fallbackLang(row.name)}>
            {row.name.text}
          </Link>
          {row.options.length > 0 && (
            <span className="acs-body-s acs-muted site-cart__options">
              {row.options.map((option, index) => (
                <span key={option.code} className="site-cart__option">
                  {index > 0 && <SlashIcon />}
                  <span lang={fallbackLang(option.value.name)}>{option.value.name.text}</span>
                </span>
              ))}
            </span>
          )}
          <LineBadges promotion={promotion} ruleId={row.promotion_rule_id} />
        </span>
        <div className="acs-stepper site-cart__qty">
          <button
            type="button"
            aria-label={t("common.a11y_qty_decrease")}
            disabled={quantity <= MIN_LINE_QUANTITY}
            onClick={() => {
              onQuantity(row.sku, quantity - 1);
            }}
          >
            <MinusIcon />
          </button>
          <input
            className="acs-input site-cart__qty-input"
            inputMode="numeric"
            aria-label={t("detail.quantity")}
            value={draft ?? String(quantity)}
            onChange={(event) => {
              onDraft(row.sku, event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                commitDraft();
              }
            }}
            onBlur={commitDraft}
          />
          <button
            type="button"
            aria-label={t("common.a11y_qty_increase")}
            disabled={!canIncrease(rows, lines, row, quantity)}
            onClick={() => {
              onQuantity(row.sku, quantity + 1);
            }}
          >
            <PlusIcon />
          </button>
        </div>
        {row.promotion_item_discount_sen > 0 ? (
          <span className="site-cart__price">
            <PromoPrices
              original={t("common.price_myr", { amount: formatSen(row.line_subtotal_sen) })}
              sale={t("common.price_myr", { amount: formatSen(row.line_subtotal_sen - row.promotion_item_discount_sen) })}
            />
          </span>
        ) : (
          <span className="acs-price site-cart__price">{t("common.price_myr", { amount: formatSen(row.line_subtotal_sen) })}</span>
        )}
        <RemoveButton sku={row.sku} onRemove={onRemove} />
      </div>
      {capped !== null && (
        <p className="acs-field__hint site-cart__note" role="status">
          {t("common.qty_capped", { count: capped })}
        </p>
      )}
      {row.status === "over_limit" && <p className="acs-field__error site-cart__note">{t("cart.over_limit", { count: row.max_per_order })}</p>}
    </div>
  );
}

export interface CartViewProps {
  lines: readonly CartLine[];
  quote: QuoteState;
  // 写回本浏览器失败（存储已满、隐私模式等）。
  writeFailed: boolean;
  // 正在输入的那一行与输入框里尚未生效的文字；null 时各行显示件数。
  draft: QuantityDraft | null;
  // 上一次输入超过上限、已改为上限的各行（SKU 到上限），在该行下显示 common.qty_capped。
  capped: ReadonlyMap<string, number>;
  onQuantity: (sku: string, quantity: number) => void;
  onDraft: (sku: string, text: string) => void;
  onQuantityInput: (sku: string, result: LineQuantityInput) => void;
  onRemove: (sku: string) => void;
}

export interface QuantityDraft {
  sku: string;
  text: string;
}

// cart.item_changed 与整单件数、金额上限的提示条（视觉稿 P04-states、P03-P04-caps）。
function CartAlert({ children }: { children: string }) {
  return (
    <div className="acs-alert acs-alert--danger" role="alert">
      <AlertIcon />
      <span>{children}</span>
    </div>
  );
}

// cart.checkout：预览之外按路由规则链到 /checkout；在预览上下文里为禁用的按钮（不跳转），之下显示 promo.preview_no_checkout（视觉稿 A12-states）。
function CheckoutAction({ className }: { className: string }) {
  const t = useCopy();
  const preview = usePreview();
  const checkout = checkoutPath();
  if (preview !== null) {
    return (
      <>
        <button className={className} type="button" disabled>
          {t("cart.checkout")}
        </button>
        <p className="acs-body-s acs-muted site-cart__preview-note">{t("promo.preview_no_checkout")}</p>
      </>
    );
  }
  return checkout === null ? null : (
    <Link className={className} to={checkout}>
      {t("cart.checkout")}
    </Link>
  );
}

export function CartView({ lines, quote, writeFailed, draft, capped, onQuantity, onDraft, onQuantityInput, onRemove }: CartViewProps) {
  const t = useCopy();
  const { language } = useLanguage();
  const { promotion } = usePromotion();
  const session = useMemberSession();
  const preview = usePreview();
  const empty = lines.length === 0;
  const busy = !empty && (quote.status === "loading" || (quote.status === "ready" && quote.pending));

  const head = (
    <div className="site-cart__head">
      <h1 className="acs-display-l">{t("cart.title")}</h1>
      <DemoHint>{t("cart.demo_hint")}</DemoHint>
    </div>
  );

  // 购物车为空、计价返回之前或失败时没有小计栏，[cart.shipping_later] 与 [cart.price_recheck] 放在主体里。
  const notes = (
    <>
      <p className="acs-body-s acs-muted">{t("cart.shipping_later")}</p>
      <p className="acs-body-s acs-muted">{t("cart.price_recheck")}</p>
    </>
  );

  if (empty) {
    return (
      <main className="site-cart">
        {head}
        <div className="site-cart__empty">
          <p className="acs-display-s">{t("cart.empty")}</p>
          <Link className="acs-btn acs-btn--secondary" to={PRODUCTS_PATH}>
            {t("cart.continue")}
          </Link>
          {notes}
        </div>
      </main>
    );
  }

  if (quote.status !== "ready") {
    return (
      <main className="site-cart" aria-busy={busy}>
        {head}
        <div className="site-cart__empty">
          {quote.status === "error" && <ErrorNotice />}
          {notes}
        </div>
      </main>
    );
  }

  // 已从购物车移除的行不等新结果返回就不再显示。
  const inCart = new Set(lines.map((line) => line.sku));
  const rows = quote.quote.lines.filter((row) => inCart.has(row.sku));
  const subtotal = t("common.price_myr", { amount: formatSen(quote.quote.subtotal_sen) });

  // 活动（UX 0.20 P04）：金额行与提示照计价结果；进度条与行角标只用与计价结果一致的共享读取活动。
  const quotePromotion = quote.quote.promotion;
  const shown = matchedPromotion(promotion, quotePromotion);
  const discount = promoDiscount(quote.quote);
  const hasDiscount = discount > 0;
  const afterPromo = t("common.price_myr", { amount: formatSen(quote.quote.subtotal_sen - discount) });
  const hints = quotePromotion === null ? [] : cartHints(language, quotePromotion, promotion);
  const progress = shown === null || quotePromotion === null ? null : cartProgress(quotePromotion);
  const memberNote = hasDiscount && session.kind === "member";

  const discountRows = hasDiscount && (
    <>
      <div className="acs-row">
        <span>{t("cart.promo_discount")}</span>
        <span>{t("common.price_myr", { amount: formatSen(discount) })}</span>
      </div>
      <div className="acs-row">
        <span>{t("cart.after_promo")}</span>
        <span>{afterPromo}</span>
      </div>
    </>
  );
  const nudges = (shown !== null && progress !== null) || hints.length > 0;
  const nudgeContent = (
    <>
      {shown !== null && progress !== null && <PromoProgress variant={shown.cart_progress} progress={progress} />}
      {hints.map((hint, index) => (
        <PromoHint key={index} hint={hint} />
      ))}
    </>
  );
  const note = memberNote && <p className="acs-body-s">{t("cart.member_scheme_note")}</p>;

  return (
    <main className={preview === null ? "site-cart" : "site-cart site-cart--preview"} aria-busy={busy}>
      {head}
      <div className="site-cart__body">
        <div className="site-cart__main">
          {writeFailed && <ErrorNotice />}
          <div className="site-cart__lines">
            {rows.map((row) => (
              <CartLineItem
                key={row.sku}
                row={row}
                rows={rows}
                lines={lines}
                draft={draft?.sku === row.sku ? draft.text : null}
                capped={capped.get(row.sku) ?? null}
                promotion={shown}
                onQuantity={onQuantity}
                onDraft={onDraft}
                onQuantityInput={onQuantityInput}
                onRemove={onRemove}
              />
            ))}
          </div>
          {hasChangedLine(rows) && <CartAlert>{t("cart.item_changed")}</CartAlert>}
          {quote.quote.units_over_limit && <CartAlert>{t("cart.units_over_limit", { count: quote.quote.unit_limit })}</CartAlert>}
          {quote.quote.amount_over_limit && <CartAlert>{t("cart.amount_over_limit", { amount: formatSen(quote.quote.amount_limit_sen) })}</CartAlert>}
          {(nudges || hasDiscount) && (
            <div className="acs-summary site-phone-only site-cart__promo-phone">
              {nudgeContent}
              {discountRows}
              {note}
            </div>
          )}
          <div className="site-phone-only site-cart__notes">
            <p className="acs-body-s acs-muted">{t("cart.shipping_later")}</p>
            <p className="acs-body-s acs-muted">{t("cart.price_recheck")}</p>
          </div>
        </div>
        <aside className="acs-summary site-desktop-only site-cart__summary">
          <div className="acs-row">
            <span>{t("cart.subtotal")}</span>
            <span className="acs-price">{subtotal}</span>
          </div>
          {discountRows}
          {nudges && <div className="site-cart__promo">{nudgeContent}</div>}
          {note}
          <p className="acs-body-s acs-muted">{t("cart.shipping_later")}</p>
          <p className="acs-body-s acs-muted">{t("cart.price_recheck")}</p>
          <CheckoutAction className="acs-btn acs-btn--primary acs-btn--block site-cart__checkout" />
          <Link className="acs-btn acs-btn--quiet site-cart__continue" to={PRODUCTS_PATH}>
            {t("cart.continue")}
          </Link>
        </aside>
      </div>
      <div className="acs site-phone-only site-cart__bar">
        <div className="acs-row">
          <span>{t(hasDiscount ? "cart.after_promo" : "cart.subtotal")}</span>
          <span className="acs-price">{hasDiscount ? afterPromo : subtotal}</span>
        </div>
        <CheckoutAction className="acs-btn acs-btn--primary acs-btn--block" />
      </div>
    </main>
  );
}

export default function CartPage() {
  const { language } = useLanguage();
  const storage = browserStorage();
  const lines = useCartLines(storage);
  // 计价函数在页面打开时按预览上下文选定（cartQuoteRequest），之后的重新计价与作废照 latestQuoter 的现有写法。
  const preview = usePreview();
  const [quoter] = useState(() => latestQuoter(cartQuoteRequest(preview)));
  const [settled, setSettled] = useState<SettledQuote | null>(null);
  const [writeFailed, setWriteFailed] = useState(false);
  const [draft, setDraft] = useState<QuantityDraft | null>(null);
  const [capped, setCapped] = useState<ReadonlyMap<string, number>>(() => new Map());
  const { generation } = usePromotion();
  const key = quoteKey(language, lines, generation);

  // 该行的 (+)(-)、移除或再次输入后收起该行的 common.qty_capped。
  const uncap = (sku: string) => {
    setCapped((previous) => {
      if (!previous.has(sku)) {
        return previous;
      }
      const next = new Map(previous);
      next.delete(sku);
      return next;
    });
  };

  // 购物车内容（同一份存储内容是同一个数组）、语言或共享读取活动的 generation 变了就重新计价；作废的请求由 latestQuoter 中止并丢弃。
  useEffect(() => {
    if (key === null) {
      return undefined;
    }
    void quoter.run(language, lines).then((outcome) => {
      if (outcome !== null) {
        setSettled((previous) => ({
          key,
          outcome,
          last: outcome.status === "ready" ? outcome.quote : (previous?.last ?? null),
        }));
      }
    });
    return () => {
      quoter.cancel();
    };
  }, [key, language, lines, quoter]);

  return (
    <CartView
      lines={lines}
      quote={quoteState(key, settled)}
      writeFailed={writeFailed}
      draft={draft}
      capped={capped}
      onQuantity={(sku, quantity) => {
        setDraft(null);
        uncap(sku);
        setWriteFailed(!changeQuantity(storage, lines, sku, quantity));
      }}
      onDraft={(sku, text) => {
        setDraft({ sku, text });
        uncap(sku);
      }}
      onQuantityInput={(sku, { quantity, capped: wasCapped }) => {
        setDraft(null);
        setCapped((previous) => {
          const next = new Map(previous);
          if (wasCapped) {
            next.set(sku, quantity);
          } else {
            next.delete(sku);
          }
          return next;
        });
        const current = lines.find((line) => line.sku === sku)?.quantity;
        if (quantity !== current) {
          setWriteFailed(!changeQuantity(storage, lines, sku, quantity));
        }
      }}
      onRemove={(sku) => {
        setDraft(null);
        uncap(sku);
        setWriteFailed(!removeFromCart(storage, lines, sku));
      }}
    />
  );
}
