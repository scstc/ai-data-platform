import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as dpApi from '@/services/data-platform';

// Mock ProComponents：抽出 columns / toolbar / 调用 request 模拟数据加载
vi.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: any) => (
    <div data-testid="page-container">{children}</div>
  ),
  ProTable: ({ columns, toolBarRender, request }: any) => {
    request?.({ current: 1, pageSize: 20 });
    return (
      <div data-testid="pro-table">
        <div data-testid="table-columns">
          {columns?.map((col: any) => (
            <div
              key={col.dataIndex ?? col.key}
              data-testid={`column-${col.dataIndex ?? col.key}`}
            >
              {typeof col.title === 'string' ? col.title : col.dataIndex}
            </div>
          ))}
        </div>
        {toolBarRender && <div data-testid="toolbar">{toolBarRender()}</div>}
      </div>
    );
  },
  ModalForm: ({ trigger, children }: any) => (
    <div data-testid="modal-form">
      {trigger}
      {children}
    </div>
  ),
  ProDescriptions: () => <div data-testid="pro-descriptions" />,
  ProFormText: ({ label }: any) => <div data-testid="form-text">{label}</div>,
  ProFormSelect: ({ label }: any) => (
    <div data-testid="form-select">{label}</div>
  ),
  ProFormTextArea: ({ label }: any) => (
    <div data-testid="form-textarea">{label}</div>
  ),
  ProFormRadio: { Group: ({ label }: any) => <div>{label}</div> },
  ProFormDependency: ({ children }: any) =>
    children?.({ schedule: { mode: 'cron' } }) ?? null,
}));

vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      loading: vi.fn(() => vi.fn()),
    },
  };
});

// 服务函数全部 mock 掉（契约唯一事实源）
vi.mock('@/services/data-platform', () => ({
  listIngestTasks: vi.fn(),
  getIngestTask: vi.fn(),
  rerunIngestTask: vi.fn(),
  stopIngestTask: vi.fn(),
  deleteIngestTask: vi.fn(),
  createIngestTask: vi.fn(),
  listDataSources: vi.fn(),
  listCategories: vi.fn(),
}));

// 分类管理抽屉占位，避免其内部组件树真实渲染干扰
vi.mock('@/components', () => ({
  CategoryManager: ({ open }: any) =>
    open ? <div data-testid="category-manager" /> : null,
}));

// access 门控：以 admin 视角渲染（分类管理入口对 admin 可见）
vi.mock('@umijs/max', () => ({
  useAccess: () => ({ canAdmin: true }),
  Access: ({ accessible, children }: any) => (accessible ? children : null),
}));

import IngestTasksPage from './index';

const runningTask: DataPlatform.IngestTask = {
  id: 'task-running',
  name: 'HDFS 增量采集',
  datasourceId: 'ds-hdfs-01',
  datasourceName: '离线计算 HDFS',
  schedule: { mode: 'cron', cron: '0 */6 * * *' },
  status: 'running',
  progress: 40,
  createdAt: '2026-06-01 00:00:00',
  lastRunAt: '2026-06-04 01:00:00',
  logs: ['[INFO] 任务启动'],
};

describe('IngestTasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dpApi.listIngestTasks).mockResolvedValue({
      data: [runningTask],
      total: 1,
      success: true,
    });
    vi.mocked(dpApi.getIngestTask).mockResolvedValue({
      data: { ...runningTask, progress: 60 },
      success: true,
    });
    vi.mocked(dpApi.listDataSources).mockResolvedValue({
      data: [],
      total: 0,
      success: true,
    });
    vi.mocked(dpApi.listCategories).mockResolvedValue({
      data: [],
      success: true,
    });
  });

  it('应正常渲染 ProTable', () => {
    render(<IngestTasksPage />);
    expect(screen.getByTestId('pro-table')).toBeInTheDocument();
  });

  it('应包含任务名/状态/操作等核心列', () => {
    render(<IngestTasksPage />);
    const cols = within(screen.getByTestId('table-columns'));
    expect(cols.getByText('任务名')).toBeInTheDocument();
    expect(cols.getByText('状态')).toBeInTheDocument();
    expect(cols.getByText('操作')).toBeInTheDocument();
  });

  it('挂载后应调用 listIngestTasks 加载数据', async () => {
    render(<IngestTasksPage />);
    await waitFor(() => {
      expect(dpApi.listIngestTasks).toHaveBeenCalled();
    });
  });

  it('对运行中的任务应调用 getIngestTask 推进进度', async () => {
    render(<IngestTasksPage />);
    await waitFor(() => {
      expect(dpApi.getIngestTask).toHaveBeenCalledWith('task-running');
    });
  });

  it('工具栏应渲染新建任务按钮', () => {
    render(<IngestTasksPage />);
    expect(screen.getByText('新建任务')).toBeInTheDocument();
  });

  it('应渲染分类列与分类管理入口并在挂载后拉取分类（#15）', async () => {
    render(<IngestTasksPage />);
    expect(screen.getByTestId('column-categoryId')).toBeInTheDocument();
    expect(screen.getByText('分类管理')).toBeInTheDocument();
    await waitFor(() => {
      expect(dpApi.listCategories).toHaveBeenCalled();
    });
  });
});
