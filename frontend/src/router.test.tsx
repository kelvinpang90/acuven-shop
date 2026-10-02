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
  // SHOP-TASK-017 验收第 2 条「路由表仍是字符串常量表，动态段写成模式（如 /products/:slug）」：详情页进表；其余页面尚未实现，不在表里。
  it("has the home page, the product list, the product detail and the privacy page", () => {
    expect([...ROUTE_PATHS]).toEqual(["/", "/products", "/products/:slug", "/privacy"]);
    expect(isRoutePath("/privacy")).toBe(true);
    expect(isRoutePath("/products")).toBe(true);
    expect(isRoutePath("/products/:slug")).toBe(true);
    expect(isRoutePath("/track")).toBe(false);
    expect(isRoutePath("/cart")).toBe(false);
  });

  // 验收：未知路径替换为首页。动态段不接受空段、多出的段、「.」「..」、含 : 的段（如模式本身）与坏的百分号编码。
  it.each([
    "/nope",
    "/products/",
    "/products/tee/",
    "/products/tee/more",
    "/products/:slug",
    "/products/..",
    "/products/a b",
    "/products/%E0%A4",
    "/track",
    "/privacy/",
    "/PRIVACY",
    "",
  ])("resolves %j to the home page", (path) => {
    expect(resolvePath(path)).toBe("/");
  });

  // SHOP-TASK-017 验收第 2 条「由 router.tsx 匹配并向页面提供 slug，站内链接与当前路径仍是实际路径」。
  it("matches a product path and gives the page its slug", () => {
    expect(matchRoute("/products/crew-neck-tee")).toEqual({ route: "/products/:slug", params: { slug: "crew-neck-tee" } });
    expect(matchRoute("/products/caf%C3%A9")).toEqual({ route: "/products/:slug", params: { slug: "café" } });
    expect(matchRoute("/products")).toEqual({ route: "/products", params: {} });
    expect(resolveHref("/products/tee?x=1")).toEqual({
      route: "/products/:slug",
      path: "/products/tee",
      params: { slug: "tee" },
      search: "?x=1",
    });
  });

  // 同一条：详情页链接由 slug 生成，生成的路径能匹配回同一个 slug。
  it.each(["crew-neck-tee", "café", "a/b", "it's (new)!", "50%"])("round-trips the slug %j through the product path", (slug) => {
    const path = productPath(slug);
    expect(path.startsWith("/products/")).toBe(true);
    expect(matchRoute(path)?.params).toEqual({ slug });
  });

  // SHOP-TASK-015 验收第 5 条「…放在查询参数里（可分享…）」：已知路径保留查询串，未知路径连同查询串换成首页。
  it("keeps the query string of known paths only", () => {
    const list = { route: "/products", path: "/products", params: {} };
    expect(resolveHref("/products?q=tee&category=bags")).toEqual({ ...list, search: "?q=tee&category=bags" });
    expect(resolveHref("/products")).toEqual({ ...list, search: "" });
    expect(resolveHref("/products?")).toEqual({ ...list, search: "" });
    expect(resolveHref("/products?q=tee#top")).toEqual({ ...list, search: "?q=tee" });
    expect(resolveHref("/nope?q=tee")).toEqual({ route: "/", path: "/", params: {}, search: "" });
  });

  // SHOP-TASK-015 验收第 5 条「商品列表 P02 路径为 /products」「放在查询参数里」：带查询串时仍渲染列表页。
  it("renders the product list for /products with a query string", () => {
    const html = render("/products?category=bags");
    expect(html).toContain(COPY["list.title"].en);
    expect(html).not.toContain(COPY["home.hero_title"].en);
  });

  it("renders the home page for an unknown path", () => {
    const html = render("/track/order");
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

  // 验收：路由表是唯一来源，站内链接只指向表里的路径（动态段填入实际值）；路径与查询参数里没有订单号或电话。
  // 查询参数只允许商品列表的条件（如首页分类链接 /products?category=…）。表里的模式本身不是可访问的地址，另加一个实际的详情路径。
  it.each([...ROUTE_PATHS, "/products/crew-neck-tee"])("links on %s point only to routes in the table", (path) => {
    const hrefs = [...render(path).matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      const [pathname = "", search = ""] = href.replace(/&amp;/g, "&").split("?");
      expect(matchRoute(pathname), href).not.toBeNull();
      for (const key of new URLSearchParams(search).keys()) {
        expect(PRODUCT_LIST_KEYS, href).toContain(key);
      }
    }
  });
});
