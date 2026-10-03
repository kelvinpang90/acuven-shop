import type { CartLine } from "../cart";
import type { Language } from "../i18n/copy";
import type { LocalizedText } from "./catalog";

// 购物车计价接口 POST /api/checkout/quote（app/api/checkout.py，SHOP-TASK-009 与 SHOP-TASK-016）。
// 购物车页 P04 只送每行的 SKU 与件数：不带收货国家（运费在结账时算）、不带任何价格，不带 cookie。
// 金额全部是服务端按当前价格算好的整数仙，页面只格式化，不相加或相乘（UX P04 的 M1、M2）。

const QUOTE_URL = "/api/checkout/quote";

// 不可购买（SKU 不存在、停用或商品未发布）：接口只给 SKU、件数与状态。
export interface UnavailableQuoteLine {
  sku: string;
  quantity: number;
  status: "unavailable";
}

export interface SelectedOption {
  code: string;
  name: LocalizedText;
  value: { code: string; name: LocalizedText };
}

export interface PricedQuoteLine {
  sku: string;
  quantity: number;
  status: "ok" | "over_limit" | "insufficient_stock";
  available_stock: number | null;
  // 该商品的每单限购件数（同一商品各行件数合计的上限）。
  max_per_order: number;
  product_slug: string;
  name: LocalizedText;
  // 按规格名的排列序号。
  options: SelectedOption[];
  image: string | null;
  // MYR 整数仙。
  unit_price_sen: number;
  line_subtotal_sen: number;
}

export type QuoteLine = UnavailableQuoteLine | PricedQuoteLine;

// 不给收货国家时 shipping、total_sen、fx_reference 都为 null，can_place_order 为 false；购物车页不用这几项。
export interface CheckoutQuote {
  lines: QuoteLine[];
  // 商品小计：服务端给出的正常、超出限购与库存不足各行行小计之和。
  subtotal_sen: number;
}

export function quoteUrl(language: Language): string {
  return `${QUOTE_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 请求体：每行只有 sku 与 quantity，顺序同购物车；slug 与其他字段不送。
export function quoteRequestBody(lines: readonly CartLine[]): { lines: { sku: string; quantity: number }[] } {
  return { lines: lines.map((line) => ({ sku: line.sku, quantity: line.quantity })) };
}

export class QuoteError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`quote request failed with status ${String(status)}`);
    this.name = "QuoteError";
    this.status = status;
  }
}

// 发一次计价请求；非 2xx 或网络错误时抛错，由页面显示 common.error_retry。
export async function fetchQuote(language: Language, lines: readonly CartLine[], signal?: AbortSignal): Promise<CheckoutQuote> {
  const response = await fetch(quoteUrl(language), {
    method: "POST",
    credentials: "omit",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(quoteRequestBody(lines)),
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new QuoteError(response.status);
  }
  return (await response.json()) as CheckoutQuote;
}

export type QuoteOutcome = { status: "ready"; quote: CheckoutQuote } | { status: "error" };

export interface LatestQuoter {
  // 发一次计价；之后又发了新的请求（或已 cancel）时，这次的结果作废，返回 null。
  run: (language: Language, lines: readonly CartLine[]) => Promise<QuoteOutcome | null>;
  cancel: () => void;
}

// 连续修改件数时只采用最后一次请求的结果：新请求发出时中止上一个，上一个无论先后返回都不再采用。
export function latestQuoter(request: typeof fetchQuote = fetchQuote): LatestQuoter {
  let current: AbortController | null = null;
  const cancel = () => {
    current?.abort();
    current = null;
  };
  const run = async (language: Language, lines: readonly CartLine[]): Promise<QuoteOutcome | null> => {
    cancel();
    const controller = new AbortController();
    current = controller;
    let outcome: QuoteOutcome;
    try {
      outcome = { status: "ready", quote: await request(language, lines, controller.signal) };
    } catch {
      outcome = { status: "error" };
    }
    return current === controller ? outcome : null;
  };
  return { run, cancel };
}
