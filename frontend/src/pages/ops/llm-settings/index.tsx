import {
  DownOutlined,
  RightOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import {
  Alert,
  AutoComplete,
  Avatar,
  Badge,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  message,
  Popconfirm,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import {
  addProviderModel,
  createLlmProvider,
  deleteLlmProvider,
  deleteProviderModel,
  fetchProviderModels,
  getLlmUsage,
  listLlmProviders,
  listLlmSystemModels,
  listProviderModels,
  revealLlmProviderKey,
  selectProviderModel,
  testLlmProvider,
  testLlmProviderConfig,
  updateLlmProvider,
  updateLlmSystemModels,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

/** 供应商类型 → 默认 baseUrl（模型不在弹窗指定,经「显示模型」拉取/系统模型设置选定） */
const PRESET_BASE_URLS: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: 'https://api.deepseek.com',
  glm: 'https://open.bigmodel.cn/api/paas/v4',
  minimax: 'https://api.minimaxi.com/v1',
  openai: 'https://api.openai.com/v1',
  siliconflow: 'https://api.siliconflow.cn/v1',
  custom: '',
};

const PROVIDER_COLORS: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: 'blue',
  glm: 'purple',
  minimax: 'cyan',
  openai: 'green',
  siliconflow: 'orange',
  custom: 'default',
};

const PROVIDER_LABELS: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: 'DeepSeek',
  glm: 'GLM',
  minimax: 'MiniMax',
  openai: 'OpenAI',
  siliconflow: 'SiliconFlow',
  custom: '自定义',
};

/**
 * 各供应商常见模型名 —— 拉取失败 / 供应商无 /models 接口（如 GLM）时的兜底候选，
 * 也用作「模型」输入框的自动补全建议；始终可手填覆盖。
 */
const PRESET_MODELS: Record<DataPlatform.LlmProvider['provider'], string[]> = {
  deepseek: [
    'deepseek-chat',
    'deepseek-reasoner',
    'deepseek-v4-flash',
    'deepseek-v4-pro',
  ],
  glm: ['glm-4.6', 'glm-4.5', 'glm-4.5-air', 'glm-4-plus', 'glm-4-flash'],
  minimax: ['MiniMax-M2', 'MiniMax-Text-01', 'abab6.5s-chat'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'o3-mini', 'gpt-4.1', 'gpt-4.1-mini'],
  siliconflow: [],
  custom: [],
};

/** 品牌头像底色（卡片左侧圆标，无官方 logo 用品牌首字母代替） */
const AVATAR_BG: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: '#4D6BFE',
  glm: '#722ED1',
  minimax: '#13C2C2',
  openai: '#10A37F',
  siliconflow: '#7C3AED',
  custom: '#8C8C8C',
};

/** 能力位展示顺序与文案（对齐 Dify「系统模型设置」） */
const CAPABILITY_ORDER = [
  'chat',
  'embedding',
  'rerank',
  'speech2text',
  'tts',
] as const;

const CAPABILITY_LABELS: Record<
  DataPlatform.LlmSystemModelItem['capability'],
  string
> = {
  chat: '系统推理模型',
  embedding: 'Embedding 模型',
  rerank: 'Rerank 模型',
  speech2text: '语音转文本模型',
  tts: '文本转语音模型',
};

/** 卡片上能力 tag 的短文案（该供应商被哪些能力位引用） */
const CAPABILITY_TAGS: Record<string, string> = {
  chat: 'LLM',
  embedding: 'TEXT EMBEDDING',
  rerank: 'RERANK',
  speech2text: 'SPEECH2TEXT',
  tts: 'TTS',
};

const SOURCE_META: Record<
  DataPlatform.LlmModel['source'],
  { text: string; color: string }
> = {
  fetched: { text: '拉取', color: 'geekblue' },
  manual: { text: '手填', color: 'default' },
};

type EditingProvider = Partial<DataPlatform.LlmProvider> | null;

// ─── 用量监控子组件 ────────────────────────────────────────────────────────────

// 用量 feature 标识 → 中文(operator = dj 算子经 llm-proxy 的调用;其余为平台内建 AI 功能)
const FEATURE_ZH: Record<string, string> = {
  operator: '算子调用',
  infer_schema: 'Schema 推断',
  generate_task: '任务生成',
  qa: '智能问答',
  suggest_name: '名称建议',
  suggest_tags: '标签建议',
  moderate: '内容审核',
  judge: '质量评审',
};
const featureLabel = (f: string) => FEATURE_ZH[f] ?? f;

