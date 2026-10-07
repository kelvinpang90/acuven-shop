import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { isOrderStatus, ORDER_STATUSES, queryAdminOrders } from "../api/adminOrders";
import type { AdminOrderList, AdminOrderRow, OrderStatus, OrdersQuery, OrdersRead } from "../api/adminOrders";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, adminOrderPath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import AdminOrderDetailContent, { STATUS_LABEL, statusTagClass } from "./AdminOrderDetail";
import { formatDate, usePrice } from "./TrackOrderPage";

// 后台订单 A02（docs/UX.md 0.10 A02 与「状态与补充（0.9）」，视觉稿 A02-desktop、A02-desktop-list、A02-desktop-empty、
// A02-desktop-frozen、A02-phone-list、A02-phone-list-pages 与 A02-phone-detail）：用后台框架渲染，当前导航项为订单。
// /admin/orders 为订单列表，占满内容区；/admin/orders/:id 在桌面列表右侧显示该单详情（AdminOrderDetail.tsx），手机只显示详情。
// 标题之下为订单号搜索框与状态筛选，其下为列表：桌面为表格，手机为卡片，两套都渲染，由 site.css 按宽度显隐。
// 表格行的订单号与整张手机卡片链接到 /admin/orders/<内部 ID>；当前订单的表格行标 aria-selected。
// 调用 POST /api/admin/orders/query；搜索、筛选与页码只在本模块的内存变量里（不进网址或浏览器存储），在列表与详情之间切换时保留。
// 提交搜索或改变筛选回到第 1 页。总数超过每页条数时列表下方为上一页与下一页两个按钮（‹ 与 ›），不显示页码。
// 没有符合条件的订单时桌面只显示列头，不另写空状态文字。待审退款数仍为文字，链接到 A03 留给之后的任务。
// 401 换成登录页 A01；离开页面或发出新查询时中止旧请求，旧请求的结果不再更新页面。

// 打开时的查询：全部订单的第 1 页。
export const FIRST_QUERY: OrdersQuery = { order_number: null, status: null, page: 1 };

// 上次的查询（订单号、状态与页码）：只在本模块的内存里，列表与详情各自挂载页面时取用；重新载入页面后回到第 1 页。
let lastQuery: OrdersQuery = FIRST_QUERY;

export function rememberedListQuery(): OrdersQuery {
  return lastQuery;
}

export function rememberListQuery(query: OrdersQuery): void {
  lastQuery = query;
}

// 提交搜索：输入原样作为订单号（规范化由服务端负责），去掉首尾空白后为空则不按订单号筛选；状态不变，回到第 1 页。
export function searchFor(input: string, current: OrdersQuery): OrdersQuery {
  return { order_number: input.trim() === "" ? null : input, status: current.status, page: 1 };
}

// 改变筛选：已提交的订单号不变，回到第 1 页。
export function filterBy(status: OrderStatus | null, current: OrdersQuery): OrdersQuery {
  return { order_number: current.order_number, status, page: 1 };
}

// 翻页按钮：总数不超过每页条数时不显示（null）；第一页没有上一页、最后一页没有下一页（为 null 时按钮禁用）。
export interface Pager {
  prev: number | null;
  next: number | null;
}

export function pagerOf(list: AdminOrderList): Pager | null {
  if (list.total <= list.page_size) {
    return null;
  }
  return {
    prev: list.page > 1 ? list.page - 1 : null,
    next: list.page * list.page_size < list.total ? list.page + 1 : null,
  };
}

// 列表区的状态：请求进行中（busy，标 aria-busy）、取到的一页（list）与失败提示（error）。
export interface ListState {
  busy: boolean;
  list: AdminOrderList | null;
  error: CopyKey | null;
}

export const INITIAL_LIST: ListState = { busy: true, list: null, error: null };

// 按查询结果决定：取到显示列表；401 回到 A01（login）；网络中断显示 common.network_check，其他失败显示 common.error_retry。
export function listStep(read: OrdersRead): ListState | "login" {
  switch (read.kind) {
    case "ok":
      return { busy: false, list: read.list, error: null };
    case "none":
      return "login";
    case "network":
      return { busy: false, list: null, error: "common.network_check" };
    case "failed":
      return { busy: false, list: null, error: "common.error_retry" };
  }
}

