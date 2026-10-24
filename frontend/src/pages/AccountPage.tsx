import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { clearMemberOrder, handOffMemberOrder, readMemberOrders } from "../api/memberOrders";
import type { MemberOrderSummary, MemberOrdersRead } from "../api/memberOrders";
import { memberLogout, useMemberSessionReader } from "../api/memberSession";
import type { MemberLogoutReply, MemberSessionReader, MemberSessionState } from "../api/memberSession";
import { AWAITING_PAYMENT } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { HOME_PATH, isRoutePath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { formatDate } from "./TrackOrderPage";

// 会员中心 P13 的框架与我的订单（docs/UX.md 0.13 P13，docs/UX-COPY.md 0.14，视觉稿 P13-desktop 与 P13-phone 的标题、导航与我的订单部分；
// DESIGN 1.13「权限与资料保护」第 3、5 条，HANDOFF 0.61）。积分、优惠券与设置三段由之后的任务各自在 ACCOUNT_SECTIONS 里加项。
// 打开页面时经 api/memberSession.ts 的读取对象重读一次会话（登录后来到本页时共享状态可能还是旧的），读完之前只显示标 aria-busy 的空主体；
// 未登录时以路由的 replace 进入 /login（路由记下的来源为 /account，登录成功后回到这里），读取失败只显示 common.error_retry。
// 这一判断只在打开时做，之后共享会话状态的变化不再影响本页。已登录时显示标题行（common.nav_account、遮掩后的手机号与 common.nav_logout；
// 手机暂放在标题之下，UX 与视觉稿在设置段，HANDOFF 0.61 第（7）条）、桌面左侧导航或手机分段标签、★ account.demo_hint 与我的订单。
// 我的订单经 api/memberOrders.ts 只取第一页（最多 20 张，按服务端顺序，不翻页，HANDOFF 0.61 第（1）条）；查看时经交接函数交出订单号，
// 再前往 /account/order（订单号不进地址、历史状态与浏览器存储），该路径进路由表之前不渲染查看入口。
// 离开页面时中止请求，之后不再更新；会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转。

const LOGIN_PATH: RoutePath = "/login";

// 订单详情 P09 会员模式的路径；进了路由表（SHOP-TASK-154）才渲染 account.order_view。
export function orderPath(): RoutePath | null {
  const path: string = "/account/order";
  return isRoutePath(path) ? path : null;
}

// 会员中心的段：桌面左侧导航与手机分段标签都按这张表渲染（只列已实现的段，HANDOFF 0.61 第（2）条）。
export type AccountSection = "orders";

export interface SectionItem {
  section: AccountSection;
  label: CopyKey;
}

export const ACCOUNT_SECTIONS: readonly SectionItem[] = [{ section: "orders", label: "account.orders" }];

// 段内容的 id（桌面导航的页内锚点）与手机标签的 id。
export function sectionId(section: AccountSection): string {
  return `account-${section}`;
}

function tabId(section: AccountSection): string {
  return `account-${section}-tab`;
}

// 我的订单：读取中（该段标 aria-busy）、已取到（第一页，按服务端顺序）、读取失败（common.error_retry 或 common.network_check）。
export type OrdersState = { status: "loading" } | { status: "ready"; orders: readonly MemberOrderSummary[] } | { status: "failed"; error: CopyKey };

// 页面的状态：读取会话中（空主体，标 aria-busy）、读取会话失败（只有 common.error_retry）、已登录。已登录时 phoneMasked 与 csrfToken
// 来自打开时那次读取（csrf 之后的重读更新令牌），loggingOut 为退出进行中（按钮禁用），logoutError 为退出失败的提示。
export type AccountScreen =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "member"; phoneMasked: string; csrfToken: string; orders: OrdersState; loggingOut: boolean; logoutError: CopyKey | null };

export const LOADING: AccountScreen = { status: "loading" };

// 按打开时重读到的会话决定：member 显示标题行并开始读取我的订单；guest 去 /login（login）；failed 只显示 common.error_retry。
// 重读的回答处理之后状态不会是 unknown，万一是也按失败处理。
export function sessionStep(state: MemberSessionState): AccountScreen | "login" {
  switch (state.kind) {
    case "member":
      return { status: "member", phoneMasked: state.phoneMasked, csrfToken: state.csrfToken, orders: { status: "loading" }, loggingOut: false, logoutError: null };
    case "guest":
      return "login";
    case "failed":
    case "unknown":
      return { status: "failed" };
  }
}

