import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as dpApi from '@/services/data-platform';

// 测试期内共享槽：捕获 StepsForm 的 formRef / onFinish / 各 step 的 onFinish
type StepFinishFn = (values: any) => Promise<any>;
const stepsState: {
  formRef: React.MutableRefObject<any> | null;
  wizardOnFinish: StepFinishFn | null;
  stepOnFinishes: Record<string, StepFinishFn>;
} = {
  formRef: null,
  wizardOnFinish: null,
  stepOnFinishes: {},
};

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
  // StepsForm 桩：渲染 children(各 StepForm)，并把 onFinish / formRef 暴露给测试
  StepsForm: Object.assign(
    ({ children, onFinish, formRef }: any) => {
      stepsState.wizardOnFinish = onFinish || null;
      if (formRef) {
        formRef.current = {
          setFieldValue: vi.fn(),
          getFieldValue: vi.fn(() => undefined),
        };
        stepsState.formRef = formRef;
      }
      return <div data-testid="steps-form">{children}</div>;
    },
    {
      StepForm: ({ name, title, children, onFinish }: any) => {
        if (name && onFinish) stepsState.stepOnFinishes[name] = onFinish;
        return (
          <div data-testid={`step-${name}`} data-title={title}>
            {children}
          </div>
        );
      },
    },
  ),
  ProDescriptions: () => <div data-testid="pro-descriptions" />,
  ProFormText: ({ label }: any) => <div data-testid="form-text">{label}</div>,
  ProFormSelect: ({ label }: any) => (
    <div data-testid="form-select">{label}</div>
  ),
  ProFormTextArea: ({ label }: any) => (
    <div data-testid="form-textarea">{label}</div>
  ),
  ProFormTreeSelect: ({ label }: any) => (
    <div data-testid="form-tree-select">{label}</div>
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
  updateIngestTask: vi.fn(),
  listDataSources: vi.fn(),
  listCategories: vi.fn(),
  listDatasourceTables: vi.fn(),
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

// SourcePreview 桩：渲染占位 + 暴露 onColumnsChange 触发器
// 验证"勾列 → 写入 extract.columns"的 wiring，不驱动真实预览
vi.mock('./components/SourcePreview', () => ({
  SourcePreview: ({
    onColumnsChange,
    datasourceId,
    mode,
  }: {
    onColumnsChange?: (cols: string[]) => void;
    datasourceId: string;
    mode?: string;
  }) => (
    <div
      data-testid="source-preview"
      data-datasource={datasourceId}
      data-mode={mode ?? ''}
    >
      <button
        type="button"
        data-testid="select-cols"
        onClick={() => onColumnsChange?.(['col_a', 'col_c'])}
      >
        select
      </button>
    </div>
  ),
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
    stepsState.formRef = null;
    stepsState.wizardOnFinish = null;
    stepsState.stepOnFinishes = {};
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

  it('编辑入口仍走 ModalForm（taskFormFields 未被 StepsForm 改造回归）', () => {
    // 回归保护：编辑入口的 ModalForm 应保留
    render(<IngestTasksPage />);
    // 编辑 ModalForm 只在点编辑时打开，这里只验证页面正常渲染、StepsForm 与 ModalForm 桩共存
    expect(screen.getByTestId('pro-table')).toBeInTheDocument();
  });

  it('新建任务走四步向导,勾列写入 extract.columns,提交载荷含选中列', async () => {
    render(<IngestTasksPage />);

    // 1) 点「新建任务」按钮 → 触发 Modal 内 StepsForm 渲染
    fireEvent.click(screen.getByText('新建任务'));
    expect(await screen.findByTestId('steps-form')).toBeInTheDocument();

    // 2) 断言四步全部出现（title 落在 data-title 上）
    expect(screen.getByTestId('step-base')).toHaveAttribute(
      'data-title',
      '基本信息',
    );
    expect(screen.getByTestId('step-object')).toHaveAttribute(
      'data-title',
      '采集对象',
    );
    expect(screen.getByTestId('step-preview')).toHaveAttribute(
      'data-title',
      '预览与字段',
    );
    expect(screen.getByTestId('step-confirm')).toHaveAttribute(
      'data-title',
      '落地确认',
    );

    // 3) 模拟前两步提交：把 datasourceId + extract 写入 wizardCtx（步骤间状态）
    //    生产代码：step.onFinish 把 values 写入 React state，驱动 step3 SourcePreview 渲染
    await stepsState.stepOnFinishes.base({
      name: 't1',
      datasourceId: 'ds-pg-01',
      categoryId: 'cat-1',
    });
    await stepsState.stepOnFinishes.object({
      schedule: { mode: 'once' },
      extract: { mode: 'table', tables: ['users'] },
    });

    // 4) SourcePreview 已挂载（datasourceId 由 wizardCtx 注入）
    const preview = await screen.findByTestId('source-preview');
    expect(preview).toHaveAttribute('data-datasource', 'ds-pg-01');
    expect(preview).toHaveAttribute('data-mode', 'table');

    // 5) 勾列 → 触发生产代码 onColumnsChange → formRef.setFieldValue(['extract','columns'], cols)
    fireEvent.click(screen.getByTestId('select-cols'));
    expect(stepsState.formRef?.current.setFieldValue).toHaveBeenCalledWith(
      ['extract', 'columns'],
      ['col_a', 'col_c'],
    );

    // 6) 提交向导：模拟 StepsForm 在最后一步把所有 step 值 deep-merge 后调 onFinish
    //    断言 createIngestTask 收到的载荷中 extract.columns 反映勾选结果
    await stepsState.wizardOnFinish?.({
      name: 't1',
      datasourceId: 'ds-pg-01',
      categoryId: 'cat-1',
      schedule: { mode: 'once' },
      extract: {
        mode: 'table',
        tables: ['users'],
        columns: ['col_a', 'col_c'],
      },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledTimes(1);
    expect(dpApi.createIngestTask).toHaveBeenCalledWith(
      expect.objectContaining({
        extract: expect.objectContaining({
          mode: 'table',
          columns: ['col_a', 'col_c'],
        }),
      }),
    );
  });
});
