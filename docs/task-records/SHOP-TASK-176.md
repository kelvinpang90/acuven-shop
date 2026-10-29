# SHOP-TASK-176 后台运费与演示汇率 A06 的删除与新增行

- [x] 依据：`docs/UX.md` 0.16 A06「增删」、`docs/UX-COPY.md` 0.17、`docs/HANDOFF.md` 0.72（2026-10-29 登记 A06 页面一条的第（4）到（8）点）与视觉稿 `A06-desktop`、`A06-phone`。设计闸门：`docs/DESIGN.md` 1.17（提交 `80e3775`）「计价、优惠、积分与库存」第 9 条与 HANDOFF 0.72——运费与汇率行的增删，核对与审计在服务端。本任务不装依赖、不改后端、不改设计；`frontend/src/router.tsx` 与 `frontend/src/App.tsx` 不需要改动（没有新路由），未改。
- [x] 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.17 逐字加入字典里还没有的六条：`admin.shipping_country`、`admin.add_country`（在 `admin.shipping_fee` 之后）与 `admin.remove`、`admin.add_fx`、`admin.fx_currency`、`admin.fx_decimals`（在 `admin.fx_rate_new` 之前），写法同相邻条目。`admin.fx_rate_new` 已由 SHOP-TASK-175 加入。没有用到 UX-COPY 以外的界面文字。
- [x] 删除（`frontend/src/pages/AdminShippingPage.tsx`）：按国家与演示汇率两块的每行（含新增的行）在输入框右边有次要按钮 `admin.remove`（`type="button"`），马来西亚两档与其他国家没有。纯函数 `removeRow` 把该行从页面去掉，不弹确认框、不发请求：现有的行记下读取时的 id（`removedCountries`、`removedFx`，输入框文字仍按原位置保留、不显示），新增的行直接丢弃。无障碍标签为「`admin.remove`, 行名」：现有的国家行与汇率行为国家名称，新增的两种行为该行当前选中的国家名称（HANDOFF 0.72 第（6）点；画板上 `Remove, SGD`、`Remove, Indonesia, IDR` 的拼法不照搬）。
- [x] 新增国家运费：按国家一块的末尾（各行之后）有次要按钮 `admin.add_country`；纯函数 `addRow(state, "countries", language)` 在该块末尾加一行：国家下拉（无障碍标签 `admin.shipping_country`）、空白的运费输入框（无障碍标签「`admin.shipping_fee`, 当前选中的国家名称」，`inputmode="decimal"`）与 `admin.remove`。
- [x] 新增汇率：演示汇率一块的末尾有次要按钮 `admin.add_fx`；`addRow(state, "fx", language)` 加一行：带可见标签 `admin.shipping_country` 的国家下拉与 `admin.fx_currency`（`maxLength` 3、`autoCapitalize="characters"`）、`admin.fx_decimals`（`inputmode="numeric"`）、`admin.fx_rate_new`（`inputmode="decimal"`）三个带可见标签的输入框（`acs-admin__field`，不另加 `aria-label`），以及 `admin.remove`。已有的汇率行仍只有汇率输入框。
- [x] 国家选项（HANDOFF 0.72 第（7）点）：导出纯函数 `countryOptions`（收货国家列表经 `checkoutPhone.ts` 的 `regionOptions` 按名称排序、只显示名称，去掉 `MY` 与已占用的国家，该行自己选中的国家仍在选项里）、`pageCountries`（一块里页面上现有的国家：没有被删的现有行与全部新增行当前选中的国家）与 `addChoices`（新增按钮可加的国家）。新增行默认选中第一个可选国家；同一块的新增行之间互不重复；已点删除的现有国家可再选（保存时同一请求先删后增）；两块各自判断。纯函数 `selectCountry` 改新增行的国家，行名随之改变。收货国家列表读取失败（`regions` 为 null）或已没有可选国家时新增按钮禁用、不另加提示，`addRow` 不改状态。
- [x] 收货国家列表的读取：`openRates`（打开页面、切换语言、`admin.reload` 与保存 403 后的重新读取都经过它）与整页读取并行调用 `api/checkout.ts` 的 `fetchRegions`（GET `/api/checkout/regions`），失败（非 2xx、网络中断、回答缺少列表、中止）为 null，只禁用两个新增按钮，表单其余部分照常；下一次整页读取成功即恢复。`readStep` 多一个可省略的参数 `regions`，页面状态 `ready` 多一项 `regions`。保存成功不是整页读取，沿用保存时的列表。
- [x] 保存内容与重新读取：`saveContent` 按页面顺序列出现有的行，被删的国家与汇率以删除的行（`{ id, version, delete: true }`）列在原位置，新增的行排在该组末尾——国家运费为 `{ country_code, fee_sen }`（运费照 SHOP-TASK-175 的 `feeSen` 换成整仙），汇率为 `{ country_code, currency_code, currency_decimals, rate }`（导出纯函数 `newFxItem`：币种 `^[A-Za-z]{3}$` 转大写，小数位 `^[0-3]$` 换成整数，汇率照 `isRateText` 原样）；新增后又删掉的行不在内；页面上任一输入框（含新增行）格式不对为 null，`submitRates` 照 SHOP-TASK-175 不发请求、显示 `admin.rates_invalid`。被删的行不在页面上，其文字不检查。增删任何一行或改新增行的国家选择都收起保存按钮下方的全部提示，表单上方 403 后的提示不收起。ok 后照 SHOP-TASK-175 以返回的整页数据重置表单（新增的行成为现有的行，删除的行消失，增删清空）；`admin.reload` 重新读取时丢弃增删。
- [x] 容器 `AdminShippingContent` 的 `onAdd`、`onRemove`、`onSelect` 与原来的 `onChange` 共用一个只改当前语言状态的 `edit`，都不返回 Promise；`addRow` 用当前界面语言排序选项。
- [x] 样式：`frontend/src/styles/site.css` 在 `.site-admin-shipping*` 里加入：国家与汇率的每行为 `.site-admin-shipping__line` 网格（名称、输入框、84px 的删除按钮，间距 10px；原来的行标签在其中为 `display: contents`），国家行输入框 110px、汇率行 96px；新增的国家行占满两列，列为 `minmax(0, 240px) 110px 84px`；新增的汇率行为两列四个字段（间距 10px、上方 12px），删除按钮另起一行靠右；删除按钮文字居中，新增按钮靠左；下拉照输入框限制最小宽度。手机（767px 及以下）：按国家的行（含新增的）为 `minmax(0, 1fr) 96px 84px`；汇率行为名称与 96px 的输入框，名称一栏跨两行，删除按钮在输入框下方靠右，每行上方 10px（原写在行标签上，行标签改为 `display: contents` 后移到行上）；删除与新增按钮至少 44px 高。没有声明颜色、字体与圆角，没有借用其他组件的类取颜色，未改 `acuven-shop.css`。
- 偏离与说明：
  - 视觉稿里 site.css 写不了而没有搬入的：新增汇率行上方的虚线（`border-top` 的颜色）、新增汇率行各字段文字的字号 13px。
  - 手机的汇率行：A06-phone 把版本标签与删除按钮放在汇率行下方同一行的两端；SHOP-TASK-175 已把版本标签放在汇率文字之下，本任务让名称一栏跨两行、删除按钮放在输入框下方靠右，两者大致在同一高度的左右两边，但不保证与画板逐像素一致。
  - 下拉的选项只列可选国家，不加空白项（HANDOFF 0.72「默认选中第一个可选国家」，所以始终有选中的国家与行名）。
  - 选项的排序与名称用 `regionOptions` 与 `countryNames`（`Intl.DisplayNames`），与结账页相同。
  - 新增汇率行的币种只查「三个字母」，`MYR` 与每个国家只能有一行等取值规则以服务端为准（回 `rates_invalid` 时照 SHOP-TASK-175 显示 `admin.rates_invalid`）；输入不去掉首尾空白，空白算格式不对（同 SHOP-TASK-175）。
  - 保存期间删除、新增与下拉不禁用（与 SHOP-TASK-175 保存期间输入框可改的写法相同），保存结果到达时照常以返回数据或失败提示更新页面。
  - UX 与视觉稿在本任务的范围内没有冲突。
