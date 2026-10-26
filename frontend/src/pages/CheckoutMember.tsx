import { useId } from "react";

import type { Outcome } from "../api/checkout";
import { readMemberPhone, runMemberOrder } from "../api/memberCheckout";
import type { MemberOrderBody, MemberOrderOptions, MemberOrderReply, MemberQuote, MemberQuoteRead } from "../api/memberCheckout";
import { stashMemberPayOrder } from "../api/memberPay";
import type { MemberSessionReader, MemberSessionState } from "../api/memberSession";
import { clearCart } from "../cart";
import type { CartStorage } from "../cart";
import { ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import { useCopy } from "../i18n/language";
import type { RoutePath } from "../router";
import type { CheckoutForm } from "./CheckoutPage";
import { PAY_PATH } from "./PayPage";

// 结账页 P05 第 3 步的会员表单（docs/UX.md 0.13 P05「已登录会员」一行与第 3 步线框，DESIGN 1.14「权限与资料保护」第 1、2、7、8 条，HANDOFF 0.63）。
// 打开时经会员会话的读取对象重读一次会话：member 跳过第 1、2 步以会员身份进入第 3 步，guest 与 failed 照游客路径。
// 会员第 3 步：可编辑的收货电话（进入时取本人完整号码，只在电话栏为空时填入）、优惠券（点 checkout.coupon_apply 才带券重新计价）、
// 积分（只收 0 到本单 points_max 的整数），摘要另有券与积分两行；下单带最近一次计价接受的券与积分，成功后写入当前订单暂存再进入 /pay。
// 本人号码、收货电话、券代码与 CSRF 令牌只在页面状态里，不写入任何浏览器存储、地址、查询参数或历史状态；只有订单号写入暂存（api/memberPay.ts）。
// 第 3 步顶部的提示区与游客路径由 CheckoutPage.tsx 渲染；本文件只有会员的状态、纯函数、请求流程与收货电话、券、积分和摘要两行。

// 打开时的身份：member 带会话里的 CSRF 令牌；guest 为未登录，读取失败也按游客（HANDOFF 0.63 第（3）项，不另加提示）。
export type CheckoutIdentity = { kind: "member"; csrfToken: string } | { kind: "guest" };

export function identityOf(session: MemberSessionState): CheckoutIdentity {
  return session.kind === "member" ? { kind: "member", csrfToken: session.csrfToken } : { kind: "guest" };
}

// 打开页面时重读一次会话（不直接用页头之前读到的状态）；会话读取是共享读取，不中止，signal 中止（离开页面）之后返回 null。
export async function openCheckout(reader: MemberSessionReader, signal: AbortSignal): Promise<CheckoutIdentity | null> {
  const session = await reader.reload();
  return signal.aborted ? null : identityOf(session);
}

// 会员第 3 步的状态：phone 为收货电话栏；coupon 为券输入框，appliedCoupon 为最近一次点 checkout.coupon_apply 时去掉首尾空白的代码（为空即不用券）；
// points 为积分栏（只含数字，空串按 0）；pointsMax 为最近一次返回的计价的 points_max（首次计价返回前为 null，积分栏不可输入）；
// round 在点 checkout.coupon_apply 与下单回 coupon_invalid、points_unavailable 时加一，重新计价；
// orderNotice 为下单回答后的提示（checkout.coupon_invalid 或 common.error_retry），下一次计价返回时清除。
export interface MemberForm {
  csrfToken: string;
  phone: string;
  phoneInvalid: boolean;
  coupon: string;
  appliedCoupon: string | null;
  points: string;
  pointsMax: number | null;
  round: number;
  orderNotice: "coupon_invalid" | "points_unavailable" | null;
}

export function memberForm(csrfToken: string): MemberForm {
  return { csrfToken, phone: "", phoneInvalid: false, coupon: "", appliedCoupon: null, points: "", pointsMax: null, round: 0, orderNotice: null };
}

// 以会员身份进入第 3 步：跳过第 1、2 步，没有游客提示；已填的收货资料保留。
export function enterAsMember(form: CheckoutForm): CheckoutForm {
  return { ...form, step: 3, phoneError: null, guestNotice: null, notice: null };
}

// 取到本人号码：只在电话栏仍为空时填入，不覆盖会员已输入的内容。
export function fillPhone(member: MemberForm, phone: string): MemberForm {
  return member.phone.trim() === "" ? { ...member, phone } : member;
}

export function editPhone(member: MemberForm, phone: string): MemberForm {
  return { ...member, phone, phoneInvalid: false };
}

// 券代码：去掉首尾空白，为空即不用券。
export function couponCode(input: string): string | null {
  const code = input.trim();
  return code === "" ? null : code;
}

// 点 checkout.coupon_apply：以输入框当前的代码重新计价（同一代码再点一次也重新计价）。输入框改动本身不重新计价。
export function applyCoupon(member: MemberForm): MemberForm {
  return { ...member, appliedCoupon: couponCode(member.coupon), round: member.round + 1 };
}

export function pointsCount(field: string): number {
  return field === "" ? 0 : Number(field);
}

// 积分栏的输入：去掉非数字字符，超过 max 时改成 max（Kelvin 2026-10-26 决定：只收 0 到本单上限的整数，不加新文案）。
export function pointsField(value: string, max: number): string {
  const digits = value.replace(/\D/g, "");
  if (digits === "") {
    return "";
  }
  return String(Math.min(Number(digits), max));
}

// 改积分栏；首次计价返回前不可输入，不接受改动。
export function changePoints(member: MemberForm, value: string): MemberForm {
  return member.pointsMax === null ? member : { ...member, points: pointsField(value, member.pointsMax) };
}

// 会员计价的标识：游客计价的标识（语言、SKU 与件数、目的地、409 order_not_placeable 后的轮次）另加所用券代码、积分数与会员的轮次。
export function memberQuoteKey(baseKey: string | null, member: MemberForm): string | null {
  if (baseKey === null) {
    return null;
  }
  return [baseKey, JSON.stringify(member.appliedCoupon), String(pointsCount(member.points)), String(member.round)].join("|");
}

// 已返回的会员计价：coupon 与 points 为该次请求所用的券代码与积分数（下单时据此取舍），last 为最近一次成功的结果。
export interface SettledMemberQuote {
  key: string;
  coupon: string | null;
  points: number;
  outcome: Outcome<MemberQuote>;
  last: MemberQuote | null;
}

// 计价请求的结果：ok 为采用的计价；signed_out 为会话失效（摘要按失败显示，页面另行重读会话）；failed 与 network 为失败。
export function memberQuoteOutcome(run: Outcome<MemberQuoteRead>): { outcome: Outcome<MemberQuote>; signedOut: boolean } {
  if (run.status === "ready" && run.quote.kind === "ok") {
    return { outcome: { status: "ready", quote: run.quote.quote }, signedOut: false };
  }
  return { outcome: { status: "error" }, signedOut: run.status === "ready" && run.quote.kind === "signed_out" };
}

export function settleMemberQuote(previous: SettledMemberQuote | null, key: string, coupon: string | null, points: number, outcome: Outcome<MemberQuote>): SettledMemberQuote {
  return { key, coupon, points, outcome, last: outcome.status === "ready" ? outcome.quote : (previous?.last ?? null) };
}

// 页面看到的会员计价（同 CheckoutPage.tsx 的 SummaryQuote，ready 另带该次请求所用的券代码与积分数）。
export type MemberSummary =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; quote: MemberQuote; pending: boolean; coupon: string | null; points: number };

