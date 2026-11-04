import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { readAdminCategories } from "../api/adminCategories";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import { readAdminProduct, readAdminProducts } from "../api/adminProducts";
import type { AdminProduct, AdminProductListItem, ProductRead, ProductsRead } from "../api/adminProducts";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";

// A04 后台商品（docs/UX.md 0.17 A04，视觉稿 A04-desktop 与 A04-phone-edit；HANDOFF 0.82 第（2）点）：用后台框架渲染，当前导航项为商品。
// 内容区为标题 admin.nav_products 与全部商品（含未上架的，按接口给的顺序；桌面表格、手机卡片，两套都渲染，由 site.css 按宽度显隐），
// 点 common.edit 后读取该商品，成功才显示编辑区（桌面在列表右侧；手机从点击起隐藏列表，编辑区上方为页内的 common.back）。
// 打开页面时并行调用 GET /api/admin/products 与 GET /api/admin/categories，两者都取到才显示列表；切换界面语言不重新读取。
// 单件为 GET /api/admin/products/{id}（经 api/adminProducts.ts 与 api/adminCategories.ts；DESIGN 1.18「计价、优惠、积分与库存」第 11 条）。
// 401 换成登录页 A01；离开页面时中止进行中的读取，之后不再更新页面。本任务的编辑区只有标题 admin.product_edit；输入框与未保存确认在
// SHOP-TASK-203，保存在 204，新增规格值与 SKU、新建、图片与当日库存调整在 205 至 208。

// 三语名称（读取给的，可为 null）。
export type ListNames = Readonly<Record<Language, string | null>>;

// 名称按后台界面语言显示：该语言有译名时用它，缺译（null 或空串）回退英文，英文也缺时为 —。
export function localName(names: ListNames, language: Language): string {
  const own = names[language];
  if (own !== null && own !== "") {
    return own;
  }
  return names.en === null || names.en === "" ? "—" : names.en;
}

// 列表一行的商品名称与分类名称（照 localName）。
export function productName(item: AdminProductListItem, language: Language): string {
  return localName({ en: item.name_en, zh: item.name_zh, ms: item.name_ms }, language);
}

export function categoryName(item: AdminProductListItem, language: Language): string {
  return localName({ en: item.category_name_en, zh: item.category_name_zh, ms: item.category_name_ms }, language);
}

// 编辑区：读取中（该位置不显示内容）、读取失败（只有提示）、已取到（读取到的单件商品，供 203 至 208 接入）。id 为点了 common.edit 的商品。
export type Editing =
  | { status: "loading"; id: number }
  | { status: "failed"; id: number; error: CopyKey }
  | { status: "ready"; id: number; product: AdminProduct };

// 页面的状态：读取中（标 aria-busy）、读取失败（只有提示）、已取到（按接口顺序的商品、分类读取的结果（供 203 的分类下拉）、
// 列表给的只用于写请求头的 CSRF 令牌与编辑区；没有点 common.edit 时为 null）。
export type ProductsState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | { status: "ready"; products: AdminProductListItem[]; categories: AdminCategory[]; csrfToken: string; editing: Editing | null };

export type ReadyState = Extract<ProductsState, { status: "ready" }>;

export const LOADING: ProductsState = { status: "loading" };

// 按两个读取的结果决定（HANDOFF 0.82）：都取到时显示列表、不选中任何商品；任一 401 回到 A01（login）；否则任一网络中断
// common.network_check，其他失败 common.error_retry。
export function readStep(products: ProductsRead, categories: CategoriesRead): ProductsState | "login" {
  if (products.kind === "none" || categories.kind === "none") {
    return "login";
  }
  if (products.kind === "network" || categories.kind === "network") {
    return { status: "failed", error: "common.network_check" };
  }
  if (products.kind === "failed" || categories.kind === "failed") {
    return { status: "failed", error: "common.error_retry" };
  }
  return { status: "ready", products: products.products, categories: categories.categories, csrfToken: products.csrf_token, editing: null };
}

