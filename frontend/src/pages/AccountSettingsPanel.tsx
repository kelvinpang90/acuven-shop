import { useId } from "react";
import type { FormEvent } from "react";

import { confirmDeletion, setMemberPassword } from "../api/memberAccount";
import type { DeleteConfirmReply, SetPasswordReply } from "../api/memberAccount";
import { clearMemberOrder } from "../api/memberOrders";
import { clearMemberPayOrder } from "../api/memberPay";
import type { MemberSessionInfo, MemberSessionState } from "../api/memberSession";
import { wait } from "../api/pay";
import { fetchSiteSettings } from "../api/siteSettings";
import type { SiteSettings } from "../api/siteSettings";
import SmsVerify from "../components/SmsVerify";
import type { DeleteSmsStep } from "../components/SmsVerify";
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
// 注销部分（SHOP-TASK-167，视觉稿 P13-desktop-settings、P13-phone-settings 的注销部分与 P13-desktop-settings-sms-off 的密码与会话确认；
// DESIGN 1.14「权限与资料保护」第 3、7 条与「边界与原则」第 4 条，HANDOFF 0.63）排在设置段末尾、手机的退出按钮之后：account.delete、
// account.delete_warning 之下按站点设置的开关与会话的 hasPassword 选择确认方式。开关开启时为 account.delete_verify 与注销确认用途的 V1，
// 验证通过后才出现 account.delete_confirm（不带密码确认）；开关关闭、已设密码时为 account.delete_verify_password 与当前密码（确认时带密码），
// 未设密码时为 account.delete_verify_session 与确认框。站点设置读取失败时注销部分只显示 common.error_retry。确认由服务端按当时的开关判定，
// 页面按回答改用别的确认方式（not_verified、password_required 与 V1 的 disabled）。注销成功显示 account.deleted，清除会员订单号的交接与
// 当前订单暂存、重读会话，5 秒后以 replace 前往首页。当前密码与验证码只在输入框与请求体里。

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

// 注销部分用到的站点设置：loading 为读取中；failed 为读取失败（注销部分只显示 common.error_retry）；ready 为读到的开关与人机挑战公开 key。
export type SiteState = { status: "loading" } | { status: "failed" } | { status: "ready"; smsEnabled: boolean; siteKey: string | null };

export const SITE_LOADING: SiteState = { status: "loading" };

export function siteStateOf(settings: SiteSettings | null): SiteState {
  return settings === null ? { status: "failed" } : { status: "ready", smsEnabled: settings.sms_verification_enabled, siteKey: settings.turnstile_site_key };
}

// 读取站点设置；非 2xx、网络中断、中止与开关不合格都为 null。
async function readSiteSettings(signal: AbortSignal): Promise<SiteSettings | null> {
  try {
    return await fetchSiteSettings(signal);
  } catch {
    return null;
  }
}

