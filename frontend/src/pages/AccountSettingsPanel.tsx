import { useId } from "react";
import type { FormEvent } from "react";

import { setMemberPassword } from "../api/memberAccount";
import type { SetPasswordReply } from "../api/memberAccount";
import type { MemberSessionInfo, MemberSessionState } from "../api/memberSession";
import { fetchSiteSettings } from "../api/siteSettings";
import type { SiteSettings } from "../api/siteSettings";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { isRoutePath, Link } from "../router";
import type { RoutePath } from "../router";
import { PASSWORD_MIN, passwordLength } from "./ForgotPasswordPage";

// 会员中心 P13 的设置段（docs/UX.md 0.13 P13，docs/UX-COPY.md 0.14，视觉稿 P13-desktop-settings、P13-phone-settings 与
// P13-desktop-settings-sms-off 的密码与号码、退出部分；DESIGN 1.14「权限与资料保护」第 3、5 条，HANDOFF 0.63 第（7）条）。
// account.password_title 之下：未设密码时为 account.password_none、新密码输入、auth.password_rule 与 account.password_set_submit，
// 短信验证开关关闭时另有 account.sms_paused_set_password；已设密码时为 account.password_exists 与去 P12 忘记密码流程的 account.password_change。
// 首次设置经 api/memberAccount.ts 的 setMemberPassword 以会话里的 CSRF 令牌提交（提交前按 auth.password_rule 检查不少于 8 个字符）；
// 成功先显示 account.password_set_done 再重读会话。站点设置读取完成前与读取失败时都按开关开启处理。手机在段首另显示遮掩后的手机号，
// 密码之后为 common.nav_logout（桌面这两项在标题行）。会话、段表与退出由 AccountPage.tsx 接上。
// 新密码只在输入框、页面状态与请求体里，不进地址、日志或浏览器存储。

// 更改已有密码走 P12 忘记密码流程；进了路由表才渲染 account.password_change。
export function passwordChangePath(): RoutePath | null {
  const path: string = "/forgot-password";
  return isRoutePath(path) ? path : null;
}

// 设置段的状态：smsEnabled 为短信验证开关（读完之前与读取失败时为开启）；password 为新密码输入；submitting 为提交进行中（按钮禁用）；
// tooShort 为 auth.password_rule 显示为字段错误；notice 为提交失败的提示；done 为已设置（显示 account.password_set_done）。
export interface SettingsState {
  smsEnabled: boolean;
  password: string;
  submitting: boolean;
  tooShort: boolean;
  notice: CopyKey | null;
  done: boolean;
}

export const SETTINGS_START: SettingsState = { smsEnabled: true, password: "", submitting: false, tooShort: false, notice: null, done: false };

// 对设置段的一次改动（结果按到达时的状态更新）。
export type SettingsChange = (current: SettingsState) => SettingsState;

// 站点设置的读取结果：读到时取开关的值，读取失败（null）按开启处理。
export function smsEnabledOf(settings: SiteSettings | null): boolean {
  return settings?.sms_verification_enabled ?? true;
}

export function withSmsSwitch(smsEnabled: boolean): SettingsChange {
  return (current) => ({ ...current, smsEnabled });
}

export function typePassword(password: string): SettingsChange {
  return (current) => ({ ...current, password });
}

// 提交前的检查：按码点计数不少于 8 个字符（auth.password_rule）。
export function passwordLongEnough(password: string): boolean {
  return passwordLength(password) >= PASSWORD_MIN;
}

// 进行中不再提交（按钮禁用）。
export function canSubmitPassword(settings: SettingsState): boolean {
  return !settings.submitting;
}

// 长度不足：不发请求，auth.password_rule 显示为字段错误。
export function rejectShort(current: SettingsState): SettingsState {
  return { ...current, tooShort: true, notice: null, done: false };
}

// 点设置密码：按钮禁用，先前的提示都收起。
export function startSetting(current: SettingsState): SettingsState {
  return { ...current, submitting: true, tooShort: false, notice: null, done: false };
}

// 已设置：显示 account.password_set_done，清空输入。
export function passwordSet(current: SettingsState): SettingsState {
  return { ...current, password: "", submitting: false, done: true };
}

