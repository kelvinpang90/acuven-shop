import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { createAdminCoupon, disableAdminCoupon, listAdminCoupons } from "../api/adminCoupons";
import type { AdminCoupon, AdminCouponInput, AdminCouponPage, CouponCreate, CouponDisable, CouponType, CouponsRead } from "../api/adminCoupons";
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

// 停用得到 not_found 或 csrf 后重新读取当前页：读取完成后仍显示 common.error_retry；重新读取本身失败时显示那次失败。
export function reloadedState(current: ListState, next: ListState): ListState {
  return current.error === "common.error_retry" && next.error === null ? { ...next, error: current.error } : next;
}

// 停用 ok：以返回的券替换同一内部 ID 的那一行，其他行不变。
export function updatedState(current: ListState, coupon: AdminCoupon): ListState {
  return current.list === null ? current : { ...current, list: { ...current.list, coupons: current.list.coupons.map((row) => row.id === coupon.id ? coupon : row) } };
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

export interface CouponForm {
  code: string;
  type: CouponType;
  value: string;
  starts_on: string;
  ends_on: string;
  min_spend: string;
  total_use_limit: string;
  per_member_use_limit: string;
}

export const DEFAULT_COUPON_FORM: CouponForm = {
  code: "", type: "fixed", value: "", starts_on: "", ends_on: "", min_spend: "0", total_use_limit: "", per_member_use_limit: "",
};

// 表单金额是 RM 字符串；接口只接受整数仙。空的次数上限代表不限。
export function couponInput(form: CouponForm): AdminCouponInput {
  const sen = (value: string) => Math.round(Number(value) * 100);
  const limit = (value: string) => value === "" ? null : Number(value);
  return {
    code: form.code,
    type: form.type,
    amount_off_sen: form.type === "fixed" ? sen(form.value) : null,
    percent_off: form.type === "percent" ? Number(form.value) : null,
    min_subtotal_sen: sen(form.min_spend),
    starts_on: form.starts_on,
    ends_on: form.ends_on,
    total_use_limit: limit(form.total_use_limit),
    per_member_use_limit: limit(form.per_member_use_limit),
  };
}

export type CreateStep = "login" | { kind: "ok" } | { kind: "reload"; page: number; error: CopyKey | null } | { kind: "error"; error: CopyKey };
export function createStep(read: CouponCreate, page: number): CreateStep {
  switch (read.kind) {
    case "ok": return { kind: "ok" };
    case "none": return "login";
    case "csrf": return { kind: "reload", page, error: "common.error_retry" };
    case "network": return { kind: "reload", page: 1, error: "common.network_check" };
    case "code_taken": return { kind: "error", error: "admin.coupon_code_taken" };
    case "invalid": return { kind: "error", error: "admin.coupon_invalid" };
    case "failed": return { kind: "error", error: "common.error_retry" };
  }
}

export interface CreateMoves {
  success: () => void;
  error: (error: CopyKey) => void;
  reload: (page: number, error: CopyKey) => void;
  replace: (path: RoutePath) => void;
}
export async function submitCoupon(input: AdminCouponInput, csrfToken: string, page: number, signal: AbortSignal, moves: CreateMoves): Promise<void> {
  const read = await createAdminCoupon(input, csrfToken, signal);
  if (signal.aborted) return;
  const step = createStep(read, page);
  if (step === "login") moves.replace(ADMIN_LOGIN_PATH);
  else if (step.kind === "ok") moves.success();
  else if (step.kind === "reload") moves.reload(step.page, step.error ?? "common.error_retry");
  else moves.error(step.error);
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

export interface CouponFormViewProps {
  form: CouponForm;
  saving: boolean;
  error: CopyKey | null;
  onChange: (change: (form: CouponForm) => CouponForm) => void;
  onSubmit: () => void;
}
export function CouponFormView({ form, saving, error, onChange, onSubmit }: CouponFormViewProps) {
  const t = useCopy();
  const change = (key: keyof CouponForm) => (event: ChangeEvent<HTMLInputElement>) => { const value = event.target.value; onChange((current) => ({ ...current, [key]: value })); };
  const valueProps = form.type === "fixed" ? { min: "0.01", max: "100000", step: "0.01" } : { min: "1", max: "100", step: "1" };
  return <form className="acs-admin__panel site-admin-coupons__form" onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); onSubmit(); }}>
    <h2 className="acs-admin__h">{t("common.create")}</h2>
    <label className="site-admin-coupons__field">{t("admin.coupon_code")}<input className="acs-admin__input" required value={form.code} onChange={change("code")} /></label>
    <fieldset className="site-admin-coupons__field"><legend>{t("admin.coupon_type")}</legend>
      <label><input type="radio" name="coupon-type" checked={form.type === "fixed"} onChange={() => { onChange((current) => ({ ...current, type: "fixed" })); }} /> {t("admin.coupon_fixed")}</label>
      <label><input type="radio" name="coupon-type" checked={form.type === "percent"} onChange={() => { onChange((current) => ({ ...current, type: "percent" })); }} /> {t("admin.coupon_percent")}</label>
    </fieldset>
    <label className="site-admin-coupons__field">{t("admin.coupon_value")}<input className="acs-admin__input" type="number" required value={form.value} onChange={change("value")} {...valueProps} /></label>
    <fieldset className="site-admin-coupons__field"><legend>{t("admin.coupon_valid")}</legend><input className="acs-admin__input" type="date" required aria-label={t("admin.coupon_valid")} value={form.starts_on} onChange={change("starts_on")} /><input className="acs-admin__input" type="date" required aria-label={t("admin.coupon_valid")} value={form.ends_on} onChange={change("ends_on")} /></fieldset>
    <label className="site-admin-coupons__field">{t("admin.coupon_min_spend")}<input className="acs-admin__input" type="number" required min="0" max="100000" step="0.01" value={form.min_spend} onChange={change("min_spend")} /></label>
    <label className="site-admin-coupons__field">{t("admin.coupon_total_limit")}<input className="acs-admin__input" type="number" min="1" max="1000000" step="1" value={form.total_use_limit} onChange={change("total_use_limit")} /></label>
    <label className="site-admin-coupons__field">{t("admin.coupon_member_limit")}<input className="acs-admin__input" type="number" min="1" max="1000000" step="1" value={form.per_member_use_limit} onChange={change("per_member_use_limit")} /></label>
    <div className="site-admin-coupons__save"><span>{t("admin.rules_apply_new")}</span><button className="acs-admin__btn" type="submit" disabled={saving} aria-busy={saving}>{t("common.save")}</button>{error !== null && <AdminAlert error={error} />}</div>
  </form>;
}

