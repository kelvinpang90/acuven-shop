import { useEffect, useId, useRef, useState } from "react";
import type { RefObject } from "react";

import { productUrl, useCatalogItem } from "../api/catalog";
import type { ProductDetail, RemoteItem } from "../api/catalog";
import { CART_MAX_LINES, useCart } from "../cart";
import type { CartLine } from "../cart";
import { PlaceholderShape } from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { useCopy, useLanguage } from "../i18n/language";
import { isRoutePath, Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RouteHref } from "../router";
import {
  addRequest,
  initialSelection,
  isValueAvailable,
  priceCopy,
  purchaseState,
  selectedVariant,
  toggleValue,
} from "./productOptions";
import type { Selection } from "./productOptions";
import { categorySearch } from "./productListQuery";

// 商品详情 P03（docs/UX.md P03；样式按 docs/design/pages/P03-desktop.html、P03-phone.html）：
// 面包屑（common.nav_shop / 分类 / 名称）、图片、名称与 detail.english_only、价格、规格、数量、限购与库存、
// 加入购物车与其提示、★ detail.demo_hint、描述。数据来自商品详情接口（随当前语言），接口 404 时换成商品列表。
// 桌面：缩略图点选切换主图。767px 以下：主图左右滑动、描述可折叠、加入购物车按钮固定在底部；
// 两套图片与两种描述都在页面里，由 site.css 按宽度只显示其一。
// 每单限购与 20 行上限只按本浏览器购物车（cart.ts）提示并禁用按钮，下单时服务端再校验。

// 购物车页 P04 的路径：按路由规则，在它进路由表之前 detail.view_cart 不渲染。
const CART_PATH: string = "/cart";

export type Notice = "added" | "select_all_options" | null;

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
      <path d="M5 12h14M12 5v14" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l3 3 5-6" />
    </svg>
  );
}

// 面包屑分隔符：图形，不是文字。
function CrumbSeparator() {
  return (
    <svg width="10" height="14" viewBox="0 0 10 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M8 1L2 13" />
    </svg>
  );
}

// 手机上加入购物车一栏固定在屏幕底部，高度随提示增减：量出高度写到根元素的 --site-detail-buy-height，
// site.css 据此在整页底部留出等高空白，免得栏盖住页面最后的内容。离开详情页时撤掉。
const BUY_HEIGHT_VAR = "--site-detail-buy-height";

