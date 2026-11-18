import { Fragment, useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";

import { readAiStatus, requestAiProposal } from "../api/adminPromotionAi";
import type { AiProposal, AiProposalRequest, AiProposalResult, AiRuleInput, AiStatusRead } from "../api/adminPromotionAi";
import type { PromotionDecoration } from "../api/adminPromotions";
import { AdminAlert, LANGUAGE_LABEL } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey } from "../i18n/copy";
import { htmlLang, useCopy } from "../i18n/language";
import { PROMO_AMBIENT_NONE, PROMO_KEEP_THEME, PROMO_PACKS, PROMO_SLOTS, PROMO_VARIANTS } from "../promoManifest";
import type { PromoPack, PromoSlot } from "../promoManifest";
import { startDeferred } from "./AdminOrderDetail";
import { PACK_LABEL } from "./AdminPromotionDecoration";
import { rereadNotice } from "./AdminStoreLogo";
import type { RereadOutcome } from "./AdminStoreLogo";
import { copyLength, formProblems, promotionInput } from "./promotionForm";
import type { CopyField, PromotionForm, PromotionFormProblem } from "./promotionForm";

// 后台促销活动 A12 编辑区的 AI 区（docs/UX.md 0.20 A12 线框「+---- [admin.ai_title] ----」一段与说明的「AI」一条，视觉稿 A12-editor-desktop 的 Design with AI
// 与 A12-states 的 AI 结果和填回后；HANDOFF 0.98 落实写法（1）至（5）；DESIGN 1.23「节日促销活动」第 13 条，SHOP-TASK-276）：
// - 打开时以 readAiStatus（SHOP-TASK-267）读一次状态：未配置时 admin.ai_generate 禁用并显示 admin.ai_not_configured；401 回到 A01；网络中断与其他失败时按钮照常可用。
// - admin.ai_describe 为单行输入框（已用字数「<已用>/500」，旁为 admin.ai_privacy）；描述只在本区的内存里，不进表单、不随保存提交。字数与 1–500 的检查用 copyLength。
// - 点 admin.ai_generate：先查描述与表单里的规则参数（formProblems 里 rules.N.<参数> 与 rules.N.tiers…，不含范围与条数），不合格时不发请求、在字段旁提示；
//   都合格时以 requestAiProposal 只送描述与规则参数（不送商品、分类 ID 与任何个人资料）。成功时显示提案，点 admin.ai_apply 才经 onApply 填回表单（装饰与 12 个文案字段，
//   由编辑页计入未保存，经普通保存提交）；不改规则、时间、开关与金额。
// - 已结束只读：不显示生成与填回按钮。提案、描述与提示都按纯文本显示。卸载（admin.reload、离开页面）时中止进行中的请求，之后不再更新。

// 描述的字符数上限（DESIGN 1.23 第 13 条「1–500 个字符」）。
export const AI_DESCRIPTION_MAX = 500;

// 已用字数「<已用>/500」。
export function describeCount(description: string): string {
  return `${String(copyLength(description))}/${String(AI_DESCRIPTION_MAX)}`;
}

// 描述合格：pyStrip 后 1 至 500 个码点（同服务端 description.strip()）。
export function descriptionOk(description: string): boolean {
  const length = copyLength(description);
  return length >= 1 && length <= AI_DESCRIPTION_MAX;
}

// 规则参数的问题：field 为 rules.N.<参数> 或 rules.N.tiers 开头；范围（product_ids、category_ids）与条数（rules）不送给 AI，不在其中。
const AI_RULE_FIELD = /^rules\.[0-9]+\.(?!(?:product_ids|category_ids)$)/;

export function isAiRuleField(field: string): boolean {
  return AI_RULE_FIELD.test(field);
}

export function aiRuleProblems(form: PromotionForm): PromotionFormProblem[] {
  return formProblems(form).filter((problem) => isAiRuleField(problem.field));
}

