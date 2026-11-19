import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";

import { fetchPreviewPromotion, previewPromotionUrl, readPreviewPromotion } from "../api/adminPromotionPreview";
import type { PreviewPromotionRead } from "../api/adminPromotionPreview";
import { readAdminPromotion } from "../api/adminPromotions";
import type { AdminPromotion, PromotionRead } from "../api/adminPromotions";
import { fetchCatalog, productsUrl } from "../api/catalog";
import type { ProductPage } from "../api/catalog";
import type { PromotionResponse } from "../api/promotion";
import { AdminAlert, useColorMode } from "../components/AdminFrame";
import type { ColorMode } from "../components/AdminFrame";
import SiteFrame from "../components/SiteFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { PreviewProvider } from "../preview";
import type { StorefrontPreview } from "../preview";
import { PromotionProvider } from "../promotion";
import { ADMIN_LOGIN_PATH, ADMIN_PROMOTIONS_PATH, adminPromotionPath, Link, matchRoute, MemoryRouter, productPath, useRouter } from "../router";
import type { RouteLocation, RouteParams, RoutePath, RoutePattern } from "../router";
import { StoreDesignProvider } from "../storeDesign";
import { startDeferred } from "./AdminOrderDetail";
import { editTarget } from "./AdminPromotionEditPage";
import CartPage from "./CartPage";
import HomePage from "./HomePage";
import ProductDetailPage from "./ProductDetailPage";
import ProductListPage from "./ProductListPage";
import SalePage from "./SalePage";

// 后台促销活动的前台预览 A12（docs/UX.md 0.20 A12「前台预览」，DESIGN 1.23「节日促销活动」第 11 条，视觉稿 A12-states 的前台预览条，
// HANDOFF 0.98 落实写法）：/admin/promotions/:id/preview（段值为活动的内部 ID）。以 /admin 开头，App.tsx 直接渲染本页，
// 前台的提供者与框架由本页自己提供。
// - 打开时先以 readAdminPromotion 取 CSRF 令牌与状态（401 以 replace 回到 A01；不存在、段值不合法与其他失败 common.error_retry，
//   网络中断 common.network_check；进行中与已结束不提供预览，同样 common.error_retry），再以 readPreviewPromotion 按当前语言读一次
//   （不可预览 admin.promo_preview_needs_time，其余同上）。切换语言不重跑这两次读取。
// - 预览条（div.acs-admin.acs-admin__previewbar）在所有状态下都显示：promo.preview_bar、五个页签与 promo.preview_exit（回到编辑区）；
//   读取中与失败时页签禁用，下面只有提示。读到后，下面是该活动正在生效的前台：StoreDesignProvider、PromotionProvider（预览接口，
//   倒计时到 0 停在 0）、PreviewProvider、内存路由与 SiteFrame，内容为当前位置对应的前台页面。
// - 内存路由只接受首页、商品列表（含查询串）、商品详情、购物车与专题页五种位置，其余路径点击后留在原处；地址栏始终是本页。
// 预览只取数与计价（购物车的结账禁用由 CartPage 负责），不下单、不碰个人资料。

// 打开的是哪一个活动：段值合法时为它的内部 ID，否则为 null（按不存在处理，不发请求）。
export function previewTarget(pattern: RoutePattern, params: RouteParams): number | null {
  const target = editTarget(pattern, params);
  return target.kind === "existing" ? target.id : null;
}

// promo.preview_exit 的去向：段值合法时回到该活动的编辑区，否则回到活动列表。
export function exitPath(promotionId: number | null): RoutePath {
  return promotionId === null ? ADMIN_PROMOTIONS_PATH : adminPromotionPath(promotionId);
}

export type PreviewError = "common.error_retry" | "common.network_check" | "admin.promo_preview_needs_time";

// 页面的状态：读取中、只有提示（失败与不可预览），或前台预览（预览上下文、读到的活动与是否有商品级规则）。
export type PreviewState =
  | { status: "loading" }
  | { status: "gone"; error: PreviewError }
  | { status: "ready"; preview: StorefrontPreview; response: PromotionResponse; itemRules: boolean };

export const LOADING: PreviewState = { status: "loading" };

