import { useEffect, useId, useState } from "react";

import { productDetailUrl, useCatalog } from "../api/catalog";
import type { FilterOption, LocalizedText, ProductDetail, Remote } from "../api/catalog";
import { MAX_CART_LINES, addToCart, saveCart, useCartLines } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { PlaceholderShape } from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { browserStorage, useCopy, useLanguage } from "../i18n/language";
import { isRoutePath, Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { categorySearch } from "./productListQuery";
import {
  clampQuantity,
  detailPriceCopy,
  initialSelection,
  isValueEnabled,
  purchaseState,
  selectedVariant,
  toggleValue,
} from "./productOptions";
import type { Selection } from "./productOptions";

// 商品详情 P03（docs/UX.md P03）：面包屑、图片、名称（回退英文时 detail.english_only）、价格、规格、数量、
// 每单限购与当日库存、加入购物车、★ detail.demo_hint、描述。数据来自商品详情接口（随当前语言），
// 接口返回之前页面主体为空（没有文字、表单或链接）；接口 404（不存在或未发布）时替换为商品列表，不另显示文字。
// 购物车只在本浏览器（cart.ts）；限购与 20 行上限只用于提示与禁用按钮，下单时由服务端再次校验。
// 桌面缩略图点选切换主图；767px 以下主图横向滑动、描述可折叠、加入按钮固定在底部（site.css）。
// 购物车页 P04 未实现：detail.view_cart 按路由规则不渲染。

// 加入后的提示：已加入、未选全规格、写入本浏览器失败。
export type AddNotice = "added" | "select_all_options" | "error" | null;

// 购物车页的路径；进了路由表才渲染 detail.view_cart。
function cartPath(): RoutePath | null {
  const path: string = "/cart";
  return isRoutePath(path) ? path : null;
}

function fallbackLang(text: LocalizedText): "en" | undefined {
  return text.english_fallback ? "en" : undefined;
}

function SlashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M15 4L9 20" />
    </svg>
  );
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

// 加入后提示条的图形（取自视觉稿 P03 手机），读屏忽略。
function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l3 3 5-6" />
    </svg>
  );
}

// 面包屑：common.nav_shop、分类（链到按该分类筛选的列表）、商品名称；分隔符是图形。
function Breadcrumb({ product }: { product: ProductDetail }) {
  const t = useCopy();
  return (
    <nav className="acs-body-s site-detail__crumbs">
      <Link className="acs-muted" to={PRODUCTS_PATH}>
        {t("common.nav_shop")}
      </Link>
      <SlashIcon />
      <Link className="acs-muted" to={PRODUCTS_PATH} search={categorySearch(product.category.slug)} lang={fallbackLang(product.category.name)}>
        {product.category.name.text}
      </Link>
      <SlashIcon />
      <span aria-current="page" lang={fallbackLang(product.name)}>
        {product.name.text}
      </span>
    </nav>
  );
}

// 图片：桌面主图加缩略图（点选切换），手机为横向滑动的一排主图；每张图的读屏标签为 detail.a11y_image。没有图片时为占位形状。
function Gallery({ images, current, onSelect }: { images: readonly string[]; current: number; onSelect: (index: number) => void }) {
  const t = useCopy();
  const label = (index: number) => t("detail.a11y_image", { n: index + 1, count: images.length });
  const shown = images[current];
  return (
    <div className="site-detail__gallery">
      <div className="acs-cat__img site-desktop-only site-detail__main">
        {shown === undefined ? <PlaceholderShape /> : <img className="site-img" src={shown} alt={label(current)} />}
      </div>
      {images.length > 1 && (
        <div className="site-desktop-only site-detail__thumbs">
          {images.map((src, index) => (
            <button
              key={index}
              className="acs-cat__img site-detail__thumb"
              type="button"
              aria-label={label(index)}
              aria-pressed={index === current}
              onClick={() => {
                onSelect(index);
              }}
            >
              <img className="site-img" src={src} alt="" />
            </button>
          ))}
        </div>
      )}
      <div className="site-phone-only site-detail__slides">
        {images.length === 0 ? (
          <span className="acs-cat__img site-detail__slide">
            <PlaceholderShape />
          </span>
        ) : (
          images.map((src, index) => (
            <span key={index} className="acs-cat__img site-detail__slide">
              <img className="site-img" src={src} alt={label(index)} loading="lazy" />
            </span>
          ))
        )}
      </div>
    </div>
  );
}

