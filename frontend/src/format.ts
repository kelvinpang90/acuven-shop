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

// 参考外币可用的小数位数上限（与服务端 app/services/shipping.py 的 MAX_CURRENCY_DECIMALS 相同）。
export const MAX_CURRENCY_DECIMALS = 3;

// 参考外币金额（结账页 P05 的 common.fx_reference 的 {amount}）：服务端给出该币种最小单位的整数与小数位数，
// 这里只按位数切分，同样不经浮点。2295 与 2 位 → "22.95"；1250 与 0 位 → "1250"；5 与 3 位 → "0.005"。不分千位。
export function formatMinorUnits(amount: number, decimals: number): string {
  if (!Number.isSafeInteger(amount) || amount < 0) {
    throw new RangeError(`amount must be a non-negative whole number of minor units, got ${String(amount)}`);
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > MAX_CURRENCY_DECIMALS) {
    throw new RangeError(`decimals must be a whole number from 0 to ${String(MAX_CURRENCY_DECIMALS)}, got ${String(decimals)}`);
  }
  if (decimals === 0) {
    return String(amount);
  }
  const digits = String(amount).padStart(decimals + 1, "0");
  return `${digits.slice(0, -decimals)}.${digits.slice(-decimals)}`;
}
