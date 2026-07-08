import { describe, expect, it } from 'vitest';
import { stepsToYaml, yamlToSteps } from './yaml';

const OP_MAP: Record<string, DataPlatform.CatalogOperator> = {
  document_deduplicator: { name: 'document_deduplicator' } as any,
  text_length_filter: { name: 'text_length_filter' } as any,
};

describe('stepsToYaml', () => {
  it('无参算子值为 null,有参算子展开为对象', () => {
    const y = stepsToYaml([
      { name: 'document_deduplicator', params: {} },
      { name: 'text_length_filter', params: { min_len: 10 } },
    ]);
    expect(y).toContain('process:');
    expect(y).toContain('document_deduplicator: null');
    expect(y).toContain('text_length_filter:');
    expect(y).toContain('min_len: 10');
  });

  it('空流水线给出占位 process: []', () => {
    expect(stepsToYaml([])).toContain('process: []');
  });

  it('与后端 build_config 同构:staging 相对路径,数据集上下文以注释呈现', () => {
    const y = stepsToYaml([{ name: 'clean_html_mapper', params: {} }], {
      datasetName: '测试6666',
      versionLabel: 'v2026.7.2 (#1)',
      fileName: '安全审核_银行业务QA_2500条',
    });
    expect(y).toContain('# dataset: 测试6666 / v2026.7.2 (#1)');
    expect(y).toContain(
      'dataset_path: inputs/安全审核_银行业务QA_2500条.jsonl',
    );
    expect(y).toContain(
      'export_path: outputs/安全审核_银行业务QA_2500条/安全审核_银行业务QA_2500条.jsonl',
    );
  });

  it('parquet 成员按 parquet 渲染路径,其余格式规范化为 jsonl', () => {
    const pq = stepsToYaml([{ name: 'clean_html_mapper', params: {} }], {
      fileName: 'users',
      fileFormat: 'parquet',
    });
    expect(pq).toContain('dataset_path: inputs/users.parquet');
    const csv = stepsToYaml([{ name: 'clean_html_mapper', params: {} }], {
      fileName: 'users',
      fileFormat: 'csv',
    });
    expect(csv).toContain('dataset_path: inputs/users.jsonl');
  });
});

describe('yamlToSteps', () => {
  it('与 stepsToYaml 互为逆运算(无参 null / 有参对象)', () => {
    const steps: DataPlatform.PipelineStep[] = [
      { name: 'document_deduplicator', params: {} },
      { name: 'text_length_filter', params: { min_len: 10 } },
    ];
    const y = stepsToYaml(steps);
    expect(yamlToSteps(y, OP_MAP).steps).toEqual(steps);
  });

  it('忽略 dataset_path/export_path 等展示字段与头部注释', () => {
    const y = stepsToYaml([{ name: 'text_length_filter', params: {} }], {
      datasetName: '测试',
      versionLabel: 'v1',
      fileName: 'a',
    });
    expect(yamlToSteps(y, OP_MAP).steps).toEqual([
      { name: 'text_length_filter', params: {} },
    ]);
  });

  it('顶层带 text_keys 时一并解析返回', () => {
    const y =
      'process:\n  - text_length_filter: null\ntext_keys:\n  - output\n';
    const parsed = yamlToSteps(y, OP_MAP);
    expect(parsed.textKeys).toEqual(['output']);
  });

  it('未出现 text_keys 时不返回该字段(区别于显式空数组)', () => {
    const y = 'process:\n  - text_length_filter: null\n';
    expect(yamlToSteps(y, OP_MAP).textKeys).toBeUndefined();
  });

  it('空文档给出空算子链', () => {
    expect(yamlToSteps('', OP_MAP)).toEqual({ steps: [] });
  });

  it('YAML 语法错误报可读的错误信息', () => {
    expect(() => yamlToSteps('process: [\n', OP_MAP)).toThrow(/YAML 语法错误/);
  });

  it('缺少 process 字段报错', () => {
    expect(() => yamlToSteps('foo: bar\n', OP_MAP)).toThrow(/缺少 process/);
  });

  it('process 不是数组报错', () => {
    expect(() => yamlToSteps('process: 1\n', OP_MAP)).toThrow(
      /process 必须是数组/,
    );
  });

  it('未知算子名报错,提示具体名字', () => {
    expect(() =>
      yamlToSteps('process:\n  - not_a_real_op: null\n', OP_MAP),
    ).toThrow(/未知算子「not_a_real_op」/);
  });

  it('单个算子条目含多个键报错', () => {
    const bad =
      'process:\n  - text_length_filter: null\n    document_deduplicator: null\n';
    expect(() => yamlToSteps(bad, OP_MAP)).toThrow(/必须只含一个算子名/);
  });

  it('算子参数不是对象或 null 时报错', () => {
    expect(() =>
      yamlToSteps('process:\n  - text_length_filter: 5\n', OP_MAP),
    ).toThrow(/参数必须是对象或 null/);
  });

  it('text_keys 不是字符串数组时报错', () => {
    expect(() =>
      yamlToSteps(
        'process:\n  - text_length_filter: null\ntext_keys: 5\n',
        OP_MAP,
      ),
    ).toThrow(/text_keys 必须是字符串数组/);
  });
});
