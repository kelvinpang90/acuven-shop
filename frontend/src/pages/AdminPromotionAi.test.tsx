import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_PROMOTION_AI_PROPOSAL_URL } from "../api/adminPromotionAi";
import type { AiProposal, AiProposalResult, AiStatusRead } from "../api/adminPromotionAi";
import { LANGUAGE_LABEL } from "../components/AdminFrame";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import AdminPromotionAi, {
  AI_COPY_FIELDS,
  AI_IDLE,
  aiRuleProblems,
  aiRules,
  aiStatusStep,
  applyProposal,
  changeDescription,
  describeCount,
  descriptionOk,
  finishGenerate,
  generateProposal,
  isAiRuleField,
  loadAiStatus,
  markApplied,
  pressGenerate,
  PromotionAiView,
} from "./AdminPromotionAi";
import type { AiMoves, AiOutcome, AiState } from "./AdminPromotionAi";
import type { RereadOutcome } from "./AdminStoreLogo";
import { newPromotionForm, newPromotionRule } from "./promotionForm";
import type { PromotionForm, PromotionFormRule } from "./promotionForm";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function named(html: string, tag: string, name: string): Map<string, string> | undefined {
  const found = tags(html, tag).find((item) => attributes(item).get("name") === name);
  return found === undefined ? undefined : attributes(found);
}

function render(language: Language, state: Partial<AiState> = {}, readOnly = false): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <PromotionAiView state={{ ...AI_IDLE, ...state }} readOnly={readOnly} onDescription={() => undefined} onGenerate={() => undefined} onApply={() => undefined} />
    </LanguageProvider>,
  );
}

// 描述旁的 admin.promo_field_invalid：输入框以 aria-describedby 指向它。
function descriptionMarked(html: string, language: Language): boolean {
  const id = named(html, "input", "ai_description")?.get("aria-describedby");
  if (id === undefined) {
    return false;
  }
  return html.includes(`<span id="${escapeHtml(id)}" class="site-admin-promo-edit__field-invalid">${escapeHtml(COPY["admin.promo_field_invalid"][language])}</span>`);
}

// 提案：农历新年素材包与配色，各装饰位不全是第一个变体；文案有 null，英文标题带尖括号（确认按纯文本显示）。
const PROPOSAL: AiProposal = {
  pack: "cny",
  palette: "cny",
  ambient: "fall",
  home_poster: "centered",
  list_strip: "band",
  card_badge: "seal",
  detail_ribbon: "ribbon",
  cart_progress: "steps",
  title_en: "Lunar <b>Sale</b>",
  title_zh: "新年特卖",
  title_ms: null,
  subtitle_en: "Big savings",
  subtitle_zh: null,
  subtitle_ms: null,
  badge_en: "Hot",
  badge_zh: "热卖",
  badge_ms: null,
  strip_en: null,
  strip_zh: null,
  strip_ms: "Jualan",
};

// 合格的三条规则：每件直降 20%（商品 5、分类 3）、满 RM100 减 RM10、满 RM120 免运费。
function validRules(): PromotionFormRule[] {
  return [
    { key: 0, kind: "item_discount", discount_mode: "percent", discount: "20", product_ids: [5], products: [], category_ids: [3] },
    { key: 1, kind: "spend_discount", discount_mode: "amount", tiers: [{ threshold: "100.00", discount: "10.00" }] },
    { key: 2, kind: "free_shipping", threshold: "120.00" },
  ];
}

function formWith(rules: PromotionFormRule[]): PromotionForm {
  return { ...newPromotionForm(), rules };
}

// 记下请求结果交给 AI 区的处理。
function movesRecorder(outcome: RereadOutcome = "ok") {
  const record = { finished: [] as AiOutcome[], rereads: 0, logins: 0 };
  const moves: AiMoves = {
    finish: (result) => {
      record.finished.push(result);
    },
    reread: () => {
      record.rereads += 1;
      return Promise.resolve(outcome);
    },
    login: () => {
      record.logins += 1;
    },
  };
  return { record, moves };
}

const REQUEST = { description: "Lunar New Year, red and gold", rules: aiRules(formWith(validRules())) };