// 按我的订单的读取结果决定：ok 原样取第一页（total 不用，不翻页）；signed_out 重读会话后去 /login（login）；
// failed 为 common.error_retry；network 为 common.network_check。
export function ordersStep(read: MemberOrdersRead): OrdersState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", orders: read.orders };
    case "signed_out":
      return "login";
    case "failed":
      return { status: "failed", error: "common.error_retry" };
    case "network":
      return { status: "failed", error: "common.network_check" };
  }
}

// 按退出的回答决定：ok 与 signed_out 去首页 P01（home，UX P13「P01（退出或注销后）」）；csrf 重读会话后显示 common.error_retry（reread）；
// failed 为 common.error_retry；network 为 common.network_check。
export function logoutStep(reply: MemberLogoutReply): "home" | "reread" | CopyKey {
  switch (reply) {
    case "ok":
    case "signed_out":
      return "home";
    case "csrf":
      return "reread";
    case "failed":
      return "common.error_retry";
    case "network":
      return "common.network_check";
  }
}

// 对当前状态的一次改动（请求的结果按到达时的状态更新）。
export type AccountChange = (current: AccountScreen) => AccountScreen;

export function withOrders(orders: OrdersState): AccountChange {
  return (current) => (current.status === "member" ? { ...current, orders } : current);
}

// 退出进行中与未登录时不再退出（按钮禁用）。
export function canLogout(screen: AccountScreen): boolean {
  return screen.status === "member" && !screen.loggingOut;
}

// 点退出：按钮禁用，先前的提示收起。
export function startLogout(screen: AccountScreen): AccountScreen {
  return screen.status === "member" ? { ...screen, loggingOut: true, logoutError: null } : screen;
}

// 退出未成：按钮恢复并显示提示；reread 为 csrf 之后重读到的会话，已登录时换用其中的新令牌。
export function logoutFailed(error: CopyKey, reread?: MemberSessionState): AccountChange {
  return (current) => {
    if (current.status !== "member") {
      return current;
    }
    const csrfToken = reread?.kind === "member" ? reread.csrfToken : current.csrfToken;
    return { ...current, csrfToken, loggingOut: false, logoutError: error };
  };
}

// 状态名（order.status_*）与标签样式（视觉稿 P13：已完成为 success，待支付与已取消为描边，其余为主色）；未知状态为 null，不显示标签。
const STATUS_LABEL: Readonly<Record<string, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

export function statusTag(status: string): { label: CopyKey; className: string } | null {
  const label = STATUS_LABEL[status];
  if (label === undefined) {
    return null;
  }
  if (status === "demo_completed") {
    return { label, className: "acs-tag acs-tag--success" };
  }
  return { label, className: status === AWAITING_PAYMENT || status === "demo_cancelled" ? "acs-tag acs-tag--outline" : "acs-tag acs-tag--accent" };
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 前往 /login 或首页。
export interface AccountMoves {
  show: (screen: AccountScreen) => void;
  update: (change: AccountChange) => void;
  replace: (path: RoutePath) => void;
}

// 重读会话（共享读取，不中止）之后以 replace 前往 path；signal 已中止（离开页面）时不跳转。
async function leaveAfterReload(reader: MemberSessionReader, signal: AbortSignal, path: RoutePath, replace: (path: RoutePath) => void): Promise<void> {
  await reader.reload();
  if (!signal.aborted) {
    replace(path);
  }
}

// 读取我的订单；signal 中止之后不再更新。signed_out 时重读会话并以 replace 进入 /login。
export async function loadOrders(reader: MemberSessionReader, signal: AbortSignal, moves: AccountMoves): Promise<void> {
  const read = await readMemberOrders(signal);
  if (signal.aborted) {
    return;
  }
  const step = ordersStep(read);
  if (step === "login") {
    await leaveAfterReload(reader, signal, LOGIN_PATH, moves.replace);
  } else {
    moves.update(withOrders(step));
  }
}

// 打开页面：重读一次会话并按结果显示或进入 /login，已登录时接着读取我的订单。signal 中止之后不再更新或跳转。
export async function openAccount(reader: MemberSessionReader, signal: AbortSignal, moves: AccountMoves): Promise<void> {
  const state = await reader.reload();
  if (signal.aborted) {
    return;
  }
  const step = sessionStep(state);
  if (step === "login") {
    moves.replace(LOGIN_PATH);
    return;
  }
  moves.show(step);
  if (step.status === "member") {
    await loadOrders(reader, signal, moves);
  }
}

// 退出（带会话里的 CSRF 令牌）：ok 与 signed_out 清除交接的会员订单号、重读会话并以 replace 前往首页；csrf 重读会话（取得新令牌）后
// 显示 common.error_retry；其余显示对应提示。signal 中止之后不再更新或跳转。
export async function logOut(reader: MemberSessionReader, csrfToken: string, signal: AbortSignal, moves: AccountMoves): Promise<void> {
  const reply = await memberLogout(csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = logoutStep(reply);
  if (step === "home") {
    clearMemberOrder();
    await leaveAfterReload(reader, signal, HOME_PATH, moves.replace);
  } else if (step === "reread") {
    const state = await reader.reload();
    if (!signal.aborted) {
      moves.update(logoutFailed("common.error_retry", state));
    }
  } else {
    moves.update(logoutFailed(step));
  }
}

// 查看一张订单：先交出订单号，之后由链接前往 /account/order。
export function viewOrder(orderNumber: string): void {
  handOffMemberOrder(orderNumber);
}

function pageMoves(setScreen: Dispatch<SetStateAction<AccountScreen>>, replace: (path: RoutePath) => void): AccountMoves {
  return {
    show: (screen) => {
      setScreen(screen);
    },
    update: (change) => {
      setScreen(change);
    },
    replace,
  };
}

// 区块里的提示（读取失败、退出失败）。
function Notice({ error }: { error: CopyKey }) {
  const t = useCopy();
  return (
    <p className="acs-alert acs-alert--danger site-notice" role="alert">
      {t(error)}
    </p>
  );
}

// 手机行尾的 ›（读屏忽略，行的读屏标签为 account.order_view）。
function ChevronIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function StatusTag({ status }: { status: string }) {
  const t = useCopy();
  const tag = statusTag(status);
  return <span>{tag !== null && <span className={tag.className}>{t(tag.label)}</span>}</span>;
}

interface OrderRowProps {
  order: MemberOrderSummary;
  orderTo: RoutePath | null;
  onView: (orderNumber: string) => void;
}

// 桌面一行（P13-desktop）：日期、订单号、状态、合计与行末次要小按钮样式的 account.order_view。
function DesktopRow({ order, orderTo, onView }: OrderRowProps) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <li className="site-account__row">
      <span className="acs-body-s acs-muted">{formatDate(order.created_at, language)}</span>
      <span className="acs-num">{order.order_number}</span>
      <StatusTag status={order.status} />
      <span className="acs-price site-account__total">{t("common.price_myr", { amount: formatSen(order.total_sen) })}</span>
      {orderTo !== null && (
        <Link
          className="acs-btn acs-btn--secondary acs-btn--sm"
          to={orderTo}
          onClick={() => {
            onView(order.order_number);
          }}
        >
          {t("account.order_view")}
        </Link>
      )}
    </li>
  );
}

