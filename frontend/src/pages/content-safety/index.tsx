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
  Modal,
  message,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { DatasetFilter } from '@/components';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createReviewJob,
  createReviewRule,
  deleteReviewRule,
  getDataset,
  getJob,
  getReviewReport,
  listDatasets,
  listReviewFindings,
  listReviewJobs,
  listReviewRules,
  previewDatasetVersion,
  updateReviewRule,
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

/** 规则库管理弹窗:自定义敏感词/正则的 CRUD + 启用开关。
 * 改动通过 onChanged 通知外层刷新可选规则列表。 */
const RulesManager: React.FC<{
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}> = ({ open, onClose, onChanged }) => {
  const [rules, setRules] = useState<DataPlatform.ReviewRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<DataPlatform.ReviewRuleCreate>({
    name: '',
    kind: 'word',
    pattern: '',
    category: 'other',
    severity: 'medium',
  });

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listReviewRules({ current: 1, pageSize: 100 });
      setRules(res.data ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) reload();
  }, [open, reload]);

  const onAdd = async () => {
    if (!draft.name.trim() || !draft.pattern.trim()) {
      message.warning('请填写规则名称与内容');
      return;
    }
    try {
      await createReviewRule({
        ...draft,
        name: draft.name.trim(),
        pattern: draft.pattern.trim(),
      });
      message.success('规则已添加');
      setDraft((d) => ({ ...d, name: '', pattern: '' }));
      reload();
      onChanged();
    } catch (e: any) {
      message.error(e?.info?.errorMessage || e?.data?.message || '添加失败');
    }
  };

  const onToggle = async (rule: DataPlatform.ReviewRule, enabled: boolean) => {
    await updateReviewRule(rule.id, { enabled }).catch(() =>
      message.error('更新失败'),
    );
    reload();
    onChanged();
  };

  const onDelete = async (rule: DataPlatform.ReviewRule) => {
    await deleteReviewRule(rule.id).catch(() => message.error('删除失败'));
    reload();
    onChanged();
  };

  return (
    <Modal
      title="内容安全规则库"
      open={open}
      onCancel={onClose}
      footer={null}
      width={860}
    >
      <Paragraph type="secondary" style={{ marginBottom: 12 }}>
        规则库沉淀可复用的自定义敏感词 /
        正则(带类别与严重度)。启用中的规则会自动参与
        上传前置预检;建审核任务时可勾选参与。已建任务不受后续增删影响(创建时冻结)。
      </Paragraph>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input
          placeholder="规则名称"
          style={{ width: 140 }}
          value={draft.name}
          onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
        />
        <Select
          style={{ width: 100 }}
          value={draft.kind}
          onChange={(kind) => setDraft((d) => ({ ...d, kind }))}
          options={[
            { label: '敏感词', value: 'word' },
            { label: '正则', value: 'regex' },
          ]}
        />
        <Input
          placeholder={
            draft.kind === 'word' ? '敏感词(子串匹配)' : '正则表达式'
          }
          style={{ width: 240 }}
          value={draft.pattern}
          onChange={(e) => setDraft((d) => ({ ...d, pattern: e.target.value }))}
        />
        <Select
          style={{ width: 100 }}
          value={draft.category}
          onChange={(category) => setDraft((d) => ({ ...d, category }))}
          options={CATEGORY_OPTIONS}
        />
        <Select
          style={{ width: 90 }}
          value={draft.severity}
          onChange={(severity) => setDraft((d) => ({ ...d, severity }))}
          options={(
            Object.keys(SEVERITY_META) as DataPlatform.ReviewSeverity[]
          ).map((s) => ({ label: SEVERITY_META[s].text, value: s }))}
        />
        <Button type="primary" onClick={onAdd}>
          添加
        </Button>
      </Space>
      <Table<DataPlatform.ReviewRule>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rules}
        pagination={false}
        columns={[
          { title: '名称', dataIndex: 'name', width: 140, ellipsis: true },
          {
            title: '类型',
            dataIndex: 'kind',
            width: 80,
            render: (k) => (k === 'word' ? '敏感词' : '正则'),
          },
          { title: '内容', dataIndex: 'pattern', ellipsis: true },
          {
            title: '类别',
            dataIndex: 'category',
            width: 90,
            render: (c) => renderCategory(c),
          },
          {
            title: '严重度',
            dataIndex: 'severity',
            width: 90,
            render: (s) => renderSeverity(s),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 80,
            render: (_, r) => (
              <Switch
                size="small"
                checked={r.enabled}
                onChange={(v) => onToggle(r, v)}
              />
            ),
          },
          {
            title: '操作',
            key: 'op',
            width: 80,
            render: (_, r) => (
              <Popconfirm
                title="删除该规则？已建任务不受影响。"
                onConfirm={() => onDelete(r)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>
            ),
          },
        ]}
      />
    </Modal>
  );
};

