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
  Spin,
  Statistic,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createJob,
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

/** Tab 1:质量报告（指标统计 + 纯 div 直方图） */
const ReportTab: React.FC<{ versionId: string }> = ({ versionId }) => {
  const { token } = theme.useToken();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [report, setReport] = useState<DataPlatform.QualityReport>();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getQualityReport(versionId)
      .then((res) => {
        if (!cancelled) setReport(res.data);
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
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // 组件卸载时清理轮询定时器，避免在抽屉关闭后仍写 state
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    },
    [],
  );

  const pollUntilDone = (jobId: string, outputSnapshot: DataPlatform.IngestOutput | undefined) => {
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
          const out = (j.output as DataPlatform.IngestOutput | undefined) ?? outputSnapshot;
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
                const out = created.output as DataPlatform.IngestOutput | undefined;
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
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Job>
        headerTitle="质量评估任务"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true }}
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
            onClick={() => history.push('/quality/editor')}
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
