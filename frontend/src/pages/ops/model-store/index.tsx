import {
  CheckCircleFilled,
  CloseCircleFilled,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import {
  App,
  Badge,
  Button,
  Card,
  Input,
  Space,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getModelStoreConfig,
  listLocalModels,
  updateModelStoreConfig,
} from '@/services/data-platform';

const { Text } = Typography;

/** 分组展示顺序与中文名(与后端 group 字段对应) */
const GROUP_LABELS: [DataPlatform.LocalModel['group'], string][] = [
  ['llm', '本地大模型'],
  ['vision', '视觉/多模态'],
  ['text', '文本处理'],
  ['asset', '基础资产'],
  ['extra', '其他'],
];

/** 字节数 → 人类可读(GB/MB) */
const fmtSize = (n: number) => {
  if (!n) return '-';
  if (n >= 2 ** 30) return `${(n / 2 ** 30).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(n / 2 ** 20))} MB`;
};

/**
 * 模型仓库:本地模型根路径配置 + 实时扫描清单。
 * 清单不落库(目录即事实源);路径保存后下个任务即生效(engine 按任务注入
 * DATA_JUICER_EXTERNAL_MODELS_HOME 给 dj-process 子进程),无需重启。
 */
const ModelStorePage: React.FC = () => {
  const { message } = App.useApp();
  const access = useAccess();
  const canEdit = access.hasPerm('ops:model:edit');
  const canScan = access.hasPerm('ops:model:scan');

  const [path, setPath] = useState('');
  const [pathExists, setPathExists] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scan, setScan] = useState<DataPlatform.ModelStoreScan>();
  const [loading, setLoading] = useState(false);
  const [group, setGroup] = useState<DataPlatform.LocalModel['group']>('llm');

  // 分组 → 该组模型;Tab 只列非空组,标签带就位/总数
  const grouped = useMemo(() => {
    const map = new Map<string, DataPlatform.LocalModel[]>();
    for (const m of scan?.models ?? []) {
      map.set(m.group, [...(map.get(m.group) ?? []), m]);
    }
    return map;
  }, [scan]);
  const tabs = GROUP_LABELS.filter(([key]) => grouped.get(key)?.length).map(
    ([key, label]) => {
      const items = grouped.get(key) ?? [];
      const ready = items.filter((m) => m.present).length;
      return {
        key,
        label: (
          <Badge
            color={ready === items.length ? 'green' : 'orange'}
            text={`${label} ${ready}/${items.length}`}
          />
        ),
      };
    },
  );
  const activeGroup = grouped.get(group)?.length ? group : tabs[0]?.key;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listLocalModels();
      setScan(res.data);
      setPathExists(res.data.pathExists);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getModelStoreConfig().then((res) => {
      setPath(res.data.path ?? '');
      setPathExists(res.data.pathExists);
    });
    void reload();
  }, [reload]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await updateModelStoreConfig(path.trim());
      setPathExists(res.data.pathExists);
      message.success(
        res.data.path
          ? res.data.pathExists
            ? '已保存,下个任务即生效'
            : '已保存,但后端无法访问该目录'
          : '已清除模型仓库路径',
      );
      void reload();
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const columns: ProColumns<DataPlatform.LocalModel>[] = [
    {
      title: '模型',
      dataIndex: 'id',
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <Text copyable={{ text: r.id }}>{r.id}</Text>
          {r.note && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {r.note}
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (_, r) =>
        r.kind === 'hf' ? <Tag color="blue">HF 模型</Tag> : <Tag>资产文件</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'present',
      width: 100,
      render: (_, r) =>
        r.present ? (
          <Text type="success">
            <CheckCircleFilled /> 就位
          </Text>
        ) : (
          <Text type="secondary">
            <CloseCircleFilled /> 缺失
          </Text>
        ),
    },
    {
      title: '大小',
      dataIndex: 'sizeBytes',
      width: 100,
      render: (_, r) => fmtSize(r.sizeBytes),
    },
    {
      title: '覆盖算子',
      dataIndex: 'usedBy',
      render: (_, r) =>
        r.usedBy.length ? (
          <Tooltip title={r.usedBy.join('、')}>
            <Space size={4} wrap>
              {r.usedBy.slice(0, 3).map((op) => (
                <Tag key={op}>{op}</Tag>
              ))}
              {r.usedBy.length > 3 && <Tag>+{r.usedBy.length - 3}</Tag>}
            </Space>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  return (
    <PageContainer content="配置离线模型根路径;任务执行时自动注入 data-juicer,命中本地模型即不联网。">
      <Card style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%', maxWidth: 720 }}>
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="后端可访问的模型根路径,如 /data/dj-models 或 D:\dj-models"
            disabled={!canEdit}
            status={path && !pathExists ? 'warning' : undefined}
            onPressEnter={canEdit ? save : undefined}
          />
          {canEdit && (
            <Button type="primary" loading={saving} onClick={save}>
              保存
            </Button>
          )}
        </Space.Compact>
        {path && !pathExists && (
          <div style={{ marginTop: 8 }}>
            <Text type="warning">
              后端当前无法访问该目录(容器内请用挂载路径)
            </Text>
          </div>
        )}
      </Card>
      <ProTable<DataPlatform.LocalModel>
        rowKey="id"
        columns={columns}
        dataSource={
          (activeGroup && grouped.get(activeGroup)) || scan?.models || []
        }
        loading={loading}
        search={false}
        pagination={false}
        options={false}
        headerTitle={
          scan
            ? `模型清单(就位 ${scan.presentCount} / ${scan.totalCount})`
            : '模型清单'
        }
        tableExtraRender={() => (
          <Tabs
            activeKey={activeGroup}
            onChange={(k) => setGroup(k as DataPlatform.LocalModel['group'])}
            items={tabs}
            style={{ paddingInline: 24, marginBottom: -16 }}
          />
        )}
        toolBarRender={() =>
          canScan
            ? [
                <Button
                  key="rescan"
                  icon={<ReloadOutlined />}
                  onClick={() => void reload()}
                >
                  重新扫描
                </Button>,
              ]
            : []
        }
      />
    </PageContainer>
  );
};

export default ModelStorePage;