export interface AdminCouponsViewProps extends Partial<CouponFormViewProps> { state: ListState; disabling: number | null; creating?: boolean; formOpen?: boolean; onDisable: (coupon: AdminCoupon) => void; onPage: (page: number) => void; onCreate?: () => void; onBack?: () => void; }
export function AdminCouponsView({ state, disabling, onDisable, onPage, form = DEFAULT_COUPON_FORM, saving = false, error = null, onChange = () => undefined, onSubmit = () => undefined, creating = false, formOpen = false, onCreate = () => undefined, onBack = () => undefined }: AdminCouponsViewProps) {
  const t = useCopy(); const list = state.list; const pager = list === null ? null : pagerOf(list);
  return <div className={`site-admin-coupons${formOpen ? " site-admin-coupons--form-open" : ""}`}><div className="site-admin-coupons__heading"><h1 className="acs-admin__h">{t("admin.nav_coupons")}</h1><button className="acs-admin__btn" type="button" disabled={list === null || state.busy || creating} onClick={onCreate}>{t("common.create")}</button></div><p className="site-admin-coupons__note">{t("admin.coupon_listed_note")}</p>{formOpen && <div className="site-admin-coupons__new"><button className="acs-admin__btn acs-admin__btn--secondary" type="button" onClick={onBack}>{t("common.back")}</button><CouponFormView form={form} saving={saving} error={error} onChange={onChange} onSubmit={onSubmit} /></div>}<div className="site-admin-coupons__list" aria-busy={state.busy}>{state.error !== null && <AdminAlert error={state.error} />}{list !== null && <div className="site-admin-coupons__page" key={list.page}><div className="acs-admin__panel site-admin-coupons__table"><table className="acs-admin__table"><thead><tr><th>{t("admin.coupon_code")}</th><th>{t("admin.coupon_type")}</th><th>{t("admin.coupon_value")}</th><th>{t("admin.coupon_valid")}</th><th>{t("admin.coupon_usage")}</th><th>{t("admin.filter_status")}</th></tr></thead><tbody>{list.coupons.map((coupon) => <CouponRows key={coupon.id} coupon={coupon} disabling={disabling} onDisable={onDisable} />)}</tbody></table></div>{list.coupons.length > 0 && <ul className="site-admin-coupons__cards">{list.coupons.map((coupon) => <CouponCard key={coupon.id} coupon={coupon} disabling={disabling} onDisable={onDisable} />)}</ul>}{pager !== null && <div className="site-admin-coupons__pager"><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_prev")} disabled={pager.prev === null} onClick={() => { if (pager.prev !== null) onPage(pager.prev); }}>‹</button><button className="acs-admin__btn acs-admin__btn--secondary" type="button" aria-label={t("common.a11y_page_next")} disabled={pager.next === null} onClick={() => { if (pager.next !== null) onPage(pager.next); }}>›</button></div>}</div>}</div></div>;
}

export function AdminCouponsContent() {
  const { replace } = useRouter(); const [page, setPage] = useState(1); const [reload, setReload] = useState(0); const [state, setState] = useState<ListState>(INITIAL_LIST); const [disabling, setDisabling] = useState<number | null>(null); const [formOpen, setFormOpen] = useState(false); const [form, setForm] = useState<CouponForm>(DEFAULT_COUPON_FORM); const [saving, setSaving] = useState(false); const [saveError, setSaveError] = useState<CopyKey | null>(null); const disableRequest = useRef<AbortController | null>(null); const createRequest = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); void loadCoupons(page, controller.signal, { show: (next) => { setState((current) => reloadedState(current, next)); }, replace }); return () => { controller.abort(); }; }, [page, reload, replace]);
  useEffect(() => () => { disableRequest.current?.abort(); createRequest.current?.abort(); }, []);
  const disable = (coupon: AdminCoupon) => {
    if (state.list === null || disabling !== null) return;
    const controller = new AbortController(); disableRequest.current = controller; setDisabling(coupon.id);
    void disableCoupon(coupon.id, state.list.csrf_token, controller.signal, { replace, finish: () => { setDisabling(null); }, update: (updated) => { setState((current) => updatedState(current, updated)); }, error: (error) => { setState((current) => ({ ...current, error })); }, reload: () => { setState((current) => ({ ...current, busy: true, error: "common.error_retry" })); setReload((current) => current + 1); } });
  };
  const submit = () => {
    if (saving) return;
    if (state.list === null) { setSaveError("common.error_retry"); return; }
    const controller = new AbortController(); createRequest.current = controller; setSaving(true); setSaveError(null);
    void submitCoupon(couponInput(form), state.list.csrf_token, page, controller.signal, { replace, success: () => { setSaving(false); setForm(DEFAULT_COUPON_FORM); setFormOpen(false); setPage(1); setState((current) => ({ ...current, busy: true })); setReload((current) => current + 1); }, error: (error) => { setSaving(false); setSaveError(error); }, reload: (next, error) => { setSaving(false); setSaveError(error); setPage(next); setState((current) => ({ ...current, busy: true })); setReload((current) => current + 1); } });
  };
  return <AdminCouponsView state={state} disabling={disabling} creating={saving} formOpen={formOpen} form={form} saving={saving} error={saveError} onChange={(change) => { setForm(change); }} onSubmit={submit} onCreate={() => { setFormOpen(true); }} onBack={() => { setFormOpen(false); }} onDisable={disable} onPage={(next) => { setPage(next); setState((current) => ({ ...current, busy: true, error: null })); }} />;
}
export default function AdminCouponsPage() { return <AdminFrame current="coupons"><AdminCouponsContent /></AdminFrame>; }
