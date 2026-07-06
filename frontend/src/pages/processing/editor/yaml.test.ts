import { describe, expect, it } from 'vitest';
import { stepsToYaml } from './yaml';

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
