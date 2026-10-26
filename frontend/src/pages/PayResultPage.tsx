import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { handOffMemberOrder } from "../api/memberOrders";
import { confirmMemberStripe, readMemberPayOrder } from "../api/memberPay";
import type { MemberPayRead, MemberStripeConfirm } from "../api/memberPay";
import { useMemberSessionReader } from "../api/memberSession";
import type { MemberSessionReader } from "../api/memberSession";
import { AWAITING_PAYMENT, confirmStripe, readPayOrder, resultKind, takePaidNotCancelled, wait } from "../api/pay";
import type { PayOrder, PayRead, StripeConfirm } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { isPlainLeftClick, isRoutePath, Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import {
  ExpiresLine,
  InfoIcon,
  MEMBER_PAY_STASH,
  MemberReopen,
  memberReadStep,
  openPayOrder,
  OrderCard,
  PAY_PATH,
  RecipientDetails,
  SessionExpired,
  settleMemberTurn,
  useCountdown,
  useMyStates,
} from "./PayPage";
import type { MemberTarget, MemberTurn, PayStash } from "./PayPage";

// 模拟支付结果页 P07（docs/UX.md P07，游客）：打开时以当前语言重新调用 GET /api/pay/orders，取第一张订单，按它的状态显示：
// 已模拟支付 → 成功（订单号与复制、应付金额、收货资料、result.guest_next、★ result.demo_hint、result.continue）；
// 最近一次支付失败且仍待支付 → 失败（pay.expires 倒计时与回 P06 的 result.retry），倒计时到 0 时重新读取；
// 已取消 → 按取消方 result.cancelled（超时）或 result.cancelled_by_you，以及 result.continue。
// 仍待支付而最近一次不是失败时换成支付页 P06。401 时整页 pay.session_expired。
// 成功页的 result.guest_next 之后是去注册页 /register 的 result.guest_register（UX P07 线框，视觉稿 P07-phone-guest）；
// 游客没有 result.track，也不显示获得积分（会员模式见下）。
// Stripe 途径（docs/UX.md 0.11「P07 Stripe 测试」，DESIGN 1.12 第 4、5、6 条，HANDOFF 0.54、0.55，视觉稿 P07-stripe-confirming、
// P07-stripe-phone-slow 与 P07-stripe-states）：仍待支付且有 Stripe 记录时调用确认接口一次（带读取给的 CSRF 令牌），确认返回前按读取中显示；
// paid、refunded 重新读取订单并按读到的状态显示；not_paid 显示 result.stripe_not_paid 与回 P06 的 result.stripe_back_to_pay；
// 其余（confirming、failed、csrf、network）为确认中：每 5 秒自动重查、最多 12 次，用完后显示 result.confirming_slow 与 result.check_again。
// 每次重查都先重新读取订单（取得新令牌）再确认。仍待支付而没有 Stripe 记录、支付途径为 stripe 时一律换成 P06（Stripe 途径没有失败页）。
// 已取消且有来源为会话的 Stripe 退款尝试时以 result.stripe_late_refund 代替两种取消文案；从 P06 取消时会话已付款进入的，
// 成功区顶部另显示 result.paid_not_cancelled（只在内存里，读一次即失效）。
// 会员模式（UX 0.13 P07，DESIGN 1.14「权限与资料保护」第 5、7 条，HANDOFF 0.63，SHOP-TASK-162）：打开时照 P06 的判定顺序 ①②（PayPage.tsx 的 openPayOrder）；
// 会员订单经会员接口确认 Stripe 付款（令牌取自会员会话）与重读，确认中、尚未付款与游客相同；会员接口回 signed_out、not_found 与 csrf 照 P06 的 settleMemberTurn。
// 会员读到的订单结果确定（成功、已取消）时清除暂存，之后切换语言等仍以页面内存里的订单号经会员接口重读；失败、确认中与尚未付款不清除。
// 会员成功页以 result.points_earned（0 也显示）与 result.track（先交出订单号，再去订单详情会员模式 /account/order）代替 result.guest_next
// 与 result.guest_register；会员确认中不显示 result.guest_next（只给游客）。

// 确认中每次自动重查之前的等待与自动重查的次数（UX 0.11「每 5 秒自动重查一次、最多 12 次」）。
export const AUTO_RECHECK_MS = 5000;
export const AUTO_RECHECKS = 12;

function CheckIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M7 7l10 10M17 7L7 17" />
    </svg>
  );
}

function PendingIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3a9 9 0 1 1-9 9" />
    </svg>
  );
}

function WaitIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

// 取自视觉稿 P07-desktop-member 获得积分一行的星形。
function StarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3L2.9 9.5l6.3-.9z" />
    </svg>
  );
}

// 订单详情会员模式 P09 的路径；进了路由表才渲染会员成功页的 result.track。
export function memberOrderPath(): RoutePath | null {
  const path: string = "/account/order";
  return isRoutePath(path) ? path : null;
}

function DoneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.5 2.5L16 9.5" />
    </svg>
  );
}

// 页面的状态：读取中（含确认返回之前）、凭据过期、会员找不到这笔订单（pay.member_reopen）、读取失败、按订单显示（成功、失败、已取消）、
// Stripe 确认中（slow 为自动重查已用完，checking 为手动再查进行中）与 Stripe 尚未付款。
export type ResultScreen =
  | { status: "loading" }
  | { status: "expired" }
  | { status: "reopen" }
  | { status: "error" }
  | { status: "ready"; order: PayOrder }
  | { status: "confirming"; order: PayOrder; slow: boolean; checking: boolean }
  | { status: "not_paid" };

export const LOADING: ResultScreen = { status: "loading" };
export const REOPEN: ResultScreen = { status: "reopen" };

export function confirming(order: PayOrder, slow: boolean): ResultScreen {
  return { status: "confirming", order, slow, checking: false };
}

// 读取结果的去向：show 换成某个状态；pay 用路由的 replace 换成 P06；confirm 调用确认接口；failed 为读取失败或网络中断。
export type ReadStep =
  | { kind: "show"; screen: ResultScreen }
  | { kind: "pay" }
  | { kind: "confirm"; order: PayOrder; csrfToken: string }
  | { kind: "failed" };

// 仍待支付且 stripe_state 不为 null 时确认；为 null 时支付途径为 stripe 一律回 P06，simulated 照 resultKind（失败页或回 P06）；
// 其他状态按订单显示；401 整页 pay.session_expired。
export function readStep(read: PayRead): ReadStep {
  if (read.kind === "expired") {
    return { kind: "show", screen: { status: "expired" } };
  }
  if (read.kind !== "ok") {
    return { kind: "failed" };
  }
  const { order } = read;
  if (order.status === AWAITING_PAYMENT) {
    if (order.stripe_state !== null) {
      return { kind: "confirm", order, csrfToken: read.csrfToken };
    }
    if (order.payment_route === "stripe" || resultKind(order) === "pay") {
      return { kind: "pay" };
    }
  }
  return { kind: "show", screen: { status: "ready", order } };
}

// 确认结果的去向：paid、refunded 重新读取订单；not_paid 尚未付款；expired 整页 pay.session_expired；
// confirming、failed、csrf 与 network 都为确认中。
export type ConfirmStep = { kind: "show"; screen: ResultScreen } | { kind: "reread" } | { kind: "confirming" };

export function confirmStep(confirm: StripeConfirm): ConfirmStep {
  switch (confirm.kind) {
    case "paid":
    case "refunded":
      return { kind: "reread" };
    case "not_paid":
      return { kind: "show", screen: { status: "not_paid" } };
    case "expired":
      return { kind: "show", screen: { status: "expired" } };
    case "confirming":
    case "failed":
    case "csrf":
    case "network":
      return { kind: "confirming" };
  }
}

// 打开或切换语言时读取失败：已按订单显示或尚未付款的保留；确认中的保留并出现 result.check_again（不再有自动重查）；其余 common.error_retry。
export function keepOnFailure(current: ResultScreen): ResultScreen {
  switch (current.status) {
    case "ready":
    case "not_paid":
      return current;
    case "confirming":
      return { ...current, slow: true, checking: false };
    default:
      return { status: "error" };
  }
}

// 点 result.check_again：自动重查用完的确认中才可以，按钮禁用并标 aria-busy。
export function startChecking(current: ResultScreen): ResultScreen {
  return current.status === "confirming" && current.slow && !current.checking ? { ...current, checking: true } : current;
}