// 编辑区已打开（或正在读取）的商品；没有、或读取失败（编辑区没有打开）时为 null。
export function editingId(state: ProductsState): number | null {
  return state.status === "ready" && state.editing !== null && state.editing.status !== "failed" ? state.editing.id : null;
}

// 点某行 common.edit：收起当前的编辑区、改为读取该商品（读取中）。点的正是编辑区已打开或正在读取的那一件时不变（null，不做任何事）；
// 列表还没取到时也不变。读取失败的那一件再点时重新读取。
export function startEditing(state: ProductsState, id: number): ProductsState | null {
  if (state.status !== "ready" || editingId(state) === id) {
    return null;
  }
  return { ...state, editing: { status: "loading", id } };
}

// 按单件读取的结果决定：取到时显示编辑区；401 回到 A01（login）；网络中断 common.network_check，其他失败（含商品不存在）common.error_retry，
// 都显示在编辑区的位置。
export function productStep(read: ProductRead, id: number): Editing | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", id, product: read.product };
    case "none":
      return "login";
    case "network":
      return { status: "failed", id, error: "common.network_check" };
    case "failed":
      return { status: "failed", id, error: "common.error_retry" };
  }
}

// 单件读取的结果只换掉仍在读取同一件商品的编辑区：之后又点了另一行或返回时（以最后一次为准），先前的结果不显示。
export function settleEditing(state: ProductsState, next: Editing): ProductsState {
  if (state.status !== "ready") {
    return state;
  }
  const { editing } = state;
  return editing !== null && editing.status === "loading" && editing.id === next.id ? { ...state, editing: next } : state;
}

// 手机的 common.back 与收起编辑区：回到只有列表。
export function closeEditing(state: ProductsState): ProductsState {
  return state.status === "ready" ? { ...state, editing: null } : state;
}

// 对当前状态的一次改动（请求的结果按结果到达时的状态更新）。
export type ProductsChange = (current: ProductsState) => ProductsState;

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface ProductsMoves {
  show: (state: ProductsState) => void;
  update: (change: ProductsChange) => void;
  replace: (path: RoutePath) => void;
}

// 打开页面：并行读取商品列表与分类；signal 中止（离开页面）之后不再更新。
export async function openProducts(signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const [products, categories] = await Promise.all([readAdminProducts(signal), readAdminCategories(signal)]);
  if (signal.aborted) {
    return;
  }
  const step = readStep(products, categories);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 读取一件商品（点 common.edit 之后）：signal 中止（又点了另一行、点了 common.back 或离开页面）之后不再更新。
export async function openProduct(id: number, signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const read = await readAdminProduct(id, signal);
  if (signal.aborted) {
    return;
  }
  const step = productStep(read, id);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update((current) => settleEditing(current, step));
  }
}

function pageMoves(setState: Dispatch<SetStateAction<ProductsState>>, replace: (path: RoutePath) => void): ProductsMoves {
  return {
    show: (state) => {
      setState(state);
    },
    update: (change) => {
      setState(change);
    },
    replace,
  };
}

function ProductStatus({ item }: { item: AdminProductListItem }) {
  const t = useCopy();
  return (
    <span className={item.is_active ? "acs-tag acs-tag--neutral" : "acs-tag acs-tag--outline"}>
      {t(item.is_active ? "admin.active" : "admin.unpublished")}
    </span>
  );
}

function ChevronIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

interface ListHandlers {
  onEdit: (item: AdminProductListItem) => void;
}