interface OptionGroupProps {
  option: FilterOption;
  product: ProductDetail;
  selection: Selection;
  onToggle: (optionCode: string, valueCode: string) => void;
}

// 一组规格：再次点已选的值即取消；与其他组已选的值组不成启用 SKU 的值禁用。
function OptionGroup({ option, product, selection, onToggle }: OptionGroupProps) {
  const nameId = useId();
  return (
    <div className="acs-field">
      <span id={nameId} className="acs-body-s acs-muted" lang={fallbackLang(option.name)}>
        {option.name.text}
      </span>
      <div className="acs-chips" role="group" aria-labelledby={nameId}>
        {option.values.map((value) => (
          <button
            key={value.code}
            className="acs-chip"
            type="button"
            aria-pressed={selection[option.code] === value.code}
            disabled={!isValueEnabled(product, selection, option.code, value.code)}
            lang={fallbackLang(value.name)}
            onClick={() => {
              onToggle(option.code, value.code);
            }}
          >
            {value.name.text}
          </button>
        ))}
      </div>
    </div>
  );
}

export interface ProductDetailViewProps {
  product: ProductDetail;
  selection: Selection;
  quantity: number;
  lines: readonly CartLine[];
  notice: AddNotice;
  image: number;
  onToggle: (optionCode: string, valueCode: string) => void;
  onQuantity: (quantity: number) => void;
  onAdd: () => void;
  onImage: (index: number) => void;
}

export function ProductDetailView({
  product,
  selection,
  quantity,
  lines,
  notice,
  image,
  onToggle,
  onQuantity,
  onAdd,
  onImage,
}: ProductDetailViewProps) {
  const t = useCopy();
  const quantityId = useId();
  const variant = selectedVariant(product, selection);
  const price = detailPriceCopy(product, variant);
  const { remaining, block } = purchaseState(product, variant, lines);
  const shownQuantity = clampQuantity(quantity, remaining);
  const englishOnly = product.name.english_fallback || product.description.english_fallback;
  const cart = cartPath();

  return (
    <main className="site-detail">
      <Breadcrumb product={product} />
      <div className="site-detail__top">
        <Gallery images={product.images} current={image} onSelect={onImage} />
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
              {product.options.map((option) => (
                <OptionGroup key={option.code} option={option} product={product} selection={selection} onToggle={onToggle} />
              ))}
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
                disabled={shownQuantity <= 1}
                onClick={() => {
                  onQuantity(shownQuantity - 1);
                }}
              >
                <MinusIcon />
              </button>
              <output aria-live="polite">{shownQuantity}</output>
              <button
                type="button"
                aria-label={t("common.a11y_qty_increase")}
                disabled={shownQuantity >= remaining}
                onClick={() => {
                  onQuantity(shownQuantity + 1);
                }}
              >
                <PlusIcon />
              </button>
            </div>
          </div>
          <span className="acs-caption">{t("detail.max_per_order", { count: product.max_per_order })}</span>
          {variant && <span className="acs-caption">{t("detail.stock_left", { count: variant.available_stock })}</span>}
          <div className="acs site-detail__buy">
            {notice === "added" && (
              <div className="acs-alert acs-alert--success site-detail__added" role="status">
                <span className="site-detail__added-text">
                  <CheckIcon />
                  {t("detail.added")}
                </span>
                {cart && <Link to={cart}>{t("detail.view_cart")}</Link>}
              </div>
            )}
            {notice === "select_all_options" && (
              <p className="acs-field__error" role="alert">
                {t("detail.select_all_options")}
              </p>
            )}
            {notice === "error" && <ErrorNotice />}
            {block === "limit_reached" && (
              <p className="acs-field__hint" role="status">
                {t("detail.limit_reached", { count: product.max_per_order })}
              </p>
            )}
            {block === "cart_full" && (
              <p className="acs-field__hint" role="status">
                {t("detail.cart_full", { count: MAX_CART_LINES })}
              </p>
            )}
            <button className="acs-btn acs-btn--primary acs-btn--lg acs-btn--block" type="button" disabled={block !== null} onClick={onAdd}>
              {t("detail.add_to_cart")}
            </button>
          </div>
          <DemoHint>{t("detail.demo_hint")}</DemoHint>
        </div>
      </div>
      <section className="site-desktop-only site-detail__description">
        <h2 className="acs-display-s">{t("detail.description")}</h2>
        <p lang={fallbackLang(product.description)}>{product.description.text}</p>
      </section>
      <details className="site-phone-only site-detail__toggle">
        <summary className="acs-field__label">{t("detail.description")}</summary>
        <p lang={fallbackLang(product.description)}>{product.description.text}</p>
      </details>
    </main>
  );
}

