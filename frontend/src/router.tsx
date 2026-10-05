import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

// 用浏览器 History 接口实现的前台路由，不引入依赖。
//
// 路由表 ROUTE_PATHS 是唯一来源：页头、菜单、页脚里的入口只在目标路径匹配表里某一项时渲染，
// App.tsx 的页面表按表里的模式穷举，表里加模式而不加页面（或反之）类型检查不通过。
// 动态段写成 :名称（如 /products/:slug）；站内链接与当前路径都是实际路径，由这里匹配并把解码后的段值交给页面。
// 路径与查询参数里不放订单号或电话（UX「阅读说明」）；需要它们的页面以请求体或服务端会话传递
// （结账页 /checkout 的电话与收货资料只在页面内存与下单请求体里；支付页 /pay 与结果页 /pay/result 凭服务端会话读取订单，
// 订单号只在页面内存与写接口的请求体里；订单查询页 /track 的订单号与电话只在页面内存与查单请求体里，
// 订单详情 /track/order 凭服务端查单授权读取订单）。
// 查询参数只用于商品列表的搜索、筛选、排序与页码（pages/productListQuery.ts），也不放语言。
export const ROUTE_PATHS = [
  "/",
  "/products",
  "/products/:slug",
  "/cart",
  "/checkout",
  "/pay",
  "/pay/result",
  "/track",
  "/track/order",
  "/privacy",
] as const;
export type RoutePattern = (typeof ROUTE_PATHS)[number];

// 模式对应的实际路径：/products/:slug → /products/<任意段>。
type ActualPath<P extends string> = P extends `${infer Head}/:${string}` ? `${Head}/${string}` : P;
export type RoutePath = ActualPath<RoutePattern>;

export const HOME_PATH: RoutePath = "/";
export const PRODUCTS_PATH: RoutePath = "/products";

export type RouteParams = Readonly<Record<string, string>>;

export interface RouteMatch {
  pattern: RoutePattern;
  params: RouteParams;
}

// 动态段的值：按路径段解码；解码失败、解码后为空、为 . 或 ..、或含 / 都不匹配。其余值不再按字符集校验。
export function decodeSegment(raw: string): string | null {
  let value: string;
  try {
    value = decodeURIComponent(raw);
  } catch {
    return null;
  }
  if (value === "" || value === "." || value === ".." || value.includes("/")) {
    return null;
  }
  return value;
}

function matchPattern(pattern: RoutePattern, pathname: string): RouteMatch | null {
  const expected = pattern.split("/");
  const actual = pathname.split("/");
  if (expected.length !== actual.length) {
    return null;
  }
  const params: Record<string, string> = {};
  for (const [index, segment] of expected.entries()) {
    const raw = actual[index] ?? "";
    if (segment.startsWith(":")) {
      const value = decodeSegment(raw);
      if (value === null) {
        return null;
      }
      params[segment.slice(1)] = value;
    } else if (segment !== raw) {
      return null;
    }
  }
  return { pattern, params };
}

// 按表的顺序取第一个匹配的模式；都不匹配时为 null。
export function matchRoute(pathname: string): RouteMatch | null {
  for (const pattern of ROUTE_PATHS) {
    const match = matchPattern(pattern, pathname);
    if (match) {
      return match;
    }
  }
  return null;
}

export function isRoutePath(path: string): path is RoutePath {
  return matchRoute(path) !== null;
}

// 商品详情的实际路径：slug 在路径里只编码一次。
export function productPath(slug: string): RoutePath {
  return `/products/${encodeURIComponent(slug)}`;
}

// 未知路径落到首页。
export function resolvePath(pathname: string): RoutePath {
  return isRoutePath(pathname) ? pathname : HOME_PATH;
}

// 当前位置：路由表里的实际路径与查询串（空串，或以 ? 开头）。
export interface RouteLocation {
  path: RoutePath;
  search: string;
}

function normalizeSearch(search: string): string {
  if (search === "" || search === "?") {
    return "";
  }
  return search.startsWith("?") ? search : `?${search}`;
}

// 未知路径落到首页，并丢掉它的查询串。
export function resolveLocation(pathname: string, search: string): RouteLocation {
  return isRoutePath(pathname) ? { path: pathname, search: normalizeSearch(search) } : { path: HOME_PATH, search: "" };
}

// 把 "/products?q=tee" 这样的地址拆成路径与查询串（# 之后的部分不用）。
export function resolveHref(href: string): RouteLocation {
  const [withoutHash = ""] = href.split("#");
  const mark = withoutHash.indexOf("?");
  return mark < 0
    ? resolveLocation(withoutHash, "")
    : resolveLocation(withoutHash.slice(0, mark), withoutHash.slice(mark));
}

