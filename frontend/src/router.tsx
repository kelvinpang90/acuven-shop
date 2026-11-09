import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

// 用浏览器 History 接口实现的前台路由，不引入依赖。
//
// 路由表 ROUTE_PATHS 是唯一来源：页头、菜单、页脚里的入口只在目标路径匹配表里某一项时渲染，
// App.tsx 的页面表按表里的模式穷举，表里加模式而不加页面（或反之）类型检查不通过。
// 动态段写成 :名称（如 /products/:slug）；站内链接与当前路径都是实际路径，由这里匹配并把解码后的段值交给页面。
// 路径与查询参数里不放订单号或电话（UX「阅读说明」）；需要它们的页面以请求体或服务端会话传递
// （结账页 /checkout 的电话与收货资料只在页面内存与下单请求体里；支付页 /pay 与结果页 /pay/result 凭服务端会话读取订单，
// 订单号只在页面内存与写接口的请求体里；订单查询页 /track 的订单号与电话只在页面内存与查单请求体里，
// 订单详情 /track/order 与退款申请 /track/order/refund 凭服务端查单授权读取订单，订单号只在页面内存与写接口的请求体里）。
// 查询参数只用于商品列表的搜索、筛选、排序与页码（pages/productListQuery.ts），也不放语言。
// 以 /admin 开头的是后台页（后台登录 /admin/login 的用户名、密码与 CSRF 令牌只在页面内存与请求体或请求头里；
// 后台订单 /admin/orders 由后台框架凭服务端会话打开，CSRF 令牌同样只在页面内存与退出请求头里；
// 订单详情 /admin/orders/:id 的段值是订单的内部 ID，订单号、收货资料与推进用的 CSRF 令牌只在页面内存与请求里，
// 列表的搜索、筛选与页码只在页面模块的内存里；退款申请列表 /admin/refunds 的状态筛选与页码只在页面内存里，
// 按订单筛选 /admin/refunds/order/:orderId 的段值是订单的内部 ID，订单号只在页面内存里；
// 退款详情 /admin/refunds/:id 的段值是申请的内部 ID，订单号、理由、审核人邮箱、令牌与幂等键只在页面内存与请求里，
// 列表的筛选与页码只在页面模块的内存里；Stripe 待处理 /admin/stripe-pending、后台商品 /admin/products、后台分类 /admin/categories、运费与演示汇率 /admin/shipping、库存重置结果 /admin/stock-resets、店铺装修 /admin/store-design 与站点设置 /admin/settings 不带段值）：
// App.tsx 不给它们套前台框架，前台页头、菜单与页脚也没有指向它们的入口（UX A01「前台不放入口链接」）。
// 会员登录 /login 的号码与密码只在页面内存与请求体里；登录成功后回到的来源页只记在路由的内存里（见下方「来源页」）。
// 忘记密码 /forgot-password 的号码、验证码、新密码与 CSRF 令牌只在页面内存与请求里。
// 注册 /register 的号码与验证码只在页面内存与请求体里；成功后的去向同会员登录。
// 会员中心 /account 凭服务端会员会话读取，遮掩后的手机号与 CSRF 令牌只在页面内存里，订单号只在页面内存与会员订单的交接里
// （api/memberOrders.ts 的 handOffMemberOrder）。订单详情会员模式 /account/order 凭服务端会员会话读取，订单号只经这一交接
// （或退款申请提交后的交接）取得，只在页面内存与请求体里，CSRF 令牌只在页面内存与写接口的请求头里。
// 退款申请会员模式 /account/order/refund 同样凭会员会话，订单号只经同一交接取得，只在页面内存与请求体里，CSRF 令牌只在页面内存与请求头里。
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
  "/track/order/refund",
  "/privacy",
  "/login",
  "/forgot-password",
  "/register",
  "/account",
  "/account/order",
  "/account/order/refund",
  "/admin/login",
  "/admin/orders",
  "/admin/orders/:id",
  "/admin/refunds",
  "/admin/refunds/order/:orderId",
  "/admin/refunds/:id",
  "/admin/stripe-pending",
  "/admin/products",
  "/admin/categories",
  "/admin/coupons",
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
] as const;
export type RoutePattern = (typeof ROUTE_PATHS)[number];

// 模式对应的实际路径：/products/:slug → /products/<任意段>。
type ActualPath<P extends string> = P extends `${infer Head}/:${string}` ? `${Head}/${string}` : P;
export type RoutePath = ActualPath<RoutePattern>;

