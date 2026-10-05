import { useEffect, useState } from "react";

import { latestQuoter, quoteRequestBody } from "../api/checkout";
import type { CheckoutQuote, PricedQuoteLine, QuoteLine, QuoteOutcome } from "../api/checkout";
import type { LocalizedText } from "../api/catalog";
import { MAX_LINE_QUANTITY, MIN_LINE_QUANTITY, removeLine, saveCart, setLineQuantity, useCartLines } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { CatalogImage, PlaceholderShape } from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import { browserStorage, useCopy, useLanguage } from "../i18n/language";
import { isRoutePath, Link, PRODUCTS_PATH, productPath } from "../router";
import type { RoutePath } from "../router";

// 购物车页 P04（docs/UX.md P04）：读本浏览器购物车（cart.ts），用各行的 SKU 与件数调用计价接口（api/checkout.ts），
// 显示接口返回的每行图片、名称与规格、行小计和商品小计；可改件数、移除。金额只格式化接口返回的整数仙，
// 不在浏览器相加或相乘；不给收货国家（运费在结账时算）。
// 页面打开、改件数、移除与切换语言后重新计价，连续修改时只采用最后一次请求的结果；
// 新结果返回之前仍显示上一次的结果（件数显示购物车里的值），主体标 aria-busy。
// 结账页 P05 已进路由表（/checkout，SHOP-TASK-025）：cart.checkout 按路由规则渲染并链到 /checkout；是否渲染仍只由路由表决定。

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

// 计价请求的标识：语言与请求体（只有 SKU 与件数）。购物车为空时不请求，为 null。
export function quoteKey(language: string, lines: readonly CartLine[]): string | null {
  return lines.length === 0 ? null : `${language}|${JSON.stringify(quoteRequestBody(lines))}`;
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

// 数量 (+)：同一商品各行件数合计达到接口返回的 max_per_order，或该行已到 99 件时禁用。
export function canIncrease(rows: readonly QuoteLine[], lines: readonly CartLine[], row: PricedQuoteLine, quantity: number): boolean {
  return quantity < MAX_LINE_QUANTITY && productTotal(rows, lines, row.product_slug) < row.max_per_order;
}

// 改件数并写回本浏览器购物车；件数低于 1、高于 99 或写入失败时返回 false，购物车不变。
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
  onQuantity: (sku: string, quantity: number) => void;
  onRemove: (sku: string) => void;
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

// 一行：图片、名称与规格、数量、行小计、移除；超出限购时行下 cart.over_limit。
// 不可购买的行只显示接口返回的 SKU 与件数，只能移除。
function CartLineItem({ row, rows, lines, onQuantity, onRemove }: LineProps) {
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
          <output aria-live="polite">{quantity}</output>
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
        <span className="acs-price site-cart__price">{t("common.price_myr", { amount: formatSen(row.line_subtotal_sen) })}</span>
        <RemoveButton sku={row.sku} onRemove={onRemove} />
      </div>
      {row.status === "over_limit" && <p className="acs-field__error site-cart__note">{t("cart.over_limit", { count: row.max_per_order })}</p>}
    </div>
  );
}

export interface CartViewProps {
  lines: readonly CartLine[];
  quote: QuoteState;
  // 写回本浏览器失败（存储已满、隐私模式等）。
  writeFailed: boolean;
  onQuantity: (sku: string, quantity: number) => void;
  onRemove: (sku: string) => void;
}

export function CartView({ lines, quote, writeFailed, onQuantity, onRemove }: CartViewProps) {
  const t = useCopy();
  const checkout = checkoutPath();
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

  return (
    <main className="site-cart" aria-busy={busy}>
      {head}
      <div className="site-cart__body">
        <div className="site-cart__main">
          {writeFailed && <ErrorNotice />}
          <div className="site-cart__lines">
            {rows.map((row) => (
              <CartLineItem key={row.sku} row={row} rows={rows} lines={lines} onQuantity={onQuantity} onRemove={onRemove} />
            ))}
          </div>
          {hasChangedLine(rows) && (
            <div className="acs-alert acs-alert--danger" role="alert">
              <AlertIcon />
              <span>{t("cart.item_changed")}</span>
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
          <p className="acs-body-s acs-muted">{t("cart.shipping_later")}</p>
          <p className="acs-body-s acs-muted">{t("cart.price_recheck")}</p>
          {checkout && (
            <Link className="acs-btn acs-btn--primary acs-btn--block site-cart__checkout" to={checkout}>
              {t("cart.checkout")}
            </Link>
          )}
          <Link className="acs-btn acs-btn--quiet site-cart__continue" to={PRODUCTS_PATH}>
            {t("cart.continue")}
          </Link>
        </aside>
      </div>
      <div className="acs site-phone-only site-cart__bar">
        <div className="acs-row">
          <span>{t("cart.subtotal")}</span>
          <span className="acs-price">{subtotal}</span>
        </div>
        {checkout && (
          <Link className="acs-btn acs-btn--primary acs-btn--block" to={checkout}>
            {t("cart.checkout")}
          </Link>
        )}
      </div>
    </main>
  );
}

export default function CartPage() {
  const { language } = useLanguage();
  const storage = browserStorage();
  const lines = useCartLines(storage);
  const [quoter] = useState(() => latestQuoter());
  const [settled, setSettled] = useState<SettledQuote | null>(null);
  const [writeFailed, setWriteFailed] = useState(false);
  const key = quoteKey(language, lines);

  // 购物车内容（同一份存储内容是同一个数组）或语言变了就重新计价；作废的请求由 latestQuoter 中止并丢弃。
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
      onQuantity={(sku, quantity) => {
        setWriteFailed(!changeQuantity(storage, lines, sku, quantity));
      }}
      onRemove={(sku) => {
        setWriteFailed(!removeFromCart(storage, lines, sku));
      }}
    />
  );
}
