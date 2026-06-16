import { describe, expect, it } from 'vitest';
import { formatDateTime } from './format';

describe('formatDateTime', () => {
  // 为什么:后端返回带 Z 的 UTC 时间戳,必须换算成北京时间(+8)展示。
  // 若退回成「按浏览器本地原样显示」,这些断言会失败(数值会差 8 小时)。
  it('把后端 UTC(带 Z)按北京时间(+8)展示', () => {
    expect(formatDateTime('2026-06-16T03:00:00Z')).toBe('2026-06-16 11:00:00');
  });

  it('跨日:UTC 20:00 → 次日北京 04:00', () => {
    expect(formatDateTime('2026-06-15T20:00:00Z')).toBe('2026-06-16 04:00:00');
  });

  it('空值返回占位符 -', () => {
    expect(formatDateTime(undefined)).toBe('-');
    expect(formatDateTime(null)).toBe('-');
    expect(formatDateTime('')).toBe('-');
  });
});
