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
import { history, useAccess, useParams, useSearchParams } from '@umijs/max';
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
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useState } from 'react';
import { VersionFilePreview } from '@/components';
import AclDrawer from '@/components/AclDrawer';
import {
  createDatasetVersion,
  deleteDatasetVersion,
  deleteVersionMembers,
  exportVersionToS3,
  getDataset,
  listBuckets,
  listCategories,
  listDataSources,
  listTags,
  previewDatasetVersion,
  publishVersion,
  setVersionVerdict,
  suggestTags,
  unpublishVersion,
  updateDataset,
  updateDatasetVersion,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import { SemanticTypeTag } from '@/utils/semanticType';
import { SourceKindTag } from '@/utils/sourceKind';
import { tagColor } from '@/utils/tags';
import { TRAIN_TYPE_META, TrainTypeTag } from '@/utils/trainType';
import { UploadChannelTag } from '@/utils/uploadChannel';

/** 自动打标匹配为空时的兜底标签(与后端 ai.py _DEFAULT_FALLBACK_TAG 同值) */
const DEFAULT_FALLBACK_TAG = '通用业务（默认）';

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

/** manifest 清单弹框预览:分页读清单行(name/format/size),就地查看不下载。
 *  数据走版本 preview 端点(manifest 版本返回成员清单),服务端分页(上限 1000 条)。 */
const ManifestPreviewModal: React.FC<{
  version?: DataPlatform.DatasetVersion;
  onClose: () => void;
}> = ({ version, onClose }) => {
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);

  // 切换版本回到第一页
  useEffect(() => {
    setPage(1);
  }, [version?.id]);

  useEffect(() => {
    if (!version) return;
    setLoading(true);
    previewDatasetVersion(version.id, {
      limit: pageSize,
      offset: (page - 1) * pageSize,
    })
      .then((res) => {
        setRows(res.data ?? []);
        setTotal(res.total ?? 0);
      })
      .catch(() => message.error('读取清单失败'))
      .finally(() => setLoading(false));
  }, [version, page, pageSize]);

  return (
    <Modal
      title={`文件清单 · ${version?.versionLabel ?? ''}`}
      open={!!version}
      onCancel={onClose}
      footer={null}
      width={680}
    >
      <Table
        size="small"
        rowKey={(r, i) => `${r.name}-${i}`}
        loading={loading}
        dataSource={rows}
        columns={[
          { title: '文件名', dataIndex: 'name', ellipsis: true },
          { title: '格式', dataIndex: 'format', width: 90 },
          {
            title: '大小',
            dataIndex: 'size',
            width: 110,
            render: (s?: number) => fmtSize(s),
          },
        ]}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个文件`,
          onChange: (p, ps) => {
            setPage(ps !== pageSize ? 1 : p);
            setPageSize(ps);
          },
        }}
      />
    </Modal>
  );
};

/** 数据集详情页:元数据 + 版本(含发布门) + 按语义类型分发的数据视图。
 *  取代原列表内的详情抽屉,不同类型数据集进入同一路由、按 semanticType 渲染不同数据视图。 */
const DatasetDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const access = useAccess();
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [loading, setLoading] = useState(true);
  const [activeVersion, setActiveVersion] = useState<string>();
  const [editOpen, setEditOpen] = useState(false);
  const [aclOpen, setAclOpen] = useState(false);
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  const [verdictModal, setVerdictModal] = useState<{
    versionId: string;
    verdict: 'passed' | 'failed';
  }>();
  const [verdictNote, setVerdictNote] = useState('');
  // 导出到 S3:记录当前要导出的版本(打开 ModalForm)
  const [exportVersion, setExportVersion] =
    useState<DataPlatform.DatasetVersion>();
  // manifest 清单弹框预览:记录当前查看清单的版本
  const [manifestVersion, setManifestVersion] =
    useState<DataPlatform.DatasetVersion>();
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [editVersionOpen, setEditVersionOpen] = useState(false);
  const [editingVersion, setEditingVersion] =
    useState<DataPlatform.DatasetVersion>();
  const [selectedMembers, setSelectedMembers] = useState<
    Record<string, string[]>
  >({}); // { versionId: [tableName1, tableName2] }
  // 自动打标:AI 建议标签(undefined=弹框关闭)与勾选态
  const [autoTagLoading, setAutoTagLoading] = useState(false);
  const [suggestedTags, setSuggestedTags] = useState<string[]>();
  const [checkedTags, setCheckedTags] = useState<string[]>([]);

  // 切换当前查看的版本;文件清单 + 按文件预览由 VersionFilePreview 按 versionId 自管
  const loadPreview = useCallback((versionId: string) => {
    setActiveVersion(versionId);
  }, []);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await getDataset(id);
      if (res?.success) {
        setDetail(res.data);
        // 深链 ?version=<id> 命中(如从"数据血缘"点版本跳来)则定位到该版本,否则落最新版
        const wanted = searchParams.get('version');
        const target =
          wanted && res.data.versions.some((v) => v.id === wanted)
            ? wanted
            : res.data.versions[res.data.versions.length - 1]?.id;
        if (target) await loadPreview(target);
      }
    } finally {
      setLoading(false);
    }
  }, [id, loadPreview, searchParams]);

  // 深链定位:activeVersion 变化后,把左侧版本列表里对应项滚到可见区
  useEffect(() => {
    if (!activeVersion) return;
    const el = document.querySelector(`[data-version-id="${activeVersion}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [activeVersion]);

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

  const handleCreateVersion = async () => {
    if (!id) return;
    setCreatingVersion(true);
    try {
      const res = await createDatasetVersion(id);
      message.success('已新建版本');
      await reloadDetail();
      if (res?.data?.id) await loadPreview(res.data.id);
    } catch {
      message.error('新建版本失败，请重试');
    } finally {
      setCreatingVersion(false);
    }
  };

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

  const handleDeleteMembers = async (versionId: string, keys: string[]) => {
    if (keys.length === 0) {
      message.warning('请选择要删除的表成员');
      return;
    }
    try {
      const res = await deleteVersionMembers(versionId, keys, {
        skipErrorHandler: true,
      });
      message.success(
        `已删除 ${res.data?.deleted ?? 0} 个表成员${res.data?.notFound ? `,${res.data.notFound} 个未找到` : ''}`,
      );
      setSelectedMembers((prev) => ({ ...prev, [versionId]: [] }));
      await reloadDetail();
    } catch (e: any) {
      const body = e?.response?.data ?? e?.data;
      message.error(body?.message ?? e?.message ?? '删除失败，请重试');
    }
  };

  /** 自动打标:拉已有标签库 + 数据集名称/元数据交给 LLM,弹框勾选后合并保存。 */
  const handleAutoTag = async () => {
    if (!detail) return;
    setAutoTagLoading(true);
    try {
      const known = await listTags()
        .then((r) => (r.data ?? []).map((t) => t.name))
        .catch(() => [] as string[]);
      const res = await suggestTags({
        name: detail.name,
        description: detail.description ?? undefined,
        category: detail.categoryName ?? undefined,
        dataType: detail.semanticType ?? undefined,
        existingTags: detail.tags ?? [],
        knownTags: known,
      });
      const tags = res.data?.tags ?? [];
      // 后端已兜底默认标签;此处二次防御,防后端老版本/降级路径返回空
      const finalTags = tags.length ? tags : [DEFAULT_FALLBACK_TAG];
      setSuggestedTags(finalTags);
      setCheckedTags(finalTags);
    } catch {
      message.error('自动打标失败，请重试');
    } finally {
      setAutoTagLoading(false);
    }
  };

  const applyAutoTags = async () => {
    if (!detail || checkedTags.length === 0) {
      setSuggestedTags(undefined);
      return;
    }
    try {
      const merged = Array.from(
        new Set([...(detail.tags ?? []), ...checkedTags]),
      );
      const res = await updateDataset(detail.id, { tags: merged });
      message.success(`已添加 ${checkedTags.length} 个标签`);
      setDetail(res.data);
      setSuggestedTags(undefined);
    } catch {
      message.error('保存标签失败，请重试');
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
        extra={
          <Space size="small">
            <a
              onClick={() => {
                setEditingVersion(v);
                setEditVersionOpen(true);
              }}
            >
              编辑
            </a>
            {v.publishStatus === 'draft' && (
              <Popconfirm
                title="确认删除此版本？"
                description="删除后不可恢复。"
                okText="删除"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={async () => {
                  try {
                    await deleteDatasetVersion(v.id);
                    message.success('版本已删除');
                    setDetail((prev) => {
                      if (!prev) return prev;
                      return {
                        ...prev,
                        versions: prev.versions.filter((x) => x.id !== v.id),
                      };
                    });
                    if (activeVersion === v.id) {
                      const remaining = (detail?.versions ?? []).filter(
                        (x) => x.id !== v.id,
                      );
                      setActiveVersion(remaining[remaining.length - 1]?.id);
                    }
                  } catch (e: any) {
                    message.error(e?.response?.data?.message || '删除失败');
                  }
                }}
              >
                <a style={{ color: 'var(--ant-color-error)' }}>删除</a>
              </Popconfirm>
            )}
          </Space>
        }
      >
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="训练用途">
            <TrainTypeTag type={v.trainType} />
          </Descriptions.Item>
          <Descriptions.Item label="说明">{v.note ?? '-'}</Descriptions.Item>
          {v.schemaVariant && (
            <Descriptions.Item label="Schema 变体">
              <Tag color="cyan">{v.schemaVariant}</Tag>
            </Descriptions.Item>
          )}
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
        </Descriptions>
        {(v.tables?.length ?? 0) > 1 && (
          <>
            <Divider style={{ margin: '12px 0' }}>
              表成员（{v.tables?.length}）
              {v.publishStatus === 'draft' &&
                access.canAdmin &&
                selectedMembers[v.id]?.length > 0 && (
                  <Popconfirm
                    title="确认删除"
                    description={`确认删除选中的 ${selectedMembers[v.id]?.length} 个表成员吗？`}
                    onConfirm={() =>
                      handleDeleteMembers(v.id, selectedMembers[v.id])
                    }
                  >
                    <Button
                      type="link"
                      danger
                      size="small"
                      style={{ marginLeft: 8 }}
                    >
                      批量删除（{selectedMembers[v.id]?.length}）
                    </Button>
                  </Popconfirm>
                )}
            </Divider>
            <Table<DataPlatform.DatasetTable>
              size="small"
              rowKey="tableName"
              pagination={false}
              dataSource={v.tables ?? []}
              rowSelection={
                v.publishStatus === 'draft' && access.canAdmin
                  ? {
                      selectedRowKeys: selectedMembers[v.id] ?? [],
                      onChange: (keys) =>
                        setSelectedMembers((prev) => ({
                          ...prev,
                          [v.id]: keys as string[],
                        })),
                    }
                  : undefined
              }
              columns={[
                { title: '表名', dataIndex: 'tableName' },
                {
                  title: '格式',
                  dataIndex: 'format',
                  render: (f: string) => <Tag>{f}</Tag>,
                },
                {
                  title: '来源',
                  dataIndex: 'sourceUploadChannel',
                  render: (c?: DataPlatform.DataLakeUploadChannel | null) => (
                    <UploadChannelTag channel={c} />
                  ),
                },
                {
                  title: '行数',
                  dataIndex: 'rows',
                  render: (r?: number) => r ?? '-',
                },
                {
                  title: '大小',
                  dataIndex: 'size',
                  render: (s?: number) => fmtSize(s),
                },
                ...(v.publishStatus === 'draft' && access.canAdmin
                  ? [
                      {
                        title: '操作',
                        width: 80,
                        render: (_: any, record: DataPlatform.DatasetTable) => (
                          <Popconfirm
                            title="确认删除"
                            description={`确认删除表「${record.tableName}」吗？`}
                            onConfirm={() =>
                              handleDeleteMembers(v.id, [record.tableName])
                            }
                          >
                            <Button type="link" danger size="small">
                              删除
                            </Button>
                          </Popconfirm>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </>
        )}
        <Divider style={{ margin: '12px 0' }} />
        <Space size="small" wrap>
          <Button
            size="small"
            onClick={() =>
              window.open(`/api/v1/dataset-versions/${v.id}/download`, '_blank')
            }
          >
            下载
          </Button>
          <Button size="small" onClick={() => setExportVersion(v)}>
            导出到分布式存储
          </Button>
          {v.format === 'manifest' && (
            <Button size="small" onClick={() => setManifestVersion(v)}>
              查看清单
            </Button>
          )}
        </Space>
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
        detail
          ? [
              <Button
                key="lineage"
                onClick={() =>
                  history.push(`/ops/lineage?datasetId=${detail.id}`)
                }
              >
                查看血缘
              </Button>,
              detail.myLevel === 'admin' ? (
                <Button key="acl" onClick={() => setAclOpen(true)}>
                  权限管理
                </Button>
              ) : null,
              access.canAdmin ? (
                <Button
                  key="edit"
                  type="primary"
                  onClick={() => setEditOpen(true)}
                >
                  编辑
                </Button>
              ) : null,
            ].filter(Boolean)
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
                  render: (_, r) =>
                    r.sourceKind ? (
                      <SourceKindTag kind={r.sourceKind} />
                    ) : r.sourceChannels?.length ? (
                      <Space size={4} wrap>
                        {r.sourceChannels.map((c) => (
                          <UploadChannelTag key={c} channel={c} />
                        ))}
                      </Space>
                    ) : (
                      '-'
                    ),
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
                  render: (_, r) => (
                    <Space size={4} wrap>
                      {r.tags?.length
                        ? r.tags.map((t) => (
                            <Tag
                              key={t}
                              color={tagColor(t)}
                              style={{ marginInlineEnd: 0 }}
                            >
                              {t}
                            </Tag>
                          ))
                        : '-'}
                      {access.canAdmin && (
                        <Button
                          size="small"
                          type="link"
                          style={{ padding: 0, height: 'auto' }}
                          loading={autoTagLoading}
                          onClick={handleAutoTag}
                        >
                          自动打标
                        </Button>
                      )}
                    </Space>
                  ),
                },
                {
                  title: '描述',
                  dataIndex: 'description',
                  span: 2,
                  render: (_, r) => r.description ?? '-',
                },
              ]}
            />

            <Flex
              justify="space-between"
              align="center"
              style={{ marginTop: 16 }}
            >
              <Typography.Title level={5} style={{ margin: 0 }}>
                版本（{detail.versions.length}）
              </Typography.Title>
              {(detail.myLevel === 'edit' || detail.myLevel === 'admin') && (
                <Button
                  size="small"
                  loading={creatingVersion}
                  onClick={handleCreateVersion}
                >
                  新建版本
                </Button>
              )}
            </Flex>
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
                        data-version-id={v.id}
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
            </Typography.Title>
            <VersionFilePreview
              versionId={activeVersion}
              semanticType={detail?.semanticType}
              editable={
                activeVer?.publishStatus === 'draft' &&
                (detail?.myLevel === 'edit' || detail?.myLevel === 'admin')
              }
              onDeleted={load}
            />
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

      {/* 自动打标:展示 AI 建议标签,勾选确认后合并进数据集标签 */}
      <Modal
        title="自动打标 · AI 建议标签"
        open={!!suggestedTags}
        onOk={applyAutoTags}
        onCancel={() => setSuggestedTags(undefined)}
        okText={`添加（${checkedTags.length}）`}
        okButtonProps={{ disabled: checkedTags.length === 0 }}
      >
        <Typography.Paragraph type="secondary">
          基于数据集名称与元数据（描述 / 分类 / 数据类型）由 AI
          生成，勾选要添加的标签：
        </Typography.Paragraph>
        <Tag.CheckableTagGroup
          multiple
          options={suggestedTags ?? []}
          value={checkedTags}
          onChange={(v) =>
            setCheckedTags(Array.isArray(v) ? (v as string[]) : [])
          }
        />
      </Modal>

      <ModalForm<DataPlatform.ExportS3Params>
        title="导出到分布式存储"
        width={520}
        open={!!exportVersion}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(o) => {
          if (!o) setExportVersion(undefined);
        }}
        onFinish={async (values) => {
          if (!exportVersion) return false;
          try {
            const res = await exportVersionToS3(exportVersion.id, {
              datasourceId: values.datasourceId,
              bucket: values.bucket,
              prefix: values.prefix || undefined,
            });
            message.success(
              `已导出 ${res.data.exported} 个对象到 ${res.data.target}`,
            );
            setExportVersion(undefined);
            return true;
          } catch (e: any) {
            const msg =
              e?.info?.errorMessage ||
              e?.response?.data?.message ||
              e?.data?.message;
            message.error(msg || '导出失败，请重试');
            return false;
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          把该已发布版本的数据导出到一个已登记的 S3
          数据源（读源、写目标，绝不回写托管源）。
        </Typography.Paragraph>
        <ProFormSelect
          name="datasourceId"
          label="目标 S3 数据源"
          rules={[{ required: true, message: '请选择目标 S3 数据源' }]}
          request={async () => {
            const res = await listDataSources({ type: 's3', pageSize: 200 });
            return (res.data ?? []).map((d) => ({
              label: d.name,
              value: d.id,
            }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormSelect
          name="bucket"
          label="目标桶"
          rules={[{ required: true, message: '请选择目标桶' }]}
          dependencies={['datasourceId']}
          request={async (params) => {
            const dsId = (params as { datasourceId?: string }).datasourceId;
            if (!dsId) return [];
            const res = await listBuckets(dsId);
            return (res.data ?? []).map((b) => ({ label: b, value: b }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormText
          name="prefix"
          label="目标前缀（可选）"
          placeholder="如 exports/my-dataset；对象将落在「前缀/文件名」"
        />
      </ModalForm>

      {/* 编辑版本元数据 */}
      <ModalForm<{ trainType?: string; note?: string }>
        title="编辑版本元数据"
        width={480}
        open={editVersionOpen}
        modalProps={{ destroyOnHidden: true }}
        initialValues={{
          trainType: editingVersion?.trainType ?? undefined,
          note: editingVersion?.note ?? undefined,
        }}
        onOpenChange={(o) => {
          if (!o) {
            setEditVersionOpen(false);
            setEditingVersion(undefined);
          }
        }}
        onFinish={async (values) => {
          if (!editingVersion) return false;
          try {
            const res = await updateDatasetVersion(editingVersion.id, {
              trainType: values.trainType ?? null,
              note: values.note ?? null,
            });
            if (res?.success) {
              message.success('已更新');
              setDetail((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  versions: prev.versions.map((v) =>
                    v.id === editingVersion.id ? { ...v, ...res.data } : v,
                  ),
                };
              });
              setEditVersionOpen(false);
              setEditingVersion(undefined);
              return true;
            }
          } catch (e: any) {
            message.error(e?.response?.data?.message || '更新失败');
          }
          return false;
        }}
      >
        <ProFormSelect
          name="trainType"
          label="训练用途"
          allowClear
          options={Object.entries(TRAIN_TYPE_META).map(([k, v]) => ({
            value: k,
            label: v.label,
          }))}
        />
        <ProFormText name="note" label="说明" placeholder="版本备注（可选）" />
      </ModalForm>

      {detail && (
        <AclDrawer
          open={aclOpen}
          onClose={() => setAclOpen(false)}
          resource="datasets"
          resourceId={detail.id}
          owner={detail.owner}
        />
      )}

      <ManifestPreviewModal
        version={manifestVersion}
        onClose={() => setManifestVersion(undefined)}
      />
    </PageContainer>
  );
};

export default DatasetDetail;
