import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { passwordLogin } from "../api/memberAuth";
import type { PasswordLoginReply } from "../api/memberAuth";
import { useMemberSessionReader } from "../api/memberSession";
import { fetchSiteSettings } from "../api/siteSettings";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import SmsVerify, { SMS_REGIONS } from "../components/SmsVerify";
import type { SmsPhone, SmsRegion, SmsVerified } from "../components/SmsVerify";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { guestPath, isRoutePath, Link, useRouter, VERIFIED_REDIRECT_MS, verifiedPath } from "../router";
import type { RoutePath } from "../router";

// 会员登录 P12 的登录部分（docs/UX.md 0.12 P12，docs/UX-COPY.md 0.13，视觉稿 P12-desktop 与 P12-phone 的登录部分；
// DESIGN 1.12「权限与资料保护」第 3 条，HANDOFF 0.58）。忘记密码 /forgot-password 与注册 /register 是之后的任务。
// 标题 auth.login_title、★ auth.login_demo_hint 与两个页签（默认密码）。打开页面时读一次站点设置（短信验证开关与人机挑战公开 key，
// 切换语言不重新读取），读完之前页签内容区标 aria-busy、不渲染表单与 V1；读取失败时密码页签照常可用（按开关关闭显示登录失败），
// 短信验证码页签只显示 common.error_retry。两个页签的内容都留在页面上（未选的页签 hidden），切换页签不清空已填的内容与 V1 的进度。
// 密码页签经 api/memberAuth.ts 的 passwordLogin 登录；短信验证码页签嵌入 V1（用途 login）。登录成功显示 auth.verified_*，
// VERIFIED_REDIRECT_MS 之后以路由的 replace 前往来源页（router.tsx）。号码与密码只在页面状态与请求体里；离开页面时中止请求与计时。

// 忘记密码与注册的路径；进了路由表才渲染 auth.forgot 与 common.nav_register。
export function forgotPath(): RoutePath | null {
  const path: string = "/forgot-password";
  return isRoutePath(path) ? path : null;
}

export function registerPath(): RoutePath | null {
  const path: string = "/register";
  return isRoutePath(path) ? path : null;
}

export type LoginTab = "password" | "sms";

export const LOGIN_TABS: readonly LoginTab[] = ["password", "sms"];

const TAB_NAME: Readonly<Record<LoginTab, CopyKey>> = {
  password: "auth.login_method_password",
  sms: "auth.login_method_sms",
};

// 站点设置：读取中、读取失败、已读到（开关的值与人机挑战公开 key）。
export type LoginSettings = { status: "loading" } | { status: "failed" } | { status: "ready"; smsEnabled: boolean; siteKey: string | null };

export const LOADING_SETTINGS: LoginSettings = { status: "loading" };

// 登录失败文案所看的开关：读到开启时为真；读取中与读取失败都按关闭（auth.login_failed_sms_off，不提示改用短信）。
export function smsEnabledOf(settings: LoginSettings): boolean {
  return settings.status === "ready" && settings.smsEnabled;
}

// 读一次站点设置；signal 中止（离开页面）之后不再更新。
export async function loadSettings(signal: AbortSignal, show: (settings: LoginSettings) => void): Promise<void> {
  let settings: LoginSettings;
  try {
    const read = await fetchSiteSettings(signal);
    settings = { status: "ready", smsEnabled: read.sms_verification_enabled, siteKey: read.turnstile_site_key };
  } catch {
    settings = { status: "failed" };
  }
  if (!signal.aborted) {
    show(settings);
  }
}

// 密码页签：region 与 input 为号码（国家码只有 +60、+65，默认 +60），password 为密码；submitting 为提交进行中；
// phoneInvalid 为号码下的 checkout.phone_invalid；notice 为登录按钮之前的提示；verified 为已登录（显示 auth.verified_login）。
export interface PasswordForm {
  region: SmsRegion;
  input: string;
  password: string;
  submitting: boolean;
  phoneInvalid: boolean;
  notice: CopyKey | null;
  verified: boolean;
}

export const INITIAL_PASSWORD: PasswordForm = {
  region: "MY",
  input: "",
  password: "",
  submitting: false,
  phoneInvalid: false,
  notice: null,
  verified: false,
};

// 对密码页签的一次改动（请求的结果按到达时的状态更新）。
export type PasswordChange = (current: PasswordForm) => PasswordForm;

// 提交进行中与已登录时不再提交（按钮禁用）。
export function canSubmit(form: PasswordForm): boolean {
  return !form.submitting && !form.verified;
}

