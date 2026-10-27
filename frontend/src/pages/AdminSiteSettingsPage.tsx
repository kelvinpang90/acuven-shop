import { useEffect, useRef, useState } from "react";
import type { Dispatch, FormEvent, SetStateAction } from "react";

import { readAdminSiteSettings, saveAdminSiteSettings } from "../api/adminSiteSettings";
import type { AdminSiteSettings, PaymentMethod, SiteSettingsRead, SiteSettingsSave } from "../api/adminSiteSettings";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";

// 后台站点设置 A09（docs/UX.md 0.14 A09，视觉稿 A09-stripe-desktop 与 A09-stripe-phone）：用后台框架渲染，当前导航项为站点设置。
// 内容区为标题 admin.nav_settings（h1）；A09-stripe-phone 不画标题（顶栏已是 admin.nav_settings），h1 仍在页面里，
// 由 site.css 在 767px 及以下只留给读屏。其下为表单：先是短信验证开关（SHOP-TASK-169）：admin.sms_toggle 一组两个单选（开启与关闭）、
// admin.sms_toggle_hint 与 admin.sms_toggle_cost；短信服务不可用时开启选项禁用、其下显示 admin.sms_not_configured，当前已开启时照常
// 显示为选中、仍可改选关闭并保存（DESIGN 1.15「边界与原则」短信验证开关一条，写法照 Stripe，HANDOFF 0.65）。再是支付方式（SHOP-TASK-139）：
// admin.payment_method 一组两个单选（模拟与 Stripe 测试）与 admin.payment_method_hint；Stripe 测试凭据不可用时 Stripe 选项禁用、其下显示
// admin.stripe_not_configured，当前已是 Stripe 测试时照常显示为选中、仍可改选模拟并保存（DESIGN 1.12「Stripe 测试环境支付」第 2 条）。
// 最后为 common.save，一次提交两项；不加确认框。
// 打开页面与切换界面语言时调用 GET /api/admin/site-settings，保存为 PUT（经 api/adminSiteSettings.ts；配置检查与审计在服务端）。
// 401 换成登录页 A01；离开页面或切换语言时中止读取与进行中的保存，旧请求的结果不再更新页面。

// 短信的两个单选依次为开启与关闭（UX A09 线框的顺序）及其文字。
export const SMS_OPTIONS: readonly { enabled: boolean; value: string; label: CopyKey }[] = [
  { enabled: true, value: "on", label: "admin.sms_on" },
  { enabled: false, value: "off", label: "admin.sms_off" },
];

// 两个单选依次为模拟与 Stripe 测试（UX A09 线框的顺序）及其文字。
export const PAYMENT_OPTIONS: readonly { method: PaymentMethod; label: CopyKey }[] = [
  { method: "simulated", label: "admin.payment_simulated" },
  { method: "stripe_test", label: "admin.payment_stripe" },
];

// 开启选项下方说明的 id（单选以 aria-describedby 指向它，照 Stripe 的写法）。
export const SMS_NOTE_ID = "site-admin-settings-sms-note";

// Stripe 选项下方说明的 id（单选以 aria-describedby 指向它，同视觉稿 A09-stripe-phone）。
export const STRIPE_NOTE_ID = "site-admin-settings-stripe-note";

// 页面的状态：读取中（标 aria-busy，不显示表单）、读取失败（只有提示）、已取到。已取到时 base 为读取的设置（保存成功后换成返回的
// 支付方式与短信开关，两种可用状态与 CSRF 令牌沿用读取的），choice 为所选的支付方式，smsChoice 为所选的短信开关，saving 为保存进行中，
// saved 为显示 admin.settings_saved，rejected 为保存被拒（凭据不可用）时在保存按钮下方显示 admin.stripe_not_configured，smsRejected 为
// 保存被拒（短信服务不可用）时在同一位置显示 admin.sms_not_configured，alert 为表单上方的提示（403 后重新读取），
// saveError 为保存按钮下方的其他失败提示。
export type SettingsState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | {
      status: "ready";
      base: AdminSiteSettings;
      choice: PaymentMethod;
      smsChoice: boolean;
      saving: boolean;
      saved: boolean;
      rejected: boolean;
      smsRejected: boolean;
      alert: CopyKey | null;
      saveError: CopyKey | null;
    };

