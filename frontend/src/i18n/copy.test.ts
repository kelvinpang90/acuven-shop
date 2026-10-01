import { describe, expect, it } from "vitest";

// 经 Vite 的 ?raw 读成字符串：不 import node:fs，不给 tsconfig.app.json 下的代码引入 Node 类型。
import uxCopy from "../../../docs/UX-COPY.md?raw";
import { COPY, LANGUAGES, formatCopy, translate } from "./copy";
import type { CopyKey, Language } from "./copy";

type Row = Record<Language, string>;

// 解析 UX-COPY 的文案表：键列形如 `key`，其后三列依次是英、中、马。同一个键出现几次就收集几次。
function parseCopyTable(markdown: string): Map<string, Row[]> {
  const rows = new Map<string, Row[]>();
  for (const line of markdown.split(/\r?\n/)) {
    const cells = line.split("|").map((cell) => cell.trim());
    // "| `key` | en | zh | ms | 提示 |" 按 | 切开后首尾是空串，中间五格。
    const [lead, keyCell, en, zh, ms] = cells;
    if (lead !== "" || keyCell === undefined || en === undefined || zh === undefined || ms === undefined) {
      continue;
    }
    const key = /^`([a-z_]+\.[a-z0-9_]+)`$/.exec(keyCell)?.[1];
    if (!key) {
      continue;
    }
    const existing = rows.get(key) ?? [];
    existing.push({ en, zh, ms });
    rows.set(key, existing);
  }
  return rows;
}

const docRows = parseCopyTable(uxCopy);

function docRow(key: string): Row {
  const found = docRows.get(key) ?? [];
  const [row] = found;
  if (found.length !== 1 || !row) {
    throw new Error(`${key} must appear exactly once in docs/UX-COPY.md, found ${found.length}`);
  }
  return row;
}

function variables(text: string): string[] {
  return [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1] ?? "").sort();
}

describe("copy dictionary against docs/UX-COPY.md", () => {
  // 解析本身的对照：表里应有上百个键，且已知的一行解析正确；解析失效时下面的逐字比较不会空转通过。
  it("parses the copy tables of the document", () => {
    expect(docRows.size).toBeGreaterThan(100);
    expect(docRow("common.nav_cart")).toEqual({ en: "Cart ({count})", zh: "购物车（{count}）", ms: "Troli ({count})" });
  });

  // 验收：字典里每个键都在 UX-COPY 中，英、中、马三列逐字相同（不自行编写文案）。
  it.each(Object.keys(COPY))("%s matches the document in all three languages", (key) => {
    expect(COPY[key as CopyKey]).toEqual(docRow(key));
  });

  // UX-COPY「约定」：每条文案三列均不留空，三种语言保留同名变量。
  it.each(Object.keys(COPY))("%s has three non-empty columns with the same variables", (key) => {
    const entry = COPY[key as CopyKey];
    for (const language of LANGUAGES) {
      expect(entry[language].trim()).not.toBe("");
      expect(variables(entry[language])).toEqual(variables(entry.en));
    }
  });
});

describe("formatCopy", () => {
  // UX-COPY「约定」：{…} 为运行时替换的变量。用文档里真实带变量的一条（购物车数量）验证三种语言。
  it("replaces variables in every language column", () => {
    const row = docRow("common.nav_cart");
    expect(formatCopy(row.en, { count: 3 })).toBe("Cart (3)");
    expect(formatCopy(row.zh, { count: 3 })).toBe("购物车（3）");
    expect(formatCopy(row.ms, { count: 3 })).toBe("Troli (3)");
  });

  it("replaces every occurrence and accepts strings", () => {
    expect(formatCopy("{a} and {a} or {b}", { a: "x", b: 2 })).toBe("x and x or 2");
  });

  // 缺变量时不能把 {变量} 原样显示给访客。
  it("throws when a variable is missing", () => {
    expect(() => formatCopy("Cart ({count})")).toThrow("count");
  });

  it("leaves text without variables unchanged", () => {
    expect(translate("zh", "privacy.title")).toBe("隐私说明");
    expect(translate("ms", "common.nav_privacy")).toBe("Privasi");
  });
});
