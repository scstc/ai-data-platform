import { PageContainer, ProDescriptions } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Alert,
  Badge,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getJob, getReviewReport } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { renderState } from '@/utils/jobState';
import {
  CATEGORY_META,
  CountTags,
  FindingsTable,
  SEVERITY_META,
  SOURCE_META,
} from './shared';

const { Text, Paragraph } = Typography;

/** 版本号文案:与 jobVersionColumns 的版本列一致,只显版本号不带数据集名。 */
const versionText = (v?: DataPlatform.IngestOutput) =>
  v ? (v.versionLabel ?? `v${v.versionNo}`) : '-';

/** 内容安全审核报告页:独立页面(替代列表页内嵌报告区),?jobId= 定位任务。
 *  任务运行中每 2s 轮询状态,终态后载入审核报告 + 命中明细,
 *  建任务后直接跳转本页即可看到"进行中 → 报告"的推进过程。
 *  布局对齐质量报告 master-detail:左侧常驻成员表清单(点行切换),
 *  右侧展示当前表的命中明细(替代原「所属表」下拉筛选)。 */
const ContentSafetyReport: React.FC = () => {
  const location = useLocation();
  const jobId = useMemo(
    () => new URLSearchParams(location.search).get('jobId'),
    [location.search],
  );
  const { token } = theme.useToken();

  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<DataPlatform.Job>();
  const [report, setReport] = useState<DataPlatform.ReviewReport>();
  const [activeTable, setActiveTable] = useState<string>();
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  // 成员表清单:报告 byTable 对每个被审成员都有条目(含 0 命中)
  const tables = useMemo(
    () => Object.entries(report?.reviewReport?.byTable ?? {}),
    [report],
  );

  // 报告载入后默认选中第一个有命中的表(无命中则第一个)
  useEffect(() => {
    if (!tables.length) return;
    setActiveTable((prev) =>
      prev && tables.some(([t]) => t === prev)
        ? prev
        : (tables.find(([, n]) => n > 0) ?? tables[0])[0],
    );
  }, [tables]);

  useEffect(() => {
    if (!jobId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      const res = await getJob(jobId).catch(() => undefined);
      if (cancelled) return;
      const j = res?.data;
      setJob(j);
      setLoading(false);
      if (!j) return;
      if (j.state === 'pending' || j.state === 'running') {
        pollTimer.current = setTimeout(tick, 2000);
        return;
      }
      const r = await getReviewReport(jobId).catch(() => undefined);
      if (!cancelled && r?.data) setReport(r.data);
    };
    tick();
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [jobId]);

  if (loading)
    return (
      <PageContainer header={{ title: '内容审核报告' }}>
        <Spin style={{ display: 'block', margin: '48px auto' }} />
      </PageContainer>
    );

  if (!jobId || !job)
    return (
      <PageContainer
        header={{
          title: '内容审核报告',
          onBack: () => history.push('/governance/content-safety'),
        }}
      >
        <Card>
          <Empty description={jobId ? '任务不存在或已删除' : '缺少任务参数'} />
        </Card>
      </PageContainer>
    );

  const body = report?.reviewReport;

  return (
    <PageContainer
      header={{
        title: job.name,
        onBack: () => history.push('/governance/content-safety'),
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
              title: '被审版本',
              dataIndex: 'input',
              render: (_, r) => versionText(r.input),
            },
            {
              title: '产出版本（净化版）',
              dataIndex: 'output',
              render: (_, r) => versionText(r.output),
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

      <Card size="small" title="审核报告">
        {job.state === 'failed' && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="审核任务失败"
            description={job.error || '未知错误'}
          />
        )}
        {(job.state === 'pending' || job.state === 'running') && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="审核进行中,报告将在完成后自动展示…"
          />
        )}

        {body ? (
          <>
            {body.sampleLimitApplied && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="已应用样本上限"
                description={`总行数 ${body.totalRows},仅扫描前 ${body.scannedRows} 行,其余行未审核。`}
              />
            )}

            {body.warnings && body.warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="部分检测已降级"
                description={body.warnings.join(';')}
              />
            )}

            <Row gutter={32} style={{ marginBottom: 16 }}>
              <Col>
                <Statistic title="总行数" value={body.totalRows} />
              </Col>
              <Col>
                <Statistic title="已扫描" value={body.scannedRows} />
              </Col>
              <Col>
                <Statistic
                  title="命中行"
                  value={body.flaggedRows}
                  valueStyle={{
                    color: body.flaggedRows > 0 ? '#cf1322' : undefined,
                  }}
                />
              </Col>
              {body.action === 'delete' && (
                <Col>
                  <Statistic
                    title="已删除行"
                    value={body.deletedRows ?? 0}
                    valueStyle={{
                      color:
                        (body.deletedRows ?? 0) > 0 ? '#cf1322' : undefined,
                    }}
                  />
                </Col>
              )}
            </Row>

            <Paragraph>
              <Text strong>按类别:</Text>{' '}
              <CountTags
                counts={body.byCategory}
                label={(k) =>
                  CATEGORY_META[k as DataPlatform.ReviewCategory]?.text ?? k
                }
              />
            </Paragraph>
            <Paragraph>
              <Text strong>按严重度:</Text>{' '}
              <CountTags
                counts={body.bySeverity}
                label={(k) =>
                  SEVERITY_META[k as DataPlatform.ReviewSeverity]?.text ?? k
                }
              />
            </Paragraph>
            <Paragraph>
              <Text strong>按来源:</Text>{' '}
              <CountTags
                counts={body.bySource}
                label={(k) => SOURCE_META[k as DataPlatform.ReviewSource] ?? k}
              />
            </Paragraph>
            {body.removedArchives &&
              Object.keys(body.removedArchives).length > 0 && (
                <Paragraph>
                  <Text strong>被删行存档:</Text>{' '}
                  <Space size={[4, 8]} wrap>
                    {Object.entries(body.removedArchives).map(([t, uri]) => (
                      <Tag key={t} title={uri}>
                        {t}.removed.jsonl
                      </Tag>
                    ))}
                  </Space>
                  <Text type="secondary">
                    (存于产出版本目录,连同命中明细构成删除留痕)
                  </Text>
                </Paragraph>
              )}
          </>
        ) : (
          job.state !== 'pending' &&
          job.state !== 'running' &&
          job.state !== 'failed' && <Empty description="该任务暂无审核报告" />
        )}
      </Card>

      {/* 命中明细:对齐质量报告 master-detail,左侧成员表清单,右侧当前表的明细 */}
      {body &&
        (tables.length > 0 ? (
          <div
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'stretch',
              marginTop: 12,
            }}
          >
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
                    命中 {tables.filter(([, n]) => n > 0).length}/
                    {tables.length}
                  </Text>
                </Space>
              }
            >
              {tables.map(([t, n]) => {
                const active = t === activeTable;
                return (
                  <div
                    key={t}
                    onClick={() => setActiveTable(t)}
                    style={{
                      padding: '6px 8px',
                      borderRadius: token.borderRadius,
                      cursor: 'pointer',
                      background: active ? token.colorPrimaryBg : undefined,
                    }}
                  >
                    <Space size={6} style={{ minWidth: 0 }}>
                      <Badge status={n > 0 ? 'error' : 'success'} />
                      <Text
                        strong={active}
                        ellipsis={{ tooltip: t }}
                        style={{ maxWidth: 170 }}
                      >
                        {t}
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
                      {n > 0 ? `命中 ${n} 行` : '无命中'}
                    </Text>
                  </div>
                );
              })}
            </Card>
            <Card
              title={`命中明细 · ${activeTable ?? '-'}`}
              size="small"
              style={{ flex: 1, minWidth: 0 }}
            >
              <FindingsTable
                jobId={job.id}
                member={tables.length > 1 ? activeTable : undefined}
              />
            </Card>
          </div>
        ) : (
          // 旧任务报告可能无 byTable → 退回单卡不筛表
          <Card title="命中明细" size="small" style={{ marginTop: 12 }}>
            <FindingsTable jobId={job.id} />
          </Card>
        ))}
    </PageContainer>
  );
};

export default ContentSafetyReport;
