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
    const designSource = await readBytes("../../../docs/design/tokens/acuven-shop.css");
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

describe("site.css", () => {
  // 验收：site.css 只放布局与显隐规则，只用设计变量，不写颜色、字体或圆角。
  it("sets no colours, fonts or radii", async () => {
    const css = new TextDecoder().decode(await readBytes("./site.css")).replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(rgba?|hsla?|oklch|color-mix)\(/i);
    expect(css).not.toMatch(/(^|[\s;{])(color|background|background-color|border-color|fill|stroke|font|font-family|font-size|font-weight|border-radius)\s*:/i);
  });

  // 验收：不使用只供视觉稿的 acs--phone；手机差异写在 767px 及以下的媒体查询里。
  it("does not use the mockup-only acs--phone class", async () => {
    const css = new TextDecoder().decode(await readBytes("./site.css"));
    expect(css).not.toContain("acs--phone");
    expect(css).toContain("@media (max-width: 767px)");
    expect(css).not.toMatch(/min-width:\s*768px/);
  });
});
