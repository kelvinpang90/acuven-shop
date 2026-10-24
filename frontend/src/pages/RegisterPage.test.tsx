import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { guestPath, RouterProvider, VERIFIED_REDIRECT_MS } from "../router";
import { LOADING_SETTINGS, loadSettings } from "./LoginPage";
import type { LoginSettings, Wait } from "./LoginPage";
import { finishRegister, registerVerify, RegisterView } from "./RegisterPage";
import type { RegisterMoves, RegisterSettings, RegisterViewProps } from "./RegisterPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的公开 key 是虚构值。
const SITE_KEY = "1x00000000000000000000AA";
const SMS_ON: RegisterSettings = { status: "ready", smsEnabled: true, siteKey: SITE_KEY };
const SMS_OFF: RegisterSettings = { status: "ready", smsEnabled: false, siteKey: SITE_KEY };
const FAILED: RegisterSettings = { status: "failed" };

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/register">{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<RegisterViewProps> = {}, language: Language = "en"): string {
  const props: RegisterViewProps = {
    settings: SMS_ON,
    done: null,
    guestTo: "/",
    onVerified: noop,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<RegisterView {...props} />, language));
}

function renderApp(path: string, language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={path} storage={languageStorage(language)} />);
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 字典某一条在页面里的样子（HTML 转义后）。
function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

// 文本节点（不含属性），依出现顺序。
function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

// V1 所在的内容区（开始标签之后，到 common.nav_login 之前）。
function body(html: string): string {
  const start = html.indexOf(`<div class="site-register__body"`);
  const end = html.indexOf(`<a class="site-register__login"`);
  const open = html.indexOf(">", start) + 1;
  return start < 0 || end < open ? "" : html.slice(open, end - "</div>".length);
}

function header(html: string): string {
  return /<header[\s\S]*<\/header>/.exec(html)?.[0] ?? "";
}