// 送出的规则：promotionInput 的规则只取类别与参数，不带商品与分类 ID。
export function aiRules(form: PromotionForm): AiRuleInput[] {
  return promotionInput(form).rules.map((rule) => ({
    kind: rule.kind,
    discount_mode: rule.discount_mode,
    percent: rule.percent,
    amount_sen: rule.amount_sen,
    buy_quantity: rule.buy_quantity,
    get_quantity: rule.get_quantity,
    get_percent: rule.get_percent,
    threshold_sen: rule.threshold_sen,
    tiers: rule.tiers,
  }));
}

// 填回的 12 个文案字段（标题、副标题、角标短语与横条文字的三语）；替代文字不在其中。
export const AI_COPY_FIELDS = [
  "title_en",
  "title_zh",
  "title_ms",
  "subtitle_en",
  "subtitle_zh",
  "subtitle_ms",
  "badge_en",
  "badge_zh",
  "badge_ms",
  "strip_en",
  "strip_zh",
  "strip_ms",
] as const satisfies readonly (CopyField & keyof AiProposal)[];

// 提案的装饰：素材包、配色与六个装饰位换成提案的值，poster_mode 不变。
export function proposalDecoration(decoration: PromotionDecoration, proposal: AiProposal): PromotionDecoration {
  return {
    ...decoration,
    pack: proposal.pack,
    palette: proposal.palette,
    ambient: proposal.ambient,
    home_poster: proposal.home_poster,
    list_strip: proposal.list_strip,
    card_badge: proposal.card_badge,
    detail_ribbon: proposal.detail_ribbon,
    cart_progress: proposal.cart_progress,
  };
}

// 填回（落实写法（3））：装饰按 proposalDecoration，12 个文案字段换成提案的值（null 填为空串）；名称、时间、开关、规则、poster_mode 与替代文字不变。
export function applyProposal(form: PromotionForm, proposal: AiProposal): PromotionForm {
  const copy = { ...form.copy };
  for (const field of AI_COPY_FIELDS) {
    copy[field] = proposal[field] ?? "";
  }
  return { ...form, decoration: proposalDecoration(form.decoration, proposal), copy };
}

// 区内的提示：admin.ai_invalid、admin.ai_unavailable、网络中断与其他失败（AdminAlert 的写法）；admin.ai_daily_limit（count 为响应的 limit）；
// 规则参数被服务端拒绝时的 admin.promo_invalid 与 admin.promo_invalid_other。
export type AiNotice =
  | { kind: "alert"; key: "admin.ai_invalid" | "admin.ai_unavailable" | "common.network_check" | "common.error_retry" }
  | { kind: "daily_limit"; limit: number }
  | { kind: "invalid_other" };

// 生成按钮之下显示的：提案、填回之后的 admin.ai_applied，或提示。
export type AiResult = { kind: "proposal"; proposal: AiProposal } | { kind: "applied" } | AiNotice;

// AI 区的状态：description 只在这里（不进表单）；descriptionInvalid 为描述旁显示 admin.promo_field_invalid；notConfigured 为已知未配置（按钮禁用）；
// generating 为请求进行中。
export interface AiState {
  description: string;
  descriptionInvalid: boolean;
  notConfigured: boolean;
  generating: boolean;
  result: AiResult | null;
}

export const AI_IDLE: AiState = { description: "", descriptionInvalid: false, notConfigured: false, generating: false, result: null };

// 状态读取的结果（落实写法（1））：configured 为假时为 not_configured；401 回到 A01；为真、网络中断与其他失败时按钮照常可用。
export function aiStatusStep(read: AiStatusRead): "usable" | "not_configured" | "login" {
  switch (read.kind) {
    case "ok":
      return read.configured ? "usable" : "not_configured";
    case "none":
      return "login";
    case "network":
    case "failed":
      return "usable";
  }
}

// 读取一次状态；signal 中止之后不再更新。
export async function loadAiStatus(
  signal: AbortSignal,
  moves: { notConfigured: (value: boolean) => void; login: () => void },
  read: typeof readAiStatus = readAiStatus,
): Promise<void> {
  const step = aiStatusStep(await read(signal));
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.login();
  } else {
    moves.notConfigured(step === "not_configured");
  }
}

