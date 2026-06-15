import {
  ACCESS_TYPES,
  acceptOf,
  formatFileSize,
  getExtension,
  isExtAllowed,
} from './constants';

/** 按 key 取类型栏;缺失即抛(测试中失败要响,不静默用非空断言) */
const byKey = (k: string) => {
  const t = ACCESS_TYPES.find((x) => x.key === k);
  if (!t) throw new Error(`未找到类型栏:${k}`);
  return t;
};

describe('access constants', () => {
  it('有 8 个类型栏,key 唯一', () => {
    expect(ACCESS_TYPES).toHaveLength(8);
    const keys = ACCESS_TYPES.map((t) => t.key);
    expect(new Set(keys).size).toBe(8);
    expect(keys).toContain('csv-tsv');
    expect(keys).toContain('sql');
  });

  it('sql 栏无扩展名(纯引导)', () => {
    const sql = byKey('sql');
    expect(sql.extensions).toEqual([]);
  });

  it('acceptOf 拼带点逗号串', () => {
    const csv = byKey('csv-tsv');
    expect(acceptOf(csv)).toBe('.csv,.tsv');
  });

  it('isExtAllowed 大小写不敏感、按栏判定', () => {
    const img = byKey('image');
    expect(isExtAllowed('A.PNG', img)).toBe(true);
    expect(isExtAllowed('a.csv', img)).toBe(false);
  });

  it('getExtension / formatFileSize', () => {
    expect(getExtension('a.b.CSV')).toBe('csv');
    expect(getExtension('noext')).toBe('');
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(1536)).toBe('1.5 KB');
  });
});