// 列表：桌面表格（每行商品名称与其下的分类名称、状态与 common.edit，照 A04-desktop），手机卡片（商品名称与状态，整张卡片为读屏标签
// common.edit 的按钮）。顺序照接口；名称照 localName。
function ProductList({ products, onEdit }: { products: readonly AdminProductListItem[] } & ListHandlers) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <>
      <div className="acs-admin__panel site-admin-products__table">
        <table className="acs-admin__table">
          <tbody>
            {products.map((item) => (
              <tr key={item.id}>
                <td className="site-admin-products__names">
                  <span>{productName(item, language)}</span>
                  <span className="acs-admin__muted">{categoryName(item, language)}</span>
                </td>
                <td>
                  <ProductStatus item={item} />
                </td>
                <td>
                  <button
                    className="acs-admin__btn acs-admin__btn--secondary"
                    type="button"
                    onClick={() => {
                      onEdit(item);
                    }}
                  >
                    {t("common.edit")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="site-admin-products__cards">
        {products.map((item) => (
          <li key={item.id}>
            <button
              className="acs-admin__panel site-admin-products__card"
              type="button"
              aria-label={t("common.edit")}
              onClick={() => {
                onEdit(item);
              }}
            >
              <span className="site-admin-products__card-name">{productName(item, language)}</span>
              <ProductStatus item={item} />
              <span className="site-admin-products__chevron">
                <ChevronIcon />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

interface EditorHandlers {
  onBack: () => void;
}

// 编辑区的位置：手机上方为 common.back（桌面由 site.css 隐藏）；读取中不显示内容（标 aria-busy），读取失败只有提示，
// 取到后为面板与标题 admin.product_edit（输入框在 SHOP-TASK-203）。
function EditorView({ editing, onBack }: { editing: Editing } & EditorHandlers) {
  const t = useCopy();
  return (
    <div className="site-admin-products__editor" aria-busy={editing.status === "loading"}>
      <button className="acs-admin__btn acs-admin__btn--secondary site-admin-products__back" type="button" onClick={onBack}>
        {t("common.back")}
      </button>
      {editing.status === "failed" && <AdminAlert error={editing.error} />}
      {editing.status === "ready" && (
        <div className="acs-admin__panel site-admin-products__form">
          <h2 className="acs-admin__h">{t("admin.product_edit")}</h2>
        </div>
      )}
    </div>
  );
}

export interface AdminProductsViewProps extends ListHandlers, EditorHandlers {
  state: ProductsState;
}

// 标题之下为列表区：读取中标 aria-busy；读取失败时只有提示。点了 common.edit 之后根元素加 --editing（桌面两栏，手机隐藏列表）。
export function AdminProductsView({ state, onEdit, onBack }: AdminProductsViewProps) {
  const t = useCopy();
  const editing = state.status === "ready" ? state.editing : null;
  return (
    <div className={editing === null ? "site-admin-products" : "site-admin-products site-admin-products--editing"}>
      <div className="site-admin-products__list">
        <h1 className="acs-admin__h">{t("admin.nav_products")}</h1>
        <div className="site-admin-products__body" aria-busy={state.status === "loading"}>
          {state.status === "failed" && <AdminAlert error={state.error} />}
          {state.status === "ready" && <ProductList products={state.products} onEdit={onEdit} />}
        </div>
      </div>
      {editing !== null && <EditorView editing={editing} onBack={onBack} />}
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载时读取一次（切换界面语言不重新读取）。
export function AdminProductsContent() {
  const { replace } = useRouter();
  const [state, setState] = useState<ProductsState>(LOADING);
  // 单件读取的中止：又点了另一行、点了 common.back 或离开页面时中止前一次。
  const productRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    return startDeferred(controller, () => {
      void openProducts(controller.signal, pageMoves(setState, replace));
    });
  }, [replace]);

  useEffect(
    () => () => {
      productRequest.current?.abort();
    },
    [],
  );

  return (
    <AdminProductsView
      state={state}
      onEdit={(item) => {
        const next = startEditing(state, item.id);
        if (next === null) {
          return;
        }
        productRequest.current?.abort();
        const controller = new AbortController();
        productRequest.current = controller;
        setState(next);
        void openProduct(item.id, controller.signal, pageMoves(setState, replace));
      }}
      onBack={() => {
        productRequest.current?.abort();
        productRequest.current = null;
        setState(closeEditing);
      }}
    />
  );
}

export default function AdminProductsPage() {
  return (
    <AdminFrame current="products">
      <AdminProductsContent />
    </AdminFrame>
  );
}
