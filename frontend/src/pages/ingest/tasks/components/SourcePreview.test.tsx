import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as dpApi from '@/services/data-platform';

// 服务契约 mock:与 tasks/index.test.tsx / access/index.test.tsx 同模式
vi.mock('@/services/data-platform', () => ({
  previewIngestSource: vi.fn(),
}));

import { SourcePreview } from './SourcePreview';

const { previewIngestSource } = dpApi;

describe('SourcePreview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('整表模式渲染样本并默认全选列,取消勾选按预览顺序回传', async () => {
    vi.mocked(previewIngestSource).mockResolvedValue({
      data: {
        columns: [
          { name: 'id', type: 'integer' },
          { name: 'name', type: 'text' },
        ],
        rows: [{ id: 1, name: 'a' }],
        truncated: false,
        sampledFrom: 'public.users',
      },
      success: true,
    });
    const onColumnsChange = vi.fn();
    render(
      <SourcePreview
        datasourceId="ds1"
        extract={{ mode: 'table', tables: ['public.users'] }}
        mode="table"
        onColumnsChange={onColumnsChange}
      />,
    );
    // 列头文本出现 → 表格已渲染(antd Table 在 scroll.x 下会渲染隐藏的 measure 表,
    // 头文本因此重复,故用 getAllByText)
    await waitFor(() =>
      expect(screen.getAllByText('name').length).toBeGreaterThan(0),
    );
    // 默认全选 → 回传 ['id','name'](预览列顺序)
    expect(onColumnsChange).toHaveBeenLastCalledWith(['id', 'name']);
    // 取消 name 勾选 → 仅剩 id(measure 表也镜像了 checkbox,取第一个即可)
    fireEvent.click(screen.getAllByLabelText('name')[0]);
    expect(onColumnsChange).toHaveBeenLastCalledWith(['id']);
  });

  it('非 table 模式只读预览(无勾选 UI)', async () => {
    vi.mocked(previewIngestSource).mockResolvedValue({
      data: {
        columns: [{ name: 'count', type: 'integer' }],
        rows: [{ count: 5 }],
        truncated: false,
        sampledFrom: '<sql>',
      },
      success: true,
    });
    render(
      <SourcePreview
        datasourceId="ds1"
        extract={{ mode: 'sql', sql: 'SELECT 5 AS count' }}
        mode="sql"
      />,
    );
    await waitFor(() =>
      expect(screen.getAllByText('count').length).toBeGreaterThan(0),
    );
    // 只读:列头无 checkbox
    expect(screen.queryByLabelText('count')).toBeNull();
  });

  it('预览失败显示错误 Alert 并支持重试', async () => {
    vi.mocked(previewIngestSource)
      .mockRejectedValueOnce({
        data: { message: '连接超时' },
      })
      .mockResolvedValueOnce({
        data: {
          columns: [{ name: 'id', type: 'integer' }],
          rows: [],
          truncated: false,
          sampledFrom: 'public.users',
        },
        success: true,
      });
    render(
      <SourcePreview
        datasourceId="ds1"
        extract={{ mode: 'table', tables: ['public.users'] }}
        mode="table"
      />,
    );
    // 出错 → 显示错误描述 + 重试按钮
    await waitFor(() =>
      expect(screen.getByText('连接超时')).toBeInTheDocument(),
    );
    // antd Button 对 2 字中文标签自动插空("重 试"),用 role 定位避开
    const retryBtn = screen.getByRole('button');
    expect(retryBtn).toBeInTheDocument();
    // 重试 → 重新调用并渲染出列
    fireEvent.click(retryBtn);
    await waitFor(() => expect(previewIngestSource).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getAllByText('id').length).toBeGreaterThan(0),
    );
  });
});
