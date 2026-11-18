import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PromotionError } from "./api/promotion";
import type { PromotionDisplay, PromotionResponse } from "./api/promotion";
import type { Language } from "./i18n/copy";
import { LanguageProvider } from "./i18n/language";
import { countdownText } from "./promoText";
import {
  createPromotionStore,
  MAX_TIMEOUT_MS,
  PromotionCountdown,
  promotionDeadline,
  PromotionProvider,
  usePromotion,
  watchCountdown,
} from "./promotion";
import type { PromotionStoreOptions } from "./promotion";

// 读取函数与本机时钟（performance.now）都以替身代替；计时用 vitest 的假计时器（连同 Date，以便改动设备时钟），
// performance 不交给假计时器，本机经过的时间只由测试的替身时钟决定。
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
  vi.setSystemTime(new Date("2026-11-14T04:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

const SERVER_TIME = "2026-11-14T04:00:00Z";
const HOUR_MS = 3_600_000;

function display(overrides: Partial<PromotionDisplay> = {}): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Deepavali Sale",
    subtitle: null,
    badge: null,
    strip: null,
    pack: "deepavali",
    palette: "deepavali",
    ambient: "garland",
    home_poster: "split",
    list_strip: "band",
    card_badge: "tag",
    detail_ribbon: "ribbon",
    cart_progress: "bar",
    poster_mode: "template",
    poster_desktop: null,
    poster_phone: null,
    poster_alt: null,
    // 比服务端时间晚一小时结束。
    ends_at: "2026-11-14T05:00:00Z",
    coupon_combinable: true,
    points_combinable: true,
    rules: [],
    ...overrides,
  };
}

function response(promotion: PromotionDisplay | null, serverTime: string = SERVER_TIME): PromotionResponse {
  return { promotion, server_time: serverTime };
}

const DEEPAVALI = display();
const CHRISTMAS = display({ promotion_id: 8, title: "Christmas Sale", pack: "christmas", palette: "theme", ends_at: "2026-11-14T07:00:00Z" });

interface FetchCall {
  url: string;
  signal: AbortSignal;
  resolve: (value: PromotionResponse) => void;
  reject: (reason: unknown) => void;
}

// 读取的替身：每次读取记下地址与中止信号，由测试决定何时、以什么结果完成。本机时钟从 50 000 开始，advance 同时推进它与假计时器。
// options 为前台预览的两个选项（url 与 holdAtZero），不传即两个都不传。
function setup(
  initial: PromotionResponse | null = null,
  language: Language = "en",
  options: Pick<PromotionStoreOptions, "url" | "holdAtZero"> = {},
) {
  let now = 50_000;
  const calls: FetchCall[] = [];
  const fetcher = vi.fn(
    (url: string, signal: AbortSignal) =>
      new Promise<PromotionResponse>((resolve, reject) => {
        calls.push({ url, signal, resolve, reject });
      }),
  );
  const store = createPromotionStore({ fetcher, clock: () => now, initial, language, ...options });
  const call = (index: number): FetchCall => {
    const found = calls[index];
    if (found === undefined) {
      throw new Error(`no fetch call ${String(index)}`);
    }
    return found;
  };
  const advance = (ms: number) => {
    now += ms;
    vi.advanceTimersByTime(ms);
  };
  return { store, fetcher, call, advance, now: () => now };
}

// 让读取替身完成后的回调跑完。
async function flush(): Promise<void> {
  for (let i = 0; i < 3; i += 1) {
    await Promise.resolve();
  }
}

