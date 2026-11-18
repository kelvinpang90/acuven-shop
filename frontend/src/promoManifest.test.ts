import { describe, expect, it } from "vitest";

// 经 Vite 的 ?raw 读成字符串（照 i18n/copy.test.ts 读 UX-COPY 的写法）：不 import node:fs，不给 tsconfig.app.json 下的代码引入 Node 类型。
import manifestText from "../../docs/design/tokens/promotions.json?raw";
import { PROMO_AMBIENT_NONE, PROMO_KEEP_THEME, PROMO_PACKS, PROMO_SLOTS, PROMO_VARIANTS } from "./promoManifest";

interface ManifestSlot {
  id: string;
  variants: string[];
  none?: string;
}

interface Manifest {
  keepThemePalette: string;
  slots: ManifestSlot[];
  packs: { id: string; variants: Record<string, string[]> }[];
}

const manifest = JSON.parse(manifestText) as Manifest;

describe("promoManifest against docs/design/tokens/promotions.json", () => {
  // UX 0.20 A12「[admin.promo_pack]    <素材包> ▾（[admin.pack_*] 七项）」与说明「素材包与配色从清单中选」：素材包 ID 与顺序同清单。
  it("lists the seven packs in the manifest's order", () => {
    expect(manifest.packs).toHaveLength(7);
    expect(PROMO_PACKS).toEqual(manifest.packs.map((pack) => pack.id));
  });

  // UX 0.20 A12「[admin.promo_palette] ( ) [admin.palette_keep]  ( ) [admin.palette_of] ▾」：沿用主题配色的值同清单的 keepThemePalette。
  it("keeps the theme palette value of the manifest", () => {
    expect(PROMO_KEEP_THEME).toBe(manifest.keepThemePalette);
  });

  // UX 0.20 A12 说明「各装饰位的下拉列出所选素材包的变体」：装饰位与顺序同清单，每个装饰位的变体同清单的 slots，也同每个素材包自己的 variants。
  it("has the variants of every slot and every pack", () => {
    expect(PROMO_SLOTS).toEqual(manifest.slots.map((slot) => slot.id));
    for (const slot of manifest.slots) {
      expect(PROMO_VARIANTS[slot.id as (typeof PROMO_SLOTS)[number]], slot.id).toEqual(slot.variants);
    }
    for (const pack of manifest.packs) {
      expect(Object.keys(pack.variants), pack.id).toEqual([...PROMO_SLOTS]);
      for (const slot of PROMO_SLOTS) {
        expect(PROMO_VARIANTS[slot], `${pack.id} ${slot}`).toEqual(pack.variants[slot]);
      }
    }
  });

  // UX 0.20 A12「[admin.slot_ambient]  <变体> ▾（含 [admin.variant_none]）」：只有页面装饰另有 none，值同清单。
  it("has none only for the page ornaments", () => {
    expect(manifest.slots.filter((slot) => slot.none !== undefined).map((slot) => [slot.id, slot.none])).toEqual([["ambient", PROMO_AMBIENT_NONE]]);
  });
});
