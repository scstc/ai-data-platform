import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/services/data-platform';

// Mock ProComponents:ProTable 按 params 调一次 request 以模拟数据加载
// (params 变化即重拉,贴合真实 ProTable 行为,用于断言类型栏切换驱动 dataType)
vi.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: any) => (
    <div data-testid="page-container">{children}</div>
  ),
  ProTable: ({ columns, toolBarRender, request, params }: any) => {
    request?.({ current: 1, pageSize: 10, ...params });
    return (
      <div data-testid="pro-table">
        <div data-testid="table-columns">
          {columns?.map((col: any) => (
            <div key={col.dataIndex ?? col.title} data-testid="column">
              {col.title}
            </div>
          ))}
        </div>
        {toolBarRender && <div data-testid="toolbar">{toolBarRender()}</div>}
      </div>
    );
  },
}));

// 上传弹框是独立单元，占位避免其内部组件真实渲染
vi.mock('./UploadModal', () => ({
  default: ({ open }: any) =>
    open ? <div data-testid="upload-modal" /> : null,
}));

// 分类管理抽屉占位
vi.mock('@/components/CategoryManager', () => ({
  default: ({ open }: any) =>
    open ? <div data-testid="category-manager" /> : null,
}));

vi.mock('@umijs/max', () => ({
  useAccess: () => ({ canAdmin: true }),
  Access: ({ children }: any) => children,
  history: { push: vi.fn() },
}));

vi.mock('@/services/data-platform', () => ({
  listDatasets: vi.fn(),
  listCategories: vi.fn(),
  deleteDataset: vi.fn(),
  getDataset: vi.fn(),
  previewDatasetVersion: vi.fn(),
}));

import AccessPage from './index';

describe('数据接入页', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDatasets).mockResolvedValue({
      data: [],
      total: 0,
      success: true,
    });
    vi.mocked(api.listCategories).mockResolvedValue({
      data: [],
      success: true,
    });
  });

  it('渲染 8 个类型栏', async () => {
    render(<AccessPage />);
    expect(await screen.findByText('CSV/TSV接入')).toBeInTheDocument();
    expect(screen.getByText('SQL接入')).toBeInTheDocument();
    expect(screen.getByText('图像接入')).toBeInTheDocument();
    expect(screen.getByText('日志接入')).toBeInTheDocument();
  });

  it('默认栏(CSV/TSV)以 dataType=csv-tsv 拉列表', async () => {
    render(<AccessPage />);
    await waitFor(() =>
      expect(api.listDatasets).toHaveBeenCalledWith(
        expect.objectContaining({ dataType: 'csv-tsv' }),
      ),
    );
  });

  it('切到「图像接入」栏后以 dataType=image 拉列表', async () => {
    render(<AccessPage />);
    fireEvent.click(screen.getByText('图像接入'));
    await waitFor(() =>
      expect(api.listDatasets).toHaveBeenCalledWith(
        expect.objectContaining({ dataType: 'image' }),
      ),
    );
  });

  it('切到「SQL接入」栏显示引导页且不渲染表格', async () => {
    render(<AccessPage />);
    fireEvent.click(screen.getByText('SQL接入'));
    expect(await screen.findByText('去数据源管理')).toBeInTheDocument();
    expect(screen.queryByTestId('pro-table')).not.toBeInTheDocument();
  });
});