- [x] 测试：`frontend/src/pages/AdminShippingPage.test.tsx`（vitest 与 react-dom/server，fetch 以 `vi.fn((input, init) => …)` 替身代替、从 `mock.calls` 取值时用 `String(…)` 收窄；以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击），每条注明守住的 UX、设计或 HANDOFF 0.72 的句子。
  - 新增的测试：删除按钮只在国家与汇率行出现（马来西亚与其他国家没有）及其无障碍标签、次要按钮与 `type`；删除现有行只改状态、不发请求、从页面去掉，保存内容在原位置为删除的行；新增后又删掉的行不进保存内容；已删的国家可再选，保存内容同时有删除与新增；新增国家行的位置、下拉（无障碍标签与选项、默认选中）、空白运费输入框的无障碍标签与删除按钮，及其在保存内容里的整仙运费；新增汇率行的位置、四个可见标签、下拉与输入框没有 `aria-label`、`maxlength` 3 与 `inputmode`、删除按钮的标签，已有的汇率行仍只有汇率输入框；选项不含 MY、不含该块已有国家、按名称排序（与 `regionOptions` 一致，英文下 Switzerland 排在 Germany、Indonesia 之后）、两块各自判断、新增行互不重复且保留自己选中的国家、改选后其他行的选项与行名随之改变、用完后新增按钮禁用；列表为 null、为空或没有可选国家时新增按钮禁用且 `addRow` 不改状态；`newFxItem` 的转大写、小数位换整数与各种不合格的币种、小数位、汇率；新增行格式不对时不调用 fetch、显示 `admin.rates_invalid`；保存内容的顺序与 PUT 请求体；ok 后新增的行成为现有的行、删除的行消失、列表沿用；增删与选国家收起五种提示而表单上方的提示留着；打开页面与 `admin.reload` 一起取得列表、`admin.reload` 丢弃增删；列表回 500、网络中断或缺少列表时只禁用两个新增按钮，下一次读取成功即恢复；有增删行时各状态的页面文字全部来自字典（国家名称、汇率行名称、填值的字典文案与以「, 」拼接的无障碍标签除外）。
  - 因本任务而改写的 SHOP-TASK-175 断言（各条测试注释里写明「SHOP-TASK-176 改动」）：
    1. 「reads the whole page only after mounting」：`openRates` 多发 GET `/api/checkout/regions`，「只发一个 GET」改为依次两个 GET（fetch 替身的调用次数与地址）。
    2. 夹具 `EDITED`：`RatesInputs` 多了 `removedCountries`、`removedFx`、`newCountries`、`newFx` 四项，补上空值（页面状态变化）。
    3. 夹具函数 `ready()`：页面状态多了 `regions`，默认为 null（页面状态变化）。
    4. 夹具函数 `render()`：View 多了 `onAdd`、`onRemove`、`onSelect` 三个回调（View 的 props 变化）。
    5. 「lists the three fee blocks, then the demo rates and save」：每行多了 `admin.remove`、两块末尾多了新增按钮，文字顺序加上这些按钮，「表单里只有保存一个按钮」改为 8 个按钮（删除与新增按钮）。
    6. 「shows only dictionary text in every state」：允许范围加上「`admin.remove`, 国家名称」的无障碍标签（删除按钮）。
    7. 「reloads like opening the page and drops unsaved edits」：请求列表多了 GET `/api/checkout/regions`，期望的状态多了新取得的 `regions`（fetch 替身的调用次数与地址、页面状态变化）。
    8. 「rereads after a CSRF refusal and shows error_retry above the form」：请求由 PUT、GET 改为 PUT、GET、GET（多了收货国家列表），期望的状态多了 `regions`（fetch 替身的调用次数与地址、页面状态变化）。
  - 其余 SHOP-TASK-173、175 的测试未改。
- 验证到什么程度：人工逐条对照 UX A06「增删」、UX-COPY 0.17 的六条文案、HANDOFF 0.72 第（4）到（8）点与两张画板；上面列出的是本次新增或修改的测试代码，前端的 lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI `frontend` 执行（Worker 沙箱不跑前端检查）。未做浏览器验收（包括桌面与手机上删除按钮、新增行的实际排版与下拉的表现）。
