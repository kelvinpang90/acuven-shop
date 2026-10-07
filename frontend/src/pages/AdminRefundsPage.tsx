import { useEffect, useState } from "react";
import type { ChangeEvent } from "react";

import { parseOrderId } from "../api/adminOrders";
import { isRefundStatus, queryAdminRefunds, REFUND_STATUSES } from "../api/adminRefunds";
import type { AdminRefundList, AdminRefundRow, RefundsQuery, RefundsRead, RefundStatus } from "../api/adminRefunds";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_REFUNDS_PATH, adminRefundPath, adminRefundsOrderPath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import AdminRefundDetailContent, { REFUND_STATUS_LABEL, RefundStatusTag } from "./AdminRefundDetail";
import { formatDateTime, usePrice } from "./TrackOrderPage";

// 后台退款审核 A03（docs/UX.md 0.10 A03 与「状态与补充（0.9）」，视觉稿 A03-desktop、A03-phone-list、A03-phone-detail、
// A03-desktop-order、A03-phone-list-order、A03-desktop-reviewed 与 A03-phone-detail-rejected）：用后台框架渲染，当前导航项为退款。
// /admin/refunds 列出全部申请；/admin/refunds/order/:orderId 只列该订单的申请（段值为订单的内部 ID），
// 状态筛选旁显示筛选标签「pay.order_no 订单号」（订单号取自返回的第一行）与链接到 /admin/refunds 的 list.filter_clear。
// /admin/refunds/:id 在桌面列表右侧显示该申请的详情与审核（AdminRefundDetail.tsx），手机只显示详情。
// 标题之下为状态筛选，其下为列表：桌面为表格，手机为卡片，两套都渲染，由 site.css 按宽度显隐。
// 表格行的订单号与整张手机卡片链接到 /admin/refunds/<内部 ID>；当前申请的表格行标 aria-selected。
// 调用 POST /api/admin/refunds/query（lang 为当前界面语言）；状态、订单内部 ID 与页码只在本模块的内存变量里（不进查询参数或浏览器存储），
// 在列表与详情之间切换时保留，详情的返回链接回到当时的列表网址；订单号只在页面内存里。
// 改变筛选回到第 1 页；翻页与没有结果时的显示沿用 A02 列表（SHOP-TASK-048）。
// 401 换成登录页 A01；离开页面、改变筛选、页码或界面语言时中止旧请求，旧请求的结果不再更新页面。

// 打开时的查询：全部状态的第 1 页；order_id 由路径决定。
export function firstQuery(orderId: number | null): RefundsQuery {
  return { status: null, order_id: orderId, page: 1 };
}

// 上次的查询（状态、订单内部 ID 与页码）：只在本模块的内存里，列表与详情各自挂载页面时取用；重新载入页面后回到全部申请的第 1 页。
let lastQuery: RefundsQuery = firstQuery(null);

export function rememberedListQuery(): RefundsQuery {
  return lastQuery;
}

export function rememberListQuery(query: RefundsQuery): void {
  lastQuery = query;
}

// 列表网址：按订单筛选时为 /admin/refunds/order/<订单内部 ID>，否则为 /admin/refunds。
export function listPathOf(query: RefundsQuery): RoutePath {
  return query.order_id === null ? ADMIN_REFUNDS_PATH : adminRefundsOrderPath(query.order_id);
}

// 改变筛选：订单筛选不变，回到第 1 页。
export function filterBy(status: RefundStatus | null, current: RefundsQuery): RefundsQuery {
  return { status, order_id: current.order_id, page: 1 };
}

// 翻页：状态与订单筛选不变。
export function pageTo(page: number, current: RefundsQuery): RefundsQuery {
  return { ...current, page };
}

// 翻页按钮：总数不超过每页条数时不显示（null）；第一页没有上一页、最后一页没有下一页（为 null 时按钮禁用）。
export interface Pager {
  prev: number | null;
  next: number | null;
}

export function pagerOf(list: AdminRefundList): Pager | null {
  if (list.total <= list.page_size) {
    return null;
  }
  return {
    prev: list.page > 1 ? list.page - 1 : null,
    next: list.page * list.page_size < list.total ? list.page + 1 : null,
  };
}

