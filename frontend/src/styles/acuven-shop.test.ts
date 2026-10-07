import { describe, expect, it } from "vitest";

// 按字节读文件。
// - 不用 `?raw`：vitest 默认不处理 CSS，`.css?raw` 读到的是空字符串，两份空串比较也会“相同”。
// - node:fs 以运行时字符串动态取得、只声明用到的一个函数：tsconfig.app.json 不带 Node 类型，这里也不引入。
interface ReadFileSync {
  readFileSync(path: URL): Uint8Array;
}

async function readBytes(relativePath: string): Promise<Uint8Array> {
  const fsModuleName = "node:fs";
  const fs = (await import(/* @vite-ignore */ fsModuleName)) as ReadFileSync;
  return fs.readFileSync(new URL(relativePath, import.meta.url));
}

function firstDifference(a: Uint8Array, b: Uint8Array): number {
  const length = Math.min(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    if (a[i] !== b[i]) {
      return i;
    }
  }
  return a.length === b.length ? -1 : length;
}

describe("design tokens stylesheet", () => {
  // 验收：frontend/src/styles/acuven-shop.css 与 docs/design/tokens/acuven-shop.css 逐字节相同（不手改生成的文件）。
  it("is a byte-for-byte copy of docs/design/tokens/acuven-shop.css", async () => {
    const frontendCopy = await readBytes("./acuven-shop.css");
    const designSource = await readBytes(
      "../../../docs/design/tokens/acuven-shop.css",
    );
    // 对照：确实读到了生成的样式（含字体引入），不是两份空文件。
    expect(new TextDecoder().decode(designSource)).toContain("@font-face");
    expect(firstDifference(frontendCopy, designSource)).toBe(-1);
  });

  // 验收：由应用入口引入，site.css 在 acuven-shop.css 之后。
  it("is imported by the entry before site.css", async () => {
    const mainSource = new TextDecoder().decode(await readBytes("../main.tsx"));
    const tokens = mainSource.indexOf('import "./styles/acuven-shop.css";');
    const site = mainSource.indexOf('import "./styles/site.css";');
    expect(tokens).toBeGreaterThanOrEqual(0);
    expect(site).toBeGreaterThan(tokens);
  });
});

// 去掉注释后的 site.css：只检查规则本身，说明规则的注释不应触发规则。
async function readSiteRules(): Promise<string> {
  return new TextDecoder()
    .decode(await readBytes("./site.css"))
    .replace(/\/\*[\s\S]*?\*\//g, "");
}

// 在括号、方括号与引号之外按分隔符切开（`a:is(.x, .y)` 的逗号、`[aria-label="A B"]` 的空格不切）。
function splitTopLevel(
  text: string,
  isSeparator: (char: string) => boolean,
): string[] {
  const parts: string[] = [];
  let depth = 0;
  let quote = "";
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const char = text.charAt(i);
    if (quote) {
      if (char === "\\") i += 1;
      else if (char === quote) quote = "";
    } else if (char === '"' || char === "'") quote = char;
    else if (char === "(" || char === "[") depth += 1;
    else if (char === ")" || char === "]") depth -= 1;
    else if (depth === 0 && isSeparator(char)) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

// 最后一段复合选择器以 a 元素开头、且不带伪元素（`::x` 或旧写法 `:before` 等）时才算选中链接。
function selectsLink(selector: string): boolean {
  const compounds = splitTopLevel(selector.trim(), (char) =>
    /[\s>+~]/.test(char),
  );
  const last = compounds[compounds.length - 1] ?? "";
  const pseudos = splitTopLevel(last, (char) => char === ":").slice(1);
  return (
    /^a(?![\w-])/.test(last) &&
    !pseudos.some(
      (pseudo) =>
        pseudo === "" ||
        /^(before|after|first-line|first-letter)$/i.test(pseudo),
    )
  );
}

// 选择器组里每个选择器都选中链接时才算链接规则。
function isLinkRule(selectors: string): boolean {
  return splitTopLevel(selectors, (char) => char === ",").every(selectsLink);
}

// 只从链接规则里去掉 color: inherit，其他规则里的同一声明照样被下面的检查拦住。
function withoutLinkColorInherit(css: string): string {
  return css.replace(
    /([^{}]*)\{([^{}]*)\}/g,
    (rule, selectors: string, body: string) =>
      isLinkRule(selectors)
        ? `${selectors}{${body.replace(/(^|[\s;])color\s*:\s*inherit\s*(?=;|$)/gi, "$1")}}`
        : rule,
  );
}

describe("site.css", () => {
  // 验收：site.css 只放布局与显隐规则，只用设计变量，不写颜色、字体或圆角。
  // 例外（Kelvin 2026-10-07 决定）：链接可写 color: inherit 取父元素的颜色，不写颜色值与变量。
  it("sets no colours, fonts or radii", async () => {
    const css = withoutLinkColorInherit(await readSiteRules());
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(rgba?|hsla?|oklch|color-mix)\(/i);
    expect(css).not.toMatch(
      /(^|[\s;{])(color|background|[\w-]*-color|fill|stroke|font|font-[\w-]+|[\w-]*radius)\s*:/i,
    );
  });

  // 对照：例外只放过链接规则里的 color: inherit，非链接规则、其他取值与其他属性仍被拦住。
  it("allows color: inherit only on links", () => {
    const declared = /(^|[\s;{])color\s*:/i;
    expect(withoutLinkColorInherit(".x a { color: inherit; }")).not.toMatch(
      declared,
    );
    expect(
      withoutLinkColorInherit(
        "@media (max-width: 767px) { a.y:hover, .z > a { color: inherit } }",
      ),
    ).not.toMatch(declared);
    expect(
      withoutLinkColorInherit(
        'a:is(.x, .y), .z a[aria-label="Order details"] { color: inherit; }',
      ),
    ).not.toMatch(declared);
    expect(withoutLinkColorInherit("a::before { color: inherit; }")).toMatch(
      declared,
    );
    expect(withoutLinkColorInherit(".x a:after { color: inherit; }")).toMatch(
      declared,
    );
    expect(withoutLinkColorInherit("body { color: inherit; }")).toMatch(
      declared,
    );
    expect(withoutLinkColorInherit(".a { color: inherit; }")).toMatch(declared);
    expect(withoutLinkColorInherit("a, .x { color: inherit; }")).toMatch(
      declared,
    );
    expect(withoutLinkColorInherit("a { color: inherit !important; }")).toMatch(
      declared,
    );
    expect(withoutLinkColorInherit("a { color: var(--a-ink); }")).toMatch(
      declared,
    );
  });

  // 验收：不使用只供视觉稿的 acs--phone；手机差异写在 767px 及以下的媒体查询里。
  it("does not use the mockup-only acs--phone class", async () => {
    const css = await readSiteRules();
    expect(css).not.toContain("acs--phone");
    expect(css).toContain("@media (max-width: 767px)");
    expect(css).not.toMatch(/min-width:\s*768px/);
  });
});
