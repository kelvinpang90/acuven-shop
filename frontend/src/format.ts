// 金额显示：服务端返回 MYR 整数仙，这里只把它写成两位小数（UX-COPY「约定」：`RM {amount}` 的 {amount}），
// 不在浏览器计算任何金额（DESIGN「计价、优惠、积分与库存」）。只做整数转十进制字符串与切分，不经浮点除法。

// 2190 → "21.90"；0 → "0.00"；5 → "0.05"。不分千位。
export function formatSen(sen: number): string {
  if (!Number.isSafeInteger(sen) || sen < 0) {
    throw new RangeError(`amount must be a non-negative whole number of sen, got ${String(sen)}`);
  }
  const digits = String(sen).padStart(3, "0");
  return `${digits.slice(0, -2)}.${digits.slice(-2)}`;
}
