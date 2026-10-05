// 站点设置的公开只读接口 GET /api/site-settings（app/api/site_settings.py，SHOP-TASK-022）。
// 只有短信验证开关的当前值，供前台决定显示哪一组短信相关界面（UX 0.7「全局框架」）；服务端下单时自行按开关判定，
// 页面显示不是依据。不带 cookie、不缓存（接口本身也回 no-store，开关改后不会读到旧值）。

const SITE_SETTINGS_URL = "/api/site-settings";

export interface SiteSettings {
  sms_verification_enabled: boolean;
}

export class SiteSettingsError extends Error {
  readonly status: number | null;

  constructor(status: number | null) {
    super(`site settings request failed${status === null ? "" : ` with status ${String(status)}`}`);
    this.name = "SiteSettingsError";
    this.status = status;
  }
}

// 读一次开关；非 2xx、网络错误或回答里开关不是布尔值时抛错，由页面显示 common.error_retry。
export async function fetchSiteSettings(signal?: AbortSignal): Promise<SiteSettings> {
  const response = await fetch(SITE_SETTINGS_URL, {
    method: "GET",
    credentials: "omit",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new SiteSettingsError(response.status);
  }
  const body = (await response.json()) as { sms_verification_enabled?: unknown };
  if (typeof body.sms_verification_enabled !== "boolean") {
    throw new SiteSettingsError(null);
  }
  return { sms_verification_enabled: body.sms_verification_enabled };
}
