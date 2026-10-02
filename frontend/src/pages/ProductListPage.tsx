import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { categoriesUrl, fetchCatalog, isSortOrder, optionsUrl, productsUrl, useCatalog } from "../api/catalog";
import type { CategoryListItem, FilterOption, ProductPage, ProductSummary, Remote, SortOrder } from "../api/catalog";
import ProductCard from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link, PRODUCTS_PATH, useRouter } from "../router";
import {
  buildProductListSearch,
  clearFilters,
  parseProductListQuery,
  toProductListRequest,
  toggle,
  withFilters,
} from "./productListQuery";
import type { ProductListQuery } from "./productListQuery";

// 商品列表 P02（docs/UX.md P02）：标题与 ★ list.demo_hint、结果数、排序、商品卡、清除筛选与空结果 list.empty。
// 条件全在网址查询参数里（productListQuery.ts）；筛选、排序与分页全部交给商品列表接口，页面不自行过滤或排序。
// 桌面：侧栏筛选（勾选即生效）与页码翻页。767px 以下：全屏筛选抽屉（勾选后按 list.filter_apply 才生效）
// 与 list.load_more 追加下一页（追加的页不写进网址）。两套控件都在页面里，由 site.css 按宽度只显示其一。
// 清除筛选（侧栏、空结果与抽屉里的 list.filter_clear）去掉搜索词、分类与规格，保留排序，立即生效。
// 切换语言时请求地址随语言变化而重新请求，网址里的条件不变。

const SORT_LABELS: Readonly<Record<SortOrder, CopyKey>> = {
  newest: "list.sort_newest",
  price_asc: "list.sort_price_asc",
  price_desc: "list.sort_price_desc",
};

// 页码最多显示 5 个，以当前页为中心。
const PAGE_WINDOW = 5;

function ChevronIcon({ direction }: { direction: "prev" | "next" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === "prev" ? "M15 6l-6 6 6 6" : "M9 6l6 6-6 6"} />
    </svg>
  );
}

interface FilterChoice {
  categories: readonly string[];
  options: readonly string[];
}

interface FilterFieldsProps {
  categories: Remote<CategoryListItem[]>;
  options: Remote<FilterOption[]>;
  chosen: FilterChoice;
  onToggleCategory: (slug: string) => void;
  onToggleOption: (option: string) => void;
}

// 筛选项：分类来自分类列表接口，规格来自规格筛选项接口（SHOP-TASK-013）；侧栏与抽屉共用。
function FilterFields({ categories, options, chosen, onToggleCategory, onToggleOption }: FilterFieldsProps) {
  const t = useCopy();
  return (
    <>
      {(categories.status === "error" || options.status === "error") && <ErrorNotice />}
      {categories.status === "ready" && categories.data.length > 0 && (
        <fieldset className="site-filter">
          <legend className="acs-field__label">{t("list.filter_category")}</legend>
          {categories.data.map((category) => (
            <label key={category.slug} className="site-check">
              <input
                type="checkbox"
                checked={chosen.categories.includes(category.slug)}
                onChange={() => {
                  onToggleCategory(category.slug);
                }}
              />
              <span lang={category.name.english_fallback ? "en" : undefined}>{category.name.text}</span>
            </label>
          ))}
        </fieldset>
      )}
      {options.status === "ready" &&
        options.data.map((option) => (
          <fieldset key={option.code} className="site-filter site-filter--values">
            <legend className="acs-field__label" lang={option.name.english_fallback ? "en" : undefined}>
              {option.name.text}
            </legend>
            {option.values.map((value) => {
              const code = `${option.code}:${value.code}`;
              return (
                <label key={code} className="site-check">
                  <input
                    type="checkbox"
                    checked={chosen.options.includes(code)}
                    onChange={() => {
                      onToggleOption(code);
                    }}
                  />
                  <span lang={value.name.english_fallback ? "en" : undefined}>{value.name.text}</span>
                </label>
              );
            })}
          </fieldset>
        ))}
    </>
  );
}

