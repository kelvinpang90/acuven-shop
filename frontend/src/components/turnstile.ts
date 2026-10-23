import type { Language } from "../i18n/copy";

// 托管人机挑战（DESIGN 1.12「失败、并发与重试」：发送短信前完成，后端核验令牌）的加载与渲染：
// 用 Cloudflare Turnstile 官方的浏览器脚本显式渲染，不加依赖。短信验证组件只经下面的 ChallengeLoader 使用它，
// 测试以替身代替、不加载外部脚本。挑战里的文字由托管服务渲染，按当前界面语言请求对应语言（UX V1 说明）。

export const TURNSTILE_SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

// 界面语言对应的 Turnstile language 参数（ISO 639-1 语言码，中文为简体）。
const TURNSTILE_LANGUAGE: Readonly<Record<Language, string>> = { en: "en", zh: "zh-CN", ms: "ms" };

export function turnstileLanguage(language: Language): string {
  return TURNSTILE_LANGUAGE[language];
}

// 已渲染的一个挑战：reset 重新挑战（之后经 onToken 给出新令牌），remove 从页面移除。
export interface ChallengeWidget {
  reset: () => void;
  remove: () => void;
}

// 挑战的结果：onToken 为完成挑战取得的令牌；onLost 为令牌过期、挑战出错或超时（手上已没有可用的令牌）。
export interface ChallengeHandlers {
  onToken: (token: string) => void;
  onLost: () => void;
}

// 在 container 里渲染一个挑战；脚本加载失败或渲染失败时 Promise 失败。
export interface ChallengeLoader {
  render: (container: HTMLElement, siteKey: string, language: Language, handlers: ChallengeHandlers) => Promise<ChallengeWidget>;
}

interface TurnstileOptions {
  sitekey: string;
  language: string;
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": () => void;
  "timeout-callback": () => void;
}

interface TurnstileApi {
  render: (container: HTMLElement, options: TurnstileOptions) => string | null | undefined;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
}

function loadedApi(): TurnstileApi | undefined {
  return (window as Window & { turnstile?: TurnstileApi }).turnstile;
}

let loading: Promise<TurnstileApi> | null = null;

// 脚本只插入一次；加载失败时移除脚本元素，下一次渲染重新加载。
function loadScript(): Promise<TurnstileApi> {
  const ready = loadedApi();
  if (ready !== undefined) {
    return Promise.resolve(ready);
  }
  if (loading === null) {
    loading = new Promise<TurnstileApi>((resolve, reject) => {
      const script = document.createElement("script");
      const fail = () => {
        loading = null;
        script.remove();
        reject(new Error("turnstile script not available"));
      };
      script.src = TURNSTILE_SCRIPT_URL;
      script.addEventListener("load", () => {
        const api = loadedApi();
        if (api === undefined) {
          fail();
        } else {
          resolve(api);
        }
      });
      script.addEventListener("error", fail);
      document.head.appendChild(script);
    });
  }
  return loading;
}

export const TURNSTILE: ChallengeLoader = {
  render: async (container, siteKey, language, handlers) => {
    const api = await loadScript();
    const widgetId = api.render(container, {
      sitekey: siteKey,
      language: turnstileLanguage(language),
      callback: handlers.onToken,
      "expired-callback": handlers.onLost,
      "error-callback": handlers.onLost,
      "timeout-callback": handlers.onLost,
    });
    if (typeof widgetId !== "string") {
      throw new Error("turnstile render failed");
    }
    return {
      reset: () => {
        api.reset(widgetId);
      },
      remove: () => {
        api.remove(widgetId);
      },
    };
  },
};
