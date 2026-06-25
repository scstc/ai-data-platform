import {
  Drawer,
  Empty,
  List,
  message,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addAcl,
  deleteAcl,
  listAcl,
  searchAclCandidates,
  updateAcl,
} from '@/services/data-platform';

/** 三个授权级别(火山「数据集权限」语义):只读 / 可编辑 / 管理 */
const LEVELS: { value: DataPlatform.AclLevel; label: string }[] = [
  { value: 'view', label: '只读权限' },
  { value: 'edit', label: '可编辑权限' },
  { value: 'admin', label: '管理权限' },
];

const LEVEL_LABEL: Record<DataPlatform.AclLevel, string> = {
  view: '只读',
  edit: '可编辑',
  admin: '管理',
};

const LEVEL_COLOR: Record<DataPlatform.AclLevel, string> = {
  view: 'default',
  edit: 'blue',
  admin: 'gold',
};

/** 主体方式:对每个级别独立切换「指定用户/角色」或「组织内所有人」 */
type SubjectMode = 'specify' | 'all';

type Props = {
  open: boolean;
  onClose: () => void;
  datasetId: string;
  /** 详情页的归属字段,只读展示为 Owner */
  owner: string;
};

/** 数据集权限抽屉:顶部只读 Owner + 三级授权(每级可授给指定用户/角色或组织内所有人)。
 *  已授条目支持改级别 / 删除;owner 天然管理、不在 ACL 列表里,无需移除。 */
const AclDrawer: React.FC<Props> = ({ open, onClose, datasetId, owner }) => {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<DataPlatform.DatasetAcl[]>([]);
  // subjectId -> 显示名缓存:add 时由 candidate 写入,解决 list 只回 subjectId 的显示问题
  const [nameCache, setNameCache] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAcl(datasetId);
      if (res?.success) setRows(res.data);
    } catch {
      message.error('加载授权列表失败');
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const refresh = useCallback(async () => {
    const res = await listAcl(datasetId);
    if (res?.success) setRows(res.data);
  }, [datasetId]);

  // 该级别下是否已存在「组织内所有人」条目(用于 Switch 受控 + 找行删除)
  const allRowOf = useCallback(
    (level: DataPlatform.AclLevel) =>
      rows.find((r) => r.subjectType === 'all' && r.level === level),
    [rows],
  );

  const handleAddSpecify = useCallback(
    async (
      level: DataPlatform.AclLevel,
      candidate: DataPlatform.AclCandidate,
    ) => {
      try {
        await addAcl(datasetId, {
          subjectType: candidate.type,
          subjectId: candidate.id,
          level,
        });
        setNameCache((m) => ({ ...m, [candidate.id]: candidate.name }));
        message.success(`已授予 ${candidate.name} ${LEVEL_LABEL[level]}权限`);
        await refresh();
      } catch {
        message.error('授权失败,请重试');
      }
    },
    [datasetId, refresh],
  );

  const handleToggleAll = useCallback(
    async (level: DataPlatform.AclLevel, checked: boolean) => {
      try {
        if (checked) {
          await addAcl(datasetId, {
            subjectType: 'all',
            subjectId: '*',
            level,
          });
          message.success(`已对组织内所有人开放${LEVEL_LABEL[level]}权限`);
        } else {
          const row = allRowOf(level);
          if (row) await deleteAcl(datasetId, row.id);
          message.success(`已取消组织内所有人的${LEVEL_LABEL[level]}权限`);
        }
        await refresh();
      } catch {
        message.error('操作失败,请重试');
      }
    },
    [datasetId, allRowOf, refresh],
  );

  const handleChangeLevel = useCallback(
    async (row: DataPlatform.DatasetAcl, level: DataPlatform.AclLevel) => {
      if (level === row.level) return;
      try {
        await updateAcl(datasetId, row.id, level);
        message.success('已修改授权级别');
        await refresh();
      } catch {
        message.error('修改失败,请重试');
      }
    },
    [datasetId, refresh],
  );

  const handleDelete = useCallback(
    async (row: DataPlatform.DatasetAcl) => {
      try {
        await deleteAcl(datasetId, row.id);
        message.success('已移除授权');
        await refresh();
      } catch {
        message.error('移除失败,请重试');
      }
    },
    [datasetId, refresh],
  );

  /** 主体显示名:all→固定文案;指定→缓存命中用名,否则降级显示 id(历史条目无缓存时) */
  const subjectName = useCallback(
    (row: DataPlatform.DatasetAcl) =>
      row.subjectType === 'all'
        ? '组织内所有人'
        : (nameCache[row.subjectId] ?? row.subjectId),
    [nameCache],
  );

  return (
    <Drawer
      title="数据集权限"
      width={520}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      <Spin spinning={loading}>
        <Typography.Text type="secondary">归属(Owner)</Typography.Text>
        <div style={{ marginTop: 4, marginBottom: 20 }}>
          <Tag color="purple">{owner}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            天然拥有管理权限,不可移除
          </Typography.Text>
        </div>

        {LEVELS.map((lv) => (
          <LevelSection
            key={lv.value}
            datasetId={datasetId}
            title={lv.label}
            allChecked={!!allRowOf(lv.value)}
            onAddSpecify={(c) => handleAddSpecify(lv.value, c)}
            onToggleAll={(checked) => handleToggleAll(lv.value, checked)}
          />
        ))}

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          已授权({rows.length})
        </Typography.Title>
        {rows.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无授权,仅归属者可访问"
          />
        ) : (
          <List<DataPlatform.DatasetAcl>
            size="small"
            dataSource={rows}
            rowKey="id"
            renderItem={(row) => (
              <List.Item
                actions={[
                  <Select<DataPlatform.AclLevel>
                    key="level"
                    size="small"
                    value={row.level}
                    style={{ width: 96 }}
                    options={LEVELS.map((l) => ({
                      label: LEVEL_LABEL[l.value],
                      value: l.value,
                    }))}
                    onChange={(v) => handleChangeLevel(row, v)}
                  />,
                  <Popconfirm
                    key="del"
                    title="移除该授权?"
                    onConfirm={() => handleDelete(row)}
                  >
                    <a>移除</a>
                  </Popconfirm>,
                ]}
              >
                <Space>
                  <Tag color={LEVEL_COLOR[row.level]}>
                    {LEVEL_LABEL[row.level]}
                  </Tag>
                  <span>{subjectName(row)}</span>
                  {row.subjectType !== 'all' && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {row.subjectType === 'user' ? '用户' : '角色'}
                    </Typography.Text>
                  )}
                </Space>
              </List.Item>
            )}
          />
        )}
      </Spin>
    </Drawer>
  );
};