// 改描述：描述旁的提示只在仍不合格时保留。
export function changeDescription(state: AiState, description: string): AiState {
  return { ...state, description, descriptionInvalid: state.descriptionInvalid && !descriptionOk(description) };
}

// 点 admin.ai_generate 的结果：不响应（只读、未配置或进行中）；检查不合格（不发请求，rulesInvalid 时规则区在字段旁提示）；或以 request 开始请求。
export type GeneratePress =
  | { kind: "ignored" }
  | { kind: "rejected"; state: AiState; rulesInvalid: boolean }
  | { kind: "submit"; state: AiState; request: AiProposalRequest };

// 落实写法（2）（3）：先查描述（1–500）与规则参数，任一不合格都不发请求（已有的提案保留，其他提示收起）；都合格时只送描述与规则参数，进入提交中、先前的结果收起。
export function pressGenerate(state: AiState, form: PromotionForm, readOnly: boolean): GeneratePress {
  if (readOnly || state.notConfigured || state.generating) {
    return { kind: "ignored" };
  }
  const descriptionInvalid = !descriptionOk(state.description);
  const rulesInvalid = aiRuleProblems(form).length > 0;
  if (descriptionInvalid || rulesInvalid) {
    const result = state.result?.kind === "proposal" ? state.result : null;
    return { kind: "rejected", state: { ...state, descriptionInvalid, result }, rulesInvalid };
  }
  return {
    kind: "submit",
    state: { ...state, descriptionInvalid: false, generating: true, result: null },
    request: { description: state.description, rules: aiRules(form) },
  };
}

// 请求结束之后的结果：提案、提示、未配置，或描述与规则参数被服务端拒绝（description 为描述旁提示，notice 为区内提示）。
export type AiOutcome =
  | { kind: "proposal"; proposal: AiProposal }
  | { kind: "notice"; notice: AiNotice }
  | { kind: "not_configured" }
  | { kind: "invalid"; description: boolean; notice: AiNotice | null };

// 落实写法（4）：result_invalid admin.ai_invalid；unavailable admin.ai_unavailable；daily_limit admin.ai_daily_limit（count 为响应的 limit）；not_configured 禁用按钮；
// invalid 时 description 的问题标在描述旁，另有规则参数的问题时区内显示 admin.promo_invalid 与 admin.promo_invalid_other；csrf 重新读取令牌；401 回到 A01；
// 网络中断 common.network_check，其他失败 common.error_retry。
export function proposalStep(result: AiProposalResult): AiOutcome | "login" | "reread" {
  switch (result.kind) {
    case "ok":
      return { kind: "proposal", proposal: result.proposal };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "invalid": {
      const description = result.problems.some((problem) => problem.field === "description");
      const other = result.problems.some((problem) => problem.field.startsWith("rules"));
      if (!description && !other) {
        return { kind: "notice", notice: { kind: "alert", key: "common.error_retry" } };
      }
      return { kind: "invalid", description, notice: other ? { kind: "invalid_other" } : null };
    }
    case "not_configured":
      return { kind: "not_configured" };
    case "daily_limit":
      return { kind: "notice", notice: { kind: "daily_limit", limit: result.limit } };
    case "unavailable":
      return { kind: "notice", notice: { kind: "alert", key: "admin.ai_unavailable" } };
    case "result_invalid":
      return { kind: "notice", notice: { kind: "alert", key: "admin.ai_invalid" } };
    case "network":
      return { kind: "notice", notice: { kind: "alert", key: "common.network_check" } };
    case "failed":
      return { kind: "notice", notice: { kind: "alert", key: "common.error_retry" } };
  }
}

// 请求结束：只改提交中的状态（之后的结果不覆盖）。
export function finishGenerate(state: AiState, outcome: AiOutcome): AiState {
  if (!state.generating) {
    return state;
  }
  const done = { ...state, generating: false };
  switch (outcome.kind) {
    case "proposal":
      return { ...done, result: { kind: "proposal", proposal: outcome.proposal } };
    case "notice":
      return { ...done, result: outcome.notice };
    case "not_configured":
      return { ...done, notConfigured: true, result: null };
    case "invalid":
      return { ...done, descriptionInvalid: outcome.description, result: outcome.notice };
  }
}

