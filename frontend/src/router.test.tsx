import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";
import { COPY } from "./i18n/copy";
import {
  ROUTE_PATHS,
  adminOrderPath,
  adminRefundPath,
  adminRefundsOrderPath,
  canonicalizeLocation,
  guestPath,
  isPlainLeftClick,
  isRoutePath,
  isSourcePath,
  matchRoute,
  nextSource,
  productPath,
  pushPath,
  replacePath,
  resolveHref,
  resolvePath,
  settleLocation,
  VERIFIED_REDIRECT_MS,
  verifiedPath,
} from "./router";
import type { BrowserLike, RoutePath } from "./router";

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
  // SHOP-TASK-047 验收第 5 条「路由 /admin/orders（frontend/src/router.tsx 路由表…加一项）」：后台订单页进表，在后台登录之后；
  // SHOP-TASK-055 验收第 2 条「路由 /admin/orders/:id（frontend/src/router.tsx 路由表…各加一项，router.test.tsx 的路由表断言加上它）」：
  // 订单详情以模式进表，在后台订单之后；
  // SHOP-TASK-056 验收第 2 条「路由 /admin/refunds 与 /admin/refunds/order/:orderId（frontend/src/router.tsx 路由表…各加两项，
  // frontend/src/router.test.tsx 的路由表断言加上它们）」：退款申请列表与按订单筛选的列表进表，在订单详情之后；
  // SHOP-TASK-057 验收第 2 条「路由 /admin/refunds/:id（frontend/src/router.tsx…与 frontend/src/router.test.tsx 各加一项）」：
  // 退款详情以模式进表，在按订单筛选的列表之后；
  // SHOP-TASK-059 验收第 2 条「路由 /admin/stock-resets（frontend/src/router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项）」：
  // 库存重置结果进表，在退款详情之后；
  // SHOP-TASK-062 验收第 2 条「路由 /admin/store-design（frontend/src/router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项）」：
  // 店铺装修进表，在库存重置结果之后；其余页面尚未实现，不在表里。
  // SHOP-TASK-100 验收第 2 条：优惠券页进表，在退款详情之后。
  // SHOP-TASK-137 验收第 2 条「路由 /admin/settings（router.tsx、App.tsx 与 router.test.tsx 各加一项）」：站点设置进表，在店铺装修之后。
  // SHOP-TASK-146 验收第 2 条「路由 /login 加进 frontend/src/router.tsx 的路由表」：会员登录进表，在隐私说明之后、后台页之前；
  // 原来断言 /login 不在表里的一行随之改为在表里，忘记密码与注册仍不在表里。
  // SHOP-TASK-147 验收第 2 条「路由 /forgot-password 加进 frontend/src/router.tsx 的路由表」：忘记密码进表，在会员登录之后；
  // 原来断言 /forgot-password 不在表里的一行随之改为在表里，注册仍不在表里。
  // SHOP-TASK-148 验收第 2 条「路由 /register 加进 frontend/src/router.tsx 的路由表」：注册进表，在忘记密码之后；
  // 原来断言 /register 不在表里的一行随之改为在表里。
  it("has the home page, the product list, the product detail, the cart, the checkout, the payment pages, the order lookup pages, the refund page, the privacy page, the member login, the password reset, the register page and the admin pages", () => {
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
      "/login",
      "/forgot-password",
      "/register",
      "/admin/login",
      "/admin/orders",
      "/admin/orders/:id",
      "/admin/refunds",
      "/admin/refunds/order/:orderId",
      "/admin/refunds/:id",
      "/admin/coupons",
      "/admin/stock-resets",
      "/admin/store-design",
      "/admin/settings",
    ]);
    expect(isRoutePath("/admin/settings")).toBe(true);
    expect(isRoutePath("/admin/coupons")).toBe(true);
    expect(isRoutePath("/admin/store-design")).toBe(true);
    expect(isRoutePath("/admin/stock-resets")).toBe(true);
    expect(isRoutePath("/admin/refunds/7")).toBe(true);
    expect(isRoutePath("/admin/login")).toBe(true);
    expect(isRoutePath("/admin/orders")).toBe(true);
    expect(isRoutePath("/admin/orders/42")).toBe(true);
    expect(isRoutePath("/admin/refunds")).toBe(true);
    expect(isRoutePath("/admin/refunds/order/42")).toBe(true);
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
    expect(isRoutePath("/login")).toBe(true);
    expect(isRoutePath("/forgot-password")).toBe(true);
    expect(isRoutePath("/register")).toBe(true);
  });

  // SHOP-TASK-147 验收第 2 条「页面为 frontend/src/pages/ForgotPasswordPage.tsx…套前台框架」与「读取完成前不渲染 V1、标 aria-busy」：
  // /forgot-password 在前台框架里渲染重设页，服务端渲染时站点设置还没读到，只有标题，内容区为空并标 aria-busy，没有号码与密码输入。
  it("renders the password reset page for /forgot-password inside the site frame", () => {
    const html = render("/forgot-password");
    expect(html).toContain(COPY["common.demo_banner"].en);
    expect(html).toContain(`<h1 class="acs-display-l">${COPY["auth.reset_title"].en}</h1>`);
    expect(html).toContain(`<div class="site-reset__body" aria-busy="true"></div>`);
    expect(html).not.toContain(`type="tel"`);
    expect(html).not.toContain(`type="password"`);
  });

  // SHOP-TASK-147 验收第 4 条「新密码与 csrf_token 只在页面状态与请求里，不进地址、日志或浏览器存储」：
  // 重设页的路径不带任何段值，多出一段（如把号码或新密码放进路径）不匹配、按未知路径落到首页。
  it("gives the password reset page no path parameters", () => {
    expect(matchRoute("/forgot-password")).toEqual({ pattern: "/forgot-password", params: {} });
    for (const path of ["/forgot-password/", "/forgot-password/60123456789", "/forgot-password/reset"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // SHOP-TASK-146 验收第 2 条「页面为 frontend/src/pages/LoginPage.tsx…套前台框架」与「读取完成之前只显示标题、★ 提示与两个页签，页签内容区标 aria-busy、不渲染表单与 V1」：
  // /login 在前台框架里渲染登录页，服务端渲染时站点设置还没读到，没有表单与号码输入；页头的 common.nav_login 指向 /login 并标为当前。
  it("renders the member login page for /login inside the site frame", () => {
    const html = render("/login");
    expect(html).toContain(COPY["common.demo_banner"].en);
    expect(html).toContain(`<h1 class="acs-display-l">${COPY["auth.login_title"].en}</h1>`);
    expect(html).toContain(`<div class="site-login__panels" aria-busy="true"></div>`);
    expect(html).not.toContain("<form class=\"site-login__form\"");
    expect(html).not.toContain(`type="password"`);
    expect(html).toContain(`<a aria-current="page" href="/login">${COPY["common.nav_login"].en}</a>`);
  });

  // 路径里不带段值（号码与密码只在页面内存与请求体里）：多出一段不匹配、按未知路径落到首页；/admin/login 仍是后台登录。
  it("gives the member login page no path parameters", () => {
    expect(matchRoute("/login")).toEqual({ pattern: "/login", params: {} });
    expect(matchRoute("/admin/login")).toEqual({ pattern: "/admin/login", params: {} });
    for (const path of ["/login/", "/login/60123456789", "/login/sms"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // SHOP-TASK-100 验收第 2 条：/admin/coupons 用后台框架渲染，当前项为优惠券，不套前台框架。
  it("renders the admin coupons page", () => {
    const html = render("/admin/coupons");
    expect(html).toContain(COPY["admin.demo_banner"].en);
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).toMatch(new RegExp(`aria-current="page"[^>]*>${COPY["admin.nav_coupons"].en}</a>`));
    expect(html).not.toContain(COPY["common.demo_banner"].en);
  });

  // SHOP-TASK-030 验收第 2 条「订单号与 CSRF 令牌不进任何路径、查询参数」：退款申请页的路径不带任何段值，多出一段（如把订单号放进路径）不匹配、按未知路径落到首页。
  it("gives the refund page no path parameters", () => {
    expect(matchRoute("/track/order/refund")).toEqual({ pattern: "/track/order/refund", params: {} });
    for (const path of ["/track/order/refund/", "/track/order/refund/B6TN2RJD8K4M0QXZ", "/track/refund"]) {
      expect(isRoutePath(path), path).toBe(false);
      expect(resolvePath(path)).toBe("/");
    }
  });

  // UX A02「状态与补充（0.9）」「订单详情有自己的网址，路径带订单的内部 ID，订单号不进网址」与 SHOP-TASK-055 验收第 2 条：
  // /admin/orders/<内部 ID> 匹配订单详情的模式并把 ID 交给页面；adminOrderPath 由内部 ID 生成同一网址；
  // 多出一段或去掉 ID 不匹配详情（是否为合法 ID 由页面判断，见 AdminOrderDetail.test.tsx）。
  it("matches the admin order detail by internal ID", () => {
    expect(matchRoute("/admin/orders/42")).toEqual({ pattern: "/admin/orders/:id", params: { id: "42" } });
    expect(adminOrderPath(42)).toBe("/admin/orders/42");
    expect(matchRoute(adminOrderPath(2147483647))).toEqual({ pattern: "/admin/orders/:id", params: { id: "2147483647" } });
    expect(matchRoute("/admin/orders")).toEqual({ pattern: "/admin/orders", params: {} });
    for (const path of ["/admin/orders/42/status", "/admin/orders/", "/admin/orders/42/"]) {
      expect(isRoutePath(path), path).toBe(false);
    }
  });

  // UX A03「状态与补充（0.9）」「从 A02 的待审退款数进入时只列该订单的申请」与 SHOP-TASK-056 验收第 5 条「以路径里的订单内部 ID 作为 order_id 查询」：
  // /admin/refunds/order/<内部 ID> 匹配按订单筛选的模式并把段值交给页面（是否为合法 ID 由页面判断，见 AdminRefundsPage.test.tsx）；
  // 少一段、多一段或以斜杠结尾都不匹配；/admin/refunds 不带段值。
  // SHOP-TASK-057 验收第 2 条「加入后 /admin/refunds/order 匹配这个模式（段值为 order，页面按不合法 ID 处理）…只许把它从…
  // 「不匹配任何路由」的路径列表里移出，并断言它匹配 /admin/refunds/:id、段值为 order」：/admin/refunds/order 已移出下面的列表。
  it("matches the admin refund list filtered by order internal ID", () => {
    expect(matchRoute("/admin/refunds")).toEqual({ pattern: "/admin/refunds", params: {} });
    expect(matchRoute("/admin/refunds/order/42")).toEqual({ pattern: "/admin/refunds/order/:orderId", params: { orderId: "42" } });
    expect(matchRoute("/admin/refunds/order")).toEqual({ pattern: "/admin/refunds/:id", params: { id: "order" } });
    for (const path of ["/admin/refunds/order/", "/admin/refunds/order/42/", "/admin/refunds/order/42/x", "/admin/refunds/"]) {
      expect(isRoutePath(path), path).toBe(false);
    }
  });

  // UX A03「状态与补充（0.9）」「退款详情有自己的网址，路径带申请的内部 ID」与 Kelvin 2026-10-07 决定「退款详情 /admin/refunds/<内部 ID>」：
  // /admin/refunds/<内部 ID> 匹配退款详情的模式并把 ID 交给页面；adminRefundPath 与 adminRefundsOrderPath 由内部 ID 生成网址；
  // 多出一段或以斜杠结尾不匹配详情（是否为合法 ID 由页面判断，见 AdminRefundDetail.test.tsx）。
  it("matches the admin refund detail by internal ID", () => {
    expect(matchRoute("/admin/refunds/7")).toEqual({ pattern: "/admin/refunds/:id", params: { id: "7" } });
    expect(adminRefundPath(7)).toBe("/admin/refunds/7");
    expect(matchRoute(adminRefundPath(2147483647))).toEqual({ pattern: "/admin/refunds/:id", params: { id: "2147483647" } });
    expect(adminRefundsOrderPath(42)).toBe("/admin/refunds/order/42");
    expect(matchRoute(adminRefundsOrderPath(42))).toEqual({ pattern: "/admin/refunds/order/:orderId", params: { orderId: "42" } });
    for (const path of ["/admin/refunds/7/approve", "/admin/refunds/7/"]) {
      expect(isRoutePath(path), path).toBe(false);
    }
  });

  // SHOP-TASK-057 验收第 2 条「页面表映射到退款页」：/admin/refunds/<内部 ID> 与 /admin/refunds/order 渲染后台退款页的框架
  // （会话请求返回前内容区标 aria-busy），当前导航项为退款；不套前台框架。
  it.each(["/admin/refunds/7", "/admin/refunds/order"])("renders the admin refunds page for the detail path %s", (path) => {
    const html = render(path);
    expect(html).toContain(COPY["admin.demo_banner"].en);
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).toMatch(new RegExp(`aria-current="page"[^>]*>${COPY["admin.nav_refunds"].en}</a>`));
    expect(html).not.toContain(COPY["common.demo_banner"].en);
  });

  // SHOP-TASK-056 验收第 2 条「页面表各加两项…用 SHOP-TASK-047 的框架渲染，当前导航项为退款」：两条路径都渲染后台退款页的框架
  // （会话请求返回前内容区标 aria-busy），当前导航项为退款；不套前台框架。
  it.each(["/admin/refunds", "/admin/refunds/order/42"])("renders the admin refunds page for %s", (path) => {
    const html = render(path);
    expect(html).toContain(COPY["admin.demo_banner"].en);
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).toMatch(new RegExp(`aria-current="page"[^>]*>${COPY["admin.nav_refunds"].en}</a>`));
    expect(html).not.toContain(COPY["common.demo_banner"].en);
  });

  // SHOP-TASK-055 验收第 2 条「页面表映射到订单页」：/admin/orders/<内部 ID> 渲染后台订单页的框架（会话请求返回前内容区标 aria-busy），
  // 当前导航项为订单；不套前台框架。
  it("renders the admin orders page for an order detail path", () => {
    const html = render("/admin/orders/42");
    expect(html).toContain(COPY["admin.demo_banner"].en);
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).toMatch(new RegExp(`aria-current="page"[^>]*>${COPY["admin.nav_orders"].en}</a>`));
    expect(html).not.toContain(COPY["common.demo_banner"].en);
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

  // SHOP-TASK-146 验收第 5 条「来源页为进入 /login 之前所在的站内前台页面…只记路径、不含查询参数…离开的是后台页或 /login、/register、/forgot-password 时不更新」
  // 与 HANDOFF 0.58 第（d）条「路由只在内存里记下上一个前台路径」：push、replace 与前进后退都经同一规则 nextSource，以切换前的路径为准。
  // 依次走过的路径（RouteLocation 的 path 已不含查询串）：首页 → 商品列表 → 结账 → 登录 → 后台登录 → 登录，来源一直是结账；
  // 同一路径只换查询串时不算离开；从登录页回到隐私说明再离开，来源才换成隐私说明。
  it("remembers the last storefront path left, skipping admin and member sign-in pages", () => {
    const visits: RoutePath[] = ["/", "/products", "/products", "/checkout", "/login", "/admin/login", "/login", "/privacy", "/cart"];
    const sources: (RoutePath | null)[] = [];
    let source: RoutePath | null = null;
    for (const [index, to] of visits.entries()) {
      const from = visits[index - 1];
      if (from !== undefined) {
        source = nextSource(source, from, to);
      }
      sources.push(source);
    }
    expect(sources).toEqual([null, "/", "/", "/products", "/checkout", "/checkout", "/checkout", "/checkout", "/privacy"]);
  });

  // 同一条「离开的是后台页或 /login、/register、/forgot-password 时不更新，所以经登录页再进注册或重设页仍保留原来的来源」：
  // 三个会员页与以 /admin 开头的路径都不可作来源，其余前台页可以。
  it("treats admin pages and the member login, register and reset pages as no source", () => {
    for (const path of ["/login", "/register", "/forgot-password", "/admin/login", "/admin/orders/42", "/admin/settings"]) {
      expect(isSourcePath(path), path).toBe(false);
    }
    for (const path of ["/", "/products", "/products/crew-neck-tee", "/cart", "/checkout", "/pay", "/track/order", "/privacy"]) {
      expect(isSourcePath(path), path).toBe(true);
    }
    expect(nextSource("/cart", "/admin/orders", "/login")).toBe("/cart");
    expect(nextSource(null, "/login", "/")).toBeNull();
  });

  // 同一条「成功后的去向（来源 → 路径）…没有记下时去首页 P01」：来源为前台页时回到来源；没有来源、来源为后台页或登录页本身时去首页。
  it("sends a verified member back to the source page or the home page", () => {
    expect(verifiedPath("/checkout")).toBe("/checkout");
    expect(verifiedPath("/products/crew-neck-tee")).toBe("/products/crew-neck-tee");
    expect(verifiedPath(null)).toBe("/");
    expect(verifiedPath("/admin/orders")).toBe("/");
    expect(verifiedPath("/login")).toBe("/");
  });

  // 同一条「auth.continue_guest 的去向（来源为 /checkout 时 /checkout，否则 /）」与 HANDOFF 0.58 第（d）条「auth.continue_guest 在来源为结账时回结账，否则去首页」。
  it("sends continue as guest back to the checkout only when the checkout is the source", () => {
    expect(guestPath("/checkout")).toBe("/checkout");
    expect(guestPath("/cart")).toBe("/");
    expect(guestPath("/admin/login")).toBe("/");
    expect(guestPath(null)).toBe("/");
  });

  // HANDOFF 0.58 第（d）条「登录、注册成功后显示 auth.verified_* 1.5 秒」：常量为 1500 毫秒。
  it("waits 1.5 seconds before leaving a verified page", () => {
    expect(VERIFIED_REDIRECT_MS).toBe(1500);
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
