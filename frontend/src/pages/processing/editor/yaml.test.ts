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

  it('编排粒度=文件:dataset 渲染数据集本名,file 单独一行,避免语义混淆', () => {
    const y = stepsToYaml([{ name: 'clean_html_mapper', params: {} }], {
      datasetName: '测试6666',
      versionLabel: 'v2026.7.2 (#1)',
      fileName: '安全审核_银行业务QA_2500条',
    });
    expect(y).toContain('dataset: 测试6666 / v2026.7.2 (#1)');
    expect(y).toContain('file: 安全审核_银行业务QA_2500条');
  });
});