// 页面对一次查询结果的处理：显示列表区，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface ListMoves {
  show: (state: ListState) => void;
  replace: (path: RoutePath) => void;
}

// 发出一次查询；signal 中止（离开页面或已发出新查询）之后不再更新。
export async function loadOrders(query: OrdersQuery, signal: AbortSignal, moves: ListMoves): Promise<void> {
  const read = await queryAdminOrders(query, signal);
  if (signal.aborted) {
    return;
  }
  const step = listStep(read);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

function ChevronIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

// 桌面表格的一行：订单号（链接到该单详情）、日期、状态、合计与待审数（为 0 时显示 —）；当前订单标 aria-selected。
function OrderRow({ order, selected }: { order: AdminOrderRow; selected: boolean }) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <tr aria-selected={selected ? "true" : undefined}>
      <td>
        <Link to={adminOrderPath(order.id)}>{order.order_number}</Link>
      </td>
      <td>{formatDate(order.created_at, language)}</td>
      <td>
        <span className={statusTagClass(order.status)}>{t(STATUS_LABEL[order.status])}</span>
      </td>
      <td className="site-admin-orders__num">{price(order.total_sen)}</td>
      {order.refunds_pending > 0 ? (
        <td>{t("admin.refunds_pending", { count: order.refunds_pending })}</td>
      ) : (
        <td className="acs-admin__muted">—</td>
      )}
    </tr>
  );
}

// 手机卡片（A02-phone-list）：整张为一个链接，指向该单详情；订单号与状态，其下为日期 · 合计（· 待审数，为 0 时不显示），右侧为 › 图形。
// 卡片里不嵌套其他链接或按钮。
function OrderCard({ order }: { order: AdminOrderRow }) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <li>
      <Link className="acs-admin__panel site-admin-orders__card" to={adminOrderPath(order.id)}>
        <span className="site-admin-orders__card-body">
          <span className="site-admin-orders__card-head">
            <span>{order.order_number}</span>
            <span className={statusTagClass(order.status)}>{t(STATUS_LABEL[order.status])}</span>
          </span>
          <span className="acs-admin__muted">
            <span>{formatDate(order.created_at, language)}</span>
            <span aria-hidden="true"> · </span>
            <span>{price(order.total_sen)}</span>
            {order.refunds_pending > 0 && (
              <>
                <span aria-hidden="true"> · </span>
                <span>{t("admin.refunds_pending", { count: order.refunds_pending })}</span>
              </>
            )}
          </span>
        </span>
        <ChevronIcon />
      </Link>
    </li>
  );
}

export interface AdminOrdersViewProps {
  // 搜索框里的文字（尚未提交）与当前的状态筛选。
  draft: string;
  status: OrderStatus | null;
  state: ListState;
  // 当前打开详情的订单（路径里的内部 ID）；只在列表时为 null 或不给。
  selectedId?: string | null;
  onDraft: (value: string) => void;
  onSearch: () => void;
  onStatus: (status: OrderStatus | null) => void;
  onPage: (page: number) => void;
}

