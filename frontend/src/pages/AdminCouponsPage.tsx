import { useEffect, useRef, useState } from "react";

import { disableAdminCoupon, listAdminCoupons } from "../api/adminCoupons";
import type { AdminCoupon, AdminCouponPage, CouponDisable, CouponsRead } from "../api/adminCoupons";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";

// A05 的日期是马来西亚营业日期，不按浏览器时区换算。
export function formatCouponDate(date: string, language: Language): string {
  const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

export interface Pager { prev: number | null; next: number | null; }
export function pagerOf(list: AdminCouponPage): Pager | null {
  if (list.total <= list.page_size) return null;
  return { prev: list.page > 1 ? list.page - 1 : null, next: list.page * list.page_size < list.total ? list.page + 1 : null };
}

export interface ListState { busy: boolean; list: AdminCouponPage | null; error: CopyKey | null; }
export const INITIAL_LIST: ListState = { busy: true, list: null, error: null };
export function listStep(read: CouponsRead): ListState | "login" {
  switch (read.kind) {
    case "ok": return { busy: false, list: read.page, error: null };
    case "none": return "login";
    case "network": return { busy: false, list: null, error: "common.network_check" };
    case "failed": return { busy: false, list: null, error: "common.error_retry" };
  }
}

export type DisableStep = { kind: "updated"; coupon: AdminCoupon } | { kind: "reload" } | { kind: "error"; error: CopyKey } | "login";
export function disableStep(read: CouponDisable): DisableStep {
  switch (read.kind) {
    case "ok": return { kind: "updated", coupon: read.coupon };
    case "none": return "login";
    case "csrf":
    case "not_found": return { kind: "reload" };
    case "network": return { kind: "error", error: "common.network_check" };
    case "failed": return { kind: "error", error: "common.error_retry" };
  }
}

export interface Moves<T> { show: (state: T) => void; replace: (path: RoutePath) => void; }
export async function loadCoupons(page: number, signal: AbortSignal, moves: Moves<ListState>): Promise<void> {
  const read = await listAdminCoupons(page, signal);
  if (signal.aborted) return;
  const step = listStep(read);
  if (step === "login") moves.replace(ADMIN_LOGIN_PATH); else moves.show(step);
}

export interface DisableMoves { update: (coupon: AdminCoupon) => void; error: (error: CopyKey) => void; reload: () => void; finish: () => void; replace: (path: RoutePath) => void; }
export async function disableCoupon(id: number, csrfToken: string, signal: AbortSignal, moves: DisableMoves): Promise<void> {
  const read = await disableAdminCoupon(id, csrfToken, signal);
  if (signal.aborted) return;
  const step = disableStep(read);
  if (step === "login") moves.replace(ADMIN_LOGIN_PATH);
  else if (step.kind === "updated") { moves.update(step.coupon); moves.finish(); }
  else if (step.kind === "reload") { moves.reload(); moves.finish(); }
  else { moves.error(step.error); moves.finish(); }
}

function valueOf(coupon: AdminCoupon, t: ReturnType<typeof useCopy>): string {
  return coupon.type === "fixed" ? t("common.price_myr", { amount: formatSen(coupon.amount_off_sen ?? 0) }) : `${String(coupon.percent_off ?? 0)}%`;
}
function validOf(coupon: AdminCoupon, language: Language): string { return `${formatCouponDate(coupon.starts_on, language)} – ${formatCouponDate(coupon.ends_on, language)}`; }
function CouponStatus({ coupon }: { coupon: AdminCoupon }) {
  const t = useCopy();
  return <span className={coupon.active ? "acs-tag acs-tag--success" : "acs-tag acs-tag--danger"}>{t(coupon.active ? "admin.coupon_enabled" : "admin.coupon_disabled")}</span>;
}
interface CouponRowProps { coupon: AdminCoupon; disabling: number | null; onDisable: (coupon: AdminCoupon) => void; }
function CouponRows({ coupon, disabling, onDisable }: CouponRowProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <tr aria-busy={disabling === coupon.id}><td>{coupon.code}</td><td>{t(coupon.type === "fixed" ? "admin.coupon_fixed" : "admin.coupon_percent")}</td><td>{valueOf(coupon, t)}</td><td>{validOf(coupon, language)}</td><td>{coupon.used_count} / {coupon.total_use_limit ?? "—"}</td><td><CouponStatus coupon={coupon} /> {coupon.active && <button className="acs-admin__btn acs-admin__btn--secondary" type="button" disabled={disabling !== null} onClick={() => { onDisable(coupon); }}>{t("admin.coupon_disable")}</button>}</td></tr>;
}
function CouponCard({ coupon, disabling, onDisable }: CouponRowProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <li className="acs-admin__panel site-admin-coupons__card" aria-busy={disabling === coupon.id}><div className="site-admin-coupons__card-head"><span>{coupon.code}</span><CouponStatus coupon={coupon} /></div><span>{valueOf(coupon, t)}</span><span>{t("admin.coupon_usage")} {coupon.used_count} / {coupon.total_use_limit ?? "—"} · {validOf(coupon, language)}</span>{coupon.active && <button className="acs-admin__btn acs-admin__btn--secondary" type="button" disabled={disabling !== null} onClick={() => { onDisable(coupon); }}>{t("admin.coupon_disable")}</button>}</li>;
}

export interface AdminCouponsViewProps { state: ListState; disabling: number | null; onDisable: (coupon: AdminCoupon) => void; onPage: (page: number) => void; }
export function AdminCouponsView({ state, disabling, onDisable, onPage }: AdminCouponsViewProps) {
  const t = useCopy(); const list = state.list; const pager = list === null ? null : pagerOf(list);
  return <div className="site-admin-coupons"><h1 className="acs-admin__h">{t("admin.nav_coupons")}</h1><p className="site-admin-coupons__note">{t("admin.coupon_listed_note")}</p><div className="site-admin-coupons__list" aria-busy={state.busy}>{state.error !== null && <AdminAlert error={state.error} />}{list !== null && <div className="site-admin-coupons__page" key={list.page}><div className="acs-admin__panel site-admin-coupons__table"><table className="acs-admin__table"><thead><tr><th>{t("admin.coupon_code")}</th><th>{t("admin.coupon_type")}</th><th>{t("admin.coupon_value")}</th><th>{t("admin.coupon_valid")}</th><th>{t("admin.coupon_usage")}</th><th>{t("admin.filter_status")}</th></tr></thead><tbody>{list.coupons.map((coupon) => <CouponRows key={coupon.id} coupon={coupon} disabling={disabling} onDisable={onDisable} />)}</tbody></table></div>{list.coupons.length > 0 && <ul className="site-admin-coupons__cards">{list.coupons.map((coupon) => <CouponCard key={coupon.id} coupon={coupon} disabling={disabling} onDisable={onDisable} />)}</ul>}{pager !== null && <div className="site-admin-coupons__pager"><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_prev")} disabled={pager.prev === null} onClick={() => { if (pager.prev !== null) onPage(pager.prev); }}>‹</button><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_next")} disabled={pager.next === null} onClick={() => { if (pager.next !== null) onPage(pager.next); }}>›</button></div>}</div>}</div></div>;
}

