import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Ref } from "react";

import { fetchCatalog, productsUrl } from "../api/catalog";
import type { ProductPage, Remote } from "../api/catalog";
import type { PromotionDisplay, RuleDisplay } from "../api/promotion";
import ProductCard from "../components/ProductCard";
import { PromoRuleList, PromoStrip } from "../components/PromoParts";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { KIND_BUY_X_GET_Y, KIND_ITEM_DISCOUNT, ruleTexts, scopeText } from "../promoText";
import { usePromotion } from "../promotion";
import { Link, PRODUCTS_PATH } from "../router";
import type { MoreResults } from "./ProductListPage";
import { DEFAULT_PRODUCT_LIST_QUERY } from "./productListQuery";

// 活动专题页 P15（docs/UX.md 0.20 P15，DESIGN 1.23「节日促销活动」第 10 条，HANDOFF 0.97 的落实写法）。
// 活动内容只取 usePromotion 的数据，商品只取目录接口（sale=true）的数据；不计算金额，不碰个人资料。
// 读取中只显示 ★ promo.demo_hint。没有活动（含倒计时到 0 后重新读取的结果）时显示 promo.ended 与去 P02 的 promo.browse_all。
// 有活动时依次为 ★ 提示、活动横条（活动标题、倒计时，不放 promo.view_all）、副标题（为空不显示）、规则区与商品区；
// 只有整单级规则时商品区换成 promo.order_level_only 与 promo.browse_all。
// 商品区的页码只在页面状态里、不进地址（P02 的页码组件写死 /products 的地址，这里不复用）：桌面为页码按钮，
// 手机为 list.load_more，两套控件都在页面里，由 site.css 按宽度只显示其一（同 P02）。
// 商品区按语言与活动 ID 重新开始（saleListKey）：活动换了、变为没有活动或切换语言时回到第 1 页重新读取；离开页面时中止未完成的读取。

// 页码最多显示 5 个，以当前页为中心（同 P02）。
const PAGE_WINDOW = 5;

// 商品级规则的类别；其余（满额减或满额折、免运费）是整单级规则。
const ITEM_RULE_KINDS: readonly string[] = [KIND_ITEM_DISCOUNT, KIND_BUY_X_GET_Y];

// 商品级规则的说明与它的范围之间的分隔（P15-desktop「20% off — Applies to: Apparel」）。
const SCOPE_SEPARATOR = " — ";

export function isItemRule(rule: RuleDisplay): boolean {
  return ITEM_RULE_KINDS.includes(rule.kind);
}

// 规则区的说明行（UX P15「商品级规则在前，按规则排序；整单级规则在后」）：先按 position 列商品级规则，
// 说明后附 scopeText 的范围（没有分类时不附）；再按 position 列整单级规则（满额每档一行）。
export function saleRuleLines(language: Language, promotion: PromotionDisplay): string[] {
  const rules = [...promotion.rules].sort((a, b) => a.position - b.position);
  const itemLines = rules.filter(isItemRule).flatMap((rule) => {
    const scope = scopeText(language, rule);
    return ruleTexts(language, rule).map((line) => (scope === null ? line : `${line}${SCOPE_SEPARATOR}${scope}`));
  });
  const orderLines = rules.filter((rule) => !isItemRule(rule)).flatMap((rule) => ruleTexts(language, rule));
  return [...itemLines, ...orderLines];
}

// 商品区一页的请求：只取生效活动商品级规则范围内的商品（sale=true），排序与每页条数同 P02 的默认（每页条数由接口决定）。
export function saleProductsUrl(language: Language, page: number): string {
  return productsUrl(language, { sort: DEFAULT_PRODUCT_LIST_QUERY.sort, page, sale: true });
}

// 商品区的身份：语言或活动 ID 变了即是另一个商品区（重新从第 1 页读取）；同一活动重新读取（generation 变化）时不变。
export function saleListKey(language: Language, promotion: PromotionDisplay): string {
  return `${language}:${String(promotion.promotion_id)}`;
}

// 读一页商品；测试传入替身。
export type SaleProductsFetcher = (url: string, signal: AbortSignal) => Promise<ProductPage>;

export interface SaleListState {
  // 正在显示的页（首次读取与桌面翻页）。
  page: number;
  products: Remote<ProductPage>;
  // 手机 list.load_more 追加的页；换页即作废。
  more: MoreResults | null;
}

const LOADING: Remote<never> = { status: "loading" };
const FAILED: Remote<never> = { status: "error" };

export const SALE_LIST_START: SaleListState = { page: 1, products: LOADING, more: null };

export interface SaleList {
  current: () => SaleListState;
  subscribe: (listener: () => void) => () => void;
  // 开始：还没有结果时读取当前页（一开始为第 1 页）。返回的函数停止：中止未完成的读取（它的结果不再更新状态）。
  start: () => () => void;
  // 桌面翻页：显示读取中，读取该页后替换显示的页；失败时为 error（同 P02 的首次读取）。
  showPage: (page: number) => void;
  // 手机加载更多：读取下一页并追加；失败时已显示的商品保留，可再按一次。
  loadMore: () => void;
}

