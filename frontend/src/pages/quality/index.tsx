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
  // key 必须直接写在 JSX 上,经 spread 传入会被 React 忽略
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
          // 指标集未变化时保持原引用,避免 columns 重建引发的级联更新
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

/** Tab 3:低质过滤（filter 算子带阈值跑 clean job 产新版本） */
const FilterTab: React.FC<{
  job: DataPlatform.Job;
  input: DataPlatform.IngestOutput;
  qualityOps: DataPlatform.Operator[];
  opMap: Record<string, DataPlatform.Operator>;
}> = ({ job, input, qualityOps, opMap }) => {
  return (
    <>
      <Typography.Paragraph type="secondary">
        选择质量算子并配置阈值，对输入版本 {renderInput(input)}{' '}
        执行过滤加工：得分不达标的数据将被删除，结果存储为该数据集的新版本（原版本不变）。
      </Typography.Paragraph>
      <ProForm<{
        operators: string[];
        params?: Record<string, Record<string, unknown>>;
      }>
        submitter={{
          searchConfig: { submitText: '删除低质数据（产出新版本）' },
          resetButtonProps: { style: { display: 'none' } },
        }}
        onFinish={async (values) => {
          const operators = (values.operators ?? []).map((name) => ({
            name,
            params: values.params?.[name],
          }));
          const hide = message.loading('低质过滤执行中（dj-process）…', 0);
          try {
            const res = await createJob({
              name: `${job.name} - 低质过滤`,
              type: 'clean',
              datasetVersionId: input.versionId,
              operators,
            });
            hide();
            if (res?.data?.state === 'success') {
              const o = res.data.output;
              message.success(
                `已删除低质数据，产出 ${o?.datasetName} ${o?.versionLabel ?? `v${o?.versionNo}`}（${o?.rows} 行）`,
              );
              return true;
            }
            message.error(`执行失败：${res?.data?.error ?? '未知错误'}`);
            return false;
          } catch {
            hide();
            message.error('请求失败，请重试');
            return false;
          }
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
  // 质量算子目录(name 以 _filter 结尾)
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