// 打开时判定出的访问方式：游客凭订单访问 cookie；会员凭会员会话、页面内存里的订单号与会话给的 CSRF 令牌（判定顺序 ①）。
export type ResultAccess = { kind: "guest" } | ({ kind: "member" } & MemberTarget);

// 页面对结果的处理：换成某个状态、按当前状态改动、用路由的 replace 换成 P06，
// 首次读到订单时的 takePaidNotCancelled 结果（notice），以及判定出的访问方式（access；null 为按 ② 重新判定之前）。
export interface ResultMoves {
  show: (screen: ResultScreen) => void;
  update: (change: (current: ResultScreen) => ResultScreen) => void;
  replace: (path: RoutePath) => void;
  notice: (shown: boolean) => void;
  access: (access: ResultAccess | null) => void;
}

// 每次自动重查之前的等待，默认 api/pay.ts 的 wait（中止时提前结束）。
export type Delay = (ms: number, signal?: AbortSignal) => Promise<void>;

export interface ResultOptions {
  delay?: Delay;
  // 读到订单时以其订单号调用一次 takePaidNotCancelled（页面只在首次读到订单之前为 true）。
  takeNotice?: boolean;
  // 会员当前订单号的暂存，默认 api/memberPay.ts 的（PayPage.tsx 的 MEMBER_PAY_STASH）。
  stash?: PayStash;
}

// 确认中之后的一步：读取失败时沿用 order 仍为确认中；仍待支付且有 Stripe 记录时为读到的订单（确认中）；
// 其余照读到的结果显示或回 P06，返回 null。
function follow(step: ReadStep, order: PayOrder, moves: ResultMoves): PayOrder | null {
  switch (step.kind) {
    case "failed":
      return order;
    case "confirm":
      return step.order;
    case "pay":
      moves.replace(PAY_PATH);
      return null;
    case "show":
      moves.show(step.screen);
      return null;
  }
}

// 调用确认接口一次并按结果显示；仍为确认中时返回当时的订单，已按结果显示或已中止时返回 null。
async function confirmOnce(order: PayOrder, csrfToken: string, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<PayOrder | null> {
  const confirm = await confirmStripe(order.order_number, csrfToken, signal);
  if (signal.aborted) {
    return null;
  }
  const step = confirmStep(confirm);
  if (step.kind === "show") {
    moves.show(step.screen);
    return null;
  }
  if (step.kind === "confirming") {
    return order;
  }
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return null;
  }
  return follow(readStep(read), order, moves);
}

// 一次重查：重新读取订单（取得新令牌），仍待支付且有 Stripe 记录时再确认一次；读取失败与确认失败一样仍为确认中。
async function recheck(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<PayOrder | null> {
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return null;
  }
  const step = readStep(read);
  if (step.kind === "confirm") {
    return confirmOnce(step.order, step.csrfToken, language, signal, moves);
  }
  return follow(step, order, moves);
}

// 自动重查的循环（游客与会员相同）：once 为一次重查，返回仍为确认中的订单，已按结果显示或已中止时返回 null。
async function recheckLoop(order: PayOrder, signal: AbortSignal, moves: ResultMoves, delay: Delay, once: (current: PayOrder) => Promise<PayOrder | null>): Promise<void> {
  let current = order;
  moves.show(confirming(current, false));
  for (let count = 1; count <= AUTO_RECHECKS; count += 1) {
    await delay(AUTO_RECHECK_MS, signal);
    if (signal.aborted) {
      return;
    }
    const next = await once(current);
    if (next === null) {
      return;
    }
    current = next;
    moves.show(confirming(current, count === AUTO_RECHECKS));
  }
}

// 确认中的自动重查：显示确认中，每次等待 AUTO_RECHECK_MS 后重查，最多 AUTO_RECHECKS 次；用完仍为确认中时显示 result.check_again。
// 自动重查期间不切换为读取中；signal 中止（离开页面或切换语言）之后不再更新。
export async function recheckAutomatically(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves, delay: Delay = wait): Promise<void> {
  await recheckLoop(order, signal, moves, delay, (current) => recheck(current, language, signal, moves));
}

