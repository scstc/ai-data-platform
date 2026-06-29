// 数据任务统一控制台:跨治理+评估类型的任务列表 + 生命周期管控(暂停/继续/停止/重跑/删除)
// + 出入参数据集多版本按文件预览。定位为运维控制台——新建仍回各类型 editor。
// 列表走 /api/v1/data-tasks;暂停/继续/停止走通用 /api/v1/jobs/{id}/pause|resume|stop。
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useSearchParams } from '@umijs/max';
import { Button, Drawer, message, Popconfirm, Progress, Tag } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { JobDetail } from '@/components';
import {
  batchDeleteJobs,
  dataTaskStats,
  deleteJob,
  getJob,
  listDataTasks,
  pauseJob,
  rerunAugmentJob,
  rerunDistillationJob,
  rerunJob,
  rerunMakeJob,
  resumeJob,
  stopJob,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { jobVersionColumns, renderState } from '@/utils/jobState';
import Dashboard, { type DataTaskStatsData } from './Dashboard';

/** 任务类型中文标签(与后端 Job.type 对齐)。 */
const TYPE_LABEL: Record<string, string> = {
  clean: '数据清洗',
  distillation: '数据蒸馏',
  synthesis: '数据合成',
  augmentation: '数据增强',
  quality: '质量评估',
  review: '内容安全',
};

const TYPE_VALUE_ENUM: Record<string, { text: string }> = Object.fromEntries(
  Object.entries(TYPE_LABEL).map(([k, v]) => [k, { text: v }]),
);

const TYPE_TAG_COLOR: Record<string, string> = {
  clean: 'blue',
  distillation: 'geekblue',
  synthesis: 'geekblue',
  augmentation: 'geekblue',
  quality: 'purple',
  review: 'magenta',
};

const STATE_VALUE_ENUM: Record<string, { text: string }> = {
  pending: { text: '待运行' },
  running: { text: '运行中' },
  paused: { text: '已暂停' },
  success: { text: '成功' },
  failed: { text: '失败' },
  cancelled: { text: '已取消' },
};

/** 成功任务的「查看报告」跳转——回各类型页(那里有各自的报告/统计视图)。 */
const REPORT_PAGE: Record<string, string> = {
  distillation: '/governance/distillation',
  synthesis: '/governance/make',
  augmentation: '/governance/augment',
  quality: '/assessment/quality',
  review: '/governance/content-safety',
};

/** 支持重跑的类型(quality/review 无重跑端点,不在此列)。 */
const RERUN_SUPPORTED = new Set([
  'clean',
  'distillation',
  'synthesis',
  'augmentation',
]);

async function rerunByType(type: string, id: string) {
  switch (type) {
    case 'distillation':
      return rerunDistillationJob(id);
    case 'synthesis':
      return rerunMakeJob(id);
    case 'augmentation':
      return rerunAugmentJob(id);
    default:
      return rerunJob(id); // process / clean
  }
}

const ERR = 'var(--ant-color-error, #ff4d4f)';

const DataTasks: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [searchParams] = useSearchParams();
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);
  const [polling, setPolling] = useState<number | undefined>(undefined);
  // 页顶概览统计,驱动 Dashboard。随列表 request(含 reload/轮询)一同刷新。
  const [stats, setStats] = useState<DataTaskStatsData>();

  const refreshStats = () => {
    dataTaskStats()
      .then((res) => {
        if (res?.success) setStats(res);
      })
      .catch(() => undefined);
  };

  const openDetail = (job?: DataPlatform.Job) => {
    setCurrentJob(job);
    setDetailOpen(!!job);
  };

  // 兑现血缘深链:?highlight=<jobId> → 拉该任务详情并打开抽屉(可能不在当前页)
  useEffect(() => {
    const hid = searchParams.get('highlight');
    if (!hid) return;
    getJob(hid)
      .then((res) => {
        if (res?.data) openDetail(res.data);
      })
      .catch(() => undefined);
    // 只在首次挂载、按 highlight 开抽屉一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const afterAction = () => {
    actionRef.current?.reload();
    // 抽屉里若正展示该任务,刷新它的最新状态
    if (currentJob) {
      getJob(currentJob.id)
        .then((res) => res?.data && setCurrentJob(res.data))
        .catch(() => undefined);
    }
  };

  const handlePause = async (id: string) => {
    const hide = message.loading('正在暂停…', 0);
    try {
      await pauseJob(id);
      hide();
      message.success('任务已暂停');
      afterAction();
    } catch (e: any) {
      hide();
      message.error(
        e?.info?.errorMessage || e?.data?.message || '暂停失败，请重试',
      );
    }
  };

  const handleResume = async (id: string) => {
    const hide = message.loading('正在继续…', 0);
    try {
      await resumeJob(id);
      hide();
      message.success('已继续（按原配置从头重跑）');
      afterAction();
    } catch (e: any) {
      hide();
      message.error(
        e?.info?.errorMessage || e?.data?.message || '继续失败，请重试',
      );
    }
  };

  const handleStop = async (id: string) => {
    const hide = message.loading('正在停止…', 0);
    try {
      await stopJob(id);
      hide();
      message.success('任务已停止');
      afterAction();
    } catch (e: any) {
      hide();
      message.error(
        e?.info?.errorMessage || e?.data?.message || '停止失败，请重试',
      );
    }
  };

  const handleRerun = async (r: DataPlatform.Job) => {
    const hide = message.loading('正在重新运行…', 0);
    try {
      await rerunByType(r.type, r.id);
      hide();
      message.success('已重新运行，产出新版本');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('重新运行失败，请重试');
    }
  };

  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteJob(id);
      hide();
      message.success('任务已删除');
      if (currentJob?.id === id) openDetail(undefined);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  const handleBatchDelete = async () => {
    const ids = selectedRows.map((r) => r.id);
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteJobs(ids);
      hide();
      message.success(`已删除 ${res?.data?.deleted ?? ids.length} 个任务`);
      setSelectedRows([]);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('批量删除失败，请重试');
    }
  };

  const columns: ProColumns<DataPlatform.Job>[] = [
    {
      title: '任务名',
      dataIndex: 'name',
      width: 240,
      ellipsis: true,
      render: (dom, record) => (
        <a
          title={record.name}
          onClick={(e) => {
            e.preventDefault();
            openDetail(record);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 100,
      valueType: 'select',
      fieldProps: {
        mode: 'multiple',
        allowClear: true,
        maxTagCount: 'responsive',
        placeholder: '全部类型',
      },
      valueEnum: TYPE_VALUE_ENUM,
      render: (_, r) => (
        <Tag color={TYPE_TAG_COLOR[r.type] ?? 'default'}>
          {TYPE_LABEL[r.type] ?? r.type}
        </Tag>
      ),
    },
    ...jobVersionColumns(),
    {
      title: '状态',
      dataIndex: 'state',
      width: 90,
      valueType: 'select',
      fieldProps: { allowClear: true, placeholder: '全部状态' },
      valueEnum: STATE_VALUE_ENUM,
      render: (_, r) => renderState(r.state),
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 140,
      search: false,
      render: (_, r) => {
        if (r.state === 'running' || r.state === 'pending') {
          return (
            <Progress percent={r.progress ?? 0} size="small" status="active" />
          );
        }
        return '-';
      },
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      search: false,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 240,
      search: false,
      render: (_, r) => {
        const actions: React.ReactNode[] = [];
        if (r.canPause) {
          actions.push(
            <Popconfirm
              key="pause"
              title="暂停该任务？"
              description="将终止当前运行；之后点「继续」会按原配置从头重跑（不保留进度）。"
              okText="暂停"
              onConfirm={() => handlePause(r.id)}
            >
              <a>暂停</a>
            </Popconfirm>,
          );
        }
        if (r.canResume) {
          actions.push(
            <Popconfirm
              key="resume"
              title="继续该任务？"
              description="将按原配置从头重跑（dj-process 无断点续跑，已处理数据会重做）。"
              okText="继续"
              onConfirm={() => handleResume(r.id)}
            >
              <a>继续</a>
            </Popconfirm>,
          );
        }
        if (r.canStop) {
          actions.push(
            <Popconfirm
              key="stop"
              title="停止该任务？"
              description="标记为已取消；要重来用「重新运行」。"
              okText="停止"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleStop(r.id)}
            >
              <a style={{ color: ERR }}>停止</a>
            </Popconfirm>,
          );
        }
        // 终态:查看报告(回类型页) / 重新运行 / 删除
        if (r.state === 'success' && REPORT_PAGE[r.type]) {
          actions.push(
            <a key="report" onClick={() => history.push(REPORT_PAGE[r.type])}>
              查看报告
            </a>,
          );
        }
        if (r.canRerun && RERUN_SUPPORTED.has(r.type) && !r.canStop) {
          actions.push(
            <Popconfirm
              key="rerun"
              title="用原配置重新运行，产出新版本？"
              onConfirm={() => handleRerun(r)}
            >
              <a>重新运行</a>
            </Popconfirm>,
          );
        }
        if (r.state !== 'running') {
          actions.push(
            <Popconfirm
              key="delete"
              title="删除该任务记录？产出的数据集版本会保留。"
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleDelete(r.id)}
            >
              <a style={{ color: ERR }}>删除</a>
            </Popconfirm>,
          );
        }
        return actions;
      },
    },
  ];

  const _inputVer = currentJob?.input;
  const _outputVer = currentJob?.output;

  return (
    <PageContainer
      header={{
        title: '数据任务',
        breadcrumb: {},
      }}
    >
      <Dashboard stats={stats} />

      <ProTable<DataPlatform.Job>
        headerTitle="任务统一管理（治理 + 评估）"
        actionRef={actionRef}
        rowKey="id"
        options={{ reload: true }}
        search={{ labelWidth: 'auto' }}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Job[]),
        }}
        tableAlertOptionRender={() => (
          <Popconfirm
            title={`确认删除选中的 ${selectedRows.length} 个任务？`}
            description="只删任务记录，产出的数据集版本会保留。"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={handleBatchDelete}
          >
            <Button type="link" danger>
              批量删除
            </Button>
          </Popconfirm>
        )}
        polling={polling}
        request={async (params) => {
          const res = await listDataTasks({
            current: params.current,
            pageSize: params.pageSize,
            keyword: params.name,
            types: Array.isArray(params.type)
              ? params.type.join(',')
              : params.type,
            state: params.state,
          });
          const rows = res.data ?? [];
          setPolling(
            rows.some((j) => j.state === 'running' || j.state === 'pending')
              ? 3000
              : undefined,
          );
          // 概览统计与列表同源刷新(首次加载、reload、轮询都会带上)
          refreshStats();
          return { data: rows, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <Drawer
        width={960}
        open={detailOpen}
        title={currentJob?.name ?? '任务详情'}
        onClose={() => openDetail(undefined)}
      >
        {currentJob && <JobDetail job={currentJob} />}
      </Drawer>
    </PageContainer>
  );
};

export default DataTasks;
