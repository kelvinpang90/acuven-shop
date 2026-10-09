import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { createWhatsAppContactReader } from "../api/siteSettings";
import type { WhatsAppContactReader } from "../api/siteSettings";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY } from "../i18n/language";

// SHOP-TASK-074 起可传入 WhatsApp 联系链接的读取（替身）；不传时为新建、尚未请求的读取，即取得前的首次渲染。
function renderMain(language: Language, whatsAppContact: WhatsAppContactReader = createWhatsAppContactReader()): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  const html = renderToStaticMarkup(<App initialPath="/privacy" storage={storage} whatsAppContact={whatsAppContact} />);
  const main = /<main class="site-privacy">[\s\S]*<\/main>/.exec(html)?.[0];
  if (main === undefined) {
    throw new Error("privacy page main not rendered");
  }
  return main;
}

// React 转义后的文字（撇号等）与字典原文对齐。
function escapeText(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/'/g, "&#x27;").replace(/"/g, "&quot;");
}

// 测试里的链接一律用 chat.example.com，仓库不出现真实号码或主机。
const WHATSAPP_LINK = "https://chat.example.com/acuven?text=hi";

// 以替身 fetch 读完一次后的读取：respond 决定接口的回答。
async function settledReader(respond: () => Promise<Response>): Promise<WhatsAppContactReader> {
  vi.stubGlobal("fetch", vi.fn(respond));
  try {
    const reader = createWhatsAppContactReader();
    await reader.load();
    return reader;
  } finally {
    vi.unstubAllGlobals();
  }
}