export const HOME_PATH: RoutePath = "/";
export const PRODUCTS_PATH: RoutePath = "/products";
export const ACCOUNT_PATH: RoutePath = "/account";
export const ADMIN_LOGIN_PATH: RoutePath = "/admin/login";
export const ADMIN_ORDERS_PATH: RoutePath = "/admin/orders";
export const ADMIN_REFUNDS_PATH: RoutePath = "/admin/refunds";
export const ADMIN_STRIPE_PENDING_PATH: RoutePath = "/admin/stripe-pending";
export const ADMIN_PRODUCTS_PATH: RoutePath = "/admin/products";
export const ADMIN_CATEGORIES_PATH: RoutePath = "/admin/categories";
export const ADMIN_COUPONS_PATH: RoutePath = "/admin/coupons";
export const ADMIN_SHIPPING_PATH: RoutePath = "/admin/shipping";
export const ADMIN_STOCK_RESETS_PATH: RoutePath = "/admin/stock-resets";
export const ADMIN_STORE_DESIGN_PATH: RoutePath = "/admin/store-design";
export const ADMIN_SITE_SETTINGS_PATH: RoutePath = "/admin/settings";

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

// 后台订单详情的实际路径：段值为订单的内部 ID（订单号不进网址）。
export function adminOrderPath(id: number): RoutePath {
  return `/admin/orders/${String(id)}`;
}

// 后台退款详情的实际路径：段值为申请的内部 ID。
export function adminRefundPath(id: number): RoutePath {
  return `/admin/refunds/${String(id)}`;
}

