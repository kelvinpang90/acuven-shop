import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import { fetchPromotion, promotionUrl } from "./api/promotion";
import type { PromotionDisplay, PromotionResponse } from "./api/promotion";
import type { Language } from "./i18n/copy";
import { useLanguage } from "./i18n/language";
import { countdownText } from "./promoText";

// 前台各页共享的节日促销活动（UX 0.20「1. 全局框架」「节日促销活动」，DESIGN 1.23「节日促销活动」第 10、12 条，HANDOFF 0.97）。
// 打开页面与切换语言时以 GET /api/promotion 读取一次；读取失败按没有活动处理，不显示提示、不自动重试。
// 切换语言重新读取期间继续显示原来的活动（状态仍为 ready），新结果到达后再替换；被中止的读取不改任何状态。
//
// 倒计时以服务端时间为准：剩余时间 = ends_at − server_time − 收到响应后本机经过的时间（以 performance.now 计，不读设备时钟的绝对时间）。
// 剩余到 0 时立即按没有活动处理（promotion 置为 null，generation 加一），再重新读取一次；读取返回的活动剩余时间不大于 0 时
// 按没有活动处理、不再读取。等待超过 setTimeout 上限时分段等待；ends_at 为 null 时不计时。
// 读取状态放在不依赖 React 的读取对象里（createPromotionStore），组件经 useSyncExternalStore 订阅。

// setTimeout 的等待上限（毫秒）；更长的等待分段进行。
export const MAX_TIMEOUT_MS = 2_147_483_647;

const SECOND_MS = 1000;

// 本机单调时钟（毫秒）：只用两次读数之差，不读设备时钟的绝对时间。
export type MonotonicClock = () => number;

const monotonicClock: MonotonicClock = () => performance.now();

// 读一次活动；测试传入替身。
export type PromotionFetcher = (url: string, signal: AbortSignal) => Promise<PromotionResponse>;

export type PromotionStatus = "loading" | "ready";

export interface PromotionValue {
  // loading 为第一次读取完成之前；之后一直是 ready（切换语言重新读取期间也是）。
  readonly status: PromotionStatus;
  // 正在显示的活动；没有活动、读取失败或已到期时为 null。
  readonly promotion: PromotionDisplay | null;
  // 每次读取完成（成功或失败，不含被中止的）与每次到期时加一，供购物车与结账在活动变化时重新计价。
  readonly generation: number;
}

export interface PromotionState extends PromotionValue {
  // 活动结束的时刻，以 clock 的读数表示；没有活动或不计时（ends_at 为 null）时为 null。
  readonly deadline: number | null;
}

const LOADING: PromotionState = { status: "loading", promotion: null, generation: 0, deadline: null };
const NONE: PromotionState = { status: "ready", promotion: null, generation: 0, deadline: null };

// 活动结束的时刻：收到响应时的读数加 ends_at 与 server_time 之差。ends_at 为 null 或时间读不出时为 null（不计时）。
export function promotionDeadline(response: PromotionResponse, receivedAt: number): number | null {
  const endsAt = response.promotion?.ends_at ?? null;
  if (endsAt === null) {
    return null;
  }
  const remaining = Date.parse(endsAt) - Date.parse(response.server_time);
  return Number.isFinite(remaining) ? receivedAt + remaining : null;
}

// 一次读取的结果换成要显示的状态（generation 由调用方给）：剩余时间不大于 0 的活动按没有活动处理。
function settledState(response: PromotionResponse | null, receivedAt: number, generation: number): PromotionState {
  const promotion = response?.promotion ?? null;
  if (response === null || promotion === null) {
    return { ...NONE, generation };
  }
  const deadline = promotionDeadline(response, receivedAt);
  if (deadline !== null && deadline - receivedAt <= 0) {
    return { ...NONE, generation };
  }
  return { status: "ready", promotion, generation, deadline };
}

export interface PromotionStoreOptions {
  fetcher: PromotionFetcher;
  clock: MonotonicClock;
  // 一次读取的结果：给出时一开始就是 ready 并用它计时，language 的第一次 start 不读取。
  initial: PromotionResponse | null;
  // initial 所属的语言。
  language: Language;
}

export interface PromotionStore {
  // 计时所用的时钟，倒计时文字与之共用。
  clock: MonotonicClock;
  current: () => PromotionState;
  subscribe: (listener: () => void) => () => void;
  // 开始按该语言显示：当前结果不属于该语言（或已到期）时读取一次，并为正在显示的活动计时。
  // 返回的函数停止：中止未完成的读取（它的结果不再更新状态）并停掉计时。
  start: (language: Language) => () => void;
}

