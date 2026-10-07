import { categoriesUrl, featuredBySlugsUrl, featuredProductsUrl, useCatalog } from "../api/catalog";
import type { CategoryListItem, ProductPage, Remote } from "../api/catalog";
import ProductCard, { CatalogImage } from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link, PRODUCTS_PATH } from "../router";
import { useStoreDesign } from "../storeDesign";
import type { HomeBlock, HomeBlockSetting, StoreDesignValue } from "../storeDesign";
import { categorySearch } from "./productListQuery";

// 首页 P01（docs/UX.md P01）：★ home.demo_hint 固定在页头下方、所有区块之前，不属于任何区块、不可隐藏；
// 其后是主视觉、演示怎么玩、按分类浏览、精选商品四个区块，顺序与显隐来自店铺装修设置（storeDesign.ts），隐藏的区块整块不渲染。
// 区块文案全部来自字典；分类与商品来自目录接口，某个请求失败只在该区块显示 common.error_retry。

const HOW_STEPS: readonly CopyKey[] = ["home.how_1", "home.how_2", "home.how_3", "home.how_4"];
const HERO_TILES = 3;

// 主视觉：右侧三个装饰图块取精选商品的前三张图片（没有时为占位形状），不是链接，读屏忽略。
function HeroBlock({ featured }: { featured: Remote<ProductPage> }) {
  const t = useCopy();
  // 前三件精选商品各占一块；无图的商品保留位置显示占位形状，不补取后续商品的图片。
  const items = featured.status === "ready" ? featured.data.items : [];
  const tiles = Array.from({ length: HERO_TILES }, (_, index) => items[index]?.image || null);
  return (
    <section className="acs-panel site-hero">
      <div className="site-hero__text">
        <h1 className="acs-display-xl">{t("home.hero_title")}</h1>
        <p className="acs-body-l acs-muted site-hero__body">{t("home.hero_body")}</p>
        <Link className="acs-btn acs-btn--primary acs-btn--lg site-hero__cta" to={PRODUCTS_PATH}>
          {t("home.hero_cta")}
        </Link>
      </div>
      <div className="site-hero__tiles" aria-hidden="true">
        {tiles.map((src, index) => (
          <span key={index} className="acs-cat__img site-hero__tile">
            <CatalogImage src={src} />
          </span>
        ))}
      </div>
    </section>
  );
}

