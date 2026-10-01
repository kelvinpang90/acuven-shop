import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";
import { COPY } from "./i18n/copy";
import {
  ROUTE_PATHS,
  canonicalizeLocation,
  isPlainLeftClick,
  isRoutePath,
  pushPath,
  resolvePath,
  settleLocation,
} from "./router";
import type { BrowserLike } from "./router";

function fakeBrowser(pathname: string) {
  const calls: string[] = [];
  const browser: BrowserLike = {
    location: { pathname },
    history: {
      pushState: (_data, _unused, url) => {
        calls.push(`push ${url}`);
        browser.location.pathname = url;
      },
      replaceState: (_data, _unused, url) => {
        calls.push(`replace ${url}`);
        browser.location.pathname = url;
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

function render(path: string): string {
  return renderToStaticMarkup(<App initialPath={path} storage={null} />);
}

describe("route table", () => {
  // 验收：本任务只有首页与 /privacy 两个路由。
  it("has exactly the home page and the privacy page", () => {
    expect([...ROUTE_PATHS]).toEqual(["/", "/privacy"]);
    expect(isRoutePath("/privacy")).toBe(true);
    expect(isRoutePath("/products")).toBe(false);
  });

  // 验收：未知路径替换为首页。
  it.each(["/nope", "/products", "/track", "/privacy/", "/PRIVACY", ""])("resolves %j to the home page", (path) => {
    expect(resolvePath(path)).toBe("/");
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
  it.each([...ROUTE_PATHS])("links on %s point only to routes in the table", (path) => {
    const hrefs = [...render(path).matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(isRoutePath(href)).toBe(true);
      expect(href).not.toContain("?");
    }
  });
});
