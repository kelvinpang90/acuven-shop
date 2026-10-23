import { useEffect, useId, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";

import { sendSmsCode, smsLogin, verifyPasswordReset } from "../api/memberAuth";
import type { MemberPhone, ResetVerifyReply, SmsLoginReply, SmsSendReply } from "../api/memberAuth";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link } from "../router";
import type { RoutePath } from "../router";
import { TURNSTILE } from "./turnstile";
import type { ChallengeLoader, ChallengeWidget } from "./turnstile";

// 短信验证组件 V1（docs/UX.md 0.12 V1，视觉稿 V1-start、V1-code-wrong、V1-errors、V1-register-unavailable、V1-no-change 与 V1-verified；
// DESIGN 1.12「权限与资料保护」第 1、3 条与「失败、并发与重试」，HANDOFF 0.58）。本任务只有两种用途：注册 / 短信登录（register、login，
// 验证通过后服务端对已注册号码登录、未注册号码创建会员）与重设密码（reset_password，不创建账号）；结账验证与注销确认由之后的任务加入。
// 号码为国家码（只有 +60、+65，默认 +60）与输入原文，格式、规范化、白名单、限流与能否降级都由服务端判定，组件只按回答显示提示。
// 发送前须完成托管人机挑战（turnstile.ts），每次发送之后重置挑战、取新令牌。手机号、验证码与令牌只在组件状态与请求体里，
// 组件不跳转、不写浏览器存储；验证通过后调用 onVerified，结果文字由嵌入的页面显示。卸载时中止进行中的请求，之后的结果不再更新。

export type SmsPurpose = "register" | "login" | "reset_password";

export type SmsRegion = "MY" | "SG";

// 国家码下拉（UX V1「结账以外只列 +60、+65」），默认第一项。
export const SMS_REGIONS: readonly { region: SmsRegion; callingCode: string }[] = [
  { region: "MY", callingCode: "60" },
  { region: "SG", callingCode: "65" },
];

const CALLING_CODE: Readonly<Record<SmsRegion, string>> = { MY: "60", SG: "65" };

// 组件提交的号码：输入原文与下拉所选的地区。
export type SmsPhone = MemberPhone & { region: SmsRegion };

// 与 app/api/member_auth.py 的 mask_phone 相同：保留前 3 个与最后 4 个字符，中间每个字符换成星号；不足 8 个字符时整串换成星号。
const MASK_HEAD = 3;
const MASK_TAIL = 4;

export function maskPhone(phone: string): string {
  if (phone.length <= MASK_HEAD + MASK_TAIL) {
    return "*".repeat(phone.length);
  }
  return phone.slice(0, MASK_HEAD) + "*".repeat(phone.length - MASK_HEAD - MASK_TAIL) + phone.slice(-MASK_TAIL);
}

// auth.code_sent 的 {phoneMasked}：输入以 + 开头时为 + 与其后的全部数字，否则为国家码加输入里的数字（去掉开头的一个 0），再遮掩。
// 只用于显示；提交的是输入原文与地区，由服务端规范化。
export function maskedPhone(phone: SmsPhone): string {
  const trimmed = phone.input.trim();
  const digits = trimmed.replace(/\D/g, "");
  const full = trimmed.startsWith("+") ? `+${digits}` : `+${CALLING_CODE[phone.region]}${digits.replace(/^0/, "")}`;
  return maskPhone(full);
}

// 验证通过时回调收到的值：注册 / 短信登录为 created（是否新建了会员）；重设密码为号码已注册（带随后提交新密码用的 CSRF 令牌）或未注册。
export type SmsVerified = { kind: "member"; created: boolean } | { kind: "registered"; csrfToken: string } | { kind: "not_registered" };