// 筛选标签里的订单号：按订单筛选时取自返回的第一行；不按订单筛选或没有行时为 null（不显示标签）。
export function filterOrderNumber(list: AdminRefundList | null, byOrder: boolean): string | null {
  if (!byOrder || list === null) {
    return null;
  }
  return list.refunds[0]?.order_number ?? null;
}

// 列表区的状态：请求进行中（busy，标 aria-busy）、取到的一页（list）与失败提示（error）。
export interface ListState {
  busy: boolean;
  list: AdminRefundList | null;
  error: CopyKey | null;
}

export const INITIAL_LIST: ListState = { busy: true, list: null, error: null };

// 路径里的订单 ID 不合法时：不发请求，只显示 common.error_retry（与 list.filter_clear）。
export const INVALID_ORDER_LIST: ListState = { busy: false, list: null, error: "common.error_retry" };

// 按查询结果决定：取到显示列表；401 回到 A01（login）；网络中断显示 common.network_check，其他失败显示 common.error_retry。
export function listStep(read: RefundsRead): ListState | "login" {
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
export async function loadRefunds(query: RefundsQuery, language: Language, signal: AbortSignal, moves: ListMoves): Promise<void> {
  const read = await queryAdminRefunds(query, language, signal);
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

// 桌面表格的一行：申请时间、订单号（链接到该申请的详情）、商品名称（逐行）、件数（与名称逐行对应）、申请金额与状态；
// 当前申请标 aria-selected。
function RefundRow({ refund, selected }: { refund: AdminRefundRow; selected: boolean }) {
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <tr aria-selected={selected ? "true" : undefined}>
      <td>{formatDateTime(refund.created_at, language)}</td>
      <td>
        <Link to={adminRefundPath(refund.id)}>{refund.order_number}</Link>
      </td>
      <td>
        <span className="site-admin-refunds__lines">
          {refund.lines.map((line, index) => (
            <span key={index}>{line.name}</span>
          ))}
        </span>
      </td>
      <td>
        <span className="site-admin-refunds__lines">
          {refund.lines.map((line, index) => (
            <span key={index}>{line.quantity}</span>
          ))}
        </span>
      </td>
      <td className="site-admin-refunds__num">{price(refund.amount_sen)}</td>
      <td>
        <RefundStatusTag status={refund.status} />
      </td>
    </tr>
  );
}

// 手机卡片（A03-phone-list）：整张为一个链接，指向该申请的详情；申请时间、订单号、名称 × 件数 · 金额、状态标签，右侧为 › 图形；
// 分隔符 · 以 aria-hidden 标出。卡片里不嵌套其他链接或按钮。
function RefundCard({ refund }: { refund: AdminRefundRow }) {
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <li>
      <Link className="acs-admin__panel site-admin-refunds__card" to={adminRefundPath(refund.id)}>
        <span className="site-admin-refunds__card-body">
          <span className="acs-admin__muted">{formatDateTime(refund.created_at, language)}</span>
          <span>{refund.order_number}</span>
          <span>
            {refund.lines.map((line, index) => (
              <span key={index}>
                <span>{`${line.name} × ${String(line.quantity)}`}</span>
                <span aria-hidden="true"> · </span>
              </span>
            ))}
            <span>{price(refund.amount_sen)}</span>
          </span>
          <span>
            <RefundStatusTag status={refund.status} />
          </span>
        </span>
        <ChevronIcon />
      </Link>
    </li>
  );
}

// 列表的范围：全部申请（all）、按路径里的订单筛选（order）、路径里的订单 ID 不合法（invalid）。
export type RefundsScope = "all" | "order" | "invalid";

// 由路径段值决定范围与查询用的订单内部 ID：段值只接受不带符号与前导零的正整数（沿用 A02 详情的 parseOrderId），否则为 invalid、不发请求。
export function scopeOf(orderId: string | null): { scope: RefundsScope; orderId: number | null } {
  if (orderId === null) {
    return { scope: "all", orderId: null };
  }
  const parsed = parseOrderId(orderId);
  return parsed === null ? { scope: "invalid", orderId: null } : { scope: "order", orderId: parsed };
}

