import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  PageContainer,
  ProDescriptions,
  ProForm,
  ProFormDependency,
  ProFormDigit,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Alert,
  Button,
  Drawer,
  Empty,
  message,
  Popconfirm,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  batchDeleteJobs,
  createJob,
  deleteJob,
  getAnalysisReport,
  getJob,
  getQualityReport,
  getVersionStats,
  listJobs,
  listOperators,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

const STATE_META: Record<
  DataPlatform.Job['state'],
  { text: string; color: string }
> = {
  pending: { text: '待运行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  paused: { text: '已暂停', color: 'gold' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '已取消', color: 'warning' },
};

const renderInput = (i?: DataPlatform.IngestOutput) =>
  i
    ? `${i.datasetName}（${i.datasetId} ${i.versionLabel ?? `v${i.versionNo}`}）`
    : '-';

const fmtNum = (v: number) =>
  Number.isInteger(v) ? String(v) : Number(v.toFixed(4)).toString();

const renderParamField = (
  opName: string,
  opLabel: string,
  p: DataPlatform.OperatorParam,
) => {
  const key = `${opName}.${p.name}`;
  const common = {
    name: ['params', opName, p.name],
    label: `${opLabel} · ${p.label}`,
    initialValue: p.default,
  };
  if (p.type === 'select') {
    return (
      <ProFormSelect
        key={key}
        {...common}
        options={(p.options ?? []).map((v) => ({ label: v, value: v }))}
      />
    );
  }
  if (p.type === 'number') {
    return <ProFormDigit key={key} {...common} />;
  }
  return <ProFormText key={key} {...common} />;
};

/** Tab 1:质量报告——优先展示 dj-analyze 产出的 analysis 报告(overall 表 + PNG),
 *  无则回退到手算聚合(数值指标 + 纯 div 直方图)。 */
const ReportTab: React.FC<{ versionId: string }> = ({ versionId }) => {
  const { token } = theme.useToken();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [analysis, setAnalysis] =
    useState<DataPlatform.AnalysisReport | null>();
  const [report, setReport] = useState<DataPlatform.QualityReport>();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setAnalysis(undefined);
    setReport(undefined);
    getAnalysisReport(versionId)
      .then(async (res) => {
        if (cancelled) return;
        const d = res.data;
        if (d && (d.overall || (d.images?.length ?? 0) > 0)) {
          setAnalysis(d);
        } else {
          // 无 dj-analyze 报告 → 回退手算聚合
          try {
            const r = await getQualityReport(versionId);
            if (!cancelled) setReport(r.data);
          } catch {
            if (!cancelled)
              setError('质量报告加载失败（该版本可能尚未完成质量评估）');
          }
        }
      })
      .catch(() => {
        if (!cancelled)
          setError('质量报告加载失败（该版本可能尚未完成质量评估）');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  if (loading)
    return <Spin style={{ display: 'block', margin: '48px auto' }} />;
  if (error) return <Empty description={error} />;

  // 主路径:dj-analyze 分析报告(overall.csv 聚合表 + analysis/ PNG)
  if (analysis) {
    const overallCols =
      analysis.overall?.columns.map((c, i) => ({
        title: i === 0 ? '指标' : c,
        dataIndex: String(i),
        key: String(i),
        ellipsis: i > 0,
        width: i === 0 ? 160 : undefined,
      })) ?? [];
    const overallRows = (analysis.overall?.rows ?? []).map((r, ri) => {
      const obj: Record<string, any> = { key: ri };
      r.forEach((v, ci) => {
        obj[String(ci)] = v;
      });
      return obj;
    });
    const labelOf = (k: string) =>
      k === 'distributions'
        ? '指标分布（直方图 / 箱线图 / 词云）'
        : k === 'correlation'
          ? '指标相关性热力图'
          : '分析图';
    return (
      <div>
        {analysis.overall && (
          <>
            <Typography.Title level={5}>
              指标总体统计（overall）
            </Typography.Title>
            <Table
              size="small"
              bordered
              pagination={false}
              scroll={{ x: 'max-content' }}
              columns={overallCols}
              dataSource={overallRows}
              style={{ marginBottom: 24 }}
            />
          </>
        )}
        {analysis.images.map((im) => (
          <div key={im.name} style={{ marginBottom: 24 }}>
            <Typography.Title level={5}>{labelOf(im.kind)}</Typography.Title>
            <img
              alt={im.name}
              style={{
                maxWidth: '100%',
                marginTop: 8,
                border: `1px solid ${token.colorBorderSecondary}`,
              }}
              src={`/api/v1/dataset-versions/${versionId}/analysis-image?name=${encodeURIComponent(
                im.name,
              )}`}
            />
          </div>
        ))}
      </div>
    );
  }

  if (!report?.metrics?.length) {
    return <Empty description="暂无数值型质量指标" />;
  }

  return (
    <div>
      <Typography.Paragraph type="secondary">
        共 {report.rows} 行数据，{report.metrics.length} 个数值型指标。
      </Typography.Paragraph>
      {report.metrics.map((m) => {
        const maxCount = Math.max(...m.histogram.map((b) => b.count), 1);
        return (
          <div key={m.name} style={{ marginBottom: 32 }}>
            <Typography.Title level={5}>{m.name}</Typography.Title>
            <div style={{ display: 'flex', gap: 32, marginBottom: 12 }}>
              <Statistic title="均值" value={fmtNum(m.mean)} />
              <Statistic title="最小" value={fmtNum(m.min)} />
              <Statistic title="最大" value={fmtNum(m.max)} />
              <Statistic title="P50" value={fmtNum(m.p50)} />
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                gap: 2,
                height: 120,
                padding: '8px 8px 0',
                background: token.colorFillQuaternary,
                borderRadius: token.borderRadiusLG,
              }}
            >
              {m.histogram.map((b, idx) => (
                <Tooltip
                  key={`${m.name}-${b.x0}`}
                  title={`[${fmtNum(b.x0)}, ${fmtNum(b.x1)}${
                    idx === m.histogram.length - 1 ? ']' : ')'
                  } · ${b.count} 条`}
                >
                  <div
                    style={{
                      flex: 1,
                      height: '100%',
                      display: 'flex',
                      alignItems: 'flex-end',
                    }}
                  >
                    <div
                      style={{
                        width: '100%',
                        height: `${(b.count / maxCount) * 100}%`,
                        minHeight: b.count > 0 ? 2 : 0,
                        background: token.colorPrimary,
                        borderRadius: '2px 2px 0 0',
                      }}
                    />
                  </div>
                </Tooltip>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
};

/** Tab 2:逐条得分（列按 metrics 动态生成） */
const StatsTab: React.FC<{ versionId: string }> = ({ versionId }) => {
  const [metrics, setMetrics] = useState<string[]>([]);

  const columns = useMemo<ProColumns<DataPlatform.VersionStatsRow>[]>(
    () => [
      { title: '#', dataIndex: 'index', width: 64 },
      { title: '文本（前 200 字符）', dataIndex: 'text', ellipsis: true },
      ...metrics.map<ProColumns<DataPlatform.VersionStatsRow>>((m) => ({
        title: m,
        key: m,
        width: 140,
        render: (_, r) => {
          const v = r.stats?.[m];
          if (typeof v === 'number') {
            return Number.isInteger(v) ? v : v.toFixed(4);
          }
          return v == null ? '-' : String(v);
        },
      })),
    ],
    [metrics],
  );

  return (
    <ProTable<DataPlatform.VersionStatsRow>
      rowKey="index"
      size="small"
      search={false}
      options={false}
      columns={columns}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 10 }}
      request={async (params) => {
        try {
          const res = await getVersionStats(versionId, {
            current: params.current,
            pageSize: params.pageSize,
          });
          const next = res.metrics ?? [];
          setMetrics((prev) =>
            prev.length === next.length && prev.every((v, i) => v === next[i])
              ? prev
              : next,
          );
          return { data: res.data, total: res.total, success: res.success };
        } catch {
          return { data: [], total: 0, success: false };
        }
      }}
    />
  );
};

/** Tab 3:低质过滤 — 提交后轮询 job 状态，完成后刷新任务列表并跳转数据集页 */
const FilterTab: React.FC<{
  job: DataPlatform.Job;
  input: DataPlatform.IngestOutput;
  qualityOps: DataPlatform.Operator[];
  opMap: Record<string, DataPlatform.Operator>;
  /** 成功后父组件回调：刷新任务列表 + 跳转到产出版本所在数据集 */
  onSuccess: (output: DataPlatform.IngestOutput) => void;
}> = ({ job, input, qualityOps, opMap, onSuccess }) => {
  // running=true 时禁止重复提交，并展示轮询进度 Alert
  const [running, setRunning] = useState(false);
  const [statusText, setStatusText] = useState('');
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  // 组件卸载时清理轮询定时器，避免在抽屉关闭后仍写 state
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    },
    [],
  );

  const pollUntilDone = (
    jobId: string,
    outputSnapshot: DataPlatform.IngestOutput | undefined,
  ) => {
    const tick = async () => {
      try {
        const res = await getJob(jobId);
        const j = res?.data;
        if (!j) {
          // 网络抖动，继续轮询
          pollTimer.current = setTimeout(tick, 2000);
          return;
        }
        if (j.state === 'success') {
          setRunning(false);
          setStatusText('');
          // 优先用 job 最新 output，其次用提交时快照
          const out =
            (j.output as DataPlatform.IngestOutput | undefined) ??
            outputSnapshot;
          if (out) {
            message.success(
              `低质过滤完成，产出 ${out.datasetName} ${out.versionLabel ?? `v${out.versionNo}`}（${out.rows ?? '-'} 行）`,
            );
            onSuccess(out);
          } else {
            message.success('低质过滤完成');
            onSuccess({} as DataPlatform.IngestOutput);
          }
        } else if (j.state === 'failed' || j.state === 'cancelled') {
          setRunning(false);
          setStatusText('');
          message.error(`执行失败：${j.error ?? '未知错误'}`);
        } else {
          // pending / running — 继续轮询，更新提示文字
          const pct = j.progress > 0 ? `（${j.progress}%）` : '';
          setStatusText(`低质过滤执行中${pct}，请稍候…`);
          pollTimer.current = setTimeout(tick, 2000);
        }
      } catch {
        // 请求出错时继续轮询，不中断
        pollTimer.current = setTimeout(tick, 2000);
      }
    };
    tick();
  };

  return (
    <>
      <Typography.Paragraph type="secondary">
        选择质量算子并配置阈值，对输入版本 {renderInput(input)}{' '}
        执行过滤加工：得分不达标的数据将被删除，结果存储为该数据集的新版本（原版本不变）。
      </Typography.Paragraph>

      {running && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={statusText || '低质过滤执行中，请稍候…'}
          description="任务完成后将自动刷新任务列表并跳转到产出数据集。"
        />
      )}

      <ProForm<{
        operators: string[];
        params?: Record<string, Record<string, unknown>>;
      }>
        submitter={{
          searchConfig: { submitText: '删除低质数据（产出新版本）' },
          resetButtonProps: { style: { display: 'none' } },
          submitButtonProps: { loading: running, disabled: running },
        }}
        onFinish={async (values) => {
          const operators = (values.operators ?? []).map((name) => ({
            name,
            params: values.params?.[name],
          }));
          setRunning(true);
          setStatusText('正在提交加工任务…');
          try {
            const res = await createJob({
              name: `${job.name} - 低质过滤`,
              type: 'clean',
              datasetVersionId: input.versionId,
              operators,
            });
            const created = res?.data;
            if (!created) {
              setRunning(false);
              setStatusText('');
              message.error('任务创建失败，请重试');
              return false;
            }
            // createJob 是同步执行（后端 await run_process_job），
            // 返回时任务已到终态；但为保持 UX 一致性仍走轮询分支。
            if (
              created.state === 'success' ||
              created.state === 'failed' ||
              created.state === 'cancelled'
            ) {
              // 任务已完成，直接处理结果
              setRunning(false);
              setStatusText('');
              if (created.state === 'success') {
                const out = created.output as
                  | DataPlatform.IngestOutput
                  | undefined;
                if (out) {
                  message.success(
                    `低质过滤完成，产出 ${out.datasetName} ${out.versionLabel ?? `v${out.versionNo}`}（${out.rows ?? '-'} 行）`,
                  );
                  onSuccess(out);
                } else {
                  message.success('低质过滤完成');
                  onSuccess({} as DataPlatform.IngestOutput);
                }
              } else {
                message.error(`执行失败：${created.error ?? '未知错误'}`);
              }
            } else {
              // pending/running — 开始轮询
              setStatusText('低质过滤执行中，请稍候…');
              pollUntilDone(
                created.id,
                created.output as DataPlatform.IngestOutput | undefined,
              );
            }
          } catch {
            setRunning(false);
            setStatusText('');
            message.error('请求失败，请重试');
          }
          return false; // 阻止 ProForm 自动 reset（我们手动控制）
        }}
      >
        <ProFormSelect
          name="operators"
          label="质量算子"
          mode="multiple"
          placeholder="选择用于过滤的质量算子"
          rules={[{ required: true, message: '请至少选择一个算子' }]}
          options={qualityOps.map((o) => ({
            label: `${o.label}（${o.name}）`,
            value: o.name,
          }))}
        />
        <ProFormDependency name={['operators']}>
          {({ operators }) => {
            const selected = (operators ?? []) as string[];
            const fields = selected
              .filter((n) => (opMap[n]?.params?.length ?? 0) > 0)
              .flatMap((n) =>
                opMap[n].params.map((p) =>
                  renderParamField(n, opMap[n].label, p),
                ),
              );
            return fields.length ? <>{fields}</> : null;
          }}
        </ProFormDependency>
      </ProForm>
    </>
  );
};

const Quality: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);
  const [qualityOps, setQualityOps] = useState<DataPlatform.Operator[]>([]);
  const opMap = useMemo(
    () => Object.fromEntries(qualityOps.map((o) => [o.name, o])),
    [qualityOps],
  );

  useEffect(() => {
    listOperators()
      .then((res) => {
        setQualityOps(
          (res.data ?? []).filter((o) => o.name.endsWith('_filter')),
        );
      })
      .catch(() => {
        message.error('算子目录加载失败，请刷新页面重试');
      });
  }, []);

  /** FilterTab 成功回调：刷新任务列表 + 跳转到产出版本所在数据集页 */
  const handleFilterSuccess = (output: DataPlatform.IngestOutput) => {
    // 刷新质量任务列表
    actionRef.current?.reload();
    // 跳转到数据集仓库并带上 datasetId，让用户直接看到新版本
    if (output.datasetId) {
      history.push(`/datasets/list?highlight=${output.datasetId}`);
    } else {
      history.push('/datasets/list');
    }
  };

  /** 删除单个质量任务(通用 /jobs/:id;后端只挡 running,前端对 running 隐藏入口)。 */
  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteJob(id);
      hide();
      message.success('任务已删除');
      if (currentJob?.id === id) {
        setDetailOpen(false);
        setCurrentJob(undefined);
      }
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  /** 批量删除质量任务(后端跳过 running 的,返回实际删除数)。 */
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
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            setCurrentJob(record);
            setDetailOpen(true);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '状态',
      dataIndex: 'state',
      render: (_, r) => {
        const m = STATE_META[r.state];
        return <Tag color={m.color}>{m.text}</Tag>;
      },
    },
    {
      title: '输入版本',
      dataIndex: 'input',
      render: (_, r) => renderInput(r.input),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 80,
      render: (_, r) =>
        // 运行中的任务后端拒绝删除(需先停止);其余状态给删除入口
        r.state === 'running'
          ? []
          : [
              <Popconfirm
                key="delete"
                title="删除该任务记录？"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(r.id)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>,
            ],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Job>
        headerTitle="质量评估任务"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true }}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Job[]),
        }}
        tableAlertOptionRender={() => (
          <Popconfirm
            title={`确认删除选中的 ${selectedRows.length} 个任务？`}
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={handleBatchDelete}
          >
            <Button type="link" danger>
              批量删除
            </Button>
          </Popconfirm>
        )}
        request={async (params) => {
          const res = await listJobs({
            current: params.current,
            pageSize: params.pageSize,
            type: 'quality',
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
        toolBarRender={() => [
          <Button
            key="create"
            type="primary"
            onClick={() => history.push('/assessment/quality/editor')}
          >
            新建质量评估
          </Button>,
        ]}
      />

      <Drawer
        size={760}
        open={detailOpen}
        title={currentJob?.name}
        onClose={() => {
          setDetailOpen(false);
          setCurrentJob(undefined);
        }}
      >
        {currentJob && (
          <>
            <ProDescriptions<DataPlatform.Job>
              column={2}
              dataSource={currentJob}
              columns={[
                {
                  title: '状态',
                  dataIndex: 'state',
                  render: (_, r) => {
                    const m = STATE_META[r.state];
                    return <Tag color={m.color}>{m.text}</Tag>;
                  },
                },
                {
                  title: '输入版本',
                  dataIndex: 'input',
                  render: (_, r) => renderInput(r.input),
                },
                {
                  title: '创建时间',
                  dataIndex: 'createdAt',
                  render: (_, r) => formatDateTime(r.createdAt),
                },
                {
                  title: '错误',
                  dataIndex: 'error',
                  render: (_, r) =>
                    r.error ? (
                      <Typography.Text type="danger">{r.error}</Typography.Text>
                    ) : (
                      '-'
                    ),
                },
              ]}
            />
            {currentJob.input ? (
              <Tabs
                key={currentJob.id}
                style={{ marginTop: 8 }}
                items={[
                  {
                    key: 'report',
                    label: '质量报告',
                    children: (
                      <ReportTab versionId={currentJob.input.versionId} />
                    ),
                  },
                  {
                    key: 'stats',
                    label: '逐条得分',
                    children: (
                      <StatsTab versionId={currentJob.input.versionId} />
                    ),
                  },
                  {
                    key: 'filter',
                    label: '低质过滤',
                    children: (
                      <FilterTab
                        job={currentJob}
                        input={currentJob.input}
                        qualityOps={qualityOps}
                        opMap={opMap}
                        onSuccess={handleFilterSuccess}
                      />
                    ),
                  },
                ]}
              />
            ) : (
              <Empty
                style={{ marginTop: 24 }}
                description="该任务缺少输入版本信息"
              />
            )}
          </>
        )}
      </Drawer>
    </PageContainer>
  );
};

export default Quality;