// 手机一行（P13-phone）：日期与状态、订单号与合计两行；整行是链接（读屏标签 account.order_view，行尾 ›）。没有去向时不作为链接，也没有 ›。
function PhoneRow({ order, orderTo, onView }: OrderRowProps) {
  const t = useCopy();
  const { language } = useLanguage();
  const content = (
    <>
      <span className="acs-body-s acs-muted">{formatDate(order.created_at, language)}</span>
      <StatusTag status={order.status} />
      <span className="acs-num">{order.order_number}</span>
      <span className="acs-price site-account__amount">
        {t("common.price_myr", { amount: formatSen(order.total_sen) })}
        {orderTo !== null && <ChevronIcon />}
      </span>
    </>
  );
  return (
    <li>
      {orderTo !== null ? (
        <Link
          className="site-account__item"
          to={orderTo}
          aria-label={t("account.order_view")}
          onClick={() => {
            onView(order.order_number);
          }}
        >
          {content}
        </Link>
      ) : (
        <div className="site-account__item">{content}</div>
      )}
    </li>
  );
}

// 我的订单的内容：段标题（手机由标签代替）；读取失败时为提示；没有订单时为 account.orders_empty；
// 否则桌面为各行与其后的 account.orders_hint，手机为 account.orders_hint 与其后的各行（两份按 site.css 的媒体查询显隐）。
function OrdersPanel({ orders, orderTo, onView }: { orders: OrdersState } & Omit<OrderRowProps, "order">) {
  const t = useCopy();
  return (
    <>
      <h2 className="acs-display-m site-desktop-only">{t("account.orders")}</h2>
      {orders.status === "failed" && <Notice error={orders.error} />}
      {orders.status === "ready" && orders.orders.length === 0 && <p className="site-account__text">{t("account.orders_empty")}</p>}
      {orders.status === "ready" && orders.orders.length > 0 && (
        <>
          <p className="acs-body-s acs-muted site-phone-only site-account__hint">{t("account.orders_hint")}</p>
          <ul className="site-desktop-only site-account__list">
            {orders.orders.map((order) => (
              <DesktopRow key={order.order_number} order={order} orderTo={orderTo} onView={onView} />
            ))}
          </ul>
          <ul className="site-phone-only site-account__list">
            {orders.orders.map((order) => (
              <PhoneRow key={order.order_number} order={order} orderTo={orderTo} onView={onView} />
            ))}
          </ul>
          <p className="acs-body-s acs-muted site-desktop-only site-account__text">{t("account.orders_hint")}</p>
        </>
      )}
    </>
  );
}