// 组件的状态。region、input 与 code 为输入；token 为完成挑战取得的令牌（null 时发送按钮禁用）；challengeLost 为挑战加载失败或令牌过期；
// sending、verifying 为请求进行中；sent 为最近一次 sent 的号码（验证码以它提交，masked 为 auth.code_sent 的 {phoneMasked}）；
// phoneInvalid 为号码下的 checkout.phone_invalid；codeWrong 为验证码下的 auth.code_wrong；notice 为其他提示；
// paused 为流程中开关被关闭（disabled）；verified 为已验证。
export interface SmsState {
  region: SmsRegion;
  input: string;
  code: string;
  token: string | null;
  challengeLost: boolean;
  sending: boolean;
  verifying: boolean;
  sent: { phone: SmsPhone; masked: string } | null;
  phoneInvalid: boolean;
  codeWrong: boolean;
  notice: CopyKey | null;
  paused: boolean;
  verified: boolean;
}

export const INITIAL_SMS: SmsState = {
  region: "MY",
  input: "",
  code: "",
  token: null,
  challengeLost: false,
  sending: false,
  verifying: false,
  sent: null,
  phoneInvalid: false,
  codeWrong: false,
  notice: null,
  paused: false,
  verified: false,
};

// 对当前状态的一次改动（请求的结果按到达时的状态更新，不覆盖期间的输入）。
export type SmsChange = (current: SmsState) => SmsState;

export function chooseRegion(state: SmsState, region: SmsRegion): SmsState {
  return { ...state, region };
}

export function editPhone(state: SmsState, input: string): SmsState {
  return { ...state, input };
}

export function editCode(state: SmsState, code: string): SmsState {
  return { ...state, code };
}

export function challengeToken(state: SmsState, token: string): SmsState {
  return { ...state, token, challengeLost: false };
}

// 挑战加载失败、出错或令牌过期：没有可用的令牌，显示 auth.challenge_failed。
export function challengeLost(state: SmsState): SmsState {
  return { ...state, token: null, challengeLost: true };
}

// 挑战重新渲染（换了界面语言）：先前的令牌不再用。
export function challengeReset(state: SmsState): SmsState {
  return { ...state, token: null, challengeLost: false };
}

// 完成挑战之前、发送或验证进行中、已验证与暂停时发送（与重发）按钮禁用。
export function canSend(state: SmsState): boolean {
  return state.token !== null && !state.sending && !state.verifying && !state.verified && !state.paused;
}

// 收到验证码之前、验证码为空、发送或验证进行中、已验证与暂停时验证按钮禁用。
export function canVerify(state: SmsState): boolean {
  return state.sent !== null && state.code.trim() !== "" && !state.sending && !state.verifying && !state.verified && !state.paused;
}

export function phoneOf(state: SmsState): SmsPhone {
  return { input: state.input, region: state.region };
}

// 点发送或重发：按钮禁用，先前的提示都收起。
export function startSending(state: SmsState): SmsState {
  return { ...state, sending: true, phoneInvalid: false, codeWrong: false, notice: null };
}

// 点验证：按钮禁用，先前的提示都收起。
export function startVerifying(state: SmsState): SmsState {
  return { ...state, verifying: true, phoneInvalid: false, codeWrong: false, notice: null };
}

const REGISTER_PURPOSES: ReadonlySet<SmsPurpose> = new Set<SmsPurpose>(["register", "login"]);

// 号码不在白名单（UX V1）：注册 / 短信登录为 auth.not_supported_country，重设密码为 auth.sms_not_sent_no_change。
function notWhitelisted(purpose: SmsPurpose): CopyKey {
  return REGISTER_PURPOSES.has(purpose) ? "auth.not_supported_country" : "auth.sms_not_sent_no_change";
}

// 短信无法送达或停发（UX V1）：注册 / 短信登录为 auth.sms_failed，重设密码为 auth.sms_not_sent_no_change。
function notSent(purpose: SmsPurpose): CopyKey {
  return REGISTER_PURPOSES.has(purpose) ? "auth.sms_failed" : "auth.sms_not_sent_no_change";
}

// 同一行显示 auth.continue_guest 的提示（只有注册 / 短信登录会出现）；验证码错误不在其中。
export const GUEST_NOTICES: ReadonlySet<CopyKey> = new Set<CopyKey>(["auth.not_supported_country", "auth.sms_failed"]);

