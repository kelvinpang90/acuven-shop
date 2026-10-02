// 前台主题与首页区块取值的唯一来源，由全站框架的根元素与首页读取。
// 现在固定为默认主题班兰、不设主色（即该主题的默认主色）、深浅色跟随访客设备；
// 首页四个区块按 UX P01 的默认顺序全部显示。
// 之后由店铺装修任务改为读取后台 A08 保存的设置（主题、主色、区块顺序与显隐），页面代码不改。

// 首页区块（docs/UX.md P01）：主视觉、演示怎么玩、按分类浏览、精选商品。★ home.demo_hint 不是区块，不可隐藏。
export const HOME_BLOCKS = ["hero", "how", "categories", "featured"] as const;
export type HomeBlock = (typeof HOME_BLOCKS)[number];

export interface HomeBlockSetting {
  readonly block: HomeBlock;
  readonly visible: boolean;
}

const homeBlocks: readonly HomeBlockSetting[] = HOME_BLOCKS.map((block) => ({ block, visible: true }));

export const storeDesign = {
  theme: "pandan",
  mode: "auto",
  homeBlocks,
} as const;