// 商品级规则的类别：活动有其中之一时，商品详情页签先取活动商品。
const ITEM_RULE_KINDS: ReadonlySet<string> = new Set(["item_discount", "buy_x_get_y"]);

export function hasItemRules(promotion: AdminPromotion): boolean {
  return promotion.rules.some((rule) => ITEM_RULE_KINDS.has(rule.kind));
}

// 第一次读取（readAdminPromotion）之后：取到草稿或未开始的活动时继续读预览；进行中与已结束不读预览、显示 common.error_retry；
// 401 回到 A01；不存在与其他失败 common.error_retry；网络中断 common.network_check。
export type AdminReadStep = { kind: "next"; csrfToken: string; itemRules: boolean } | { kind: "show"; state: PreviewState } | { kind: "login" };

export function adminReadStep(read: PromotionRead): AdminReadStep {
  switch (read.kind) {
    case "ok":
      if (read.promotion.state === "running" || read.promotion.state === "ended") {
        return { kind: "show", state: { status: "gone", error: "common.error_retry" } };
      }
      return { kind: "next", csrfToken: read.csrf_token, itemRules: hasItemRules(read.promotion) };
    case "none":
      return { kind: "login" };
    case "missing":
    case "failed":
      return { kind: "show", state: { status: "gone", error: "common.error_retry" } };
    case "network":
      return { kind: "show", state: { status: "gone", error: "common.network_check" } };
  }
}

// 第二次读取（readPreviewPromotion）之后：取到时为前台预览；不可预览 admin.promo_preview_needs_time；其余同第一次。
export function previewReadStep(read: PreviewPromotionRead, preview: StorefrontPreview, itemRules: boolean): PreviewState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", preview, response: read.response, itemRules };
    case "unavailable":
      return { status: "gone", error: "admin.promo_preview_needs_time" };
    case "none":
      return "login";
    case "missing":
    case "failed":
      return { status: "gone", error: "common.error_retry" };
    case "network":
      return { status: "gone", error: "common.network_check" };
  }
}

// 两次读取所用的接口；测试传入替身。
export interface PreviewApi {
  readOne: (promotionId: number, signal?: AbortSignal) => Promise<PromotionRead>;
  readPreview: (promotionId: number, language: Language, signal?: AbortSignal) => Promise<PreviewPromotionRead>;
}

export const PREVIEW_API: PreviewApi = { readOne: readAdminPromotion, readPreview: readPreviewPromotion };

// 页面对结果的处理：换成某个状态，或用路由的 replace 回到 A01（不留本页的历史记录）。
export interface PreviewMoves {
  show: (state: PreviewState) => void;
  replace: (path: RoutePath) => void;
}

