import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";

import { adjustAdminAvailableStock } from "../api/adminStockAdjust";
import type { StockAdjustResult } from "../api/adminStockAdjust";
import { useCopy } from "../i18n/language";
import type { RereadOutcome } from "./AdminProductImages";

// A04 后台商品的当日库存调整区（docs/UX.md 0.17 A04「当日库存调整」，视觉稿 A04-images-stock 与 A04-phone-edit；HANDOFF 0.82 第（6）点）：
// 某个已有 SKU 点 admin.adjust_today 后展开，标题 admin.adjust_title（{sku} 为该 SKU 的代码）与 admin.adjust_shown（{count} 为页面当前显示的
// 当日可用库存），其下带可见标签 admin.adjust_new 的新值输入框（初值为空串）、admin.adjust_save 与 common.cancel，常显 admin.adjust_note，
// 最后为结果。admin.adjust_save 经 api/adminStockAdjust.ts（SHOP-TASK-201）立即提交，请求带页面显示的值（DESIGN 1.18「计价、优惠、积分与库存」
// 第 12 条：不等即「库存已变动」、不写入；下一次每日重置时被覆盖）；不需要 common.save，不改商品的版本号与编辑区已填的值。
// 调整区从页面取得 SKU 的 ID、代码、页面显示的当日可用库存与 CSRF 令牌；返回的值经 onAvailable 交给页面改显示，403 后经 onReread 由页面重新
// 读取列表与分类换新令牌，401 经 onLogin 回到 A01，common.cancel 经 onCancel 由页面收起。收起（卸载）时丢弃所填的新值、中止进行中的请求。

// 新值：一到四位数字（0–9,999）。
const STOCK_PATTERN = /^\d{1,4}$/;

// 调整区的提示（A04-images-stock）：调整成功（role 为 status）、库存已变动（带服务端返回的当前值）、取值不合格、网络中断与其他失败。
export type AdjustNotice =
  | { key: "admin.adjust_saved" }
  | { key: "admin.stock_changed"; count: number }
  | { key: "admin.adjust_invalid" }
  | { key: "common.network_check" }
  | { key: "common.error_retry" };

// 调整区的状态：text 为新值输入框的原文，busy 为提交进行中，notice 为最近一次提交的结果。
export interface AdjustState {
  text: string;
  busy: boolean;
  notice: AdjustNotice | null;
}

export const IDLE: AdjustState = { text: "", busy: false, notice: null };

// 新值的先查：一到四位数字（0–9,999；空串、负数、小数、带空白或多于四位都不合格）；不合格为 null。
export function adjustValue(text: string): number | null {
  return STOCK_PATTERN.test(text) ? Number(text) : null;
}

// 改新值输入框：换上原文，收起上一次的提示。
export function changeText(state: AdjustState, text: string): AdjustState {
  return { ...state, text, notice: null };
}

// 点 admin.adjust_save：记下进行中，收起上一次的提示；所填的新值不变。
export function startAdjust(state: AdjustState): AdjustState {
  return { ...state, busy: true, notice: null };
}

// 提交结束：不再进行中，显示 notice；所填的新值不变。
export function finishAdjust(state: AdjustState, notice: AdjustNotice): AdjustState {
  return { ...state, busy: false, notice };
}

// 请求结果对调整区的作用：available 为页面要改显示的当日可用库存（不改为 null）、notice 为提示；login 为回到 A01，reread 为 403 后由页面重新读取。
export type AdjustChange = { available: number | null; notice: AdjustNotice };
export type AdjustStep = AdjustChange | "login" | "reread";

// 调整的结果：ok 改显示返回的值、admin.adjust_saved；changed 改显示返回的当前值、admin.stock_changed（{count} 为该值）；invalid 为
// admin.adjust_invalid；none 回到 A01；csrf 由页面重新读取；网络中断 common.network_check，其他失败 common.error_retry。
export function adjustStep(result: StockAdjustResult): AdjustStep {
  switch (result.kind) {
    case "ok":
      return { available: result.available_stock, notice: { key: "admin.adjust_saved" } };
    case "changed":
      return { available: result.available_stock, notice: { key: "admin.stock_changed", count: result.available_stock } };
    case "invalid":
      return { available: null, notice: { key: "admin.adjust_invalid" } };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "network":
      return { available: null, notice: { key: "common.network_check" } };
    case "failed":
      return { available: null, notice: { key: "common.error_retry" } };
  }
}

// 403 之后的提示（照图片区）：重新读取取到时为 common.error_retry（请再提交一次），网络中断 common.network_check，其他失败 common.error_retry；
// 401 回到 A01。
export function rereadNotice(outcome: RereadOutcome): AdjustNotice | "login" {
  switch (outcome) {
    case "login":
      return "login";
    case "network":
      return { key: "common.network_check" };
    case "ok":
    case "failed":
      return { key: "common.error_retry" };
  }
}

// 调整区对结果的处理：available 交给页面改显示当日可用库存，finish 结束进行中并显示提示，reread 由页面重新读取列表与分类，login 回到 A01。
export interface AdjustMoves {
  available: (count: number) => void;
  finish: (notice: AdjustNotice) => void;
  reread: (signal: AbortSignal) => Promise<RereadOutcome>;
  login: () => void;
}