// 只含某张订单申请的退款列表的实际路径：段值为订单的内部 ID（订单号不进网址）。
export function adminRefundsOrderPath(orderId: number): RoutePath {
  return `/admin/refunds/order/${String(orderId)}`;
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

// 来源页（UX P12「登录成功 → 来源页或 P13」，HANDOFF 0.58 第（d）条）：站内跳转（push、replace 与浏览器的前进后退）时
// 以切换前的路径为准，只在内存里记下最近一个离开的前台路径（只记路径、不含查询参数）；登录（之后还有注册与重设）成功后回到那里。
// 离开的是后台页或会员登录、注册、重设页时不更新，所以经登录页再进注册或重设页仍保留原来的来源。
// 不进地址、历史状态与浏览器存储，刷新后没有来源。
const CHECKOUT_PATH: RoutePath = "/checkout";
const MEMBER_AUTH_PATHS: ReadonlySet<string> = new Set(["/login", "/register", "/forgot-password"]);

// 可作来源的路径：前台页，且不是会员登录、注册与重设页。
export function isSourcePath(path: string): boolean {
  return !path.startsWith("/admin") && !MEMBER_AUTH_PATHS.has(path);
}

// 从 from 换到 to 之后记下的来源：换了路径且离开的是可作来源的页时为 from，否则不变。
export function nextSource(source: RoutePath | null, from: RoutePath, to: RoutePath): RoutePath | null {
  return from !== to && isSourcePath(from) ? from : source;
}

// 成功文案显示之后的去向：记下的来源；没有来源（或不是可作来源的路径）时去会员中心 P13（UX P11、P12「去来源页或 P13」，HANDOFF 0.61 第（5）条）。
export function verifiedPath(source: RoutePath | null): RoutePath {
  return source !== null && isSourcePath(source) ? source : ACCOUNT_PATH;
}

// auth.continue_guest 的去向：来源为结账页时回结账，否则去首页 P01。
export function guestPath(source: RoutePath | null): RoutePath {
  return source === CHECKOUT_PATH ? CHECKOUT_PATH : HOME_PATH;
}

// 成功文案显示多久之后以 replace 前往 verifiedPath（毫秒）。
export const VERIFIED_REDIRECT_MS = 1500;

// 离开拦截（UX A04、A10「列表」一段的未保存确认，HANDOFF 0.81）：页面有未保存的修改时由后台框架设置，路由只拦、不渲染确认框。
// 拦截函数收到「继续」回调，选离开时调用它才换位置；不调用即留在本页。
// navigate 换到与当前不同的位置、浏览器的后退与前进要先经它；replace（如 401 回到登录页）不拦截；设有拦截期间刷新与关闭标签页由浏览器自己的离开提示拦一次。
export type LeaveGuard = (proceed: () => void) => void;

export function sameLocation(a: RouteLocation, b: RouteLocation): boolean {
  return a.path === b.path && a.search === b.search;
}

// 是否交给拦截函数：设有拦截，且目标与当前位置不同（相同时照旧只回到顶部）。
export function shouldGuard(guard: LeaveGuard | null, here: RouteLocation, next: RouteLocation): guard is LeaveGuard {
  return guard !== null && !sameLocation(here, next);
}

// 站内跳转（navigate）：需要拦截时交给拦截函数，选继续才 push；否则与没有拦截时相同（push 并换位置，同一位置只回到顶部）。
export function guardedPush(
  browser: BrowserLike,
  guard: LeaveGuard | null,
  here: RouteLocation,
  next: RouteLocation,
  moveTo: (next: RouteLocation) => void,
): void {
  const go = () => {
    if (pushPath(browser, next.path, next.search)) {
      moveTo(next);
    } else {
      browser.scrollTo(0, 0);
    }
  };
  if (shouldGuard(guard, here, next)) {
    guard(go);
  } else {
    go();
  }
}

// 替换当前历史记录（replace）：不经离开拦截（如 401 回到登录页），直接换位置。
export function replaceTo(browser: BrowserLike, next: RouteLocation, moveTo: (next: RouteLocation) => void): void {
  replacePath(browser, next.path, next.search);
  moveTo(next);
}

// 浏览器后退或前进被拦下时的处理：先写回地址栏的地址（切换前的路径与查询串），选继续后前往的目标（popstate 的目标）。
export interface PopGuard {
  restore: string;
  target: RouteLocation;
}

// popstate 时（地址栏已换成 target）：需要拦截时给出写回的地址与继续时的目标，不需要时为 null（照旧换到 target）。
export function popGuard(guard: LeaveGuard | null, here: RouteLocation, target: RouteLocation): PopGuard | null {
  return shouldGuard(guard, here, target) ? { restore: `${here.path}${here.search}`, target } : null;
}

// 浏览器后退或前进：设有拦截时先以 pushState 把地址栏写回本页、页面不换，再交给拦截函数，选继续时以 push 前往目标
// （历史记录会多一条，HANDOFF 0.81 认可）；没有拦截时照旧换到地址栏的位置。
export function guardedPop(
  browser: BrowserLike,
  guard: LeaveGuard | null,
  here: RouteLocation,
  moveTo: (next: RouteLocation) => void,
): void {
  const target = resolveLocation(browser.location.pathname, browser.location.search);
  const held = popGuard(guard, here, target);
  if (guard === null || held === null) {
    moveTo(target);
    return;
  }
  browser.history.pushState(null, "", held.restore);
  guard(() => {
    if (pushPath(browser, held.target.path, held.target.search)) {
      moveTo(held.target);
    }
  });
}

// beforeunload 事件里本页用到的部分。
export interface BeforeUnloadLike {
  preventDefault(): void;
  returnValue: unknown;
}

export interface UnloadTarget {
  addEventListener(type: "beforeunload", listener: (event: BeforeUnloadLike) => void): void;
  removeEventListener(type: "beforeunload", listener: (event: BeforeUnloadLike) => void): void;
}

// 让浏览器弹出自己的离开提示（文字由浏览器决定）。
export function blockUnload(event: BeforeUnloadLike): void {
  event.preventDefault();
  event.returnValue = "";
}

// 设有拦截时注册 beforeunload 监听，返回移除它的函数；没有拦截时不注册。
export function watchUnload(target: UnloadTarget, guarded: boolean): () => void {
  if (!guarded) {
    return () => undefined;
  }
  target.addEventListener("beforeunload", blockUnload);
  return () => {
    target.removeEventListener("beforeunload", blockUnload);
  };
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
  // 读取记下的来源页（没有时为 null）。
  source: () => RoutePath | null;
  // 设置或清除（null）离开拦截。
  setLeaveGuard: (guard: LeaveGuard | null) => void;
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
  // 路由状态里当前的位置（每次换位置时与状态一起更新），换位置时以它为切换前的路径记下来源，离开拦截也以它为本页。
  const current = useRef<RouteLocation>(location);
  const source = useRef<RoutePath | null>(null);
  // 离开拦截只在内存里；guarded 决定是否注册 beforeunload。
  const guard = useRef<LeaveGuard | null>(null);
  const [guarded, setGuarded] = useState(false);

  const moveTo = useCallback((next: RouteLocation) => {
    source.current = nextSource(source.current, current.current.path, next.path);
    current.current = next;
    setLocation(next);
  }, []);

  const readSource = useCallback(() => source.current, []);

  const setLeaveGuard = useCallback((next: LeaveGuard | null) => {
    guard.current = next;
    setGuarded(next !== null);
  }, []);

  useEffect(() => {
    const onPopState = () => {
      guardedPop(window, guard.current, current.current, moveTo);
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, [moveTo]);

  useEffect(() => watchUnload(window, guarded), [guarded]);

  // 只在换页面时回到顶部；同一页面只改查询串（如商品列表换筛选）时不动。
  useEffect(() => {
    settleLocation(window, path);
  }, [path]);

  const navigate = useCallback((next: RoutePath, nextSearch = "") => {
    guardedPush(window, guard.current, current.current, resolveLocation(next, normalizeSearch(nextSearch)), moveTo);
  }, [moveTo]);

  const replace = useCallback((next: RoutePath, nextSearch = "") => {
    replaceTo(window, resolveLocation(next, normalizeSearch(nextSearch)), moveTo);
  }, [moveTo]);

  const value = useMemo(
    () => ({ path, search, pattern: match.pattern, params: match.params, navigate, replace, source: readSource, setLeaveGuard }),
    [path, search, match, navigate, replace, readSource, setLeaveGuard],
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
