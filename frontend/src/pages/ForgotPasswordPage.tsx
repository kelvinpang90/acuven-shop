import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { submitPasswordReset } from "../api/memberAuth";
import type { ResetSubmitReply } from "../api/memberAuth";
import { ErrorNotice } from "../components/SiteFrame";
import SmsVerify from "../components/SmsVerify";
import type { SmsVerified } from "../components/SmsVerify";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { Link } from "../router";
import type { RoutePath } from "../router";
import { LOADING_SETTINGS, loadSettings, registerPath } from "./LoginPage";
import type { LoginSettings } from "./LoginPage";

// 会员忘记密码 P12 的重设部分（docs/UX.md 0.12 P12，docs/UX-COPY.md 0.13，视觉稿 P12-desktop 的重设部分与 P12-reset-states；
// DESIGN 1.12「权限与资料保护」第 3 条，HANDOFF 0.58）。标题 auth.reset_title 与 V1（用途 reset_password）。打开页面时读一次站点设置
// （照登录页 loadSettings，切换语言不重新读取），读完之前内容区标 aria-busy、不渲染 V1；读取失败只显示 common.error_retry；
// 开关关闭时 V1 自己换成 auth.sms_paused。V1 验证通过后：号码已注册显示新密码（auth.new_password）、auth.password_rule 与
// auth.reset_submit，以 V1 交回的 CSRF 令牌经 api/memberAuth.ts 的 submitPasswordReset 提交；未注册显示 auth.reset_not_registered
// 与去 /register 的 common.nav_register（不创建账号）。新密码与 CSRF 令牌只在页面状态与请求里；离开页面时中止请求。

const LOGIN_PATH: RoutePath = "/login";

// 新密码的长度范围（与服务端相同，按 Unicode 码点计数）；超出时按钮禁用，服务端仍会判定。
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 256;

// 站点设置：与登录页相同（读取中、读取失败、已读到开关的值与人机挑战公开 key）。
export type ResetSettings = LoginSettings;

// V1 验证通过的结果：号码已注册（带提交新密码用的 CSRF 令牌）或未注册。
export type ResetVerified = { kind: "registered"; csrfToken: string } | { kind: "not_registered" };

// 页面状态。round 为 V1 的 key（凭据过期或 CSRF 被拒时加一，V1 重新挂载回到初始状态）；verified 为验证通过的结果（未通过为 null）；
// password 为新密码；submitting 为提交进行中；tooShort 为服务端判定过短（auth.password_rule 显示为字段错误）；notice 为按钮之前的提示；
// retry 为 V1 之上的 common.error_retry；paused 为提交时服务端回答开关已关闭（之后按关闭处理）；done 为已重设。
export interface ResetForm {
  round: number;
  verified: ResetVerified | null;
  password: string;
  submitting: boolean;
  tooShort: boolean;
  notice: CopyKey | null;
  retry: boolean;
  paused: boolean;
  done: boolean;
}

export const INITIAL_RESET: ResetForm = {
  round: 0,
  verified: null,
  password: "",
  submitting: false,
  tooShort: false,
  notice: null,
  retry: false,
  paused: false,
  done: false,
};

// 对页面状态的一次改动（请求的结果按到达时的状态更新）。
export type ResetChange = (current: ResetForm) => ResetForm;

// 页面交给 V1 的开关：读到开启且提交时没有被告知已关闭。
export function smsEnabledFor(settings: ResetSettings, form: ResetForm): boolean {
  return settings.status === "ready" && settings.smsEnabled && !form.paused;
}

// 新密码的长度：按 Unicode 码点计数（与服务端 Python 的 len 相同，一个表情符号算一个），不按 UTF-16 长度。
export function passwordLength(password: string): number {
  return Array.from(password).length;
}

// 号码已注册、长度在范围内、没有进行中的提交、尚未重设且没有暂停时才可提交。
export function canReset(form: ResetForm): boolean {
  const length = passwordLength(form.password);
  return form.verified?.kind === "registered" && !form.submitting && !form.done && !form.paused && length >= PASSWORD_MIN && length <= PASSWORD_MAX;
}