// 打开时的两次读取：段值不合法时不发请求；signal 中止（离开页面）之后不再更新。
export async function loadPreview(
  promotionId: number | null,
  language: Language,
  signal: AbortSignal,
  moves: PreviewMoves,
  api: PreviewApi = PREVIEW_API,
): Promise<void> {
  if (promotionId === null) {
    moves.show({ status: "gone", error: "common.error_retry" });
    return;
  }
  const first = adminReadStep(await api.readOne(promotionId, signal));
  if (signal.aborted) {
    return;
  }
  if (first.kind === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return;
  }
  if (first.kind === "show") {
    moves.show(first.state);
    return;
  }
  const preview: StorefrontPreview = { promotionId, csrfToken: first.csrfToken };
  const step = previewReadStep(await api.readPreview(promotionId, language, signal), preview, first.itemRules);
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 预览的五个页签（视觉稿 A12-states 的顺序）。
export const PREVIEW_TABS = ["home", "list", "detail", "cart", "sale"] as const;
export type PreviewTab = (typeof PREVIEW_TABS)[number];

const TAB_LABEL: Readonly<Record<PreviewTab, CopyKey>> = {
  home: "admin.preview_home",
  list: "admin.preview_list",
  detail: "admin.preview_detail",
  cart: "admin.preview_cart",
  sale: "admin.preview_sale",
};

// 预览里可去的前台位置：路由表模式 → 页签与页面。
type PreviewPattern = "/" | "/products" | "/products/:slug" | "/cart" | "/sale";

const PREVIEW_PAGES: Readonly<Record<PreviewPattern, { tab: PreviewTab; page: ComponentType }>> = {
  "/": { tab: "home", page: HomePage },
  "/products": { tab: "list", page: ProductListPage },
  "/products/:slug": { tab: "detail", page: ProductDetailPage },
  "/cart": { tab: "cart", page: CartPage },
  "/sale": { tab: "sale", page: SalePage },
};

function isPreviewPattern(pattern: RoutePattern): pattern is PreviewPattern {
  return pattern in PREVIEW_PAGES;
}

// 除商品详情外各页签打开的位置。
const TAB_PATH: Readonly<Record<Exclude<PreviewTab, "detail">, RoutePath>> = {
  home: "/",
  list: "/products",
  cart: "/cart",
  sale: "/sale",
};

// 预览的初始位置：首页。
export const PREVIEW_START: RouteLocation = { path: "/", search: "" };

// 内存路由要换到的位置：五种前台位置之一时为该位置（只有商品列表保留查询串），其余路径（会员、查单、隐私说明等）为 null，即留在原处。
export function previewLocation(next: RouteLocation): RouteLocation | null {
  const match = matchRoute(next.path);
  if (match === null || !isPreviewPattern(match.pattern)) {
    return null;
  }
  return { path: next.path, search: match.pattern === "/products" ? next.search : "" };
}

// 当前位置对应的页签与页面；不在五种位置里时为 null。
function previewPage(location: RouteLocation): { tab: PreviewTab; page: ComponentType } | null {
  const match = matchRoute(location.path);
  return match !== null && isPreviewPattern(match.pattern) ? PREVIEW_PAGES[match.pattern] : null;
}

export function currentTab(location: RouteLocation): PreviewTab | null {
  return previewPage(location)?.tab ?? null;
}

// 取一页预览商品列表；测试传入替身。
export type PreviewProductsFetcher = (url: string, signal: AbortSignal, preview: StorefrontPreview) => Promise<ProductPage>;

const fetchPreviewProducts: PreviewProductsFetcher = (url, signal, preview) => fetchCatalog<ProductPage>(url, signal, preview);

// 商品详情页签打开的商品：有商品级规则时取预览商品列表 sale=true 的第一件，没有商品级规则或该列表为空时取按最新排序的第一件；
// 都没有商品或取数失败时为 null（不换页、不提示）。
export async function pickDetailProduct(
  itemRules: boolean,
  language: Language,
  signal: AbortSignal,
  preview: StorefrontPreview,
  fetcher: PreviewProductsFetcher = fetchPreviewProducts,
): Promise<string | null> {
  try {
    if (itemRules) {
      const sale = await fetcher(productsUrl(language, { sale: true, sort: "newest", page: 1 }), signal, preview);
      const first = sale.items[0];
      if (first !== undefined) {
        return first.slug;
      }
    }
    const newest = await fetcher(productsUrl(language, { sort: "newest", page: 1 }), signal, preview);
    return newest.items[0]?.slug ?? null;
  } catch {
    return null;
  }
}

export interface PreviewBarProps {
  mode: ColorMode;
  // 当前页签（aria-current）；读取中与失败时为 null。
  current: PreviewTab | null;
  disabled: boolean;
  exit: RoutePath;
  onTab: (tab: PreviewTab) => void;
}

// 预览条：sticky 在顶、不可关闭，在被预览的 .acs 之前；promo.preview_exit 为回到编辑区的链接（走外面的路由）。
export function PreviewBar({ mode, current, disabled, exit, onTab }: PreviewBarProps) {
  const t = useCopy();
  return (
    <div className="acs-admin acs-admin__previewbar" data-mode={mode}>
      <strong>{t("promo.preview_bar")}</strong>
      <nav aria-label={t("admin.preview")}>
        {PREVIEW_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            aria-current={tab === current ? "page" : undefined}
            disabled={disabled}
            onClick={() => {
              onTab(tab);
            }}
          >
            {t(TAB_LABEL[tab])}
          </button>
        ))}
      </nav>
      <Link className="acs-admin__btn acs-admin__btn--secondary" to={exit}>
        {t("promo.preview_exit")}
      </Link>
    </div>
  );
}

export interface PreviewStorefrontProps {
  preview: StorefrontPreview;
  response: PromotionResponse;
  location: RouteLocation;
  onNavigate: (next: RouteLocation) => void;
}

