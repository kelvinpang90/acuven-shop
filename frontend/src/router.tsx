import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

// 用浏览器 History 接口实现的前台路由，不引入依赖。
//
// 路由表 ROUTE_PATHS 是唯一来源：页头、菜单、页脚里的入口只在目标路径在表里时渲染，
// App.tsx 的页面表按 RoutePath 穷举，表里加路径而不加页面（或反之）类型检查不通过。
// 动态段写成模式（如 /products/:slug），由 matchRoute 匹配实际路径并把段的值交给页面；
// 站内链接与当前路径（useRouter().path）都是实际路径，模式只用来查页面表。
// 路径与查询参数里不放订单号或电话（UX「阅读说明」）；需要它们的页面以请求体或服务端会话传递。
// 查询参数只用于商品列表的搜索、筛选、排序与页码（pages/productListQuery.ts），也不放语言。
export const ROUTE_PATHS = ["/", "/products", "/products/:slug", "/privacy"] as const;
export type RoutePath = (typeof ROUTE_PATHS)[number];
export const HOME_PATH = "/" satisfies RoutePath;
export const PRODUCTS_PATH = "/products" satisfies RoutePath;
export const PRODUCT_DETAIL_PATH = "/products/:slug" satisfies RoutePath;

// 模式对应的实际路径类型：动态段换成任意字符串（/products/:slug → /products/${string}）。
// 站内链接只接受这些路径，指向尚未实现页面的链接写不出来。
type HrefOf<P extends string> = P extends `${infer Head}:${string}/${infer Tail}`
  ? `${Head}${string}/${HrefOf<Tail>}`
  : P extends `${infer Head}:${string}`
    ? `${Head}${string}`
    : P;
export type RouteHref = HrefOf<RoutePath>;

// 动态段的值。
export type RouteParams = Readonly<Record<string, string>>;

// 路由表里有这一项（静态路径，或模式本身）。
export function isRoutePath(path: string): path is RoutePath {
  return (ROUTE_PATHS as readonly string[]).includes(path);
}

// 动态段的实际值只认 RFC 3986 的非保留字符与百分号编码；空段、「.」「..」与含 : 的段（如模式本身）都不匹配。
const SEGMENT_VALUE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+$/;

function segmentValue(raw: string): string | null {
  if (!SEGMENT_VALUE.test(raw) || raw === "." || raw === "..") {
    return null;
  }
  try {
    return decodeURIComponent(raw);
  } catch {
    // 百分号编码不是合法的 UTF-8。
    return null;
  }
}

// 把一个值写成路径里的一段：encodeURIComponent 之外再编码 !'()*，结果只含 SEGMENT_VALUE 认的字符。
export function encodeSegment(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

// 商品详情 P03 的实际路径。
export function productPath(slug: string): RouteHref {
  return `/products/${encodeSegment(slug)}`;
}

export interface RouteMatch {
  route: RoutePath;
  params: RouteParams;
}

// 实际路径对应的路由表项；静态路径逐字比较，动态段按 segmentValue 取值。都不匹配时为 null。
export function matchRoute(pathname: string): RouteMatch | null {
  const parts = pathname.split("/");
  for (const route of ROUTE_PATHS) {
    const pattern = route.split("/");
    if (pattern.length !== parts.length) {
      continue;
    }
    const params: Record<string, string> = {};
    const matched = pattern.every((segment, index) => {
      const actual = parts[index] ?? "";
      if (!segment.startsWith(":")) {
        return segment === actual;
      }
      const value = segmentValue(actual);
      if (value === null) {
        return false;
      }
      params[segment.slice(1)] = value;
      return true;
    });
    if (matched) {
      return { route, params };
    }
  }
  return null;
}

// 未知路径落到首页。
export function resolvePath(pathname: string): RoutePath {
  return matchRoute(pathname)?.route ?? HOME_PATH;
}

// 当前位置：路由表项、实际路径、动态段的值与查询串（空串，或以 ? 开头）。
export interface RouteLocation {
  route: RoutePath;
  path: string;
  params: RouteParams;
  search: string;
}

function normalizeSearch(search: string): string {
  if (search === "" || search === "?") {
    return "";
  }
  return search.startsWith("?") ? search : `?${search}`;
}

const HOME_LOCATION: RouteLocation = { route: HOME_PATH, path: HOME_PATH, params: {}, search: "" };

// 未知路径落到首页，并丢掉它的查询串。
export function resolveLocation(pathname: string, search: string): RouteLocation {
  const match = matchRoute(pathname);
  return match ? { route: match.route, path: pathname, params: match.params, search: normalizeSearch(search) } : HOME_LOCATION;
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
export function pushPath(browser: BrowserLike, path: string, search = ""): boolean {
  if (browser.location.pathname === path && normalizeSearch(browser.location.search) === search) {
    return false;
  }
  browser.history.pushState(null, "", `${path}${search}`);
  return true;
}

// 地址栏不是路由表里的路径时（未知路径），替换为首页，不新增历史记录。
export function canonicalizeLocation(browser: BrowserLike, path: string): void {
  if (browser.location.pathname !== path) {
    browser.history.replaceState(null, "", path);
  }
}

// 每次换页（含前进后退与首次进入）后：未知路径在地址栏里换成首页，并回到页面顶部。
export function settleLocation(browser: BrowserLike, path: string): void {
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
  // 路由表项（动态段为模式），用来查页面表。
  route: RoutePath;
  // 实际路径。
  path: string;
  // 动态段的值，如商品详情的 slug。
  params: RouteParams;
  // 当前查询串：空串，或以 ? 开头。
  search: string;
  navigate: (to: RouteHref, search?: string) => void;
  // 替换当前位置，不新增历史记录（如商品不存在时换成商品列表）。
  replace: (to: RouteHref, search?: string) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

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
  const { route, path, params, search } = location;

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

  const navigate = useCallback((to: RouteHref, nextSearch = "") => {
    const next = resolveLocation(to, nextSearch);
    if (pushPath(window, next.path, next.search)) {
      setLocation(next);
    } else {
      window.scrollTo(0, 0);
    }
  }, []);

  const replace = useCallback((to: RouteHref, nextSearch = "") => {
    const next = resolveLocation(to, nextSearch);
    window.history.replaceState(null, "", `${next.path}${next.search}`);
    setLocation(next);
  }, []);

  const value = useMemo(
    () => ({ route, path, params, search, navigate, replace }),
    [route, path, params, search, navigate, replace],
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
  // 只接受路由表里的路径（动态段填入实际值）：指向尚未实现页面的链接写不出来。
  to: RouteHref;
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
