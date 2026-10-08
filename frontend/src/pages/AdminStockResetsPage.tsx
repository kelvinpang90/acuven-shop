import { useEffect, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { listStockResets, readStockReset } from "../api/adminStockResets";
import type { ResetRead, ResetResult, ResetsRead, StockResetLine, StockResetList, StockResetRow } from "../api/adminStockResets";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";

// 后台库存重置结果 A07（docs/UX.md 0.10 A07，视觉稿 A07-desktop 与 A07-phone）：用后台框架渲染，当前导航项为库存重置。
// 内容区为标题 admin.stock_reset_title（h1）与其下的 admin.stock_reset_note，再下为按营业日期从新到旧的每日重置结果：
// 桌面为表格（日期、结果、SKU 数），手机为卡片，两套都渲染，由 site.css 按宽度显隐。
// 每天的明细用原生 details 与 summary（admin.col_sku_count）展开：桌面在该日表格行下方另起一行，手机在卡片里；
// 首次展开时才请求该日明细，每个 SKU 一行显示 SKU 与 admin.stock_reset_breakdown；明细为空时展开后不显示行。
// 调用 GET /api/admin/stock-resets?page=N 与 GET /api/admin/stock-resets/{内部 ID}；页码只在页面状态里。
// 翻页与没有结果时的显示沿用 A02（SHOP-TASK-048）：总数超过每页条数时列表下方为 ‹ 与 › 两个按钮，没有结果时桌面只有列头。
// 401 换成登录页 A01；离开页面或翻页时中止旧请求（含明细），旧请求的结果不再更新页面。

// 营业日期本身（YYYY-MM-DD，马来西亚日期）按界面语言显示：按 UTC 的那一天格式化，不随访客浏览器的时区偏移。
export function formatBusinessDate(date: string, language: Language): string {
  const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

// 结果标签（视觉稿 A07-desktop）：成功为 admin.stock_reset_ok 的成功标签，失败为 admin.stock_reset_failed 的危险标签。
export const RESULT_LABEL: Record<ResetResult, CopyKey> = {
  succeeded: "admin.stock_reset_ok",
  failed: "admin.stock_reset_failed",
};

export function resultTagClass(result: ResetResult): string {
  return result === "succeeded" ? "acs-tag acs-tag--success" : "acs-tag acs-tag--danger";
}

// 翻页按钮：总数不超过每页条数时不显示（null）；第一页没有上一页、最后一页没有下一页（为 null 时按钮禁用）。
export interface Pager {
  prev: number | null;
  next: number | null;
}

export function pagerOf(list: StockResetList): Pager | null {
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
  list: StockResetList | null;
  error: CopyKey | null;
}

export const INITIAL_LIST: ListState = { busy: true, list: null, error: null };

// 按列表结果决定：取到显示列表；401 回到 A01（login）；网络中断显示 common.network_check，其他失败显示 common.error_retry。
export function listStep(read: ResetsRead): ListState | "login" {
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

// 某一天明细的状态：读取中、取到的各行，或失败提示（只在该行展开处显示）。没有记录即尚未请求。
export type DetailState = { kind: "busy" } | { kind: "ok"; lines: StockResetLine[] } | { kind: "error"; error: CopyKey };

export const DETAIL_BUSY: DetailState = { kind: "busy" };

// 展开时是否要请求明细：尚未请求或上次读取失败时要；读取中与已取得明细时不要。
export function needsDetail(state: DetailState | undefined): boolean {
  return state === undefined || state.kind === "error";
}

export function detailStep(read: ResetRead): DetailState | "login" {
  switch (read.kind) {
    case "ok":
      return { kind: "ok", lines: read.reset.lines };
    case "none":
      return "login";
    case "network":
      return { kind: "error", error: "common.network_check" };
    case "failed":
      return { kind: "error", error: "common.error_retry" };
  }
}

// 页面对一次请求结果的处理：显示，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface Moves<T> {
  show: (state: T) => void;
  replace: (path: RoutePath) => void;
}

// 读取一页；signal 中止（离开页面或已翻页）之后不再更新。
export async function loadResets(page: number, signal: AbortSignal, moves: Moves<ListState>): Promise<void> {
  const read = await listStockResets(page, signal);
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

// 读取某一天的明细；signal 中止（离开页面或已翻页）之后不再更新。
export async function loadDetail(id: number, signal: AbortSignal, moves: Moves<DetailState>): Promise<void> {
  const read = await readStockReset(id, signal);
  if (signal.aborted) {
    return;
  }
  const step = detailStep(read);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 各天明细的状态，按重置记录的内部 ID。
export type DetailMap = Readonly<Record<number, DetailState>>;

function breakdown(t: ReturnType<typeof useCopy>, line: StockResetLine): string {
  return t("admin.stock_reset_breakdown", { initial: line.initial_stock, held: line.held_quantity, available: line.available_stock });
}

interface DetailsProps {
  reset: StockResetRow;
  state: DetailState | undefined;
  onOpen: (id: number) => void;
}

// 展开处：原生 details，summary 为 admin.col_sku_count；读取中标 aria-busy，失败时显示提示，取得明细后每个 SKU 一行。
// 展开（toggle 后为 open）时交给页面决定是否请求。各行的写法桌面与手机不同，由 children 按明细给出。
function ResetDetails({ reset, state, onOpen, children }: DetailsProps & { children: (lines: StockResetLine[]) => ReactNode }) {
  const t = useCopy();
  return (
    <details
      className="site-admin-stock-resets__details"
      aria-busy={state?.kind === "busy"}
      onToggle={(event: SyntheticEvent<HTMLDetailsElement>) => {
        if (event.currentTarget.open) {
          onOpen(reset.id);
        }
      }}
    >
      <summary>{t("admin.col_sku_count")}</summary>
      {state?.kind === "error" && <AdminAlert error={state.error} />}
      {state?.kind === "ok" && state.lines.length > 0 && children(state.lines)}
    </details>
  );
}

// 桌面表格的一天：日期、结果标签与 SKU 数（靠右）；其下另起一行放该日的展开处（跨三列，照 A07-desktop）。
function ResetRows({ reset, state, onOpen }: DetailsProps) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <>
      <tr>
        <td>{formatBusinessDate(reset.business_date, language)}</td>
        <td>
          <span className={resultTagClass(reset.result)}>{t(RESULT_LABEL[reset.result])}</span>
        </td>
        <td className="site-admin-stock-resets__num">{reset.sku_count}</td>
      </tr>
      <tr className="site-admin-stock-resets__detail-row">
        <td colSpan={3}>
          <ResetDetails reset={reset} state={state} onOpen={onOpen}>
            {(lines) => (
              <table className="acs-admin__table">
                <tbody>
                  {lines.map((line) => (
                    <tr key={line.sku}>
                      <td>{line.sku}</td>
                      <td>{breakdown(t, line)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ResetDetails>
        </td>
      </tr>
    </>
  );
}

// 手机卡片：顺序照 UX A07 手机线框「<日期> [admin.stock_reset_ok]」「[admin.col_sku_count] <n>」（A07-phone 把 SKU 数放在第一行、
// 标签在其下，与 UX 冲突处以 UX 为准）——第一行日期与结果标签分列两端，其下为「admin.col_sku_count n」，再下为该日的展开处，
// 每个 SKU 一项（SKU 与其下的 breakdown）。
function ResetCard({ reset, state, onOpen }: DetailsProps) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <li className="acs-admin__panel site-admin-stock-resets__card">
      <div className="site-admin-stock-resets__card-head">
        <span>{formatBusinessDate(reset.business_date, language)}</span>
        <span className={resultTagClass(reset.result)}>{t(RESULT_LABEL[reset.result])}</span>
      </div>
      <span>
        <span>{t("admin.col_sku_count")}</span> <span>{reset.sku_count}</span>
      </span>
      <ResetDetails reset={reset} state={state} onOpen={onOpen}>
        {(lines) =>
          lines.map((line) => (
            <div className="site-admin-stock-resets__line" key={line.sku}>
              <span>{line.sku}</span>
              <span className="acs-admin__muted">{breakdown(t, line)}</span>
            </div>
          ))
        }
      </ResetDetails>
    </li>
  );
}

export interface AdminStockResetsViewProps {
  state: ListState;
  details: DetailMap;
  onOpen: (id: number) => void;
  onPage: (page: number) => void;
}

export function AdminStockResetsView({ state, details, onOpen, onPage }: AdminStockResetsViewProps) {
  const t = useCopy();
  const { list } = state;
  const pager = list === null ? null : pagerOf(list);
  return (
    <div className="site-admin-stock-resets">
      <h1 className="acs-admin__h">{t("admin.stock_reset_title")}</h1>
      <p className="site-admin-stock-resets__note">{t("admin.stock_reset_note")}</p>
      <div className="site-admin-stock-resets__list" aria-busy={state.busy}>
        {state.error !== null && <AdminAlert error={state.error} />}
        {list !== null && (
          // 换页后表格与卡片按页码重新挂载，上一页展开的 details 不带到新的一页。
          <div className="site-admin-stock-resets__page" key={list.page}>
            <div className="acs-admin__panel site-admin-stock-resets__table">
              <table className="acs-admin__table">
                <thead>
                  <tr>
                    <th>{t("admin.col_date_myt")}</th>
                    <th>{t("admin.col_result")}</th>
                    <th className="site-admin-stock-resets__num">{t("admin.col_sku_count")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.resets.map((reset) => (
                    <ResetRows key={reset.id} reset={reset} state={details[reset.id]} onOpen={onOpen} />
                  ))}
                </tbody>
              </table>
            </div>
            {list.resets.length > 0 && (
              <ul className="site-admin-stock-resets__cards">
                {list.resets.map((reset) => (
                  <ResetCard key={reset.id} reset={reset} state={details[reset.id]} onOpen={onOpen} />
                ))}
              </ul>
            )}
            {pager !== null && (
              <div className="site-admin-stock-resets__pager">
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
          </div>
        )}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载即读取第 1 页。
export function AdminStockResetsContent() {
  const { replace } = useRouter();
  const [page, setPage] = useState(1);
  const [state, setState] = useState<ListState>(INITIAL_LIST);
  const [details, setDetails] = useState<DetailMap>({});
  // 进行中的明细请求，翻页或离开页面时一起中止。
  const detailRequests = useRef(new Map<number, AbortController>());

  // 每次页码变化都发出新请求，并中止上一次的列表与明细请求（离开页面时同样中止）。
  useEffect(() => {
    const controller = new AbortController();
    const requests = detailRequests.current;
    void loadResets(page, controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
      for (const request of requests.values()) {
        request.abort();
      }
      requests.clear();
    };
  }, [page, replace]);

  const openDetail = (id: number) => {
    if (!needsDetail(details[id])) {
      return;
    }
    const controller = new AbortController();
    detailRequests.current.set(id, controller);
    const show = (next: DetailState) => {
      setDetails((current) => ({ ...current, [id]: next }));
    };
    show(DETAIL_BUSY);
    void loadDetail(id, controller.signal, { show, replace });
  };

  return (
    <AdminStockResetsView
      state={state}
      details={details}
      onOpen={openDetail}
      onPage={(next) => {
        setPage(next);
        setDetails({});
        setState((current) => ({ ...current, busy: true, error: null }));
      }}
    />
  );
}

export default function AdminStockResetsPage() {
  return (
    <AdminFrame current="stockResets">
      <AdminStockResetsContent />
    </AdminFrame>
  );
}