export interface BrowserLike {
  location: { pathname: string; search: string };
  history: {
    pushState(data: unknown, unused: string, url: string): void;
    replaceState(data: unknown, unused: string, url: string): void;
  };
  scrollTo(x: number, y: number): void;
}

// 站内跳转：只改历史记录，不整页刷新。返回是否真的换了位置（路径与查询串都相同时不重复记一条历史）。
export function pushPath(browser: BrowserLike, path: RoutePath, search = ""): boolean {
  if (browser.location.pathname === path && normalizeSearch(browser.location.search) === search) {
    return false;
  }
  browser.history.pushState(null, "", `${path}${search}`);
  return true;
}

// 用另一个页面替换当前历史记录（如商品不存在时换成商品列表），不新增记录。
export function replacePath(browser: BrowserLike, path: RoutePath, search = ""): void {
  browser.history.replaceState(null, "", `${path}${search}`);
}

// 地址栏不是路由表里的路径时（未知路径），替换为首页，不新增历史记录。
export function canonicalizeLocation(browser: BrowserLike, path: RoutePath): void {
  if (browser.location.pathname !== path) {
    browser.history.replaceState(null, "", path);
  }
}

// 每次换页（含前进后退与首次进入）后：未知路径在地址栏里换成首页，并回到页面顶部。
export function settleLocation(browser: BrowserLike, path: RoutePath): void {
  canonicalizeLocation(browser, path);
  browser.scrollTo(0, 0);
}

type ClickLike = Pick<MouseEvent, "button" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey" | "defaultPrevented">;

// 新标签页、新窗口、下载等修饰键点击交给浏览器自己处理。
export function isPlainLeftClick(event: ClickLike): boolean {
  return (
    !event.defaultPrevented &&
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

interface RouterContextValue {
  // 当前实际路径（如 /products/crew-neck-tee）。
  path: RoutePath;
  // 当前查询串：空串，或以 ? 开头。
  search: string;
  // 当前路径匹配的路由表模式与解码后的动态段。
  pattern: RoutePattern;
  params: RouteParams;
  navigate: (path: RoutePath, search?: string) => void;
  // 替换当前历史记录，不新增一条。
  replace: (path: RoutePath, search?: string) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

const HOME_MATCH: RouteMatch = { pattern: "/", params: {} };

interface RouterProviderProps {
  children: ReactNode;
  // 测试与服务端渲染时给定地址（可带查询串）；不给时读地址栏。
  initialPath?: string | undefined;
}

export function RouterProvider({ children, initialPath }: RouterProviderProps) {
  const [location, setLocation] = useState<RouteLocation>(() =>
    initialPath === undefined
      ? resolveLocation(window.location.pathname, window.location.search)
      : resolveHref(initialPath),
  );
  const { path, search } = location;
  const match = useMemo(() => matchRoute(path) ?? HOME_MATCH, [path]);

  useEffect(() => {
    const onPopState = () => {
      setLocation(resolveLocation(window.location.pathname, window.location.search));
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, []);

  // 只在换页面时回到顶部；同一页面只改查询串（如商品列表换筛选）时不动。
  useEffect(() => {
    settleLocation(window, path);
  }, [path]);

  const navigate = useCallback((next: RoutePath, nextSearch = "") => {
    const normalized = normalizeSearch(nextSearch);
    if (pushPath(window, next, normalized)) {
      setLocation(resolveLocation(next, normalized));
    } else {
      window.scrollTo(0, 0);
    }
  }, []);

  const replace = useCallback((next: RoutePath, nextSearch = "") => {
    const normalized = normalizeSearch(nextSearch);
    replacePath(window, next, normalized);
    setLocation(resolveLocation(next, normalized));
  }, []);

  const value = useMemo(
    () => ({ path, search, pattern: match.pattern, params: match.params, navigate, replace }),
    [path, search, match, navigate, replace],
  );
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterContextValue {
  const value = useContext(RouterContext);
  if (!value) {
    throw new Error("useRouter must be used inside RouterProvider");
  }
  return value;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  // 只接受路由表里模式对应的实际路径：指向尚未实现页面的链接写不出来。
  to: RoutePath;
  // 查询串（以 ? 开头）；只由 pages/productListQuery.ts 生成。
  search?: string | undefined;
};

export function Link({ to, search = "", onClick, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (isPlainLeftClick(event)) {
      event.preventDefault();
      navigate(to, search);
    }
  };
  return (
    <a {...rest} href={`${to}${normalizeSearch(search)}`} onClick={handleClick}>
      {children}
    </a>
  );
}
