import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

// 用浏览器 History 接口实现的前台路由，不引入依赖。
//
// 路由表 ROUTE_PATHS 是唯一来源：页头、菜单、页脚里的入口只在目标路径在表里时渲染，
// App.tsx 的页面表按 RoutePath 穷举，表里加路径而不加页面（或反之）类型检查不通过。
// 路径与查询参数里不放订单号或电话（UX「阅读说明」）；需要它们的页面以请求体或服务端会话传递。
export const ROUTE_PATHS = ["/", "/privacy"] as const;
export type RoutePath = (typeof ROUTE_PATHS)[number];
export const HOME_PATH: RoutePath = "/";

export function isRoutePath(path: string): path is RoutePath {
  return (ROUTE_PATHS as readonly string[]).includes(path);
}

// 未知路径落到首页。
export function resolvePath(pathname: string): RoutePath {
  return isRoutePath(pathname) ? pathname : HOME_PATH;
}

export interface BrowserLike {
  location: { pathname: string };
  history: {
    pushState(data: unknown, unused: string, url: string): void;
    replaceState(data: unknown, unused: string, url: string): void;
  };
  scrollTo(x: number, y: number): void;
}

// 站内跳转：只改历史记录，不整页刷新。返回是否真的换了页面（同一路径不重复记一条历史）。
export function pushPath(browser: BrowserLike, path: RoutePath): boolean {
  if (browser.location.pathname === path) {
    return false;
  }
  browser.history.pushState(null, "", path);
  return true;
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
  path: RoutePath;
  navigate: (path: RoutePath) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

interface RouterProviderProps {
  children: ReactNode;
  // 测试与服务端渲染时给定路径；不给时读地址栏。
  initialPath?: string | undefined;
}

export function RouterProvider({ children, initialPath }: RouterProviderProps) {
  const [path, setPath] = useState<RoutePath>(() =>
    resolvePath(initialPath ?? window.location.pathname),
  );

  useEffect(() => {
    const onPopState = () => {
      setPath(resolvePath(window.location.pathname));
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, []);

  useEffect(() => {
    settleLocation(window, path);
  }, [path]);

  const navigate = useCallback((next: RoutePath) => {
    if (pushPath(window, next)) {
      setPath(next);
    } else {
      window.scrollTo(0, 0);
    }
  }, []);

  const value = useMemo(() => ({ path, navigate }), [path, navigate]);
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
  // 只接受路由表里的路径：指向尚未实现页面的链接写不出来。
  to: RoutePath;
};

export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (isPlainLeftClick(event)) {
      event.preventDefault();
      navigate(to);
    }
  };
  return (
    <a {...rest} href={to} onClick={handleClick}>
      {children}
    </a>
  );
}
