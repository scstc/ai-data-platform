import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Empty,
  Input,
  InputNumber,
  message,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createReviewJob,
  getDataset,
  getJob,
  getReviewReport,
  listDatasets,
  listReviewFindings,
  listReviewJobs,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

const { Text, Title, Paragraph } = Typography;

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

const CATEGORY_META: Record<
  DataPlatform.ReviewCategory,
  { text: string; color: string }
> = {
  porn: { text: '色情', color: 'magenta' },
  gambling: { text: '赌博', color: 'gold' },
  drugs: { text: '毒品', color: 'volcano' },
  politics: { text: '涉政', color: 'red' },
  terrorism: { text: '涉恐', color: 'red' },
  pii: { text: '隐私', color: 'blue' },
  other: { text: '其他', color: 'default' },
};

const SEVERITY_META: Record<
  DataPlatform.ReviewSeverity,
  { text: string; color: string }
> = {
  high: { text: '高', color: 'error' },
  medium: { text: '中', color: 'warning' },
  low: { text: '低', color: 'default' },
};

const SOURCE_META: Record<DataPlatform.ReviewSource, string> = {
  keyword: '自定义敏感词',
  regex: '自定义正则',
  flagged_words: '内置敏感词',
  llm: 'LLM 审核',
  pii: 'PII 识别',
};

// 类别多选项(黄赌毒政恐 + 隐私/其他)
const CATEGORY_OPTIONS = (
  Object.keys(CATEGORY_META) as DataPlatform.ReviewCategory[]
).map((c) => ({ label: CATEGORY_META[c].text, value: c }));

const renderCategory = (c: DataPlatform.ReviewCategory) => {
  const m = CATEGORY_META[c] ?? CATEGORY_META.other;
  return <Tag color={m.color}>{m.text}</Tag>;
};

const renderSeverity = (s: DataPlatform.ReviewSeverity) => {
  const m = SEVERITY_META[s] ?? SEVERITY_META.low;
  return <Tag color={m.color}>{m.text}</Tag>;
};

const renderSource = (s: DataPlatform.ReviewSource) => SOURCE_META[s] ?? s;

/** 计数 map → Tag 列表(空则占位) */
const CountTags: React.FC<{
  counts: Record<string, number>;
  label: (k: string) => React.ReactNode;
}> = ({ counts, label }) => {
  const entries = Object.entries(counts ?? {}).filter(([, v]) => v > 0);
  if (!entries.length) return <Text type="secondary">无</Text>;
  return (
    <Space size={[4, 8]} wrap>
      {entries.map(([k, v]) => (
        <Tag key={k}>
          {label(k)} {v}
        </Tag>
      ))}
    </Space>
  );
};

/** 命中明细表(按类别·来源·严重度筛,接 listReviewFindings) */
const FindingsTable: React.FC<{ jobId: string }> = ({ jobId }) => {
  const columns: ProColumns<DataPlatform.ReviewFinding>[] = [
    { title: '行号', dataIndex: 'rowIndex', width: 80, search: false },
    {
      title: '类别',
      dataIndex: 'category',
      width: 90,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(CATEGORY_META) as DataPlatform.ReviewCategory[]).map(
          (c) => [c, { text: CATEGORY_META[c].text }],
        ),
      ),
      render: (_, r) => renderCategory(r.category),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(SEVERITY_META) as DataPlatform.ReviewSeverity[]).map(
          (s) => [s, { text: SEVERITY_META[s].text }],
        ),
      ),
      render: (_, r) => renderSeverity(r.severity),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 120,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(SOURCE_META) as DataPlatform.ReviewSource[]).map((s) => [
          s,
          { text: SOURCE_META[s] },
        ]),
      ),
      render: (_, r) => renderSource(r.source),
    },
    { title: '命中详情', dataIndex: 'detail', width: 160, search: false },
    {
      title: '片段',
      dataIndex: 'snippet',
      ellipsis: true,
      search: false,
    },
  ];

  return (
    <ProTable<DataPlatform.ReviewFinding, DataPlatform.ReviewFindingListParams>
      rowKey={(r) => `${r.rowIndex}-${r.source}-${r.category}-${r.detail}`}
      size="small"
      options={false}
      search={{ labelWidth: 'auto' }}
      columns={columns}
      pagination={{ pageSize: 10 }}
      request={async (params) => {
        const res = await listReviewFindings(jobId, {
          current: params.current,
          pageSize: params.pageSize,
          category: params.category,
          source: params.source,
          severity: params.severity,
        });
        return { data: res.data, total: res.total, success: res.success };
      }}
    />
  );
};