export function editPassword(form: ResetForm, password: string): ResetForm {
  return { ...form, password };
}

// V1 验证通过（V1 的回调）：记下结果与 CSRF 令牌，先前的提示都收起。注册 / 短信登录的结果不会出现在这一用途，忽略。
export function verifiedStep(verified: SmsVerified): ResetChange {
  return (current) => {
    switch (verified.kind) {
      case "registered":
        return { ...current, verified: { kind: "registered", csrfToken: verified.csrfToken }, password: "", tooShort: false, notice: null, retry: false };
      case "not_registered":
        return { ...current, verified: { kind: "not_registered" }, retry: false };
      case "member":
        return current;
    }
  };
}

// 点设置新密码：按钮禁用，先前的提示都收起。
export function startResetting(form: ResetForm): ResetForm {
  return { ...form, submitting: true, tooShort: false, notice: null };
}

// 按提交的结果决定：ok 为已重设（密码输入收起，新密码与令牌清空）；too_short 留在表单，auth.password_rule 显示为字段错误；
// expired 与 csrf 换一个 V1 的 key、回到验证前的初始状态，V1 之上显示 common.error_retry（同一令牌重交必然再被拒）；
// disabled 回到验证前、之后按开关关闭处理（V1 显示 auth.sms_paused，不另显示 common.error_retry）；
// unavailable 为 common.service_unavailable，failed 为 common.error_retry，network 为 common.network_check，都留在表单可再提交。
export function resetStep(reply: ResetSubmitReply): ResetChange {
  return (current) => {
    const settled: ResetForm = { ...current, submitting: false };
    switch (reply) {
      case "ok":
        return { ...settled, verified: null, password: "", done: true };
      case "too_short":
        return { ...settled, tooShort: true };
      case "expired":
      case "csrf":
        return { ...INITIAL_RESET, round: current.round + 1, retry: true, paused: current.paused };
      case "disabled":
        return { ...INITIAL_RESET, round: current.round, paused: true };
      case "unavailable":
        return { ...settled, notice: "common.service_unavailable" };
      case "failed":
        return { ...settled, notice: "common.error_retry" };
      case "network":
        return { ...settled, notice: "common.network_check" };
    }
  };
}

