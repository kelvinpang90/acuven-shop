import { useEffect, useRef, useState } from "react";

import { disableAdminCoupon, listAdminCoupons } from "../api/adminCoupons";
import type { AdminCoupon, AdminCouponPage, CouponDisable, CouponsRead } from "../api/adminCoupons";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";

export function formatCouponDate(date: string, language: Language): string {
  const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

export interface Pager {
  prev: number | null;
  next: number | null;
}

export function pagerOf(list: AdminCouponPage): Pager | null {
  if (list.total <= list.page_size) {
    return null;
  }
  return { prev: list.page > 1 ? list.page - 1 : null, next: list.page * list.page_size < list.total ? list.page + 1 : null };
}

export interface ListState {
  busy: boolean;
  list: AdminCouponPage | null;
  error: CopyKey | null;
}
export const INITIAL_LIST: ListState = { busy: true, list: null, error: null };

export function listStep(read: CouponsRead): ListState | "login" {
  switch (read.kind) {
    case "ok": return { busy: false, list: read.page, error: null };
    case "none": return "login";
    case "network": return { busy: false, list: null, error: "common.network_check" };
    case "failed": return { busy: false, list: null, error: "common.error_retry" };
  }
}

export type DisableStep = { kind: "update"; coupon: AdminCoupon } | { kind: "reload" } | { kind: "error"; error: CopyKey } | "login";

export function disableStep(result: CouponDisable): DisableStep {
  switch (result.kind) {
    case "ok": return { kind: "update", coupon: result.coupon };
    case "none": return "login";
    case "csrf":
    case "not_found": return { kind: "reload" };
    case "network": return { kind: "error", error: "common.network_check" };
    case "failed": return { kind: "error", error: "common.error_retry" };
  }
}

export interface Moves<T> {
  show: (state: T) => void;
  replace: (path: RoutePath) => void;
}

export async function loadCoupons(page: number, signal: AbortSignal, moves: Moves<ListState>): Promise<void> {
  const read = await listAdminCoupons(page, signal);
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

export async function disableCoupon(id: number, csrfToken: string, signal: AbortSignal, moves: Moves<DisableStep>): Promise<void> {
  const result = await disableAdminCoupon(id, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = disableStep(result);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

function couponValue(coupon: AdminCoupon, t: ReturnType<typeof useCopy>): string {
  return coupon.type === "fixed" ? t("common.price_myr", { amount: formatSen(coupon.amount_off_sen ?? 0) }) : `${String(coupon.percent_off ?? 0)}%`;
}

function couponType(coupon: AdminCoupon): CopyKey {
  return coupon.type === "fixed" ? "admin.coupon_fixed" : "admin.coupon_percent";
}

function statusClass(coupon: AdminCoupon): string {
  return coupon.active ? "acs-tag acs-tag--neutral" : "acs-tag acs-tag--outline";
}

interface CouponProps {
  coupon: AdminCoupon;
  disabling: boolean;
  onDisable: (coupon: AdminCoupon) => void;
}

function CouponStatus({ coupon, disabling, onDisable }: CouponProps) {
  const t = useCopy();
  return <><span className={statusClass(coupon)}>{t(coupon.active ? "admin.coupon_enabled" : "admin.coupon_disabled")}</span>{coupon.active && <button className="acs-admin__btn acs-admin__btn--secondary" type="button" disabled={disabling} aria-busy={disabling} onClick={() => { onDisable(coupon); }}>{t("admin.coupon_disable")}</button>}</>;
}

function CouponRow({ coupon, disabling, onDisable }: CouponProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <tr><td>{coupon.code}</td><td>{t(couponType(coupon))}</td><td className="site-admin-coupons__value">{couponValue(coupon, t)}</td><td>{formatCouponDate(coupon.starts_on, language)} – {formatCouponDate(coupon.ends_on, language)}</td><td>{String(coupon.used_count)} / {coupon.total_use_limit === null ? "—" : String(coupon.total_use_limit)}</td><td><CouponStatus coupon={coupon} disabling={disabling} onDisable={onDisable} /></td></tr>;
}

function CouponCard({ coupon, disabling, onDisable }: CouponProps) {
  const t = useCopy(); const { language } = useLanguage();
  return <li className="acs-admin__panel site-admin-coupons__card"><div className="site-admin-coupons__card-head"><span>{coupon.code} · {couponValue(coupon, t)}</span><CouponStatus coupon={coupon} disabling={disabling} onDisable={onDisable} /></div><span className="acs-admin__muted">{t("admin.coupon_usage")} {String(coupon.used_count)} / {coupon.total_use_limit === null ? "—" : String(coupon.total_use_limit)} · {formatCouponDate(coupon.starts_on, language)} – {formatCouponDate(coupon.ends_on, language)}</span></li>;
}

export interface AdminCouponsViewProps { state: ListState; disabling: number | null; onDisable: (coupon: AdminCoupon) => void; onPage: (page: number) => void; }

export function AdminCouponsView({ state, disabling, onDisable, onPage }: AdminCouponsViewProps) {
  const t = useCopy(); const { list } = state; const pager = list === null ? null : pagerOf(list);
  return <div className="site-admin-coupons"><h1 className="acs-admin__h">{t("admin.nav_coupons")}</h1><p className="site-admin-coupons__note">{t("admin.coupon_listed_note")}</p><div className="site-admin-coupons__list" aria-busy={state.busy}>{state.error !== null && <AdminAlert error={state.error} />}{list !== null && <div className="site-admin-coupons__page" key={list.page}><div className="acs-admin__panel site-admin-coupons__table"><table className="acs-admin__table"><thead><tr><th>{t("admin.coupon_code")}</th><th>{t("admin.coupon_type")}</th><th className="site-admin-coupons__value">{t("admin.coupon_value")}</th><th>{t("admin.coupon_valid")}</th><th>{t("admin.coupon_usage")}</th><th>{t("admin.filter_status")}</th></tr></thead><tbody>{list.coupons.map((coupon) => <CouponRow key={coupon.id} coupon={coupon} disabling={disabling === coupon.id} onDisable={onDisable} />)}</tbody></table></div>{list.coupons.length > 0 && <ul className="site-admin-coupons__cards">{list.coupons.map((coupon) => <CouponCard key={coupon.id} coupon={coupon} disabling={disabling === coupon.id} onDisable={onDisable} />)}</ul>}{pager !== null && <div className="site-admin-coupons__pager"><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_prev")} disabled={pager.prev === null} onClick={() => { if (pager.prev !== null) onPage(pager.prev); }}>‹</button><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_next")} disabled={pager.next === null} onClick={() => { if (pager.next !== null) onPage(pager.next); }}>›</button></div>}</div>}</div></div>;
}

export function AdminCouponsContent() {
  const { replace } = useRouter(); const [page, setPage] = useState(1); const [state, setState] = useState<ListState>(INITIAL_LIST); const [disabling, setDisabling] = useState<number | null>(null); const disableRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void loadCoupons(page, controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
      disableRequest.current?.abort();
    };
  }, [page, replace]);
  const onDisable = (coupon: AdminCoupon) => {
    if (state.list === null || disabling !== null) return;
    const controller = new AbortController(); disableRequest.current?.abort(); disableRequest.current = controller; setDisabling(coupon.id);
    void disableCoupon(coupon.id, state.list.csrf_token, controller.signal, { replace, show: (step) => {
      if (step.kind === "update") { setState((current) => current.list === null ? current : { ...current, list: { ...current.list, coupons: current.list.coupons.map((row) => row.id === step.coupon.id ? step.coupon : row) } }); setDisabling(null); }
      else if (step.kind === "error") { setState((current) => ({ ...current, error: step.error })); setDisabling(null); }
      else void loadCoupons(page, controller.signal, { replace, show: (next) => { setState({ ...next, error: "common.error_retry" }); setDisabling(null); } });
    } });
  };
  return <AdminCouponsView state={state} disabling={disabling} onDisable={onDisable} onPage={(next) => { disableRequest.current?.abort(); setDisabling(null); setPage(next); setState((current) => ({ ...current, busy: true, error: null })); }} />;
}

export default function AdminCouponsPage() { return <AdminFrame current="coupons"><AdminCouponsContent /></AdminFrame>; }
