import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";
import { COPY } from "./i18n/copy";
import {
  ROUTE_PATHS,
  canonicalizeLocation,
  isPlainLeftClick,
  isRoutePath,
  matchRoute,
  productPath,
  pushPath,
  replacePath,
  resolveHref,
  resolvePath,
  settleLocation,
} from "./router";
import type { BrowserLike } from "./router";

function fakeBrowser(pathname: string, search = "") {
  const calls: string[] = [];
  const setUrl = (url: string) => {
    const mark = url.indexOf("?");
    browser.location.pathname = mark < 0 ? url : url.slice(0, mark);
    browser.location.search = mark < 0 ? "" : url.slice(mark);
  };
  const browser: BrowserLike = {
    location: { pathname, search },
    history: {
      pushState: (_data, _unused, url) => {
        calls.push(`push ${url}`);
        setUrl(url);
      },
      replaceState: (_data, _unused, url) => {
        calls.push(`replace ${url}`);
        setUrl(url);
      },
    },
    scrollTo: (x, y) => {
      calls.push(`scroll ${x},${y}`);
    },
  };
  return { browser, calls };
}

const plainClick = {
  button: 0,
  metaKey: false,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  defaultPrevented: false,
};

const PRODUCT_LIST_KEYS = ["q", "category", "option", "sort", "page"];

function render(path: string): string {
  return renderToStaticMarkup(<App initialPath={path} storage={null} />);
}