// 点 admin.adjust_save（调用前调整区已经 startAdjust）：先照 adjustValue 查，不通过时不发请求、显示 admin.adjust_invalid；通过时以 expected 为
// 页面显示的值（shown）、available_stock 为新值提交。401 回到 A01；403 由页面重新读取后照 rereadNotice 提示；其余先改显示返回的值再结束。
// signal 中止（收起或离开页面）之后不再更新。
export async function submitAdjust(
  variantId: number,
  shown: number,
  text: string,
  csrfToken: string,
  signal: AbortSignal,
  moves: AdjustMoves,
): Promise<void> {
  const value = adjustValue(text);
  if (value === null) {
    moves.finish({ key: "admin.adjust_invalid" });
    return;
  }
  const result = await adjustAdminAvailableStock(variantId, { expected: shown, available_stock: value }, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = adjustStep(result);
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
      moves.finish(after);
    }
    return;
  }
  if (step.available !== null) {
    moves.available(step.available);
  }
  moves.finish(step.notice);
}

export interface AdjustHandlers {
  onText: (text: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

export interface StockAdjustViewProps extends AdjustHandlers {
  sku: string;
  shown: number;
  state: AdjustState;
}

// 调整区（照 A04-images-stock 与 A04-phone-edit 的顺序）：admin.adjust_title 与 admin.adjust_shown 一行；admin.adjust_new 的输入框（展开时取得
// 焦点；在输入框里按 Enter 等同 admin.adjust_save，不提交编辑区的 common.save）、admin.adjust_save（提交中禁用并标 aria-busy）与
// common.cancel；常显的 admin.adjust_note；最后为提示（admin.adjust_saved 为 role="status"，其他为 alert）。
export function StockAdjustView({ sku, shown, state, onText, onSave, onCancel }: StockAdjustViewProps) {
  const t = useCopy();
  const titleId = useId();
  const { notice } = state;
  return (
    <div className="acs-admin__panel site-admin-adjust" role="group" aria-labelledby={titleId}>
      <div className="site-admin-adjust__head">
        <span id={titleId} className="site-admin-adjust__title">
          {t("admin.adjust_title", { sku })}
        </span>
        <span className="acs-admin__muted">{t("admin.adjust_shown", { count: shown })}</span>
      </div>
      <div className="site-admin-adjust__inputs">
        <label className="site-admin-adjust__field">
          <span>{t("admin.adjust_new")}</span>
          <input
            className="acs-admin__input"
            inputMode="numeric"
            autoFocus
            value={state.text}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              onText(event.target.value);
            }}
            onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onSave();
              }
            }}
          />
        </label>
        <button
          className="acs-admin__btn"
          type="button"
          disabled={state.busy}
          aria-busy={state.busy}
          onClick={() => {
            onSave();
          }}
        >
          {t("admin.adjust_save")}
        </button>
        <button
          className="acs-admin__btn acs-admin__btn--secondary"
          type="button"
          onClick={() => {
            onCancel();
          }}
        >
          {t("common.cancel")}
        </button>
      </div>
      <span className="acs-admin__muted">{t("admin.adjust_note")}</span>
      {notice !== null && notice.key === "admin.adjust_saved" && (
        <span className="site-admin-adjust__saved" role="status">
          {t(notice.key)}
        </span>
      )}
      {notice !== null && notice.key !== "admin.adjust_saved" && (
        <div className="acs-admin__alert" role="alert">
          <span>{notice.key === "admin.stock_changed" ? t(notice.key, { count: notice.count }) : t(notice.key)}</span>
        </div>
      )}
    </div>
  );
}

export interface AdminStockAdjustProps {
  // SKU 规格 ID 与代码。
  variantId: number;
  sku: string;
  // 页面当前显示的当日可用库存（最近一次读取、保存或调整给的值），随请求作 expected 提交。
  shown: number;
  csrfToken: string;
  onAvailable: (count: number) => void;
  onReread: (signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  onCancel: () => void;
}

// 调整区的容器：自己保留 AdjustState 与一个随卸载中止的 AbortController；页面展开时挂载、收起时卸载（所填的新值随之丢弃）。
// 传给事件属性的处理函数都不返回 Promise。
export default function AdminStockAdjust({ variantId, sku, shown, csrfToken, onAvailable, onReread, onLogin, onCancel }: AdminStockAdjustProps) {
  const [state, setState] = useState<AdjustState>(IDLE);
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
    };
  }, []);

  const moves: AdjustMoves = {
    available: onAvailable,
    finish: (notice) => {
      setState((current) => finishAdjust(current, notice));
    },
    reread: onReread,
    login: onLogin,
  };

  return (
    <StockAdjustView
      sku={sku}
      shown={shown}
      state={state}
      onText={(text) => {
        setState((current) => changeText(current, text));
      }}
      onSave={() => {
        const controller = lifetime.current;
        if (controller === null || state.busy) {
          return;
        }
        setState(startAdjust);
        void submitAdjust(variantId, shown, state.text, csrfToken, controller.signal, moves);
      }}
      onCancel={onCancel}
    />
  );
}