// 提交结束（按钮恢复），notice 为要显示的提示。
export function settled(notice: CopyKey | null): SettingsChange {
  return (current) => ({ ...current, submitting: false, notice });
}

// 已设过密码（转为已设密码的内容）：按钮恢复，输入清空。
export function alreadySet(current: SettingsState): SettingsState {
  return { ...current, password: "", submitting: false, notice: null };
}

// 按提交的回答决定：done 显示 account.password_set_done 并重读会话（done）；already_set 重读会话转为已设密码的内容（reread）；
// signed_out 重读会话后去 /login（login）；csrf 重读会话换用新令牌（csrf）；too_short 以 auth.password_rule 作字段错误（too_short）；
// unavailable 为 common.service_unavailable；failed 为 common.error_retry；network 为 common.network_check。
export function passwordStep(reply: SetPasswordReply): "done" | "reread" | "login" | "csrf" | "too_short" | CopyKey {
  switch (reply) {
    case "done":
      return "done";
    case "already_set":
      return "reread";
    case "signed_out":
      return "login";
    case "csrf":
      return "csrf";
    case "too_short":
      return "too_short";
    case "unavailable":
      return "common.service_unavailable";
    case "failed":
      return "common.error_retry";
    case "network":
      return "common.network_check";
  }
}

// 读取短信验证开关并把结果交给 settle（读取失败按开启）；signal 中止之后不再交出结果。
export async function loadSmsSwitch(signal: AbortSignal, settle: (smsEnabled: boolean) => void): Promise<void> {
  let settings: SiteSettings | null;
  try {
    settings = await fetchSiteSettings(signal);
  } catch {
    settings = null;
  }
  if (signal.aborted) {
    return;
  }
  settle(smsEnabledOf(settings));
}

// 页面对设置结果的处理：改动设置段、重读会话（共享读取，不中止）、换上重读到的会话（hasPassword 与 CSRF 令牌）、以 replace 进入 /login。
export interface SettingsMoves {
  update: (change: SettingsChange) => void;
  reread: () => Promise<MemberSessionState>;
  session: (info: MemberSessionInfo) => void;
  login: () => void;
}

// 重读会话之后：member 换上会话（再按 onMember 改动设置段）；guest 以 replace 进入 /login；failed 保留当前内容并显示 common.error_retry。
// signal 已中止（离开页面）时不再更新或跳转。
async function afterReread(signal: AbortSignal, moves: SettingsMoves, onMember: SettingsChange | null): Promise<void> {
  const state = await moves.reread();
  if (signal.aborted) {
    return;
  }
  if (state.kind === "member") {
    moves.session(state);
    if (onMember !== null) {
      moves.update(onMember);
    }
  } else if (state.kind === "guest") {
    moves.login();
  } else {
    moves.update(settled("common.error_retry"));
  }
}

// 首次设置密码（带会话里的 CSRF 令牌）：长度不足时不发请求；signal 中止之后不再更新或跳转。
export async function submitPassword(password: string, csrfToken: string, signal: AbortSignal, moves: SettingsMoves): Promise<void> {
  if (!passwordLongEnough(password)) {
    moves.update(rejectShort);
    return;
  }
  moves.update(startSetting);
  const reply = await setMemberPassword(password, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = passwordStep(reply);
  switch (step) {
    case "done":
      moves.update(passwordSet);
      await afterReread(signal, moves, null);
      return;
    case "reread":
      await afterReread(signal, moves, alreadySet);
      return;
    case "login":
      await moves.reread();
      if (!signal.aborted) {
        moves.login();
      }
      return;
    case "csrf":
      await afterReread(signal, moves, settled("common.error_retry"));
      return;
    case "too_short":
      moves.update((current) => ({ ...current, submitting: false, tooShort: true }));
      return;
    default:
      moves.update(settled(step));
  }
}

// 字段错误的图形（同 CheckoutMember.tsx），读屏忽略。
function FieldAlertIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

// 成功提示的图形（取自视觉稿 P13-desktop-settings），读屏忽略。
function DoneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l3 3 5-6" />
    </svg>
  );
}