// 演示怎么玩：编号由有序列表与 site.css 的计数器给出，页面上不另写数字。
function HowBlock() {
  const t = useCopy();
  return (
    <section className="site-home__block site-how">
      <h2 className="acs-display-m">{t("home.how_title")}</h2>
      <ol className="site-how__steps">
        {HOW_STEPS.map((key) => (
          <li key={key}>
            <span className="acs-display-m site-how__number" aria-hidden="true" />
            <span>{t(key)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

// 按分类浏览：每块是分类图片（为空时占位形状）与名称，链到按该分类筛选的商品列表。
function CategoriesBlock({ categories }: { categories: Remote<CategoryListItem[]> }) {
  const t = useCopy();
  return (
    <section className="site-home__block">
      <h2 className="acs-display-m">{t("home.categories")}</h2>
      {categories.status === "error" ? (
        <ErrorNotice />
      ) : (
        <div className="site-cats" aria-busy={categories.status === "loading"}>
          {categories.status === "ready" &&
            categories.data.map((category) => {
              const lang = category.name.english_fallback ? "en" : undefined;
              return (
                <Link key={category.slug} className="acs-cat" to={PRODUCTS_PATH} search={categorySearch(category.slug)}>
                  <span className="acs-cat__img">
                    <CatalogImage src={category.image} />
                  </span>
                  <span className="acs-display-s site-desktop-only" lang={lang}>
                    {category.name.text}
                  </span>
                  <span className="site-phone-only" lang={lang}>
                    {category.name.text}
                  </span>
                </Link>
              );
            })}
        </div>
      )}
    </section>
  );
}

// 精选商品：按 A08 挑选的顺序显示，未挑选或都取不到时显示最新的 4 件（见 featuredSource）。
function FeaturedBlock({ featured }: { featured: Remote<ProductPage> }) {
  const t = useCopy();
  return (
    <section className="site-home__block">
      <h2 className="acs-display-m">{t("home.featured")}</h2>
      {featured.status === "error" ? (
        <ErrorNotice />
      ) : (
        <div className="acs-pgrid" aria-busy={featured.status === "loading"}>
          {featured.status === "ready" &&
            featured.data.items.map((product) => <ProductCard key={product.slug} product={product} />)}
        </div>
      )}
    </section>
  );
}

export interface HomeViewProps {
  blocks: readonly HomeBlockSetting[];
  categories: Remote<CategoryListItem[]>;
  featured: Remote<ProductPage>;
}

function HomeSection({ block, categories, featured }: Omit<HomeViewProps, "blocks"> & { block: HomeBlock }) {
  switch (block) {
    case "hero":
      return <HeroBlock featured={featured} />;
    case "how":
      return <HowBlock />;
    case "categories":
      return <CategoriesBlock categories={categories} />;
    case "featured":
      return <FeaturedBlock featured={featured} />;
  }
}

export function HomeView({ blocks, categories, featured }: HomeViewProps) {
  const t = useCopy();
  return (
    <main className="site-home">
      <DemoHint>{t("home.demo_hint")}</DemoHint>
      {blocks
        .filter((setting) => setting.visible)
        .map((setting) => (
          <HomeSection key={setting.block} block={setting.block} categories={categories} featured={featured} />
        ))}
    </main>
  );
}

function isShown(blocks: readonly HomeBlockSetting[], block: HomeBlock): boolean {
  return blocks.some((setting) => setting.block === block && setting.visible);
}

const LOADING: Remote<never> = { status: "loading" };

// 只为显示中的区块请求：主视觉的图块与「精选商品」共用同一组商品。
export function categoriesRequest(language: Language, blocks: readonly HomeBlockSetting[]): string | null {
  return isShown(blocks, "categories") ? categoriesUrl(language) : null;
}

// 精选商品要用的挑选：设置还没返回、或主视觉与精选商品都隐藏时为 null，即先不取（也不取最新 4 件）。
export function featuredSlugsToShow(design: Pick<StoreDesignValue, "homeBlocks" | "featuredSlugs">): readonly string[] | null {
  const { homeBlocks, featuredSlugs } = design;
  return isShown(homeBlocks, "hero") || isShown(homeBlocks, "featured") ? featuredSlugs : null;
}

// 有挑选时按 slug 取商品卡片（SHOP-TASK-053）。
export function pickedRequest(language: Language, slugs: readonly string[] | null): string | null {
  return slugs !== null && slugs.length > 0 ? featuredBySlugsUrl(language, slugs) : null;
}

// 按挑选顺序排返回的商品（接口按 newest 返回）；没返回的 slug 略去，重复的 slug 只算一次。
export function orderedFeatured(page: ProductPage, slugs: readonly string[]): ProductPage {
  const items = [...new Set(slugs)].flatMap((slug) => page.items.filter((item) => item.slug === slug).slice(0, 1));
  return { ...page, items };
}

export type FeaturedSource =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "picked"; page: ProductPage }
  | { kind: "newest" };

// 精选商品取自哪里（REQUIREMENTS「店铺装修」、UX P01）：按挑选顺序只显示仍上架的商品；未挑选（含设置请求失败）
// 或按 slug 的请求成功但一件也没返回时显示最新 4 件；按 slug 的请求本身失败时在区块显示错误，不改取最新 4 件。
export function featuredSource(slugs: readonly string[] | null, picked: Remote<ProductPage>): FeaturedSource {
  if (slugs === null) {
    return { kind: "loading" };
  }
  if (slugs.length === 0) {
    return { kind: "newest" };
  }
  switch (picked.status) {
    case "loading":
      return { kind: "loading" };
    case "ready": {
      const page = orderedFeatured(picked.data, slugs);
      return page.items.length > 0 ? { kind: "picked", page } : { kind: "newest" };
    }
    default:
      return { kind: "error" };
  }
}

export function newestRequest(language: Language, source: FeaturedSource): string | null {
  return source.kind === "newest" ? featuredProductsUrl(language) : null;
}

export function featuredRemote(source: FeaturedSource, newest: Remote<ProductPage>): Remote<ProductPage> {
  switch (source.kind) {
    case "loading":
      return LOADING;
    case "error":
      return { status: "error" };
    case "picked":
      return { status: "ready", data: source.page };
    case "newest":
      return newest;
  }
}

export default function HomePage() {
  const { language } = useLanguage();
  const design = useStoreDesign();
  const blocks = design.homeBlocks;
  const categories = useCatalog<CategoryListItem[]>(categoriesRequest(language, blocks));
  // 精选在设置返回后才取：先按挑选取，必要时再取最新 4 件。
  const slugs = featuredSlugsToShow(design);
  const picked = useCatalog<ProductPage>(pickedRequest(language, slugs));
  const source = featuredSource(slugs, picked);
  const newest = useCatalog<ProductPage>(newestRequest(language, source));
  return <HomeView blocks={blocks} categories={categories} featured={featuredRemote(source, newest)} />;
}
