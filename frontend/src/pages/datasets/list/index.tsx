import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormDependency,
  ProFormSelect,
  ProFormText,
  ProFormTreeSelect,
  ProTable,
} from '@ant-design/pro-components';
import { Access, history, useAccess, useLocation } from '@umijs/max';
import {
  Button,
  Dropdown,
  type MenuProps,
  message,
  Popconfirm,
  Popover,
  Select,
  Tag,
  TreeSelect,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  batchDeleteDatasets,
  deleteDataset,
  hostS3,
  listBuckets,
  listCategories,
  listDataSources,
  listDatasets,
  listObjects,
  listTags,
  unhostDataset,
  updateDataset,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  collectSubtreeIds,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import {
  MODALITY_ENUM,
  MODALITY_SUBTYPE_META,
  ModalitySubtypeTag,
} from '@/utils/modalitySubtype';
import {
  SEMANTIC_TYPE_ENUM,
  SEMANTIC_TYPE_META,
  SemanticTypeTag,
} from '@/utils/semanticType';
import { SOURCE_KIND_ENUM, SourceKindTag } from '@/utils/sourceKind';
import { tagColor } from '@/utils/tags';

/** 数据集类型枚举（列表搜索 + 托管表单复用） */
const DATA_TYPE_ENUM = {
  text: { text: 'text' },
  multimodal: { text: 'multimodal' },
  qa: { text: 'qa' },
  cot: { text: 'cot' },
  preference: { text: 'preference' },
  timeseries: { text: 'timeseries' },
  gis: { text: 'gis' },
};

/** 字节数转人类可读 */
const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

/** 行内快速编辑标签(admin):标签是多值,暂存本地、点「保存」才提交(不逐字 PATCH)。*/
const QuickTagEditor: React.FC<{
  datasetId: string;
  initialTags?: string[];
  options: { label: string; value: string }[];
  onSaved: () => void;
}> = ({ datasetId, initialTags, options, onSaved }) => {
  const [val, setVal] = useState<string[]>(initialTags ?? []);
  const [saving, setSaving] = useState(false);
  const onSave = async () => {
    setSaving(true);
    try {
      await updateDataset(datasetId, { tags: val });
      message.success('已更新标签');
      onSaved();
    } catch {
      message.error('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };
  return (
    <div style={{ width: 260 }}>
      <Select
        mode="tags"
        style={{ width: '100%' }}
        value={val}
        onChange={setVal}
        options={options}
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="输入标签，回车添加（可多选）"
      />
      <div style={{ marginTop: 8, textAlign: 'right' }}>
        <Button size="small" type="primary" loading={saving} onClick={onSave}>
          保存
        </Button>
      </div>
    </div>
  );
};

const DatasetsList: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Dataset[]>([]);
  const [hostOpen, setHostOpen] = useState(false);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  // 行内快速设置分类的 Popover 受控:记录当前展开的行(数据集 id),选完即关。
  const [quickCatId, setQuickCatId] = useState<string | null>(null);
  // 全部标签(标签筛选项的 options 联想)。
  const [tagOptions, setTagOptions] = useState<
    { label: string; value: string }[]
  >([]);
  // 行内快速编辑标签的 Popover 受控:记录当前展开的行(数据集 id)。
  const [quickTagId, setQuickTagId] = useState<string | null>(null);

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryTreeData(toCategoryTreeData(res.data));
    } catch {
      // 静默：分类筛选不可用不应阻断列表
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  useEffect(() => {
    listTags()
      .then((res) =>
        setTagOptions(
          (res.data ?? []).map((t) => ({ label: t.name, value: t.name })),
        ),
      )
      .catch(() => undefined);
  }, []);

  // 兼容旧的 ?highlight=<datasetId> 跳转(来自低质过滤等):改为直接进详情页
  const location = useLocation();
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const highlightId = params.get('highlight');
    if (!highlightId) return;
    history.replace(`/datasets/${highlightId}`);
  }, [location.search]);

  const selectedRowKeys = selectedRows.map((r) => r.id);
  // 外部托管数据集不可删除(后端 403 兜底)——批量删除前先拦截给提示
  const hasHostedSelected = selectedRows.some((r) => r.hosted);

  const handleBatchDelete = async () => {
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteDatasets(selectedRowKeys);
      hide();
      message.success(
        `已删除 ${res?.data?.deleted ?? selectedRowKeys.length} 个数据集`,
      );
      setSelectedRows([]);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('批量删除失败，请重试');
    }
  };

  // 取消托管(仅 admin):只移除平台引用，绝不删 S3 源对象
  const handleUnhost = async (id: string) => {
    const hide = message.loading('正在取消托管…', 0);
    try {
      await unhostDataset(id);
      hide();
      message.success('已取消托管（仅移除平台引用，S3 源对象保留）');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('取消托管失败，请重试');
    }
  };

  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteDataset(id);
      hide();
      message.success('删除成功');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  // 行内快速设置数据类型(admin):点类型 Tag 弹菜单即选即存,免进详情。
  // key 命中多模态子类型(image|video|audio|cross)→ 一并把 semanticType 设为 multimodal,
  // 子类型反写展示版本 modalities(后端合成代表值);否则按普通语义类型设置。
  const handleQuickSetSemanticType = async (id: string, key: string) => {
    const sub = MODALITY_SUBTYPE_META[key];
    try {
      await updateDataset(
        id,
        sub
          ? {
              semanticType: 'multimodal',
              modalitySubtype: key as 'image' | 'video' | 'audio' | 'cross',
            }
          : { semanticType: key as DataPlatform.SemanticType },
      );
      message.success(
        sub
          ? `已设为多模态·${sub.label}`
          : `已设为${SEMANTIC_TYPE_META[key]?.label ?? key}`,
      );
      actionRef.current?.reload();
    } catch {
      message.error('设置失败，请重试');
    }
  };

  // 行内快速设置分类(admin):点分类单元格弹 TreeSelect,选完即存并关 Popover。
  const handleQuickSetCategory = async (
    id: string,
    categoryId: string | null,
  ) => {
    try {
      await updateDataset(id, { categoryId });
      message.success(categoryId ? '已设置分类' : '已清除分类');
      setQuickCatId(null);
      actionRef.current?.reload();
    } catch {
      message.error('设置失败，请重试');
    }
  };

  const goDetail = (id: string) => history.push(`/datasets/${id}`);

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (dom, record) => (
        <span>
          <a
            onClick={(e) => {
              e.preventDefault();
              goDetail(record.id);
            }}
          >
            {dom}
          </a>
          {record.hosted && (
            <Tag color="geekblue" style={{ marginLeft: 8 }}>
              S3 托管
            </Tag>
          )}
        </span>
      ),
    },
    {
      title: '来源',
      dataIndex: 'sourceKind',
      valueType: 'select',
      valueEnum: SOURCE_KIND_ENUM,
      render: (_, r) => <SourceKindTag kind={r.sourceKind} />,
    },
    {
      title: '版本',
      dataIndex: 'latestVersionLabel',
      search: false,
      width: 150,
      render: (_, r) =>
        r.latestVersionLabel ? (
          <Tag color="blue">{r.latestVersionLabel}</Tag>
        ) : (
          '-'
        ),
    },
    {
      title: '数据类型',
      dataIndex: 'semanticType',
      valueType: 'select',
      valueEnum: SEMANTIC_TYPE_ENUM,
      render: (_, r) => {
        const inner = (
          <>
            <SemanticTypeTag type={r.semanticType} />
            {r.semanticType === 'multimodal' && (
              <ModalitySubtypeTag modalities={r.modalities} />
            )}
          </>
        );
        if (!access.canAdmin) return inner;
        // 多模态展开为子类型子菜单(图片/视频/音频/跨模态);其余语义类型为平铺项。
        const items: MenuProps['items'] = Object.entries(
          SEMANTIC_TYPE_META,
        ).map(([k, meta]) =>
          k === 'multimodal'
            ? {
                key: k,
                label: (
                  <Tag
                    color={meta.color}
                    icon={meta.icon}
                    style={{ marginInlineEnd: 0 }}
                  >
                    {meta.label}
                  </Tag>
                ),
                children: Object.entries(MODALITY_SUBTYPE_META).map(
                  ([sk, sm]) => ({
                    key: sk,
                    label: (
                      <Tag
                        color={sm.color}
                        icon={sm.icon}
                        style={{ marginInlineEnd: 0 }}
                      >
                        {sm.label}
                      </Tag>
                    ),
                  }),
                ),
              }
            : {
                key: k,
                label: (
                  <Tag
                    color={meta.color}
                    icon={meta.icon}
                    style={{ marginInlineEnd: 0 }}
                  >
                    {meta.label}
                  </Tag>
                ),
              },
        );
        return (
          <Dropdown
            menu={{
              items,
              onClick: (e) => handleQuickSetSemanticType(r.id, e.key),
            }}
            trigger={['click']}
          >
            <span style={{ cursor: 'pointer' }}>{inner}</span>
          </Dropdown>
        );
      },
    },
    {
      title: '模态',
      dataIndex: 'modality',
      valueType: 'select',
      valueEnum: MODALITY_ENUM,
      hideInTable: true, // 仅作查询筛选项,不显示为列
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'treeSelect',
      fieldProps: {
        treeData: categoryTreeData,
        allowClear: true,
        treeDefaultExpandAll: true,
        showSearch: true,
        treeNodeFilterProp: 'title',
      },
      render: (_, r) => {
        const inner = r.categoryName || <Tag bordered={false}>未设置</Tag>;
        if (!access.canAdmin) return inner;
        return (
          <Popover
            open={quickCatId === r.id}
            onOpenChange={(o) => setQuickCatId(o ? r.id : null)}
            trigger="click"
            placement="bottomLeft"
            content={
              <div style={{ width: 240 }}>
                <TreeSelect
                  style={{ width: '100%' }}
                  treeData={categoryTreeData}
                  defaultValue={r.categoryId ?? undefined}
                  allowClear
                  showSearch
                  treeNodeFilterProp="title"
                  treeDefaultExpandAll
                  placeholder="选择分类"
                  onChange={(val) => handleQuickSetCategory(r.id, val ?? null)}
                />
              </div>
            }
          >
            <span style={{ cursor: 'pointer' }}>{inner}</span>
          </Popover>
        );
      },
    },
    {
      title: '标签',
      dataIndex: 'tags',
      search: false,
      render: (_, r) => {
        const inner = r.tags?.length ? (
          r.tags.map((t) => (
            <Tag key={t} color={tagColor(t)}>
              {t}
            </Tag>
          ))
        ) : (
          <Tag bordered={false}>未设置</Tag>
        );
        if (!access.canAdmin) return inner;
        return (
          <Popover
            open={quickTagId === r.id}
            onOpenChange={(o) => setQuickTagId(o ? r.id : null)}
            trigger="click"
            placement="bottomLeft"
            content={
              <QuickTagEditor
                datasetId={r.id}
                initialTags={r.tags}
                options={tagOptions}
                onSaved={() => {
                  setQuickTagId(null);
                  actionRef.current?.reload();
                }}
              />
            }
          >
            <span style={{ cursor: 'pointer' }}>{inner}</span>
          </Popover>
        );
      },
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tagsFilter',
      valueType: 'select',
      hideInTable: true,
      fieldProps: {
        mode: 'multiple',
        options: tagOptions,
        allowClear: true,
        showSearch: true,
        optionFilterProp: 'label',
      },
    },
    { title: '描述', dataIndex: 'description', search: false, ellipsis: true },
    { title: '创建人', dataIndex: 'creator' },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAtRange',
      valueType: 'dateRange',
      hideInTable: true,
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      render: (_, record) => [
        <a key="detail" onClick={() => goDetail(record.id)}>
          详情
        </a>,
        // 外部托管数据集禁止删除(#18)——隐藏「删除」，改显 admin「取消托管」;
        // 受管数据集照旧显示「删除」(仅 admin，后端 require_admin 双层防护)
        record.hosted
          ? access.canAdmin && (
              <Popconfirm
                key="unhost"
                title="确认取消托管该数据集？"
                description="仅移除平台引用，不删除 S3 源对象。"
                okText="取消托管"
                onConfirm={() => handleUnhost(record.id)}
              >
                <a>取消托管</a>
              </Popconfirm>
            )
          : access.canAdmin && (
              <Popconfirm
                key="delete"
                title="确认删除该数据集？"
                description="将删除其全部版本与产物文件，不可恢复。"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record.id)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>
            ),
      ],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Dataset>
        headerTitle="数据集仓库"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 'auto', defaultCollapsed: false }}
        options={{ reload: true }}
        rowSelection={{
          selectedRowKeys,
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Dataset[]),
        }}
        tableAlertOptionRender={() => (
          <Access accessible={!!access.canAdmin}>
            {hasHostedSelected ? (
              <Button
                type="link"
                danger
                onClick={() =>
                  message.warning(
                    '外部托管数据集不支持删除，请对其单独使用「取消托管」',
                  )
                }
              >
                批量删除
              </Button>
            ) : (
              <Popconfirm
                title={`确认删除选中的 ${selectedRowKeys.length} 个数据集？`}
                description="将删除其全部版本与产物文件，不可恢复。"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={handleBatchDelete}
              >
                <Button type="link" danger>
                  批量删除
                </Button>
              </Popconfirm>
            )}
          </Access>
        )}
        toolBarRender={() => [
          <Access key="category" accessible={!!access.canAdmin}>
            <Button onClick={() => setCategoryOpen(true)}>分类管理</Button>
          </Access>,
          <Button
            key="host-s3"
            type="primary"
            onClick={() => setHostOpen(true)}
          >
            托管 S3 数据
          </Button>,
        ]}
        request={async (params) => {
          const range = params.createdAt as [string, string] | undefined;
          // 选父含子:把选中的 categoryId 展开成其全部后代 id(逗号 join),后端 IN 查询。
          const selectedCat = (params.categoryId as string) || undefined;
          const categoryIds = selectedCat
            ? collectSubtreeIds(categoryTreeData, selectedCat).join(',') ||
              undefined
            : undefined;
          // 标签筛选(多选 OR):数组 join 逗号,后端 IN 查询。
          const tagsArr = params.tagsFilter as string[] | undefined;
          const tagsParam = tagsArr?.length ? tagsArr.join(',') : undefined;
          const res = await listDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name || undefined,
            semanticType: params.semanticType || undefined,
            modality: params.modality || undefined,
            sourceKind: params.sourceKind || undefined,
            creator: params.creator || undefined,
            categoryIds,
            tags: tagsParam,
            // dateRange 给的是纯日期:起取当日 0 点、止取当日 23:59:59,
            // 否则 created_at <= 当日0点 会漏掉当天创建的记录
            createdStart: range?.[0]
              ? dayjs(range[0]).startOf('day').toISOString()
              : undefined,
            createdEnd: range?.[1]
              ? dayjs(range[1]).endOf('day').toISOString()
              : undefined,
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <ModalForm<DataPlatform.HostS3Params>
        title="托管 S3 数据"
        width={640}
        open={hostOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setHostOpen}
        onFinish={async (values) => {
          try {
            const res = await hostS3(values);
            const n = res?.data?.length ?? values.keys.length;
            message.success(`已托管 ${n} 个对象为受管数据集（未发生下载）`);
            actionRef.current?.reload();
            return true;
          } catch {
            message.error('托管失败，请检查数据源连接与对象选择');
            return false;
          }
        }}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
          将三方 S3 / MinIO 上的对象登记为受管数据集版本（仅存引用，不拷贝）。
          可对其浏览 / 预览 / 加工 / 质量 /
          审核；删除源对象不提供，仅管理员可取消托管。
        </Typography.Paragraph>
        <ProFormSelect
          name="datasourceId"
          label="S3 数据源"
          placeholder="请选择 s3 类型数据源"
          rules={[{ required: true, message: '请选择 S3 数据源' }]}
          request={async () => {
            const res = await listDataSources({ type: 's3', pageSize: 100 });
            return res.data.map((d) => ({
              label: `${d.name}（${d.status}）`,
              value: d.id,
            }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormDependency name={['datasourceId']}>
          {({ datasourceId }) =>
            datasourceId ? (
              <ProFormSelect
                name="bucket"
                label="桶"
                placeholder="请选择桶"
                rules={[{ required: true, message: '请选择桶' }]}
                params={{ datasourceId }}
                request={async () => {
                  try {
                    const res = await listBuckets(datasourceId);
                    return (res.data ?? []).map((b) => ({
                      label: b,
                      value: b,
                    }));
                  } catch {
                    return [];
                  }
                }}
                fieldProps={{ showSearch: true }}
              />
            ) : null
          }
        </ProFormDependency>
        <ProFormDependency name={['datasourceId', 'bucket']}>
          {({ datasourceId, bucket }) =>
            datasourceId && bucket ? (
              <ProFormSelect
                name="keys"
                label="对象"
                mode="multiple"
                placeholder="勾选一个或多个对象（每个对象各产一个数据集）"
                rules={[{ required: true, message: '请至少选择一个对象' }]}
                params={{ datasourceId, bucket }}
                request={async () => {
                  try {
                    const res = await listObjects(datasourceId, { bucket });
                    return (res.data ?? []).map((o) => ({
                      label: `${o.key}（${fmtSize(o.size)}）`,
                      value: o.key,
                    }));
                  } catch {
                    return [];
                  }
                }}
                fieldProps={{ showSearch: true }}
              />
            ) : null
          }
        </ProFormDependency>
        <ProFormText
          name="name"
          label="数据集名称"
          tooltip="留空则按对象 key 自动命名；多选时作为名称前缀"
          placeholder="可选"
        />
        <ProFormSelect
          name="dataType"
          label="数据类型"
          valueEnum={DATA_TYPE_ENUM}
          placeholder="可选"
          fieldProps={{ allowClear: true }}
        />
        <ProFormTreeSelect
          name="categoryId"
          label="分类"
          placeholder="可选"
          fieldProps={{
            treeData: categoryTreeData,
            allowClear: true,
            showSearch: true,
            treeNodeFilterProp: 'title',
            treeDefaultExpandAll: true,
          }}
        />
      </ModalForm>

      <CategoryManager
        open={categoryOpen}
        canAdmin={!!access.canAdmin}
        onClose={() => setCategoryOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />
    </PageContainer>
  );
};

export default DatasetsList;