export const LOADING: SettingsState = { status: "loading" };

function ready(base: AdminSiteSettings, alert: CopyKey | null, saved = false): SettingsState {
  return {
    status: "ready",
    base,
    choice: base.payment_method,
    smsChoice: base.sms_verification_enabled,
    saving: false,
    saved,
    rejected: false,
    smsRejected: false,
    alert,
    saveError: null,
  };
}

// 按读取结果决定：取到以结果的当前值为选中项（alert 为表单上方的提示）；401 回到 A01（login）；网络中断 common.network_check，
// 其他失败 common.error_retry，都不显示表单。
export function readStep(read: SiteSettingsRead, alert: CopyKey | null = null): SettingsState | "login" {
  switch (read.kind) {
    case "ok":
      return ready(read.settings, alert);
    case "none":
      return "login";
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 对当前状态的一次改动（保存的结果按保存时的状态更新，不覆盖保存进行中的其他状态）。
export type SettingsChange = (current: SettingsState) => SettingsState;

function failedSave(change: { rejected: true } | { smsRejected: true } | { saveError: CopyKey }): SettingsChange {
  return (current) => (current.status === "ready" ? { ...current, saving: false, ...change } : current);
}

// 按保存结果决定：200 以返回的支付方式与短信开关更新两组的选中项并显示 admin.settings_saved；401 回到 A01；403 重新读取（reread）；
// 凭据不可用（422 stripe_not_configured）在保存按钮下方显示 admin.stripe_not_configured、短信服务不可用（422 sms_not_configured）
// 在同一位置显示 admin.sms_not_configured，都不改两组的选中项；网络中断 common.network_check，其他失败 common.error_retry，都保留表单。
export function saveStep(save: SiteSettingsSave, base: AdminSiteSettings): SettingsChange | "login" | "reread" {
  switch (save.kind) {
    case "ok": {
      const saved = ready({ ...base, payment_method: save.payment_method, sms_verification_enabled: save.sms_verification_enabled }, null, true);
      return () => saved;
    }
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "not_configured":
      return failedSave({ rejected: true });
    case "sms_not_configured":
      return failedSave({ smsRejected: true });
    case "network":
      return failedSave({ saveError: "common.network_check" });
    case "failed":
      return failedSave({ saveError: "common.error_retry" });
  }
}

// 改选一项支付方式：更新选中项，收起 admin.settings_saved 与保存被拒的 admin.stripe_not_configured、admin.sms_not_configured。
export function chooseMethod(state: SettingsState, method: PaymentMethod): SettingsState {
  return state.status === "ready" ? { ...state, choice: method, saved: false, rejected: false, smsRejected: false } : state;
}

// 改选短信开关：同 chooseMethod，更新短信的选中项并收起保存的提示。
export function chooseSms(state: SettingsState, enabled: boolean): SettingsState {
  return state.status === "ready" ? { ...state, smsChoice: enabled, saved: false, rejected: false, smsRejected: false } : state;
}

// 点保存：按钮禁用并标 aria-busy，先前的提示都收起。
export function startSaving(state: SettingsState): SettingsState {
  return state.status === "ready"
    ? { ...state, saving: true, saved: false, rejected: false, smsRejected: false, alert: null, saveError: null }
    : state;
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface SettingsMoves {
  show: (state: SettingsState) => void;
  update: (change: SettingsChange) => void;
  replace: (path: RoutePath) => void;
}

// 读取（打开页面、切换语言与 403 之后）；signal 中止（离开页面或已切换语言）之后不再更新。
export async function openSettings(signal: AbortSignal, moves: SettingsMoves, alert: CopyKey | null = null): Promise<void> {
  const read = await readAdminSiteSettings(signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read, alert);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 保存两组所选的项（带读取给的 CSRF 令牌）；signal 中止之后其结果不再更新表单。403 时与首次读取一样显示读取中，
// 取得后以结果重置表单并在表单上方显示 common.error_retry。
export async function submitSettings(
  base: AdminSiteSettings,
  choice: PaymentMethod,
  smsChoice: boolean,
  signal: AbortSignal,
  moves: SettingsMoves,
): Promise<void> {
  const save = await saveAdminSiteSettings(choice, smsChoice, base.csrf_token, signal);
  if (signal.aborted) {
    return;
  }
  const step = saveStep(save, base);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else if (step === "reread") {
    moves.show(LOADING);
    await openSettings(signal, moves, "common.error_retry");
  } else {
    moves.update(step);
  }
}

// 页面状态与它所属的界面语言：语言换了而新结果还没到时按读取中显示。
export interface SettingsEntry {
  language: Language;
  state: SettingsState;
}

export function shownState(entry: SettingsEntry, language: Language): SettingsState {
  return entry.language === language ? entry.state : LOADING;
}

function pageMoves(language: Language, setEntry: Dispatch<SetStateAction<SettingsEntry>>, replace: (path: RoutePath) => void): SettingsMoves {
  return {
    show: (state) => {
      setEntry({ language, state });
    },
    update: (change) => {
      setEntry((current) => ({ language, state: change(current.state) }));
    },
    replace,
  };
}

type ReadyState = Extract<SettingsState, { status: "ready" }>;

interface FormHandlers {
  onChoose: (method: PaymentMethod) => void;
  onChooseSms: (enabled: boolean) => void;
  onSave: () => void;
}

function DemoMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 3l9 9-9 9-9-9z" />
    </svg>
  );
}

// 短信验证一组（A09-stripe-desktop 与 A09-stripe-phone；未配置的状态照其中 Stripe 未配置的写法）：两个单选，短信服务不可用时开启选项
// 禁用（文字为次要文字）、其下为 admin.sms_not_configured；再是 admin.sms_toggle_hint 与始终显示的 ★ admin.sms_toggle_cost。
function SmsGroup({ state, onChooseSms }: { state: ReadyState } & Pick<FormHandlers, "onChooseSms">) {
  const t = useCopy();
  const unavailable = !state.base.sms_available;
  return (
    <fieldset className="acs-admin__panel site-admin-settings__group site-admin-settings__sms">
      <legend>{t("admin.sms_toggle")}</legend>
      {SMS_OPTIONS.map(({ enabled, value, label }) => {
        const disabled = enabled && unavailable;
        return (
          <label key={value} className={disabled ? "acs-admin__muted site-admin-settings__option" : "site-admin-settings__option"}>
            <input
              type="radio"
              name="sms_verification_enabled"
              value={value}
              checked={state.smsChoice === enabled}
              disabled={disabled}
              aria-describedby={disabled ? SMS_NOTE_ID : undefined}
              onChange={() => {
                onChooseSms(enabled);
              }}
            />
            <span>{t(label)}</span>
          </label>
        );
      })}
      {unavailable && (
        <div id={SMS_NOTE_ID} className="acs-admin__alert">
          <span>{t("admin.sms_not_configured")}</span>
        </div>
      )}
      <span className="acs-admin__muted">{t("admin.sms_toggle_hint")}</span>
      <div className="site-admin-settings__cost">
        <DemoMark />
        <span>{t("admin.sms_toggle_cost")}</span>
      </div>
    </fieldset>
  );
}

// 支付方式一组（A09-stripe-desktop 与 A09-stripe-phone）：两个单选，Stripe 测试凭据不可用时 Stripe 选项禁用（文字为次要文字，同视觉稿）、
// 其下为 admin.stripe_not_configured；最后为 admin.payment_method_hint。
function PaymentGroup({ state, onChoose }: { state: ReadyState } & Pick<FormHandlers, "onChoose">) {
  const t = useCopy();
  const unavailable = !state.base.stripe_available;
  return (
    <fieldset className="acs-admin__panel site-admin-settings__group site-admin-settings__payment">
      <legend>{t("admin.payment_method")}</legend>
      {PAYMENT_OPTIONS.map(({ method, label }) => {
        const disabled = method === "stripe_test" && unavailable;
        return (
          <label key={method} className={disabled ? "acs-admin__muted site-admin-settings__option" : "site-admin-settings__option"}>
            <input
              type="radio"
              name="payment_method"
              value={method}
              checked={state.choice === method}
              disabled={disabled}
              aria-describedby={disabled ? STRIPE_NOTE_ID : undefined}
              onChange={() => {
                onChoose(method);
              }}
            />
            <span>{t(label)}</span>
          </label>
        );
      })}
      {unavailable && (
        <div id={STRIPE_NOTE_ID} className="acs-admin__alert">
          <span>{t("admin.stripe_not_configured")}</span>
        </div>
      )}
      <span className="acs-admin__muted">{t("admin.payment_method_hint")}</span>
    </fieldset>
  );
}

function SettingsFormView({ state, onChoose, onChooseSms, onSave }: { state: ReadyState } & FormHandlers) {
  const t = useCopy();
  return (
    <form
      className="site-admin-settings__form"
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSave();
      }}
    >
      {state.alert !== null && <AdminAlert error={state.alert} />}
      <SmsGroup state={state} onChooseSms={onChooseSms} />
      <PaymentGroup state={state} onChoose={onChoose} />
      <div className="site-admin-settings__save">
        <button className="acs-admin__btn" type="submit" disabled={state.saving} aria-busy={state.saving}>
          {t("common.save")}
        </button>
        {state.saved && <span role="status">{t("admin.settings_saved")}</span>}
        {state.smsRejected && <AdminAlert error="admin.sms_not_configured" />}
        {state.rejected && <AdminAlert error="admin.stripe_not_configured" />}
        {state.saveError !== null && <AdminAlert error={state.saveError} />}
      </div>
    </form>
  );
}