export function AdminCouponsContent() {
  const { replace } = useRouter(); const [page, setPage] = useState(1); const [reload, setReload] = useState(0); const [state, setState] = useState<ListState>(INITIAL_LIST); const [disabling, setDisabling] = useState<number | null>(null); const disableRequest = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); void loadCoupons(page, controller.signal, { show: (next) => { setState((current) => current.error === "common.error_retry" && next.error === null ? { ...next, error: current.error } : next); }, replace }); return () => { controller.abort(); }; }, [page, reload, replace]);
  useEffect(() => () => { disableRequest.current?.abort(); }, []);
  const disable = (coupon: AdminCoupon) => {
    if (state.list === null || disabling !== null) return;
    const controller = new AbortController(); disableRequest.current = controller; setDisabling(coupon.id);
    void disableCoupon(coupon.id, state.list.csrf_token, controller.signal, { replace, finish: () => { setDisabling(null); }, update: (updated) => { setState((current) => current.list === null ? current : { ...current, list: { ...current.list, coupons: current.list.coupons.map((row) => row.id === updated.id ? updated : row) } }); }, error: (error) => { setState((current) => ({ ...current, error })); }, reload: () => { setState((current) => ({ ...current, busy: true, error: "common.error_retry" })); setReload((current) => current + 1); } });
  };
  return <AdminCouponsView state={state} disabling={disabling} onDisable={disable} onPage={(next) => { setPage(next); setState((current) => ({ ...current, busy: true, error: null })); }} />;
}
export default function AdminCouponsPage() { return <AdminFrame current="coupons"><AdminCouponsContent /></AdminFrame>; }