function settings(whatsappContactUrl: unknown): () => Promise<Response> {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify({ sms_verification_enabled: false, whatsapp_contact_url: whatsappContactUrl }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
}

// 「联系」段的三个键（SHOP-TASK-074 起收入字典，与文档逐字相同由 i18n/copy.test.ts 守住）。
const CONTACT_KEYS: readonly CopyKey[] = ["privacy.h_contact", "privacy.contact", "privacy.contact_button"];

// docs/UX.md P14 线框的顺序：标题、★ 提示、引言、四个段落（段标题后接各自的文案）。
const ORDER: readonly CopyKey[] = [
  "privacy.title",
  "privacy.demo_hint",
  "privacy.intro",
  "privacy.h_collect",
  "privacy.collect",
  "privacy.fictional",
  "privacy.h_retention",
  "privacy.retention_recipient",
  "privacy.member",
  "privacy.member_backup",
  "privacy.h_access",
  "privacy.browser_access",
  "privacy.lookup_risk",
  "privacy.h_sms_logs",
  "privacy.sms",
  "privacy.logs",
  "privacy.sms_toggle",
];

const languages = [...LANGUAGES];

describe("privacy page P14", () => {
  // UX P14：标题、★ privacy.demo_hint、引言与四个段落的全部文案，按线框顺序（0.7 的 privacy.sms_toggle 在短信与日志段）。
  it.each(languages)("renders every paragraph in order in %s", (language) => {
    const main = renderMain(language);
    let previous = -1;
    for (const key of ORDER) {
      const at = main.indexOf(escapeText(COPY[key][language]));
      expect(at, key).toBeGreaterThan(previous);
      previous = at;
    }
    expect(main).toContain(`<h1 class="acs-display-l">${escapeText(COPY["privacy.title"][language])}</h1>`);
    expect(main.match(/<section\b/g)).toHaveLength(4);
    expect(main.match(/<h2\b/g)).toHaveLength(4);
  });

  // UX P14 与 Q10：WhatsApp 联系链接未配置时隐藏整个「联系」段，不显示占位。
  // SHOP-TASK-074 起这里是链接取得前的首次渲染；三个键改从字典取文字（此前字典里没有、从 docs/UX-COPY.md 取），断言不变。
  it.each(languages)("has no contact section and no WhatsApp link in %s", (language) => {
    const main = renderMain(language);
    for (const key of CONTACT_KEYS) {
      expect(main, key).not.toContain(escapeText(COPY[key][language]));
    }
    expect(main.toLowerCase()).not.toContain("whatsapp");
    expect(main).not.toContain("{{");
  });

  // UX Q10「配置缺失时隐藏所有 WhatsApp 按钮与联系段落，不显示占位文字」与 SHOP-TASK-074 验收「未取得时整段不渲染」：
  // 接口回答链接为 null、缺字段、不合格（http），或请求失败（503、网络错误）后，正文仍只有四段，没有联系段的文字、whatsapp 与 {{。
  it.each([
    ["not configured", settings(null)],
    ["missing", () => Promise.resolve(new Response(JSON.stringify({ sms_verification_enabled: true }), { status: 200 }))],
    ["not https", settings("http://chat.example.com/")],
    ["503", () => Promise.resolve(new Response("{}", { status: 503 }))],
    ["network error", () => Promise.reject(new TypeError("Failed to fetch"))],
  ] as [string, () => Promise<Response>][])("has no contact section when the link is %s", async (name, respond) => {
    const reader = await settledReader(respond);
    for (const language of LANGUAGES) {
      const main = renderMain(language, reader);
      expect(main.match(/<section\b/g), name).toHaveLength(4);
      for (const key of CONTACT_KEYS) {
        expect(main, `${name} ${key}`).not.toContain(escapeText(COPY[key][language]));
      }
      expect(main.toLowerCase(), name).not.toContain("whatsapp");
      expect(main, name).not.toContain("{{");
    }
  });

  // UX P14 线框「[privacy.h_contact] [privacy.contact] ( [privacy.contact_button] ) → {{WHATSAPP_CONTACT_LINK}}」在四段之后，
  // 与 SHOP-TASK-074 验收「h2 privacy.h_contact、privacy.contact 与链到该地址的 privacy.contact_button 按钮（同上新标签页与 rel）」、
  // 「按钮为 acs-btn acs-btn--secondary，内含 aria-hidden 的对话气泡图形」：第五段依次为 h2、段落与按钮，文字都取自字典。
  it.each(languages)("shows the contact section after the four sections in %s once the link is loaded", async (language) => {
    const reader = await settledReader(settings(WHATSAPP_LINK));
    const main = renderMain(language, reader);
    const sections = main.match(/<section class="site-privacy__section">[\s\S]*?<\/section>/g) ?? [];
    expect(sections).toHaveLength(5);
    expect(main.indexOf(escapeText(COPY["privacy.sms_toggle"][language]))).toBeLessThan(main.indexOf(sections[4] ?? "-"));
    expect(main.endsWith(`${sections[4] ?? "-"}</article></main>`)).toBe(true);
    const contact = sections[4] ?? "";
    const heading = `<h2 class="acs-display-s">${escapeText(COPY["privacy.h_contact"][language])}</h2>`;
    const paragraph = `<p>${escapeText(COPY["privacy.contact"][language])}</p>`;
    expect(contact.startsWith(`<section class="site-privacy__section">${heading}${paragraph}<a `)).toBe(true);
    const button = /<a [^>]*>[\s\S]*?<\/a>/.exec(contact)?.[0] ?? "";
    expect(button).toMatch(/^<a class="acs-btn acs-btn--secondary site-privacy__contact" /);
    expect(button).toMatch(/^<a [^>]*\bhref="https:\/\/chat\.example\.com\/acuven\?text=hi"/);
    expect(button).toMatch(/^<a [^>]*\btarget="_blank"/);
    expect(button).toMatch(/^<a [^>]*\brel="noopener noreferrer"/);
    expect(button).toMatch(/^<a [^>]*><svg [^>]*aria-hidden="true"[^>]*><path [^>]*><\/path><\/svg>/);
    expect(button.endsWith(`</svg>${escapeText(COPY["privacy.contact_button"][language])}</a>`)).toBe(true);
    expect(contact.endsWith(`${button}</section>`)).toBe(true);
    expect(main).not.toContain("{{");
  });

  // UX P14：本页不提供、也不暗示访客删除收货资料的入口——正文里没有任何链接、按钮或表单。
  // SHOP-TASK-074 起已配置时正文多了「联系」段的 WhatsApp 链接：已取得时去掉这一个链接后照样断言。
  it("offers no way to delete shipping details", async () => {
    expect(renderMain("en")).not.toMatch(/<(a|button|form|input|select|textarea)\b/);
    const configured = renderMain("en", await settledReader(settings(WHATSAPP_LINK)));
    const withoutContact = configured.replace(/<a class="acs-btn acs-btn--secondary site-privacy__contact"[^>]*>[\s\S]*?<\/a>/, "");
    expect(withoutContact).not.toBe(configured);
    expect(withoutContact).not.toMatch(/<(a|button|form|input|select|textarea)\b/);
  });

  // UX P14：手机上各段默认展开——段落不是可折叠控件，也没有被隐藏的内容（已配置时的「联系」段也一样）。
  it("keeps every section expanded", async () => {
    expect(renderMain("en")).not.toMatch(/<details\b|\shidden(=|\s|>)|aria-expanded/);
    const configured = renderMain("en", await settledReader(settings(WHATSAPP_LINK)));
    expect(configured).not.toMatch(/<details\b|\shidden(=|\s|>)|aria-expanded/);
  });
});