// 商品区的读取状态，放在不依赖 React 的读取对象里，组件经 useSyncExternalStore 订阅（同 promotion.tsx）。
// 页码只在这里，不进地址与历史记录。同一时刻只有一个读取：新的读取中止之前未完成的读取。
export function createSaleList(fetcher: SaleProductsFetcher, language: Language, initial: SaleListState | null): SaleList {
  const listeners = new Set<() => void>();
  let state: SaleListState = initial ?? SALE_LIST_START;
  let controller: AbortController | null = null;

  const publish = (next: SaleListState) => {
    state = next;
    for (const listener of listeners) {
      listener();
    }
  };

  // 读一页，完成后交给 settle（失败为 null）；被中止的读取不调用 settle。
  const read = (page: number, settle: (result: ProductPage | null) => void) => {
    controller?.abort();
    const current = new AbortController();
    controller = current;
    const finish = (result: ProductPage | null) => {
      if (current.signal.aborted) {
        return;
      }
      controller = null;
      settle(result);
    };
    void fetcher(saleProductsUrl(language, page), current.signal).then(finish, () => {
      finish(null);
    });
  };

  const readPage = (page: number) => {
    read(page, (result) => {
      publish({ page, products: result === null ? FAILED : { status: "ready", data: result }, more: null });
    });
  };

  return {
    current: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    start: () => {
      if (state.products.status === "loading") {
        readPage(state.page);
      }
      return () => {
        controller?.abort();
        controller = null;
      };
    },
    showPage: (page) => {
      publish({ page, products: LOADING, more: null });
      readPage(page);
    },
    loadMore: () => {
      if (state.products.status !== "ready") {
        return;
      }
      const items = state.more?.items ?? [];
      const lastPage = state.more?.lastPage ?? state.page;
      const next = lastPage + 1;
      publish({ ...state, more: { items, lastPage, status: "loading" } });
      read(next, (result) => {
        publish({
          ...state,
          more:
            result === null
              ? { items, lastPage, status: "error" }
              : { items: [...items, ...result.items], lastPage: next, status: "idle" },
        });
      });
    },
  };
}

function ChevronIcon({ direction }: { direction: "prev" | "next" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === "prev" ? "M15 6l-6 6 6 6" : "M9 6l6 6-6 6"} />
    </svg>
  );
}

// 桌面页码：上一页、最多 5 个页码、下一页，样式与显隐同 P02；都是按钮，只改页面状态、不改地址。
function SalePagination({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (page: number) => void }) {
  const t = useCopy();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) {
    return null;
  }
  const first = Math.max(1, Math.min(page - Math.floor(PAGE_WINDOW / 2), pageCount - PAGE_WINDOW + 1));
  const pages = Array.from({ length: Math.min(PAGE_WINDOW, pageCount) }, (_, index) => first + index);
  return (
    <div className="site-desktop-only site-list__pages">
      {page > 1 ? (
        <button
          className="acs-btn acs-btn--secondary acs-btn--sm"
          type="button"
          aria-label={t("common.a11y_page_prev")}
          onClick={() => {
            onPage(Math.min(page - 1, pageCount));
          }}
        >
          <ChevronIcon direction="prev" />
        </button>
      ) : (
        <span className="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true">
          <ChevronIcon direction="prev" />
        </span>
      )}
      {pages.map((number) => (
        <button
          key={number}
          className={number === page ? "acs-btn acs-btn--primary acs-btn--sm" : "acs-btn acs-btn--secondary acs-btn--sm"}
          type="button"
          aria-current={number === page ? "page" : undefined}
          onClick={() => {
            if (number !== page) {
              onPage(number);
            }
          }}
        >
          {number}
        </button>
      ))}
      {page < pageCount ? (
        <button
          className="acs-btn acs-btn--secondary acs-btn--sm"
          type="button"
          aria-label={t("common.a11y_page_next")}
          onClick={() => {
            onPage(page + 1);
          }}
        >
          <ChevronIcon direction="next" />
        </button>
      ) : (
        <span className="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true">
          <ChevronIcon direction="next" />
        </span>
      )}
    </div>
  );
}

export interface SaleProductsViewProps {
  state: SaleListState;
  onPage: (page: number) => void;
  onLoadMore: () => void;
  ref?: Ref<HTMLElement> | undefined;
}