export interface AdminSiteSettingsViewProps extends FormHandlers {
  state: SettingsState;
}

// 标题之下为表单区：读取中标 aria-busy、不显示表单；读取失败时只有提示。
export function AdminSiteSettingsView({ state, ...handlers }: AdminSiteSettingsViewProps) {
  const t = useCopy();
  return (
    <div className="site-admin-settings">
      <h1 className="acs-admin__h site-admin-settings__title">{t("admin.nav_settings")}</h1>
      <div className="site-admin-settings__body" aria-busy={state.status === "loading"}>
        {state.status === "failed" && <AdminAlert error={state.error} />}
        {state.status === "ready" && <SettingsFormView state={state} {...handlers} />}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载与切换界面语言时读取。
export function AdminSiteSettingsContent() {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [entry, setEntry] = useState<SettingsEntry>({ language, state: LOADING });
  // 每次切换语言都先回到读取中（含读取未到时又换回先前语言的情形），与首次读取相同。
  if (entry.language !== language) {
    setEntry({ language, state: LOADING });
  }
  // 当前语言下的请求（读取、保存与 403 后的重新读取）共用的中止；离开页面或切换语言时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openSettings(controller.signal, pageMoves(language, setEntry, replace));
    });
  }, [language, replace]);

  const state = shownState(entry, language);

  return (
    <AdminSiteSettingsView
      state={state}
      onChoose={(method) => {
        setEntry((current) => (current.language === language ? { language, state: chooseMethod(current.state, method) } : current));
      }}
      onChooseSms={(enabled) => {
        setEntry((current) => (current.language === language ? { language, state: chooseSms(current.state, enabled) } : current));
      }}
      onSave={() => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.saving || controller === null) {
          return;
        }
        setEntry({ language, state: startSaving(state) });
        void submitSettings(state.base, state.choice, state.smsChoice, controller.signal, pageMoves(language, setEntry, replace));
      }}
    />
  );
}

export default function AdminSiteSettingsPage() {
  return (
    <AdminFrame current="settings">
      <AdminSiteSettingsContent />
    </AdminFrame>
  );
}