function useBuyBarHeight(bar: RefObject<HTMLDivElement | null>): void {
  useEffect(() => {
    const element = bar.current;
    if (element === null || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const root = document.documentElement;
    const update = () => {
      root.style.setProperty(BUY_HEIGHT_VAR, `${String(Math.ceil(element.getBoundingClientRect().height))}px`);
    };
    const observer = new ResizeObserver(update);
    observer.observe(element);
    update();
    return () => {
      observer.disconnect();
      root.style.removeProperty(BUY_HEIGHT_VAR);
    };
  }, [bar]);
}

function fallbackLang(text: { english_fallback: boolean }): "en" | undefined {
  return text.english_fallback ? "en" : undefined;
}

// 图片：桌面主图加缩略图，手机可左右滑动的一排；每张图的读屏标签是 detail.a11y_image。没有图片时显示占位形状。
function Gallery({ images }: { images: readonly string[] }) {
  const t = useCopy();
  const [active, setActive] = useState(0);
  const count = images.length;
  const current = Math.min(active, Math.max(0, count - 1));
  const label = (index: number) => t("detail.a11y_image", { n: index + 1, count });
  const mainImage = images[current];
  return (
    <>
      <div className="site-desktop-only site-detail__gallery">
        <span className="acs-cat__img site-detail__tile">
          {mainImage === undefined ? <PlaceholderShape /> : <img className="site-img" src={mainImage} alt={label(current)} />}
        </span>
        {count > 1 && (
          <div className="site-detail__thumbs">
            {images.map((src, index) => (
              <button
                key={`${String(index)}-${src}`}
                className="acs-chip site-detail__thumb"
                type="button"
                aria-pressed={index === current}
                aria-label={label(index)}
                onClick={() => {
                  setActive(index);
                }}
              >
                <span className="acs-cat__img site-detail__tile">
                  <img className="site-img" src={src} alt="" loading="lazy" />
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="site-phone-only site-detail__slides">
        {count === 0 ? (
          <span className="acs-cat__img site-detail__tile site-detail__slide">
            <PlaceholderShape />
          </span>
        ) : (
          images.map((src, index) => (
            <span key={`${String(index)}-${src}`} className="acs-cat__img site-detail__tile site-detail__slide">
              <img className="site-img" src={src} alt={label(index)} loading={index === 0 ? undefined : "lazy"} />
            </span>
          ))
        )}
      </div>
    </>
  );
}

export interface ProductDetailViewProps {
  product: ProductDetail;
  // 本浏览器购物车的当前内容。
  cart: readonly CartLine[];
  selection: Selection;
  // 访客选的件数；显示时按限购剩余件数收窄。
  quantity: number;
  notice: Notice;
  onToggle: (option: string, value: string) => void;
  onQuantity: (quantity: number) => void;
  onAdd: () => void;
}

export function ProductDetailView({
  product,
  cart,
  selection,
  quantity,
  notice,
  onToggle,
  onQuantity,
  onAdd,
}: ProductDetailViewProps) {
  const t = useCopy();
  const baseId = useId();
  const quantityId = `${baseId}-quantity`;
  const blockId = `${baseId}-block`;
  const buyBar = useRef<HTMLDivElement>(null);
  useBuyBarHeight(buyBar);
  const variant = selectedVariant(product, selection);
  const purchase = purchaseState(product, variant, cart, quantity);
  const price = priceCopy(product, variant);
  // UX P03：当前语言缺少商品文案、回退英文时在名称旁标出。
  const englishOnly = product.name.english_fallback || product.description.english_fallback;

  return (
    <main className="site-detail">
      <nav className="acs-body-s acs-muted site-desktop-only site-detail__crumbs">
        <Link to={PRODUCTS_PATH}>{t("common.nav_shop")}</Link>
        <CrumbSeparator />
        <Link to={PRODUCTS_PATH} search={categorySearch(product.category.slug)} lang={fallbackLang(product.category.name)}>
          {product.category.name.text}
        </Link>
        <CrumbSeparator />
        <span aria-current="page" lang={fallbackLang(product.name)}>
          {product.name.text}
        </span>
      </nav>

      <Gallery images={product.images} />

      <div className="site-detail__info">
        <div className="site-detail__title">
          <h1 className="acs-display-l" lang={fallbackLang(product.name)}>
            {product.name.text}
          </h1>
          {englishOnly && <span className="acs-tag acs-tag--outline">{t("detail.english_only")}</span>}
        </div>
        {price && <span className="acs-price acs-price--l">{t(...price)}</span>}

        {product.options.length > 0 && (
          <div className="site-detail__options">
            <span className="acs-field__label">{t("detail.options")}</span>
            {product.options.map((option, optionIndex) => {
              const labelId = `${baseId}-option-${String(optionIndex)}`;
              return (
                <div key={option.code} className="acs-field" role="group" aria-labelledby={labelId}>
                  <span id={labelId} className="acs-body-s acs-muted" lang={fallbackLang(option.name)}>
                    {option.name.text}
                  </span>
                  <div className="acs-chips">
                    {option.values.map((value) => {
                      const pressed = selection[option.code] === value.code;
                      return (
                        <button
                          key={value.code}
                          className="acs-chip"
                          type="button"
                          aria-pressed={pressed}
                          disabled={!pressed && !isValueAvailable(product, selection, option.code, value.code)}
                          lang={fallbackLang(value.name)}
                          onClick={() => {
                            onToggle(option.code, value.code);
                          }}
                        >
                          {value.name.text}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="site-detail__quantity">
          <span id={quantityId} className="acs-field__label">
            {t("detail.quantity")}
          </span>
          <div className="acs-stepper" role="group" aria-labelledby={quantityId}>
            <button
              type="button"
              aria-label={t("common.a11y_qty_decrease")}
              disabled={purchase.quantity <= 1}
              onClick={() => {
                onQuantity(purchase.quantity - 1);
              }}
            >
              <MinusIcon />
            </button>
            <output>{purchase.quantity}</output>
            <button
              type="button"
              aria-label={t("common.a11y_qty_increase")}
              disabled={purchase.quantity >= purchase.remaining}
              onClick={() => {
                onQuantity(purchase.quantity + 1);
              }}
            >
              <PlusIcon />
            </button>
          </div>
        </div>
        <span className="acs-caption">{t("detail.max_per_order", { count: product.max_per_order })}</span>
        {variant && <span className="acs-caption">{t("detail.stock_left", { count: variant.available_stock })}</span>}
      </div>

      <div ref={buyBar} className="acs site-detail__buy">
        {notice === "added" && (
          <div className="acs-alert acs-alert--success site-detail__added" role="status">
            <span className="site-detail__added-text">
              <CheckIcon />
              {t("detail.added")}
            </span>
            {isRoutePath(CART_PATH) && <Link to={CART_PATH}>{t("detail.view_cart")}</Link>}
          </div>
        )}
        {notice === "select_all_options" && purchase.block === null && (
          <p className="acs-alert acs-alert--danger site-notice" role="alert">
            {t("detail.select_all_options")}
          </p>
        )}
        {purchase.block === "limit_reached" && (
          <p id={blockId} className="acs-alert acs-alert--info site-notice" role="status">
            {t("detail.limit_reached", { count: product.max_per_order })}
          </p>
        )}
        {purchase.block === "cart_full" && (
          <p id={blockId} className="acs-alert acs-alert--info site-notice" role="status">
            {t("detail.cart_full", { count: CART_MAX_LINES })}
          </p>
        )}
        <button
          className="acs-btn acs-btn--primary acs-btn--lg acs-btn--block"
          type="button"
          disabled={purchase.block !== null}
          aria-describedby={purchase.block === null ? undefined : blockId}
          onClick={onAdd}
        >
          {t("detail.add_to_cart")}
        </button>
      </div>

      <DemoHint>{t("detail.demo_hint")}</DemoHint>

      <section className="site-desktop-only site-detail__description">
        <h2 className="acs-display-s">{t("detail.description")}</h2>
        <p lang={fallbackLang(product.description)}>{product.description.text}</p>
      </section>
      <details className="site-phone-only site-detail__more">
        <summary className="acs-field__label">{t("detail.description")}</summary>
        <p lang={fallbackLang(product.description)}>{product.description.text}</p>
      </details>
    </main>
  );
}

// 还没取到数据，或商品不存在（正被换成商品列表）时：空的页面主体，不另显示文字。请求失败时显示 common.error_retry。
export function ProductDetailPending({ failed }: { failed: boolean }) {
  return (
    <main className="site-detail" aria-busy={!failed}>
      {failed && <ErrorNotice />}
    </main>
  );
}

// UX P03 与 SHOP-TASK-017：商品不存在或未发布（接口 404）时替换为商品列表页，不新增历史记录。
export function leaveIfMissing(remote: RemoteItem<unknown>, replace: (to: RouteHref) => void): void {
  if (remote.status === "not_found") {
    replace(PRODUCTS_PATH);
  }
}

export default function ProductDetailPage() {
  const { language } = useLanguage();
  const { params, replace } = useRouter();
  const slug = params.slug ?? "";
  const remote = useCatalogItem<ProductDetail>(slug === "" ? null : productUrl(language, slug));
  const cart = useCart();
  // null：访客还没点过规格，用初始选择（只有一个启用 SKU 时直接选定）。切换语言重新取数时选择保留。
  const [choice, setChoice] = useState<Selection | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    leaveIfMissing(remote, replace);
  }, [remote, replace]);

  if (remote.status !== "ready") {
    return <ProductDetailPending failed={remote.status === "error"} />;
  }

  const product = remote.data;
  const selection = choice ?? initialSelection(product);

  const add = () => {
    const variant = selectedVariant(product, selection);
    const request = addRequest(product, variant, purchaseState(product, variant, cart.lines, quantity));
    if (request === "select_all_options") {
      setNotice("select_all_options");
    } else if (request !== null) {
      setNotice(cart.add(request).ok ? "added" : null);
    }
  };

  return (
    <ProductDetailView
      product={product}
      cart={cart.lines}
      selection={selection}
      quantity={quantity}
      notice={notice}
      onToggle={(option, value) => {
        setChoice(toggleValue(selection, option, value));
        setNotice(null);
      }}
      onQuantity={(next) => {
        setQuantity(next);
        setNotice(null);
      }}
      onAdd={add}
    />
  );
}