describe("route table", () => {
  // SHOP-TASK-015 验收第 5 条「加进 frontend/src/router.tsx 的路由表」与 SHOP-TASK-017 验收第 2 条「路由表仍是字符串常量表，动态段写成模式（如 /products/:slug）」：
  // 详情页以模式进表；SHOP-TASK-018 验收第 2 条「路由 /cart」：购物车页进表；
  // SHOP-TASK-024 验收第 2 条「路由 /pay（P06）与 /pay/result（P07）」：两页进表，路径里没有动态段；
  // SHOP-TASK-025 验收第 2 条「路由 /checkout」：结账页进表，在购物车之后；
  // SHOP-TASK-028 验收第 2 条「路由 /track（P08）与 /track/order（P09 查单模式）」：两页进表，在支付页之后；
  // SHOP-TASK-030 验收第 2 条「路由 /track/order/refund（P10 查单模式）」：进表，在订单详情之后；
  // SHOP-TASK-038 验收第 2 条「路由 /admin/login」：后台登录页进表，在隐私说明之后；
  // SHOP-TASK-047 验收第 5 条「路由 /admin/orders（frontend/src/router.tsx 路由表…加一项）」：后台订单页进表，在后台登录之后；其余页面尚未实现，不在表里。
  it("has the home page, the product list, the product detail, the cart, the checkout, the payment pages, the order lookup pages, the refund page, the privacy page and the admin pages", () => {
    expect([...ROUTE_PATHS]).toEqual([
      "/",
      "/products",
      "/products/:slug",
      "/cart",
      "/checkout",
      "/pay",
      "/pay/result",
      "/track",
      "/track/order",
      "/track/order/refund",
      "/privacy",
      "/admin/login",
      "/admin/orders",
    ]);
    expect(isRoutePath("/admin/login")).toBe(true);
    expect(isRoutePath("/admin/orders")).toBe(true);
    expect(isRoutePath("/privacy")).toBe(true);
    expect(isRoutePath("/products")).toBe(true);
    expect(isRoutePath("/products/crew-neck-tee")).toBe(true);
    expect(isRoutePath("/cart")).toBe(true);
    expect(isRoutePath("/checkout")).toBe(true);
    expect(isRoutePath("/pay")).toBe(true);
    expect(isRoutePath("/pay/result")).toBe(true);
    expect(isRoutePath("/track")).toBe(true);
    expect(isRoutePath("/track/order")).toBe(true);
    expect(isRoutePath("/track/order/refund")).toBe(true);
    expect(isRoutePath("/account/order/refund")).toBe(false);
    expect(isRoutePath("/login")).toBe(false);
  });

  // SHOP-TASK-030 验收第 2 条「订单号与 CSRF 令牌不进任何路径、查询参数」：退款申请页的路径不带任何段值，多出一段（如把订单号放进路径）不匹配、按未知路径落到首页。
  it("gives the refund page no path parameters", () => {
    expect(matchRoute("/track/order/refund")).toEqual({ pattern: "/track/order/refund", params: {} });
    for (const path of ["/track/order/refund/", "/track/order/refund/B6TN2RJD8K4M0QXZ", "/track/refund"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // UX 页面地图「P10 退款申请 /track/order/refund」：渲染退款申请页；接口返回之前（服务端渲染）主体为空并标 aria-busy，不出现订单内容或授权过期提示。
  it("renders the refund page for /track/order/refund", () => {
    const html = render("/track/order/refund");
    expect(html).toContain(`<main class="site-refund" aria-busy="true"></main>`);
    expect(html).not.toContain(COPY["order.session_expired"].en);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
  });

  // SHOP-TASK-028 验收第 2 条「订单号、电话与 CSRF 令牌不进任何路径、查询参数」：订单查询与订单详情的路径不带任何段值，
  // 多出一段（如把订单号或电话放进路径）不匹配、按未知路径落到首页。
  it("gives the order lookup pages no path parameters", () => {
    expect(matchRoute("/track")).toEqual({ pattern: "/track", params: {} });
    expect(matchRoute("/track/order")).toEqual({ pattern: "/track/order", params: {} });
    for (const path of ["/track/B6TN2RJD8K4M0QXZ", "/track/order/B6TN2RJD8K4M0QXZ", "/track/+60123456789", "/track/", "/track/order/"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // UX 页面地图「P08 订单查询」「P09 订单详情」：/track 渲染查单表单，输入框为空；/track/order 在接口返回之前（服务端渲染）主体为空并标 aria-busy，
  // 不出现订单内容或授权过期提示。
  it("renders the order lookup pages for /track and /track/order", () => {
    const track = render("/track");
    expect(track).toContain(`<h1 class="acs-display-l">${COPY["lookup.title"].en}</h1>`);
    expect(track).not.toContain(COPY["home.demo_hint"].en);
    const order = render("/track/order");
    expect(order).toContain(`<main class="site-order" aria-busy="true"></main>`);
    expect(order).not.toContain(COPY["order.session_expired"].en);
    expect(order).not.toContain(COPY["home.demo_hint"].en);
  });

  // SHOP-TASK-025 验收第 2 条「收货资料与电话只保存在页面内存，不写进网址」：结账页路径没有段值，多出一段（如把电话放进路径）不匹配、按未知路径落到首页。
  it("gives the checkout page no path parameters", () => {
    expect(matchRoute("/checkout")).toEqual({ pattern: "/checkout", params: {} });
    for (const path of ["/checkout/", "/checkout/60123456789", "/checkout/step-3"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // UX 页面地图「P05 结账 /checkout」：/checkout 渲染结账页；服务端渲染时没有本浏览器购物车，为空购物车（cart.empty 与 cart.continue），没有表单。
  it("renders the checkout page for /checkout", () => {
    const html = render("/checkout");
    expect(html).toContain(`<h1 class="acs-display-l">${COPY["checkout.title"].en}</h1>`);
    expect(html).toContain(COPY["cart.empty"].en);
    expect(html).toContain(`href="/products">${COPY["cart.continue"].en}</a>`);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
  });

  // SHOP-TASK-024 验收第 2 条「订单号、电话与 CSRF 令牌不进任何路径、查询参数」：支付页与结果页的路径不带任何段值，
  // 多出一段（如把订单号放进路径）不匹配、按未知路径落到首页。
  it("gives the payment pages no path parameters", () => {
    expect(matchRoute("/pay")).toEqual({ pattern: "/pay", params: {} });
    expect(matchRoute("/pay/result")).toEqual({ pattern: "/pay/result", params: {} });
    for (const path of ["/pay/B6TN2RJD00000000", "/pay/result/B6TN2RJD00000000", "/pay/", "/pay/result/"]) {
      expect(isRoutePath(path), path).toBe(false);
    }
  });

  // UX 页面地图「P06 模拟支付 /pay」「P07 模拟支付结果 /pay/result」：两条路径分别渲染两页；接口返回之前（服务端渲染）主体为空并标 aria-busy，
  // 不出现订单内容或凭据过期提示。
  it.each(["/pay", "/pay/result"])("renders the payment page for %s", (path) => {
    const html = render(path);
    expect(html).toContain(`<main class="site-pay" aria-busy="true"></main>`);
    expect(html).not.toContain(COPY["pay.session_expired"].en);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
  });

  // UX 页面地图「P04 购物车 /cart」：/cart 渲染购物车页（服务端渲染时没有本浏览器购物车，为空购物车）。
  it("renders the cart page for /cart", () => {
    const html = render("/cart");
    expect(html).toContain(`<h1 class="acs-display-l">${COPY["cart.title"].en}</h1>`);
    expect(html).toContain(COPY["cart.empty"].en);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
  });

  // SHOP-TASK-017 验收第 2 条「由 router.tsx 匹配并向页面提供 slug」：实际路径匹配到模式，段值交给页面。
  it("matches an actual detail path and provides the slug", () => {
    expect(matchRoute("/products/crew-neck-tee")).toEqual({ pattern: "/products/:slug", params: { slug: "crew-neck-tee" } });
    expect(matchRoute("/products")).toEqual({ pattern: "/products", params: {} });
    expect(matchRoute("/products/:slug")).toEqual({ pattern: "/products/:slug", params: { slug: ":slug" } });
  });

  // SHOP-TASK-017 验收第 2 条「动态段的值按路径段解码后交给页面…其余值不再按字符集校验」：%3A 得到「:」，a%20b 得到「a b」。
  it.each([
    ["/products/%3A", ":"],
    ["/products/a%20b", "a b"],
    ["/products/%E4%B8%AD", "中"],
    ["/products/a%2520b", "a%20b"],
  ])("decodes %s to the slug %j", (path, slug) => {
    expect(matchRoute(path)?.params).toEqual({ slug });
    expect(resolvePath(path)).toBe(path);
  });

  // SHOP-TASK-017 验收第 2 条「解码失败视为不匹配…解码后为空、为 . 或 ..、或含 / 的段不匹配、按未知路径落到首页」。
  it.each(["/products/%2E", "/products/%2e", "/products/%2E%2E", "/products/.", "/products/..", "/products/%2F", "/products/a%2Fb", "/products/%", "/products/%E4%B8", "/products/%zz"])(
    "does not match %s",
    (path) => {
      expect(matchRoute(path)).toBeNull();
      expect(isRoutePath(path)).toBe(false);
      expect(resolvePath(path)).toBe("/");
    },
  );

  // SHOP-TASK-017 验收第 2 条「站内链接…仍是实际路径」与「请求路径里只编码一次」的链接一侧：商品链接把 slug 编码一次，再解码回原值。
  it("builds detail links that decode back to the slug", () => {
    for (const slug of ["crew-neck-tee", "a b", ":", "a%20b", "中"]) {
      const path = productPath(slug);
      expect(matchRoute(path)?.params).toEqual({ slug });
    }
    expect(productPath("a b")).toBe("/products/a%20b");
    expect(productPath("a/b")).toBe("/products/a%2Fb");
  });

  // 验收：未知路径替换为首页。
  it.each(["/nope", "/products/", "/products/tee/", "/products/a/b", "/track/", "/track/orders", "/privacy/", "/PRIVACY", "/cart/", "/pay/", "/pay/results", ""])(
    "resolves %j to the home page",
    (path) => {
      expect(resolvePath(path)).toBe("/");
    },
  );

  // SHOP-TASK-015 验收第 5 条「…放在查询参数里（可分享…）」：已知路径保留查询串，未知路径连同查询串换成首页。
  it("keeps the query string of known paths only", () => {
    expect(resolveHref("/products?q=tee&category=bags")).toEqual({ path: "/products", search: "?q=tee&category=bags" });
    expect(resolveHref("/products")).toEqual({ path: "/products", search: "" });
    expect(resolveHref("/products?")).toEqual({ path: "/products", search: "" });
    expect(resolveHref("/products?q=tee#top")).toEqual({ path: "/products", search: "?q=tee" });
    expect(resolveHref("/nope?q=tee")).toEqual({ path: "/", search: "" });
  });

  // SHOP-TASK-015 验收第 5 条「商品列表 P02 路径为 /products」「放在查询参数里」：带查询串时仍渲染列表页。
  it("renders the product list for /products with a query string", () => {
    const html = render("/products?category=bags");
    expect(html).toContain(COPY["list.title"].en);
    expect(html).not.toContain(COPY["home.hero_title"].en);
  });

  // SHOP-TASK-017 验收第 2 条「当前路径仍是实际路径」与「接口返回之前页面主体不出现…面包屑与购买区等数据返回后才渲染」：
  // 详情路径渲染详情页的空主体，语言切换链接指向实际路径。
  it("renders the detail page for a detail path and keeps the actual path", () => {
    const html = render("/products/a%20b");
    expect(html).toContain(`<main class="site-detail" aria-busy="true"></main>`);
    expect(html).toContain(`href="/products/a%20b" lang="en"`);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
  });

  // SHOP-TASK-017 验收第 2 条「…不匹配、按未知路径落到首页」：渲染首页而不是详情页。
  it("renders the home page for a detail path that does not match", () => {
    const html = render("/products/%2F");
    expect(html).toContain(COPY["home.demo_hint"].en);
    expect(html).not.toContain("site-detail");
  });

  it("renders the home page for an unknown path", () => {
    const html = render("/account/order/refund");
    expect(html).toContain(COPY["home.demo_hint"].en);
    expect(html).not.toContain(COPY["privacy.intro"].en);
  });

  // 验收：未知路径在地址栏里替换（不新增历史记录）为首页；已知路径不动。
  it("replaces an unknown address with the home page", () => {
    const unknown = fakeBrowser("/nope");
    canonicalizeLocation(unknown.browser, resolvePath("/nope"));
    expect(unknown.calls).toEqual(["replace /"]);

    const known = fakeBrowser("/privacy");
    canonicalizeLocation(known.browser, "/privacy");
    expect(known.calls).toEqual([]);
  });

  // 验收：切换页面后回到页面顶部（站内链接与前进后退换页后都走这一步）。
  it("scrolls to the top after a page change", () => {
    const known = fakeBrowser("/privacy");
    settleLocation(known.browser, "/privacy");
    expect(known.calls).toEqual(["scroll 0,0"]);

    const unknown = fakeBrowser("/nope");
    settleLocation(unknown.browser, "/");
    expect(unknown.calls).toEqual(["replace /", "scroll 0,0"]);
  });
});

describe("in-site navigation", () => {
  // 验收：站内链接不整页刷新、前进后退可用——经 History 接口新增一条记录，而不是加载新页面。
  it("pushes a history entry instead of loading a page", () => {
    const { browser, calls } = fakeBrowser("/");
    expect(pushPath(browser, "/privacy")).toBe(true);
    expect(calls).toEqual(["push /privacy"]);
    expect(browser.location.pathname).toBe("/privacy");
  });

  // 点当前页面的链接不重复记历史，后退一次就能离开。
  it("does not push a duplicate entry for the current page", () => {
    const { browser, calls } = fakeBrowser("/privacy");
    expect(pushPath(browser, "/privacy")).toBe(false);
    expect(calls).toEqual([]);

    const list = fakeBrowser("/products", "?q=tee");
    expect(pushPath(list.browser, "/products", "?q=tee")).toBe(false);
    expect(list.calls).toEqual([]);
  });

  // SHOP-TASK-015 验收第 5 条「前进后退可用」：换筛选、排序或页码各记一条历史。
  it("pushes a history entry when only the query string changes", () => {
    const { browser, calls } = fakeBrowser("/products", "?q=tee");
    expect(pushPath(browser, "/products", "?q=tee&sort=price_asc")).toBe(true);
    expect(pushPath(browser, "/products")).toBe(true);
    expect(calls).toEqual(["push /products?q=tee&sort=price_asc", "push /products"]);
    expect(browser.location).toEqual({ pathname: "/products", search: "" });
  });

  // SHOP-TASK-017 验收第 2 条「商品不存在或未发布（接口 404）时替换为商品列表页」：替换当前历史记录，不新增一条。
  it("replaces the current entry when a page is swapped for another", () => {
    const { browser, calls } = fakeBrowser("/products/gone");
    replacePath(browser, "/products");
    expect(calls).toEqual(["replace /products"]);
    expect(browser.location).toEqual({ pathname: "/products", search: "" });
  });

  // 新标签页、新窗口等修饰键点击交给浏览器，不拦截。
  it("only intercepts plain left clicks", () => {
    expect(isPlainLeftClick(plainClick)).toBe(true);
    expect(isPlainLeftClick({ ...plainClick, button: 1 })).toBe(false);
    expect(isPlainLeftClick({ ...plainClick, metaKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...plainClick, ctrlKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...plainClick, shiftKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...plainClick, altKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...plainClick, defaultPrevented: true })).toBe(false);
  });

  // 验收：路由表是唯一来源，站内链接只指向表里的路径；路径与查询参数里没有订单号或电话。
  // 查询参数只允许商品列表的条件（如首页分类链接 /products?category=…）。
  it.each([...ROUTE_PATHS])("links on %s point only to routes in the table", (path) => {
    const hrefs = [...render(path).matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      const [pathname = "", search = ""] = href.replace(/&amp;/g, "&").split("?");
      expect(isRoutePath(pathname), href).toBe(true);
      for (const key of new URLSearchParams(search).keys()) {
        expect(PRODUCT_LIST_KEYS, href).toContain(key);
      }
    }
  });
});
