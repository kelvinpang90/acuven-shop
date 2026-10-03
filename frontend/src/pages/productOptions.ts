import type { ProductDetail, VariantDetail } from "../api/catalog";
import { isCartFullFor, productQuantity } from "../cart";
import type { CartLine } from "../cart";
import type { CopyKey, CopyVars } from "../i18n/copy";
import { formatSen } from "../format";

// 商品详情 P03 的规格选择、价格文案与加入购物车的判断（纯函数，页面与测试共用）。
// 金额只取服务端给的整数仙并格式化，不在浏览器计算。

// 已选的规格：规格名 code → 规格值 code；没选的组不出现。
export type Selection = Readonly<Record<string, string>>;

type Product = Pick<ProductDetail, "options" | "variants">;

function hasSingleVariant(product: Product): boolean {
  return product.variants.length === 1;
}

// 初始不预选；只有一个启用 SKU 的商品直接选定它。
export function initialSelection(product: Product): Selection {
  const [only] = product.variants;
  return hasSingleVariant(product) && only ? { ...only.options } : {};
}

// 某组的某个值可选：有启用 SKU 取这个值，且与其他组已选的值都一致。
export function isValueEnabled(product: Product, selection: Selection, optionCode: string, valueCode: string): boolean {
  return product.variants.some(
    (variant) =>
      variant.options[optionCode] === valueCode &&
      Object.entries(selection).every(([code, value]) => code === optionCode || variant.options[code] === value),
  );
}

// 点一个值：选上它（替换该组原来的值）；再次点已选的值即取消该组的选择（Kelvin 2026-10-03 决定），
// 只有一个启用 SKU 的商品不取消。不可选的值不改变选择。
export function toggleValue(product: Product, selection: Selection, optionCode: string, valueCode: string): Selection {
  if (selection[optionCode] === valueCode) {
    if (hasSingleVariant(product)) {
      return selection;
    }
    return Object.fromEntries(Object.entries(selection).filter(([code]) => code !== optionCode));
  }
  if (!isValueEnabled(product, selection, optionCode, valueCode)) {
    return selection;
  }
  return { ...selection, [optionCode]: valueCode };
}

// 每个规格名都选了值时，取与之一致的启用 SKU；没选全或没有这样的 SKU 时为 null。
export function selectedVariant(product: Product, selection: Selection): VariantDetail | null {
  if (product.options.some((option) => selection[option.code] === undefined)) {
    return null;
  }
  return (
    product.variants.find((variant) =>
      product.options.every((option) => variant.options[option.code] === selection[option.code]),
    ) ?? null
  );
}

// 价格文案：选全后为所选 SKU 的单价；选全前按 SHOP-TASK-015 的起价规则——多于一个启用 SKU 时一律
// list.price_from（取启用 SKU 中最低的单价），只有一个时 common.price_myr。没有启用 SKU 时不显示价格。
export function detailPriceCopy(product: Product, variant: VariantDetail | null): [CopyKey, CopyVars] | null {
  if (variant) {
    return ["common.price_myr", { amount: formatSen(variant.price_sen) }];
  }
  if (product.variants.length === 0) {
    return null;
  }
  const lowest = Math.min(...product.variants.map((item) => item.price_sen));
  return [product.variants.length > 1 ? "list.price_from" : "common.price_myr", { amount: formatSen(lowest) }];
}

// 加入购物车按钮的状态。
export type PurchaseBlock = "limit_reached" | "cart_full" | null;

export interface PurchaseState {
  // 数量 (+) 的上限：限购件数减去购物车中同一商品各行件数之和，不小于 0。
  remaining: number;
  // 按钮禁用的原因；null 时可点。
  block: PurchaseBlock;
}

export function purchaseState(
  product: Pick<ProductDetail, "slug" | "max_per_order">,
  variant: VariantDetail | null,
  lines: readonly CartLine[],
): PurchaseState {
  const remaining = Math.max(0, product.max_per_order - productQuantity(lines, product.slug));
  if (remaining === 0) {
    return { remaining, block: "limit_reached" };
  }
  if (variant && isCartFullFor(lines, variant.sku)) {
    return { remaining, block: "cart_full" };
  }
  return { remaining, block: null };
}

// 显示的数量：在 1 与剩余件数之间（剩余为 0 时仍显示 1，按钮已禁用）。
export function clampQuantity(quantity: number, remaining: number): number {
  return Math.min(Math.max(1, quantity), Math.max(1, remaining));
}