// 点登录：按钮禁用，先前的提示都收起。
export function startSubmitting(form: PasswordForm): PasswordForm {
  return { ...form, submitting: true, phoneInvalid: false, notice: null };
}

// 按密码登录的结果决定（号码保留、密码一律清空）：ok 为已登录；login_failed 在开关开启时为 auth.login_failed、关闭时为
// auth.login_failed_sms_off（未设密码、密码错误与连续失败锁定同一回答）；phone_invalid 在号码下显示 checkout.phone_invalid；
// unavailable 为 common.service_unavailable；failed 为 common.error_retry；network 为 common.network_check。
export function passwordStep(reply: PasswordLoginReply, smsEnabled: boolean): PasswordChange {
  return (current) => {
    const settled: PasswordForm = { ...current, submitting: false, password: "" };
    switch (reply) {
      case "ok":
        return { ...settled, verified: true };
      case "login_failed":
        return { ...settled, notice: smsEnabled ? "auth.login_failed" : "auth.login_failed_sms_off" };
      case "phone_invalid":
        return { ...settled, phoneInvalid: true };
      case "unavailable":
        return { ...settled, notice: "common.service_unavailable" };
      case "failed":
        return { ...settled, notice: "common.error_retry" };
      case "network":
        return { ...settled, notice: "common.network_check" };
    }
  };
}

// 短信验证通过的结果文字（视觉稿 V1-verified）：新建了会员为 auth.verified_registered，否则为 auth.verified_login。
export function smsVerifiedCopy(verified: SmsVerified): CopyKey {
  return verified.kind === "member" && verified.created ? "auth.verified_registered" : "auth.verified_login";
}

// 计时：ms 之后完成；signal 中止时取消计时（之后不再完成）。测试传入替身，不真的等待。
export type Wait = (ms: number, signal: AbortSignal) => Promise<void>;

export const timerWait: Wait = (ms, signal) =>
  new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
      },
      { once: true },
    );
  });

// 页面对结果的处理：改动密码页签、显示短信验证通过的文字、用路由的 replace 前往成功后的去向，
// 或在登录成功后要求重读会员会话（页面传入共享读取的 reload，不等待结果；页头随之切换为 common.nav_account）。
export interface LoginMoves {
  update: (change: PasswordChange) => void;
  smsVerified: (copy: CopyKey) => void;
  replace: (path: RoutePath) => void;
  reloadSession: () => void;
}

// 成功文案显示 VERIFIED_REDIRECT_MS 之后以 replace 前往来源页（没有来源时首页）；signal 中止（离开页面）之后不再跳转。
export async function redirectLater(source: RoutePath | null, signal: AbortSignal, wait: Wait, replace: (path: RoutePath) => void): Promise<void> {
  await wait(VERIFIED_REDIRECT_MS, signal);
  if (signal.aborted) {
    return;
  }
  replace(verifiedPath(source));
}

// 密码登录：请求体只有号码与密码（api/memberAuth.ts）。ok 时先要求重读一次会话（服务端已发出会话，离开页面也照样重读），
// 提交失败时不重读。signal 中止之后不再更新；ok 时显示 auth.verified_login 后计时跳转。
export async function submitPassword(
  phone: SmsPhone,
  password: string,
  smsEnabled: boolean,
  source: RoutePath | null,
  signal: AbortSignal,
  wait: Wait,
  moves: LoginMoves,
): Promise<void> {
  const reply = await passwordLogin(phone, password, signal);
  if (reply === "ok") {
    moves.reloadSession();
  }
  if (signal.aborted) {
    return;
  }
  moves.update(passwordStep(reply, smsEnabled));
  if (reply === "ok") {
    await redirectLater(source, signal, wait, moves.replace);
  }
}

