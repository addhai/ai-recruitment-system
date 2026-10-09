/**
 * 展示层格式化。抽成独立模块是为了可测：
 * 币种符号这类映射错一次就是"谎报金额"，但埋在组件里只能靠肉眼看界面发现。
 */

/** 币种符号：未知币种直接显示代码，不要猜成 $ —— 猜错等于谎报金额 */
export const currencySymbol = (cur: string | null | undefined): string => {
  const c = (cur || '').toUpperCase();
  if (c === 'CNY' || c === 'RMB') return '¥';
  if (c === 'USD') return '$';
  if (c === 'EUR') return '€';
  return c ? `${c} ` : '';
};

/** 金额。币种未知时只给数字，不臆造单位 */
export const fmtCost = (n: number | null | undefined, cur?: string | null): string =>
  n === null || n === undefined
    ? '-'
    : `${currencySymbol(cur)}${Number(n).toFixed(4)}`;

export const fmtNum = (n: number | null | undefined): string =>
  n === null || n === undefined ? '-' : n.toLocaleString('zh-CN');