// 确认（读到仍待支付且有 Stripe 记录的订单之后）：确认返回前按读取中显示，确认中时进入自动重查。
export async function confirmResult(
  order: PayOrder,
  csrfToken: string,
  language: Language,
  signal: AbortSignal,
  moves: ResultMoves,
  delay: Delay = wait,
): Promise<void> {
  moves.show(LOADING);
  const next = await confirmOnce(order, csrfToken, language, signal, moves);
  if (next !== null) {
    await recheckAutomatically(next, language, signal, moves, delay);
  }
}

// 游客读到的结果：读取失败时照现有写法保留已显示的结果（keepOnFailure）。
async function showGuestRead(read: PayRead, language: Language, signal: AbortSignal, moves: ResultMoves, delay: Delay, takeNotice: boolean): Promise<void> {
  if (read.kind === "ok" && takeNotice) {
    moves.notice(takePaidNotCancelled(read.order.order_number));
  }
  const step = readStep(read);
  switch (step.kind) {
    case "failed":
      moves.update(keepOnFailure);
      return;
    case "pay":
      moves.replace(PAY_PATH);
      return;
    case "show":
      moves.show(step.screen);
      return;
    case "confirm":
      await confirmResult(step.order, step.csrfToken, language, signal, moves, delay);
  }
}

// 游客的重新读取（判定为游客之后：失败页倒计时到 0 与切换语言）；401 照现有写法整页 pay.session_expired（HANDOFF 0.63 第（1）项）。
export async function readGuestResult(language: Language, signal: AbortSignal, moves: ResultMoves, { delay = wait, takeNotice = false }: ResultOptions = {}): Promise<void> {
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return;
  }
  await showGuestRead(read, language, signal, moves, delay, takeNotice);
}

// 打开（以及还没判定出访问方式时的重新读取）：照 P06 的判定顺序 ①②（PayPage.tsx 的 openPayOrder，UX 0.13 P07）——
// guest 照游客显示；member 按会员订单显示（结果确定时清除暂存）；reopen 整页 pay.member_reopen；expired 整页 pay.session_expired；
// error 照现有写法 common.error_retry（keepOnFailure）。会话的重读是共享读取，不中止；signal 中止之后不再更新。
export async function openResult(
  language: Language,
  reader: MemberSessionReader,
  signal: AbortSignal,
  moves: ResultMoves,
  { delay = wait, takeNotice = false, stash = MEMBER_PAY_STASH }: ResultOptions = {},
): Promise<void> {
  const opening = await openPayOrder(language, reader, signal, stash);
  if (opening === null || signal.aborted) {
    return;
  }
  switch (opening.kind) {
    case "guest":
      moves.access({ kind: "guest" });
      await showGuestRead(opening.read, language, signal, moves, delay, takeNotice);
      return;
    case "member": {
      const target: MemberTarget = { orderNumber: opening.orderNumber, csrfToken: opening.csrfToken };
      moves.access({ kind: "member", ...target });
      if (takeNotice) {
        moves.notice(takePaidNotCancelled(opening.order.order_number));
      }
      await showMemberRead({ kind: "ok", order: opening.order }, { target, language, reader, signal, moves, delay, stash });
      return;
    }
    case "reopen":
      moves.show(REOPEN);
      return;
    case "expired":
      moves.show({ status: "expired" });
      return;
    case "error":
      moves.update(keepOnFailure);
      return;
  }
}

