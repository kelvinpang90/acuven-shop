import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import uxCopy from "../../../docs/UX-COPY.md?raw";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY } from "../i18n/language";

function renderMain(language: Language): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  const html = renderToStaticMarkup(<App initialPath="/privacy" storage={storage} />);
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

// 「联系」段的三语文案只在文档里有、字典里没有（本任务不渲染），从文档取来做反向检查。
function documentRow(key: string): string[] {
  const line = uxCopy.split(/\r?\n/).find((l) => l.startsWith(`| \`${key}\` |`));
  if (!line) {
    throw new Error(`${key} not found in docs/UX-COPY.md`);
  }
  return line.split("|").slice(2, 5).map((cell) => cell.trim());
}

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
  it.each(languages)("has no contact section and no WhatsApp link in %s", (language) => {
    const main = renderMain(language);
    const index = LANGUAGES.indexOf(language);
    for (const key of ["privacy.h_contact", "privacy.contact", "privacy.contact_button"]) {
      const text = documentRow(key)[index];
      expect(text, key).toBeTruthy();
      expect(main).not.toContain(escapeText(text ?? ""));
    }
    expect(main.toLowerCase()).not.toContain("whatsapp");
    expect(main).not.toContain("{{");
  });

  // UX P14：本页不提供、也不暗示访客删除收货资料的入口——正文里没有任何链接、按钮或表单。
  it("offers no way to delete shipping details", () => {
    const main = renderMain("en");
    expect(main).not.toMatch(/<(a|button|form|input|select|textarea)\b/);
  });

  // UX P14：手机上各段默认展开——段落不是可折叠控件，也没有被隐藏的内容。
  it("keeps every section expanded", () => {
    const main = renderMain("en");
    expect(main).not.toMatch(/<details\b|\shidden(=|\s|>)|aria-expanded/);
  });
});