// 被预览的前台：活动按预览接口读取（切换语言时由 PromotionProvider 按 url 重读），倒计时到 0 停在 0、不去掉活动；
// 页面以路径为 key，从一件商品换到另一件时重新开始。
export function PreviewStorefront({ preview, response, location, onNavigate }: PreviewStorefrontProps) {
  const { promotionId } = preview;
  const url = useCallback((language: Language) => previewPromotionUrl(promotionId, language), [promotionId]);
  const Page = previewPage(location)?.page ?? HomePage;
  return (
    <StoreDesignProvider>
      <PromotionProvider initial={response} url={url} fetcher={fetchPreviewPromotion} holdAtZero>
        <PreviewProvider preview={preview}>
          <MemoryRouter location={location} onNavigate={onNavigate}>
            <SiteFrame>
              <Page key={location.path} />
            </SiteFrame>
          </MemoryRouter>
        </PreviewProvider>
      </PromotionProvider>
    </StoreDesignProvider>
  );
}

export interface AdminPromotionPreviewViewProps {
  mode: ColorMode;
  state: PreviewState;
  location: RouteLocation;
  // 商品详情页签正在取商品。
  picking: boolean;
  exit: RoutePath;
  onTab: (tab: PreviewTab) => void;
  onNavigate: (next: RouteLocation) => void;
}

// 预览条在所有状态下都显示；读取中与失败时页签禁用，下面只有提示（读取中标 aria-busy），读到后为被预览的前台。
export function AdminPromotionPreviewView({ mode, state, location, picking, exit, onTab, onNavigate }: AdminPromotionPreviewViewProps) {
  const ready = state.status === "ready";
  return (
    <div className="site-promo-preview">
      <PreviewBar mode={mode} current={ready ? currentTab(location) : null} disabled={!ready || picking} exit={exit} onTab={onTab} />
      {state.status === "ready" ? (
        <PreviewStorefront preview={state.preview} response={state.response} location={location} onNavigate={onNavigate} />
      ) : (
        <div className="acs-admin site-promo-preview__status" data-mode={mode} aria-busy={state.status === "loading"}>
          {state.status === "gone" && <AdminAlert error={state.error} />}
        </div>
      )}
    </div>
  );
}

export default function AdminPromotionPreviewPage() {
  const mode = useColorMode();
  const { language } = useLanguage();
  const { pattern, params, replace } = useRouter();
  const segment = params.id;
  const promotionId = useMemo(() => previewTarget(pattern, segment === undefined ? {} : { id: segment }), [pattern, segment]);
  const [state, setState] = useState<PreviewState>(LOADING);
  const [location, setLocation] = useState<RouteLocation>(PREVIEW_START);
  const [picking, setPicking] = useState(false);
  // 打开时按当时的语言读一次；之后切换语言不重跑两次读取（活动由 PromotionProvider 重读，目录由各页重取）。
  const languageNow = useRef(language);
  // 本页的请求（两次读取与详情页签取商品）共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    languageNow.current = language;
  }, [language]);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void loadPreview(promotionId, languageNow.current, controller.signal, { show: setState, replace });
    });
  }, [promotionId, replace]);

  // 内存路由的换位置：五种前台位置之外的路径留在原处。
  const onNavigate = useCallback((next: RouteLocation) => {
    const allowed = previewLocation(next);
    if (allowed !== null) {
      setLocation(allowed);
    }
  }, []);

  const onTab = (tab: PreviewTab) => {
    if (state.status !== "ready" || picking) {
      return;
    }
    if (tab !== "detail") {
      onNavigate({ path: TAB_PATH[tab], search: "" });
      return;
    }
    const controller = lifetime.current;
    if (controller === null) {
      return;
    }
    setPicking(true);
    void pickDetailProduct(state.itemRules, language, controller.signal, state.preview).then((slug) => {
      if (controller.signal.aborted) {
        return;
      }
      setPicking(false);
      if (slug !== null) {
        onNavigate({ path: productPath(slug), search: "" });
      }
    });
  };

  return (
    <AdminPromotionPreviewView
      mode={mode}
      state={state}
      location={location}
      picking={picking}
      exit={exitPath(promotionId)}
      onTab={onTab}
      onNavigate={onNavigate}
    />
  );
}
