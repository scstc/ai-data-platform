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
});
