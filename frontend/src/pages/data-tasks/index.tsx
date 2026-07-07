// 数据任务统一控制台:跨治理+评估类型的任务列表 + 生命周期管控(暂停/继续/停止/重跑/删除)
// + 出入参数据集多版本按文件预览。定位为运维控制台——新建仍回各类型 editor。
// 列表走 /api/v1/data-tasks;暂停/继续/停止走通用 /api/v1/jobs/{id}/pause|resume|stop。
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess, useSearchParams } from '@umijs/max';
import {
  Button,
  Drawer,
  message,
  Popconfirm,
  Progress,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { JobDetail } from '@/components';
import DistillationReportModal from '@/pages/distillation/ReportModal';
import {
  batchDeleteJobs,
  dataTaskStats,
  deleteJob,
  getDistillationReport,
  getJob,
  listDatasets,
  listDataTasks,
  listPipelines,
  pauseJob,
  rerunAugmentJob,
  rerunDistillationJob,
  rerunJob,
  rerunMakeJob,
  rerunTrainsetJob,
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
  trainset: '训练集生成',
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
  trainset: 'geekblue',
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

/** 成功任务的「查看报告」跳转——回各类型页(那里有各自的报告/统计视图)。
 *  quality/review 有独立报告页,点击处带 ?jobId= 直达该任务报告。
 *  distillation 不走跳转:/governance/distillation 是治理工场薄入口(渲染
 *  Workbench,无报告),报告改为本页内弹 Modal(getDistillationReport)。
 *  augmentation 无此入口:/governance/augment 同为工场薄入口,
 *  报告实际在 /governance/augment/jobs 里按行展开,此处不接;
 *  故不在此表中,「查看报告」按钮相应不出现。 */
const REPORT_PAGE: Record<string, string> = {
  synthesis: '/governance/make',
  quality: '/assessment/quality/report',
  review: '/governance/content-safety/report',
};

/** 报告页支持 ?jobId= 定位的任务类型。 */
const REPORT_WITH_JOB_ID = new Set(['quality', 'review']);

/** 支持重跑的类型(quality/review 无重跑端点,不在此列)。 */
const RERUN_SUPPORTED = new Set([
  'clean',
  'distillation',
  'synthesis',
  'augmentation',
  'trainset',
]);

/** 「来源流水线」列点击跳转——回各场景编辑器并带上 pipelineId(quality/review 无编辑器,不在此列)。 */
const PIPELINE_EDITOR_PAGE: Record<string, string> = {
  clean: '/governance/cleaning/editor',
  distillation: '/governance/distillation/editor',
  synthesis: '/governance/make/editor',
  augmentation: '/governance/augment/editor',
  trainset: '/governance/trainset/editor',
};

async function rerunByType(type: string, id: string) {
  switch (type) {
    case 'distillation':
      return rerunDistillationJob(id);
    case 'synthesis':
      return rerunMakeJob(id);
    case 'augmentation':
      return rerunAugmentJob(id);
    case 'trainset':
      return rerunTrainsetJob(id);
    default:
      return rerunJob(id); // process / clean
  }
}

const ERR = 'var(--ant-color-error, #ff4d4f)';

const DataTasks: React.FC = () => {
  const access = useAccess();
  const canPause = access.hasPerm('ops:datatask:pause');
  const canResume = access.hasPerm('ops:datatask:resume');
  const canStop = access.hasPerm('ops:datatask:stop');
  const canRerun = access.hasPerm('ops:datatask:rerun');
  const canRemove = access.hasPerm('ops:datatask:remove');
  const canBatchRemove = access.hasPerm('ops:datatask:batch-remove');
  const actionRef = useRef<ActionType | null>(null);
  const [searchParams] = useSearchParams();
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);
  const [polling, setPolling] = useState<number | undefined>(undefined);
  // 页顶概览统计,驱动 Dashboard。随列表 request(含 reload/轮询)一同刷新。
  const [stats, setStats] = useState<DataTaskStatsData>();
  // 数据集列表:供「数据集」搜索项下拉选项
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  // 流水线 id → {name, scenario} 映射:供「来源流水线」列展示名称与拼编辑器跳转链接
  const [pipelineMap, setPipelineMap] = useState<
    Record<string, { name: string; scenario: string }>
  >({});
  // 蒸馏报告 Modal:本页内直接展示(蒸馏无独立报告页可跳)
  const [reportJobId, setReportJobId] = useState<string>();
  const [report, setReport] = useState<DataPlatform.DistillationReport>();

  const openDistillReport = async (jobId: string) => {
    try {
      const res = await getDistillationReport(jobId);
      if (res.data) {
        setReport(res.data);
        setReportJobId(jobId);
      }
    } catch (e: any) {
      message.error(
        e?.info?.errorMessage || e?.data?.message || '读取报告失败',
      );
    }
  };

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 })
      .then((r) => setDatasets(r.data ?? []))
      .catch(() => setDatasets([]));
    listPipelines({ pageSize: 100 })
      .then((r) => {
        const map: Record<string, { name: string; scenario: string }> = {};
        (r.data ?? []).forEach((p) => {
          map[p.id] = { name: p.name, scenario: p.scenario };
        });
        setPipelineMap(map);
      })
      .catch(() => setPipelineMap({}));
  }, []);

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
      // 直显后端数据库主键(形如 job-xxxxxx);搜索项走后端 jobId 模糊匹配,
      // 可只输 "job-" 后的 hex 片段。
      title: '任务ID',
      dataIndex: 'id',
      width: 120,
      copyable: true,
      fieldProps: { placeholder: '按任务ID', allowClear: true },
    },
    {
      title: '任务名',
      dataIndex: 'name',
      width: 240,
      // Tailwind preflight 把裸 <a> 的颜色重置为 inherit,须用 Typography.Link
      // 才有链接蓝色与 hover 反馈;省略也交给它做——列级 ellipsis 会把内容包进
      // Typography.Text,其自带文字色会盖掉链接蓝
      render: (_, record) => (
        <Typography.Link
          ellipsis
          title={record.name}
          style={{ width: '100%' }}
          onClick={(e) => {
            e.preventDefault();
            openDetail(record);
          }}
        >
          {record.name}
        </Typography.Link>
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
    {
      title: '来源流水线',
      dataIndex: 'pipelineId',
      width: 160,
      ellipsis: true,
      search: false,
      render: (_, r) => {
        const pipeline = r.pipelineId ? pipelineMap[r.pipelineId] : undefined;
        if (!pipeline) return '-';
        const editorPath = PIPELINE_EDITOR_PAGE[r.type];
        if (!editorPath) return pipeline.name;
        return (
          <a
            title={pipeline.name}
            onClick={() =>
              history.push(`${editorPath}?pipelineId=${r.pipelineId}`)
            }
          >
            {pipeline.name}
          </a>
        );
      },
    },
    {
      // 仅作搜索项(数据集筛选);列表展示由 jobVersionColumns 的「数据集」列负责
      title: '数据集',
      dataIndex: 'datasetId',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        showSearch: true,
        allowClear: true,
        optionFilterProp: 'label',
        placeholder: '全部数据集',
        options: datasets.map((d) => ({ label: d.name, value: d.id })),
      },
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
        if (r.canPause && canPause) {
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
        if (r.canResume && canResume) {
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
        if (r.canStop && canStop) {
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
        // 终态:查看报告(蒸馏本页弹 Modal;quality/review 直达该任务的报告页,
        // 其余回类型页) / 重新运行 / 删除
        if (r.state === 'success' && r.type === 'distillation') {
          actions.push(
            <a key="report" onClick={() => openDistillReport(r.id)}>
              查看报告
            </a>,
          );
        } else if (r.state === 'success' && REPORT_PAGE[r.type]) {
          actions.push(
            <a
              key="report"
              onClick={() =>
                history.push(
                  REPORT_WITH_JOB_ID.has(r.type)
                    ? `${REPORT_PAGE[r.type]}?jobId=${r.id}`
                    : REPORT_PAGE[r.type],
                )
              }
            >
              查看报告
            </a>,
          );
        }
        if (
          r.canRerun &&
          RERUN_SUPPORTED.has(r.type) &&
          !r.canStop &&
          canRerun
        ) {
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
        if (r.state !== 'running' && canRemove) {
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
        headerTitle="数据任务管理"
        actionRef={actionRef}
        rowKey="id"
        options={{ reload: true }}
        search={{ labelWidth: 'auto' }}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Job[]),
        }}
        tableAlertOptionRender={() =>
          canBatchRemove && (
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
          )
        }
        polling={polling}
        request={async (params) => {
          const current = params.current ?? 1;
          const pageSize = params.pageSize ?? 10;
          const res = await listDataTasks({
            current,
            pageSize,
            keyword: params.name,
            types: Array.isArray(params.type)
              ? params.type.join(',')
              : params.type,
            state: params.state,
            datasetId: params.datasetId,
            jobId: params.id,
          });
          const data = res.data ?? [];

          setPolling(
            data.some((j) => j.state === 'running' || j.state === 'pending')
              ? 3000
              : undefined,
          );
          // 概览统计与列表同源刷新(首次加载、reload、轮询都会带上)
          refreshStats();
          return {
            data,
            total: res.total ?? 0,
            success: res.success,
          };
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

      <DistillationReportModal
        open={!!reportJobId}
        jobId={reportJobId}
        report={report}
        onClose={() => {
          setReportJobId(undefined);
          setReport(undefined);
        }}
      />
    </PageContainer>
  );
};

export default DataTasks;