// 说明提示的图形（取自视觉稿 P13-desktop-settings-sms-off），读屏忽略。
function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.5" />
    </svg>
  );
}

export interface SettingsPanelProps {
  settings: SettingsState;
  hasPassword: boolean;
  phoneMasked: string;
  // account.password_change 的去向（passwordChangePath）；为 null 时不渲染。
  changeTo: RoutePath | null;
  // 手机的退出按钮：与标题行的退出共用禁用条件与处理。
  logoutDisabled: boolean;
  loggingOut: boolean;
  onPassword: (password: string) => void;
  onSubmit: () => void;
  onLogout: () => void;
}

// 未设密码（P13-desktop-settings）：新密码（password 输入）、其下 auth.password_rule（不足 8 个字符时为字段错误）与设置按钮。
function PasswordForm({ settings, onPassword, onSubmit }: Pick<SettingsPanelProps, "settings" | "onPassword" | "onSubmit">) {
  const t = useCopy();
  const passwordId = useId();
  const ruleId = useId();
  return (
    <form
      className="site-account__password"
      noValidate
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className={settings.tooShort ? "acs-field acs-field--invalid site-account__field" : "acs-field site-account__field"}>
        <label className="acs-field__label" htmlFor={passwordId}>
          {t("auth.new_password")}
        </label>
        <input
          className="acs-input"
          id={passwordId}
          type="password"
          autoComplete="new-password"
          value={settings.password}
          aria-invalid={settings.tooShort ? true : undefined}
          aria-describedby={ruleId}
          onChange={(event) => {
            onPassword(event.target.value);
          }}
        />
        {settings.tooShort ? (
          <span className="acs-field__error" id={ruleId}>
            <FieldAlertIcon />
            {t("auth.password_rule")}
          </span>
        ) : (
          <span className="acs-field__hint" id={ruleId}>
            {t("auth.password_rule")}
          </span>
        )}
      </div>
      <button className="acs-btn acs-btn--primary site-account__submit" type="submit" disabled={!canSubmitPassword(settings)} aria-busy={settings.submitting}>
        {t("account.password_set_submit")}
      </button>
    </form>
  );
}

// 设置段：段标题（手机由标签代替）；手机的遮掩号码；account.password_title 之下 account.password_set_done（已设置时，留在内容上方）、
// 未设或已设密码的内容与提交失败的提示；手机的退出按钮。
export function SettingsPanel({ settings, hasPassword, phoneMasked, changeTo, logoutDisabled, loggingOut, onPassword, onSubmit, onLogout }: SettingsPanelProps) {
  const t = useCopy();
  return (
    <div className="site-account__settings">
      <h2 className="acs-display-m site-desktop-only">{t("account.settings")}</h2>
      <span className="acs-num acs-muted site-phone-only">{phoneMasked}</span>
      <h3 className="acs-display-s">{t("account.password_title")}</h3>
      {settings.done && (
        <div className="acs-alert acs-alert--success" role="status">
          <DoneIcon />
          <span>{t("account.password_set_done")}</span>
        </div>
      )}
      {hasPassword ? (
        <>
          <p>{t("account.password_exists")}</p>
          {changeTo !== null && (
            <Link className="site-account__change" to={changeTo}>
              {t("account.password_change")}
            </Link>
          )}
        </>
      ) : (
        <>
          <p>{t("account.password_none")}</p>
          {!settings.smsEnabled && (
            <div className="acs-alert acs-alert--info site-account__paused" role="status">
              <InfoIcon />
              <span>{t("account.sms_paused_set_password")}</span>
            </div>
          )}
          <PasswordForm settings={settings} onPassword={onPassword} onSubmit={onSubmit} />
        </>
      )}
      {settings.notice !== null && (
        <p className="acs-alert acs-alert--danger site-notice" role="alert">
          {t(settings.notice)}
        </p>
      )}
      <button className="acs-btn acs-btn--secondary acs-btn--block site-phone-only site-account__signout" type="button" disabled={logoutDisabled} aria-busy={loggingOut} onClick={onLogout}>
        {t("common.nav_logout")}
      </button>
    </div>
  );
}