type MemberScreen = Extract<AccountScreen, { status: "member" }>;

function SectionPanel({ section, screen, orderTo, onView }: { section: AccountSection; screen: MemberScreen } & Omit<OrderRowProps, "order">) {
  switch (section) {
    case "orders":
      return <OrdersPanel orders={screen.orders} orderTo={orderTo} onView={onView} />;
  }
}

// 段内容是否在读取中（该段标 aria-busy）。
function sectionBusy(section: AccountSection, screen: MemberScreen): boolean {
  switch (section) {
    case "orders":
      return screen.orders.status === "loading";
  }
}

export interface AccountViewProps {
  screen: AccountScreen;
  // 当前段：桌面导航标 aria-current，手机标签标 aria-selected，手机只显示该段。
  section: AccountSection;
  // account.order_view 的去向（orderPath）；为 null 时不渲染查看入口。
  orderTo: RoutePath | null;
  onSection: (section: AccountSection) => void;
  onView: (orderNumber: string) => void;
  onLogout: () => void;
}

// 已登录：标题行之后为两栏——桌面左侧导航（页内锚点）与右栏（★ 提示、各段）；手机为一栏，★ 提示、分段标签与当前段的内容。
export function AccountView({ screen, section, orderTo, onSection, onView, onLogout }: AccountViewProps) {
  const t = useCopy();
  if (screen.status === "loading") {
    return <main className="site-account" aria-busy="true" />;
  }
  if (screen.status === "failed") {
    return (
      <main className="site-account">
        <ErrorNotice />
      </main>
    );
  }
  return (
    <main className="site-account">
      <div className="site-account__head">
        <h1 className="acs-display-l">{t("common.nav_account")}</h1>
        <span className="acs-num acs-muted">{screen.phoneMasked}</span>
        <button className="acs-btn acs-btn--secondary acs-btn--sm site-account__logout" type="button" disabled={!canLogout(screen)} aria-busy={screen.loggingOut} onClick={onLogout}>
          {t("common.nav_logout")}
        </button>
      </div>
      {screen.logoutError !== null && <Notice error={screen.logoutError} />}
      <div className="site-account__body">
        <nav className="site-desktop-only site-account__nav">
          {ACCOUNT_SECTIONS.map((item) => (
            <a
              key={item.section}
              href={`#${sectionId(item.section)}`}
              aria-current={item.section === section ? "true" : undefined}
              onClick={() => {
                onSection(item.section);
              }}
            >
              {t(item.label)}
            </a>
          ))}
        </nav>
        <div className="site-account__main">
          <DemoHint>{t("account.demo_hint")}</DemoHint>
          <div className="acs-tabs site-phone-only site-account__tabs" role="tablist">
            {ACCOUNT_SECTIONS.map((item) => (
              <button
                key={item.section}
                className="acs-tab"
                id={tabId(item.section)}
                role="tab"
                type="button"
                aria-selected={item.section === section}
                aria-controls={sectionId(item.section)}
                onClick={() => {
                  onSection(item.section);
                }}
              >
                {t(item.label)}
              </button>
            ))}
          </div>
          {ACCOUNT_SECTIONS.map((item) => (
            <section
              key={item.section}
              className={item.section === section ? "site-account__section" : "site-account__section site-account__section--off"}
              id={sectionId(item.section)}
              role="tabpanel"
              aria-labelledby={tabId(item.section)}
              aria-busy={sectionBusy(item.section, screen)}
            >
              <SectionPanel section={item.section} screen={screen} orderTo={orderTo} onView={onView} />
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}

export default function AccountPage() {
  const { replace } = useRouter();
  const reader = useMemberSessionReader();
  const [screen, setScreen] = useState<AccountScreen>(LOADING);
  const [section, setSection] = useState<AccountSection>("orders");
  // 我的订单的读取与退出共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openAccount(reader, controller.signal, pageMoves(setScreen, replace));
    });
  }, [reader, replace]);

  return (
    <AccountView
      screen={screen}
      section={section}
      orderTo={orderPath()}
      onSection={setSection}
      onView={viewOrder}
      onLogout={() => {
        const controller = lifetime.current;
        if (screen.status !== "member" || !canLogout(screen) || controller === null) {
          return;
        }
        setScreen(startLogout);
        void logOut(reader, screen.csrfToken, controller.signal, pageMoves(setScreen, replace));
      }}
    />
  );
}