// 按发送的结果决定（每种结果都收起进行中、作废已用的令牌）：sent 记下号码并显示验证码输入（重发时清空已填的验证码）；
// disabled 整个组件换成暂停提示；phone_invalid 在号码下显示 checkout.phone_invalid；not_whitelisted、undeliverable 与 suspended 按用途显示；
// captcha_failed 为 auth.challenge_failed，rate_limited 为 common.rate_limited，network 为 common.network_check，其他失败为 common.error_retry。
export function sendStep(reply: SmsSendReply, purpose: SmsPurpose, phone: SmsPhone): SmsChange {
  return (current) => {
    const settled: SmsState = { ...current, sending: false, token: null };
    switch (reply) {
      case "sent":
        return { ...settled, sent: { phone, masked: maskedPhone(phone) }, code: "" };
      case "disabled":
        return { ...settled, paused: true };
      case "phone_invalid":
        return { ...settled, phoneInvalid: true };
      case "not_whitelisted":
        return { ...settled, notice: notWhitelisted(purpose) };
      case "undeliverable":
      case "suspended":
        return { ...settled, notice: notSent(purpose) };
      case "captcha_failed":
        return { ...settled, notice: "auth.challenge_failed" };
      case "rate_limited":
        return { ...settled, notice: "common.rate_limited" };
      case "network":
        return { ...settled, notice: "common.network_check" };
      case "failed":
        return { ...settled, notice: "common.error_retry" };
    }
  };
}

// 验证的结果：change 为对状态的改动，verified 为通过时交给回调的值（未通过为 null）。
export interface VerifyStep {
  change: SmsChange;
  verified: SmsVerified | null;
}

function settledVerify(patch: Partial<SmsState>): SmsChange {
  return (current) => ({ ...current, verifying: false, ...patch });
}

// 按验证的结果决定：通过（ok、registered、not_registered）进入已验证状态；wrong 在验证码下显示 auth.code_wrong（不出现 auth.continue_guest）；
// disabled 整个组件换成暂停提示；unavailable 显示 common.service_unavailable，留在验证码输入；phone_invalid 显示 checkout.phone_invalid；
// network 为 common.network_check，其他失败为 common.error_retry。
export function verifyStep(reply: SmsLoginReply | ResetVerifyReply): VerifyStep {
  switch (reply.kind) {
    case "ok":
      return { change: settledVerify({ verified: true }), verified: { kind: "member", created: reply.created } };
    case "registered":
      return { change: settledVerify({ verified: true }), verified: { kind: "registered", csrfToken: reply.csrfToken } };
    case "not_registered":
      return { change: settledVerify({ verified: true }), verified: { kind: "not_registered" } };
    case "wrong":
      return { change: settledVerify({ codeWrong: true }), verified: null };
    case "disabled":
      return { change: settledVerify({ paused: true }), verified: null };
    case "unavailable":
      return { change: settledVerify({ notice: "common.service_unavailable" }), verified: null };
    case "phone_invalid":
      return { change: settledVerify({ phoneInvalid: true }), verified: null };
    case "network":
      return { change: settledVerify({ notice: "common.network_check" }), verified: null };
    case "failed":
      return { change: settledVerify({ notice: "common.error_retry" }), verified: null };
  }
}

// 组件对结果的处理：按当前状态改动、重置挑战与验证通过的回调。
export interface SmsMoves {
  update: (change: SmsChange) => void;
  resetChallenge: () => void;
  verified: (verified: SmsVerified) => void;
}

// 发送（与重发）验证码：register 与 login 以同名 purpose 发送，reset_password 亦然。signal 中止（组件已卸载）之后不再更新；
// 否则无论结果都在更新状态之后重置挑战，下一次发送要重新完成挑战。
export async function sendCode(purpose: SmsPurpose, phone: SmsPhone, token: string, signal: AbortSignal, moves: SmsMoves): Promise<void> {
  const reply = await sendSmsCode(phone, purpose, token, signal);
  if (signal.aborted) {
    return;
  }
  moves.update(sendStep(reply, purpose, phone));
  moves.resetChallenge();
}