function json(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

type Reply = () => Response | "network";

// fetch 替身：每次请求调用 reply 取回答（"network" 时抛出网络错误）。
function stubFetch(reply: Reply) {
  const request = vi.fn((input: string | URL, init?: RequestInit) => {
    const next = reply();
    return next === "network" ? Promise.reject(new TypeError(`Failed to fetch: ${String(init?.method)} ${String(input)}`)) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", request);
  return request;
}

// 页面对结果的处理的替身：按顺序记下事件。
function fakeMoves() {
  const events: string[] = [];
  const moves: RegisterMoves = {
    verified: (key) => {
      events.push(`verified ${key}`);
    },
    replace: (path) => {
      events.push(`replace ${path}`);
    },
  };
  return { moves, events };
}

// 计时替身：记下等待的毫秒数，不真的等待；run 在完成前执行（如模拟期间离开页面）。
function fakeWait(events: string[], run: () => void = noop): Wait {
  return vi.fn((ms: number, signal: AbortSignal) => {
    events.push(`wait ${String(ms)}${signal.aborted ? " aborted" : ""}`);
    run();
    return Promise.resolve();
  });
}

describe("route and card", () => {
  // SHOP-TASK-148 验收第 2 条「路由 /register…套前台框架」「页面为 frontend/src/pages/RegisterPage.tsx」与「开关的值与公开 key 照 SHOP-TASK-146 的写法读取站点设置」：
  // 经路由打开 /register 在前台框架里（演示横幅）渲染注册页；服务端渲染时站点设置还没读到，V1 所在的内容区为空并标 aria-busy，没有号码输入。
  it.each(LANGUAGES)("opens /register inside the site frame with a busy body in %s", (language) => {
    const html = renderApp("/register", language);
    expect(html).toContain(COPY["common.demo_banner"][language]);
    expect(html).toContain(`<h1 class="acs-display-l">${copy("auth.register_title", language)}</h1>`);
    expect(html).toContain(`<div class="site-register__body" aria-busy="true"></div>`);
    expect(html).not.toContain(`type="tel"`);
    expect(html).toContain(`<a class="site-register__login" href="/login">${copy("common.nav_login", language)}</a>`);
  });

  // UX P11「入口…页头只有登录入口，不设注册入口」与 SHOP-TASK-148 验收第 2 条「页头不出现注册入口」：注册页与首页的页头里，除语言切换链接
  // （指向当前页、带 lang）外没有指向 /register 的链接，也没有 common.nav_register 的文字；页头的登录入口照旧。
  it.each(LANGUAGES)("has no register entry in the header in %s", (language) => {
    for (const path of ["/register", "/"]) {
      const head = header(renderApp(path, language));
      const toRegister = [...head.matchAll(/<a\s[^>]*href="\/register"[^>]*>/g)].map((m) => m[0]);
      expect(toRegister.filter((tag) => !/\slang="/.test(tag))).toEqual([]);
      expect(head).not.toContain(`>${copy("common.nav_register", language)}<`);
      expect(head).toContain(`href="/login">${copy("common.nav_login", language)}</a>`);
    }
  });

  // UX P11 桌面线框「居中单栏卡片：[auth.register_title]、★ [auth.register_demo_hint]、[auth.same_flow_note]、<V1>、[common.nav_login] → P12」与
  // 视觉稿 P11-desktop：卡片里依次为标题（h1）、★ 提示、说明、V1 与去 /login 的链接，链接是卡片的最后一项。
  it.each(LANGUAGES)("shows the card content in the order of the wireframe in %s", (language) => {
    const html = render({}, language);
    expect(html).toMatch(
      new RegExp(
        `<main class="site-register"><section class="acs-card site-register__card"><h1 class="acs-display-l">${copy("auth.register_title", language)}</h1>` +
          `<p class="acs-hint"><svg[^>]*>[\\s\\S]*?</svg><span>${copy("auth.register_demo_hint", language)}</span></p>` +
          `<p class="acs-body-s site-register__text">${copy("auth.same_flow_note", language)}</p>` +
          `<div class="site-register__body" aria-busy="false"><div class="site-sms">`,
      ),
    );
    expect(html).toMatch(new RegExp(`</div></div><a class="site-register__login" href="/login">${copy("common.nav_login", language)}</a></section></main>`));
  });

  // 同一条「读取完成之前」（照 SHOP-TASK-146 的写法）：读取中卡片只有标题、★ 提示、说明与 common.nav_login，内容区为空。
  it("shows only the fixed card text while the site settings load", () => {
    const html = render({ settings: LOADING_SETTINGS });
    expect(visibleTexts(html)).toEqual([COPY["auth.register_title"].en, COPY["auth.register_demo_hint"].en, COPY["auth.same_flow_note"].en, COPY["common.nav_login"].en]);
    expect(body(html)).toBe("");
  });

  // 同一条（照 SHOP-TASK-146 读取失败时短信验证码页签只显示 common.error_retry）：读取失败时内容区只有 common.error_retry，没有 V1。
  it("shows common.error_retry instead of V1 when the settings fail", () => {
    const html = render({ settings: FAILED });
    expect(textNodes(body(html))).toEqual([COPY["common.error_retry"].en]);
    expect(html).not.toContain(`<div class="site-sms">`);
  });

  // 同一条「开关的值与公开 key 照 SHOP-TASK-146 的写法读取站点设置」：页面复用登录页的 loadSettings，只发一次 GET /api/site-settings。
  it("reads the switch and the challenge site key once", async () => {
    const request = stubFetch(() => json(200, { sms_verification_enabled: true, turnstile_site_key: SITE_KEY, whatsapp_contact_url: null }));
    const shown: LoginSettings[] = [];
    await loadSettings(new AbortController().signal, (settings) => {
      shown.push(settings);
    });
    expect(shown).toEqual([SMS_ON]);
    expect(request.mock.calls.map(([input, init]) => `${String(init?.method)} ${String(input)}`)).toEqual(["GET /api/site-settings"]);
  });
});

describe("embedded V1", () => {
  // SHOP-TASK-148 验收第 2 条「V1（用途 register，开关的值与公开 key…，auth.continue_guest 的去向取 router.tsx 导出的同一函数：来源为 /checkout 时 /checkout，
  // 否则首页 P01）」：交给 V1 的是用途 register、站点设置里的开关与公开 key，以及 guestPath(来源)。
  it.each([
    ["/checkout", "/checkout"],
    ["/cart", "/"],
    [null, "/"],
  ] as const)("embeds V1 for register and sends continue as guest from %s to %s", (source, expected) => {
    const onVerified = noop;
    expect(registerVerify({ smsEnabled: true, siteKey: SITE_KEY }, guestPath(source), onVerified)).toEqual({
      purpose: "register",
      smsEnabled: true,
      siteKey: SITE_KEY,
      guestTo: expected,
      onVerified,
    });
    expect(registerVerify({ smsEnabled: false, siteKey: null }, guestPath(source), onVerified)).toMatchObject({ purpose: "register", smsEnabled: false, siteKey: null });
    const off = body(render({ settings: SMS_OFF, guestTo: guestPath(source) }));
    expect(off).toContain(`<a class="acs-btn acs-btn--secondary site-sms__guest" href="${expected}">${copy("auth.continue_guest")}</a>`);
  });

  // UX P11 线框「<V1：用途『注册 / 短信登录』> [auth.phone]…[auth.sms_scope]、[auth.claim_notice]、[auth.challenge]、([auth.send_code])」：
  // 开关开启时内容区是注册 / 短信登录的 V1（有 auth.claim_notice 与发送按钮），没有暂停提示。
  it.each(LANGUAGES)("shows the register form of V1 with SMS on in %s", (language) => {
    const html = body(render({}, language));
    expect(html).toMatch(/^<div class="site-sms">/);
    expect(html).toContain(`${copy("auth.phone", language)}</label>`);
    expect(html).toContain(copy("auth.sms_scope", language));
    expect(html).toContain(copy("auth.claim_notice", language));
    expect(html).toContain(`>${copy("auth.send_code", language)}</button>`);
    expect(html).not.toContain(copy("auth.sms_paused", language));
  });

  // UX P11「短信验证开关关闭时：V1 整个替换为 [auth.sms_paused] 与 [auth.continue_guest]，不显示 [auth.sms_scope] 与 [auth.claim_notice]」与
  // SHOP-TASK-148 验收第 2 条「开关关闭时由 V1 显示 auth.sms_paused 与 auth.continue_guest」：内容区只有这两段文字；卡片其余部分不变。
  it.each(LANGUAGES)("replaces V1 with auth.sms_paused and continue as guest with SMS off in %s", (language) => {
    const html = render({ settings: SMS_OFF, guestTo: "/checkout" }, language);
    expect(textNodes(body(html))).toEqual([COPY["auth.sms_paused"][language], COPY["auth.continue_guest"][language]]);
    // 只看 V1 所在的内容区：卡片上的 auth.same_flow_note 本身含有「手机号」等字样。
    for (const key of ["auth.sms_scope", "auth.claim_notice", "auth.send_code", "auth.phone"] as const) {
      expect(body(html), key).not.toContain(copy(key, language));
    }
    expect(html).not.toContain(`${copy("auth.phone", language)}</label>`);
    expect(html).toContain(copy("auth.same_flow_note", language));
    expect(html).toContain(`href="/login">${copy("common.nav_login", language)}</a>`);
  });
});

describe("after verifying", () => {
  // SHOP-TASK-148 验收第 3 条「按 created 显示 auth.verified_registered 或 auth.verified_login，1.5 秒后用路由的 replace 前往来源页，来源与去向复用
  // router.tsx 导出的函数与 VERIFIED_REDIRECT_MS」与 DESIGN 1.12「权限与资料保护」第 3 条（号码已注册则登录，未注册则创建无密码会员并登录）：
  // 先显示文字，再等 VERIFIED_REDIRECT_MS，然后以 replace 前往 verifiedPath（来源为前台页时回到来源，后台页、登录页与没有来源时去会员中心 P13；
  // SHOP-TASK-152 验收第 6 条与 HANDOFF 0.61 第（5）条「登录与注册成功没有来源时改去 /account」，原期望为首页 P01）。
  it.each([
    [true, "auth.verified_registered", "/checkout", "/checkout"],
    [false, "auth.verified_login", "/products/crew-neck-tee", "/products/crew-neck-tee"],
    [true, "auth.verified_registered", "/admin/orders", "/account"],
    [false, "auth.verified_login", "/login", "/account"],
    [true, "auth.verified_registered", null, "/account"],
  ] as const)("finishes with created = %s as %s and goes from %s to %s", async (created, key, source, expected) => {
    const { moves, events } = fakeMoves();
    await finishRegister({ kind: "member", created }, source, new AbortController().signal, fakeWait(events), moves);
    expect(events).toEqual([`verified ${key}`, `wait ${String(VERIFIED_REDIRECT_MS)}`, `replace ${expected}`]);
  });

  // DESIGN 1.12「权限与资料保护」第 3 条「注册即短信验证」：只有注册 / 短信登录的结果才算登录，重设密码的两种结果不会出现在这一用途，页面不理会。
  it("ignores results that do not log in", async () => {
    const { moves, events } = fakeMoves();
    for (const verified of [{ kind: "registered", csrfToken: "x" }, { kind: "not_registered" }] as const) {
      await finishRegister(verified, "/cart", new AbortController().signal, fakeWait(events), moves);
    }
    expect(events).toEqual([]);
  });

  // 视觉稿 V1-verified 与 UX P11 线框「成功：[auth.verified_registered] 或 [auth.verified_login]」：结果文字在 V1 之后、common.nav_login 之前，为 success 提示。
  it.each(LANGUAGES)("shows the result after V1 in %s", (language) => {
    for (const key of ["auth.verified_registered", "auth.verified_login"] as const) {
      const html = render({ done: key }, language);
      expect(html).toMatch(
        new RegExp(`</div><div class="acs-alert acs-alert--success" role="status"><svg[^>]*>[\\s\\S]*?</svg><span>${copy(key, language)}</span></div></div><a class="site-register__login"`),
      );
    }
  });

  // SHOP-TASK-148 验收第 4 条「离开页面时中止请求与计时，之后不再更新」：已离开时不显示文字也不跳转；计时期间离开不跳转。
  it("stops after leaving the page", async () => {
    const before = fakeMoves();
    const gone = new AbortController();
    gone.abort();
    await finishRegister({ kind: "member", created: true }, "/cart", gone.signal, fakeWait(before.events), before.moves);
    expect(before.events).toEqual([]);

    const during = fakeMoves();
    const leaving = new AbortController();
    await finishRegister(
      { kind: "member", created: false },
      "/cart",
      leaving.signal,
      fakeWait(during.events, () => {
        leaving.abort();
      }),
      during.moves,
    );
    expect(during.events).toEqual(["verified auth.verified_login", `wait ${String(VERIFIED_REDIRECT_MS)}`]);
  });
});

describe("page text", () => {
  // SHOP-TASK-148 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…」与第 5 条「页面文字（国家码选项 +60、+65 除外）全部来自字典」：
  // 读取中、读取失败、开关开闭与两种成功状态下，三种语言的文字都在当前语言的字典里。
  const states: Partial<RegisterViewProps>[] = [
    { settings: LOADING_SETTINGS },
    { settings: FAILED },
    {},
    { settings: SMS_OFF, guestTo: "/checkout" },
    { settings: { status: "ready", smsEnabled: true, siteKey: null } },
    { done: "auth.verified_registered" },
    { done: "auth.verified_login" },
  ];
  it.each(LANGUAGES)("uses only dictionary text in %s", (language) => {
    const allowed = new Set<string>([...Object.values(COPY).map((entry) => entry[language]), "+60", "+65"]);
    for (const state of states) {
      for (const text of visibleTexts(render(state, language))) {
        expect(allowed.has(text), text).toBe(true);
      }
    }
  });
});