/** 单个授权级别的授权区:主体方式切换 + 防抖远程搜索 Select(用户/角色) 或 组织内所有人 Switch */
const LevelSection: React.FC<{
  datasetId: string;
  title: string;
  allChecked: boolean;
  onAddSpecify: (c: DataPlatform.AclCandidate) => void;
  onToggleAll: (checked: boolean) => void;
}> = ({ datasetId, title, allChecked, onAddSpecify, onToggleAll }) => {
  const [mode, setMode] = useState<SubjectMode>('specify');
  const [type, setType] = useState<'user' | 'role'>('user');
  const [fetching, setFetching] = useState(false);
  const [options, setOptions] = useState<DataPlatform.AclCandidate[]>([]);
  // 防抖请求竞态保护:只接受最新一次请求结果
  const fetchRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const doSearch = useCallback(
    (q: string) => {
      const seq = ++fetchRef.current;
      setOptions([]);
      setFetching(true);
      searchAclCandidates(datasetId, { q, type })
        .then((res) => {
          if (seq !== fetchRef.current) return; // 过期结果丢弃
          setOptions(res?.data ?? []);
        })
        .catch(() => {
          if (seq === fetchRef.current) setOptions([]);
        })
        .finally(() => {
          if (seq === fetchRef.current) setFetching(false);
        });
    },
    [datasetId, type],
  );

  const onSearch = useCallback(
    (q: string) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => doSearch(q), 400);
    },
    [doSearch],
  );

  // 切换 user/role 时清空旧候选,避免串档
  useEffect(() => {
    setOptions([]);
  }, [type]);

  const selectOptions = useMemo(
    () =>
      options.map((c) => ({
        label: c.name,
        value: c.id,
        candidate: c,
      })),
    [options],
  );

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ marginBottom: 8 }}>
        <Typography.Text strong>{title}</Typography.Text>
      </div>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Segmented<SubjectMode>
          size="small"
          value={mode}
          onChange={setMode}
          options={[
            { label: '指定用户/角色', value: 'specify' },
            { label: '组织内所有人', value: 'all' },
          ]}
        />
        {mode === 'specify' ? (
          <Space.Compact style={{ width: '100%' }}>
            <Select<'user' | 'role'>
              value={type}
              style={{ width: 88 }}
              onChange={setType}
              options={[
                { label: '用户', value: 'user' },
                { label: '角色', value: 'role' },
              ]}
            />
            <Select
              showSearch
              // value 不受控:选中即触发授权后立即清空,作为一次性「添加」入口
              value={null}
              placeholder={`搜索${type === 'user' ? '用户' : '角色'}名称`}
              style={{ width: '100%' }}
              filterOption={false}
              loading={fetching}
              onSearch={onSearch}
              notFoundContent={fetching ? <Spin size="small" /> : null}
              options={selectOptions}
              onChange={(_, opt) => {
                const candidate = (
                  opt as { candidate?: DataPlatform.AclCandidate }
                )?.candidate;
                if (candidate) onAddSpecify(candidate);
                setOptions([]);
              }}
            />
          </Space.Compact>
        ) : (
          <Space>
            <Switch checked={allChecked} onChange={onToggleAll} />
            <Typography.Text type="secondary">
              {allChecked
                ? `组织内所有人已具备${title}`
                : `开启后组织内所有人获得${title}`}
            </Typography.Text>
          </Space>
        )}
      </Space>
    </div>
  );
};

export default AclDrawer;