const ContentSafety: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);

  // —— 配置区 state ——
  const [name, setName] = useState('');
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);

  const [useLlm, setUseLlm] = useState(true);
  const [useFlaggedWords, setUseFlaggedWords] = useState(true);
  const [usePii, setUsePii] = useState(true);
  const [categories, setCategories] = useState<DataPlatform.ReviewCategory[]>([
    'porn',
    'gambling',
    'drugs',
    'politics',
    'terrorism',
  ]);
  const [customWordsText, setCustomWordsText] = useState('');
  const [customRegex, setCustomRegex] = useState<
    DataPlatform.ReviewCustomRegex[]
  >([]);
  const [sampleLimit, setSampleLimit] = useState<number>(500);
  const [submitting, setSubmitting] = useState(false);

  // —— 报告区 state ——
  const [activeJob, setActiveJob] = useState<DataPlatform.Job>();
  const [report, setReport] = useState<DataPlatform.ReviewReport>();

  // 数据集 → 版本级联(用 listDatasets + getDataset,参照 quality/editor)
  useEffect(() => {
    listDatasets({ current: 1, pageSize: 500 }).then((r) =>
      setDatasets(r.data),
    );
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => {
      const vs = r.data.versions ?? [];
      setVersions(vs);
      setVersionId(vs.length ? vs[vs.length - 1].id : undefined);
    });
  }, [datasetId]);

  // 从数据集版本表「流程」入口跳入时,按 URL 预选数据集 + 版本(覆盖默认选最新版),
  // 让“扫描某版本”一键直达;不带参则维持原交互
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  // 载入某 job 的报告(被选中时 / 轮询时)
  const loadReport = useCallback(async (jobId: string) => {
    const res = await getReviewReport(jobId).catch(() => undefined);
    if (res?.data) setReport(res.data);
    return res?.data;
  }, []);

  const addRegex = () =>
    setCustomRegex((prev) => [...prev, { name: '', pattern: '' }]);
  const removeRegex = (idx: number) =>
    setCustomRegex((prev) => prev.filter((_, i) => i !== idx));
  const updateRegex = (
    idx: number,
    key: keyof DataPlatform.ReviewCustomRegex,
    value: string,
  ) =>
    setCustomRegex((prev) =>
      prev.map((r, i) => (i === idx ? { ...r, [key]: value } : r)),
    );

  // 开始审核 → createReviewJob → 轮询 getJob 状态 → 完成后载报告
  const onSubmit = async () => {
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    const customWords = customWordsText
      .split('\n')
      .map((w) => w.trim())
      .filter(Boolean);
    const cleanedRegex = customRegex.filter(
      (r) => r.name.trim() && r.pattern.trim(),
    );
    if (
      !useLlm &&
      !useFlaggedWords &&
      !usePii &&
      !customWords.length &&
      !cleanedRegex.length
    ) {
      message.warning(
        '请至少启用一种检测手段(LLM/内置词表/PII)或填写自定义敏感词/正则',
      );
      return;
    }

    setSubmitting(true);
    const hide = message.loading('正在创建审核任务…', 0);
    try {
      const res = await createReviewJob({
        datasetVersionId: versionId,
        name: name.trim() || undefined,
        config: {
          categories,
          customWords,
          customRegex: cleanedRegex,
          useLlm,
          usePii,
          useFlaggedWords,
          sampleLimit,
        },
      });
      hide();
      message.success('审核任务已创建');
      actionRef.current?.reload();
      const job = res.data;
      setActiveJob(job);
      setReport(undefined);
      pollJob(job.id);
    } catch {
      hide();
      message.error('创建失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  // 轮询单 job 状态(2s),终态停止并载报告;复用 getJob(同 ingest 推进式轮询思路)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const pollJob = useCallback(
    (jobId: string) => {
      const tick = async () => {
        const res = await getJob(jobId).catch(() => undefined);
        const job = res?.data;
        if (job) {
          setActiveJob(job);
          if (job.state === 'success' || job.state === 'failed') {
            actionRef.current?.reload();
            await loadReport(jobId);
            return;
          }
        }
        pollTimer.current = setTimeout(tick, 2000);
      };
      tick();
    },
    [loadReport],
  );

  // 卸载时清理轮询
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    },
    [],
  );

  // 点选任务列表 → 载入该 job 报告
  const onSelectJob = async (job: DataPlatform.Job) => {
    setActiveJob(job);
    setReport(undefined);
    if (job.state === 'running' || job.state === 'pending') {
      pollJob(job.id);
    } else {
      await loadReport(job.id);
    }
  };

  const jobColumns: ProColumns<DataPlatform.Job>[] = [
    {
      title: '任务名',
      dataIndex: 'name',
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            onSelectJob(record);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '被审版本',
      dataIndex: 'input',
      render: (_, r) =>
        r.input
          ? `${r.input.datasetName}（${r.input.datasetId} ${r.input.versionLabel ?? `v${r.input.versionNo}`}）`
          : '-',
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 90,
      render: (_, r) => {
        const m = STATE_META[r.state];
        return <Tag color={m.color}>{m.text}</Tag>;
      },
    },
    {
      title: '命中行',
      dataIndex: 'output',
      width: 90,
      render: (_, r) => r.output?.rows ?? '-',
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (_, r) => formatDateTime(r.createdAt),
    },
  ];

  const body = report?.reviewReport;

  return (
    <PageContainer>
      {/* 配置区 */}
      <Card title="新建内容审核" size="small" style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input
            placeholder="任务名(可选)"
            style={{ width: 220 }}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Select
            placeholder="选择数据集"
            style={{ width: 220 }}
            showSearch
            optionFilterProp="label"
            value={datasetId}
            onChange={setDatasetId}
            options={datasets.map((d) => ({ label: d.name, value: d.id }))}
          />
          <Select
            placeholder="选择版本"
            style={{ width: 220 }}
            value={versionId}
            onChange={setVersionId}
            options={versions.map((v) => {
              const isBinary = isBinaryFormat(v.format);
              return {
                label: isBinary
                  ? `${v.versionLabel}（${v.format}·二进制不可审核）`
                  : `${v.versionLabel}（${v.format}）`,
                value: v.id,
                disabled: isBinary,
              };
            })}
          />
        </Space>

        <Row gutter={24}>
          <Col span={12}>
            <Title level={5}>检测手段</Title>
            <Space direction="vertical" size={8}>
              <Space>
                <Switch checked={useLlm} onChange={setUseLlm} />
                <Text>LLM 审核(大模型分类 黄/赌/毒/政/恐)</Text>
              </Space>
              <Space>
                <Switch
                  checked={useFlaggedWords}
                  onChange={setUseFlaggedWords}
                />
                <Text>内置敏感词表</Text>
              </Space>
              <Space>
                <Switch checked={usePii} onChange={setUsePii} />
                <Text>PII 隐私识别(身份证/手机号/邮箱/银行卡/IP)</Text>
              </Space>
            </Space>

            <Title level={5} style={{ marginTop: 16 }}>
              审核类别
            </Title>
            <Checkbox.Group
              options={CATEGORY_OPTIONS}
              value={categories}
              onChange={(v) =>
                setCategories(v as DataPlatform.ReviewCategory[])
              }
            />

            <Title level={5} style={{ marginTop: 16 }}>
              样本上限
            </Title>
            <Space>
              <InputNumber
                min={1}
                style={{ width: 160 }}
                value={sampleLimit}
                onChange={(v) => setSampleLimit(v ?? 500)}
              />
              <Text type="secondary">超过则只扫前 N 行,报告会显式标注</Text>
            </Space>
          </Col>

          <Col span={12}>
            <Title level={5}>自定义敏感词(换行分隔)</Title>
            <Input.TextArea
              rows={4}
              placeholder={'每行一个敏感词\n例如:\n违禁词A\n违禁词B'}
              value={customWordsText}
              onChange={(e) => setCustomWordsText(e.target.value)}
            />

            <Title level={5} style={{ marginTop: 16 }}>
              自定义正则
            </Title>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {customRegex.map((r, idx) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: 受控行无稳定 id,顺序即身份
                <Space key={idx} style={{ width: '100%' }}>
                  <Input
                    placeholder="名称"
                    style={{ width: 120 }}
                    value={r.name}
                    onChange={(e) => updateRegex(idx, 'name', e.target.value)}
                  />
                  <Input
                    placeholder="正则表达式"
                    style={{ width: 220 }}
                    value={r.pattern}
                    onChange={(e) =>
                      updateRegex(idx, 'pattern', e.target.value)
                    }
                  />
                  <Button danger onClick={() => removeRegex(idx)}>
                    删除
                  </Button>
                </Space>
              ))}
              <Button onClick={addRegex}>+ 添加正则</Button>
            </Space>
          </Col>
        </Row>

        <Divider style={{ margin: '16px 0' }} />
        <Button type="primary" loading={submitting} onClick={onSubmit}>
          开始审核
        </Button>
      </Card>

      {/* 任务列表 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <ProTable<DataPlatform.Job>
          headerTitle="审核任务"
          actionRef={actionRef}
          rowKey="id"
          search={false}
          options={{ reload: true }}
          columns={jobColumns}
          request={async (params) => {
            const res = await listReviewJobs({
              current: params.current,
              pageSize: params.pageSize,
            });
            return { data: res.data, total: res.total, success: res.success };
          }}
        />
      </Card>

      {/* 报告区 */}
      {activeJob ? (
        <Card
          size="small"
          title={
            <Space>
              <span>审核报告 · {activeJob.name}</span>
              <Tag color={STATE_META[activeJob.state].color}>
                {STATE_META[activeJob.state].text}
              </Tag>
            </Space>
          }
        >
          {activeJob.state === 'failed' && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message="审核任务失败"
              description={activeJob.error || '未知错误'}
            />
          )}
          {(activeJob.state === 'pending' || activeJob.state === 'running') && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="审核进行中,报告将在完成后展示…"
            />
          )}

          {body && (
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
                  label={(k) =>
                    SOURCE_META[k as DataPlatform.ReviewSource] ?? k
                  }
                />
              </Paragraph>

              {report?.taggedVersionId && (
                <Paragraph>
                  <Text strong>打标版本:</Text>{' '}
                  <Text code>{report.taggedVersionId}</Text>{' '}
                  <a
                    onClick={(e) => {
                      e.preventDefault();
                      history.push('/datasets/list');
                    }}
                  >
                    查看打标版本
                  </a>
                </Paragraph>
              )}

              <Divider style={{ margin: '8px 0 16px' }} />
              <Title level={5}>命中明细</Title>
              <FindingsTable jobId={activeJob.id} />
            </>
          )}
        </Card>
      ) : (
        <Card size="small">
          <Empty description="新建审核任务,或从上方任务列表点选以查看报告" />
        </Card>
      )}
    </PageContainer>
  );
};

export default ContentSafety;
