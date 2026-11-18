import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PreviewProvider, usePreview } from "./preview";

function Probe() {
  const preview = usePreview();
  return preview === null ? "none" : `${String(preview.promotionId)} ${preview.csrfToken}`;
}

describe("usePreview", () => {
  // DESIGN 1.23 第 11 条「预览不改变访客看到的内容」：前台平常的页面都在 Provider 之外，取值为 null，行为不变。
  it("is null outside the provider", () => {
    expect(renderToStaticMarkup(<Probe />)).toBe("none");
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：Provider 之内给出正在预览的活动 ID 与预览计价所带的 CSRF 令牌。
  it("gives the previewed promotion and CSRF token inside the provider", () => {
    const html = renderToStaticMarkup(
      <PreviewProvider preview={{ promotionId: 7, csrfToken: "csrf-abc" }}>
        <Probe />
      </PreviewProvider>,
    );
    expect(html).toBe("7 csrf-abc");
  });

  // 同一句：内层 Provider 覆盖外层，离开 Provider 的兄弟节点仍为 null。
  it("uses the nearest provider only", () => {
    const html = renderToStaticMarkup(
      <>
        <PreviewProvider preview={{ promotionId: 7, csrfToken: "outer" }}>
          <p>
            <PreviewProvider preview={{ promotionId: 9, csrfToken: "inner" }}>
              <Probe />
            </PreviewProvider>
          </p>
        </PreviewProvider>
        <p>
          <Probe />
        </p>
      </>,
    );
    expect(html).toBe("<p>9 inner</p><p>none</p>");
  });
});
