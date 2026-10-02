import type { ProductDetail, ProductVariant } from "../api/catalog";
import { isCartFullFor, remainingAllowance } from "../cart";
import type { CartLine } from "../cart";
import { priceCopy as startPriceCopy } from "../components/ProductCard";
import { formatSen } from "../format";
import type { CopyKey, CopyVars } from "../i18n/copy";

// 商品详情 P03 的规格选择（docs/UX.md P03；docs/design/COMPONENTS.md「Options」）。
// 选择是规格名 code → 规格值 code；只在页面内存里，不进网址也不进存储。
// 规格值可选与否只看详情接口给出的启用 SKU：与已选的其他规格值组不成任何启用 SKU 的值禁用。
// 价格与库存只显示接口的数字，不在浏览器计算金额。

export type Selection = Readonly<Record<string, string>>;

type OptionsShape = Pick<ProductDetail, "options" | "variants">;

// 初始不预选；只有一个启用 SKU 时直接选定它。
export function initialSelection(product: OptionsShape): Selection {
  const [only, ...rest] = product.variants;
  return only !== undefined && rest.length === 0 ? { ...only.options } : {};
}

// 该 SKU 与选择中除 exceptOption 以外的每一项都一致。
function agrees(variant: ProductVariant, selection: Selection, exceptOption?: string): boolean {
  return Object.entries(selection).every(([option, value]) => option === exceptOption || variant.options[option] === value);
}

// 规格值可选：存在一个启用 SKU 取这个值，且与其他规格名下已选的值都一致。
export function isValueAvailable(product: OptionsShape, selection: Selection, option: string, value: string): boolean {
  return product.variants.some((variant) => variant.options[option] === value && agrees(variant, selection, option));
}

// 点一个规格值：未选时选上（替换同一规格名下原来的值），已选时取消。
export function toggleValue(selection: Selection, option: string, value: string): Selection {
  if (selection[option] === value) {
    return Object.fromEntries(Object.entries(selection).filter(([key]) => key !== option));
  }
  return { ...selection, [option]: value };
}

// 每个规格名都已选。
export function isComplete(product: OptionsShape, selection: Selection): boolean {
  return product.options.every((option) => selection[option.code] !== undefined);
}

// 选全后对应的启用 SKU；未选全或组不成启用 SKU 时为 null。
export function selectedVariant(product: OptionsShape, selection: Selection): ProductVariant | null {
  if (!isComplete(product, selection)) {
    return null;
  }
  return product.variants.find((variant) => agrees(variant, selection)) ?? null;
}

// 加入按钮禁用的原因（UX P03 0.6），按先后：该商品已达每单限购；购物车已有 20 行且所选 SKU 不在其中。
export type AddBlock = "limit_reached" | "cart_full";

export interface PurchaseState {
  // 还能加入的件数：每单限购减去购物车中同一商品（按 slug）各行件数之和，最少 0。
  remaining: number;
  // 显示与加入的件数：访客选的件数限制在 1 到 remaining 之间（remaining 为 0 时仍显示 1）。
  quantity: number;
  block: AddBlock | null;
}

export function purchaseState(
  product: Pick<ProductDetail, "slug" | "max_per_order">,
  variant: ProductVariant | null,
  lines: readonly CartLine[],
  quantity: number,
): PurchaseState {
  const remaining = remainingAllowance(lines, product.slug, product.max_per_order);
  const block: AddBlock | null =
    remaining === 0 ? "limit_reached" : variant && isCartFullFor(lines, variant.sku) ? "cart_full" : null;
  return { remaining, quantity: Math.min(Math.max(1, quantity), Math.max(1, remaining)), block };
}

// 点加入购物车的结果：按钮禁用时什么也不做（null）；未选全时提示 detail.select_all_options；否则是要加入的一行。
export function addRequest(
  product: Pick<ProductDetail, "slug">,
  variant: ProductVariant | null,
  state: PurchaseState,
): CartLine | "select_all_options" | null {
  if (state.block !== null) {
    return null;
  }
  if (!variant) {
    return "select_all_options";
  }
  return { sku: variant.sku, slug: product.slug, quantity: state.quantity };
}

// 价格文案：选全后是所选 SKU 的单价；选全前按 SHOP-TASK-015 的起价规则——
// 多于一个启用 SKU 时一律 list.price_from（即使同价），只有一个时 common.price_myr。没有启用 SKU 时不显示。
export function priceCopy(product: OptionsShape, variant: ProductVariant | null): [CopyKey, CopyVars] | null {
  if (variant) {
    return ["common.price_myr", { amount: formatSen(variant.price_sen) }];
  }
  if (product.variants.length === 0) {
    return null;
  }
  return startPriceCopy({
    min_price_sen: Math.min(...product.variants.map((item) => item.price_sen)),
    has_multiple_variants: product.variants.length > 1,
  });
}
