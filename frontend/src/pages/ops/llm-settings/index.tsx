import type { ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import {
  Alert,
  Badge,
  Button,
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
  createLlmProvider,
  deleteLlmProvider,
  getLlmUsage,
  listLlmProviders,
  testLlmProvider,
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

type EditingProvider = Partial<DataPlatform.LlmProvider> | null;

// ─── 用量监控子组件 ────────────────────────────────────────────────────────────

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
    { title: '功能', dataIndex: 'feature' },
    { title: '调用次数', dataIndex: 'calls', align: 'right' },
    { title: 'Token 总量', dataIndex: 'tokens', align: 'right' },
  ];

  const recentCols: ColumnsType<DataPlatform.LlmUsageRecent> = [
    { title: '功能', dataIndex: 'feature', ellipsis: true },
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

// ─── 主页面 ───────────────────────────────────────────────────────────────────

const LlmSettings: React.FC = () => {
  const [providers, setProviders] = useState<DataPlatform.LlmProvider[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<EditingProvider>(null);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [activatingId, setActivatingId] = useState<number | null>(null);
  const [form] = Form.useForm();
  const actionRef = useRef(null);

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
    setModalOpen(true);
  };

  const openEdit = (record: DataPlatform.LlmProvider) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      provider: record.provider,
      baseUrl: record.baseUrl,
      model: record.model,
      apiKey: '',
    });
    setModalOpen(true);
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

  const handleDelete = async (id: number) => {
    try {
      await deleteLlmProvider(id);
      message.success('已删除');
      loadProviders();
    } catch {
      message.error('删除失败');
    }
  };

  const handleActivate = async (id: number) => {
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

  const handleTest = async (id: number) => {
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
      width: 220,
      render: (_, r) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            loading={testingId === r.id}
            onClick={() => handleTest(r.id)}
          >
            测试
          </Button>
          <Button
            type="link"
            size="small"
            disabled={r.isActive}
            loading={activatingId === r.id}
            onClick={() => handleActivate(r.id)}
          >
            激活
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
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
        toolBarRender={() => [
          <Button key="create" type="primary" onClick={openCreate}>
            新建供应商
          </Button>,
        ]}
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
        <ProFormText
          name="model"
          label="模型"
          placeholder="model-name"
          rules={[{ required: true, message: '请输入模型名称' }]}
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
    </PageContainer>
  );
};

export default LlmSettings;