// 提交验证码：register 与 login 提交短信登录（同名 purpose），reset_password 提交重设验证。signal 中止之后不再更新；
// 通过时先调用回调，再进入已验证状态。
export async function submitCode(purpose: SmsPurpose, phone: MemberPhone, code: string, signal: AbortSignal, moves: SmsMoves): Promise<void> {
  const reply = purpose === "reset_password" ? await verifyPasswordReset(phone, code, signal) : await smsLogin(phone, purpose, code, signal);
  if (signal.aborted) {
    return;
  }
  const step = verifyStep(reply);
  if (step.verified !== null) {
    moves.verified(step.verified);
  }
  moves.update(step.change);
}

// 挑战的处理：token 为取得令牌，lost 为加载失败、出错或过期，widget 为已渲染（或已移除，null）的挑战。
export interface ChallengeMoves {
  token: (token: string) => void;
  lost: () => void;
  widget: (widget: ChallengeWidget | null) => void;
}

// 在 container 里渲染挑战；返回的函数移除它，之后才到的令牌、失败与渲染结果都不再处理（渲染晚到时随即移除）。
export function mountChallenge(loader: ChallengeLoader, container: HTMLElement, siteKey: string, language: Language, moves: ChallengeMoves): () => void {
  let closed = false;
  let mounted: ChallengeWidget | null = null;
  void loader
    .render(container, siteKey, language, {
      onToken: (token) => {
        if (!closed) {
          moves.token(token);
        }
      },
      onLost: () => {
        if (!closed) {
          moves.lost();
        }
      },
    })
    .then(
      (widget) => {
        if (closed) {
          widget.remove();
        } else {
          mounted = widget;
          moves.widget(widget);
        }
      },
      () => {
        if (!closed) {
          moves.lost();
        }
      },
    );
  return () => {
    closed = true;
    mounted?.remove();
    moves.widget(null);
  };
}

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.5" />
    </svg>
  );
}

function AlertIcon({ size }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

// 视觉稿 V1-code-wrong 的 auth.code_sent 图标。
function MailIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  );
}

// 提示一行：danger 为 role="alert"（V1-errors、V1-no-change），info 为 role="status"（V1-register-unavailable）。
function Notice({ copy, info }: { copy: CopyKey; info: boolean }) {
  const t = useCopy();
  return info ? (
    <div className="acs-alert acs-alert--info" role="status">
      <InfoIcon />
      <span>{t(copy)}</span>
    </div>
  ) : (
    <div className="acs-alert acs-alert--danger" role="alert">
      <AlertIcon />
      <span>{t(copy)}</span>
    </div>
  );
}

// auth.continue_guest：指向嵌入页给出的站内去向的链接（V1-register-unavailable 的次要按钮样式）。
function GuestLink({ to }: { to: RoutePath }) {
  const t = useCopy();
  return (
    <Link className="acs-btn acs-btn--secondary site-sms__guest" to={to}>
      {t("auth.continue_guest")}
    </Link>
  );
}

export interface SmsHandlers {
  onRegion: (region: SmsRegion) => void;
  onPhone: (input: string) => void;
  onCode: (code: string) => void;
  onSend: () => void;
  onVerify: () => void;
}

export interface SmsVerifyViewProps extends SmsHandlers {
  purpose: SmsPurpose;
  smsEnabled: boolean;
  siteKey: string | null;
  // auth.continue_guest 的去向；只有注册 / 短信登录用到，重设密码为 null。
  guestTo: RoutePath | null;
  state: SmsState;
  // 托管挑战的容器（View 只放一个空的 div，挑战由组件经 turnstile.ts 渲染进去）。
  challengeRef: (element: HTMLDivElement | null) => void;
}

// 整个组件替换为一条提示（不显示号码、挑战与发送按钮）：开关关闭或流程中被关闭时为 auth.sms_paused；公开 key 为 null 时注册 / 短信登录为
// auth.sms_failed、重设密码为 auth.sms_not_sent_no_change。注册 / 短信登录另有 auth.continue_guest。
function replacement(props: Pick<SmsVerifyViewProps, "purpose" | "smsEnabled" | "siteKey" | "state">): CopyKey | null {
  if (!props.smsEnabled || props.state.paused) {
    return "auth.sms_paused";
  }
  return props.siteKey === null ? notSent(props.purpose) : null;
}