// 提交新密码：请求体只有新密码，CSRF 令牌只在请求头里（api/memberAuth.ts）。signal 中止（离开页面）之后不再更新。
export async function submitReset(password: string, csrfToken: string, signal: AbortSignal, update: (change: ResetChange) => void): Promise<void> {
  const reply = await submitPasswordReset(password, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  update(resetStep(reply));
}

// 字段错误与提示条的图形（同 V1），读屏忽略。
function AlertIcon({ size }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

export interface ResetHandlers {
  onVerified: (verified: SmsVerified) => void;
  onPassword: (password: string) => void;
  onSubmit: () => void;
}

// 号码已注册（P12-desktop 的重设部分）：新密码（password 输入）、其下 auth.password_rule（过短时为字段错误）、提示与整宽的设置按钮。
function PasswordForm({ form, onPassword, onSubmit }: { form: ResetForm } & Pick<ResetHandlers, "onPassword" | "onSubmit">) {
  const t = useCopy();
  const passwordId = useId();
  const ruleId = useId();
  return (
    <form
      className="site-reset__form"
      noValidate
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className={form.tooShort ? "acs-field acs-field--invalid" : "acs-field"}>
        <label className="acs-field__label" htmlFor={passwordId}>
          {t("auth.new_password")}
        </label>
        <input
          className="acs-input"
          id={passwordId}
          type="password"
          autoComplete="new-password"
          value={form.password}
          aria-invalid={form.tooShort ? true : undefined}
          aria-describedby={ruleId}
          onChange={(event) => {
            onPassword(event.target.value);
          }}
        />
        {form.tooShort ? (
          <span className="acs-field__error" id={ruleId}>
            <AlertIcon size={16} />
            {t("auth.password_rule")}
          </span>
        ) : (
          <span className="acs-field__hint" id={ruleId}>
            {t("auth.password_rule")}
          </span>
        )}
      </div>
      {form.notice !== null && (
        <div className="acs-alert acs-alert--danger" role="alert">
          <AlertIcon />
          <span>{t(form.notice)}</span>
        </div>
      )}
      <button className="acs-btn acs-btn--primary acs-btn--block" type="submit" disabled={!canReset(form)} aria-busy={form.submitting}>
        {t("auth.reset_submit")}
      </button>
    </form>
  );
}

// 已重设（P12-reset-states）：auth.reset_done 与去登录页的 common.nav_login（HANDOFF 0.58 第（g）条）。
function ResetDone() {
  const t = useCopy();
  return (
    <>
      <div className="acs-alert acs-alert--success" role="status">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M8 12.5l3 3 5-6" />
        </svg>
        <span>{t("auth.reset_done")}</span>
      </div>
      <Link className="acs-btn acs-btn--secondary site-reset__link" to={LOGIN_PATH}>
        {t("common.nav_login")}
      </Link>
    </>
  );
}

// 号码未注册（P12-reset-states）：auth.reset_not_registered 与去注册页的 common.nav_register（/register 进入路由表之前不渲染）。
function NotRegistered({ registerTo }: { registerTo: RoutePath | null }) {
  const t = useCopy();
  return (
    <>
      <div className="acs-alert acs-alert--info" role="status">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v6M12 7.5v.5" />
        </svg>
        <span>{t("auth.reset_not_registered")}</span>
      </div>
      {registerTo !== null && (
        <Link className="acs-btn acs-btn--secondary site-reset__link" to={registerTo}>
          {t("common.nav_register")}
        </Link>
      )}
    </>
  );
}

export interface ResetViewProps extends ResetHandlers {
  settings: ResetSettings;
  form: ResetForm;
  // common.nav_register 的去向；/register 进入路由表之前为 null。
  registerTo: RoutePath | null;
}

export function ResetView({ settings, form, registerTo, onVerified, onPassword, onSubmit }: ResetViewProps) {
  const t = useCopy();
  const loading = settings.status === "loading";
  const { verified } = form;
  return (
    <main className="site-reset">
      <section className="acs-card site-reset__card">
        <h1 className="acs-display-l">{t("auth.reset_title")}</h1>
        <div className="site-reset__body" aria-busy={loading}>
          {settings.status === "failed" && <ErrorNotice />}
          {settings.status === "ready" && (
            <>
              {form.retry && <ErrorNotice />}
              <SmsVerify key={form.round} purpose="reset_password" smsEnabled={smsEnabledFor(settings, form)} siteKey={settings.siteKey} onVerified={onVerified} />
              {form.done && <ResetDone />}
              {verified?.kind === "registered" && <PasswordForm form={form} onPassword={onPassword} onSubmit={onSubmit} />}
              {verified?.kind === "not_registered" && <NotRegistered registerTo={registerTo} />}
            </>
          )}
        </div>
      </section>
    </main>
  );
}

export default function ForgotPasswordPage() {
  const [settings, setSettings] = useState<ResetSettings>(LOADING_SETTINGS);
  const [form, setForm] = useState<ResetForm>(INITIAL_RESET);
  // 站点设置的读取与重设提交共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  // 打开时读一次站点设置（不随语言重新读取）。
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void loadSettings(controller.signal, (loaded) => {
      setSettings(loaded);
    });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <ResetView
      settings={settings}
      form={form}
      registerTo={registerPath()}
      onVerified={(verified) => {
        setForm(verifiedStep(verified));
      }}
      onPassword={(password) => {
        setForm((current) => editPassword(current, password));
      }}
      onSubmit={() => {
        const controller = lifetime.current;
        const { verified } = form;
        if (!canReset(form) || verified?.kind !== "registered" || controller === null) {
          return;
        }
        setForm(startResetting);
        void submitReset(form.password, verified.csrfToken, controller.signal, (change) => {
          setForm(change);
        });
      }}
    />
  );
}
