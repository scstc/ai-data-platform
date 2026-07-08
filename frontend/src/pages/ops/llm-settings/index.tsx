import type { ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import {
  Alert,
  AutoComplete,
  Badge,
  Button,
  Drawer,
  Form,
  Input,
  message,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  activateLlmProvider,
  addProviderModel,
  createLlmProvider,
  deleteLlmProvider,
  deleteProviderModel,
  fetchProviderModels,
  getLlmUsage,
  listLlmProviders,
  listProviderModels,
  revealLlmProviderKey,
  selectProviderModel,
  testLlmProvider,
  testLlmProviderConfig,
  updateLlmProvider,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

/** 供应商类型 → 默认 baseUrl + model */
const PRESETS: Record<
  DataPlatform.LlmProvider['provider'],
  { baseUrl: string; model: string }
> = {
  deepseek: { baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat' },
  glm: {
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
  },
  minimax: {
    baseUrl: 'https://api.minimaxi.com/v1',
    model: 'MiniMax-Text-01',
  },
  openai: { baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  custom: { baseUrl: '', model: '' },
};

const PROVIDER_COLORS: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: 'blue',
  glm: 'purple',
  minimax: 'cyan',
  openai: 'green',
  custom: 'default',
};

const PROVIDER_LABELS: Record<DataPlatform.LlmProvider['provider'], string> = {
  deepseek: 'DeepSeek',
  glm: 'GLM',
  minimax: 'MiniMax',
  openai: 'OpenAI',
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
  custom: [],
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

// ─── 模型管理抽屉（主从式详情） ───────────────────────────────────────────────

const ModelsDrawer: React.FC<{
  provider: DataPlatform.LlmProvider | null;
  open: boolean;
  onClose: () => void;
  /** 当前模型变更后通知主表刷新 */
  onProviderChanged: () => void;
}> = ({ provider, open, onClose, onProviderChanged }) => {
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
    if (open && provider) {
      setCurrentModel(provider.model);
      setNewModel('');
      loadModels(provider.id);
    }
  }, [open, provider, loadModels]);

  const presetOptions = provider
    ? PRESET_MODELS[provider.provider].map((m) => ({ value: m }))
    : [];

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
    <Drawer
      title={provider ? `管理模型 · ${provider.name}` : '管理模型'}
      size="large"
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {provider && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={`当前生效模型:${currentModel || '(未设置)'}`}
            description="「获取模型」调用供应商接口拉取清单;部分供应商(如 GLM)无该接口,可用下方预置候选手动添加。"
          />
          <Space style={{ marginBottom: 16 }} wrap>
            <Button type="primary" loading={fetching} onClick={handleFetch}>
              获取模型
            </Button>
            <AutoComplete
              value={newModel}
              options={presetOptions}
              style={{ width: 240 }}
              placeholder="手动输入或选择模型名"
              onChange={(v) => setNewModel(v)}
            />
            <Button onClick={handleAdd} disabled={!newModel.trim()}>
              添加
            </Button>
          </Space>
          <Table<DataPlatform.LlmModel>
            size="small"
            rowKey="id"
            loading={loading}
            dataSource={models}
            columns={cols}
            pagination={false}
          />
        </>
      )}
    </Drawer>
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
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [managing, setManaging] = useState<DataPlatform.LlmProvider | null>(
    null,
  );
  const [form] = Form.useForm();
  const watchedProvider = Form.useWatch('provider', form) as
    | DataPlatform.LlmProvider['provider']
    | undefined;
  const actionRef = useRef(null);
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

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

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
      model: record.model,
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
    const preset = PRESETS[val];
    form.setFieldsValue({ baseUrl: preset.baseUrl, model: preset.model });
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
      loadProviders();
    } catch (e: any) {
      message.error(e?.message ?? '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteLlmProvider(id);
      message.success('已删除');
      loadProviders();
    } catch {
      message.error('删除失败');
    }
  };

  const handleActivate = async (id: string) => {
    setActivatingId(id);
    try {
      await activateLlmProvider(id);
      message.success('已激活');
      loadProviders();
    } catch (e: any) {
      message.error(e?.message ?? '激活失败');
    } finally {
      setActivatingId(null);
    }
  };

  const openManage = (record: DataPlatform.LlmProvider) => {
    setManaging(record);
    setDrawerOpen(true);
  };

  /** 对话框内测试：用当前表单值校验连通性（保存前）。
   * 编辑态且 API Key 留空时改用已保存密钥测已存供应商。 */
  const handleDialogTest = async () => {
    const fields = editing?.id
      ? ['baseUrl', 'model']
      : ['baseUrl', 'model', 'apiKey'];
    try {
      await form.validateFields(fields);
    } catch {
      return; // 必填项未填，表单已就地提示
    }
    const baseUrl = (form.getFieldValue('baseUrl') as string).trim();
    const model = (form.getFieldValue('model') as string).trim();
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

  const columns: ProColumns<DataPlatform.LlmProvider>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
    },
    {
      title: '供应商',
      dataIndex: 'provider',
      width: 110,
      render: (_, r) => (
        <Tag color={PROVIDER_COLORS[r.provider]}>
          {PROVIDER_LABELS[r.provider]}
        </Tag>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'baseUrl',
      ellipsis: true,
    },
    {
      title: '模型',
      dataIndex: 'model',
      ellipsis: true,
    },
    {
      title: 'API Key',
      dataIndex: 'apiKeyMasked',
      width: 160,
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'isActive',
      width: 100,
      render: (_, r) =>
        r.isActive ? (
          <Badge status="success" text="已激活" />
        ) : (
          <Badge status="default" text="未激活" />
        ),
    },
    {
      title: '更新时间',
      dataIndex: 'updatedAt',
      width: 180,
      render: (_, r) => formatDateTime(r.updatedAt),
    },
    {
      title: '操作',
      key: 'actions',
      width: 360,
      render: (_, r) => (
        <Space size="small">
          {canTest && (
            <Button
              type="link"
              size="small"
              loading={testingId === r.id}
              onClick={() => handleTest(r.id)}
            >
              测试
            </Button>
          )}
          {canManageModel && (
            <Button type="link" size="small" onClick={() => openManage(r)}>
              管理模型
            </Button>
          )}
          {canActivate && (
            <Button
              type="link"
              size="small"
              disabled={r.isActive}
              loading={activatingId === r.id}
              onClick={() => handleActivate(r.id)}
            >
              激活
            </Button>
          )}
          {canEdit && (
            <Button type="link" size="small" onClick={() => openEdit(r)}>
              编辑
            </Button>
          )}
          {canRemove && (
            <Popconfirm
              title="确定删除该供应商配置？"
              onConfirm={() => handleDelete(r.id)}
              okText="删除"
              cancelText="取消"
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <PageContainer>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="仅管理员可见。已激活的配置将被 AI 助手 / needs_api 算子使用。"
      />

      <ProTable<DataPlatform.LlmProvider>
        actionRef={actionRef}
        headerTitle="LLM 供应商"
        rowKey="id"
        search={false}
        loading={listLoading}
        dataSource={providers}
        columns={columns}
        options={{ reload: () => loadProviders() }}
        toolBarRender={() =>
          canAdd
            ? [
                <Button key="create" type="primary" onClick={openCreate}>
                  新建供应商
                </Button>,
              ]
            : []
        }
      />

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
          name="model"
          label="模型"
          rules={[{ required: true, message: '请输入模型名称' }]}
        >
          <AutoComplete
            options={(watchedProvider
              ? PRESET_MODELS[watchedProvider]
              : []
            ).map((m) => ({ value: m }))}
            placeholder="选择或输入模型名;保存后可在「管理模型」拉取完整列表"
          />
        </Form.Item>
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
                  ? `连接成功 · ${dialogTestResult.model} · ${dialogTestResult.latencyMs} ms`
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
          已激活的配置将被 AI 助手 / needs_api 算子使用
        </div>
      </ModalForm>

      <ModelsDrawer
        provider={managing}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onProviderChanged={loadProviders}
      />
    </PageContainer>
  );
};

export default LlmSettings;