export function SmsVerifyView({ purpose, smsEnabled, siteKey, guestTo, state, challengeRef, onRegion, onPhone, onCode, onSend, onVerify }: SmsVerifyViewProps) {
  const t = useCopy();
  const labelId = useId();
  const phoneId = useId();
  const phoneErrorId = useId();
  const codeId = useId();
  const codeErrorId = useId();
  const guest = REGISTER_PURPOSES.has(purpose) ? guestTo : null;
  const replaced = replacement({ purpose, smsEnabled, siteKey, state });
  if (replaced !== null) {
    return (
      <div className="site-sms">
        <Notice copy={replaced} info={replaced !== "auth.sms_not_sent_no_change"} />
        {guest !== null && <GuestLink to={guest} />}
      </div>
    );
  }
  const { sent, verified } = state;
  // 服务端的 captcha_failed 与挑战本身的失败是同一条提示，只显示一次。
  const notice = state.notice === "auth.challenge_failed" && state.challengeLost ? null : state.notice;
  return (
    <div className="site-sms">
      {!verified && (
        <>
          <div className={state.phoneInvalid ? "acs-field acs-field--invalid" : "acs-field"}>
            <label className="acs-field__label" id={labelId} htmlFor={phoneId}>
              {t("auth.phone")}
            </label>
            <div className="acs-phone site-sms__phone">
              <select
                className="acs-select"
                aria-labelledby={labelId}
                value={state.region}
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
                value={state.input}
                aria-invalid={state.phoneInvalid ? true : undefined}
                aria-describedby={state.phoneInvalid ? phoneErrorId : undefined}
                onChange={(event) => {
                  onPhone(event.target.value);
                }}
              />
            </div>
            {state.phoneInvalid && (
              <span className="acs-field__error" id={phoneErrorId}>
                <AlertIcon size={16} />
                {t("checkout.phone_invalid")}
              </span>
            )}
          </div>
          <p className="acs-body-s acs-muted site-sms__text">{t("auth.sms_scope")}</p>
          {REGISTER_PURPOSES.has(purpose) && <p className="acs-body-s site-sms__text">{t("auth.claim_notice")}</p>}
          <span className="acs-body-s">{t("auth.challenge")}</span>
          <div className="site-sms__challenge" ref={challengeRef} />
          {state.challengeLost && <Notice copy="auth.challenge_failed" info={false} />}
          {sent === null && (
            <button
              className="acs-btn acs-btn--primary site-sms__send"
              type="button"
              disabled={!canSend(state)}
              aria-busy={state.sending}
              onClick={() => {
                onSend();
              }}
            >
              {t("auth.send_code")}
            </button>
          )}
        </>
      )}
      {sent !== null && (
        <div className="acs-alert acs-alert--info" role="status">
          <MailIcon />
          <span>{t("auth.code_sent", { phoneMasked: sent.masked })}</span>
        </div>
      )}
      {sent !== null && !verified && (
        <div className={state.codeWrong ? "acs-field acs-field--invalid" : "acs-field"}>
          <label className="acs-field__label" htmlFor={codeId}>
            {t("auth.code")}
          </label>
          <div className="site-sms__code">
            <input
              className="acs-input acs-otp"
              id={codeId}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={state.code}
              aria-invalid={state.codeWrong ? true : undefined}
              aria-describedby={state.codeWrong ? codeErrorId : undefined}
              onChange={(event) => {
                onCode(event.target.value);
              }}
            />
            <button
              className="acs-btn acs-btn--primary"
              type="button"
              disabled={!canVerify(state)}
              aria-busy={state.verifying}
              onClick={() => {
                onVerify();
              }}
            >
              {t("auth.verify_submit")}
            </button>
          </div>
          {state.codeWrong && (
            <span className="acs-field__error" id={codeErrorId}>
              <AlertIcon size={16} />
              {t("auth.code_wrong")}
            </span>
          )}
        </div>
      )}
      {!verified && notice !== null && <Notice copy={notice} info={GUEST_NOTICES.has(notice)} />}
      {!verified && notice !== null && GUEST_NOTICES.has(notice) && guest !== null && <GuestLink to={guest} />}
      {sent !== null && !verified && (
        <button
          className="acs-btn acs-btn--quiet site-sms__resend"
          type="button"
          disabled={!canSend(state)}
          aria-busy={state.sending}
          onClick={() => {
            onSend();
          }}
        >
          {t("auth.resend_code")}
        </button>
      )}
    </div>
  );
}