describe("AdminPromotionAi", () => {
  // UX 0.20 A12 线框「+---- [admin.ai_title] ----」「[admin.ai_describe] [<占位 admin.ai_placeholder>______]  <已用>/500」「[admin.ai_privacy]」「( [admin.ai_generate] )」：
  // 依次为描述标签与已用字数、单行输入框（占位 admin.ai_placeholder）、admin.ai_privacy 与 admin.ai_generate，文字按当前语言；没有结果时不显示别的。
  it.each(LANGUAGES)("shows the description, the privacy note and the generate button in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const html = render(language);
    expect(textNodes(html)).toEqual([t("admin.ai_describe"), "0/500", t("admin.ai_privacy"), t("admin.ai_generate")]);
    const input = named(html, "input", "ai_description");
    expect(input?.get("placeholder")).toBe(t("admin.ai_placeholder"));
    expect(input?.get("value")).toBe("");
    expect(input?.has("type")).toBe(false);
    expect(input?.has("readonly")).toBe(false);
    const button = named(html, "button", "ai_generate");
    expect(button?.get("type")).toBe("button");
    expect(button?.has("disabled")).toBe(false);
    expect(button?.get("aria-busy")).toBe("false");
    expect(tags(html, "textarea")).toEqual([]);
  });

  // 落实写法（1）「configured 为假时 admin.ai_generate 禁用并显示 admin.ai_not_configured」与 UX 0.20 A12 线框「未配置：( [admin.ai_generate] ) 禁用，
  // [admin.ai_not_configured]」：未配置时按钮禁用并以 aria-describedby 指向 admin.ai_not_configured；点它不响应。
  it.each(LANGUAGES)("disables the generate button when AI isn't set up in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const html = render(language, { notConfigured: true, description: "Lunar" });
    const button = named(html, "button", "ai_generate");
    expect(button?.has("disabled")).toBe(true);
    const describedBy = button?.get("aria-describedby") ?? "";
    expect(describedBy).not.toBe("");
    expect(html).toContain(`<span id="${escapeHtml(describedBy)}" class="acs-admin__muted">${escapeHtml(t("admin.ai_not_configured"))}</span>`);
    expect(textNodes(html)).toEqual([t("admin.ai_describe"), "5/500", t("admin.ai_privacy"), t("admin.ai_generate"), t("admin.ai_not_configured")]);
    expect(pressGenerate({ ...AI_IDLE, notConfigured: true, description: "Lunar" }, formWith(validRules()), false)).toEqual({ kind: "ignored" });
  });

  // 落实写法（1）「编辑区打开时读一次 readAiStatus：configured 为假时 admin.ai_generate 禁用…；401 回到 A01；网络中断与其他失败时按钮照常可用」（DESIGN 1.23 第 13 条
  // 「未配置时后台的 AI 按钮禁用并说明原因」）：读到的每种结果对应的处理；中止之后不再更新。
  it("reads the AI status once and maps each result", async () => {
    expect(aiStatusStep({ kind: "ok", configured: true })).toBe("usable");
    expect(aiStatusStep({ kind: "ok", configured: false })).toBe("not_configured");
    expect(aiStatusStep({ kind: "none" })).toBe("login");
    expect(aiStatusStep({ kind: "network" })).toBe("usable");
    expect(aiStatusStep({ kind: "failed" })).toBe("usable");

    for (const [read, expected] of [
      [{ kind: "ok", configured: false }, [true]],
      [{ kind: "ok", configured: true }, [false]],
      [{ kind: "network" }, [false]],
      [{ kind: "failed" }, [false]],
    ] as const) {
      const shown: boolean[] = [];
      const login = vi.fn();
      const signal = new AbortController().signal;
      const readStatus = vi.fn(() => Promise.resolve<AiStatusRead>(read));
      await loadAiStatus(signal, { notConfigured: (value) => shown.push(value), login }, readStatus);
      expect(readStatus).toHaveBeenCalledTimes(1);
      expect(readStatus).toHaveBeenCalledWith(signal);
      expect(shown).toEqual(expected);
      expect(login).not.toHaveBeenCalled();
    }

    const login = vi.fn();
    const shown = vi.fn();
    await loadAiStatus(new AbortController().signal, { notConfigured: shown, login }, () => Promise.resolve<AiStatusRead>({ kind: "none" }));
    expect(login).toHaveBeenCalledTimes(1);
    expect(shown).not.toHaveBeenCalled();

    const controller = new AbortController();
    const late = vi.fn();
    await loadAiStatus(controller.signal, { notConfigured: late, login: late }, () => {
      controller.abort();
      return Promise.resolve<AiStatusRead>({ kind: "ok", configured: false });
    });
    expect(late).not.toHaveBeenCalled();
  });

  // 落实写法（2）「已用字数与 1–500 的检查都用 copyLength（pyStrip 去掉首尾空白后的码点数，空白集同服务端 description.strip()）；为空或超过 500 时输入框旁显示
  // admin.promo_field_invalid、不发请求」（DESIGN 1.23 第 13 条「输入一段描述（1–500 个字符…）」）：空、只有空白、只有 U+0085 与 501 个字符不合格，不发请求、描述旁提示；
  // 只有 U+FEFF（Python 不当作空白）为 1 个字符、合格；表情按码点计 1 个。
  it.each(LANGUAGES)("checks the description before any request in %s", (language) => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    const form = formWith(validRules());
    expect(describeCount("\u0085")).toBe("0/500");
    expect(describeCount("﻿")).toBe("1/500");
    expect(describeCount("  🎉 新年  ")).toBe("4/500");
    for (const description of ["", "   ", "\u0085", "\u0085　 ", "x".repeat(501)]) {
      expect(descriptionOk(description), JSON.stringify(description)).toBe(false);
      const press = pressGenerate({ ...AI_IDLE, description }, form, false);
      expect(press.kind).toBe("rejected");
      if (press.kind !== "rejected") {
        continue;
      }
      expect(press.rulesInvalid).toBe(false);
      expect(press.state.generating).toBe(false);
      const html = render(language, press.state);
      expect(descriptionMarked(html, language)).toBe(true);
      expect(named(html, "input", "ai_description")?.get("aria-invalid")).toBe("true");
    }
    expect(render(language, { description: "x".repeat(501) })).toContain('<span class="acs-admin__muted">501/500</span>');
    for (const description of ["﻿", "x".repeat(500), "  🎉  "]) {
      expect(descriptionOk(description), JSON.stringify(description)).toBe(true);
      expect(pressGenerate({ ...AI_IDLE, description }, form, false).kind).toBe("submit");
    }
    expect(request).not.toHaveBeenCalled();

    // 改正之后提示随当前的值消失；仍不合格时保留。
    const marked: AiState = { ...AI_IDLE, description: "", descriptionInvalid: true };
    expect(changeDescription(marked, "\u0085").descriptionInvalid).toBe(true);
    expect(changeDescription(marked, "Lunar").descriptionInvalid).toBe(false);
    expect(changeDescription(AI_IDLE, "").descriptionInvalid).toBe(false);
    expect(descriptionMarked(render(language, changeDescription(marked, "Lunar")), language)).toBe(false);
  });

  // 落实写法（3）「先跑 formProblems 里规则参数的那部分（field 为 rules.N.<参数> 与 rules.N.tiers 开头的，不含范围 product_ids、category_ids 与条数 rules…）：有不合格时
  // 不发请求」（DESIGN 1.23 第 13 条「送出的内容只有：这段描述、活动的规则类别与参数」）：只看规则参数，范围为空、条数超出与名称、时间、文案不合格都不拦。
  it("checks only the rule parameters before a request", () => {
    expect(["rules.1.percent", "rules.2.tiers", "rules.2.tiers.1.amount_sen", "rules.12.threshold_sen"].map(isAiRuleField)).toEqual([true, true, true, true]);
    expect(["rules.1.product_ids", "rules.1.category_ids", "rules", "name", "ends_at", "copy.title_en", "description"].map(isAiRuleField)).toEqual([
      false,
      false,
      false,
      false,
      false,
      false,
      false,
    ]);
    const state = { ...AI_IDLE, description: "Lunar" };

    const badPercent = formWith([newPromotionRule("item_discount", 0)]);
    expect(aiRuleProblems(badPercent).map((problem) => problem.field)).toEqual(["rules.1.percent"]);
    const press = pressGenerate(state, badPercent, false);
    expect(press).toEqual({ kind: "rejected", state: { ...state, descriptionInvalid: false }, rulesInvalid: true });

    const badTier = formWith([{ key: 0, kind: "spend_discount", discount_mode: "amount", tiers: [{ threshold: "", discount: "" }] }]);
    expect(aiRuleProblems(badTier).map((problem) => problem.field)).toEqual(["rules.1.tiers.1.threshold_sen", "rules.1.tiers.1.amount_sen"]);
    expect(pressGenerate(state, badTier, false).kind).toBe("rejected");

    // 描述与规则参数都不合格时两处都提示。
    const both = pressGenerate({ ...AI_IDLE, description: " " }, badPercent, false);
    expect(both.kind === "rejected" && both.rulesInvalid && both.state.descriptionInvalid).toBe(true);

    // 范围为空、两条免运费（条数）、名称为空与超长文案都不拦。
    const scopeless: PromotionFormRule = { key: 0, kind: "item_discount", discount_mode: "percent", discount: "15", product_ids: [], products: [], category_ids: [] };
    const others: PromotionForm = {
      ...formWith([scopeless, { key: 1, kind: "free_shipping", threshold: "0" }, { key: 2, kind: "free_shipping", threshold: "50.00" }]),
      name: "",
      ends_at: "bad",
      copy: { ...newPromotionForm().copy, title_en: "x".repeat(41) },
    };
    expect(aiRuleProblems(others)).toEqual([]);
    expect(pressGenerate(state, others, false).kind).toBe("submit");
  });

  // DESIGN 1.23 第 13 条「送出的内容只有：这段描述、活动的规则类别与参数…系统不从订单、会员或收货资料取任何内容送出」与落实写法（3）「发送描述与表单当前的规则参数
  // （promotionInput 的规则，去掉商品与分类 ID）」：请求体只有 description 与 rules，规则只有类别与参数（照表单当前的值换算），不带商品与分类 ID；提交中先前的结果收起。
  it("sends only the description and the rule parameters", async () => {
    const form = formWith(validRules());
    const state: AiState = { ...AI_IDLE, description: "  Lunar New Year, red and gold ", result: { kind: "alert", key: "admin.ai_unavailable" } };
    const press = pressGenerate(state, form, false);
    expect(press.kind).toBe("submit");
    if (press.kind !== "submit") {
      return;
    }
    expect(press.state).toEqual({ ...state, generating: true, result: null });
    const expectedRules = [
      { kind: "item_discount", discount_mode: "percent", percent: 20, amount_sen: null, buy_quantity: null, get_quantity: null, get_percent: null, threshold_sen: null, tiers: [] },
      {
        kind: "spend_discount",
        discount_mode: "amount",
        percent: null,
        amount_sen: null,
        buy_quantity: null,
        get_quantity: null,
        get_percent: null,
        threshold_sen: null,
        tiers: [{ threshold_sen: 10000, amount_sen: 1000, percent: null }],
      },
      { kind: "free_shipping", discount_mode: null, percent: null, amount_sen: null, buy_quantity: null, get_quantity: null, get_percent: null, threshold_sen: 12000, tiers: [] },
    ];
    expect(press.request).toEqual({ description: "  Lunar New Year, red and gold ", rules: expectedRules });
    for (const rule of press.request.rules) {
      expect(Object.keys(rule)).not.toContain("product_ids");
      expect(Object.keys(rule)).not.toContain("category_ids");
    }

    // 经真实的请求模块发出时，请求体同样只有描述与规则参数。
    const fetchMock = vi.fn((input: string | URL, init?: RequestInit) =>
      String(input) === ADMIN_PROMOTION_AI_PROPOSAL_URL && init?.method === "POST"
        ? Promise.resolve(new Response(JSON.stringify({ proposal: PROPOSAL }), { status: 200, headers: { "Content-Type": "application/json" } }))
        : Promise.reject(new Error("unexpected request")),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { record, moves } = movesRecorder();
    await generateProposal(press.request, "csrf-1", new AbortController().signal, moves);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const sent = fetchMock.mock.calls[0]?.[1]?.body;
    const text = typeof sent === "string" ? sent : "";
    const body = JSON.parse(text) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["description", "rules"]);
    expect(body).toEqual({ description: "  Lunar New Year, red and gold ", rules: expectedRules });
    expect(text).not.toMatch(/product_ids|category_ids|products/);
    expect(record.finished).toEqual([{ kind: "proposal", proposal: PROPOSAL }]);
  });

  // 落实写法（3）「提交中显示 admin.ai_generating 并禁用按钮」与 UX 0.20 A12 线框「( [admin.ai_generate] )   提交中：[admin.ai_generating]」：提交中按钮禁用并标
  // aria-busy，旁边为 admin.ai_generating（role="status"）；再点不响应。
  it.each(LANGUAGES)("shows generating while the request runs in %s", (language) => {
    const html = render(language, { description: "Lunar", generating: true });
    const button = named(html, "button", "ai_generate");
    expect(button?.has("disabled")).toBe(true);
    expect(button?.get("aria-busy")).toBe("true");
    expect(html).toContain(`<span class="acs-admin__muted" role="status">${escapeHtml(COPY["admin.ai_generating"][language])}</span>`);
    expect(pressGenerate({ ...AI_IDLE, description: "Lunar", generating: true }, formWith(validRules()), false)).toEqual({ kind: "ignored" });
  });

  // 落实写法（3）「成功时显示提案：素材包名称（admin.pack_<ID>）、配色（theme 为 admin.palette_keep，否则 admin.palette_of）、六个装饰位（admin.slot_<装饰位> 与
  // admin.variant_n 或 admin.variant_none）与四种文案的三语（null 显示为空），其下为 admin.ai_review_copy 与 admin.ai_apply」与 UX 0.20 A12 线框「结果：<提案的素材包、
  // 配色、各装饰位变体与三语文案>」「[admin.ai_review_copy]   ( [admin.ai_apply] )」：提案按纯文本显示，文案带该语言的 lang。
  it.each(LANGUAGES)("shows the proposal as plain text in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const n = (value: number) => formatCopy(t("admin.variant_n"), { n: value });
    const lang = (value: Language) => t(LANGUAGE_LABEL[value]);
    const html = render(language, { description: "Lunar", result: { kind: "proposal", proposal: PROPOSAL } });
    const area = html.slice(html.indexOf('<div class="site-admin-promo-ai__proposal">'));
    expect(textNodes(area)).toEqual([
      t("admin.promo_pack"),
      t("admin.pack_cny"),
      t("admin.promo_palette"),
      formatCopy(t("admin.palette_of"), { pack: t("admin.pack_cny") }),
      t("admin.slot_ambient"),
      n(3),
      t("admin.slot_home_poster"),
      n(2),
      t("admin.slot_list_strip"),
      n(1),
      t("admin.slot_card_badge"),
      n(2),
      t("admin.slot_detail_ribbon"),
      n(1),
      t("admin.slot_cart_progress"),
      n(2),
      t("admin.copy_title"),
      lang("en"),
      "Lunar <b>Sale</b>",
      lang("zh"),
      "新年特卖",
      lang("ms"),
      t("admin.copy_subtitle"),
      lang("en"),
      "Big savings",
      lang("zh"),
      lang("ms"),
      t("admin.copy_badge"),
      lang("en"),
      "Hot",
      lang("zh"),
      "热卖",
      lang("ms"),
      t("admin.copy_strip"),
      lang("en"),
      lang("zh"),
      lang("ms"),
      "Jualan",
      t("admin.ai_review_copy"),
      t("admin.ai_apply"),
    ]);
    expect(html).toContain('<span lang="en">Lunar &lt;b&gt;Sale&lt;/b&gt;</span>');
    expect(html).toContain('<span lang="zh-Hans">新年特卖</span>');
    expect(html).toContain('<span lang="ms"></span>');
    expect(html).not.toContain("<b>");
    expect(named(html, "button", "ai_apply")?.get("type")).toBe("button");

    // 沿用主题配色为 admin.palette_keep，页面装饰 none 为 admin.variant_none。
    const keep = render(language, { result: { kind: "proposal", proposal: { ...PROPOSAL, pack: "raya", palette: "theme", ambient: "none" } } });
    const keepTexts = textNodes(keep);
    expect(keepTexts).toContain(t("admin.palette_keep"));
    expect(keepTexts).toContain(t("admin.variant_none"));
    expect(keepTexts).toContain(t("admin.pack_raya"));
    expect(keepTexts).not.toContain(formatCopy(t("admin.palette_of"), { pack: t("admin.pack_raya") }));
  });

  // 落实写法（3）「点 admin.ai_apply 才把提案填回表单：装饰的 pack、palette 与六个装饰位，文案的标题、副标题、角标短语与横条文字共 12 个字段（null 填为空串）；poster_mode、
  // 替代文字、规则、时间与开关不变；填回后显示 admin.ai_applied」（DESIGN 1.23 第 13 条「AI 的提案只含装饰 ID 与文案，没有改动规则、时间、开关或金额的字段」）。
  it.each(LANGUAGES)("fills in only the decoration and the twelve copy fields in %s", (language) => {
    const form: PromotionForm = {
      ...formWith(validRules()),
      name: "Lunar",
      starts_at: "2027-01-20T00:00",
      ends_at: "2027-02-12T23:59",
      coupon_combinable: true,
      points_combinable: true,
      decoration: { ...newPromotionForm().decoration, poster_mode: "image" },
      copy: { ...newPromotionForm().copy, title_ms: "Lama", strip_en: "Old strip", poster_alt_en: "Banner", poster_alt_zh: "横幅" },
    };
    const applied = applyProposal(form, PROPOSAL);
    expect(applied.decoration).toEqual({
      pack: "cny",
      palette: "cny",
      ambient: "fall",
      home_poster: "centered",
      list_strip: "band",
      card_badge: "seal",
      detail_ribbon: "ribbon",
      cart_progress: "steps",
      poster_mode: "image",
    });
    expect(AI_COPY_FIELDS).toHaveLength(12);
    expect(applied.copy).toEqual({
      title_en: "Lunar <b>Sale</b>",
      title_zh: "新年特卖",
      title_ms: "",
      subtitle_en: "Big savings",
      subtitle_zh: "",
      subtitle_ms: "",
      badge_en: "Hot",
      badge_zh: "热卖",
      badge_ms: "",
      strip_en: "",
      strip_zh: "",
      strip_ms: "Jualan",
      poster_alt_en: "Banner",
      poster_alt_zh: "横幅",
      poster_alt_ms: "",
    });
    expect({ ...applied, decoration: form.decoration, copy: form.copy }).toEqual(form);
    expect(applied.rules).toBe(form.rules);

    const done = markApplied({ ...AI_IDLE, result: { kind: "proposal", proposal: PROPOSAL } });
    expect(done.result).toEqual({ kind: "applied" });
    expect(markApplied(AI_IDLE)).toBe(AI_IDLE);
    const html = render(language, done);
    expect(html).toContain(`<p class="site-admin-promo-edit__done" role="status">${escapeHtml(COPY["admin.ai_applied"][language])}</p>`);
    expect(html).not.toContain('name="ai_apply"');
  });

  // 落实写法（4）「result_invalid 为 admin.ai_invalid；unavailable 为 admin.ai_unavailable；daily_limit 为 admin.ai_daily_limit（count 为响应的 limit）；not_configured 为
  // admin.ai_not_configured 并禁用按钮；…网络中断 common.network_check，其他失败 common.error_retry」与 UX 0.20 A12 线框「失败：[admin.ai_invalid] / [admin.ai_unavailable] /
  // [admin.ai_daily_limit]」：每种结果在 AI 区的提示；提交结束、按钮恢复（未配置时禁用）。
  it.each(LANGUAGES)("shows each failure in the AI section in %s", async (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const alert = (text: string) => `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(text)}</span></div>`;
    const generating: AiState = { ...AI_IDLE, description: "Lunar", generating: true };
    for (const [result, text] of [
      [{ kind: "result_invalid" }, t("admin.ai_invalid")],
      [{ kind: "unavailable" }, t("admin.ai_unavailable")],
      [{ kind: "daily_limit", limit: 7 }, formatCopy(t("admin.ai_daily_limit"), { count: 7 })],
      [{ kind: "daily_limit", limit: 120 }, formatCopy(t("admin.ai_daily_limit"), { count: 120 })],
      [{ kind: "network" }, t("common.network_check")],
      [{ kind: "failed" }, t("common.error_retry")],
    ] as const) {
      const { record, moves } = movesRecorder();
      await generateProposal(REQUEST, "csrf-1", new AbortController().signal, moves, () => Promise.resolve<AiProposalResult>(result));
      expect(record.finished).toHaveLength(1);
      const state = finishGenerate(generating, record.finished[0] as AiOutcome);
      expect(state.generating).toBe(false);
      const html = render(language, state);
      expect(html, result.kind).toContain(alert(text));
      expect(named(html, "button", "ai_generate")?.has("disabled")).toBe(false);
    }
    expect(formatCopy(t("admin.ai_daily_limit"), { count: 120 })).toContain("120");

    const { record, moves } = movesRecorder();
    await generateProposal(REQUEST, "csrf-1", new AbortController().signal, moves, () => Promise.resolve<AiProposalResult>({ kind: "not_configured" }));
    const off = finishGenerate(generating, record.finished[0] as AiOutcome);
    expect(off).toEqual({ ...generating, generating: false, notConfigured: true, result: null });
    const html = render(language, off);
    expect(named(html, "button", "ai_generate")?.has("disabled")).toBe(true);
    expect(html).toContain(escapeHtml(t("admin.ai_not_configured")));

    // 已不在提交中时结果不覆盖。
    expect(finishGenerate(AI_IDLE, { kind: "not_configured" })).toBe(AI_IDLE);
  });

  // 落实写法（4）「invalid 时问题只有 description 的在描述旁显示 admin.promo_field_invalid，另有其他问题（规则参数，field 以 rules 开头）时在 AI 区显示 admin.promo_invalid
  // 与 admin.promo_invalid_other，同时有 description 问题的描述旁照样显示」。
  it.each(LANGUAGES)("marks the description or lists the other problems after invalid in %s", async (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const generating: AiState = { ...AI_IDLE, description: "Lunar", generating: true };
    const outcomeOf = async (fields: string[]) => {
      const { record, moves } = movesRecorder();
      const problems = fields.map((field) => ({ code: "invalid", field }));
      await generateProposal(REQUEST, "csrf-1", new AbortController().signal, moves, () => Promise.resolve<AiProposalResult>({ kind: "invalid", problems }));
      return finishGenerate(generating, record.finished[0] as AiOutcome);
    };
    const other = `<div class="acs-admin__alert site-admin-promo-edit__invalid" role="alert"><p>${escapeHtml(t("admin.promo_invalid"))}</p><ul><li>${escapeHtml(
      t("admin.promo_invalid_other"),
    )}</li></ul></div>`;

    const only = render(language, await outcomeOf(["description"]));
    expect(descriptionMarked(only, language)).toBe(true);
    expect(only).not.toContain(escapeHtml(t("admin.promo_invalid_other")));

    const rules = render(language, await outcomeOf(["rules.1.percent", "rules.2.tiers.1.amount_sen"]));
    expect(descriptionMarked(rules, language)).toBe(false);
    expect(rules).toContain(other);

    const both = render(language, await outcomeOf(["description", "rules.1"]));
    expect(descriptionMarked(both, language)).toBe(true);
    expect(both).toContain(other);

    // 没有可标的问题时为 common.error_retry。
    expect(render(language, await outcomeOf([]))).toContain(escapeHtml(t("common.error_retry")));
  });

  // 落实写法（4）「csrf 显示 common.error_retry 并重新读取 CSRF 令牌；401 回到 A01」：403 后由编辑页重新读取令牌，之后照 A08 的 rereadNotice（取到与其他失败
  // common.error_retry、网络中断 common.network_check、401 回到 A01）；401 直接回到 A01；中止之后不再更新。
  it("reads a new CSRF token after csrf and returns to A01 after 401", async () => {
    for (const [outcome, key] of [
      ["ok", "common.error_retry"],
      ["failed", "common.error_retry"],
      ["network", "common.network_check"],
    ] as const) {
      const { record, moves } = movesRecorder(outcome);
      const propose = vi.fn(() => Promise.resolve<AiProposalResult>({ kind: "csrf" }));
      const signal = new AbortController().signal;
      await generateProposal(REQUEST, "csrf-1", signal, moves, propose);
      expect(propose).toHaveBeenCalledWith(REQUEST, "csrf-1", signal);
      expect(record.rereads).toBe(1);
      expect(record.finished).toEqual([{ kind: "notice", notice: { kind: "alert", key } }]);
      expect(record.logins).toBe(0);
    }
    const relogin = movesRecorder("login");
    await generateProposal(REQUEST, "csrf-1", new AbortController().signal, relogin.moves, () => Promise.resolve<AiProposalResult>({ kind: "csrf" }));
    expect(relogin.record).toEqual({ finished: [], rereads: 1, logins: 1 });

    const none = movesRecorder();
    await generateProposal(REQUEST, "csrf-1", new AbortController().signal, none.moves, () => Promise.resolve<AiProposalResult>({ kind: "none" }));
    expect(none.record).toEqual({ finished: [], rereads: 0, logins: 1 });

    const controller = new AbortController();
    const aborted = movesRecorder();
    await generateProposal(REQUEST, "csrf-1", controller.signal, aborted.moves, () => {
      controller.abort();
      return Promise.resolve<AiProposalResult>({ kind: "ok", proposal: PROPOSAL });
    });
    expect(aborted.record).toEqual({ finished: [], rereads: 0, logins: 0 });
  });

  // UX 0.20 A12 说明「AI」「进行中的活动也可用 AI 改装饰与文案；已结束的不可用」与落实写法（5）「已结束的活动不显示生成按钮（区块只读）」：只读时描述只读，不显示
  // admin.ai_generate、admin.ai_not_configured 与 admin.ai_apply，点生成不响应；不能描述与发送，也不显示 admin.ai_privacy 与结果。
  it.each(LANGUAGES)("shows a read-only section without buttons when ended in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const html = render(language, { notConfigured: true }, true);
    expect(textNodes(html)).toEqual([t("admin.ai_describe"), "0/500"]);
    expect(named(html, "input", "ai_description")?.has("readonly")).toBe(true);
    expect(tags(html, "button")).toEqual([]);
    const proposal = render(language, { result: { kind: "proposal", proposal: PROPOSAL } }, true);
    expect(named(proposal, "button", "ai_apply")).toBeUndefined();
    expect(textNodes(proposal)).toEqual([t("admin.ai_describe"), "0/500"]);
    expect(pressGenerate({ ...AI_IDLE, description: "Lunar" }, formWith(validRules()), true)).toEqual({ kind: "ignored" });
  });

  // 落实写法（1）「编辑区打开时读一次 readAiStatus」与（2）「描述只在页面内存里」：首次渲染（服务端渲染不跑 effect）不发请求，与只给初始状态的区块相同。
  it("renders the container without any request", () => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const language of LANGUAGES) {
      const html = renderToStaticMarkup(
        <LanguageProvider storage={storage(language)}>
          <AdminPromotionAi
            form={formWith(validRules())}
            readOnly={false}
            csrfToken="csrf-1"
            onApply={() => undefined}
            onRulesChecked={() => undefined}
            onReread={() => Promise.resolve("failed")}
            onLogin={() => undefined}
          />
        </LanguageProvider>,
      );
      expect(html).toBe(render(language));
    }
    expect(request).not.toHaveBeenCalled();
  });
});