// 手动再查（result.check_again，页面已按 startChecking 标为进行中）：重查一次，仍为确认中时按钮恢复可用。
export async function checkAgain(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<void> {
  const next = await recheck(order, language, signal, moves);
  if (next !== null) {
    moves.show(confirming(next, true));
  }
}

// —— 会员模式 ——

// 会员读取的去向：与游客相同的（按订单显示、回 P06、确认、读取失败）照 readStep，令牌取自会员会话；其余为 MemberTurn。
export type MemberResultStep = ReadStep | MemberTurn;

export function memberResultStep(read: MemberPayRead, csrfToken: string): MemberResultStep {
  const step = memberReadStep(read);
  if (step.kind !== "same") {
    return step;
  }
  return step.value === null ? { kind: "failed" } : readStep({ kind: "ok", order: step.value, csrfToken });
}

// 会员确认的去向：paid、refunded、not_paid、confirming、failed 与 network 与游客相同（confirmStep）；
// signed_out、not_found 与 csrf 为 MemberTurn（csrf 不再按确认中处理，验收「会员的 Stripe 确认」）。
export type MemberConfirmStep = ConfirmStep | MemberTurn;

export function memberConfirmStep(confirm: MemberStripeConfirm): MemberConfirmStep {
  switch (confirm.kind) {
    case "signed_out":
    case "not_found":
      return { kind: confirm.kind };
    case "csrf":
      return { kind: "csrf", order: null };
    default:
      return confirmStep(confirm);
  }
}

// 结果确定（成功、已取消，含超时取消与迟到付款按已取消处理）时清除暂存（DESIGN 1.14「权限与资料保护」第 7 条）；模拟支付失败与仍待支付不清除。
export function settlesStash(order: Pick<PayOrder, "status" | "last_payment" | "cancelled_by">): boolean {
  const kind = resultKind(order);
  return kind === "success" || kind === "cancelled" || kind === "cancelled_by_you";
}

// 会员一次读取与确认共用的上下文：暂存的订单号与令牌、语言、会话读取、页面的中止、页面的处理、自动重查的等待与暂存。
interface MemberFlow {
  target: MemberTarget;
  language: Language;
  reader: MemberSessionReader;
  signal: AbortSignal;
  moves: ResultMoves;
  delay: Delay;
  stash: PayStash;
}

// 会员按某个状态显示：按订单显示且结果确定时先清除暂存（本次照常显示）；确认中、尚未付款与失败页不清除。
function showMember(screen: ResultScreen, flow: MemberFlow): void {
  if (screen.status === "ready" && settlesStash(screen.order)) {
    flow.stash.clear();
  }
  flow.moves.show(screen);
}

// MemberTurn 照 P06 的 settleMemberTurn：signed_out 清除暂存并按 ② 重新判定；not_found 清除暂存并整页 pay.member_reopen；
// csrf 重读会话——member 换用新令牌，failed 保留原令牌，两者都显示 common.error_retry；guest 清除暂存并按 ② 重新判定。
// 按 ② 重新判定时回到读取中，再走一次打开时的判定（暂存已清除，所以照游客读取）。
async function settleTurn(turn: MemberTurn, flow: MemberFlow): Promise<void> {
  const next = { redetermine: false };
  await settleMemberTurn(
    turn,
    flow.reader,
    flow.signal,
    {
      reopen: () => {
        flow.moves.show(REOPEN);
      },
      redetermine: () => {
        next.redetermine = true;
      },
      retry: (csrfToken) => {
        if (csrfToken !== null) {
          flow.moves.access({ kind: "member", orderNumber: flow.target.orderNumber, csrfToken });
        }
        flow.moves.show({ status: "error" });
      },
    },
    flow.stash,
  );
  if (next.redetermine && !flow.signal.aborted) {
    flow.moves.access(null);
    flow.moves.show(LOADING);
    await openResult(flow.language, flow.reader, flow.signal, flow.moves, { delay: flow.delay, stash: flow.stash });
  }
}

// 会员确认中之后的一步：同游客的 follow，另按 MemberTurn 处理；按订单显示时结果确定则清除暂存。
async function followMember(step: MemberResultStep, order: PayOrder, flow: MemberFlow): Promise<PayOrder | null> {
  switch (step.kind) {
    case "failed":
      return order;
    case "confirm":
      return step.order;
    case "pay":
      flow.moves.replace(PAY_PATH);
      return null;
    case "show":
      showMember(step.screen, flow);
      return null;
    case "signed_out":
    case "not_found":
    case "csrf":
      await settleTurn(step, flow);
      return null;
  }
}

// 以会话里的令牌调用会员确认接口一次（订单号为暂存的那一个），paid、refunded 后以会员接口重读；仍为确认中时返回当时的订单。
async function confirmOnceAsMember(order: PayOrder, flow: MemberFlow): Promise<PayOrder | null> {
  const confirm = await confirmMemberStripe(flow.target.orderNumber, flow.target.csrfToken, flow.signal);
  if (flow.signal.aborted) {
    return null;
  }
  const step = memberConfirmStep(confirm);
  switch (step.kind) {
    case "show":
      flow.moves.show(step.screen);
      return null;
    case "confirming":
      return order;
    case "reread":
      break;
    case "signed_out":
    case "not_found":
    case "csrf":
      await settleTurn(step, flow);
      return null;
  }
  const read = await readMemberPayOrder(flow.language, flow.target.orderNumber, flow.signal);
  if (flow.signal.aborted) {
    return null;
  }
  return followMember(memberResultStep(read, flow.target.csrfToken), order, flow);
}

// 会员的一次重查：以会员接口重新读取，仍待支付且有 Stripe 记录时再确认一次；读取失败与确认失败一样仍为确认中。
async function recheckAsMember(order: PayOrder, flow: MemberFlow): Promise<PayOrder | null> {
  const read = await readMemberPayOrder(flow.language, flow.target.orderNumber, flow.signal);
  if (flow.signal.aborted) {
    return null;
  }
  const step = memberResultStep(read, flow.target.csrfToken);
  if (step.kind === "confirm") {
    return confirmOnceAsMember(step.order, flow);
  }
  return followMember(step, order, flow);
}

// 会员读到的结果：读取失败照游客保留已显示的结果；确认时确认返回前按读取中显示，确认中时进入自动重查（规则与游客相同）。
async function showMemberRead(read: MemberPayRead, flow: MemberFlow): Promise<void> {
  const step = memberResultStep(read, flow.target.csrfToken);
  switch (step.kind) {
    case "failed":
      flow.moves.update(keepOnFailure);
      return;
    case "pay":
      flow.moves.replace(PAY_PATH);
      return;
    case "show":
      showMember(step.screen, flow);
      return;
    case "confirm": {
      flow.moves.show(LOADING);
      const next = await confirmOnceAsMember(step.order, flow);
      if (next !== null) {
        await recheckLoop(next, flow.signal, flow.moves, flow.delay, (current) => recheckAsMember(current, flow));
      }
      return;
    }
    case "signed_out":
    case "not_found":
    case "csrf":
      await settleTurn(step, flow);
      return;
  }
}

// 会员的重新读取（判定为会员之后：失败页倒计时到 0 与切换语言）：以页面内存里的订单号经会员接口读取同一张订单，
// 暂存已在结果确定时清除也一样（不转 ②）。signal 中止之后不再更新。
export async function readMemberResult(
  target: MemberTarget,
  language: Language,
  reader: MemberSessionReader,
  signal: AbortSignal,
  moves: ResultMoves,
  { delay = wait, stash = MEMBER_PAY_STASH }: ResultOptions = {},
): Promise<void> {
  const read = await readMemberPayOrder(language, target.orderNumber, signal);
  if (signal.aborted) {
    return;
  }
  await showMemberRead(read, { target, language, reader, signal, moves, delay, stash });
}

// 会员的手动再查（同游客的 checkAgain，经会员接口）。
export async function checkAgainAsMember(
  order: PayOrder,
  target: MemberTarget,
  language: Language,
  reader: MemberSessionReader,
  signal: AbortSignal,
  moves: ResultMoves,
  { delay = wait, stash = MEMBER_PAY_STASH }: ResultOptions = {},
): Promise<void> {
  const next = await recheckAsMember(order, { target, language, reader, signal, moves, delay, stash });
  if (next !== null) {
    moves.show(confirming(next, true));
  }
}

// 点 result.track：先经 handOffMemberOrder 交出订单号，再前往订单详情会员模式（HANDOFF 0.61 第（4）项的交接）。
export function openTrack(orderNumber: string, path: RoutePath, navigate: (path: RoutePath) => void, handOff: (orderNumber: string) => void = handOffMemberOrder): void {
  handOff(orderNumber);
  navigate(path);
}

export interface PayResultViewProps {
  screen: ResultScreen;
  minutes: number;
  states: ReadonlyMap<string, string> | null;
  // 从 P06 取消时会话已付款进入（成功区顶部的 result.paid_not_cancelled）。
  paidNotCancelled: boolean;
  onCheckAgain: () => void;
  // 会员模式（UX 0.13 P07）：不给时为游客。
  member?: boolean;
  // 会员成功页 result.track 的去向（memberOrderPath）；为 null 时不渲染。
  track?: RoutePath | null;
  // 点 result.track（普通左键点击）：页面先交出订单号再前往 track（openTrack）。
  onTrack?: () => void;
}

export function PayResultView({ screen, minutes, states, paidNotCancelled, onCheckAgain, member = false, track = null, onTrack }: PayResultViewProps) {
  const t = useCopy();

  if (screen.status === "loading") {
    return <main className="site-pay" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }
  if (screen.status === "reopen") {
    return <MemberReopen />;
  }
  if (screen.status === "error") {
    return (
      <main className="site-pay">
        <ErrorNotice />
      </main>
    );
  }

  if (screen.status === "not_paid") {
    return (
      <main className="site-pay site-result">
        <div className="site-result__stripe">
          <div className="acs-alert acs-alert--info" role="status">
            <InfoIcon />
            <span>{t("result.stripe_not_paid")}</span>
          </div>
          <Link className="acs-btn acs-btn--primary" to={PAY_PATH}>
            {t("result.stripe_back_to_pay")}
          </Link>
        </div>
      </main>
    );
  }

  const guestNext = (
    <div className="acs-alert acs-alert--info">
      <InfoIcon />
      <span>{t("result.guest_next")}</span>
    </div>
  );

  if (screen.status === "confirming") {
    // 确认中不显示 result.demo_hint 与 pay.expires（UX 0.11）；result.guest_next 只给游客（线框「游客：[G] [result.guest_next]」）。
    return (
      <main className="site-pay site-result">
        <span className="site-result__pending">
          <PendingIcon />
        </span>
        <div className="site-result__confirming" role="status">
          <h1 className="acs-display-l">{t("result.confirming_title")}</h1>
          <p className="acs-body-l">{t("result.confirming_body")}</p>
        </div>
        <OrderCard order={screen.order} />
        {!member && guestNext}
        {screen.slow && (
          <>
            <div className="acs-alert acs-alert--info" role="status">
              <WaitIcon />
              <span>{t("result.confirming_slow")}</span>
            </div>
            <button className="acs-btn acs-btn--primary site-result__check" type="button" disabled={screen.checking} aria-busy={screen.checking} onClick={onCheckAgain}>
              {t("result.check_again")}
            </button>
          </>
        )}
      </main>
    );
  }

  const { order } = screen;
  const kind = resultKind(order);
  const continueLink = (
    <Link className="acs-btn acs-btn--secondary site-result__continue" to={PRODUCTS_PATH}>
      {t("result.continue")}
    </Link>
  );

  if (kind === "failure") {
    return (
      <main className="site-pay site-result">
        <div className="site-result__head">
          <span className="acs-tag acs-tag--danger site-result__icon">
            <CrossIcon />
          </span>
          <h1 className="acs-display-l">{t("result.failure_title")}</h1>
          <p className="acs-body-l">{t("result.failure_body")}</p>
        </div>
        <ExpiresLine minutes={minutes} />
        <Link className="acs-btn acs-btn--primary site-result__retry" to={PAY_PATH}>
          {t("result.retry")}
        </Link>
      </main>
    );
  }

  if (kind === "cancelled" || kind === "cancelled_by_you") {
    // 有来源为会话的 Stripe 退款尝试时不论取消方都以 result.stripe_late_refund 代替（UX 0.11）。
    const text = order.stripe_refunded ? "result.stripe_late_refund" : kind === "cancelled" ? "result.cancelled" : "result.cancelled_by_you";
    return (
      <main className="site-pay site-result">
        <div className="acs-alert acs-alert--info" role="status">
          <InfoIcon />
          <span>{t(text)}</span>
        </div>
        {continueLink}
      </main>
    );
  }

  if (kind === "pay") {
    // 页面正在换成支付页。
    return <main className="site-pay" aria-busy="true"></main>;
  }

  return (
    <main className="site-pay site-result">
      {paidNotCancelled && (
        <div className="acs-alert acs-alert--success" role="status">
          <DoneIcon />
          <span>{t("result.paid_not_cancelled")}</span>
        </div>
      )}
      <div className="site-result__head">
        <span className="acs-tag acs-tag--success site-result__icon">
          <CheckIcon />
        </span>
        <h1 className="acs-display-l">{t("result.success_title")}</h1>
        <p className="acs-body-l">{t("result.success_body")}</p>
      </div>
      <OrderCard order={order} />
      <RecipientDetails recipient={order.recipient} states={states} />
      {member ? (
        // 会员（UX P07 线框「会员：[result.points_earned] [M2] ( [result.track] ) → P09 会员模式」，视觉稿 P07-desktop-member）：
        // 获得积分原样显示，0 也显示（HANDOFF 0.63 第（5）项）。
        <>
          <div className="acs-alert acs-alert--success">
            <StarIcon />
            <span>{t("result.points_earned", { points: order.points_earned })}</span>
          </div>
          {track !== null && (
            <Link
              className="acs-btn acs-btn--primary site-result__track"
              to={track}
              onClick={(event) => {
                if (isPlainLeftClick(event)) {
                  event.preventDefault();
                  onTrack?.();
                }
              }}
            >
              {t("result.track")}
            </Link>
          )}
        </>
      ) : (
        <>
          {guestNext}
          <Link className="site-result__register" to="/register">
            {t("result.guest_register")}
          </Link>
        </>
      )}
      <DemoHint>{t("result.demo_hint")}</DemoHint>
      {continueLink}
    </main>
  );
}

function pageMoves(
  setScreen: Dispatch<SetStateAction<ResultScreen>>,
  replace: (path: RoutePath) => void,
  notice: (shown: boolean) => void,
  access: (access: ResultAccess | null) => void,
): ResultMoves {
  return {
    show: (screen) => {
      setScreen(screen);
    },
    update: (change) => {
      setScreen(change);
    },
    replace: (path) => {
      replace(path);
    },
    notice,
    access,
  };
}

export default function PayResultPage() {
  const { language } = useLanguage();
  const { navigate, replace } = useRouter();
  const reader = useMemberSessionReader();
  const [screen, setScreen] = useState<ResultScreen>(LOADING);
  // 打开时判定出的访问方式；null 为还没判定（打开时与按 ② 重新判定时）。读取的 effect 取 ref，判定完成本身不再触发一次读取。
  const [access, setAccess] = useState<ResultAccess | null>(null);
  const accessRef = useRef<ResultAccess | null>(null);
  // 失败页的倒计时到 0 或切换语言时重新读取订单。
  const [reads, setReads] = useState(0);
  const [paidNotCancelled, setPaidNotCancelled] = useState(false);
  // 首次读到订单之后不再调用 takePaidNotCancelled（它读一次即失效）。
  const noticeTaken = useRef(false);
  // 当前读取与其后的确认、重查共用的中止；离开页面、切换语言或重新读取时中止，自动重查次数随新的读取从头计。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  const failure = order !== null && resultKind(order) === "failure" ? order : null;
  const minutes = useCountdown(failure, () => {
    setReads((count) => count + 1);
  });
  const states = useMyStates(order?.recipient ?? null);

  const enterAccess = (next: ResultAccess | null) => {
    accessRef.current = next;
    setAccess(next);
  };

  // 还没判定（打开或按 ② 重新判定）时按判定顺序打开；之后失败页倒计时到 0、切换语言等重新读取同一订单：会员经会员接口，游客照现有写法。
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const moves = pageMoves(
      setScreen,
      replace,
      (shown) => {
        noticeTaken.current = true;
        setPaidNotCancelled(shown);
      },
      (next) => {
        accessRef.current = next;
        setAccess(next);
      },
    );
    const current = accessRef.current;
    const options = { takeNotice: !noticeTaken.current };
    if (current === null) {
      void openResult(language, reader, controller.signal, moves, options);
    } else if (current.kind === "member") {
      void readMemberResult(current, language, reader, controller.signal, moves);
    } else {
      void readGuestResult(language, controller.signal, moves, options);
    }
    return () => {
      controller.abort();
    };
  }, [language, reads, replace, reader]);

  const member = access?.kind === "member" ? access : null;
  const track = memberOrderPath();

  return (
    <PayResultView
      screen={screen}
      minutes={minutes}
      states={states}
      paidNotCancelled={paidNotCancelled}
      member={member !== null}
      track={track}
      onTrack={() => {
        if (order !== null && track !== null) {
          openTrack(order.order_number, track, navigate);
        }
      }}
      onCheckAgain={() => {
        const controller = lifetime.current;
        const checking = startChecking(screen);
        if (checking === screen || checking.status !== "confirming" || controller === null) {
          return;
        }
        setScreen(checking);
        const moves = pageMoves(setScreen, replace, setPaidNotCancelled, enterAccess);
        if (member !== null) {
          void checkAgainAsMember(checking.order, member, language, reader, controller.signal, moves);
          return;
        }
        void checkAgain(checking.order, language, controller.signal, moves);
      }}
    />
  );
}