// 桌面页码：上一页、最多 5 个页码、下一页；都是带查询参数的链接，可分享、可在新标签页打开。
function Pagination({ query, total, pageSize }: { query: ProductListQuery; total: number; pageSize: number }) {
  const t = useCopy();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) {
    return null;
  }
  const first = Math.max(1, Math.min(query.page - Math.floor(PAGE_WINDOW / 2), pageCount - PAGE_WINDOW + 1));
  const pages = Array.from({ length: Math.min(PAGE_WINDOW, pageCount) }, (_, index) => first + index);
  const scrollTop = () => {
    window.scrollTo(0, 0);
  };
  const pageLink = (page: number) => buildProductListSearch({ ...query, page });
  return (
    <div className="site-desktop-only site-list__pages">
      {query.page > 1 ? (
        <Link
          className="acs-btn acs-btn--secondary acs-btn--sm"
          to={PRODUCTS_PATH}
          search={pageLink(Math.min(query.page - 1, pageCount))}
          aria-label={t("common.a11y_page_prev")}
          onClick={scrollTop}
        >
          <ChevronIcon direction="prev" />
        </Link>
      ) : (
        <span className="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true">
          <ChevronIcon direction="prev" />
        </span>
      )}
      {pages.map((page) => (
        <Link
          key={page}
          className={page === query.page ? "acs-btn acs-btn--primary acs-btn--sm" : "acs-btn acs-btn--secondary acs-btn--sm"}
          to={PRODUCTS_PATH}
          search={pageLink(page)}
          aria-current={page === query.page ? "page" : undefined}
          onClick={scrollTop}
        >
          {page}
        </Link>
      ))}
      {query.page < pageCount ? (
        <Link
          className="acs-btn acs-btn--secondary acs-btn--sm"
          to={PRODUCTS_PATH}
          search={pageLink(query.page + 1)}
          aria-label={t("common.a11y_page_next")}
          onClick={scrollTop}
        >
          <ChevronIcon direction="next" />
        </Link>
      ) : (
        <span className="acs-btn acs-btn--secondary acs-btn--sm" aria-hidden="true" aria-disabled="true">
          <ChevronIcon direction="next" />
        </span>
      )}
    </div>
  );
}

// 手机「加载更多」追加的页：只在内存里，换条件或语言即作废。
export interface MoreResults {
  items: readonly ProductSummary[];
  lastPage: number;
  status: "idle" | "loading" | "error";
}

export interface ProductListViewProps {
  query: ProductListQuery;
  products: Remote<ProductPage>;
  categories: Remote<CategoryListItem[]>;
  options: Remote<FilterOption[]>;
  more: MoreResults | null;
  onQueryChange: (next: ProductListQuery) => void;
  onLoadMore: (lastPage: number) => void;
}

