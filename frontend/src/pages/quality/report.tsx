import { ClearOutlined } from '@ant-design/icons';
import type { ProColumns } from '@ant-design/pro-components';
import {
  PageContainer,
  ProDescriptions,
  ProTable,
} from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  getAnalysisReport,
  getJob,
  getQualityMembers,
  getQualityReport,
  getVersionStats,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { renderState } from '@/utils/jobState';

const { Text } = Typography;

/** 版本号文案:与 jobVersionColumns 的版本列一致,只显版本号不带数据集名。 */
const versionText = (v?: DataPlatform.IngestOutput) =>
  v ? (v.versionLabel ?? `v${v.versionNo}`) : '-';

const fmtNum = (v: number) =>
  Number.isInteger(v) ? String(v) : Number(v.toFixed(4)).toString();

/** 质量报告——优先展示 dj-analyze 产出的 analysis 报告(overall 表 + PNG),
 *  无则回退到手算聚合(数值指标 + 纯 div 直方图)。member:多文件版本指定成员。 */
const ReportTab: React.FC<{ versionId: string; member?: string }> = ({
  versionId,
  member,
}) => {
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
    getAnalysisReport(versionId, { member })
      .then(async (res) => {
        if (cancelled) return;
        const d = res.data;
        if (d && (d.overall || (d.images?.length ?? 0) > 0)) {
          setAnalysis(d);
        } else {
          // 无 dj-analyze 报告 → 回退手算聚合
          try {
            const r = await getQualityReport(versionId, { member });
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
  }, [versionId, member]);

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
              )}${member ? `&member=${encodeURIComponent(member)}` : ''}`}
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

/** 逐条得分（列按 metrics 动态生成）。member:多文件版本指定成员。 */
const StatsTab: React.FC<{ versionId: string; member?: string }> = ({
  versionId,
  member,
}) => {
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
      key={member}
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
            member,
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

/** 质量评估报告页:独立页面(替代列表页抽屉),?jobId= 定位任务。
 *  报告读输入版本(评估不产新版本,stats_uri 回写输入版本/成员);历史任务曾
 *  产出评估版本,有 output 时优先以兼容旧数据。布局对齐编辑器 master-detail:
 *  左侧常驻文件清单(点行切换),右侧展示当前文件的报告/逐条得分。 */
const QualityReport: React.FC = () => {
  const location = useLocation();
  const jobId = useMemo(
    () => new URLSearchParams(location.search).get('jobId'),
    [location.search],
  );
  const { token } = theme.useToken();

  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<DataPlatform.Job>();
  const [members, setMembers] = useState<DataPlatform.QualityMember[]>();
  const [activeMember, setActiveMember] = useState<string>();

  const resultVersionId = job?.output?.versionId ?? job?.input?.versionId;
  const datasetId = job?.input?.datasetId;

  useEffect(() => {
    if (!jobId) {
      setLoading(false);
      return;
    }
    getJob(jobId)
      .then((res) => setJob(res?.data))
      .catch(() => setJob(undefined))
      .finally(() => setLoading(false));
  }, [jobId]);

  useEffect(() => {
    setMembers(undefined);
    setActiveMember(undefined);
    if (!resultVersionId) return;
    getQualityMembers(resultVersionId)
      .then((res) => {
        setMembers(res.data);
        // 默认选中第一个已评估的文件
        setActiveMember(
          (res.data.find((m) => m.hasStats) ?? res.data[0])?.memberName,
        );
      })
      .catch(() => setMembers([]));
  }, [resultVersionId]);

  // 单成员(含旧版单文件合成的 "data")不传 member,行为等同单文件版本
  const memberParam = (members?.length ?? 0) > 1 ? activeMember : undefined;
  const activeInfo = members?.find((m) => m.memberName === activeMember);

  if (loading)
    return (
      <PageContainer header={{ title: '质量评估报告' }}>
        <Spin style={{ display: 'block', margin: '48px auto' }} />
      </PageContainer>
    );

  if (!jobId || !job)
    return (
      <PageContainer
        header={{
          title: '质量评估报告',
          onBack: () => history.push('/assessment/quality'),
        }}
      >
        <Card>
          <Empty description={jobId ? '任务不存在或已删除' : '缺少任务参数'} />
        </Card>
      </PageContainer>
    );

  return (
    <PageContainer
      header={{
        title: job.name,
        onBack: () => history.push('/assessment/quality'),
        extra:
          datasetId && resultVersionId
            ? [
                <Tooltip
                  key="clean"
                  title="带入本数据集与版本前往数据清洗:按指标阈值过滤低质数据并输出新版本"
                >
                  <Button
                    icon={<ClearOutlined />}
                    onClick={() =>
                      history.push(
                        `/governance/cleaning/editor?datasetId=${datasetId}&versionId=${resultVersionId}`,
                      )
                    }
                  >
                    清洗低质数据
                  </Button>
                </Tooltip>,
              ]
            : undefined,
      }}
    >
      <Card size="small" style={{ marginBottom: 12 }}>
        <ProDescriptions<DataPlatform.Job>
          column={2}
          dataSource={job}
          columns={[
            {
              title: '状态',
              dataIndex: 'state',
              render: (_, r) => renderState(r.state),
            },
            {
              title: '数据集',
              dataIndex: ['input', 'datasetName'],
              render: (_, r) => r.input?.datasetName ?? '-',
            },
            {
              title: '输入版本',
              dataIndex: 'input',
              render: (_, r) => versionText(r.input),
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
      </Card>

      {!job.input || !resultVersionId ? (
        <Card>
          <Empty
            description={
              job.input ? '该任务尚未产出评估结果' : '该任务缺少输入版本信息'
            }
          />
        </Card>
      ) : members === undefined ? (
        <Card>
          <Spin style={{ display: 'block', margin: '48px auto' }} />
        </Card>
      ) : (
        <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
          <Card
            size="small"
            style={{ width: 240, flexShrink: 0 }}
            styles={{ body: { padding: 8 } }}
            title={
              <Space>
                <span>文件</span>
                <Text
                  type="secondary"
                  style={{ fontWeight: 'normal', fontSize: 12 }}
                >
                  已评估 {members.filter((m) => m.hasStats).length}/
                  {members.length}
                </Text>
              </Space>
            }
          >
            {members.map((m) => {
              const active = m.memberName === activeMember;
              return (
                <div
                  key={m.memberName}
                  onClick={() => setActiveMember(m.memberName)}
                  style={{
                    padding: '6px 8px',
                    borderRadius: token.borderRadius,
                    cursor: 'pointer',
                    background: active ? token.colorPrimaryBg : undefined,
                  }}
                >
                  <Space size={6} style={{ minWidth: 0 }}>
                    <Badge status={m.hasStats ? 'success' : 'default'} />
                    <Text
                      strong={active}
                      ellipsis={{ tooltip: m.memberName }}
                      style={{ maxWidth: 170 }}
                    >
                      {m.memberName}
                    </Text>
                  </Space>
                  <Text
                    type="secondary"
                    style={{
                      fontSize: 12,
                      paddingLeft: 14,
                      display: 'block',
                    }}
                  >
                    {m.hasStats ? '已评估' : '未评估'}
                  </Text>
                </div>
              );
            })}
          </Card>
          <Card
            title={`评估报告 · ${activeMember ?? '-'}`}
            size="small"
            style={{ flex: 1, minWidth: 0 }}
          >
            {activeInfo && !activeInfo.hasStats ? (
              <Empty description="该文件未参与本次评估" />
            ) : (
              <Tabs
                key={activeMember ?? ''}
                items={[
                  {
                    key: 'report',
                    label: '质量报告',
                    children: (
                      <ReportTab
                        versionId={resultVersionId}
                        member={memberParam}
                      />
                    ),
                  },
                  {
                    key: 'stats',
                    label: '逐条得分',
                    children: (
                      <StatsTab
                        versionId={resultVersionId}
                        member={memberParam}
                      />
                    ),
                  },
                ]}
              />
            )}
          </Card>
        </div>
      )}
    </PageContainer>
  );
};

export default QualityReport;
