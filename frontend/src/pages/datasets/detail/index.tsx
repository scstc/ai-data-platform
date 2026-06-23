import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDatePicker,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProFormTreeSelect,
} from '@ant-design/pro-components';
import { history, useAccess, useParams } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Flex,
  Input,
  List,
  Modal,
  message,
  Popconfirm,
  Row,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useState } from 'react';
import {
  getDataset,
  listCategories,
  listTags,
  previewDatasetVersion,
  publishVersion,
  setVersionVerdict,
  unpublishVersion,
  updateDataset,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import { SemanticTypeTag } from '@/utils/semanticType';
import {
  SENSITIVITY_LEVEL_ENUM,
  sensitivityLevelLabel,
} from '@/utils/sensitivityLevel';
import { SourceKindTag } from '@/utils/sourceKind';
import { tagColor } from '@/utils/tags';
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
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
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
      .then((res) => setCategoryTreeData(toCategoryTreeData(res.data)))
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

  const renderLineage = (v: DataPlatform.DatasetVersion) =>
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
    );

  const renderPipelineLinks = (v: DataPlatform.DatasetVersion) => (
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
            `/assessment/quality/editor?datasetId=${v.datasetId}&versionId=${v.id}`,
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
  );

  /** 管理员才有的发布门操作（预览由左侧选中隐式触发，故不再有单独「预览」入口）。 */
  const renderVersionActions = (v: DataPlatform.DatasetVersion) => (
    <Space size="small" wrap>
      {v.publishStatus === 'published' ? (
        <Popconfirm
          title="确认下架该版本？"
          description="算法工程师将不再能选用它。"
          onConfirm={() => handleUnpublish(v.id)}
        >
          <Button size="small">下架</Button>
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
          <Button
            size="small"
            type="primary"
            disabled={v.scanVerdict !== 'passed'}
            onClick={() =>
              v.scanVerdict === 'passed' ? handlePublish(v.id) : undefined
            }
          >
            发布
          </Button>
        </Tooltip>
      )}
      {v.scanVerdict !== 'passed' && (
        <Button
          size="small"
          onClick={() => {
            setVerdictModal({ versionId: v.id, verdict: 'passed' });
            setVerdictNote('');
          }}
        >
          接受风险
        </Button>
      )}
      {v.scanVerdict !== 'failed' && (
        <Button
          size="small"
          danger
          onClick={() => {
            setVerdictModal({ versionId: v.id, verdict: 'failed' });
            setVerdictNote('');
          }}
        >
          驳回
        </Button>
      )}
    </Space>
  );

  /** 右侧:选中版本的完整详情(行数/大小/来源/血缘/扫描/发布 + 流程 + 操作)。 */
  const renderVersionDetail = (v: DataPlatform.DatasetVersion) => {
    const scan =
      SCAN_VERDICT_TAG[v.scanVerdict ?? 'unscanned'] ??
      SCAN_VERDICT_TAG.unscanned;
    const pub =
      PUBLISH_STATUS_TAG[v.publishStatus ?? 'draft'] ??
      PUBLISH_STATUS_TAG.draft;
    return (
      <Card
        size="small"
        title={
          <Space>
            <span>{v.versionLabel ?? `v${v.versionNo}`}</span>
            <Tag color={v.origin === 'managed' ? 'green' : 'gold'}>
              {v.origin}
            </Tag>
          </Space>
        }
      >
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="行数">{v.rows ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="大小">{fmtSize(v.size)}</Descriptions.Item>
          <Descriptions.Item label="扫描结论">
            <Tooltip title={v.verdictNote}>
              <Tag color={scan.color}>
                {scan.text}
                {v.verdictSource === 'manual' ? '·人工' : ''}
              </Tag>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="发布状态">
            <Tag color={pub.color}>{pub.text}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="加工血缘">
            {renderLineage(v)}
          </Descriptions.Item>
          <Descriptions.Item label="流程">
            {renderPipelineLinks(v)}
          </Descriptions.Item>
        </Descriptions>
        {access.canAdmin && (
          <>
            <Divider style={{ margin: '12px 0' }} />
            {renderVersionActions(v)}
          </>
        )}
      </Card>
    );
  };

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
                  title: '来源',
                  dataIndex: 'sourceKind',
                  render: (_, r) => <SourceKindTag kind={r.sourceKind} />,
                },
                {
                  title: '数据类型',
                  dataIndex: 'semanticType',
                  render: (_, r) => <SemanticTypeTag type={r.semanticType} />,
                },
                {
                  title: '格式',
                  dataIndex: 'sourceFormat',
                  render: (_, r) =>
                    r.sourceFormat ? r.sourceFormat.toUpperCase() : '-',
                },
                {
                  title: '分级',
                  dataIndex: 'sensitivityLevel',
                  render: (_, r) =>
                    sensitivityLevelLabel(r.sensitivityLevel) ?? '-',
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
                  title: '最后变更人',
                  dataIndex: 'lastModifier',
                  render: (_, r) => r.lastModifier ?? '-',
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
                  title: '标签',
                  dataIndex: 'tags',
                  render: (_, r) =>
                    r.tags?.length
                      ? r.tags.map((t) => (
                          <Tag key={t} color={tagColor(t)}>
                            {t}
                          </Tag>
                        ))
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
              版本（{detail.versions.length}）
            </Typography.Title>
            <Row gutter={16}>
              <Col xs={24} md={9} lg={7}>
                <List<DataPlatform.DatasetVersion>
                  size="small"
                  bordered
                  rowKey="id"
                  dataSource={[...detail.versions].reverse()}
                  style={{
                    maxHeight: 248,
                    overflowY: 'auto',
                    borderRadius: 8,
                  }}
                  renderItem={(v) => {
                    const selected = activeVersion === v.id;
                    const pub =
                      PUBLISH_STATUS_TAG[v.publishStatus ?? 'draft'] ??
                      PUBLISH_STATUS_TAG.draft;
                    const scan =
                      SCAN_VERDICT_TAG[v.scanVerdict ?? 'unscanned'] ??
                      SCAN_VERDICT_TAG.unscanned;
                    return (
                      <List.Item
                        onClick={() => loadPreview(v.id)}
                        style={{
                          cursor: 'pointer',
                          paddingInline: 12,
                          background: selected
                            ? 'var(--ant-color-primary-bg)'
                            : undefined,
                          borderInlineStart: `2px solid ${
                            selected
                              ? 'var(--ant-color-primary)'
                              : 'transparent'
                          }`,
                          transition: 'background 0.2s',
                        }}
                      >
                        <Flex vertical gap={2} style={{ width: '100%' }}>
                          <Typography.Text strong={selected}>
                            {v.versionLabel ?? `v${v.versionNo}`}
                          </Typography.Text>
                          <Space size={4} wrap>
                            <Tag
                              color={pub.color}
                              style={{ marginInlineEnd: 0 }}
                            >
                              {pub.text}
                            </Tag>
                            <Tag
                              color={scan.color}
                              style={{ marginInlineEnd: 0 }}
                            >
                              {scan.text}
                              {v.verdictSource === 'manual' ? '·人工' : ''}
                            </Tag>
                          </Space>
                        </Flex>
                      </List.Item>
                    );
                  }}
                />
              </Col>
              <Col xs={24} md={15} lg={17}>
                {activeVer ? (
                  renderVersionDetail(activeVer)
                ) : (
                  <Empty description="选择左侧版本查看详情" />
                )}
              </Col>
            </Row>

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
                tags: detail.tags,
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
              tags: values.tags,
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
          valueEnum={SENSITIVITY_LEVEL_ENUM}
        />
        <ProFormTreeSelect
          name="categoryId"
          label="分类"
          placeholder="请选择分类（可选）"
          fieldProps={{
            treeData: categoryTreeData,
            allowClear: true,
            showSearch: true,
            treeNodeFilterProp: 'title',
            treeDefaultExpandAll: true,
          }}
        />
        <ProFormDatePicker name="validUntil" label="有效期" />
        <ProFormSelect
          name="tags"
          label="标签"
          mode="tags"
          placeholder="输入标签，回车添加（可多选）"
          request={async () => {
            const res = await listTags();
            return (res.data ?? []).map((t) => ({
              label: t.name,
              value: t.name,
            }));
          }}
          fieldProps={{ allowClear: true }}
        />
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