// 任务类型 → 中文(与各任务列表菜单名对齐)
const JOB_TYPE_ZH: Record<string, string> = {
  clean: '数据清洗',
  process: '数据加工',
  distillation: '数据蒸馏',
  synthesis: '数据合并',
  augmentation: '数据增强',
  trainset: '数据合成',
  quality: '质量评估',
};

const UsagePanel: React.FC = () => {
  const [days, setDays] = useState<number>(7);
  const [loading, setLoading] = useState(false);
  const [usage, setUsage] = useState<DataPlatform.LlmUsageSummary | null>(null);

  const load = useCallback(async (d: number) => {
    setLoading(true);
    try {
      const res = await getLlmUsage(d);
      if (res.success) setUsage(res.data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(days);
  }, [days, load]);

  const byFeatureCols: ColumnsType<DataPlatform.LlmUsageByFeature> = [
    {
      title: '功能',
      dataIndex: 'feature',
      render: (_, r) => featureLabel(r.feature),
    },
    { title: '调用次数', dataIndex: 'calls', align: 'right' },
    { title: 'Token 总量', dataIndex: 'tokens', align: 'right' },
  ];

  const byJobCols: ColumnsType<DataPlatform.LlmUsageByJob> = [
    { title: '任务', dataIndex: 'jobName', ellipsis: true },
    {
      title: '类型',
      dataIndex: 'jobType',
      width: 110,
      render: (_, r) => JOB_TYPE_ZH[r.jobType] ?? r.jobType ?? '-',
    },
    { title: '调用次数', dataIndex: 'calls', align: 'right', width: 100 },
    { title: 'Token 总量', dataIndex: 'tokens', align: 'right', width: 120 },
  ];

  const recentCols: ColumnsType<DataPlatform.LlmUsageRecent> = [
    {
      title: '功能',
      dataIndex: 'feature',
      ellipsis: true,
      render: (_, r) => featureLabel(r.feature),
    },
    { title: '模型', dataIndex: 'model', width: 160, ellipsis: true },
    { title: 'Token', dataIndex: 'totalTokens', align: 'right', width: 90 },
    {
      title: '状态',
      dataIndex: 'success',
      width: 80,
      render: (_, r) => (
        <Badge
          status={r.success ? 'success' : 'error'}
          text={r.success ? '成功' : '失败'}
        />
      ),
    },
    {
      title: '延迟(ms)',
      dataIndex: 'latencyMs',
      align: 'right',
      width: 100,
    },
    {
      title: '时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (_, r) => formatDateTime(r.createdAt),
    },
  ];

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 16,
        }}
      >
        <span style={{ fontWeight: 500 }}>统计周期：</span>
        <Select
          value={days}
          onChange={setDays}
          style={{ width: 100 }}
          options={[
            { label: '近 7 天', value: 7 },
            { label: '近 30 天', value: 30 },
          ]}
        />
        <Button onClick={() => load(days)} loading={loading}>
          刷新
        </Button>
      </div>

      {usage && (
        <>
          <div
            style={{
              display: 'flex',
              gap: 24,
              flexWrap: 'wrap',
              marginBottom: 24,
              padding: 16,
              background: 'var(--ant-color-bg-container)',
              borderRadius: 8,
              border: '1px solid var(--ant-color-border)',
            }}
          >
            <Statistic title="调用次数" value={usage.totalCalls} />
            <Statistic title="Token 总量" value={usage.totalTokens} />
            <Statistic
              title="成功率"
              value={(usage.successRate * 100).toFixed(1)}
              suffix="%"
            />
            <Statistic title="提示 Token" value={usage.promptTokens} />
            <Statistic title="补全 Token" value={usage.completionTokens} />
          </div>

          <div style={{ marginBottom: 24 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>按功能分布</div>
            <Table<DataPlatform.LlmUsageByFeature>
              size="small"
              rowKey="feature"
              dataSource={usage.byFeature}
              columns={byFeatureCols}
              pagination={false}
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>
              按任务分布(算子调用,Token 用量 Top 20)
            </div>
            <Table<DataPlatform.LlmUsageByJob>
              size="small"
              rowKey="jobId"
              dataSource={usage.byJob}
              columns={byJobCols}
              pagination={false}
              locale={{ emptyText: '暂无任务级调用(算子 LLM 调用完成后出现)' }}
            />
          </div>

          <div>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>近期调用记录</div>
            <Table<DataPlatform.LlmUsageRecent>
              size="small"
              rowKey={(r) => `${r.createdAt}-${r.feature}`}
              dataSource={usage.recent}
              columns={recentCols}
              pagination={{ pageSize: 10, showSizeChanger: false }}
            />
          </div>
        </>
      )}
    </div>
  );
};

// ─── 卡片内嵌模型管理面板（Dify「显示模型」展开区） ───────────────────────────

const ModelsPanel: React.FC<{
  provider: DataPlatform.LlmProvider;
  /** 当前模型变更后通知主列表刷新 */
  onProviderChanged: () => void;
}> = ({ provider, onProviderChanged }) => {
  const [models, setModels] = useState<DataPlatform.LlmModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [currentModel, setCurrentModel] = useState('');
  const [newModel, setNewModel] = useState('');

  const loadModels = useCallback(async (pid: string) => {
    setLoading(true);
    try {
      const res = await listProviderModels(pid);
      if (res.success) setModels(res.data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setCurrentModel(provider.model);
    setNewModel('');
    loadModels(provider.id);
  }, [provider, loadModels]);

  const presetOptions = PRESET_MODELS[provider.provider].map((m) => ({
    value: m,
  }));

  const handleFetch = async () => {
    if (!provider) return;
    setFetching(true);
    try {
      const res = await fetchProviderModels(provider.id);
      setModels(res.data.models);
      if (res.data.success) {
        message.success(res.data.message);
      } else {
        message.warning(
          `拉取失败:${res.data.message}。可用下方预置候选手动添加`,
        );
      }
    } catch (e: any) {
      message.error(e?.message ?? '拉取失败');
    } finally {
      setFetching(false);
    }
  };

  const handleAdd = async () => {
    if (!provider) return;
    const m = newModel.trim();
    if (!m) return;
    try {
      const res = await addProviderModel(provider.id, m);
      if (res.success) {
        setModels(res.data);
        setNewModel('');
        message.success('已添加');
      }
    } catch (e: any) {
      message.error(e?.message ?? '添加失败');
    }
  };

  const handleSelect = async (model: string) => {
    if (!provider) return;
    try {
      await selectProviderModel(provider.id, model);
      setCurrentModel(model);
      message.success(`已设为当前模型:${model}`);
      onProviderChanged();
    } catch (e: any) {
      message.error(e?.message ?? '设置失败');
    }
  };

  const handleDeleteModel = async (modelId: string) => {
    if (!provider) return;
    try {
      await deleteProviderModel(provider.id, modelId);
      setModels((prev) => prev.filter((x) => x.id !== modelId));
      message.success('已删除');
    } catch (e: any) {
      message.error(e?.message ?? '删除失败');
    }
  };

  const cols: ColumnsType<DataPlatform.LlmModel> = [
    {
      title: '模型',
      dataIndex: 'model',
      render: (_, r) => (
        <Space>
          <span>{r.model}</span>
          {r.model === currentModel && <Tag color="green">当前</Tag>}
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      render: (_, r) => (
        <Tag color={SOURCE_META[r.source].color}>
          {SOURCE_META[r.source].text}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_, r) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            disabled={r.model === currentModel}
            onClick={() => handleSelect(r.model)}
          >
            设为当前
          </Button>
          <Popconfirm
            title="删除该模型?"
            onConfirm={() => handleDeleteModel(r.id)}
            okText="删除"
            cancelText="取消"
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div
      style={{
        padding: '12px 20px 16px',
        borderTop: '1px solid var(--ant-color-border-secondary)',
      }}
    >
      <Space style={{ marginBottom: 12 }} wrap>
        <Button
          size="small"
          type="primary"
          loading={fetching}
          onClick={handleFetch}
        >
          获取模型
        </Button>
        <AutoComplete
          value={newModel}
          options={presetOptions}
          size="small"
          style={{ width: 260 }}
          placeholder="手动输入或选择模型名"
          onChange={(v) => setNewModel(v)}
        />
        <Button size="small" onClick={handleAdd} disabled={!newModel.trim()}>
          添加
        </Button>
        <span style={{ color: 'var(--ant-color-text-tertiary)', fontSize: 12 }}>
          「获取模型」调用供应商 /models 接口在线拉取;不支持的供应商(如
          GLM)可手动添加
        </span>
      </Space>
      <Table<DataPlatform.LlmModel>
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={models}
        columns={cols}
        pagination={false}
      />
    </div>
  );
};

// ─── 系统模型设置弹窗（按能力位选默认模型，Dify 风格） ─────────────────────────

/** capability 值编码为 "providerId|||model"（model 名可含任意字符,分隔符取不常见序列） */
const SEP = '|||';

const SystemModelsModal: React.FC<{
  open: boolean;
  providers: DataPlatform.LlmProvider[];
  onClose: () => void;
  /** 保存成功后由父级刷新列表并关闭 */
  onSaved: () => void;
}> = ({ open, providers, onClose, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string | undefined>>({});
  const [providerModels, setProviderModels] = useState<
    Record<string, string[]>
  >({});

  useEffect(() => {
    if (!open) return;
    (async () => {
      setLoading(true);
      try {
        const sysRes = await listLlmSystemModels();
        const modelRes = await Promise.all(
          providers.map((p) => listProviderModels(p.id).catch(() => null)),
        );
        const pm: Record<string, string[]> = {};
        providers.forEach((p, i) => {
          const res = modelRes[i];
          const list = res?.success ? res.data.map((m) => m.model) : [];
          // 当前生效模型可能不在清单里,始终可选
          if (p.model && !list.includes(p.model)) list.unshift(p.model);
          pm[p.id] = list;
        });
        const next: Record<string, string | undefined> = {};
        if (sysRes.success) {
          for (const it of sysRes.data) {
            if (it.providerId && it.model) {
              next[it.capability] = `${it.providerId}${SEP}${it.model}`;
              // 已保存的选择也可能不在清单里,补进选项避免显示原始编码值
              const list = pm[it.providerId];
              if (list && !list.includes(it.model)) list.unshift(it.model);
            } else {
              next[it.capability] = undefined;
            }
          }
        }
        setProviderModels(pm);
        setValues(next);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, providers]);

  const groupedOptions = providers
    .map((p) => ({
      label: `${p.name}（${PROVIDER_LABELS[p.provider]}）`,
      options: (providerModels[p.id] ?? []).map((m) => ({
        label: m,
        value: `${p.id}${SEP}${m}`,
      })),
    }))
    .filter((g) => g.options.length > 0);

  const handleSave = async () => {
    setSaving(true);
    try {
      const items = CAPABILITY_ORDER.map((cap) => {
        const v = values[cap];
        if (!v) return { capability: cap, providerId: null, model: null };
        const idx = v.indexOf(SEP);
        return {
          capability: cap,
          providerId: v.slice(0, idx),
          model: v.slice(idx + SEP.length),
        };
      }) as DataPlatform.LlmSystemModelItem[];
      const res = await updateLlmSystemModels(items);
      if (res.success) {
        message.success('系统模型设置已保存');
        onSaved();
      }
    } catch (e: any) {
      message.error(e?.message ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="系统模型设置"
      open={open}
      onCancel={onClose}
      onOk={handleSave}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      destroyOnHidden
    >
      <Spin spinning={loading}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            padding: '8px 0',
          }}
        >
          {CAPABILITY_ORDER.map((cap) => (
            <div key={cap}>
              <div style={{ marginBottom: 6, fontWeight: 500 }}>
                {CAPABILITY_LABELS[cap]}
                {cap === 'chat' && (
                  <span
                    style={{
                      marginLeft: 8,
                      fontWeight: 400,
                      fontSize: 12,
                      color: 'var(--ant-color-text-tertiary)',
                    }}
                  >
                    即激活供应商,供 AI 助手 / needs_api 算子使用
                  </span>
                )}
              </div>
              <Select
                style={{ width: '100%' }}
                showSearch
                allowClear
                placeholder="选择模型"
                value={values[cap]}
                options={groupedOptions}
                optionFilterProp="label"
                onChange={(v) => setValues((prev) => ({ ...prev, [cap]: v }))}
              />
            </div>
          ))}
          <div
            style={{ fontSize: 12, color: 'var(--ant-color-text-tertiary)' }}
          >
            Embedding / Rerank / 语音能力位当前仅保存配置,供后续 RAG
            等场景使用;候选来自各供应商「显示模型」里的清单。
          </div>
        </div>
      </Spin>
    </Modal>
  );
};

// ─── 主页面 ───────────────────────────────────────────────────────────────────

const LlmSettings: React.FC = () => {
  const [providers, setProviders] = useState<DataPlatform.LlmProvider[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<EditingProvider>(null);
  const [dialogTesting, setDialogTesting] = useState(false);
  const [dialogTestResult, setDialogTestResult] =
    useState<DataPlatform.LlmTestResult | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  // Dify 风格卡片列表：展开的供应商 / 搜索关键字 / 系统模型设置
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('');
  const [systemModels, setSystemModels] = useState<
    DataPlatform.LlmSystemModelItem[]
  >([]);
  const [systemOpen, setSystemOpen] = useState(false);
  const [form] = Form.useForm();
  const access = useAccess();
  const canAdd = access.hasPerm('ops:llm:add');
  const canEdit = access.hasPerm('ops:llm:edit');
  const canRemove = access.hasPerm('ops:llm:remove');
  const canTest = access.hasPerm('ops:llm:test');
  const canActivate = access.hasPerm('ops:llm:activate');
  const canManageModel = access.hasPerm('ops:llm:manage-model');

  const loadProviders = useCallback(async () => {
    setListLoading(true);
    try {
      const res = await listLlmProviders();
      if (res.success) setProviders(res.data);
    } finally {
      setListLoading(false);
    }
  }, []);

  const loadSystemModels = useCallback(async () => {
    try {
      const res = await listLlmSystemModels();
      if (res.success) setSystemModels(res.data);
    } catch {
      // 忽略:仅影响卡片上的能力位标签展示
    }
  }, []);

  const loadAll = useCallback(() => {
    loadProviders();
    loadSystemModels();
  }, [loadProviders, loadSystemModels]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setDialogTestResult(null);
    setModalOpen(true);
  };

  const openEdit = async (record: DataPlatform.LlmProvider) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      provider: record.provider,
      baseUrl: record.baseUrl,
      apiKey: '',
    });
    setDialogTestResult(null);
    setModalOpen(true);
    // 回填真实 Key（管理员专用 reveal 接口）；失败则保持留空 = 不修改
    try {
      const res = await revealLlmProviderKey(record.id);
      if (res.success) form.setFieldsValue({ apiKey: res.data.apiKey });
    } catch {
      // 忽略：保持空，提交时按"留空 = 不修改"处理
    }
  };

  const handleProviderChange = (val: DataPlatform.LlmProvider['provider']) => {
    form.setFieldsValue({ baseUrl: PRESET_BASE_URLS[val] });
  };

  const handleSubmit = async (values: DataPlatform.LlmProviderCreate) => {
    try {
      if (editing?.id) {
        const body: DataPlatform.LlmProviderUpdate = {
          name: values.name,
          provider: values.provider,
          baseUrl: values.baseUrl,
          model: values.model,
        };
        if (values.apiKey) body.apiKey = values.apiKey;
        await updateLlmProvider(editing.id, body);
        message.success('更新成功');
      } else {
        await createLlmProvider(values);
        message.success('创建成功');
      }
      setModalOpen(false);
      loadAll();
    } catch (e: any) {
      message.error(e?.message ?? '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteLlmProvider(id);
      message.success('已删除');
      if (expandedId === id) setExpandedId(null);
      loadAll();
    } catch {
      message.error('删除失败');
    }
  };

  /** 对话框内测试：用当前表单值校验连通性（保存前）。
   * 编辑态且 API Key 留空时改用已保存密钥测已存供应商。 */
  const handleDialogTest = async () => {
    const fields = editing?.id ? ['baseUrl'] : ['baseUrl', 'apiKey'];
    try {
      await form.validateFields(fields);
    } catch {
      return; // 必填项未填，表单已就地提示
    }
    const baseUrl = (form.getFieldValue('baseUrl') as string).trim();
    // 无模型字段:编辑态沿用已保存的当前模型做 chat 探测,新建为空走 /models 探测
    const model = editing?.model ?? '';
    const apiKey = ((form.getFieldValue('apiKey') as string) ?? '').trim();
    setDialogTesting(true);
    setDialogTestResult(null);
    try {
      const res =
        editing?.id && !apiKey
          ? await testLlmProvider(editing.id)
          : await testLlmProviderConfig({ baseUrl, model, apiKey });
      setDialogTestResult(res.data);
    } catch (e: any) {
      setDialogTestResult({
        success: false,
        latencyMs: 0,
        message: e?.message ?? '测试失败',
        model,
      });
    } finally {
      setDialogTesting(false);
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    try {
      const res = await testLlmProvider(id);
      if (res.data.success) {
        message.success(
          `连接成功：${res.data.message}（${res.data.latencyMs} ms）`,
        );
      } else {
        message.error(`连接失败：${res.data.message}`);
      }
    } catch (e: any) {
      message.error(e?.message ?? '测试失败');
    } finally {
      setTestingId(null);
    }
  };

  // 各供应商被哪些能力位引用（卡片能力 tag）；chat 位供应商即「系统推理模型」
  const providerCaps: Record<string, string[]> = {};
  for (const it of systemModels) {
    if (it.providerId) {
      providerCaps[it.providerId] = [
        ...(providerCaps[it.providerId] ?? []),
        it.capability,
      ];
    }
  }
  const chatItem = systemModels.find((i) => i.capability === 'chat');

  const kw = keyword.trim().toLowerCase();
  const filtered = providers.filter(
    (p) =>
      !kw ||
      p.name.toLowerCase().includes(kw) ||
      PROVIDER_LABELS[p.provider].toLowerCase().includes(kw) ||
      p.baseUrl.toLowerCase().includes(kw) ||
      p.model.toLowerCase().includes(kw),
  );

  return (
    <PageContainer>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="仅管理员可见。「系统模型设置」中的系统推理模型将被 AI 助手 / needs_api 算子使用。"
      />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 16,
        }}
      >
        <span style={{ fontSize: 16, fontWeight: 600 }}>模型列表</span>
        <span style={{ color: 'var(--ant-color-text-tertiary)' }}>
          {providers.length} 个供应商
        </span>
        <div style={{ flex: 1 }} />
        <Input.Search
          allowClear
          placeholder="搜索供应商 / 模型"
          style={{ width: 240 }}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={setKeyword}
        />
        {canActivate && (
          <Button
            icon={<SettingOutlined />}
            onClick={() => setSystemOpen(true)}
          >
            系统模型设置
          </Button>
        )}
        {canAdd && (
          <Button type="primary" onClick={openCreate}>
            新建供应商
          </Button>
        )}
      </div>

      <Spin spinning={listLoading}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            marginBottom: 8,
          }}
        >
          {filtered.map((p) => {
            const caps = providerCaps[p.id] ?? [];
            const expanded = expandedId === p.id;
            return (
              <div
                key={p.id}
                style={{
                  border: '1px solid var(--ant-color-border)',
                  borderRadius: 12,
                  background: 'var(--ant-color-bg-container)',
                }}
              >
                <div style={{ padding: '16px 20px' }}>
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 12 }}
                  >
                    <Avatar
                      shape="square"
                      size={40}
                      style={{
                        background: AVATAR_BG[p.provider],
                        fontWeight: 600,
                        borderRadius: 10,
                        flexShrink: 0,
                      }}
                    >
                      {PROVIDER_LABELS[p.provider][0]}
                    </Avatar>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Space size={8} wrap>
                        <span style={{ fontWeight: 600, fontSize: 15 }}>
                          {p.name}
                        </span>
                        <Tag color={PROVIDER_COLORS[p.provider]}>
                          {PROVIDER_LABELS[p.provider]}
                        </Tag>
                        {chatItem?.providerId === p.id && (
                          <Tag color="success">系统推理模型</Tag>
                        )}
                        {caps.map((c) => (
                          <Tag
                            key={c}
                            style={{
                              color: 'var(--ant-color-text-secondary)',
                              fontSize: 11,
                            }}
                          >
                            {CAPABILITY_TAGS[c]}
                          </Tag>
                        ))}
                      </Space>
                      <div
                        style={{
                          marginTop: 4,
                          color: 'var(--ant-color-text-tertiary)',
                          fontSize: 12,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {p.baseUrl} · API Key {p.apiKeyMasked} · 当前模型{' '}
                        {p.model || '(未设置)'} · 更新于{' '}
                        {formatDateTime(p.updatedAt)}
                      </div>
                    </div>
                    <Space size="small">
                      {canTest && (
                        <Button
                          size="small"
                          loading={testingId === p.id}
                          onClick={() => handleTest(p.id)}
                        >
                          测试
                        </Button>
                      )}
                      {canEdit && (
                        <Button size="small" onClick={() => openEdit(p)}>
                          设置
                        </Button>
                      )}
                      {canRemove && (
                        <Popconfirm
                          title="确定删除该供应商配置？"
                          onConfirm={() => handleDelete(p.id)}
                          okText="删除"
                          cancelText="取消"
                        >
                          <Button size="small" danger>
                            删除
                          </Button>
                        </Popconfirm>
                      )}
                    </Space>
                  </div>
                  {canManageModel && (
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, marginTop: 8 }}
                      onClick={() => setExpandedId(expanded ? null : p.id)}
                    >
                      {expanded ? <DownOutlined /> : <RightOutlined />}
                      显示模型
                    </Button>
                  )}
                </div>
                {canManageModel && expanded && (
                  <ModelsPanel provider={p} onProviderChanged={loadAll} />
                )}
              </div>
            );
          })}
          {!filtered.length && !listLoading && (
            <Empty
              description={
                kw ? '没有匹配的供应商' : '暂无供应商,点右上「新建供应商」接入'
              }
            />
          )}
        </div>
      </Spin>

      <div style={{ marginTop: 24 }}>
        <div
          style={{
            fontSize: 16,
            fontWeight: 600,
            marginBottom: 16,
            padding: '12px 0',
            borderBottom: '1px solid var(--ant-color-border)',
          }}
        >
          用量监控
        </div>
        <UsagePanel />
      </div>

      {/* 新建 / 编辑 Modal */}
      <ModalForm
        title={editing ? '编辑供应商' : '新建供应商'}
        open={modalOpen}
        form={form}
        onOpenChange={(open) => {
          if (!open) setModalOpen(false);
        }}
        onFinish={handleSubmit}
        onValuesChange={() => {
          if (dialogTestResult) setDialogTestResult(null);
        }}
        modalProps={{ destroyOnHidden: true }}
      >
        <ProFormText
          name="name"
          label="名称"
          placeholder="请输入供应商名称"
          rules={[{ required: true, message: '请输入名称' }]}
        />
        <ProFormSelect<DataPlatform.LlmProvider['provider']>
          name="provider"
          label="供应商类型"
          rules={[{ required: true, message: '请选择供应商类型' }]}
          options={[
            { label: 'DeepSeek', value: 'deepseek' },
            { label: 'GLM', value: 'glm' },
            { label: 'MiniMax', value: 'minimax' },
            { label: 'OpenAI', value: 'openai' },
            { label: 'SiliconFlow', value: 'siliconflow' },
            { label: '自定义', value: 'custom' },
          ]}
          fieldProps={{ onChange: handleProviderChange }}
        />
        <ProFormText
          name="baseUrl"
          label="Base URL"
          placeholder="https://api.example.com/v1"
          rules={[{ required: true, message: '请输入 Base URL' }]}
        />
        <Form.Item
          name="apiKey"
          label="API Key"
          rules={editing ? [] : [{ required: true, message: '请输入 API Key' }]}
        >
          <Input.Password
            placeholder={editing ? '留空则不修改' : '请输入 API Key'}
            autoComplete="new-password"
          />
        </Form.Item>
        <Form.Item>
          <Button loading={dialogTesting} onClick={handleDialogTest}>
            测试连接
          </Button>
          {editing && (
            <span
              style={{
                color: 'var(--ant-color-text-secondary)',
                fontSize: 12,
                marginLeft: 12,
              }}
            >
              API Key 留空将用已保存密钥测试
            </span>
          )}
          {dialogTestResult && (
            <Alert
              style={{ marginTop: 12 }}
              type={dialogTestResult.success ? 'success' : 'error'}
              showIcon
              message={
                dialogTestResult.success
                  ? `${dialogTestResult.message} · ${dialogTestResult.latencyMs} ms`
                  : `连接失败：${dialogTestResult.message}`
              }
            />
          )}
        </Form.Item>
        <div
          style={{
            color: 'var(--ant-color-text-secondary)',
            fontSize: 12,
            marginTop: -8,
          }}
        >
          在「系统模型设置」选为系统推理模型后,将被 AI 助手 / needs_api 算子使用
        </div>
      </ModalForm>

      <SystemModelsModal
        open={systemOpen}
        providers={providers}
        onClose={() => setSystemOpen(false)}
        onSaved={() => {
          setSystemOpen(false);
          loadAll();
        }}
      />
    </PageContainer>
  );
};

export default LlmSettings;
