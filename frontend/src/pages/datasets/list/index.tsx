import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDatePicker,
  ProFormDependency,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { Access, useAccess, useLocation, history } from '@umijs/max';
import type { TableColumnsType } from 'antd';
import {
  Button,
  Drawer,
  Empty,
  Input,
  Modal,
  message,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  batchDeleteDatasets,
  deleteDataset,
  getDataset,
  hostS3,
  listBuckets,
  listCategories,
  listDataSources,
  listDatasets,
  listObjects,
  previewDatasetVersion,
  publishVersion,
  setVersionVerdict,
  unhostDataset,
  unpublishVersion,
  updateDataset,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

/** 数据集类型枚举（列表搜索 + 编辑表单复用） */
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

/** 单元格值渲染：对象转 JSON，其余转字符串 */
const cellText = (v: unknown) =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

/** 安全扫描结论徽章（#4 发布门） */
const SCAN_VERDICT_TAG: Record<string, { color: string; text: string }> = {
  unscanned: { color: 'default', text: '未扫描' },
  passed: { color: 'green', text: '通过' },
  failed: { color: 'red', text: '未通过' },
};

/** 发布状态徽章（#4 发布门） */
const PUBLISH_STATUS_TAG: Record<string, { color: string; text: string }> = {
  draft: { color: 'default', text: '草稿' },
  published: { color: 'green', text: '已发布' },
  unpublished: { color: 'default', text: '已下架' },
};

const DatasetsList: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [activeVersion, setActiveVersion] = useState<string>();
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Dataset[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [hostOpen, setHostOpen] = useState(false);
  const [categoryOpen, setCategoryOpen] = useState(false);
  // 人工覆盖安全扫描结论的弹窗（#4 发布门）
  const [verdictModal, setVerdictModal] = useState<{
    versionId: string;
    verdict: 'passed' | 'failed';
  }>();
  const [verdictNote, setVerdictNote] = useState('');
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
    } catch {
      // 静默：分类筛选不可用不应阻断列表
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  // 从 URL ?highlight=<datasetId> 自动打开对应数据集详情抽屉（来自低质过滤跳转）
  const location = useLocation();
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const highlightId = params.get('highlight');
    if (!highlightId) return;
    // 清除 URL 中的 highlight 参数，避免刷新后重复触发
    history.replace('/datasets/list');
    openDetail(highlightId);
    // openDetail 在 mount 后稳定，不需要列为依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const loadPreview = async (versionId: string) => {
    setActiveVersion(versionId);
    setPreviewLoading(true);
    try {
      const res = await previewDatasetVersion(versionId, { limit: 50 });
      setPreview(res);
    } finally {
      setPreviewLoading(false);
    }
  };

  const openDetail = async (id: string) => {
    const res = await getDataset(id);
    if (res?.success) {
      setDetail(res.data);
      setDetailOpen(true);
      const latest = res.data.versions[res.data.versions.length - 1];
      setPreview(undefined);
      setActiveVersion(undefined);
      if (latest) await loadPreview(latest.id);
    }
  };

  // 发布门动作后刷新抽屉里的版本列表（#4）
  const reloadDetail = async () => {
    if (!detail) return;
    const res = await getDataset(detail.id);
    if (res?.success) setDetail(res.data);
  };

  const handlePublish = async (versionId: string) => {
    try {
      await publishVersion(versionId);
      message.success('已发布为可训练版本');
      await reloadDetail();
    } catch (e: any) {
      // 未过安全扫描 → 后端 409，skipErrorHandler 透出 message
      const msg =
        e?.info?.errorMessage || e?.response?.data?.message || e?.data?.message;
      message.error(msg || '发布失败');
    }
  };

  const handleUnpublish = async (versionId: string) => {
    try {
      await unpublishVersion(versionId);
      message.success('已下架');
      await reloadDetail();
    } catch {
      message.error('下架失败，请重试');
    }
  };

  const submitVerdict = async () => {
    if (!verdictModal) return;
    try {
      await setVersionVerdict(verdictModal.versionId, {
        verdict: verdictModal.verdict,
        note: verdictNote.trim() || undefined,
      });
      message.success(
        verdictModal.verdict === 'passed' ? '已人工标为通过' : '已驳回',
      );
      setVerdictModal(undefined);
      setVerdictNote('');
      await reloadDetail();
    } catch {
      message.error('操作失败，请重试');
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

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (dom, record) => (
        <span>
          <a
            onClick={(e) => {
              e.preventDefault();
              openDetail(record.id);
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
      title: '类型',
      dataIndex: 'dataType',
      valueType: 'select',
      valueEnum: DATA_TYPE_ENUM,
      render: (_, r) => (r.dataType ? <Tag>{r.dataType}</Tag> : '-'),
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'select',
      fieldProps: { options: categoryOptions, allowClear: true },
      render: (_, r) => r.categoryName || '-',
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
        <a key="detail" onClick={() => openDetail(record.id)}>
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

  const versionColumns: TableColumnsType<DataPlatform.DatasetVersion> = [
    {
      title: '版本',
      dataIndex: 'versionNo',
      render: (_, v) => v.versionLabel ?? `v${v.versionNo}`,
    },
    { title: '行数', dataIndex: 'rows', render: (_, v) => v.rows ?? '-' },
    { title: '大小', dataIndex: 'size', render: (_, v) => fmtSize(v.size) },
    {
      title: '来源',
      dataIndex: 'origin',
      render: (_, v) => (
        <Tag color={v.origin === 'managed' ? 'green' : 'gold'}>{v.origin}</Tag>
      ),
    },
    {
      title: '扫描结论',
      dataIndex: 'scanVerdict',
      render: (_, v) => {
        const t =
          SCAN_VERDICT_TAG[v.scanVerdict ?? 'unscanned'] ??
          SCAN_VERDICT_TAG.unscanned;
        return (
          <Tooltip title={v.verdictNote}>
            <Tag color={t.color}>
              {t.text}
              {v.verdictSource === 'manual' ? '·人工' : ''}
            </Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '发布状态',
      dataIndex: 'publishStatus',
      render: (_, v) => {
        const t =
          PUBLISH_STATUS_TAG[v.publishStatus ?? 'draft'] ??
          PUBLISH_STATUS_TAG.draft;
        return <Tag color={t.color}>{t.text}</Tag>;
      },
    },
    {
      // 跨阶段导航:从某版本一键进入下一流程步骤(携 dataset/version 预选,消除“各页面孤立”)
      title: '流程',
      key: 'pipeline',
      render: (_, v) => (
        <Space size="small" wrap>
          <a
            onClick={() =>
              history.push(
                `/governance/content-safety?datasetId=${v.datasetId}&versionId=${v.id}`,
              )
            }
          >
            安全扫描
          </a>
          <a
            onClick={() =>
              history.push(
                `/governance/quality/editor?datasetId=${v.datasetId}&versionId=${v.id}`,
              )
            }
          >
            质量评估
          </a>
          {access.canAdmin && (
            <a
              onClick={() =>
                history.push(
                  `/governance/processing/editor?datasetId=${v.datasetId}&versionId=${v.id}`,
                )
              }
            >
              数据加工
            </a>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      render: (_, v) => (
        <Space size="small" wrap>
          <a
            onClick={() => loadPreview(v.id)}
            style={{ fontWeight: activeVersion === v.id ? 600 : undefined }}
          >
            预览
          </a>
          {access.canAdmin &&
            (v.publishStatus === 'published' ? (
              <Popconfirm
                title="确认下架该版本？"
                description="算法工程师将不再能选用它。"
                onConfirm={() => handleUnpublish(v.id)}
              >
                <a>下架</a>
              </Popconfirm>
            ) : (
              <Tooltip
                title={
                  v.scanVerdict === 'unscanned'
                    ? '需先完成内容安全全量扫描（或管理员人工接受风险）才能发布'
                    : v.scanVerdict === 'failed'
                      ? '安全扫描未通过，请在加工中挂隐私脱敏算子产出新版本重扫，或人工接受风险'
                      : undefined
                }
              >
                <a
                  onClick={() =>
                    v.scanVerdict === 'passed' ? handlePublish(v.id) : undefined
                  }
                  style={
                    v.scanVerdict !== 'passed'
                      ? { color: 'var(--ant-color-text-disabled, rgba(0,0,0,.25))', cursor: 'not-allowed' }
                      : undefined
                  }
                >
                  发布
                </a>
              </Tooltip>
            ))}
          {access.canAdmin && v.scanVerdict !== 'passed' && (
            <a
              onClick={() => {
                setVerdictModal({ versionId: v.id, verdict: 'passed' });
                setVerdictNote('');
              }}
            >
              接受风险
            </a>
          )}
          {access.canAdmin && v.scanVerdict !== 'failed' && (
            <a
              style={{ color: 'var(--ant-color-error, #ff4d4f)' }}
              onClick={() => {
                setVerdictModal({ versionId: v.id, verdict: 'failed' });
                setVerdictNote('');
              }}
            >
              驳回
            </a>
          )}
        </Space>
      ),
    },
  ];

  const previewColumns = (preview?.columns ?? []).map((c) => ({
    title: c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: unknown) => cellText(v),
  }));

  return (
    <PageContainer>
      <ProTable<DataPlatform.Dataset>
        headerTitle="数据集仓库"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 'auto' }}
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
          const res = await listDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name || undefined,
            dataType: params.dataType || undefined,
            creator: params.creator || undefined,
            categoryId: (params.categoryId as string) || undefined,
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

      <Drawer
        width={900}
        open={detailOpen}
        title={detail?.name}
        extra={
          detail && (
            <Access accessible={!!access.canAdmin}>
              <Button type="primary" onClick={() => setEditOpen(true)}>
                编辑
              </Button>
            </Access>
          )
        }
        onClose={() => {
          setDetailOpen(false);
          setDetail(undefined);
          setPreview(undefined);
          setActiveVersion(undefined);
        }}
      >
        {detail && (
          <>
            <ProDescriptions<DataPlatform.DatasetDetail>
              column={2}
              dataSource={detail}
              columns={[
                { title: 'ID', dataIndex: 'id' },
                { title: '名称', dataIndex: 'name' },
                {
                  title: '类型',
                  dataIndex: 'dataType',
                  render: (_, r) => r.dataType ?? '-',
                },
                {
                  title: '分级',
                  dataIndex: 'sensitivityLevel',
                  render: (_, r) => r.sensitivityLevel ?? '-',
                },
                {
                  title: '分类',
                  dataIndex: 'categoryName',
                  render: (_, r) => r.categoryName ?? '-',
                },
                { title: '归属', dataIndex: 'owner' },
                { title: '创建人', dataIndex: 'creator' },
                {
                  title: '最后变更人',
                  dataIndex: 'lastModifier',
                  render: (_, r) => r.lastModifier ?? '-',
                },
                {
                  title: '创建时间',
                  dataIndex: 'createdAt',
                  render: (_, r) => formatDateTime(r.createdAt),
                },
                {
                  title: '更新时间',
                  dataIndex: 'updatedAt',
                  render: (_, r) => formatDateTime(r.updatedAt),
                },
                {
                  title: '有效期',
                  dataIndex: 'validUntil',
                  render: (_, r) =>
                    r.validUntil
                      ? dayjs(r.validUntil).format('YYYY-MM-DD')
                      : '-',
                },
                {
                  title: '描述',
                  dataIndex: 'description',
                  span: 2,
                  render: (_, r) => r.description ?? '-',
                },
              ]}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              版本
            </Typography.Title>
            <Table<DataPlatform.DatasetVersion>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={detail.versions}
              columns={versionColumns}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              数据预览{preview ? `（共 ${preview.total} 行，前 50 行）` : ''}
            </Typography.Title>
            <Spin spinning={previewLoading}>
              {preview && preview.data.length > 0 ? (
                <Table
                  rowKey={(_, i) => String(i)}
                  size="small"
                  scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 10 }}
                  dataSource={preview.data}
                  columns={previewColumns}
                />
              ) : (
                <Empty
                  description={preview?.message ?? '暂无数据'}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </Spin>
          </>
        )}
      </Drawer>

      <ModalForm<DataPlatform.DatasetUpdate>
        title="编辑数据集元数据"
        width={520}
        open={editOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setEditOpen}
        initialValues={
          detail
            ? {
                name: detail.name,
                description: detail.description,
                dataType: detail.dataType,
                sensitivityLevel: detail.sensitivityLevel,
                categoryId: detail.categoryId ?? undefined,
                validUntil: detail.validUntil,
              }
            : undefined
        }
        onFinish={async (values) => {
          if (!detail) return false;
          try {
            // 清空字段显式传 null(后端 exclude_unset 才会把旧值置空);
            // 有效期是纯日期,用 YYYY-MM-DD 避免 toISOString 的 UTC 偏移漂一天
            const res = await updateDataset(detail.id, {
              name: values.name,
              description: values.description ?? null,
              dataType: values.dataType ?? null,
              sensitivityLevel: values.sensitivityLevel ?? null,
              categoryId: values.categoryId ?? null,
              validUntil: values.validUntil
                ? dayjs(values.validUntil).format('YYYY-MM-DD')
                : null,
            });
            message.success('已保存');
            setDetail(res.data);
            setEditOpen(false);
            actionRef.current?.reload();
            return true;
          } catch {
            message.error('保存失败，请重试');
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="名称"
          rules={[{ required: true, message: '请输入名称' }]}
        />
        <ProFormTextArea
          name="description"
          label="描述"
          fieldProps={{ rows: 3 }}
        />
        <ProFormSelect
          name="dataType"
          label="类型"
          valueEnum={DATA_TYPE_ENUM}
          fieldProps={{ allowClear: true }}
        />
        <ProFormSelect
          name="sensitivityLevel"
          label="分级"
          fieldProps={{ allowClear: true }}
          valueEnum={{
            public: { text: 'public' },
            internal: { text: 'internal' },
            confidential: { text: 'confidential' },
          }}
        />
        <ProFormSelect
          name="categoryId"
          label="分类"
          placeholder="请选择分类（可选）"
          options={categoryOptions}
          fieldProps={{ allowClear: true, showSearch: true }}
        />
        <ProFormDatePicker name="validUntil" label="有效期" />
      </ModalForm>

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
        <ProFormSelect
          name="categoryId"
          label="分类"
          placeholder="可选"
          options={categoryOptions}
          fieldProps={{ allowClear: true, showSearch: true }}
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

      <Modal
        title={
          verdictModal?.verdict === 'passed' ? '人工接受风险' : '驳回该版本'
        }
        open={!!verdictModal}
        onOk={submitVerdict}
        onCancel={() => setVerdictModal(undefined)}
        okText="确认"
        okButtonProps={{ danger: verdictModal?.verdict === 'failed' }}
      >
        <Typography.Paragraph type="secondary">
          {verdictModal?.verdict === 'passed'
            ? '将该版本的安全扫描结论人工标为「通过」，即可发布。请填写接受风险的理由（留痕审计）。'
            : '将该版本的安全扫描结论人工标为「未通过」，已发布的将无法继续被选用。请填写驳回理由。'}
        </Typography.Paragraph>
        <Input.TextArea
          rows={3}
          placeholder="理由（可选，建议填写以便审计追溯）"
          value={verdictNote}
          onChange={(e) => setVerdictNote(e.target.value)}
        />
      </Modal>
    </PageContainer>
  );
};

export default DatasetsList;
