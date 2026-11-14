import type { Language } from "../i18n/copy";

// 节日促销活动的公开展示接口（app/api/promotion.py 与 app/services/promotion_display.py，SHOP-TASK-243）。
// 请求只带语言：不带 cookie（credentials: "omit"），不带任何个人资料（DESIGN 1.23「节日促销活动」第 10 条）。
// 规则只给类别与参数，说明由 promoText.ts 套三语文案模板生成；金额是服务端给的整数仙，页面只格式化。

const PROMOTION_BASE = "/api/promotion";

// 满额减或满额折的一档：amount_sen 与 percent 恰有一个不为空。
export interface TierDisplay {
  threshold_sen: number;
  amount_sen: number | null;
  percent: number | null;
}

// 商品级规则适用的一个分类：name 为所选语言（缺译回退英文）。
export interface CategoryDisplay {
  slug: string;
  name: string;
}

// 一条规则的类别与参数；不属于该类别的参数为 null。
export interface RuleDisplay {
  rule_id: number;
  position: number;
  // item_discount / buy_x_get_y / spend_discount / free_shipping
  kind: string;
  // percent / amount / null
  discount_mode: string | null;
  percent: number | null;
  amount_sen: number | null;
  buy_quantity: number | null;
  get_quantity: number | null;
  get_percent: number | null;
  threshold_sen: number | null;
  // 满额减或满额折的各档，按档的排序；其余类别为空列表。
  tiers: TierDisplay[];
  // 商品级规则：启用且有英文名称的分类，按分类 ID 升序；整单级规则为空列表。
  categories: CategoryDisplay[];
  // 规则显式选的商品中有上架的。
  has_products: boolean;
}

export interface PromotionDisplay {
  promotion_id: number;
  // 所选语言的活动文案，缺译回退英文，英文也空即 null。
  title: string | null;
  subtitle: string | null;
  badge: string | null;
  strip: string | null;
  pack: string;
  palette: string;
  ambient: string;
  home_poster: string;
  list_strip: string;
  card_badge: string;
  detail_ribbon: string;
  cart_progress: string;
  // template / image
  poster_mode: string;
  // 上传图模式时两张图的提供路径与替代文字；模板模式时都为 null。
  poster_desktop: string | null;
  poster_phone: string | null;
  poster_alt: string | null;
  // 实际结束时间（提前结束时为提前结束的时刻），带 Z 的 UTC 字符串。
  ends_at: string | null;
  coupon_combinable: boolean;
  points_combinable: boolean;
  // 按 position 升序。
  rules: RuleDisplay[];
}

export interface PromotionResponse {
  // 没有生效活动时为 null。
  promotion: PromotionDisplay | null;
  // 服务端当前时间，带 Z 的 UTC 字符串；倒计时用 ends_at 与它之差。
  server_time: string;
}

export function promotionUrl(language: Language): string {
  return `${PROMOTION_BASE}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 接口返回非 2xx 时的错误，带状态码。
export class PromotionError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`promotion request failed with status ${String(status)}`);
    this.name = "PromotionError";
    this.status = status;
  }
}

// 读一次生效活动；非 2xx 抛 PromotionError，网络错误与中止原样抛出。
export async function fetchPromotion(url: string, signal?: AbortSignal): Promise<PromotionResponse> {
  const response = await fetch(url, {
    method: "GET",
    credentials: "omit",
    headers: { Accept: "application/json" },
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new PromotionError(response.status);
  }
  return (await response.json()) as PromotionResponse;
}