// 加入购物车：未选全时提示 detail.select_all_options；按钮禁用（限购已满或购物车已满）时不加入；
// 写入本浏览器失败时显示 common.error_retry，购物车不变。返回加入后要显示的提示。
export function addSelection(
  storage: CartStorage | null,
  product: ProductDetail,
  selection: Selection,
  quantity: number,
  lines: readonly CartLine[],
): AddNotice {
  const variant = selectedVariant(product, selection);
  if (!variant) {
    return "select_all_options";
  }
  const { remaining, block } = purchaseState(product, variant, lines);
  if (block !== null) {
    return null;
  }
  const next = addToCart(lines, { sku: variant.sku, slug: product.slug, quantity: clampQuantity(quantity, remaining) });
  return next !== null && saveCart(storage, next) ? "added" : "error";
}

// 商品不存在或未发布（接口 404）时换成商品列表；其余状态留在本页。
export function detailRedirect(remote: Remote<ProductDetail>): RoutePath | null {
  return remote.status === "not_found" ? PRODUCTS_PATH : null;
}

interface PageState {
  selection: Selection | null;
  quantity: number;
  notice: AddNotice;
  image: number;
}

const INITIAL_STATE: PageState = { selection: null, quantity: 1, notice: null, image: 0 };

export default function ProductDetailPage() {
  const { language } = useLanguage();
  const { params, replace } = useRouter();
  const slug = params.slug ?? "";
  const remote = useCatalog<ProductDetail>(productDetailUrl(language, slug));
  const storage = browserStorage();
  const lines = useCartLines(storage);
  // 换语言重新请求时保留已选规格、数量与提示（规格以 code 记录，与语言无关）。
  const [state, setState] = useState<PageState>(INITIAL_STATE);

  const redirect = detailRedirect(remote);
  useEffect(() => {
    if (redirect !== null) {
      replace(redirect);
    }
  }, [redirect, replace]);

  if (remote.status !== "ready") {
    return <main className="site-detail" aria-busy={remote.status === "loading"}>{remote.status === "error" && <ErrorNotice />}</main>;
  }

  const product = remote.data;
  const selection = state.selection ?? initialSelection(product);
  return (
    <ProductDetailView
      product={product}
      selection={selection}
      quantity={state.quantity}
      lines={lines}
      notice={state.notice}
      image={Math.min(state.image, Math.max(0, product.images.length - 1))}
      onToggle={(optionCode, valueCode) => {
        setState({ ...state, selection: toggleValue(product, selection, optionCode, valueCode), notice: null });
      }}
      onQuantity={(quantity) => {
        setState({ ...state, quantity, notice: null });
      }}
      onAdd={() => {
        const notice = addSelection(storage, product, selection, state.quantity, lines);
        setState({ ...state, selection, notice, quantity: notice === "added" ? 1 : state.quantity });
      }}
      onImage={(image) => {
        setState({ ...state, image });
      }}
    />
  );
}