// 读取短信验证开关并把结果交给 settle：密码部分的开关（读取失败按开启）与注销部分的站点设置；signal 中止之后不再交出结果。
export async function loadSmsSwitch(signal: AbortSignal, settle: (smsEnabled: boolean, site: SiteState) => void): Promise<void> {
  const settings = await readSiteSettings(signal);
  if (signal.aborted) {
    return;
  }
  settle(smsEnabledOf(settings), siteStateOf(settings));
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

// 注销的确认方式：loading 为站点设置读取中（只显示 account.delete 与 account.delete_warning）；failed 为读取失败（只显示 common.error_retry）；
// sms 为开关开启（V1）；password 为开关关闭且已设密码；session 为开关关闭且未设密码（确认框）。
export type DeletionMode = "loading" | "failed" | "sms" | "password" | "session";

export function deletionMode(site: SiteState, hasPassword: boolean): DeletionMode {
  if (site.status !== "ready") {
    return site.status;
  }
  if (site.smsEnabled) {
    return "sms";
  }
  return hasPassword ? "password" : "session";
}

// 注销部分的状态：round 为 V1 的轮次（回到起点时加一，V1 重新挂载）；approved 为 V1 已验证通过；password 为当前密码输入；
// passwordWrong 为密码框下的 account.delete_password_wrong；asking 为确认框打开；confirming 为确认进行中（按钮禁用）；
// notice 为其他提示；deleted 为已注销（换成 account.deleted）。
export interface DeletionState {
  round: number;
  approved: boolean;
  password: string;
  passwordWrong: boolean;
  asking: boolean;
  confirming: boolean;
  notice: CopyKey | null;
  deleted: boolean;
}

export const DELETION_START: DeletionState = { round: 0, approved: false, password: "", passwordWrong: false, asking: false, confirming: false, notice: null, deleted: false };

// 注销成功之后 account.deleted 留在本页的时间（HANDOFF 0.63，Kelvin 2026-10-26 决定）。
export const DELETED_DELAY_MS = 5000;

// 对注销部分的一次改动（结果按到达时的状态更新）。
export type DeletionChange = (current: DeletionState) => DeletionState;

export function typeDeletePassword(password: string): DeletionChange {
  return (current) => ({ ...current, password });
}

// V1 验证通过：出现 account.delete_confirm。
export function deletionApproved(current: DeletionState): DeletionState {
  return { ...current, approved: true, notice: null };
}

// 未设密码时点 account.delete_confirm：打开确认框。
export function askDeletion(current: DeletionState): DeletionState {
  return { ...current, asking: true, notice: null };
}

// account.delete_keep（或 Escape）：关闭确认框。
export function keepAccount(current: DeletionState): DeletionState {
  return { ...current, asking: false };
}

// 确认进行中：按钮禁用，先前的提示收起。
export function startConfirming(current: DeletionState): DeletionState {
  return { ...current, confirming: true, passwordWrong: false, notice: null };
}

// 确认结束（按钮恢复、确认框关闭），notice 为要显示的提示。
export function deletionSettled(notice: CopyKey | null): DeletionChange {
  return (current) => ({ ...current, confirming: false, asking: false, notice });
}

// 密码错误：密码框下显示 account.delete_password_wrong，输入保留。
export function passwordRejected(current: DeletionState): DeletionState {
  return { ...current, confirming: false, passwordWrong: true };
}

// 回到起点（按重读到的开关与 hasPassword 重新开始）：V1 换一轮，批准、密码、确认框与提示都收起。
export function restartDeletion(current: DeletionState): DeletionState {
  return { ...DELETION_START, round: current.round + 1 };
}

// 已注销：注销部分换成 account.deleted。
export function deletionDone(current: DeletionState): DeletionState {
  return { ...current, password: "", confirming: false, asking: false, notice: null, deleted: true };
}

// 能否确认注销（进行中、已注销时不能）：开关开启时须 V1 已通过；已设密码时须填了密码；未设密码时须在确认框里。
export function canConfirmDeletion(mode: DeletionMode, deletion: DeletionState): boolean {
  if (deletion.confirming || deletion.deleted) {
    return false;
  }
  switch (mode) {
    case "sms":
      return deletion.approved;
    case "password":
      return deletion.password !== "";
    case "session":
      return deletion.asking;
    default:
      return false;
  }
}

// 确认时带的密码：只有开关关闭且已设密码时带当前密码，其余为 null（不带密码）。
export function deletionPassword(mode: DeletionMode, deletion: DeletionState): string | null {
  return mode === "password" ? deletion.password : null;
}

// 按确认注销的回答决定：deleted 显示 account.deleted 并离开（deleted）；not_verified 重读站点设置、开关开启则回到 V1 起点（recheck）；
// password_required 视为开关途中被关闭，重读站点设置与会话后改用对应的确认方式（switched）；password_wrong 在密码框下提示（password_wrong）；
// signed_out 重读会话后去 /login（login）；csrf 重读会话换用新令牌（csrf）；rate_limited 为 common.rate_limited；unavailable 为
// common.service_unavailable；failed 为 common.error_retry；network 为 common.network_check。
export function deletionStep(reply: DeleteConfirmReply): "deleted" | "recheck" | "switched" | "password_wrong" | "login" | "csrf" | CopyKey {
  switch (reply) {
    case "deleted":
      return "deleted";
    case "not_verified":
      return "recheck";
    case "password_required":
      return "switched";
    case "password_wrong":
      return "password_wrong";
    case "signed_out":
      return "login";
    case "csrf":
      return "csrf";
    case "rate_limited":
      return "common.rate_limited";
    case "unavailable":
      return "common.service_unavailable";
    case "failed":
      return "common.error_retry";
    case "network":
      return "common.network_check";
  }
}

// 页面对注销结果的处理：改动注销部分、换上重读到的站点设置、重读会话（共享读取，不中止）、换上重读到的会话（hasPassword 与 CSRF 令牌）、
// 以 replace 进入 /login 或首页。
export interface DeletionMoves {
  update: (change: DeletionChange) => void;
  site: (site: SiteState) => void;
  reread: () => Promise<MemberSessionState>;
  session: (info: MemberSessionInfo) => void;
  login: () => void;
  home: () => void;
}

// 注销成功之后的等待（可注入，测试以替身代替）；signal 中止时提前结束。
export type DeletionDelay = (ms: number, signal: AbortSignal) => Promise<void>;

// signed_out：重读会话之后以 replace 进入 /login；signal 已中止时不跳转。
async function leaveForLogin(signal: AbortSignal, moves: DeletionMoves): Promise<void> {
  await moves.reread();
  if (!signal.aborted) {
    moves.login();
  }
}

// csrf：重读会话——member 换用新令牌并显示 common.error_retry；guest 以 replace 进入 /login；failed 保留原令牌并显示 common.error_retry。
async function rereadAfterCsrf(signal: AbortSignal, moves: DeletionMoves): Promise<void> {
  const state = await moves.reread();
  if (signal.aborted) {
    return;
  }
  if (state.kind === "guest") {
    moves.login();
    return;
  }
  if (state.kind === "member") {
    moves.session(state);
  }
  moves.update(deletionSettled("common.error_retry"));
}

// not_verified（开关开启而没有批准）：重读站点设置——开关开启则回到 V1 起点重新验证（含原先为开关关闭的确认方式、途中开关被重新打开），
// 仍关闭则显示 common.error_retry；读取失败时注销部分只显示 common.error_retry。
async function recheckSwitch(signal: AbortSignal, moves: DeletionMoves): Promise<void> {
  const site = siteStateOf(await readSiteSettings(signal));
  if (signal.aborted) {
    return;
  }
  moves.site(site);
  moves.update(site.status === "ready" && site.smsEnabled ? restartDeletion : deletionSettled("common.error_retry"));
}

// 开关在流程途中被关闭（V1 回 disabled、确认回 password_required）：同时重读站点设置与会员会话（hasPassword 可能已在别的标签页改变），
// 按两者回到对应确认方式的起点；会话重读得到 guest 时以 replace 进入 /login，得到 failed 时显示 common.error_retry。
async function switchedOff(signal: AbortSignal, moves: DeletionMoves): Promise<void> {
  const [settings, state] = await Promise.all([readSiteSettings(signal), moves.reread()]);
  if (signal.aborted) {
    return;
  }
  if (state.kind === "guest") {
    moves.login();
    return;
  }
  moves.site(siteStateOf(settings));
  if (state.kind === "member") {
    moves.session(state);
    moves.update(restartDeletion);
  } else {
    moves.update(deletionSettled("common.error_retry"));
  }
}

// 已注销：换成 account.deleted，同时清除会员订单号的交接与当前订单暂存（DESIGN 1.14「权限与资料保护」第 7 条）、重读会话（页头随之回到未登录），
// 等待 5 秒后以 replace 前往首页；signal 中止（离开页面）时取消等待、不再跳转。
async function afterDeleted(signal: AbortSignal, moves: DeletionMoves, delay: DeletionDelay): Promise<void> {
  moves.update(deletionDone);
  clearMemberOrder();
  clearMemberPayOrder();
  const reread = moves.reread();
  await delay(DELETED_DELAY_MS, signal);
  if (!signal.aborted) {
    moves.home();
  }
  await reread;
}

// 确认注销（带会话里的 CSRF 令牌；password 为 null 时不带密码）：先禁用按钮，再按回答处理。signal 中止之后不再更新或跳转。
export async function deleteAccount(password: string | null, csrfToken: string, signal: AbortSignal, moves: DeletionMoves, delay: DeletionDelay = wait): Promise<void> {
  moves.update(startConfirming);
  const reply = await confirmDeletion(password, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = deletionStep(reply);
  switch (step) {
    case "deleted":
      await afterDeleted(signal, moves, delay);
      return;
    case "recheck":
      await recheckSwitch(signal, moves);
      return;
    case "switched":
      await switchedOff(signal, moves);
      return;
    case "password_wrong":
      moves.update(passwordRejected);
      return;
    case "login":
      await leaveForLogin(signal, moves);
      return;
    case "csrf":
      await rereadAfterCsrf(signal, moves);
      return;
    default:
      moves.update(deletionSettled(step));
  }
}

// 注销确认用途的 V1 交来的结果：verified 出现 account.delete_confirm；disabled 视为开关途中被关闭；signed_out 重读会话后去 /login；
// csrf 重读会话换用新令牌。signal 中止之后不再更新或跳转。
export async function takeDeleteSmsStep(step: DeleteSmsStep, signal: AbortSignal, moves: DeletionMoves): Promise<void> {
  if (signal.aborted) {
    return;
  }
  switch (step) {
    case "verified":
      moves.update(deletionApproved);
      return;
    case "disabled":
      await switchedOff(signal, moves);
      return;
    case "signed_out":
      await leaveForLogin(signal, moves);
      return;
    case "csrf":
      await rereadAfterCsrf(signal, moves);
      return;
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
  // 注销部分：会话给的 CSRF 令牌（交给注销确认用途的 V1）、站点设置与注销部分的状态。
  csrfToken: string;
  site: SiteState;
  deletion: DeletionState;
  onDeletePassword: (password: string) => void;
  // 未设密码时的 account.delete_confirm：打开确认框。
  onDeleteAsk: () => void;
  onDeleteKeep: () => void;
  // V1 通过后、已设密码时与确认框里的 account.delete_confirm。
  onDeleteConfirm: () => void;
  // 注销确认用途的 V1 交来的结果。
  onDeleteStep: (step: DeleteSmsStep) => void;
}

type DeletionProps = Pick<
  SettingsPanelProps,
  "hasPassword" | "phoneMasked" | "csrfToken" | "site" | "deletion" | "onDeletePassword" | "onDeleteAsk" | "onDeleteKeep" | "onDeleteConfirm" | "onDeleteStep"
>;

// 提示一行（role="alert"），同密码部分的提交失败。
function DangerNotice({ copy }: { copy: CopyKey }) {
  const t = useCopy();
  return (
    <p className="acs-alert acs-alert--danger site-notice" role="alert">
      {t(copy)}
    </p>
  );
}

// 开关关闭、已设密码（P13-desktop-settings-sms-off）：account.delete_verify_password、当前密码（auth.password）与 account.delete_confirm；
// 密码错误时 account.delete_password_wrong 在密码框下方（HANDOFF 0.63 第（3）项）；密码为空或进行中按钮禁用。
function PasswordConfirm({ deletion, onDeletePassword, onDeleteConfirm }: Pick<DeletionProps, "deletion" | "onDeletePassword" | "onDeleteConfirm">) {
  const t = useCopy();
  const passwordId = useId();
  const errorId = useId();
  return (
    <form
      className="site-account__password"
      noValidate
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onDeleteConfirm();
      }}
    >
      <p className="acs-body-s">{t("account.delete_verify_password")}</p>
      <div className={deletion.passwordWrong ? "acs-field acs-field--invalid site-account__field" : "acs-field site-account__field"}>
        <label className="acs-field__label" htmlFor={passwordId}>
          {t("auth.password")}
        </label>
        <input
          className="acs-input"
          id={passwordId}
          type="password"
          autoComplete="current-password"
          value={deletion.password}
          aria-invalid={deletion.passwordWrong ? true : undefined}
          aria-describedby={deletion.passwordWrong ? errorId : undefined}
          onChange={(event) => {
            onDeletePassword(event.target.value);
          }}
        />
        {deletion.passwordWrong && (
          <span className="acs-field__error" id={errorId}>
            <FieldAlertIcon />
            {t("account.delete_password_wrong")}
          </span>
        )}
      </div>
      <button className="acs-btn acs-btn--danger site-account__confirm" type="submit" disabled={!canConfirmDeletion("password", deletion)} aria-busy={deletion.confirming}>
        {t("account.delete_confirm")}
      </button>
    </form>
  );
}

// 开关关闭、未设密码（P13-desktop-settings-sms-off）：account.delete_verify_session 与 account.delete_confirm；点后出现确认框（alertdialog）——
// account.delete_session_confirm、account.delete_keep（或 Escape，关闭确认框）与 account.delete_confirm（才确认注销）。
function SessionConfirm({ deletion, onDeleteAsk, onDeleteKeep, onDeleteConfirm }: Pick<DeletionProps, "deletion" | "onDeleteAsk" | "onDeleteKeep" | "onDeleteConfirm">) {
  const t = useCopy();
  const labelId = useId();
  return (
    <>
      <p className="acs-body-s">{t("account.delete_verify_session")}</p>
      <button className="acs-btn acs-btn--danger site-account__confirm" type="button" disabled={deletion.asking || deletion.confirming} onClick={onDeleteAsk}>
        {t("account.delete_confirm")}
      </button>
      {deletion.asking && (
        <div
          className="site-account__overlay"
          onKeyDown={(event) => {
            if (event.key === "Escape" && !deletion.confirming) {
              onDeleteKeep();
            }
          }}
        >
          <div className="acs-dialog" role="alertdialog" aria-modal="true" aria-labelledby={labelId}>
            <p id={labelId}>{t("account.delete_session_confirm")}</p>
            <div className="acs-dialog__actions">
              <button className="acs-btn acs-btn--secondary" type="button" autoFocus disabled={deletion.confirming} onClick={onDeleteKeep}>
                {t("account.delete_keep")}
              </button>
              <button className="acs-btn acs-btn--danger" type="button" disabled={!canConfirmDeletion("session", deletion)} aria-busy={deletion.confirming} onClick={onDeleteConfirm}>
                {t("account.delete_confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// 开关开启（P13-desktop-settings、P13-phone-settings）：account.delete_verify 与注销确认用途的 V1（每一轮重新挂载）；验证通过后只出现
// account.delete_confirm（HANDOFF 0.63 第（2）项，不加「验证成功」提示）。
function SmsConfirm({ phoneMasked, csrfToken, siteKey, deletion, onDeleteConfirm, onDeleteStep }: Pick<DeletionProps, "phoneMasked" | "csrfToken" | "deletion" | "onDeleteConfirm" | "onDeleteStep"> & { siteKey: string | null }) {
  const t = useCopy();
  return (
    <>
      <p className="acs-body-s">{t("account.delete_verify")}</p>
      {deletion.approved ? (
        <button className="acs-btn acs-btn--danger site-account__confirm" type="button" disabled={!canConfirmDeletion("sms", deletion)} aria-busy={deletion.confirming} onClick={onDeleteConfirm}>
          {t("account.delete_confirm")}
        </button>
      ) : (
        <SmsVerify key={deletion.round} purpose="delete_account" smsEnabled siteKey={siteKey} phoneMasked={phoneMasked} csrfToken={csrfToken} onDeleteStep={onDeleteStep} />
      )}
    </>
  );
}

// 注销部分：已注销时只有 account.deleted；站点设置读取失败时只有 common.error_retry；否则为 account.delete、account.delete_warning、
// 按确认方式的内容与提示（读取完成前只有前两项，标 aria-busy）。
function DeletionPart({ hasPassword, phoneMasked, csrfToken, site, deletion, onDeletePassword, onDeleteAsk, onDeleteKeep, onDeleteConfirm, onDeleteStep }: DeletionProps) {
  const t = useCopy();
  const mode = deletionMode(site, hasPassword);
  if (deletion.deleted) {
    return (
      <div className="site-account__delete">
        <div className="acs-alert acs-alert--success" role="status">
          <DoneIcon />
          <span>{t("account.deleted")}</span>
        </div>
      </div>
    );
  }
  if (mode === "failed") {
    return (
      <div className="site-account__delete">
        <DangerNotice copy="common.error_retry" />
      </div>
    );
  }
  return (
    <div className="site-account__delete" aria-busy={mode === "loading" ? true : undefined}>
      <h3 className="acs-display-s">{t("account.delete")}</h3>
      <p>{t("account.delete_warning")}</p>
      {mode === "sms" && site.status === "ready" && (
        <SmsConfirm phoneMasked={phoneMasked} csrfToken={csrfToken} siteKey={site.siteKey} deletion={deletion} onDeleteConfirm={onDeleteConfirm} onDeleteStep={onDeleteStep} />
      )}
      {mode === "password" && <PasswordConfirm deletion={deletion} onDeletePassword={onDeletePassword} onDeleteConfirm={onDeleteConfirm} />}
      {mode === "session" && <SessionConfirm deletion={deletion} onDeleteAsk={onDeleteAsk} onDeleteKeep={onDeleteKeep} onDeleteConfirm={onDeleteConfirm} />}
      {deletion.notice !== null && <DangerNotice copy={deletion.notice} />}
    </div>
  );
}

// 未设密码（P13-desktop-settings）：新密码（password 输入）、其下 auth.password_rule（不足 8 个字符时为字段错误）与设置按钮。
function PasswordForm({ settings, inert, onPassword, onSubmit }: Pick<SettingsPanelProps, "settings" | "onPassword" | "onSubmit"> & { inert: boolean }) {
  const t = useCopy();
  const passwordId = useId();
  const ruleId = useId();
  return (
    <form
      className="site-account__password"
      inert={inert ? true : undefined}
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
// 未设或已设密码的内容与提交失败的提示；手机的退出按钮；注销部分（UX P13 手机线框排在退出之后）。已注销时密码部分不再可操作（inert）。
export function SettingsPanel({ settings, hasPassword, phoneMasked, changeTo, logoutDisabled, loggingOut, onPassword, onSubmit, onLogout, ...deletionProps }: SettingsPanelProps) {
  const t = useCopy();
  const deleted = deletionProps.deletion.deleted;
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
            <Link className="site-account__change" to={changeTo} inert={deleted ? true : undefined}>
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
          <PasswordForm settings={settings} inert={deleted} onPassword={onPassword} onSubmit={onSubmit} />
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
      <DeletionPart hasPassword={hasPassword} phoneMasked={phoneMasked} {...deletionProps} />
    </div>
  );
}
