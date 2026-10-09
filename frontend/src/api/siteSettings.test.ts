import { afterEach, describe, expect, it, vi } from "vitest";

import { createWhatsAppContactReader, fetchSiteSettings, parseWhatsAppContactUrl, SiteSettingsError } from "./siteSettings";

afterEach(() => {
  vi.unstubAllGlobals();
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// 测试里的链接一律用 chat.example.com，仓库不出现真实号码或主机（同 SHOP-TASK-073）。
const LINK = "https://chat.example.com/acuven?text=hi";

describe("site settings", () => {
  // SHOP-TASK-025 验收第 3 条「打开时调用 SHOP-TASK-022 的 GET /api/site-settings」与 UX 0.7 修订要点「前台按服务端提供的开关当前值决定显示哪一组」：
  // GET 固定地址，不带 cookie、不缓存，回答里只取开关的布尔值。
  // SHOP-TASK-074 改动：结果另含 whatsapp_contact_url，回答里没有该字段时为 null，整体相等的期望值随之加上这一项。
  it.each([true, false])("reads the SMS switch (%s) without cookies or caching", async (enabled) => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => Promise.resolve(json(200, { sms_verification_enabled: enabled })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchSiteSettings()).resolves.toEqual({ sms_verification_enabled: enabled, whatsapp_contact_url: null });
    const [url, init]: [string, RequestInit] = fetchMock.mock.calls[0] ?? ["", {}];
    expect(url).toBe("/api/site-settings");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.body).toBeUndefined();
  });

  // SHOP-TASK-025 验收第 3 条「失败时显示 common.error_retry」：非 2xx、网络错误与开关不是布尔值都抛错（页面据此显示提示），不猜测开关的值。
  it("throws when the switch cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(503, {}))));
    await expect(fetchSiteSettings()).rejects.toEqual(new SiteSettingsError(503));
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(200, { sms_verification_enabled: "false" }))));
    await expect(fetchSiteSettings()).rejects.toBeInstanceOf(SiteSettingsError);
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(fetchSiteSettings()).rejects.toBeInstanceOf(TypeError);
  });

  // SHOP-TASK-074 验收「另取 whatsapp_contact_url…不因它让现有 fetchSiteSettings 失败（结账页读取开关的行为不变）」：
  // 链接合格时一并取出；链接为 null、其他类型或不合格时开关照常读出、链接为 null。
  it.each([LINK, null, 42, { url: LINK }, "http://chat.example.com/", "not a url"])(
    "reads the switch whatever the contact link is (%j)",
    async (link) => {
      vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(200, { sms_verification_enabled: true, whatsapp_contact_url: link }))));
      await expect(fetchSiteSettings()).resolves.toEqual({
        sms_verification_enabled: true,
        whatsapp_contact_url: link === LINK ? LINK : null,
      });
    },
  );
});

describe("WhatsApp contact link", () => {
  // UX 0.10「全局框架」页脚 common.whatsapp_cta 链到 {{WHATSAPP_CONTACT_LINK}} 与 SHOP-TASK-074 验收「协议为 https 且主机非空的绝对地址字符串时为该地址」：原样返回。
  it.each([LINK, "https://chat.example.com", "HTTPS://chat.example.com/x", "https://chat.example.com:8443/p?q=1#f"])("keeps %s", (link) => {
    expect(parseWhatsAppContactUrl(link)).toBe(link);
  });

  // UX Q10「配置缺失时隐藏」与 SHOP-TASK-074 验收「其余（缺字段、null、其他类型或不合格）一律为 null」。
  it.each([
    undefined,
    null,
    true,
    0,
    [LINK],
    { href: LINK },
    "",
    "   ",
    "chat.example.com",
    "//chat.example.com/",
    "/privacy",
    "http://chat.example.com/",
    "ftp://chat.example.com/",
    "javascript:alert(1)",
    "whatsapp://send?phone=0",
    "https://",
    "https:///path",
    "https:chat.example.com",
    "https://:443/",
    "https://user@/",
    "https://[::1/",
    "https://chat example.com/",
  ])("returns null for %j", (value) => {
    expect(parseWhatsAppContactUrl(value)).toBeNull();
  });

  // SHOP-TASK-074 验收「供页脚与 P14 共用的读取（同一次页面加载只请求一次，取得前与失败时为 null）」：
  // 取得前为 null；多次读取只发一次 GET（同地址、不带 cookie、不缓存），之后都是同一结果。
  it("requests once and shares the result", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(json(200, { sms_verification_enabled: false, whatsapp_contact_url: LINK })),
    );
    vi.stubGlobal("fetch", fetchMock);
    const reader = createWhatsAppContactReader();
    expect(reader.current()).toBeNull();
    const [first, second] = await Promise.all([reader.load(), reader.load()]);
    expect(first).toBe(LINK);
    expect(second).toBe(LINK);
    await expect(reader.load()).resolves.toBe(LINK);
    expect(reader.current()).toBe(LINK);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init]: [string, RequestInit] = fetchMock.mock.calls[0] ?? ["", {}];
    expect(url).toBe("/api/site-settings");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
  });

  // 同一句「取得前与失败时为 null」与 UX Q10：非 2xx、网络错误、不是 JSON、回答不是对象、未配置（null）或链接不合格时为 null，不抛错；
  // 失败后再读也不重发请求（一次页面加载只请求一次）。
  const failures: [string, () => Promise<Response>][] = [
    ["503", () => Promise.resolve(json(503, {}))],
    ["network error", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["not JSON", () => Promise.resolve(new Response("<html>", { status: 200 }))],
    ["not an object", () => Promise.resolve(json(200, [LINK]))],
    ["not configured", () => Promise.resolve(json(200, { sms_verification_enabled: false, whatsapp_contact_url: null }))],
    ["missing field", () => Promise.resolve(json(200, { sms_verification_enabled: false }))],
    ["http link", () => Promise.resolve(json(200, { sms_verification_enabled: false, whatsapp_contact_url: "http://chat.example.com/" }))],
  ];
  it.each(failures)("is null when %s", async (name, respond) => {
    const fetchMock = vi.fn(respond);
    vi.stubGlobal("fetch", fetchMock);
    const reader = createWhatsAppContactReader();
    await expect(reader.load(), name).resolves.toBeNull();
    await expect(reader.load(), name).resolves.toBeNull();
    expect(reader.current(), name).toBeNull();
    expect(fetchMock, name).toHaveBeenCalledTimes(1);
  });

  // SHOP-TASK-074 验收「不因它让现有 fetchSiteSettings 失败」的另一面：链接的读取只看链接，开关不合格时链接照常取出。
  it("reads the link even when the switch is malformed", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(200, { sms_verification_enabled: "yes", whatsapp_contact_url: LINK }))));
    await expect(createWhatsAppContactReader().load()).resolves.toBe(LINK);
  });
});
