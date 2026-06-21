import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDatePicker,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
} from '@ant-design/pro-components';
import { history, useAccess, useParams } from '@umijs/max';
import type { TableColumnsType } from 'antd';
import {
  Button,
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
import { useCallback, useEffect, useState } from 'react';
import {
  getDataset,
  listCategories,
  previewDatasetVersion,
  publishVersion,
  setVersionVerdict,
  unpublishVersion,
  updateDataset,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { SemanticTypeTag } from '@/utils/semanticType';
import DatasetDataView from './views/DataView';

/** 数据类型枚举（编辑表单复用） */
const DATA_TYPE_ENUM = {
  text: { text: 'text' },
  multimodal: { text: 'multimodal' },
  qa: { text: 'qa' },
  cot: { text: 'cot' },
  preference: { text: 'preference' },
  timeseries: { text: 'timeseries' },
  gis: { text: 'gis' },
};

const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const SCAN_VERDICT_TAG: Record<string, { color: string; text: string }> = {
  unscanned: { color: 'default', text: '未扫描' },
  passed: { color: 'green', text: '通过' },
  failed: { color: 'red', text: '未通过' },
};

const PUBLISH_STATUS_TAG: Record<string, { color: string; text: string }> = {
  draft: { color: 'default', text: '草稿' },
  published: { color: 'green', text: '已发布' },
  unpublished: { color: 'default', text: '已下架' },
};

/** 数据集详情页:元数据 + 版本(含发布门) + 按语义类型分发的数据视图。
 *  取代原列表内的详情抽屉,不同类型数据集进入同一路由、按 semanticType 渲染不同数据视图。 */
const DatasetDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const access = useAccess();
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [loading, setLoading] = useState(true);
  const [activeVersion, setActiveVersion] = useState<string>();
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [verdictModal, setVerdictModal] = useState<{
    versionId: string;
    verdict: 'passed' | 'failed';
  }>();
  const [verdictNote, setVerdictNote] = useState('');

  const loadPreview = useCallback(async (versionId: string) => {
    setActiveVersion(versionId);
    setPreviewLoading(true);
    try {
      const res = await previewDatasetVersion(versionId, { limit: 50 });
      setPreview(res);
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await getDataset(id);
      if (res?.success) {
        setDetail(res.data);
        const latest = res.data.versions[res.data.versions.length - 1];
        if (latest) await loadPreview(latest.id);
      }
    } finally {
      setLoading(false);
    }
  }, [id, loadPreview]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    listCategories()
      .then((res) =>
        setCategoryOptions(
          res.data.map((c) => ({ label: c.name, value: c.id })),
        ),
      )
      .catch(() => undefined);
  }, []);

  const reloadDetail = useCallback(async () => {
    if (!id) return;
    const res = await getDataset(id);
    if (res?.success) setDetail(res.data);
  }, [id]);

  const handlePublish = async (versionId: string) => {
    try {
      await publishVersion(versionId);
      message.success('已发布为可训练版本');
      await reloadDetail();
    } catch (e: any) {
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

  const versionColumns: TableColumnsType<DataPlatform.DatasetVersion> = [
    {
      title: '版本',
      dataIndex: 'versionNo',
      render: (_, v) => (
        <a
          onClick={() => loadPreview(v.id)}
          style={{ fontWeight: activeVersion === v.id ? 600 : undefined }}
        >
          {v.versionLabel ?? `v${v.versionNo}`}
        </a>
      ),
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
      title: '加工血缘',
      key: 'lineage',
      render: (_, v) =>
        v.producedByJobId ? (
          <a
            onClick={() =>
              history.push(`/ops/data-tasks?highlight=${v.producedByJobId}`)
            }
          >
            加工自任务
          </a>
        ) : (
          <Tag>原始接入</Tag>
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
          {activeVersion === v.id ? (
            <Tag color="blue">预览中</Tag>
          ) : (
            <a onClick={() => loadPreview(v.id)}>预览</a>
          )}
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
                      ? {
                          color:
                            'var(--ant-color-text-disabled, rgba(0,0,0,.25))',
                          cursor: 'not-allowed',
                        }
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

  // 当前预览的版本标签(供数据预览标题展示「正在看哪个版本」)
  const activeVer = detail?.versions.find((v) => v.id === activeVersion);
  const activeVerLabel =
    activeVer?.versionLabel ?? (activeVer ? `v${activeVer.versionNo}` : '');

  return (
    <PageContainer
      title={
        <Space>
          {detail?.name ?? '数据集详情'}
          {detail?.semanticType && (
            <SemanticTypeTag type={detail.semanticType} />
          )}
        </Space>
      }
      onBack={() => history.push('/datasets/list')}
      breadcrumb={{
        items: [
          { title: '数据集仓库' },
          {
            title: (
              <a onClick={() => history.push('/datasets/list')}>数据集列表</a>
            ),
          },
          { title: detail?.name ?? id },
        ],
      }}
      extra={
        detail && access.canAdmin
          ? [
              <Button
                key="edit"
                type="primary"
                onClick={() => setEditOpen(true)}
              >
                编辑
              </Button>,
            ]
          : undefined
      }
    >
      <Spin spinning={loading}>
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
                  title: '语义类型',
                  dataIndex: 'semanticType',
                  render: (_, r) => <SemanticTypeTag type={r.semanticType} />,
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
              onRow={(v) => ({
                style:
                  activeVersion === v.id
                    ? { background: 'var(--ant-color-primary-bg)' }
                    : undefined,
              })}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              数据预览
              {activeVerLabel ? ` · ${activeVerLabel}` : ''}
              {preview ? `（共 ${preview.total} 行，前 50 行）` : ''}
            </Typography.Title>
            <Spin spinning={previewLoading}>
              <DatasetDataView
                semanticType={detail.semanticType}
                preview={preview}
              />
            </Spin>
          </>
        )}
      </Spin>

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

export default DatasetDetail;
