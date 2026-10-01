import { DemoHint } from "../components/SiteFrame";
import { useCopy } from "../i18n/language";

// 首页 P01。区块（主视觉、演示怎么玩、按分类浏览、精选商品）由之后的任务加入；
// 现在等同 UX 允许的「四个区块都隐藏」：只有页头下方固定位置、不可隐藏的 ★ 提示。
export default function HomePage() {
  const t = useCopy();
  return (
    <main className="site-home">
      <DemoHint>{t("home.demo_hint")}</DemoHint>
    </main>
  );
}
