import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as dpApi from '@/services/data-platform';

// 服务契约 mock:与 tasks/index.test.tsx / SourcePreview.test.tsx 同模式
vi.mock('@/services/data-platform', () => ({
  listOperatorCatalog: vi.fn(),
}));

import FilterOperatorPicker from './FilterOperatorPicker';

const { listOperatorCatalog } = dpApi;

describe('FilterOperatorPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listOperatorCatalog).mockResolvedValue({
      data: [],
      total: 0,
      success: true,
    });
  });

  // 回归:后端把未配置算子的 extract.operators 序列化为 null(非 undefined),
  // 编辑 DB 采集任务回填时 Form.Item 会把 null 灌进 value。
  // 旧实现用默认参数 value=[] 对 null 不生效,导致 value.map 抛错使整页 ErrorBoundary 崩溃。
  // 此测试钉住「value=null 必须当空列表渲染、不抛错」这一意图。
  it('value 为 null 时按空列表渲染,不抛错(编辑回填 null operators 回归)', async () => {
    expect(() =>
      render(<FilterOperatorPicker value={null as never} />),
    ).not.toThrow();
    // 空态文案出现 = 走了 steps.length===0 分支,未触碰 null.map
    expect(
      screen.getByText('未选择算子(采集到的数据将原样落地)'),
    ).toBeInTheDocument();
    await waitFor(() => expect(listOperatorCatalog).toHaveBeenCalled());
  });

  it('value 为 undefined 时同样按空列表渲染', () => {
    expect(() => render(<FilterOperatorPicker />)).not.toThrow();
    expect(
      screen.getByText('未选择算子(采集到的数据将原样落地)'),
    ).toBeInTheDocument();
  });
});
