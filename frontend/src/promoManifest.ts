// 节日促销活动的装饰清单（DESIGN 1.23「节日促销活动」第 12 条，HANDOFF 0.98 落实写法（1），SHOP-TASK-273）：docs/design/tokens/promotions.json 的
// 素材包 ID、沿用主题配色的值与各装饰位的变体。前端镜像只打包 frontend/，页面代码读不到那份 json，所以这里写一份常量，由 promoManifest.test.ts
// 逐项对照清单。读到的 ID 都经服务端按同一清单校验过；页面只从这里列出选项，保存时由服务端再校验。

// 素材包 ID，顺序同清单的 packs。
export const PROMO_PACKS = ["cny", "raya", "deepavali", "christmas", "merdeka", "mega_sale", "generic"] as const;
export type PromoPack = (typeof PROMO_PACKS)[number];

// 沿用店铺主题配色时 palette 的值（清单的 keepThemePalette）；其余 palette 值为素材包 ID。
export const PROMO_KEEP_THEME = "theme";

// 装饰位，顺序同清单的 slots。
export const PROMO_SLOTS = ["ambient", "home_poster", "list_strip", "card_badge", "detail_ribbon", "cart_progress"] as const;
export type PromoSlot = (typeof PROMO_SLOTS)[number];

// 各装饰位的变体（各素材包相同），顺序同清单；第一个为换素材包时的取值。
export const PROMO_VARIANTS: Readonly<Record<PromoSlot, readonly string[]>> = {
  ambient: ["garland", "corners", "fall"],
  home_poster: ["split", "centered"],
  list_strip: ["band", "ticket"],
  card_badge: ["tag", "seal"],
  detail_ribbon: ["ribbon", "panel"],
  cart_progress: ["bar", "steps"],
};

// 只有页面装饰（ambient）另有「无」（清单该装饰位的 none）。
export const PROMO_AMBIENT_NONE = "none";