// 挂载时的范围与查询。详情（refundId 不为 null）沿用记下的查询，范围随它的订单筛选；
// 列表路径按段值决定范围，记下的查询属于同一范围（同一订单筛选或都不按订单）时取回，否则从该路径的第 1 页、全部状态开始。
export function openingOf(orderId: string | null, refundId: string | null = null): { scope: RefundsScope; query: RefundsQuery } {
  const remembered = rememberedListQuery();
  if (refundId !== null) {
    return { scope: remembered.order_id === null ? "all" : "order", query: remembered };
  }
  const { scope, orderId: filterId } = scopeOf(orderId);
  if (scope !== "invalid" && remembered.order_id === filterId) {
    return { scope, query: remembered };
  }
  return { scope, query: firstQuery(filterId) };
}

export interface AdminRefundsViewProps {
  scope: RefundsScope;
  status: RefundStatus | null;
  state: ListState;
  // 当前打开详情的申请（路径里的内部 ID）；只在列表时为 null 或不给。
  selectedId?: string | null;
  onStatus: (status: RefundStatus | null) => void;
  onPage: (page: number) => void;
}

export function AdminRefundsView({ scope, status, state, selectedId = null, onStatus, onPage }: AdminRefundsViewProps) {
  const t = useCopy();
  const { list } = state;
  const pager = list === null ? null : pagerOf(list);
  const orderNumber = filterOrderNumber(list, scope === "order");
  return (
    <div className="site-admin-refunds">
      <h1 className="acs-admin__h">{t("admin.nav_refunds")}</h1>
      <div className="site-admin-refunds__filters">
        {scope !== "invalid" && (
          <select
            className="acs-admin__select"
            aria-label={t("admin.filter_status")}
            value={status ?? ""}
            onChange={(event: ChangeEvent<HTMLSelectElement>) => {
              const value = event.target.value;
              onStatus(isRefundStatus(value) ? value : null);
            }}
          >
            <option value="">{t("admin.filter_status")}</option>
            {REFUND_STATUSES.map((option) => (
              <option key={option} value={option}>
                {t(REFUND_STATUS_LABEL[option])}
              </option>
            ))}
          </select>
        )}
        {scope !== "all" && (
          <div className="site-admin-refunds__order">
            {orderNumber !== null && (
              <span className="acs-tag acs-tag--neutral">
                <span>{t("pay.order_no")}</span> <span>{orderNumber}</span>
              </span>
            )}
            <Link className="acs-admin__btn acs-admin__btn--secondary site-admin-refunds__clear" to={ADMIN_REFUNDS_PATH}>
              {t("list.filter_clear")}
            </Link>
          </div>
        )}
      </div>
      <div className="site-admin-refunds__list" aria-busy={state.busy}>
        {state.error !== null && <AdminAlert error={state.error} />}
        {list !== null && (
          <>
            <div className="acs-admin__panel site-admin-refunds__table">
              <table className="acs-admin__table">
                <thead>
                  <tr>
                    <th>{t("admin.col_requested_at")}</th>
                    <th>{t("pay.order_no")}</th>
                    <th>{t("order.items")}</th>
                    <th>{t("detail.quantity")}</th>
                    <th className="site-admin-refunds__num">{t("admin.col_amount")}</th>
                    <th>{t("admin.filter_status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.refunds.map((refund) => (
                    <RefundRow key={refund.id} refund={refund} selected={String(refund.id) === selectedId} />
                  ))}
                </tbody>
              </table>
            </div>
            {list.refunds.length > 0 && (
              <ul className="site-admin-refunds__cards">
                {list.refunds.map((refund) => (
                  <RefundCard key={refund.id} refund={refund} />
                ))}
              </ul>
            )}
            {pager !== null && (
              <div className="site-admin-refunds__pager">
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

// 框架的内容区：框架确认已登录后才挂载。orderId 为按订单筛选的路径段值（不按订单筛选时为 null）：
// 合法时挂载即按该订单查询，不合法时不发请求、只显示 common.error_retry 与 list.filter_clear。
// refundId 为退款详情的路径段值：不为 null 时按记下的查询显示列表，并在列表右侧显示该申请的详情（手机只显示详情）。
export function AdminRefundsContent({ orderId = null, refundId = null }: { orderId?: string | null; refundId?: string | null }) {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [opening] = useState(() => openingOf(orderId, refundId));
  const { scope } = opening;
  const [query, setQuery] = useState<RefundsQuery>(opening.query);
  const [state, setState] = useState<ListState>(scope === "invalid" ? INVALID_ORDER_LIST : INITIAL_LIST);
  // 列表区所显示结果的界面语言：切换语言后、新语言的结果到来之前，列表区同样标 aria-busy。
  const [shownLanguage, setShownLanguage] = useState<Language>(language);

  // 每次查询或界面语言变化都发出新请求，并中止上一次的（离开页面时同样中止）。路径 ID 不合法时不发请求。
  // 发出的查询记在模块内存里，供列表与详情之间切换时取回。
  useEffect(() => {
    if (scope === "invalid") {
      return undefined;
    }
    rememberListQuery(query);
    const controller = new AbortController();
    const show = (next: ListState) => {
      setState(next);
      setShownLanguage(language);
    };
    void loadRefunds(query, language, controller.signal, { show, replace });
    return () => {
      controller.abort();
    };
  }, [query, language, scope, replace]);

  const run = (next: RefundsQuery) => {
    setQuery(next);
    setState((current) => ({ ...current, busy: true, error: null }));
  };

  const list = (
    <AdminRefundsView
      scope={scope}
      status={query.status}
      state={scope !== "invalid" && shownLanguage !== language ? { ...state, busy: true } : state}
      selectedId={refundId}
      onStatus={(status) => {
        run(filterBy(status, query));
      }}
      onPage={(page) => {
        run(pageTo(page, query));
      }}
    />
  );
  if (refundId === null) {
    return list;
  }
  return (
    <div className="site-admin-refunds-split">
      {list}
      <AdminRefundDetailContent
        key={refundId}
        id={refundId}
        backTo={listPathOf(query)}
        onReviewed={() => {
          // 审核 200 之后按同一查询重新查询列表（新的对象让 effect 再执行一次）。
          setQuery((current) => ({ ...current }));
          setState((current) => ({ ...current, busy: true, error: null }));
        }}
      />
    </div>
  );
}

// 内容区按路径段值换一份：从 /admin/refunds/order/42 经 list.filter_clear 回到 /admin/refunds、换成另一张订单，
// 或在列表与详情之间、详情与详情之间切换时，内容区重新挂载，查询按 openingOf 重新决定（列表区状态从头开始并重新查询）。
export function contentKey(orderId: string | null, refundId: string | null = null): string {
  if (refundId !== null) {
    return `refund:${refundId}`;
  }
  return orderId === null ? "all" : `order:${orderId}`;
}

export function AdminRefundsRoute({ orderId, refundId = null }: { orderId: string | null; refundId?: string | null }) {
  return <AdminRefundsContent key={contentKey(orderId, refundId)} orderId={orderId} refundId={refundId} />;
}

// /admin/refunds、/admin/refunds/order/:orderId 与 /admin/refunds/:id 都是本页；按订单筛选的段值即订单的内部 ID，
// 退款详情的段值即申请的内部 ID（是否合法分别由内容区与详情判断；/admin/refunds/order 按段值 order 的详情处理）。
export default function AdminRefundsPage() {
  const { pattern, params } = useRouter();
  const orderId = pattern === "/admin/refunds/order/:orderId" ? (params.orderId ?? null) : null;
  const refundId = pattern === "/admin/refunds/:id" ? (params.id ?? null) : null;
  return (
    <AdminFrame current="refunds">
      <AdminRefundsRoute orderId={orderId} refundId={refundId} />
    </AdminFrame>
  );
}
