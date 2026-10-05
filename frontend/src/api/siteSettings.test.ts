import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchSiteSettings, SiteSettingsError } from "./siteSettings";

afterEach(() => {
  vi.unstubAllGlobals();
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("site settings", () => {
  // SHOP-TASK-025 验收第 3 条「打开时调用 SHOP-TASK-022 的 GET /api/site-settings」与 UX 0.7 修订要点「前台按服务端提供的开关当前值决定显示哪一组」：
  // GET 固定地址，不带 cookie、不缓存，回答里只取开关的布尔值。
  it.each([true, false])("reads the SMS switch (%s) without cookies or caching", async (enabled) => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => Promise.resolve(json(200, { sms_verification_enabled: enabled })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchSiteSettings()).resolves.toEqual({ sms_verification_enabled: enabled });
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
});