// 点 admin.ai_apply 之后：提案换成 admin.ai_applied（表单由编辑页改）；没有提案时不变。
export function markApplied(state: AiState): AiState {
  return state.result?.kind === "proposal" ? { ...state, result: { kind: "applied" } } : state;
}

// AI 区对请求结果的处理：finish 结束提交并显示结果，reread 由编辑页重新读取 CSRF 令牌，login 回到 A01。
export interface AiMoves {
  finish: (outcome: AiOutcome) => void;
  reread: (signal: AbortSignal) => Promise<RereadOutcome>;
  login: () => void;
}

// 发请求（调用前已由 pressGenerate 检查并进入提交中）。403 后重新读取令牌，之后照 A08 的 rereadNotice 提示（取到与其他失败 common.error_retry、网络中断
// common.network_check，401 回到 A01），管理员再点一次。signal 中止之后不再更新。
export async function generateProposal(
  request: AiProposalRequest,
  csrfToken: string,
  signal: AbortSignal,
  moves: AiMoves,
  propose: typeof requestAiProposal = requestAiProposal,
): Promise<void> {
  const result = await propose(request, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = proposalStep(result);
  if (step === "login") {
    moves.login();
    return;
  }
  if (step === "reread") {
    const outcome = await moves.reread(signal);
    if (signal.aborted) {
      return;
    }
    const after = rereadNotice(outcome);
    if (after === "login") {
      moves.login();
    } else {
      moves.finish({ kind: "notice", notice: { kind: "alert", key: after === "common.network_check" ? "common.network_check" : "common.error_retry" } });
    }
    return;
  }
  moves.finish(step);
}

// 六个装饰位的标签（同装饰区）。
export const AI_SLOT_LABEL: Readonly<Record<PromoSlot, CopyKey>> = {
  ambient: "admin.slot_ambient",
  home_poster: "admin.slot_home_poster",
  list_strip: "admin.slot_list_strip",
  card_badge: "admin.slot_card_badge",
  detail_ribbon: "admin.slot_detail_ribbon",
  cart_progress: "admin.slot_cart_progress",
};

// 提案的四项文案（每项三语）及其标签，同文案区。
const AI_COPY_GROUPS = [
  { group: "title", label: "admin.copy_title" },
  { group: "subtitle", label: "admin.copy_subtitle" },
  { group: "badge", label: "admin.copy_badge" },
  { group: "strip", label: "admin.copy_strip" },
] as const satisfies readonly { group: string; label: CopyKey }[];

function isPack(id: string): id is PromoPack {
  return (PROMO_PACKS as readonly string[]).includes(id);
}

// 提案：素材包名称、配色、六个装饰位的变体与四项文案的三语（null 为空），其下 admin.ai_review_copy 与（不是只读时）admin.ai_apply。
// 提案的 ID 已经服务端按清单校验；万一不在清单常量里，照原样按纯文本显示。
function ProposalView({ proposal, readOnly, onApply }: { proposal: AiProposal; readOnly: boolean; onApply: () => void }) {
  const t = useCopy();
  const packName = (id: string) => (isPack(id) ? t(PACK_LABEL[id]) : id);
  const variantName = (slot: PromoSlot, id: string) => {
    if (slot === "ambient" && id === PROMO_AMBIENT_NONE) {
      return t("admin.variant_none");
    }
    const index = PROMO_VARIANTS[slot].indexOf(id);
    return index < 0 ? id : t("admin.variant_n", { n: index + 1 });
  };
  return (
    <div className="site-admin-promo-ai__proposal">
      <dl className="site-admin-promo-ai__items">
        <dt>{t("admin.promo_pack")}</dt>
        <dd>{packName(proposal.pack)}</dd>
        <dt>{t("admin.promo_palette")}</dt>
        <dd>{proposal.palette === PROMO_KEEP_THEME ? t("admin.palette_keep") : t("admin.palette_of", { pack: packName(proposal.palette) })}</dd>
        {PROMO_SLOTS.map((slot) => (
          <Fragment key={slot}>
            <dt>{t(AI_SLOT_LABEL[slot])}</dt>
            <dd>{variantName(slot, proposal[slot])}</dd>
          </Fragment>
        ))}
        {AI_COPY_GROUPS.map(({ group, label }) => (
          <Fragment key={group}>
            <dt>{t(label)}</dt>
            <dd className="site-admin-promo-ai__langs">
              {LANGUAGES.map((language) => (
                <span key={language} className="site-admin-promo-ai__lang">
                  <span className="acs-admin__muted">{t(LANGUAGE_LABEL[language])}</span>
                  <span lang={htmlLang(language)}>{proposal[`${group}_${language}` as const] ?? ""}</span>
                </span>
              ))}
            </dd>
          </Fragment>
        ))}
      </dl>
      <p className="site-admin-promo-ai__review">{t("admin.ai_review_copy")}</p>
      {!readOnly && (
        <button className="acs-admin__btn site-admin-promo-ai__apply" type="button" name="ai_apply" onClick={onApply}>
          {t("admin.ai_apply")}
        </button>
      )}
    </div>
  );
}

function ResultView({ result, readOnly, onApply }: { result: AiResult; readOnly: boolean; onApply: () => void }) {
  const t = useCopy();
  switch (result.kind) {
    case "proposal":
      return <ProposalView proposal={result.proposal} readOnly={readOnly} onApply={onApply} />;
    case "applied":
      return (
        <p className="site-admin-promo-edit__done" role="status">
          {t("admin.ai_applied")}
        </p>
      );
    case "alert":
      return <AdminAlert error={result.key} />;
    case "daily_limit":
      return (
        <div className="acs-admin__alert" role="alert">
          <span>{t("admin.ai_daily_limit", { count: result.limit })}</span>
        </div>
      );
    case "invalid_other":
      return (
        <div className="acs-admin__alert site-admin-promo-edit__invalid" role="alert">
          <p>{t("admin.promo_invalid")}</p>
          <ul>
            <li>{t("admin.promo_invalid_other")}</li>
          </ul>
        </div>
      );
  }
}

export interface PromotionAiViewProps {
  state: AiState;
  // 已结束：描述只读，不显示生成与填回按钮。
  readOnly: boolean;
  onDescription: (description: string) => void;
  onGenerate: () => void;
  onApply: () => void;
}

// AI 区：admin.ai_describe 与单行输入框（占位 admin.ai_placeholder，标签旁为已用字数，不合格时旁边为 admin.promo_field_invalid）、admin.ai_privacy、
// admin.ai_generate 与提交中的 admin.ai_generating、未配置时的 admin.ai_not_configured，再下为结果。描述框里按回车不提交编辑页的表单。
// 只读（已结束）时不能描述与发送，不显示 admin.ai_privacy、按钮与结果，只留描述标签、字数与只读的输入框。
export function PromotionAiView({ state, readOnly, onDescription, onGenerate, onApply }: PromotionAiViewProps) {
  const t = useCopy();
  const invalidId = useId();
  const notConfiguredId = useId();
  const { description, descriptionInvalid, notConfigured, generating, result } = state;
  return (
    <div className="site-admin-promo-ai">
      <div className="site-admin-promo-edit__field site-admin-promo-ai__describe">
        <label className="acs-admin__field">
          <span className="site-admin-promo-edit__copy-label">
            <span>{t("admin.ai_describe")}</span>
            <span className="acs-admin__muted">{describeCount(description)}</span>
          </span>
          <input
            className="acs-admin__input"
            name="ai_description"
            value={description}
            placeholder={t("admin.ai_placeholder")}
            readOnly={readOnly}
            aria-invalid={descriptionInvalid}
            aria-describedby={descriptionInvalid ? invalidId : undefined}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              onDescription(event.target.value);
            }}
            onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
              if (event.key === "Enter") {
                event.preventDefault();
              }
            }}
          />
        </label>
        {descriptionInvalid && (
          <span id={invalidId} className="site-admin-promo-edit__field-invalid">
            {t("admin.promo_field_invalid")}
          </span>
        )}
      </div>
      {!readOnly && <span className="acs-admin__muted">{t("admin.ai_privacy")}</span>}
      {!readOnly && (
        <div className="site-admin-promo-ai__actions">
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            name="ai_generate"
            disabled={notConfigured || generating}
            aria-busy={generating}
            aria-describedby={notConfigured ? notConfiguredId : undefined}
            onClick={onGenerate}
          >
            {t("admin.ai_generate")}
          </button>
          {generating && (
            <span className="acs-admin__muted" role="status">
              {t("admin.ai_generating")}
            </span>
          )}
        </div>
      )}
      {!readOnly && notConfigured && (
        <span id={notConfiguredId} className="acs-admin__muted">
          {t("admin.ai_not_configured")}
        </span>
      )}
      {!readOnly && result !== null && <ResultView result={result} readOnly={readOnly} onApply={onApply} />}
    </div>
  );
}