/** 命中明细表(按表·类别·来源·严重度筛,接 listReviewFindings)。
 * tables 为报告 byTable 的表名列表,多表时开放「所属表」筛选。 */
const FindingsTable: React.FC<{ jobId: string; tables?: string[] }> = ({
  jobId,
  tables,
}) => {
  const columns: ProColumns<DataPlatform.ReviewFinding>[] = [
    {
      title: '所属表',
      dataIndex: 'tableName',
      width: 110,
      ellipsis: true,
      valueType: 'select',
      search: tables && tables.length > 0 ? undefined : false,
      valueEnum: Object.fromEntries(
        (tables ?? []).map((t) => [t, { text: t }]),
      ),
      render: (_, r) => r.tableName ?? '-',
    },
    { title: '行号', dataIndex: 'rowIndex', width: 80, search: false },
    {
      title: '字段',
      dataIndex: 'field',
      width: 100,
      ellipsis: true,
      search: false,
      render: (_, r) => r.field ?? '-',
    },
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
          tableName: params.tableName,
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
  // 处置方式:tag 打标(产出带 safety 字段版本) / delete 删除(产出净化版+存档)
  const [action, setAction] = useState<DataPlatform.ReviewAction>('tag');
  // 多表版本:参与审核的成员表(默认全选)
  const [targetMembers, setTargetMembers] = useState<string[]>([]);
  // 逐表扫描字段(表名 -> 字段列表;不选 = 默认取 text/首个文本字段;单文件键 "data")
  const [scanFieldsMap, setScanFieldsMap] = useState<Record<string, string[]>>(
    {},
  );
  // 逐表候选字段(预览 columns;拉取失败为空,选择器退化为自由输入)
  const [memberColumns, setMemberColumns] = useState<Record<string, string[]>>(
    {},
  );
  // 规则库:启用中的条目 + 本次任务勾选(默认全选启用项)
  const [rules, setRules] = useState<DataPlatform.ReviewRule[]>([]);
  const [ruleIds, setRuleIds] = useState<string[]>([]);
  const [rulesOpen, setRulesOpen] = useState(false);

  const reloadRules = useCallback(() => {
    listReviewRules({ current: 1, pageSize: 100, enabled: true })
      .then((r) => {
        const list = r.data ?? [];
        setRules(list);
        // 默认全选启用项;保留用户已有勾选中仍存在的部分
        setRuleIds((prev) => {
          const alive = prev.filter((id) => list.some((x) => x.id === id));
          return alive.length ? alive : list.map((x) => x.id);
        });
      })
      .catch(() => setRules([]));
  }, []);

  useEffect(() => {
    reloadRules();
  }, [reloadRules]);

  // 当前选中版本的成员表(单表/旧版本为空或单元素,不展示选择器)
  const memberNames = (
    versions.find((v) => v.id === versionId)?.tables ?? []
  ).map((t) => t.tableName);

  // —— 报告区 state ——
  const [activeJob, setActiveJob] = useState<DataPlatform.Job>();
  const [jobDatasetId, setJobDatasetId] = useState<string>();
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

  // 切换版本 → 成员默认全选
  useEffect(() => {
    setTargetMembers(
      (versions.find((v) => v.id === versionId)?.tables ?? []).map(
        (t) => t.tableName,
      ),
    );
  }, [versionId]);

  // 切换版本 → 清空字段选择,并逐成员拉候选字段(预览 columns;失败静默,
  // 选择器退化为自由输入,不阻塞建任务)
  useEffect(() => {
    setScanFieldsMap({});
    setMemberColumns({});
    if (!versionId) return;
    const v = versions.find((x) => x.id === versionId);
    if (!v || isBinaryFormat(v.format)) return;
    const tables = v.tables ?? [];
    if (!tables.length) {
      // 旧单文件版本:整版预览取列,固定键 "data"(与后端口径一致)
      previewDatasetVersion(versionId, { limit: 20 })
        .then((r) => setMemberColumns({ data: r.columns ?? [] }))
        .catch(() => undefined);
      return;
    }
    for (const t of tables) {
      if (!t.storageUri?.startsWith('s3://')) continue;
      // 成员预览 key = storageUri 去掉 s3://<bucket>/ 前缀
      const key = t.storageUri.replace(/^s3:\/\/[^/]+\//, '');
      previewDatasetVersion(versionId, { limit: 20, key })
        .then((r) =>
          setMemberColumns((prev) => ({
            ...prev,
            [t.tableName]: r.columns ?? [],
          })),
        )
        .catch(() => undefined);
    }
  }, [versionId]);

  // 展示字段选择器的表:多表 = 被勾选的成员;单表 = 该成员;无成员 = 固定键 "data"
  const curVersion = versions.find((v) => v.id === versionId);
  const auditedTables: string[] =
    !versionId || !curVersion || isBinaryFormat(curVersion.format)
      ? []
      : memberNames.length > 1
        ? targetMembers
        : memberNames.length === 1
          ? memberNames
          : ['data'];

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
  const doSubmit = async (
    customWords: string[],
    cleanedRegex: DataPlatform.ReviewCustomRegex[],
  ) => {
    if (!versionId) return;
    setSubmitting(true);
    const hide = message.loading('正在创建审核任务…', 0);
    // 只带被审表里有选择的项;全空则不传(后端走默认取文本逻辑)
    const scanFields = Object.fromEntries(
      Object.entries(scanFieldsMap).filter(
        ([t, fs]) => fs.length > 0 && auditedTables.includes(t),
      ),
    );
    try {
      const res = await createReviewJob({
        datasetVersionId: versionId,
        name: name.trim() || undefined,
        config: {
          categories,
          customWords,
          customRegex: cleanedRegex,
          action,
          useLlm,
          usePii,
          useFlaggedWords,
          sampleLimit,
          scanFields: Object.keys(scanFields).length ? scanFields : undefined,
        },
        // 全选(或无成员)不传 → 后端审全部;部分勾选才圈范围
        targetMembers:
          memberNames.length > 1 && targetMembers.length < memberNames.length
            ? targetMembers
            : undefined,
        ruleIds: ruleIds.length ? ruleIds : undefined,
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

  const onSubmit = () => {
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    if (memberNames.length > 1 && !targetMembers.length) {
      message.warning('请至少选择一个参与审核的成员表');
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
      !cleanedRegex.length &&
      !ruleIds.length
    ) {
      message.warning(
        '请至少启用一种检测手段(LLM/内置词表/PII)或填写自定义敏感词/正则/勾选规则库',
      );
      return;
    }
    if (action === 'delete') {
      Modal.confirm({
        title: '确认以「删除」方式处置命中行？',
        content:
          '将产出剔除全部命中行的净化版本(强制全量扫描,忽略样本上限);被删行会完整' +
          '存档(<表名>.removed.jsonl)并逐条记录在命中明细中,原版本不受影响。',
        okText: '确认删除处置',
        okButtonProps: { danger: true },
        onOk: () => doSubmit(customWords, cleanedRegex),
      });
      return;
    }
    doSubmit(customWords, cleanedRegex);
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
          ? `${r.input.datasetName}（${r.input.versionLabel ?? `v${r.input.versionNo}`}）`
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
          {memberNames.length > 1 && (
            <Select
              mode="multiple"
              placeholder="参与审核的成员表"
              style={{ minWidth: 260 }}
              maxTagCount="responsive"
              value={targetMembers}
              onChange={setTargetMembers}
              options={memberNames.map((m) => ({ label: m, value: m }))}
            />
          )}
        </Space>
        {memberNames.length > 1 &&
          targetMembers.length < memberNames.length && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message={`仅审核选中的 ${targetMembers.length}/${memberNames.length} 个成员表;产出版本只包含被审成员,且被审版本将标记为「未完整扫描」`}
            />
          )}
        {auditedTables.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Title level={5} style={{ marginTop: 0 }}>
              扫描字段
            </Title>
            <Space direction="vertical" size={8}>
              {auditedTables.map((t) => (
                <Space key={t}>
                  <Text
                    strong
                    style={{ display: 'inline-block', minWidth: 120 }}
                  >
                    {t === 'data' && !memberNames.length ? '默认(单文件)' : t}
                  </Text>
                  <Select
                    mode="tags"
                    allowClear
                    placeholder="不选 = 默认扫 text/首个文本字段"
                    style={{ width: 420 }}
                    maxTagCount="responsive"
                    value={scanFieldsMap[t] ?? []}
                    onChange={(vals) =>
                      setScanFieldsMap((prev) => ({
                        ...prev,
                        [t]: vals as string[],
                      }))
                    }
                    options={(memberColumns[t] ?? [])
                      .filter((c) => c !== '__member')
                      .map((c) => ({ label: c, value: c }))}
                  />
                </Space>
              ))}
            </Space>
          </div>
        )}

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
              命中处置
            </Title>
            <Radio.Group
              value={action}
              onChange={(e) => setAction(e.target.value)}
              options={[
                { label: '打标(保留原行,附加 safety 字段)', value: 'tag' },
                { label: '删除(产出净化版,被删行存档留痕)', value: 'delete' },
              ]}
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
                disabled={action === 'delete'}
              />
              <Text type="secondary">
                {action === 'delete'
                  ? '删除处置强制全量扫描,样本上限不生效'
                  : '超过则只扫前 N 行,报告会显式标注'}
              </Text>
            </Space>
          </Col>

          <Col span={12}>
            <Title level={5}>
              规则库{' '}
              <Button
                size="small"
                type="link"
                onClick={() => setRulesOpen(true)}
              >
                管理
              </Button>
            </Title>
            {rules.length ? (
              <Select
                mode="multiple"
                style={{ width: '100%' }}
                placeholder="勾选参与本次审核的规则库条目"
                maxTagCount="responsive"
                optionFilterProp="label"
                value={ruleIds}
                onChange={setRuleIds}
                options={rules.map((r) => ({
                  label: `${r.name}(${r.kind === 'word' ? '词' : '正则'}·${
                    CATEGORY_META[r.category]?.text ?? r.category
                  })`,
                  value: r.id,
                }))}
              />
            ) : (
              <Text type="secondary">
                暂无启用中的规则,点「管理」沉淀可复用的敏感词/正则
              </Text>
            )}

            <Title level={5} style={{ marginTop: 16 }}>
              自定义敏感词(换行分隔)
            </Title>
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
          params={{ datasetId: jobDatasetId }}
          request={async (params) => {
            const res = await listReviewJobs({
              current: params.current,
              pageSize: params.pageSize,
              datasetId: jobDatasetId,
            });
            return { data: res.data, total: res.total, success: res.success };
          }}
          toolBarRender={() => [
            <DatasetFilter
              key="dataset"
              value={jobDatasetId}
              onChange={setJobDatasetId}
            />,
          ]}
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
                  label={(k) =>
                    SOURCE_META[k as DataPlatform.ReviewSource] ?? k
                  }
                />
              </Paragraph>
              {body.byTable && Object.keys(body.byTable).length > 0 && (
                <Paragraph>
                  <Text strong>按成员表(命中行):</Text>{' '}
                  <CountTags counts={body.byTable} label={(k) => k} />
                </Paragraph>
              )}
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

              {report?.taggedVersionId && (
                <Paragraph>
                  <Text strong>
                    {body.action === 'delete' ? '净化版本:' : '打标版本:'}
                  </Text>{' '}
                  <Text code>{report.taggedVersionId}</Text>{' '}
                  <a
                    onClick={(e) => {
                      e.preventDefault();
                      history.push('/datasets/list');
                    }}
                  >
                    {body.action === 'delete' ? '查看净化版本' : '查看打标版本'}
                  </a>
                </Paragraph>
              )}

              <Divider style={{ margin: '8px 0 16px' }} />
              <Title level={5}>命中明细</Title>
              <FindingsTable
                jobId={activeJob.id}
                tables={Object.keys(body.byTable ?? {})}
              />
            </>
          )}
        </Card>
      ) : (
        <Card size="small">
          <Empty description="新建审核任务,或从上方任务列表点选以查看报告" />
        </Card>
      )}

      <RulesManager
        open={rulesOpen}
        onClose={() => setRulesOpen(false)}
        onChanged={reloadRules}
      />
    </PageContainer>
  );
};

export default ContentSafety;
