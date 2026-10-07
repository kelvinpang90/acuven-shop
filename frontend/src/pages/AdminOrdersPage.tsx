import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { isOrderStatus, ORDER_STATUSES, queryAdminOrders } from "../api/adminOrders";
import type { AdminOrderList, AdminOrderRow, OrderStatus, OrdersQuery, OrdersRead } from "../api/adminOrders";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { formatDate, usePrice } from "./TrackOrderPage";

// 后台订单 A02 的订单列表（docs/UX.md 0.9 A02 与「状态与补充（0.9）」，视觉稿 A02-desktop 的列表部分、A02-desktop-list、
// A02-desktop-empty、A02-phone-list 与 A02-phone-list-pages）：用后台框架渲染，当前导航项为订单。
// 标题之下为订单号搜索框与状态筛选，其下为列表：桌面为表格，手机为卡片，两套都渲染，由 site.css 按宽度显隐。
// 调用 POST /api/admin/orders/query；搜索、筛选与页码只在页面内存里，不进网址。提交搜索或改变筛选回到第 1 页。
// 总数超过每页条数时列表下方为上一页与下一页两个按钮（‹ 与 ›），不显示页码。没有符合条件的订单时桌面只显示列头，不另写空状态文字。
// 行不可点：订单详情与发货、待审退款数链接到 A03 留给之后的任务。
// 401 换成登录页 A01；离开页面或发出新查询时中止旧请求，旧请求的结果不再更新页面。

// 状态名（order.status_*）与标签样式（取自视觉稿 A02：已取消为描边标签，其余为中性标签）。
const STATUS_LABEL: Readonly<Record<OrderStatus, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

function statusTagClass(status: OrderStatus): string {
  return status === "demo_cancelled" ? "acs-tag acs-tag--outline" : "acs-tag acs-tag--neutral";
}

// 打开时的查询：全部订单的第 1 页。
export const FIRST_QUERY: OrdersQuery = { order_number: null, status: null, page: 1 };

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

// 桌面表格的一行：订单号、日期、状态、合计与待审数（为 0 时显示 —）。
function OrderRow({ order }: { order: AdminOrderRow }) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <tr>
      <td>{order.order_number}</td>
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

// 手机卡片：订单号与状态，其下为日期 · 合计（· 待审数，为 0 时不显示）。
function OrderCard({ order }: { order: AdminOrderRow }) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <li className="acs-admin__panel site-admin-orders__card">
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
    </li>
  );
}

export interface AdminOrdersViewProps {
  // 搜索框里的文字（尚未提交）与当前的状态筛选。
  draft: string;
  status: OrderStatus | null;
  state: ListState;
  onDraft: (value: string) => void;
  onSearch: () => void;
  onStatus: (status: OrderStatus | null) => void;
  onPage: (page: number) => void;
}

export function AdminOrdersView({ draft, status, state, onDraft, onSearch, onStatus, onPage }: AdminOrdersViewProps) {
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
                    <OrderRow key={order.id} order={order} />
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

// 框架的内容区：框架确认已登录后才挂载，挂载即查询第 1 页。
export function AdminOrdersContent() {
  const { replace } = useRouter();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState<OrdersQuery>(FIRST_QUERY);
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
    setQuery(next);
    setState((current) => ({ ...current, busy: true, error: null }));
  };

  return (
    <AdminOrdersView
      draft={draft}
      status={query.status}
      state={state}
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
}

export default function AdminOrdersPage() {
  return (
    <AdminFrame current="orders">
      <AdminOrdersContent />
    </AdminFrame>
  );
}