export function createPromotionStore({ fetcher, clock, initial, language }: PromotionStoreOptions): PromotionStore {
  const listeners = new Set<() => void>();
  let state = initial === null ? LOADING : settledState(initial, clock(), 0);
  // 当前结果所属的语言；null 即还没有可用的结果（尚未读取、读取被中止或活动已到期）。
  let loaded: Language | null = initial === null ? null : language;
  // 正在显示的语言；停止后为 null。
  let active: Language | null = null;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const publish = (next: PromotionState) => {
    state = next;
    for (const listener of listeners) {
      listener();
    }
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const read = (target: Language) => {
    controller?.abort();
    const current = new AbortController();
    controller = current;
    const finish = (response: PromotionResponse | null) => {
      if (current.signal.aborted) {
        return;
      }
      controller = null;
      loaded = target;
      publish(settledState(response, clock(), state.generation + 1));
      arm();
    };
    void fetcher(promotionUrl(target), current.signal).then(finish, () => {
      finish(null);
    });
  };

  // 剩余到 0：立即去掉活动，再重新读取一次（已有读取在进行时等它的结果）。
  const expire = () => {
    clearTimer();
    loaded = null;
    publish({ ...NONE, generation: state.generation + 1 });
    if (active !== null && controller === null) {
      read(active);
    }
  };

  // 为正在显示的活动计时；超过 setTimeout 上限时先等上限，到点后按时钟重新计算剩余时间。
  function arm() {
    clearTimer();
    if (active === null || state.promotion === null || state.deadline === null) {
      return;
    }
    const remaining = state.deadline - clock();
    if (remaining <= 0) {
      expire();
      return;
    }
    timer = setTimeout(arm, Math.min(remaining, MAX_TIMEOUT_MS));
  }

  return {
    clock,
    current: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    start: (target) => {
      active = target;
      if (loaded !== target) {
        read(target);
      }
      arm();
      return () => {
        active = null;
        controller?.abort();
        controller = null;
        clearTimer();
      };
    },
  };
}

interface PromotionContextValue {
  state: PromotionState;
  clock: MonotonicClock;
}

// Provider 之外（后台页与单独渲染的组件）：ready、没有活动。
const PromotionContext = createContext<PromotionContextValue>({ state: NONE, clock: monotonicClock });

interface PromotionProviderProps {
  children: ReactNode;
  // 一次读取的结果（页面测试与前台预览用）：给出时首次渲染即为 ready 并用它计时，挂载后不读取。
  initial?: PromotionResponse | undefined;
  // 测试传入替身；不传时用 api/promotion.ts 的 fetchPromotion 与 performance.now。
  fetcher?: PromotionFetcher | undefined;
  clock?: MonotonicClock | undefined;
}

export function PromotionProvider({ children, initial, fetcher, clock }: PromotionProviderProps) {
  const { language } = useLanguage();
  const [store] = useState(() =>
    createPromotionStore({ fetcher: fetcher ?? fetchPromotion, clock: clock ?? monotonicClock, initial: initial ?? null, language }),
  );
  const state = useSyncExternalStore(store.subscribe, store.current, store.current);

  useEffect(() => store.start(language), [store, language]);

  const value = useMemo(() => ({ state, clock: store.clock }), [state, store]);
  return <PromotionContext.Provider value={value}>{children}</PromotionContext.Provider>;
}

// 读取状态、正在显示的活动与 generation；Provider 之外为 ready、promotion 为 null。
export function usePromotion(): PromotionValue {
  return useContext(PromotionContext).state;
}

// 每当剩余时间跨过一个整秒就调用 onTick（倒计时文字按整秒向下取整）；剩余不足一秒（文字已是 0）后停止。返回停止函数。
export function watchCountdown(deadline: number, clock: MonotonicClock, onTick: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const schedule = () => {
    const remaining = deadline - clock();
    if (remaining < SECOND_MS) {
      timer = null;
      return;
    }
    timer = setTimeout(() => {
      onTick();
      schedule();
    }, (remaining % SECOND_MS) + 1);
  };
  schedule();
  return () => {
    if (timer !== null) {
      clearTimeout(timer);
    }
  };
}

// 活动倒计时：只渲染 promo.ends_in 的文字（promoText.ts 的 countdownText），自己每秒更新，不设 aria-live。
// 没有活动或不计时（ends_at 为 null）时什么都不渲染。
export function PromotionCountdown() {
  const { language } = useLanguage();
  const { state, clock } = useContext(PromotionContext);
  const [, setTick] = useState(0);
  const deadline = state.promotion === null ? null : state.deadline;

  useEffect(() => {
    if (deadline === null) {
      return;
    }
    return watchCountdown(deadline, clock, () => {
      setTick((tick) => tick + 1);
    });
  }, [deadline, clock]);

  return deadline === null ? null : countdownText(language, deadline - clock());
}