// 短信验证通过（V1 的回调）：先要求重读一次会话（同上，离开页面也照样重读），再显示 auth.verified_* 后计时跳转。
export async function finishSms(verified: SmsVerified, source: RoutePath | null, signal: AbortSignal, wait: Wait, moves: LoginMoves): Promise<void> {
  moves.reloadSession();
  if (signal.aborted) {
    return;
  }
  moves.smsVerified(smsVerifiedCopy(verified));
  await redirectLater(source, signal, wait, moves.replace);
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

// 登录成功的提示（视觉稿 V1-verified）。
function VerifiedNotice({ copy }: { copy: CopyKey }) {
  const t = useCopy();
  return (
    <div className="acs-alert acs-alert--success" role="status">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12.5l3 3 5-6" />
      </svg>
      <span>{t(copy)}</span>
    </div>
  );
}

export interface LoginHandlers {
  onTab: (tab: LoginTab) => void;
  onRegion: (region: SmsRegion) => void;
  onPhone: (input: string) => void;
  onPassword: (password: string) => void;
  onSubmit: () => void;
  onSmsVerified: (verified: SmsVerified) => void;
}

// 密码页签（P12-desktop、P12-phone）：号码（国家码下拉以 auth.phone 的标签作读屏名称，同 V1）、密码、提示、登录按钮与 auth.forgot；
// 已登录时整块换成 auth.verified_login。
function PasswordPanel({
  form,
  forgotTo,
  onRegion,
  onPhone,
  onPassword,
  onSubmit,
}: { form: PasswordForm; forgotTo: RoutePath | null } & Pick<LoginHandlers, "onRegion" | "onPhone" | "onPassword" | "onSubmit">) {
  const t = useCopy();
  const labelId = useId();
  const phoneId = useId();
  const phoneErrorId = useId();
  const passwordId = useId();
  if (form.verified) {
    return <VerifiedNotice copy="auth.verified_login" />;
  }
  return (
    <form
      className="site-login__form"
      noValidate
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className={form.phoneInvalid ? "acs-field acs-field--invalid" : "acs-field"}>
        <label className="acs-field__label" id={labelId} htmlFor={phoneId}>
          {t("auth.phone")}
        </label>
        <div className="acs-phone site-login__phone">
          <select
            className="acs-select"
            aria-labelledby={labelId}
            value={form.region}
            onChange={(event) => {
              onRegion(event.target.value === "SG" ? "SG" : "MY");
            }}
          >
            {SMS_REGIONS.map((option) => (
              <option key={option.region} value={option.region}>
                {`+${option.callingCode}`}
              </option>
            ))}
          </select>
          <input
            className="acs-input"
            id={phoneId}
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            value={form.input}
            aria-invalid={form.phoneInvalid ? true : undefined}
            aria-describedby={form.phoneInvalid ? phoneErrorId : undefined}
            onChange={(event) => {
              onPhone(event.target.value);
            }}
          />
        </div>
        {form.phoneInvalid && (
          <span className="acs-field__error" id={phoneErrorId}>
            <AlertIcon size={16} />
            {t("checkout.phone_invalid")}
          </span>
        )}
      </div>
      <div className="acs-field">
        <label className="acs-field__label" htmlFor={passwordId}>
          {t("auth.password")}
        </label>
        <input
          className="acs-input"
          id={passwordId}
          type="password"
          autoComplete="current-password"
          value={form.password}
          onChange={(event) => {
            onPassword(event.target.value);
          }}
        />
      </div>
      {form.notice !== null && (
        <div className="acs-alert acs-alert--danger" role="alert">
          <AlertIcon />
          <span>{t(form.notice)}</span>
        </div>
      )}
      <button className="acs-btn acs-btn--primary acs-btn--block" type="submit" disabled={!canSubmit(form)} aria-busy={form.submitting}>
        {t("auth.login_submit")}
      </button>
      {forgotTo !== null && (
        <Link className="acs-btn acs-btn--quiet site-login__forgot" to={forgotTo}>
          {t("auth.forgot")}
        </Link>
      )}
    </form>
  );
}

// 短信验证码页签：auth.same_flow_note 与 V1（用途 login；开关关闭时 V1 自己换成 auth.sms_paused），验证通过后 auth.verified_*，
// 其下 common.nav_register。
function SmsPanel({
  smsEnabled,
  siteKey,
  smsDone,
  guestTo,
  registerTo,
  onSmsVerified,
}: { smsEnabled: boolean; siteKey: string | null; smsDone: CopyKey | null; guestTo: RoutePath; registerTo: RoutePath | null } & Pick<LoginHandlers, "onSmsVerified">) {
  const t = useCopy();
  return (
    <>
      <p className="acs-body-s site-login__text">{t("auth.same_flow_note")}</p>
      <SmsVerify purpose="login" smsEnabled={smsEnabled} siteKey={siteKey} guestTo={guestTo} onVerified={onSmsVerified} />
      {smsDone !== null && <VerifiedNotice copy={smsDone} />}
      {registerTo !== null && (
        <Link className="site-login__register" to={registerTo}>
          {t("common.nav_register")}
        </Link>
      )}
    </>
  );
}

export interface LoginViewProps extends LoginHandlers {
  settings: LoginSettings;
  tab: LoginTab;
  form: PasswordForm;
  // 短信验证通过后的文字；未通过为 null。
  smsDone: CopyKey | null;
  // auth.continue_guest 的去向（router.tsx 的 guestPath）。
  guestTo: RoutePath;
  forgotTo: RoutePath | null;
  registerTo: RoutePath | null;
}

export function LoginView({ settings, tab, form, smsDone, guestTo, forgotTo, registerTo, onTab, onRegion, onPhone, onPassword, onSubmit, onSmsVerified }: LoginViewProps) {
  const t = useCopy();
  const tabIds: Readonly<Record<LoginTab, string>> = { password: useId(), sms: useId() };
  const panelIds: Readonly<Record<LoginTab, string>> = { password: useId(), sms: useId() };
  const loading = settings.status === "loading";
  return (
    <main className="site-login">
      <section className="acs-card site-login__card">
        <h1 className="acs-display-l">{t("auth.login_title")}</h1>
        <DemoHint>{t("auth.login_demo_hint")}</DemoHint>
        <div className="acs-tabs" role="tablist">
          {LOGIN_TABS.map((option) => (
            <button
              key={option}
              className="acs-tab"
              id={tabIds[option]}
              role="tab"
              type="button"
              aria-selected={option === tab}
              aria-controls={loading ? undefined : panelIds[option]}
              onClick={() => {
                onTab(option);
              }}
            >
              {t(TAB_NAME[option])}
            </button>
          ))}
        </div>
        <div className="site-login__panels" aria-busy={loading}>
          {!loading && (
            <>
              <div className="site-login__panel" id={panelIds.password} role="tabpanel" aria-labelledby={tabIds.password} hidden={tab !== "password"}>
                <PasswordPanel form={form} forgotTo={forgotTo} onRegion={onRegion} onPhone={onPhone} onPassword={onPassword} onSubmit={onSubmit} />
              </div>
              <div className="site-login__panel" id={panelIds.sms} role="tabpanel" aria-labelledby={tabIds.sms} hidden={tab !== "sms"}>
                {settings.status === "ready" ? (
                  <SmsPanel
                    smsEnabled={settings.smsEnabled}
                    siteKey={settings.siteKey}
                    smsDone={smsDone}
                    guestTo={guestTo}
                    registerTo={registerTo}
                    onSmsVerified={onSmsVerified}
                  />
                ) : (
                  <ErrorNotice />
                )}
              </div>
            </>
          )}
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  const { replace, source } = useRouter();
  const sessionReader = useMemberSessionReader();
  // 打开页面时读一次来源页（在本页期间不会改变）。
  const [origin] = useState<RoutePath | null>(() => source());
  const [settings, setSettings] = useState<LoginSettings>(LOADING_SETTINGS);
  const [tab, setTab] = useState<LoginTab>("password");
  const [form, setForm] = useState<PasswordForm>(INITIAL_PASSWORD);
  const [smsDone, setSmsDone] = useState<CopyKey | null>(null);
  // 站点设置的读取、密码登录与成功后的计时共用的中止；离开页面时中止。
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

  const moves: LoginMoves = {
    update: (change) => {
      setForm(change);
    },
    smsVerified: (copy) => {
      setSmsDone(copy);
    },
    replace: (path) => {
      replace(path);
    },
    reloadSession: () => {
      void sessionReader.reload();
    },
  };

  return (
    <LoginView
      settings={settings}
      tab={tab}
      form={form}
      smsDone={smsDone}
      guestTo={guestPath(origin)}
      forgotTo={forgotPath()}
      registerTo={registerPath()}
      onTab={setTab}
      onRegion={(region) => {
        setForm((current) => ({ ...current, region }));
      }}
      onPhone={(input) => {
        setForm((current) => ({ ...current, input }));
      }}
      onPassword={(password) => {
        setForm((current) => ({ ...current, password }));
      }}
      onSubmit={() => {
        const controller = lifetime.current;
        if (settings.status === "loading" || !canSubmit(form) || controller === null) {
          return;
        }
        setForm(startSubmitting);
        void submitPassword({ input: form.input, region: form.region }, form.password, smsEnabledOf(settings), origin, controller.signal, timerWait, moves);
      }}
      onSmsVerified={(verified) => {
        const controller = lifetime.current;
        if (controller !== null) {
          void finishSms(verified, origin, controller.signal, timerWait, moves);
        }
      }}
    />
  );
}
