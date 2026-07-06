import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess, useLocation } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Input,
  InputNumber,
  Modal,
  message,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DatasetFilter } from '@/components';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createReviewJob,
  createReviewRule,
  deleteReviewRule,
  getDataset,
  listDatasets,
  listReviewJobs,
  listReviewRules,
  updateReviewRule,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { jobVersionColumns, renderState } from '@/utils/jobState';
import { suggestTaskName } from '@/utils/taskName';
import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  renderCategory,
  renderSeverity,
  SEVERITY_META,
} from './shared';

const { Text, Title, Paragraph } = Typography;

// 「全部」扫描:传一个远超任何数据集行数的上限,后端 min(total, limit)=total 即全量
// (与 delete 模式强制 sampleLimit=行数 的全量口径一致)
const SCAN_ALL_LIMIT = 1_000_000_000;

/** 规则库管理弹窗:自定义敏感词/正则的 CRUD + 启用开关。
 * 改动通过 onChanged 通知外层刷新可选规则列表。 */
const RulesManager: React.FC<{
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}> = ({ open, onClose, onChanged }) => {
  const access = useAccess();
  const canAdd = access.hasPerm('governance:contentsafety:add');
  const canEdit = access.hasPerm('governance:contentsafety:edit');
  const canRemove = access.hasPerm('governance:contentsafety:remove');
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
        {canAdd && (
          <Button type="primary" onClick={onAdd}>
            添加
          </Button>
        )}
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
            render: (_, r) =>
              canEdit && (
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
            render: (_, r) =>
              canRemove && (
                <Popconfirm
                  title="删除该规则？已建任务不受影响。"
                  onConfirm={() => onDelete(r)}
                >
                  <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>
                    删除
                  </a>
                </Popconfirm>
              ),
          },
        ]}
      />
    </Modal>
  );
};

const ContentSafety: React.FC = () => {
  const access = useAccess();
  const canRun = access.hasPerm('governance:contentsafety:run');
  const actionRef = useRef<ActionType | null>(null);

  // —— 配置区 state ——
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false); // 用户改过则不再自动覆盖
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
  const [submitting, setSubmitting] = useState(false);
  const [sampleLimit, setSampleLimit] = useState<number>(500);
  // 「全部」:不限样本量,扫描全部行(送大上限 SCAN_ALL_LIMIT)
  const [scanAll, setScanAll] = useState(false);
  // 处置方式固定为 delete:命中行直接删除,产出净化版 + 被删行存档
  const action: DataPlatform.ReviewAction = 'delete';
  // 多表版本:参与审核的成员表(默认全选)
  const [targetMembers, setTargetMembers] = useState<string[]>([]);
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

  // 自动任务名:数据集变化时重算,用户手动改过(nameDirty)则不再覆盖(同 processing/quality 编辑器)
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = useMemo(
    () => suggestTaskName(selectedDatasetName, '内容审核'),
    [selectedDatasetName],
  );
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  // —— 任务列表筛选 ——
  const [jobDatasetId, setJobDatasetId] = useState<string>();

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

  // 开始审核 → createReviewJob → 跳独立报告页(那里轮询状态并展示报告)
  const doSubmit = async (
    customWords: string[],
    cleanedRegex: DataPlatform.ReviewCustomRegex[],
  ) => {
    if (!versionId) return;
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
          action,
          useLlm,
          usePii,
          useFlaggedWords,
          sampleLimit: scanAll ? SCAN_ALL_LIMIT : sampleLimit,
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
      history.push(`/governance/content-safety/report?jobId=${res.data.id}`);
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
    doSubmit(customWords, cleanedRegex);
  };

  const jobColumns: ProColumns<DataPlatform.Job>[] = [
    {
      title: '任务名',
      dataIndex: 'name',
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            history.push(
              `/governance/content-safety/report?jobId=${record.id}`,
            );
          }}
        >
          {dom}
        </a>
      ),
    },
    // 与其他任务列表统一的 数据集/输入版本/产物版本 三列(审核产出净化版)
    ...jobVersionColumns(),
    {
      title: '状态',
      dataIndex: 'state',
      width: 90,
      render: (_, r) => renderState(r.state),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 100,
      render: (_, r) => [
        <a
          key="report"
          onClick={(e) => {
            e.preventDefault();
            history.push(`/governance/content-safety/report?jobId=${r.id}`);
          }}
        >
          查看报告
        </a>,
      ],
    },
  ];

  return (
    <PageContainer>
      {/* 配置区 */}
      <Card title="新建内容审核" size="small" style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input
            placeholder="任务名(自动生成,可编辑)"
            style={{ width: 220 }}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setNameDirty(true);
            }}
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
              options={[
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
                disabled={scanAll}
              />
              <Checkbox
                checked={scanAll}
                onChange={(e) => setScanAll(e.target.checked)}
              >
                全部
              </Checkbox>
              <Text type="secondary">
                {scanAll
                  ? '扫描全部行'
                  : '只扫前 N 行,未扫行原样保留(不删除),报告会显式标注'}
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
        {canRun && (
          <Button type="primary" loading={submitting} onClick={onSubmit}>
            开始审核
          </Button>
        )}
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

      <RulesManager
        open={rulesOpen}
        onClose={() => setRulesOpen(false)}
        onChanged={reloadRules}
      />
    </PageContainer>
  );
};

export default ContentSafety;
