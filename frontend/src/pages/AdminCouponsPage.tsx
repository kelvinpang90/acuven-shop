import { useEffect, useRef, useState } from "react";

import { disableAdminCoupon, listAdminCoupons } from "../api/adminCoupons";
import type { AdminCoupon, AdminCouponPage, CouponDisable, CouponsRead } from "../api/adminCoupons";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";

export function formatBusinessDate(date: string, language: Language): string {
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
export interface Moves<T> { show: (state: T) => void; replace: (path: RoutePath) => void; }
export async function loadCoupons(page: number, signal: AbortSignal, moves: Moves<ListState>): Promise<void> {
  const read = await listAdminCoupons(page, signal);
  if (signal.aborted) return;
  const step = listStep(read);
  if (step === "login") moves.replace(ADMIN_LOGIN_PATH); else moves.show(step);
}
export interface DisableState { busyId: number | null; error: CopyKey | null; }
export const INITIAL_DISABLE: DisableState = { busyId: null, error: null };
export type DisableStep = { kind: "show"; coupon: AdminCoupon } | { kind: "reload" } | { kind: "login" } | { kind: "error"; error: CopyKey };
export function disableStep(read: CouponDisable): DisableStep {
  switch (read.kind) {
    case "ok": return { kind: "show", coupon: read.coupon };
    case "none": return { kind: "login" };
    case "csrf": case "not_found": return { kind: "reload" };
    case "network": return { kind: "error", error: "common.network_check" };
    case "failed": return { kind: "error", error: "common.error_retry" };
  }
}
export function replaceCoupon(list: AdminCouponPage, coupon: AdminCoupon): AdminCouponPage {
  return { ...list, coupons: list.coupons.map((row) => row.id === coupon.id ? coupon : row) };
}
interface CouponProps { coupon: AdminCoupon; disableBusy: boolean; busy: boolean; onDisable: (coupon: AdminCoupon) => void; }
function valueOf(coupon: AdminCoupon, t: ReturnType<typeof useCopy>): string {
  return coupon.type === "fixed" ? t("common.price_myr", { amount: formatSen(coupon.amount_off_sen ?? 0) }) : `${coupon.percent_off ?? 0}%`;
}
function CouponTag({ coupon }: { coupon: AdminCoupon }) {
  const t = useCopy();
  return <span className={coupon.active ? "acs-tag acs-tag--success" : "acs-tag acs-tag--danger"}>{t(coupon.active ? "admin.coupon_enabled" : "admin.coupon_disabled")}</span>;
}
function DisableButton({ coupon, disableBusy, busy, onDisable }: CouponProps) {
  const t = useCopy();
  return coupon.active ? <button className="acs-admin__btn acs-admin__btn--secondary" type="button" disabled={disableBusy} aria-busy={busy} onClick={() => { onDisable(coupon); }}>{t("admin.coupon_disable")}</button> : null;
}
function CouponRow({ coupon, disableBusy, busy, onDisable }: CouponProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <tr><td>{coupon.code}</td><td>{t(coupon.type === "fixed" ? "admin.coupon_fixed" : "admin.coupon_percent")}</td><td>{valueOf(coupon, t)}</td><td>{formatBusinessDate(coupon.starts_on, language)} – {formatBusinessDate(coupon.ends_on, language)}</td><td>{coupon.used_count} / {coupon.total_use_limit ?? "—"}</td><td><CouponTag coupon={coupon} /> <DisableButton coupon={coupon} disableBusy={disableBusy} busy={busy} onDisable={onDisable} /></td></tr>;
}
function CouponCard({ coupon, disableBusy, busy, onDisable }: CouponProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <li className="acs-admin__panel site-admin-coupons__card"><div className="site-admin-coupons__card-head"><span>{coupon.code}</span><span>{valueOf(coupon, t)}</span><CouponTag coupon={coupon} /></div><span>{t("admin.coupon_usage")} {coupon.used_count} / {coupon.total_use_limit ?? "—"} · {formatBusinessDate(coupon.starts_on, language)} – {formatBusinessDate(coupon.ends_on, language)}</span><DisableButton coupon={coupon} disableBusy={disableBusy} busy={busy} onDisable={onDisable} /></li>;
}
export interface AdminCouponsViewProps { state: ListState; disable: DisableState; onPage: (page: number) => void; onDisable: (coupon: AdminCoupon) => void; }
export function AdminCouponsView({ state, disable, onPage, onDisable }: AdminCouponsViewProps) {
  const t = useCopy(); const { list } = state; const pager = list === null ? null : pagerOf(list);
  return <div className="site-admin-coupons"><h1 className="acs-admin__h">{t("admin.nav_coupons")}</h1><p className="site-admin-coupons__note">{t("admin.coupon_listed_note")}</p><div className="site-admin-coupons__list" aria-busy={state.busy}>{state.error !== null && <AdminAlert error={state.error} />}{disable.error !== null && <AdminAlert error={disable.error} />}{list !== null && <div className="site-admin-coupons__page" key={list.page}><div className="acs-admin__panel site-admin-coupons__table"><table className="acs-admin__table"><thead><tr><th>{t("admin.coupon_code")}</th><th>{t("admin.coupon_type")}</th><th>{t("admin.coupon_value")}</th><th>{t("admin.coupon_valid")}</th><th>{t("admin.coupon_usage")}</th><th>{t("admin.filter_status")}</th></tr></thead><tbody>{list.coupons.map((coupon) => <CouponRow key={coupon.id} coupon={coupon} disableBusy={disable.busyId !== null} busy={disable.busyId === coupon.id} onDisable={onDisable} />)}</tbody></table></div>{list.coupons.length > 0 && <ul className="site-admin-coupons__cards">{list.coupons.map((coupon) => <CouponCard key={coupon.id} coupon={coupon} disableBusy={disable.busyId !== null} busy={disable.busyId === coupon.id} onDisable={onDisable} />)}</ul>}{pager !== null && <div className="site-admin-coupons__pager"><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_prev")} disabled={pager.prev === null} onClick={() => { if (pager.prev !== null) onPage(pager.prev); }}>‹</button><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_next")} disabled={pager.next === null} onClick={() => { if (pager.next !== null) onPage(pager.next); }}>›</button></div>}</div>}</div></div>;
}
export function AdminCouponsContent() {
  const { replace } = useRouter(); const [page, setPage] = useState(1); const [state, setState] = useState<ListState>(INITIAL_LIST); const [disable, setDisable] = useState<DisableState>(INITIAL_DISABLE); const disableRequest = useRef<AbortController | null>(null); const listRequest = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); listRequest.current = controller; void loadCoupons(page, controller.signal, { show: setState, replace }); return () => { controller.abort(); listRequest.current?.abort(); disableRequest.current?.abort(); }; }, [page, replace]);
  const reloadWithError = () => { setState((current) => ({ ...current, busy: true, error: "common.error_retry" })); listRequest.current?.abort(); const controller = new AbortController(); listRequest.current = controller; void loadCoupons(page, controller.signal, { show: (next) => setState(next.error === null ? { ...next, error: "common.error_retry" } : next), replace }); };
  const onDisable = (coupon: AdminCoupon) => { if (state.list === null || disable.busyId !== null) return; const controller = new AbortController(); disableRequest.current = controller; setDisable({ busyId: coupon.id, error: null }); void disableAdminCoupon(coupon.id, state.list.csrf_token, controller.signal).then((read) => { if (controller.signal.aborted) return; const step = disableStep(read); setDisable(INITIAL_DISABLE); if (step.kind === "login") replace(ADMIN_LOGIN_PATH); else if (step.kind === "show") setState((current) => current.list === null ? current : { ...current, list: replaceCoupon(current.list, step.coupon) }); else if (step.kind === "reload") reloadWithError(); else setDisable({ busyId: null, error: step.error }); }); };
  return <AdminCouponsView state={state} disable={disable} onPage={(next) => { setPage(next); setState((current) => ({ ...current, busy: true, error: null })); }} onDisable={onDisable} />;
}
export default function AdminCouponsPage() { return <AdminFrame current="coupons"><AdminCouponsContent /></AdminFrame>; }