// 只认最后一次请求的结果：属于当前标识时直接采用，否则显示上一次成功的结果并标为等待中。
export function memberSummary(key: string | null, settled: SettledMemberQuote | null): MemberSummary {
  if (settled !== null && settled.key === key) {
    return settled.outcome.status === "ready" ? { status: "ready", quote: settled.outcome.quote, pending: false, coupon: settled.coupon, points: settled.points } : { status: "error" };
  }
  return settled?.last ? { status: "ready", quote: settled.last, pending: true, coupon: settled.coupon, points: settled.points } : { status: "loading" };
}

// 每次计价返回之后：清除下单回答后的提示；成功时记下 points_max，积分栏大于它时改成它（标识随之改变，页面再计价一次）。
export function afterMemberQuote(member: MemberForm, quote: MemberQuote | null): MemberForm {
  const settled: MemberForm = { ...member, orderNotice: null };
  if (quote === null) {
    return settled;
  }
  const points = pointsCount(member.points) > quote.points_max ? String(quote.points_max) : member.points;
  return { ...settled, pointsMax: quote.points_max, points };
}

// checkout.coupon_invalid：下单回 coupon_invalid 之后到下一次计价返回之前，或最近一次返回的计价 coupon_applied 为假时显示。
export function couponInvalid(member: MemberForm, quote: MemberSummary): boolean {
  return member.orderNotice === "coupon_invalid" || (quote.status === "ready" && quote.quote.coupon_applied === false);
}

