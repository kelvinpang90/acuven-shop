import { categoriesUrl, featuredProductsUrl, useCatalog } from "../api/catalog";
import type { CategoryListItem, ProductPage, Remote } from "../api/catalog";
import ProductCard, { CatalogImage } from "../components/ProductCard";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link, PRODUCTS_PATH } from "../router";
import { storeDesign } from "../storeDesign";
import type { HomeBlock, HomeBlockSetting } from "../storeDesign";
import { categorySearch } from "./productListQuery";

// 首页 P01（docs/UX.md P01）：★ home.demo_hint 固定在页头下方、所有区块之前，不属于任何区块、不可隐藏；
// 其后是主视觉、演示怎么玩、按分类浏览、精选商品四个区块，顺序与显隐来自 storeDesign.ts，隐藏的区块整块不渲染。
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

// 精选商品：后台挑选尚未实现，按 UX P01「未挑选」时的规则显示最新的 4 件。
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

export default function HomePage() {
  const { language } = useLanguage();
  const blocks = storeDesign.homeBlocks;
  // 只为显示中的区块请求：主视觉的图块与「精选商品」共用同一个请求。
  const categories = useCatalog<CategoryListItem[]>(isShown(blocks, "categories") ? categoriesUrl(language) : null);
  const featured = useCatalog<ProductPage>(
    isShown(blocks, "hero") || isShown(blocks, "featured") ? featuredProductsUrl(language) : null,
  );
  return <HomeView blocks={blocks} categories={categories} featured={featured} />;
}
