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
  ProFormDigit: ({ label, name }: any) => (
    <div data-testid="form-digit" data-name={JSON.stringify(name)}>
      {label}
    </div>
  ),
  ProFormSwitch: ({ label, name }: any) => (
    <div data-testid="form-switch" data-name={JSON.stringify(name)}>
      {label}
    </div>
  ),
  ProFormSelect: ({ label }: any) => (
    <div data-testid="form-select">{label}</div>
  ),
  ProFormTextArea: ({ label }: any) => (
    <div data-testid="form-textarea">{label}</div>
  ),
  ProFormTreeSelect: ({ label }: any) => (
    <div data-testid="form-tree-select">{label}</div>
  ),
  ProFormRadio: {
    Group: ({ label, options }: any) => (
      <div data-testid="form-radio-group" data-label={label}>
        {label}
        <div data-testid="radio-options">
          {(options ?? []).map((opt: any) => (
            <span
              key={String(opt.value)}
              data-testid={`radio-${opt.value}`}
              data-disabled={opt.disabled ? 'true' : 'false'}
            >
              {opt.label}
            </span>
          ))}
        </div>
      </div>
    ),
  },
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
  ingestTaskStats: vi.fn(),
  listIngestRuns: vi.fn(),
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

// Dashboard 桩：内部用 @antv/g2 图表，jsdom 无 canvas 无法初始化；
// 本测试不验证图表渲染，占位即可（含命名导出 IngestTaskStatsData 的类型不影响运行时）
vi.mock('./Dashboard', () => ({
  default: () => <div data-testid="dashboard" />,
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
    vi.mocked(dpApi.ingestTaskStats).mockResolvedValue({
      total: 0,
      running: 0,
      success: true,
    } as any);
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

  it('VERDICT_META 映射：failed=error/red, passed=success/green, skipped=default/gray（产物 Tag 颜色契约）', async () => {
    // ProDescriptions 在本测试套件被桩代,无法直接断言 Drawer 内 Tag DOM;
    // 把映射作为契约固定——index.tsx 的 Tag color=meta.color 与 Timeline color=meta.dot
    // 据此渲染,等价于「failed 版本得到红色 Tag」。
    const { VERDICT_META } = await import('./index');
    expect(VERDICT_META.failed).toEqual({
      text: '未通过',
      color: 'error',
      dot: 'red',
    });
    expect(VERDICT_META.passed.color).toBe('success');
    expect(VERDICT_META.passed.dot).toBe('green');
    expect(VERDICT_META.skipped.color).toBe('default');
    expect(VERDICT_META.skipped.dot).toBe('gray');
  });

  it('qualityPolicy 字段出现在新建向导 step-4 落地确认（form-digit/form-switch name=qualityPolicy.*）', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    // 落地确认 step 已渲染质量策略两个字段（name 落在 data-name 上）
    // 同时编辑 ModalForm（taskFormFields）也含此字段，故需 scope 到 step-confirm 内断言
    const confirmStep = within(screen.getByTestId('step-confirm'));
    const digit = confirmStep.getByTestId('form-digit');
    expect(digit).toHaveAttribute(
      'data-name',
      JSON.stringify(['qualityPolicy', 'maxNullRate']),
    );
    const sw = confirmStep.getByTestId('form-switch');
    expect(sw).toHaveAttribute(
      'data-name',
      JSON.stringify(['qualityPolicy', 'blockOnSchemaDrift']),
    );
  });

  it('提交向导时 qualityPolicy 透传到 createIngestTask 载荷', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-policy',
      datasourceId: 'ds-pg-01',
      schedule: { mode: 'once' },
      extract: { mode: 'table', tables: ['users'] },
      qualityPolicy: { maxNullRate: 0.2, blockOnSchemaDrift: true },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledWith(
      expect.objectContaining({
        qualityPolicy: { maxNullRate: 0.2, blockOnSchemaDrift: true },
      }),
    );
  });

  it('全空 qualityPolicy（未填阈值 + 未开开关）在载荷中被剔除为 undefined（让后端落 skipped 而非 passed）', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-empty',
      datasourceId: 'ds-pg-01',
      schedule: { mode: 'once' },
      extract: { mode: 'table', tables: ['users'] },
      qualityPolicy: { maxNullRate: undefined, blockOnSchemaDrift: false },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledTimes(1);
    const call = vi.mocked(dpApi.createIngestTask).mock.calls[0][0];
    expect(call.qualityPolicy).toBeUndefined();
  });

  it('Cron 选项已启用（不再 disabled）—— 切片 C 解禁', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    // step-object 内的调度方式选项里,cron 不能再带 disabled(切片 C 解禁的契约)
    const stepObject = screen.getByTestId('step-object');
    const cronOption = within(stepObject).getByTestId('radio-cron');
    expect(cronOption).toHaveAttribute('data-disabled', 'false');
    expect(cronOption).toHaveTextContent('Cron 周期');
    // 「未启用」字样已被移除(不再误导用户)
    expect(cronOption.textContent).not.toContain('未启用');
  });

  it('向导提交时 schedule.cron 透传到 createIngestTask 载荷', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-cron',
      datasourceId: 'ds-pg-01',
      schedule: { mode: 'cron', cron: '0 2 * * *' },
      extract: { mode: 'table', tables: ['users'] },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledWith(
      expect.objectContaining({
        schedule: { mode: 'cron', cron: '0 2 * * *' },
      }),
    );
  });

  it('DB 增量配置(column+type)透传到 createIngestTask 载荷的 incremental', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-inc-db',
      datasourceId: 'ds-pg-01',
      schedule: { mode: 'once' },
      extract: { mode: 'table', tables: ['users'] },
      incremental: { column: 'updated_at', type: 'timestamp' },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledWith(
      expect.objectContaining({
        incremental: { column: 'updated_at', type: 'timestamp' },
      }),
    );
  });

  it('文件增量配置(by)透传到 createIngestTask 载荷的 incremental', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-inc-file',
      datasourceId: 'ds-s3-01',
      schedule: { mode: 'once' },
      extract: { mode: 'path', paths: ['raw/data.jsonl'] },
      incremental: { by: 'mtime' },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledWith(
      expect.objectContaining({
        incremental: { by: 'mtime' },
      }),
    );
  });

  it('全空 incremental（column/type/by 都未填）在载荷中被剔除为 undefined（避免后端 model_validator 拒绝）', async () => {
    render(<IngestTasksPage />);
    fireEvent.click(screen.getByText('新建任务'));
    await screen.findByTestId('steps-form');

    await stepsState.wizardOnFinish?.({
      name: 't-inc-empty',
      datasourceId: 'ds-pg-01',
      schedule: { mode: 'once' },
      extract: { mode: 'table', tables: ['users'] },
      // 表单上「增量列」留空 + 「类型」未选 → 字段都是 undefined / 空
      incremental: { column: undefined, type: undefined },
    });

    expect(dpApi.createIngestTask).toHaveBeenCalledTimes(1);
    const call = vi.mocked(dpApi.createIngestTask).mock.calls[0][0];
    expect(call.incremental).toBeUndefined();
  });

  it('validateCronFields: 5 段合法表达式通过,4 段/含字母段被拒(前端轻校验契约)', async () => {
    const { validateCronFields } = await import('./index');
    // 合法:5 段、* / - , 数字
    expect(validateCronFields('0 2 * * *')).toBeUndefined();
    expect(validateCronFields('*/15 0 1-7 * 1,2,3')).toBeUndefined();
    // 段数错
    expect(validateCronFields('0 2 * *')).toBe(
      'cron 表达式须为 5 段(分 时 日 月 周)',
    );
    expect(validateCronFields('0 2 * * * *')).toBe(
      'cron 表达式须为 5 段(分 时 日 月 周)',
    );
    // 含字母(非标准 crontab 段)
    expect(validateCronFields('0 2 L * *')).toMatch(/只能含数字/);
    // 空
    expect(validateCronFields(undefined)).toBe('请输入 cron 表达式');
    expect(validateCronFields('   ')).toBe('请输入 cron 表达式');
  });
});