interface CommonProps {
  smsEnabled: boolean;
  // 人机挑战公开 key（站点设置 turnstile_site_key）；null 时整个组件替换。
  siteKey: string | null;
  onVerified: (verified: SmsVerified) => void;
  // 挑战的加载与渲染；默认 Cloudflare Turnstile。
  challenge?: ChallengeLoader;
}

// 注册 / 短信登录须给出 auth.continue_guest 的去向（站内前台路径），重设密码不传。
export type SmsVerifyProps = CommonProps & ({ purpose: "register" | "login"; guestTo: RoutePath } | { purpose: "reset_password" });

function smsMoves(setState: Dispatch<SetStateAction<SmsState>>, widget: RefObject<ChallengeWidget | null>, onVerified: (verified: SmsVerified) => void): SmsMoves {
  return {
    update: (change) => {
      setState(change);
    },
    resetChallenge: () => {
      widget.current?.reset();
    },
    verified: onVerified,
  };
}

export default function SmsVerify(props: SmsVerifyProps) {
  const { purpose, smsEnabled, siteKey, onVerified } = props;
  const loader = props.challenge ?? TURNSTILE;
  const guestTo = props.purpose === "reset_password" ? null : props.guestTo;
  const { language } = useLanguage();
  const [state, setState] = useState<SmsState>(INITIAL_SMS);
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  // 换了界面语言或公开 key 时挑战重新渲染，先前的令牌不再用（与 AdminStoreDesignPage 换语言的写法相同，在渲染时更新）。
  const challengeFor = `${language} ${siteKey ?? ""}`;
  const [shownFor, setShownFor] = useState(challengeFor);
  if (shownFor !== challengeFor) {
    setShownFor(challengeFor);
    setState(challengeReset);
  }
  const widget = useRef<ChallengeWidget | null>(null);
  // 进行中的发送与验证共用的中止；卸载时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
    };
  }, []);

  // 挑战容器在页面上（未替换、未验证）时渲染挑战；容器移除或换语言时移除。
  useEffect(() => {
    if (box === null || siteKey === null) {
      return undefined;
    }
    return mountChallenge(loader, box, siteKey, language, {
      token: (token) => {
        setState((current) => challengeToken(current, token));
      },
      lost: () => {
        setState(challengeLost);
      },
      widget: (mounted) => {
        widget.current = mounted;
      },
    });
  }, [box, siteKey, language, loader]);

  return (
    <SmsVerifyView
      purpose={purpose}
      smsEnabled={smsEnabled}
      siteKey={siteKey}
      guestTo={guestTo}
      state={state}
      challengeRef={setBox}
      onRegion={(region) => {
        setState((current) => chooseRegion(current, region));
      }}
      onPhone={(input) => {
        setState((current) => editPhone(current, input));
      }}
      onCode={(code) => {
        setState((current) => editCode(current, code));
      }}
      onSend={() => {
        const controller = lifetime.current;
        const token = state.token;
        if (!smsEnabled || siteKey === null || !canSend(state) || token === null || controller === null) {
          return;
        }
        setState(startSending);
        void sendCode(purpose, phoneOf(state), token, controller.signal, smsMoves(setState, widget, onVerified));
      }}
      onVerify={() => {
        const controller = lifetime.current;
        if (!smsEnabled || !canVerify(state) || state.sent === null || controller === null) {
          return;
        }
        setState(startVerifying);
        void submitCode(purpose, state.sent.phone, state.code.trim(), controller.signal, smsMoves(setState, widget, onVerified));
      }}
    />
  );
}