describe("reading the promotion", () => {
  // HANDOFF 0.97「打开页面…时以 fetchPromotion 读取一次」：开始时按当前语言读取一次，完成前为 loading，成功后为 ready 并带活动，generation 加一。
  it("reads once in the current language and shows the result", async () => {
    const { store, fetcher, call } = setup();
    store.start("en");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(call(0).url).toBe("/api/promotion?lang=en");
    expect(store.current()).toMatchObject({ status: "loading", promotion: null, generation: 0 });
    call(0).resolve(response(DEEPAVALI));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 1 });
  });

  // 同一写法「读取失败按没有活动处理、不显示提示、不自动重试」：非 2xx 与网络错误都为 ready、没有活动，generation 加一，之后不再读取。
  it.each([
    ["503", new PromotionError(503)],
    ["network error", new TypeError("Failed to fetch")],
  ])("treats a failed read (%s) as no promotion without retrying", async (_name, error) => {
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).reject(error);
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: null, generation: 1 });
    advance(HOUR_MS * 24);
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  // DESIGN 1.23 第 10 条「没有生效活动时这些字段为空，前台不显示任何活动内容」：promotion 为 null 的回答即没有活动。
  it("shows nothing when the server has no promotion", async () => {
    const { store, call } = setup();
    store.start("ms");
    call(0).resolve(response(null));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: null, generation: 1 });
  });

  // HANDOFF 0.97「切换语言重新读取期间继续显示原来的活动（状态仍为 ready），新结果到达后再替换」与验收「语言变化时中止未完成的读取」。
  it("rereads on a language change and keeps the old promotion until the new one arrives", async () => {
    const { store, fetcher, call } = setup();
    const stop = store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    stop();
    store.start("zh");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(call(1).url).toBe("/api/promotion?lang=zh");
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 1 });
    const translated = display({ title: "屠妖节特卖" });
    call(1).resolve(response(translated));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: translated, generation: 2 });
  });

  // 验收「离开页面或语言变化时中止未完成的读取，旧读取的结果不再更新」与 HANDOFF 0.97「被中止的读取不改任何状态」：
  // 停止后旧读取的信号已中止，它之后到达的结果（成功或失败）都不改状态，generation 不变；新语言的读取照常替换。
  it("ignores an aborted read", async () => {
    const { store, call } = setup();
    const stop = store.start("en");
    stop();
    expect(call(0).signal.aborted).toBe(true);
    call(0).resolve(response(DEEPAVALI));
    await flush();
    expect(store.current()).toMatchObject({ status: "loading", promotion: null, generation: 0 });

    store.start("zh");
    store.start("ms");
    expect(call(1).signal.aborted).toBe(true);
    call(1).reject(new DOMException("The operation was aborted.", "AbortError"));
    await flush();
    expect(store.current()).toMatchObject({ status: "loading", generation: 0 });
    call(2).resolve(response(CHRISTMAS));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: CHRISTMAS, generation: 1 });
  });

  // 同一写法「打开页面…时读取一次」：同一语言下停止后再开始（如 StrictMode 的重新挂载），已有的结果不再读取。
  it("does not reread for the same language once loaded", async () => {
    const { store, fetcher, call } = setup();
    const stop = store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    stop();
    store.start("en");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  // HANDOFF 0.97「每次读取完成（成功或失败，不含被中止的）就加一的 generation」与到 0 时「generation 加一」：依次数清每一步。
  it("counts completed reads and expiries in generation", async () => {
    const { store, call, advance } = setup();
    let stop = store.start("en");
    expect(store.current().generation).toBe(0);
    call(0).resolve(response(DEEPAVALI));
    await flush();
    expect(store.current().generation).toBe(1);
    stop();
    stop = store.start("zh");
    stop();
    call(1).resolve(response(DEEPAVALI));
    await flush();
    expect(store.current().generation).toBe(1);
    store.start("ms");
    call(2).reject(new PromotionError(500));
    await flush();
    expect(store.current().generation).toBe(2);
    store.start("en");
    call(3).resolve(response(CHRISTMAS));
    await flush();
    advance(3 * HOUR_MS);
    expect(store.current().generation).toBe(4);
    call(4).resolve(response(null));
    await flush();
    expect(store.current().generation).toBe(5);
  });

  // 订阅者在状态改变时收到通知，取消订阅后不再收到。
  it("notifies subscribers", async () => {
    const { store, call } = setup();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    store.start("zh");
    call(1).resolve(response(null));
    await flush();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("countdown by server time", () => {
  // HANDOFF 0.97「剩余时间＝ends_at 减 server_time，再减去收到响应后本机经过的时间」：与收到响应时的本机读数相加，与设备时钟无关。
  it("computes the deadline from ends_at and server_time only", () => {
    expect(promotionDeadline(response(DEEPAVALI), 1_000)).toBe(1_000 + HOUR_MS);
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    expect(promotionDeadline(response(DEEPAVALI), 1_000)).toBe(1_000 + HOUR_MS);
    vi.setSystemTime(new Date("2001-01-01T00:00:00Z"));
    expect(promotionDeadline(response(DEEPAVALI), 1_000)).toBe(1_000 + HOUR_MS);
    expect(promotionDeadline(response(display({ ends_at: null })), 1_000)).toBeNull();
    expect(promotionDeadline(response(null), 1_000)).toBeNull();
  });

  // 验收「剩余时间照落实写法计算，与访客设备时钟的绝对值无关」：服务端时间比设备时钟早一天、读取后设备时钟又被调快一天，
  // 活动仍在本机经过一小时（ends_at − server_time）时才到期。
  it("ignores changes to the device clock", async () => {
    vi.setSystemTime(new Date("2026-11-15T04:00:00Z"));
    const { store, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    vi.setSystemTime(new Date("2026-11-16T09:00:00Z"));
    advance(HOUR_MS - 1);
    expect(store.current().promotion).toBe(DEEPAVALI);
    vi.setSystemTime(new Date("2026-11-01T00:00:00Z"));
    advance(1);
    expect(store.current().promotion).toBeNull();
  });

  // UX 0.20「到 0 时页面重新读取活动数据」与 HANDOFF 0.97「剩余到 0 时立即按没有活动处理（promotion 置为 null，generation 加一），再重新读取一次」：
  // 到 0 的那一刻活动即去掉（不等新读取），并只发一次新读取；新读取返回另一个活动时随之显示并为它计时。
  it("drops the promotion at zero and rereads once", async () => {
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    advance(HOUR_MS - 1);
    expect(store.current()).toMatchObject({ promotion: DEEPAVALI, generation: 1 });
    advance(1);
    expect(store.current()).toMatchObject({ status: "ready", promotion: null, generation: 2 });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(call(1).url).toBe("/api/promotion?lang=en");
    advance(HOUR_MS);
    expect(fetcher).toHaveBeenCalledTimes(2);
    call(1).resolve(response(CHRISTMAS, "2026-11-14T05:00:00Z"));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: CHRISTMAS, generation: 3 });
    advance(2 * HOUR_MS - 1);
    expect(store.current().promotion).toBe(CHRISTMAS);
    advance(1);
    expect(store.current().promotion).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  // HANDOFF 0.97「读取返回的活动剩余时间不大于 0 时按没有活动处理、不再读取」：ends_at 等于或早于 server_time 的回答即没有活动，之后不再读取。
  it.each(["2026-11-14T04:00:00Z", "2026-11-14T03:00:00Z"])("treats a promotion ending at %s as over without rereading", async (endsAt) => {
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(display({ ends_at: endsAt })));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: null, generation: 1 });
    advance(HOUR_MS);
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  // 同一条：到 0 后的重新读取若仍返回已到期的活动，按没有活动处理，不再读取（只重新读取一次）。
  it("stops after one reread that is already over", async () => {
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    advance(HOUR_MS);
    call(1).resolve(response(DEEPAVALI, "2026-11-14T05:00:00Z"));
    await flush();
    expect(store.current()).toMatchObject({ promotion: null, generation: 3 });
    advance(HOUR_MS);
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  // HANDOFF 0.97「ends_at 为 null 时不计时」：没有计时器，过多久都不到期、不重新读取。
  it("does not time a promotion without ends_at", async () => {
    const open = display({ ends_at: null });
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(open));
    await flush();
    expect(vi.getTimerCount()).toBe(0);
    advance(365 * 24 * HOUR_MS);
    expect(store.current().promotion).toBe(open);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  // HANDOFF 0.97「等待超过 setTimeout 上限（2147483647 毫秒）时分段等待」与验收「长于 setTimeout 上限的等待不会立即触发」：
  // 剩余比上限多 10 秒时，开始后、上限到点时都不到期，再过 10 秒才到期；setTimeout 从不收到超过上限的等待。
  it("waits in segments beyond the setTimeout limit", async () => {
    const remaining = MAX_TIMEOUT_MS + 10_000;
    const endsAt = new Date(Date.parse(SERVER_TIME) + remaining).toISOString();
    const timeout = vi.spyOn(globalThis, "setTimeout");
    const { store, fetcher, call, advance } = setup();
    store.start("en");
    call(0).resolve(response(display({ ends_at: endsAt })));
    await flush();
    advance(1);
    expect(store.current().promotion).not.toBeNull();
    advance(MAX_TIMEOUT_MS - 1);
    expect(store.current().promotion).not.toBeNull();
    advance(9_999);
    expect(store.current().promotion).not.toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    advance(1);
    expect(store.current().promotion).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const [, delay] of timeout.mock.calls) {
      expect(delay ?? 0).toBeLessThanOrEqual(MAX_TIMEOUT_MS);
    }
    timeout.mockRestore();
  });

  // 验收「离开页面…时中止」：停止后计时也停掉，到点不去掉活动、不重新读取。
  it("stops timing when stopped", async () => {
    const { store, fetcher, call, advance } = setup();
    const stop = store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    stop();
    advance(2 * HOUR_MS);
    expect(store.current().promotion).toBe(DEEPAVALI);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("initial result", () => {
  // HANDOFF 0.97「传入时一开始就是 ready 并用它计时、挂载后不读取」：开始（含停止后再开始）都不读取。
  it("starts ready without reading", () => {
    const { store, fetcher } = setup(response(DEEPAVALI));
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 0 });
    const stop = store.start("en");
    stop();
    store.start("en");
    expect(fetcher).not.toHaveBeenCalled();
  });

  // 同一写法「之后到 0 与切换语言时的行为与不传时相同」：到 0 时去掉活动并读取一次；切换语言时读取。
  it("rereads at zero and on a language change like a loaded result", async () => {
    const atZero = setup(response(DEEPAVALI));
    atZero.store.start("en");
    atZero.advance(HOUR_MS);
    expect(atZero.store.current()).toMatchObject({ promotion: null, generation: 1 });
    expect(atZero.fetcher).toHaveBeenCalledTimes(1);

    const relabelled = setup(response(DEEPAVALI));
    relabelled.store.start("zh");
    expect(relabelled.fetcher).toHaveBeenCalledTimes(1);
    expect(relabelled.call(0).url).toBe("/api/promotion?lang=zh");
    expect(relabelled.store.current().promotion).toBe(DEEPAVALI);
    await flush();
  });

  // 验收「传入 initial 时首次渲染即显示该活动、不发读取」：服务端渲染时 usePromotion 即为 ready 与该活动，读取替身没有被调用。
  it("renders the promotion on the first render", () => {
    const fetcher = vi.fn(() => new Promise<PromotionResponse>(() => undefined));
    function Probe() {
      const { status, promotion, generation } = usePromotion();
      return `${status} ${promotion?.title ?? "none"} ${String(generation)}`;
    }
    const html = renderToStaticMarkup(
      <LanguageProvider storage={null}>
        <PromotionProvider initial={response(DEEPAVALI)} fetcher={fetcher} clock={() => 0}>
          <Probe />
        </PromotionProvider>
      </LanguageProvider>,
    );
    expect(html).toBe("ready Deepavali Sale 0");
    expect(fetcher).not.toHaveBeenCalled();
  });

  // 不传 initial 时首次渲染为 loading、没有活动（读取在挂载后进行）。
  it("renders loading without an initial result", () => {
    function Probe() {
      const { status, promotion } = usePromotion();
      return `${status} ${promotion === null ? "none" : "some"}`;
    }
    const html = renderToStaticMarkup(
      <LanguageProvider storage={null}>
        <PromotionProvider fetcher={() => new Promise<PromotionResponse>(() => undefined)}>
          <Probe />
        </PromotionProvider>
      </LanguageProvider>,
    );
    expect(html).toBe("loading none");
  });
});

describe("storefront preview options", () => {
  const previewUrl = (language: Language) => `/api/admin/promotions/7/preview?lang=${language}`;

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：传 url 时开始与切换语言都按它读取，
  // 不读 /api/promotion。
  it("reads through the given url on start and on a language change", async () => {
    const { store, fetcher, call } = setup(null, "en", { url: previewUrl });
    const stop = store.start("en");
    expect(call(0).url).toBe("/api/admin/promotions/7/preview?lang=en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    stop();
    store.start("ms");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(call(1).url).toBe("/api/admin/promotions/7/preview?lang=ms");
    const translated = display({ title: "Jualan Deepavali" });
    call(1).resolve(response(translated));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: translated, generation: 2 });
  });

  // UX 0.20 A12「前台预览」「预览中倒计时走到 0 时停在 0，不触发重新读取，也不去掉活动内容」：holdAtZero 时到 0 与之后很久，
  // 活动、状态与 generation 都不变，不读取、不留计时器，倒计时文字为 0。
  it("holds the promotion at zero without rereading", async () => {
    const { store, fetcher, call, advance, now } = setup(null, "en", { url: previewUrl, holdAtZero: true });
    store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    advance(HOUR_MS);
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 1 });
    advance(24 * HOUR_MS);
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 1 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    const deadline = store.current().deadline;
    expect(deadline).not.toBeNull();
    expect(countdownText("en", (deadline ?? 0) - now())).toBe(countdownText("en", 0));
  });

  // 同一句「也不去掉活动内容」与 HANDOFF 0.98（3）：到 0 之后切换语言照常按 url 读取一次；后台因结束时间已过回答 409 时保留当前的活动、
  // 状态与 generation，不置为没有活动，也不再读取。
  it("keeps the shown promotion when a language reread fails", async () => {
    const { store, fetcher, call, advance } = setup(null, "en", { url: previewUrl, holdAtZero: true });
    const stop = store.start("en");
    call(0).resolve(response(DEEPAVALI));
    await flush();
    advance(HOUR_MS);
    stop();
    store.start("zh");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(call(1).url).toBe("/api/admin/promotions/7/preview?lang=zh");
    call(1).reject(new PromotionError(409));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 1 });
    advance(HOUR_MS);
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  // HANDOFF 0.98（3）与 UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：传入 initial 时切换语言按 url 读取，
  // 读取失败（含网络中断）保留 initial 的活动与 generation 0。
  it.each([
    ["409", new PromotionError(409)],
    ["network error", new TypeError("Failed to fetch")],
  ])("keeps an initial result when a language reread fails (%s)", async (_name, error) => {
    const { store, call } = setup(response(DEEPAVALI), "en", { url: previewUrl, holdAtZero: true });
    store.start("zh");
    expect(call(0).url).toBe("/api/admin/promotions/7/preview?lang=zh");
    call(0).reject(error);
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: DEEPAVALI, generation: 0 });
  });

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」与 HANDOFF 0.98（3）「还没有结果（仍在 loading）则照现有写法变为
  // ready、没有活动」：后台接口第一次读取就失败时没有可显示的活动，holdAtZero 也按没有活动处理。
  it("settles to no promotion when the first read fails", async () => {
    const { store, call } = setup(null, "en", { url: previewUrl, holdAtZero: true });
    store.start("en");
    call(0).reject(new PromotionError(409));
    await flush();
    expect(store.current()).toMatchObject({ status: "ready", promotion: null, generation: 1 });
  });

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：预览之外两个选项都不传（或 holdAtZero 为假）时照旧读 /api/promotion，到 0 去掉活动并重新读取一次，
  // 切换语言读取失败时按没有活动处理。
  it("behaves as before without the options", async () => {
    const plain = setup(null, "en", { url: undefined, holdAtZero: false });
    plain.store.start("en");
    expect(plain.call(0).url).toBe("/api/promotion?lang=en");
    plain.call(0).resolve(response(DEEPAVALI));
    await flush();
    plain.advance(HOUR_MS);
    expect(plain.store.current()).toMatchObject({ status: "ready", promotion: null, generation: 2 });
    expect(plain.fetcher).toHaveBeenCalledTimes(2);

    const relabelled = setup(response(DEEPAVALI));
    relabelled.store.start("zh");
    relabelled.call(0).reject(new PromotionError(503));
    await flush();
    expect(relabelled.store.current()).toMatchObject({ status: "ready", promotion: null, generation: 1 });
  });

  // UX 0.20 A12「前台预览」「倒计时按活动的结束时间显示」：PromotionProvider 收下 url 与 holdAtZero，首次渲染照 initial 显示活动与倒计时，不读取。
  it("accepts the options on the provider", () => {
    const fetcher = vi.fn(() => new Promise<PromotionResponse>(() => undefined));
    function Probe() {
      const { status, promotion } = usePromotion();
      return `${status} ${promotion?.title ?? "none"}`;
    }
    const storage = { getItem: () => "en", setItem: () => undefined };
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage}>
        <PromotionProvider initial={response(DEEPAVALI)} fetcher={fetcher} clock={() => 0} url={previewUrl} holdAtZero>
          <p>
            <Probe />
          </p>
          <p>
            <PromotionCountdown />
          </p>
        </PromotionProvider>
      </LanguageProvider>,
    );
    expect(html).toBe(`<p>ready Deepavali Sale</p><p>${countdownText("en", HOUR_MS)}</p>`);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("outside the provider", () => {
  // 验收「在 Provider 之外调用 usePromotion 时返回 ready、promotion 为 null（后台页与测试不受影响）」。
  it("is ready without a promotion", () => {
    function Probe() {
      const { status, promotion, generation } = usePromotion();
      return `${status} ${promotion === null ? "none" : "some"} ${String(generation)}`;
    }
    expect(renderToStaticMarkup(<Probe />)).toBe("ready none 0");
  });
});

describe("PromotionCountdown", () => {
  function renderCountdown(initial: PromotionResponse, language: Language = "en"): string {
    const storage = { getItem: () => language, setItem: () => undefined };
    return renderToStaticMarkup(
      <LanguageProvider storage={storage}>
        <PromotionProvider initial={initial} clock={() => 0}>
          <p className="acs-promo-count">
            <PromotionCountdown />
          </p>
        </PromotionProvider>
      </LanguageProvider>,
    );
  }

  // HANDOFF 0.97「PromotionCountdown 只渲染 SHOP-TASK-252 countdownText 的文字…不设 aria-live」：只有文字，没有元素与 aria-live。
  it.each(["en", "zh", "ms"] as const)("renders only the countdown text in %s", (language) => {
    const html = renderCountdown(response(DEEPAVALI), language);
    expect(html).toBe(`<p class="acs-promo-count">${countdownText(language, HOUR_MS)}</p>`);
    expect(html).not.toContain("aria-live");
  });

  // ends_at 为 null（不计时）与没有活动时什么都不渲染。
  it("renders nothing without a deadline", () => {
    expect(renderCountdown(response(display({ ends_at: null })))).toBe(`<p class="acs-promo-count"></p>`);
    expect(renderCountdown(response(null))).toBe(`<p class="acs-promo-count"></p>`);
  });

  // 同一写法「自己每秒更新」：每当剩余时间跨过一个整秒就通知一次，文字随之每秒变化；剩余到 0 后不再通知。
  it("ticks every second until zero", () => {
    let now = 0;
    const deadline = 3_300;
    const texts: string[] = [];
    const stop = watchCountdown(deadline, () => now, () => {
      texts.push(countdownText("en", deadline - now));
    });
    const advance = (ms: number) => {
      now += ms;
      vi.advanceTimersByTime(ms);
    };
    expect(countdownText("en", deadline - now)).toBe("Ends in 00:00:03");
    advance(300);
    expect(texts).toEqual([]);
    advance(1);
    expect(texts).toEqual(["Ends in 00:00:02"]);
    advance(1_000);
    expect(texts).toEqual(["Ends in 00:00:02", "Ends in 00:00:01"]);
    advance(1_000);
    advance(1_000);
    expect(texts).toEqual(["Ends in 00:00:02", "Ends in 00:00:01", "Ends in 00:00:00"]);
    expect(vi.getTimerCount()).toBe(0);
    stop();
  });

  // 停止后不再通知。
  it("stops ticking when stopped", () => {
    let now = 0;
    const onTick = vi.fn();
    const stop = watchCountdown(10_000, () => now, onTick);
    stop();
    now += 5_000;
    vi.advanceTimersByTime(5_000);
    expect(onTick).not.toHaveBeenCalled();
  });
});