// AI 区用到的请求，测试以替身代替。
export interface AiApi {
  readStatus: typeof readAiStatus;
  propose: typeof requestAiProposal;
}

export const AI_API: AiApi = { readStatus: readAiStatus, propose: requestAiProposal };

export interface AdminPromotionAiProps {
  // 表单当前的值（只取规则参数）。
  form: PromotionForm;
  readOnly: boolean;
  csrfToken: string;
  // 点 admin.ai_apply：编辑页把提案填回表单（计入未保存，不保存）。
  onApply: (proposal: AiProposal) => void;
  // 点 admin.ai_generate 做过检查：rulesInvalid 时规则区在字段旁提示规则参数的问题。
  onRulesChecked: (rulesInvalid: boolean) => void;
  // 403 之后由编辑页重新读取 CSRF 令牌。
  onReread: (signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  api?: AiApi;
}

// AI 区的容器：自己保留描述、状态与结果；挂载时读一次状态。传给事件属性的处理函数都不返回 Promise。
export default function AdminPromotionAi({ form, readOnly, csrfToken, onApply, onRulesChecked, onReread, onLogin, api = AI_API }: AdminPromotionAiProps) {
  const [state, setState] = useState<AiState>(AI_IDLE);
  // 本区的请求（状态与提案）共用的中止；卸载时中止。
  const lifetime = useRef<AbortController | null>(null);
  // 编辑页每次渲染给的 onLogin 都是新函数；状态读取只在打开时做一次，所以经 ref 取最新的那个，不让它重新读取。
  const login = useRef(onLogin);
  const { readStatus, propose } = api;

  useEffect(() => {
    login.current = onLogin;
  });

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void loadAiStatus(
        controller.signal,
        {
          notConfigured: (notConfigured) => {
            setState((current) => (current.notConfigured === notConfigured ? current : { ...current, notConfigured }));
          },
          login: () => {
            login.current();
          },
        },
        readStatus,
      );
    });
  }, [readStatus]);

  return (
    <PromotionAiView
      state={state}
      readOnly={readOnly}
      onDescription={(description) => {
        setState((current) => changeDescription(current, description));
      }}
      onGenerate={() => {
        const controller = lifetime.current;
        if (controller === null) {
          return;
        }
        const press = pressGenerate(state, form, readOnly);
        if (press.kind === "ignored") {
          return;
        }
        setState(press.state);
        if (press.kind === "rejected") {
          onRulesChecked(press.rulesInvalid);
          return;
        }
        onRulesChecked(false);
        const moves: AiMoves = {
          finish: (outcome) => {
            setState((current) => finishGenerate(current, outcome));
          },
          reread: onReread,
          login: onLogin,
        };
        void generateProposal(press.request, csrfToken, controller.signal, moves, propose);
      }}
      onApply={() => {
        const { result } = state;
        if (readOnly || result === null || result.kind !== "proposal") {
          return;
        }
        onApply(result.proposal);
        setState(markApplied);
      }}
    />
  );
}
