import type { ProductSummary } from "../api/catalog";
import type { CopyKey, CopyVars } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { formatSen } from "../format";

// 商品卡（P01「精选商品」与 P02 共用）：首张图片、名称、价格，当日售罄时加 list.out_of_stock。
// 价格只显示 MYR，不显示参考外币（UX Q9）。指向商品详情 P03 的链接按路由规则在详情页实现前不渲染，卡片不是链接。

// 图片为空时的占位形状：取自视觉稿 P01「按分类浏览」的图形，读屏忽略。
export function PlaceholderShape() {
  return (
    <svg viewBox="0 0 100 100" aria-hidden="true">
      <path className="ph-fill" d="M20 38 h60 l-7 52 h-46z" />
      <path d="M36 38 q0-22 14-22 q14 0 14 22" fill="none" stroke="currentColor" strokeWidth="5" />
    </svg>
  );
}

// 商品或分类图片：旁边总有名称，图片本身不再读出（alt 为空）；没有图片时显示占位形状。
export function CatalogImage({ src }: { src: string | null }) {
  return src ? <img className="site-img" src={src} alt="" loading="lazy" /> : <PlaceholderShape />;
}

// UX P01、P02 M1：商品有多于一个启用规格时一律「起」，即使各规格同价；只有一个规格时用 common.price_myr。
export function priceCopy(product: Pick<ProductSummary, "min_price_sen" | "has_multiple_variants">): [CopyKey, CopyVars] {
  const amount = formatSen(product.min_price_sen);
  return [product.has_multiple_variants ? "list.price_from" : "common.price_myr", { amount }];
}

export default function ProductCard({ product }: { product: ProductSummary }) {
  const t = useCopy();
  const [priceKey, priceVars] = priceCopy(product);
  return (
    <div className={product.sold_out_today ? "acs-pcard acs-pcard--oos" : "acs-pcard"}>
      <span className="acs-pcard__img">
        <CatalogImage src={product.image} />
      </span>
      <span className="acs-pcard__name" lang={product.name.english_fallback ? "en" : undefined}>
        {product.name.text}
      </span>
      <span className="acs-pcard__price">{t(priceKey, priceVars)}</span>
      {product.sold_out_today && (
        <span>
          <span className="acs-tag acs-tag--neutral">{t("list.out_of_stock")}</span>
        </span>
      )}
    </div>
  );
}