// 会员另加的下单条件：电话栏非空，且当前请求的计价已返回、所用的券代码与积分数就是最近一次点 checkout.coupon_apply 时的代码与积分栏当前的积分数。
// 券输入框改了而没有点 checkout.coupon_apply 不影响下单（仍带最近一次计价接受的券）。
export function memberPlaceable(member: MemberForm, quote: MemberSummary): boolean {
  return (
    member.phone.trim() !== "" && quote.status === "ready" && !quote.pending && quote.coupon === member.appliedCoupon && quote.points === pointsCount(member.points)
  );
}

// 下单带的券与积分：只带最近一次计价里 coupon_applied 为真的代码与 points_accepted 为真的积分数，否则不带券、积分为 0（HANDOFF 0.63 第（3）项）。
export function memberOrderChoice(quote: MemberSummary): { coupon: string | null; points: number } {
  if (quote.status !== "ready") {
    return { coupon: null, points: 0 };
  }
  return { coupon: quote.quote.coupon_applied === true ? quote.coupon : null, points: quote.quote.points_accepted ? quote.points : 0 };
}

// 由表单处理的下单回答（placed、signed_out 与 csrf 另有去向，网络中断由 runMemberOrder 以同一幂等键重试）。
export type MemberOrderAnswer = Exclude<MemberOrderReply, { kind: "placed" | "signed_out" | "csrf" | "network" }>;

// 下单回答之后的表单（已填内容一律保留）：not_placeable、conflict、unavailable 与 failed 同游客（cart.item_changed 并重新计价、换新幂等键并 common.error_retry、
// common.service_unavailable、common.error_retry）；phone_invalid、coupon_invalid 与 points_unavailable 停在第 3 步，提示在会员表单里。
export function formAfterMemberReply(form: CheckoutForm, reply: MemberOrderAnswer): CheckoutForm {
  const settled: CheckoutForm = { ...form, submitting: false, checking: false };
  switch (reply.kind) {
    case "not_placeable":
      return { ...settled, notice: "changed" };
    case "conflict":
      return { ...settled, attempt: null, notice: "error" };
    case "unavailable":
      return { ...settled, notice: "unavailable" };
    case "failed":
      return { ...settled, notice: "error" };
    case "phone_invalid":
    case "coupon_invalid":
    case "points_unavailable":
      return { ...settled, notice: null };
  }
}

// phone_invalid 在电话栏下显示 checkout.phone_invalid；coupon_invalid 与 points_unavailable 重新计价并显示 checkout.coupon_invalid 与 common.error_retry。
export function memberAfterReply(member: MemberForm, reply: MemberOrderAnswer): MemberForm {
  switch (reply.kind) {
    case "phone_invalid":
      return { ...member, phoneInvalid: true };
    case "coupon_invalid":
    case "points_unavailable":
      return { ...member, orderNotice: reply.kind, round: member.round + 1 };
    default:
      return member;
  }
}

// order_not_placeable 与游客相同，由页面加一游客计价的轮次重新计价。
export function memberReplyRequotes(reply: MemberOrderAnswer): boolean {
  return reply.kind === "not_placeable";
}

// 留在会员第 3 步并显示 common.error_retry（401 或 csrf 之后重读会话仍为会员，或 csrf 之后重读失败）。
export function retryNotice(form: CheckoutForm): CheckoutForm {
  return { ...form, submitting: false, checking: false, notice: "error" };
}

export function renewMember(member: MemberForm, csrfToken: string): MemberForm {
  return { ...member, csrfToken };
}

// 会员会话失效：回到第 1 步按游客路径继续，已填的收货资料保留（DESIGN 1.14 第 8 条）；会员改填的收货电话不带回第 1 步，第 1 步手机号栏为空。
export function lapseToGuest(form: CheckoutForm): CheckoutForm {
  return { ...form, step: 1, phoneInput: "", phoneError: null, guestNotice: null, submitting: false, checking: false, notice: null, attempt: null };
}

// 页面对会话重读结果的处理：renew 留在会员第 3 步并显示 common.error_retry（csrfToken 为新令牌，null 为保留原令牌）；lapse 为会话失效。
export interface SessionMoves {
  renew: (csrfToken: string | null) => void;
  lapse: () => void;
}

