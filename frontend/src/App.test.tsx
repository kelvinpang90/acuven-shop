import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";

// 需求：所有商品、金额、支付与发货都是演示，页面须持续、清楚地标注这一点。
// 外壳页是第一张对外页面，演示标注从这里开始就不能缺。
describe("App shell", () => {
  it("labels the site as a demo", () => {
    expect(renderToStaticMarkup(<App />)).toContain("演示站");
  });
});