export function ProductListView({
  query,
  products,
  categories,
  options,
  more,
  onQueryChange,
  onLoadMore,
}: ProductListViewProps) {
  const t = useCopy();
  const sortId = useId();
  const drawerId = useId();
  const drawerTitleId = useId();
  // 抽屉里尚未应用的选择；null 表示抽屉关着。
  const [draft, setDraft] = useState<FilterChoice | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const filterButtonRef = useRef<HTMLButtonElement>(null);
  const drawerOpen = draft !== null;

  useEffect(() => {
    if (drawerOpen) {
      drawerRef.current?.focus();
    }
  }, [drawerOpen]);

  const closeDrawer = () => {
    setDraft(null);
    filterButtonRef.current?.focus();
  };
  const applyDrawer = (choice: FilterChoice) => {
    onQueryChange(withFilters(query, { categories: choice.categories, options: choice.options }));
    closeDrawer();
  };
  const onDrawerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      closeDrawer();
    }
  };
  const clear = () => {
    onQueryChange(clearFilters(query));
  };

  const ready = products.status === "ready" ? products.data : null;
  const items = ready ? [...ready.items, ...(more?.items ?? [])] : [];
  const lastPage = more?.lastPage ?? query.page;
  const hasMore = ready !== null && lastPage * ready.page_size < ready.total;

  return (
    <main className="site-list">
      <div className="site-list__head">
        <h1 className="acs-display-l">{t("list.title")}</h1>
        <DemoHint>{t("list.demo_hint")}</DemoHint>
      </div>
      <div className="site-list__body">
        <aside className="site-desktop-only site-list__aside">
          <h2 className="acs-display-s">{t("list.filter_title")}</h2>
          <FilterFields
            categories={categories}
            options={options}
            chosen={query}
            onToggleCategory={(slug) => {
              onQueryChange(withFilters(query, { categories: toggle(query.categories, slug) }));
            }}
            onToggleOption={(option) => {
              onQueryChange(withFilters(query, { options: toggle(query.options, option) }));
            }}
          />
          <button className="acs-btn acs-btn--quiet site-list__clear" type="button" onClick={clear}>
            {t("list.filter_clear")}
          </button>
        </aside>
        <div className="site-list__main">
          <div className="site-list__tools">
            <button
              ref={filterButtonRef}
              className="acs-btn acs-btn--secondary site-phone-only site-list__filter-button"
              type="button"
              aria-haspopup="dialog"
              aria-expanded={drawerOpen}
              aria-controls={drawerId}
              onClick={() => {
                setDraft({ categories: query.categories, options: query.options });
              }}
            >
              {t("list.filter_title")}
            </button>
            {ready && <span className="acs-muted site-list__count">{t("list.results_count", { count: ready.total })}</span>}
            <div className="site-list__sort">
              <label className="acs-field__label site-desktop-only" htmlFor={sortId}>
                {t("list.sort")}
              </label>
              <select
                id={sortId}
                className="acs-select"
                aria-label={t("list.sort")}
                value={query.sort}
                onChange={(event) => {
                  const sort = event.currentTarget.value;
                  if (isSortOrder(sort)) {
                    onQueryChange(withFilters(query, { sort }));
                  }
                }}
              >
                {Object.entries(SORT_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {t(label)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {products.status === "error" && <ErrorNotice />}
          {products.status === "loading" && <div className="acs-pgrid site-list__grid" aria-busy="true" />}
          {ready && ready.total === 0 && (
            <div className="site-list__empty">
              <p className="acs-display-s">{t("list.empty")}</p>
              <button className="acs-btn acs-btn--secondary" type="button" onClick={clear}>
                {t("list.filter_clear")}
              </button>
            </div>
          )}
          {ready && ready.total > 0 && (
            <>
              <div className="acs-pgrid site-list__grid">
                {items.map((product) => (
                  <ProductCard key={product.slug} product={product} />
                ))}
              </div>
              <Pagination query={query} total={ready.total} pageSize={ready.page_size} />
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
                  onClick={() => {
                    onLoadMore(lastPage);
                  }}
                >
                  {t("list.load_more")}
                </button>
              )}
            </>
          )}
        </div>
      </div>
      <div
        ref={drawerRef}
        id={drawerId}
        className="acs site-phone-only site-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={drawerTitleId}
        tabIndex={-1}
        hidden={!drawerOpen}
        onKeyDown={onDrawerKeyDown}
      >
        <div className="site-drawer__head">
          <h2 className="acs-display-s" id={drawerTitleId}>
            {t("list.filter_title")}
          </h2>
        </div>
        <div className="site-drawer__body">
          {draft && (
            <FilterFields
              categories={categories}
              options={options}
              chosen={draft}
              onToggleCategory={(slug) => {
                setDraft({ ...draft, categories: toggle(draft.categories, slug) });
              }}
              onToggleOption={(option) => {
                setDraft({ ...draft, options: toggle(draft.options, option) });
              }}
            />
          )}
        </div>
        <div className="site-drawer__actions">
          <button
            className="acs-btn acs-btn--secondary"
            type="button"
            onClick={() => {
              clear();
              closeDrawer();
            }}
          >
            {t("list.filter_clear")}
          </button>
          <button
            className="acs-btn acs-btn--primary"
            type="button"
            onClick={() => {
              if (draft) {
                applyDrawer(draft);
              }
            }}
          >
            {t("list.filter_apply")}
          </button>
        </div>
      </div>
    </main>
  );
}

export default function ProductListPage() {
  const { language } = useLanguage();
  const { search, navigate } = useRouter();
  const query = useMemo(() => parseProductListQuery(search), [search]);
  const listUrl = productsUrl(language, toProductListRequest(query));
  const products = useCatalog<ProductPage>(listUrl);
  const categories = useCatalog<CategoryListItem[]>(categoriesUrl(language));
  const options = useCatalog<FilterOption[]>(optionsUrl(language));
  // 「加载更多」的结果按列表地址记下：条件、页码或语言变了，地址就变，之前追加的页不再显示。
  const [more, setMore] = useState<(MoreResults & { listUrl: string }) | null>(null);
  const currentMore = more !== null && more.listUrl === listUrl ? more : null;

  const loadMore = (lastPage: number) => {
    const page = lastPage + 1;
    const items = currentMore?.items ?? [];
    setMore({ listUrl, items, lastPage, status: "loading" });
    fetchCatalog<ProductPage>(productsUrl(language, toProductListRequest(query, page))).then(
      (data) => {
        setMore((current) =>
          current?.listUrl === listUrl
            ? { listUrl, items: [...current.items, ...data.items], lastPage: page, status: "idle" }
            : current,
        );
      },
      () => {
        setMore((current) => (current?.listUrl === listUrl ? { ...current, status: "error" } : current));
      },
    );
  };

  return (
    <ProductListView
      query={query}
      products={products}
      categories={categories}
      options={options}
      more={currentMore}
      onQueryChange={(next) => {
        navigate(PRODUCTS_PATH, buildProductListSearch(next));
      }}
      onLoadMore={loadMore}
    />
  );
}