// 会员请求回 401：重读会话（共享读取，不中止）——member 时换用新令牌（例如另一标签页已重新登录），guest 与 failed 为会话失效。signal 中止之后不再更新。
export async function settleSignedOut(reader: MemberSessionReader, signal: AbortSignal, moves: SessionMoves): Promise<void> {
  const session = await reader.reload();
  if (signal.aborted) {
    return;
  }
  if (session.kind === "member") {
    moves.renew(session.csrfToken);
  } else {
    moves.lapse();
  }
}

// 下单回 403 csrf_failed：重读会话——member 时换用新令牌，guest 时为会话失效，failed 时保留原令牌；member 与 failed 都显示 common.error_retry。
export async function settleCsrf(reader: MemberSessionReader, signal: AbortSignal, moves: SessionMoves): Promise<void> {
  const session = await reader.reload();
  if (signal.aborted) {
    return;
  }
  if (session.kind === "member") {
    moves.renew(session.csrfToken);
  } else if (session.kind === "guest") {
    moves.lapse();
  } else {
    moves.renew(null);
  }
}

export interface PhoneMoves extends SessionMoves {
  fill: (phone: string) => void;
}

// 每次以会员身份进入第 3 步时取本人号码一次：ok 交给 fill（只在电话栏为空时填入）；signed_out 按 401 处理；
// failed 与 network 留空、不另加提示、不以遮掩号码预填（Kelvin 2026-10-26 决定）。signal 中止之后不再更新。
export async function prefillPhone(reader: MemberSessionReader, signal: AbortSignal, moves: PhoneMoves): Promise<void> {
  const read = await readMemberPhone(signal);
  if (signal.aborted) {
    return;
  }
  switch (read.kind) {
    case "ok":
      moves.fill(read.phone);
      return;
    case "signed_out":
      await settleSignedOut(reader, signal, moves);
      return;
    case "failed":
    case "network":
      return;
  }
}

export interface OrderMoves extends SessionMoves {
  placed: (orderNumber: string) => void;
  answer: (reply: MemberOrderAnswer) => void;
}

// 以会话里的 CSRF 令牌提交会员订单（网络中断时 runMemberOrder 以同一幂等键重试，DESIGN 1.14「失败、并发与重试」第 1 条）：
// placed 交给页面写入暂存并进入 /pay；signed_out 与 csrf 重读会话；其余交给 answer。signal 中止（离开页面）之后不再更新或跳转。
export async function submitMemberCheckout(
  body: MemberOrderBody,
  key: string,
  csrfToken: string,
  reader: MemberSessionReader,
  options: MemberOrderOptions & { signal: AbortSignal },
  moves: OrderMoves,
): Promise<void> {
  const reply = await runMemberOrder(body, key, csrfToken, options);
  if (reply === null || options.signal.aborted) {
    return;
  }
  switch (reply.kind) {
    case "placed":
      moves.placed(reply.orderNumber);
      return;
    case "signed_out":
      await settleSignedOut(reader, options.signal, moves);
      return;
    case "csrf":
      await settleCsrf(reader, options.signal, moves);
      return;
    default:
      moves.answer(reply);
  }
}

// 会员下单成功：清空本浏览器购物车，把订单号写入当前订单暂存（DESIGN 1.14 第 7 条，订单号取自下单回答），再以站内导航进入 /pay。
export function completeMemberOrder(
  storage: CartStorage | null,
  orderNumber: string,
  navigate: (path: RoutePath) => void,
  stash: (orderNumber: string) => void = stashMemberPayOrder,
): void {
  clearCart(storage);
  stash(orderNumber);
  navigate(PAY_PATH);
}

export interface MemberActions {
  onPhone: (value: string) => void;
  onCoupon: (value: string) => void;
  onApplyCoupon: () => void;
  onPoints: (value: string) => void;
}

// CheckoutView 的 member 属性：给出时为会员第 3 步（quote 为会员计价，代替游客的计价）。
export interface CheckoutMemberProps {
  form: MemberForm;
  quote: MemberSummary;
  actions: MemberActions;
}

// 字段错误的图形（取自视觉稿 P05-states），读屏忽略。
function FieldAlertIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

function describedBy(...ids: (string | null)[]): string {
  return ids.filter((id) => id !== null).join(" ");
}

interface MemberFieldProps {
  member: CheckoutMemberProps;
  submitting: boolean;
}

