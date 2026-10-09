import { describe, expect, it } from 'vitest';

import { currencySymbol, fmtCost, fmtNum } from './format';

describe('currencySymbol', () => {
  it('把常见币种映射成符号', () => {
    expect(currencySymbol('CNY')).toBe('¥');
    expect(currencySymbol('RMB')).toBe('¥');
    expect(currencySymbol('USD')).toBe('$');
    expect(currencySymbol('EUR')).toBe('€');
  });

  it('大小写不敏感', () => {
    expect(currencySymbol('cny')).toBe('¥');
    expect(currencySymbol('Usd')).toBe('$');
  });

  it('未知币种显示代码而不是猜成 $', () => {
    // 猜错币种等于谎报金额，比显示代码更糟
    expect(currencySymbol('JPY')).toBe('JPY ');
    expect(currencySymbol('HKD')).toBe('HKD ');
  });

  it('币种缺失时不给符号', () => {
    // 多币种改造前的历史行 currency 为 NULL，此时不该臆造单位
    expect(currencySymbol(null)).toBe('');
    expect(currencySymbol(undefined)).toBe('');
    expect(currencySymbol('')).toBe('');
  });
});

describe('fmtCost', () => {
  it('带币种符号并保留 4 位小数', () => {
    expect(fmtCost(0.000183, 'CNY')).toBe('¥0.0002');
    expect(fmtCost(5, 'CNY')).toBe('¥5.0000');
  });

  it('币种未知时只给数字', () => {
    expect(fmtCost(0, null)).toBe('0.0000');
  });

  it('空值显示为短横线而不是 0', () => {
    // 显示 0 会被误读成"这项没花钱"，而实际是"没有数据"
    expect(fmtCost(null, 'CNY')).toBe('-');
    expect(fmtCost(undefined, 'CNY')).toBe('-');
  });

  it('0 与缺失区分开', () => {
    expect(fmtCost(0, 'CNY')).toBe('¥0.0000');
  });
});

describe('fmtNum', () => {
  it('千分位分隔', () => {
    expect(fmtNum(492950)).toBe('492,950');
  });

  it('空值显示为短横线', () => {
    expect(fmtNum(null)).toBe('-');
    expect(fmtNum(undefined)).toBe('-');
  });

  it('0 正常显示', () => {
    expect(fmtNum(0)).toBe('0');
  });
});