// 商品区：promo.products_title、list.results_count（接口给的总数）与商品卡网格（同 P02）。
// 目录返回 0 件时为结果数与 list.empty（没有筛选，不放 list.filter_clear）；读取失败照 P02。
export function SaleProductsView({ state, onPage, onLoadMore, ref }: SaleProductsViewProps) {
  const t = useCopy();
  const { page, products, more } = state;
  const ready = products.status === "ready" ? products.data : null;
  const items = ready ? [...ready.items, ...(more?.items ?? [])] : [];
  const lastPage = more?.lastPage ?? page;
  const hasMore = ready !== null && lastPage * ready.page_size < ready.total;
  return (
    <section ref={ref} className="site-sale__products">
      <div className="site-sale__products-head">
        <h2 className="acs-display-m">{t("promo.products_title")}</h2>
        {ready && <span className="acs-muted">{t("list.results_count", { count: ready.total })}</span>}
      </div>
      {products.status === "error" && <ErrorNotice />}
      {products.status === "loading" && <div className="acs-pgrid site-list__grid" aria-busy="true" />}
      {ready && ready.total === 0 && (
        <div className="site-list__empty">
          <p className="acs-display-s">{t("list.empty")}</p>
        </div>
      )}
      {ready && ready.total > 0 && (
        <>
          <div className="acs-pgrid site-list__grid">
            {items.map((product) => (
              <ProductCard key={product.slug} product={product} />
            ))}
          </div>
          <SalePagination page={page} total={ready.total} pageSize={ready.page_size} onPage={onPage} />
          {more?.status === "error" && (
            <div className="site-phone-only site-list__more-error">
              <ErrorNotice />
            </div>
          )}
          {hasMore && (
            <button
              className="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-list__more"
              type="button"
              disabled={more?.status === "loading"}
              aria-busy={more?.status === "loading"}
              onClick={onLoadMore}
            >
              {t("list.load_more")}
            </button>
          )}
        </>
      )}
    </section>
  );
}

interface SaleProductsProps {
  language: Language;
  fetcher: SaleProductsFetcher;
  initial: SaleListState | null;
}

// 商品区的读取：挂载时读取第 1 页，卸载（离开页面、活动换了或切换语言）时中止未完成的读取。
// 桌面翻页后回到商品区的开头（P02 回到页面顶部，这里商品区在规则区之下）。
function SaleProducts({ language, fetcher, initial }: SaleProductsProps) {
  const [list] = useState(() => createSaleList(fetcher, language, initial));
  const state = useSyncExternalStore(list.subscribe, list.current, list.current);
  const section = useRef<HTMLElement>(null);

  useEffect(() => list.start(), [list]);

  return (
    <SaleProductsView
      ref={section}
      state={state}
      onPage={(page) => {
        section.current?.scrollIntoView({ block: "start" });
        list.showPage(page);
      }}
      onLoadMore={list.loadMore}
    />
  );
}

function fetchSaleProducts(url: string, signal: AbortSignal): Promise<ProductPage> {
  return fetchCatalog<ProductPage>(url, signal);
}

export interface SalePageProps {
  // 测试传入替身；不传时用 api/catalog.ts 的 fetchCatalog。
  fetcher?: SaleProductsFetcher | undefined;
  // 测试用：商品区一开始的状态，给出时挂载后不读取；不传时为读取中的第 1 页。
  initial?: SaleListState | undefined;
}

export default function SalePage({ fetcher, initial }: SalePageProps) {
  const t = useCopy();
  const { language } = useLanguage();
  const { status, promotion } = usePromotion();
  return (
    <main className="site-sale" aria-busy={status === "loading" ? true : undefined}>
      <DemoHint>{t("promo.demo_hint")}</DemoHint>
      {status === "ready" && promotion === null && (
        <div className="acs-card site-sale__ended">
          <p className="acs-display-s">{t("promo.ended")}</p>
          <Link className="acs-btn acs-btn--primary" to={PRODUCTS_PATH}>
            {t("promo.browse_all")}
          </Link>
        </div>
      )}
      {status === "ready" && promotion !== null && (
        <>
          <div className="site-sale__intro">
            <PromoStrip text={promotion.title ?? ""} viewAll={false} />
            {promotion.subtitle ? <p className="acs-body-l">{promotion.subtitle}</p> : null}
          </div>
          <div className="site-sale__rules">
            <section className="acs-promo-rules">
              <h2 className="acs-display-s">{t("promo.rules_title")}</h2>
              <PromoRuleList
                lines={saleRuleLines(language, promotion)}
                couponCombinable={promotion.coupon_combinable}
                pointsCombinable={promotion.points_combinable}
              />
            </section>
          </div>
          {promotion.rules.some(isItemRule) ? (
            <SaleProducts
              key={saleListKey(language, promotion)}
              language={language}
              fetcher={fetcher ?? fetchSaleProducts}
              initial={initial ?? null}
            />
          ) : (
            <div className="acs-panel site-sale__note">
              <p className="acs-body-l">{t("promo.order_level_only")}</p>
              <Link className="acs-btn acs-btn--primary" to={PRODUCTS_PATH}>
                {t("promo.browse_all")}
              </Link>
            </div>
          )}
        </>
      )}
    </main>
  );
}
