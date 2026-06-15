import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/services/data-platform';

// Mock ProComponents：ProTable 调用一次 request 以模拟数据加载
vi.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: any) => (
    <div data-testid="page-container">{children}</div>
  ),
  ProTable: ({ columns, toolBarRender, request }: any) => {
    request?.({ current: 1, pageSize: 10 });
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

// 表单抽屉是独立单元，这里仅占位，避免 StepsForm 真实渲染干扰
vi.mock('./components/DataSourceFormDrawer', () => ({
  default: ({ open }: any) => (open ? <div data-testid="form-drawer" /> : null),
}));

// 分类管理抽屉是独立组件，这里占位，避免其内部 ProTable/ModalForm 真实渲染干扰
vi.mock('@/components', () => ({
  CategoryManager: ({ open }: any) =>
    open ? <div data-testid="category-manager" /> : null,
}));

// access 门控：测以 admin 视角渲染（写入口对 admin 可见，对 user 隐藏由 access.ts 保证）
vi.mock('@umijs/max', () => ({
  useAccess: () => ({ canAdmin: true }),
  Access: ({ accessible, children }: any) => (accessible ? children : null),
}));

vi.mock('antd', async () => {
  const actual = await vi.importActual<any>('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

vi.mock('@/services/data-platform', () => ({
  listDataSources: vi.fn(),
  deleteDataSource: vi.fn(),
  listCategories: vi.fn(),
}));

import DataSourcesPage from './index';

describe('DataSourcesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDataSources).mockResolvedValue({
      data: [],
      total: 0,
      success: true,
    });
    vi.mocked(api.listCategories).mockResolvedValue({
      data: [],
      success: true,
    });
  });

  it('renders without crashing', () => {
    const { container } = render(<DataSourcesPage />);
    expect(container).toBeTruthy();
    expect(screen.getByTestId('pro-table')).toBeInTheDocument();
  });

  it('renders the four contract columns plus 操作', () => {
    render(<DataSourcesPage />);
    expect(screen.getByText('名称')).toBeInTheDocument();
    expect(screen.getByText('类型')).toBeInTheDocument();
    expect(screen.getByText('状态')).toBeInTheDocument();
    expect(screen.getByText('创建人')).toBeInTheDocument();
    expect(screen.getByText('操作')).toBeInTheDocument();
  });

  it('renders the 新建数据源 toolbar button', () => {
    render(<DataSourcesPage />);
    expect(screen.getByText('新建数据源')).toBeInTheDocument();
  });

  it('renders the 分类 column and 分类管理 toolbar entry (#15)', () => {
    render(<DataSourcesPage />);
    expect(screen.getByText('分类')).toBeInTheDocument();
    expect(screen.getByText('分类管理')).toBeInTheDocument();
  });

  it('loads categories on mount for the 分类 filter (#15)', async () => {
    render(<DataSourcesPage />);
    await waitFor(() => {
      expect(api.listCategories).toHaveBeenCalled();
    });
  });

  it('calls listDataSources on mount via ProTable request', async () => {
    render(<DataSourcesPage />);
    await waitFor(() => {
      expect(api.listDataSources).toHaveBeenCalledWith(
        expect.objectContaining({ current: 1, pageSize: 10 }),
      );
    });
  });
});