// 会员的收货电话（视觉稿 P05-desktop-3-member、P05-phone-3-member）：可编辑，其后为 checkout.member_phone_hint 与 checkout.phone_hint，
// 下单回 phone_invalid 时在其下显示 checkout.phone_invalid。
export function MemberPhoneField({ member, submitting }: MemberFieldProps) {
  const t = useCopy();
  const inputId = useId();
  const memberHintId = useId();
  const hintId = useId();
  const errorId = useId();
  const invalid = member.form.phoneInvalid;
  return (
    <div className={invalid ? "acs-field acs-field--invalid site-checkout__wide" : "acs-field site-checkout__wide"}>
      <label className="acs-field__label" htmlFor={inputId}>
        {t("checkout.phone")}
      </label>
      <input
        className="acs-input site-checkout__member-phone"
        id={inputId}
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        disabled={submitting}
        value={member.form.phone}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy(memberHintId, hintId, invalid ? errorId : null)}
        onChange={(event) => {
          member.actions.onPhone(event.target.value);
        }}
      />
      <span className="acs-field__hint" id={memberHintId}>
        {t("checkout.member_phone_hint")}
      </span>
      <span className="acs-field__hint" id={hintId}>
        {t("checkout.phone_hint")}
      </span>
      {invalid && (
        <span className="acs-field__error" id={errorId}>
          <FieldAlertIcon />
          {t("checkout.phone_invalid")}
        </span>
      )}
    </div>
  );
}

// 优惠券与积分（同两张视觉稿与 P05-states 的 checkout.coupon_invalid）：券输入框与 checkout.coupon_apply；积分栏，其后为 checkout.points_available
// （最近一次返回的计价的可用积分）与下单回 points_unavailable 后的 common.error_retry；最后是 checkout.points_not_shipping。
export function MemberPerks({ member, submitting }: MemberFieldProps) {
  const t = useCopy();
  const couponId = useId();
  const couponErrorId = useId();
  const pointsId = useId();
  const pointsHintId = useId();
  const { form, quote, actions } = member;
  const shown = quote.status === "ready" ? quote.quote : null;
  const invalid = couponInvalid(form, quote);
  return (
    <div className="site-checkout__perks">
      <div className={invalid ? "acs-field acs-field--invalid" : "acs-field"}>
        <label className="acs-field__label" htmlFor={couponId}>
          {t("checkout.coupon")}
        </label>
        <div className="site-checkout__coupon">
          <input
            className="acs-input"
            id={couponId}
            autoComplete="off"
            disabled={submitting}
            value={form.coupon}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={invalid ? couponErrorId : undefined}
            onChange={(event) => {
              actions.onCoupon(event.target.value);
            }}
          />
          <button className="acs-btn acs-btn--secondary" type="button" disabled={submitting} onClick={actions.onApplyCoupon}>
            {t("checkout.coupon_apply")}
          </button>
        </div>
        {invalid && (
          <span className="acs-field__error" id={couponErrorId}>
            <FieldAlertIcon />
            {t("checkout.coupon_invalid")}
          </span>
        )}
      </div>
      <div className="acs-field">
        <label className="acs-field__label" htmlFor={pointsId}>
          {t("checkout.points")}
        </label>
        <input
          className="acs-input acs-num site-checkout__points"
          id={pointsId}
          inputMode="numeric"
          autoComplete="off"
          disabled={submitting || form.pointsMax === null}
          value={form.points}
          aria-describedby={shown === null ? undefined : pointsHintId}
          onChange={(event) => {
            actions.onPoints(event.target.value);
          }}
        />
        {shown !== null && (
          <span className="acs-field__hint" id={pointsHintId}>
            {t("checkout.points_available", { points: shown.points_available })}
          </span>
        )}
        {form.orderNotice === "points_unavailable" && <ErrorNotice />}
      </div>
      <p className="acs-body-s acs-muted">{t("checkout.points_not_shipping")}</p>
    </div>
  );
}

// 摘要的券与积分两行（选定收货国家之后，在商品小计与示例运费之间）：为 0 也显示，金额原样格式化、不加负号；积分抵扣按 1 积分 1 仙格式化（HANDOFF 2026-10-24 运营者落实写法第（8）项、0.63 第（3）项）。
export function MemberDiscounts({ quote }: { quote: MemberQuote }) {
  const t = useCopy();
  const price = (sen: number) => t("common.price_myr", { amount: formatSen(sen) });
  return (
    <>
      <div className="acs-row">
        <span>{t("checkout.summary_coupon")}</span>
        <span>{price(quote.coupon_discount_sen)}</span>
      </div>
      <div className="acs-row">
        <span>{t("checkout.summary_points")}</span>
        <span>{price(quote.points_redeemed)}</span>
      </div>
    </>
  );
}
