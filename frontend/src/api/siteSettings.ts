import { createContext, createElement, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

// 站点设置的公开只读接口 GET /api/site-settings（app/api/site_settings.py，SHOP-TASK-022、073）。
// 短信验证开关的当前值，供前台决定显示哪一组短信相关界面（UX 0.7「全局框架」）；服务端下单时自行按开关判定，
// 页面显示不是依据。WhatsApp 联系链接（SHOP-TASK-073），供页脚的 common.whatsapp_cta 与隐私说明 P14 的「联系」段：
// 已配置时显示，未配置、不合格或读取失败时整个按钮与整段隐藏（UX Q10）。不带 cookie、不缓存（接口本身也回 no-store，开关改后不会读到旧值）。

const SITE_SETTINGS_URL = "/api/site-settings";

export interface SiteSettings {
  sms_verification_enabled: boolean;
  // 不合格或没有时为 null；不因它让开关的读取失败。
  whatsapp_contact_url: string | null;
}

export class SiteSettingsError extends Error {
  readonly status: number | null;

  constructor(status: number | null) {
    super(`site settings request failed${status === null ? "" : ` with status ${String(status)}`}`);
    this.name = "SiteSettingsError";
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// WhatsApp 联系链接：协议为 https 且主机非空的绝对地址字符串时原样返回，其余（缺字段、null、其他类型或不合格）一律为 null。
// 先要求以 https:// 开头且其后紧接主机（浏览器的 URL 会把 https:host、https:///host 补成有主机的地址，接口按缺主机处理）。
export function parseWhatsAppContactUrl(value: unknown): string | null {
  if (typeof value !== "string" || !/^https:\/\/[^/\\]/i.test(value)) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname !== "" ? value : null;
  } catch {
    return null;
  }
}

// 非 2xx 时抛 SiteSettingsError，网络错误照原样抛出。
async function requestSiteSettings(signal?: AbortSignal): Promise<unknown> {
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
  const body: unknown = await response.json();
  return body;
}

// 读一次开关；非 2xx、网络错误或回答里开关不是布尔值时抛错，由页面显示 common.error_retry。
export async function fetchSiteSettings(signal?: AbortSignal): Promise<SiteSettings> {
  const body = await requestSiteSettings(signal);
  if (!isRecord(body) || typeof body.sms_verification_enabled !== "boolean") {
    throw new SiteSettingsError(null);
  }
  return {
    sms_verification_enabled: body.sms_verification_enabled,
    whatsapp_contact_url: parseWhatsAppContactUrl(body.whatsapp_contact_url),
  };
}

// 只取 WhatsApp 联系链接：不看开关是否合格；请求失败或响应不合格时为 null。
async function fetchWhatsAppContactUrl(): Promise<string | null> {
  try {
    const body = await requestSiteSettings();
    return isRecord(body) ? parseWhatsAppContactUrl(body.whatsapp_contact_url) : null;
  } catch {
    return null;
  }
}

// 页脚与 P14 共用的读取：第一次 load 时请求，之后返回同一结果，不再请求（失败也不重试）。
export interface WhatsAppContactReader {
  // 已取得的链接；取得前、未配置与失败时为 null。
  current(): string | null;
  load(): Promise<string | null>;
}

export function createWhatsAppContactReader(): WhatsAppContactReader {
  let request: Promise<string | null> | null = null;
  let url: string | null = null;
  return {
    current: () => url,
    load: () => {
      request ??= fetchWhatsAppContactUrl().then((loaded) => {
        url = loaded;
        return loaded;
      });
      return request;
    },
  };
}

// 本次页面加载唯一的读取；在页面之间切换时不再请求。
const pageLoadReader = createWhatsAppContactReader();

const WhatsAppContactContext = createContext<WhatsAppContactReader>(pageLoadReader);

interface WhatsAppContactProviderProps {
  children: ReactNode;
  // 测试传入替身；不传时用本次页面加载的读取。
  reader?: WhatsAppContactReader | undefined;
}

export function WhatsAppContactProvider({ children, reader }: WhatsAppContactProviderProps) {
  return createElement(WhatsAppContactContext.Provider, { value: reader ?? pageLoadReader }, children);
}

// 已取得的链接；首次渲染用已取得的值（没有时为 null），取得后更新。
export function useWhatsAppContactUrl(): string | null {
  const reader = useContext(WhatsAppContactContext);
  const [url, setUrl] = useState<string | null>(() => reader.current());

  useEffect(() => {
    let active = true;
    void reader.load().then((loaded) => {
      if (active) {
        setUrl(loaded);
      }
    });
    return () => {
      active = false;
    };
  }, [reader]);

  return url;
}