export function AdminOrdersView({ draft, status, state, selectedId = null, onDraft, onSearch, onStatus, onPage }: AdminOrdersViewProps) {
  const t = useCopy();
  const { list } = state;
  const pager = list === null ? null : pagerOf(list);
  return (
    <div className="site-admin-orders">
      <h1 className="acs-admin__h">{t("admin.nav_orders")}</h1>
      <form
        className="site-admin-orders__filters"
        role="search"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          onSearch();
        }}
      >
        <input
          className="acs-admin__input site-admin-orders__search"
          type="search"
          aria-label={t("admin.search_order")}
          placeholder={t("admin.search_order")}
          value={draft}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            onDraft(event.target.value);
          }}
        />
        <select
          className="acs-admin__select"
          aria-label={t("admin.filter_status")}
          value={status ?? ""}
          onChange={(event: ChangeEvent<HTMLSelectElement>) => {
            const value = event.target.value;
            onStatus(isOrderStatus(value) ? value : null);
          }}
        >
          <option value="">{t("admin.filter_status")}</option>
          {ORDER_STATUSES.map((option) => (
            <option key={option} value={option}>
              {t(STATUS_LABEL[option])}
            </option>
          ))}
        </select>
      </form>
      <div className="site-admin-orders__list" aria-busy={state.busy}>
        {state.error !== null && <AdminAlert error={state.error} />}
        {list !== null && (
          <>
            <div className="acs-admin__panel site-admin-orders__table">
              <table className="acs-admin__table">
                <thead>
                  <tr>
                    <th>{t("pay.order_no")}</th>
                    <th>{t("admin.col_date")}</th>
                    <th>{t("admin.filter_status")}</th>
                    <th className="site-admin-orders__num">{t("admin.col_total")}</th>
                    <th>{t("admin.col_refunds")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.orders.map((order) => (
                    <OrderRow key={order.id} order={order} selected={String(order.id) === selectedId} />
                  ))}
                </tbody>
              </table>
            </div>
            {list.orders.length > 0 && (
              <ul className="site-admin-orders__cards">
                {list.orders.map((order) => (
                  <OrderCard key={order.id} order={order} />
                ))}
              </ul>
            )}
            {pager !== null && (
              <div className="site-admin-orders__pager">
                <button
                  className="acs-admin__btn acs-admin__btn--secondary"
                  type="button"
                  aria-label={t("common.a11y_page_prev")}
                  disabled={pager.prev === null}
                  onClick={() => {
                    if (pager.prev !== null) {
                      onPage(pager.prev);
                    }
                  }}
                >
                  ‹
                </button>
                <button
                  className="acs-admin__btn acs-admin__btn--secondary"
                  type="button"
                  aria-label={t("common.a11y_page_next")}
                  disabled={pager.next === null}
                  onClick={() => {
                    if (pager.next !== null) {
                      onPage(pager.next);
                    }
                  }}
                >
                  ›
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载即按上次的查询（第一次为全部订单的第 1 页）查询；
// 搜索框里预先填上已提交的订单号。orderId 不为 null 时在列表右侧显示该单详情（手机只显示详情）。
export function AdminOrdersContent({ orderId = null }: { orderId?: string | null }) {
  const { replace } = useRouter();
  const [draft, setDraft] = useState(() => rememberedListQuery().order_number ?? "");
  const [query, setQuery] = useState<OrdersQuery>(rememberedListQuery);
  const [state, setState] = useState<ListState>(INITIAL_LIST);

  // 每次查询变化都发出新请求，并中止上一次的（离开页面时同样中止）。
  useEffect(() => {
    const controller = new AbortController();
    void loadOrders(query, controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
    };
  }, [query, replace]);

  const run = (next: OrdersQuery) => {
    rememberListQuery(next);
    setQuery(next);
    setState((current) => ({ ...current, busy: true, error: null }));
  };

  const list = (
    <AdminOrdersView
      draft={draft}
      status={query.status}
      state={state}
      selectedId={orderId}
      onDraft={setDraft}
      onSearch={() => {
        run(searchFor(draft, query));
      }}
      onStatus={(status) => {
        run(filterBy(status, query));
      }}
      onPage={(page) => {
        run({ ...query, page });
      }}
    />
  );
  if (orderId === null) {
    return list;
  }
  return (
    <div className="site-admin-orders-split">
      {list}
      <AdminOrderDetailContent key={orderId} id={orderId} />
    </div>
  );
}

// /admin/orders 与 /admin/orders/:id 都是本页；后者的段值即订单的内部 ID（是否合法由详情判断）。
export default function AdminOrdersPage() {
  const { pattern, params } = useRouter();
  const orderId = pattern === "/admin/orders/:id" ? (params.id ?? null) : null;
  return (
    <AdminFrame current="orders">
      <AdminOrdersContent orderId={orderId} />
    </AdminFrame>
  );
}
