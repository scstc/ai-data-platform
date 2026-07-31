import {
  Button,
  Checkbox,
  Collapse,
  Empty,
  Flex,
  Input,
  List,
  Modal,
  message,
  Pagination,
  Popconfirm,
  Spin,
  Tabs,
} from 'antd';
import hljs from 'highlight.js/lib/core';
import jsonLang from 'highlight.js/lib/languages/json';
import { useEffect, useState } from 'react';
import DatasetDataView from '@/pages/datasets/detail/views/DataView';
import {
  deleteVersionMembers,
  editVersionRows,
  getDatasetMemberUrl,
  listDatasetMembers,
  previewDatasetVersion,
} from '@/services/data-platform';
import { toBrowserFileUrl } from '@/utils/storageUrl';

hljs.registerLanguage('json', jsonLang);

/** 预览格式分流:结构化走表格;图片 <img> 直嵌(URL 经 toBrowserFileUrl 归一化);
 *  其余走 kkFileView(onlinePreview?url=base64(presigned))。
 *  与 datasets/detail 原内联逻辑一致——抽到这里供「数据集详情」与「数据任务详情抽屉」共用。
 *  parquet 后端 preview_version 走 DuckDB read_parquet(类型保真),与 csv/jsonl 同走表格预览。 */
const PREVIEW_STRUCTURAL = new Set([
  'csv',
  'tsv',
  'xlsx',
  'xls',
  'json',
  'jsonl',
  'parquet',
  'txt',
  'log',
]);

/** 图片格式:<img> 直嵌。不走 kkFileView——kk 对图片是把原始 URL 交给浏览器端
 *  渲染,内部域名签发形态下浏览器不可达;直嵌配合 toBrowserFileUrl 两种形态都通。 */
const PREVIEW_IMAGE = new Set([
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'bmp',
  'svg',
]);

/** 浏览器原生可播的音频格式:<audio> 直嵌(presigned URL 归一化后直连),
 *  不走 kkFileView——kk 的音频页体验差且内部端点形态下经常播不了。
 *  amr/wma 等浏览器不支持的仍走 kkFileView 转换。 */
const PREVIEW_AUDIO = new Set([
  'wav',
  'mp3',
  'flac',
  'ogg',
  'm4a',
  'aac',
  'opus',
]);

/** 浏览器原生可播的视频格式:<video> 直嵌,同音频理由。
 *  avi/flv/mkv/wmv 等浏览器不支持的仍走 kkFileView 转码。 */
const PREVIEW_VIDEO = new Set(['mp4', 'webm', 'mov', 'm4v']);

/** kkFileView 同源相对路径,由 nginx 反代到 compose 内 kkfileview:8012(KK_CONTEXT_PATH=/kkfileview)。 */
const KK_FILEVIEW_BASE = '/kkfileview';

const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

export interface VersionFilePreviewProps {
  /** 要预览的数据集版本 id;变化时自动重拉成员清单。 */
  versionId?: string;
  /** 结构化预览时透传给 DatasetDataView(按语义类型选视图);可空。 */
  semanticType?: DataPlatform.SemanticType;
  /** 无文件 / 无版本时的占位文案。 */
  emptyText?: string;
  /** 是否允许删除操作(仅 draft 版本且有 edit/admin 权限时传 true)。 */
  editable?: boolean;
  /** 删除成员后的回调(可用于刷新父组件)。 */
  onDeleted?: () => void;
}

/** 单个数据集版本的「文件清单 + 按文件预览」组件。
 *
 * 给定 versionId,列出其成员文件;点「预览」按格式分流:
 * 结构化(csv/tsv/xlsx/json/jsonl/txt/log)→ preview?key= 表格(DatasetDataView);
 * 图片 → presigned URL(归一化)<img> 直嵌;
 * 其余(pdf/office/媒体)→ presigned URL 经 kkFileView onlinePreview 渲染。
 *
 * 抽自 datasets/detail,数据任务详情抽屉的「输入版本/产出版本」面板各用一个实例。
 */
/** 结构化预览的多视图:表格 / JSON(可折叠高亮),服务端真分页。
 *  自己按 (page, pageSize) 调 preview?key=&offset=&limit= 拉当前页,
 *  分页器在 Tabs 下方对两个视图共用;总行数优先用成员元数据 rows
 *  (接口 total 是版本级行数,多文件版本下不等于单成员行数)。 */
const StructuralViews: React.FC<{
  versionId?: string;
  memberKey?: string;
  memberRows?: number;
  semanticType?: DataPlatform.SemanticType;
  /** 允许行级增删改(draft + edit 权限 + jsonl 成员时由父层置 true)。 */
  editable?: boolean;
  /** 行级增删改成功后的回调(父层刷新成员清单/版本信息)。 */
  onMutated?: () => void;
}> = ({
  versionId,
  memberKey,
  memberRows,
  semanticType,
  editable,
  onMutated,
}) => {
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // searchText 是输入框内容,query 是已提交的搜索词(回车/点按钮才生效)
  const [searchText, setSearchText] = useState('');
  const [query, setQuery] = useState('');
  // 行级编辑:refreshKey 触发重拉;rowsTotal 记录编辑后的最新行数
  // (memberRows 来自打开弹窗时的成员快照,编辑后会过期)
  const [refreshKey, setRefreshKey] = useState(0);
  const [rowsTotal, setRowsTotal] = useState<number | undefined>(memberRows);
  const [editorOpen, setEditorOpen] = useState(false);
  // undefined = 新增;否则为待更新行的文件内全局行号
  const [editorIndex, setEditorIndex] = useState<number>();
  const [editorText, setEditorText] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setPage(1);
    setSearchText('');
    setQuery('');
  }, [versionId, memberKey]);

  useEffect(() => {
    setRowsTotal(memberRows);
  }, [memberRows, memberKey]);

  useEffect(() => {
    if (!versionId || !memberKey) return;
    let cancelled = false;
    setLoading(true);
    previewDatasetVersion(versionId, {
      key: memberKey,
      limit: pageSize,
      offset: (page - 1) * pageSize,
      q: query || undefined,
    })
      .then((res) => {
        if (!cancelled) setPreview(res);
      })
      .catch(() => {
        if (!cancelled) setPreview(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, memberKey, page, pageSize, query, refreshKey]);

  const runMutation = async (body: {
    op: 'add' | 'update' | 'delete';
    index?: number;
    row?: Record<string, any>;
  }) => {
    if (!versionId) return;
    setSaving(true);
    try {
      const res = await editVersionRows(versionId, {
        ...body,
        key: memberKey,
      });
      setRowsTotal(res.data?.rows);
      message.success(
        body.op === 'add'
          ? '已新增行'
          : body.op === 'update'
            ? '已保存修改'
            : '已删除行',
      );
      setEditorOpen(false);
      // 删除后当前页只剩这一行且非第一页 → 回退一页;否则原页重拉
      if (
        body.op === 'delete' &&
        (preview?.data?.length ?? 0) <= 1 &&
        page > 1
      ) {
        setPage(page - 1);
      }
      setRefreshKey((k) => k + 1);
      onMutated?.();
    } catch {
      // requestErrorConfig 已弹出后端 message,此处仅兜底
      message.error('操作失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  const submitEditor = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(editorText);
    } catch {
      message.error('JSON 格式错误，请检查');
      return;
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      message.error('行内容必须是 JSON 对象（{...}）');
      return;
    }
    runMutation(
      editorIndex === undefined
        ? { op: 'add', row: parsed as Record<string, any> }
        : {
            op: 'update',
            index: editorIndex,
            row: parsed as Record<string, any>,
          },
    );
  };

  // 行级编辑仅在 indices 可用时开放(非 jsonl 分支如 parquet 不返回 indices)
  const canEdit = !!editable && !!preview?.indices;

  const openAddEditor = () => {
    setEditorIndex(undefined);
    setEditorText(
      JSON.stringify(
        Object.fromEntries((preview?.columns ?? []).map((c) => [c, ''])),
        null,
        2,
      ),
    );
    setEditorOpen(true);
  };

  const rowActions = canEdit
    ? (row: Record<string, any>, i: number) => {
        const globalIdx = preview?.indices?.[i];
        if (globalIdx === undefined) return null;
        return (
          <>
            <Button
              type="link"
              size="small"
              onClick={() => {
                setEditorIndex(globalIdx);
                setEditorText(JSON.stringify(row, null, 2));
                setEditorOpen(true);
              }}
            >
              编辑
            </Button>
            <Popconfirm
              title="确认删除此行？"
              onConfirm={() => runMutation({ op: 'delete', index: globalIdx })}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </>
        );
      }
    : undefined;

  const offset = (page - 1) * pageSize;
  // 搜索时 total 是服务端返回的匹配行数;否则优先用成员行数(编辑后取最新)
  const total = query
    ? (preview?.total ?? 0)
    : (rowsTotal ?? preview?.total ?? 0);
  const jsonPanels = (preview?.data ?? []).map((row, i) => ({
    key: String(i),
    label: `第 ${offset + i + 1} 行`,
    children: (
      <pre
        style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}
        // biome-ignore lint/security/noDangerouslySetInnerHtml: hljs 已对输入 HTML 转义,仅注入高亮 <span>,源数据经 JSON.stringify 安全
        dangerouslySetInnerHTML={{
          __html: hljs.highlight(JSON.stringify(row, null, 2), {
            language: 'json',
          }).value,
        }}
      />
    ),
  }));

  return (
    <Spin spinning={loading}>
      <Tabs
        defaultActiveKey="table"
        tabBarExtraContent={
          <Flex gap={8}>
            <Input.Search
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onSearch={(v) => {
                setQuery(v.trim());
                setPage(1);
              }}
              placeholder="搜索全部行（全文匹配）"
              allowClear
              style={{ width: 260 }}
            />
            {canEdit && (
              <Button type="primary" onClick={openAddEditor}>
                新增行
              </Button>
            )}
          </Flex>
        }
        items={[
          {
            key: 'table',
            label: '表格',
            children: (
              <DatasetDataView
                semanticType={semanticType}
                preview={preview}
                pagination={false}
                rowActions={rowActions}
              />
            ),
          },
          {
            key: 'json',
            label: 'JSON',
            children: jsonPanels.length ? (
              <Collapse
                items={jsonPanels}
                style={{ maxHeight: '70vh', overflow: 'auto' }}
              />
            ) : (
              <Empty
                description="无数据"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ),
          },
        ]}
      />
      <Flex justify="flex-end" style={{ marginTop: 12 }}>
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          pageSizeOptions={[10, 20, 50, 100, 200]}
          showTotal={(t) => `共 ${t.toLocaleString()} 行`}
          onChange={(p, ps) => {
            setPage(ps !== pageSize ? 1 : p);
            setPageSize(ps);
          }}
        />
      </Flex>
      <Modal
        open={editorOpen}
        title={
          editorIndex === undefined ? '新增行' : `编辑第 ${editorIndex + 1} 行`
        }
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={submitEditor}
        onCancel={() => setEditorOpen(false)}
        destroyOnHidden
      >
        <Input.TextArea
          value={editorText}
          onChange={(e) => setEditorText(e.target.value)}
          autoSize={{ minRows: 10, maxRows: 24 }}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          placeholder='{"字段": "值", ...}'
        />
      </Modal>
    </Spin>
  );
};

const VersionFilePreview: React.FC<VersionFilePreviewProps> = ({
  versionId,
  semanticType,
  emptyText,
  editable,
  onDeleted,
}) => {
  const [members, setMembers] = useState<DataPlatform.DatasetMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalMember, setModalMember] = useState<DataPlatform.DatasetMember>();
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalUrl, setModalUrl] = useState<string>();
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);

  const reloadMembers = () => {
    if (!versionId) return;
    setLoading(true);
    listDatasetMembers(versionId)
      .then((res) => setMembers(res.data ?? []))
      .catch(() => setMembers([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!versionId) {
      setMembers([]);
      setSelectedKeys(new Set());
      return;
    }
    let cancelled = false;
    setLoading(true);
    setMembers([]);
    setSelectedKeys(new Set());
    listDatasetMembers(versionId)
      .then((res) => {
        if (!cancelled) setMembers(res.data ?? []);
      })
      .catch(() => {
        if (!cancelled) setMembers([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  const handleDeleteKeys = async (keys: string[]) => {
    if (!versionId || keys.length === 0) return;
    setDeleting(true);
    try {
      await deleteVersionMembers(versionId, keys);
      setSelectedKeys((prev) => {
        const next = new Set(prev);
        keys.forEach((k) => {
          next.delete(k);
        });
        return next;
      });
      reloadMembers();
      onDeleted?.();
    } finally {
      setDeleting(false);
    }
  };

  const toggleSelect = (key: string, checked: boolean) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      checked ? next.add(key) : next.delete(key);
      return next;
    });
  };

  const toggleSelectAll = (checked: boolean) => {
    setSelectedKeys(checked ? new Set(members.map((m) => m.key)) : new Set());
  };

  const openPreview = async (m: DataPlatform.DatasetMember) => {
    if (!versionId) return;
    setModalMember(m);
    setModalOpen(true);
    setModalUrl(undefined);
    const fmt = (m.format || '').toLowerCase();
    if (PREVIEW_STRUCTURAL.has(fmt)) {
      // 结构化:StructuralViews 自带分页拉取与 loading,父层不再预取
      setModalLoading(false);
      return;
    }
    setModalLoading(true);
    try {
      const res = await getDatasetMemberUrl(versionId, m.key);
      setModalUrl(res.data?.url);
      // 音/视频:原生标签直嵌,URL 到手即可渲染,不等 iframe
      if (PREVIEW_AUDIO.has(fmt) || PREVIEW_VIDEO.has(fmt))
        setModalLoading(false);
      // 其余非结构化:modalLoading 保持,等 iframe onLoad(kkFileView 就绪)再结束
    } catch {
      setModalLoading(false);
    }
  };

  const renderPreviewContent = (m?: DataPlatform.DatasetMember) => {
    if (!m) return null;
    const fmt = (m.format || '').toLowerCase();
    if (PREVIEW_STRUCTURAL.has(fmt)) {
      return (
        <StructuralViews
          versionId={versionId}
          memberKey={m.key}
          memberRows={m.rows}
          semanticType={semanticType}
          editable={editable && fmt === 'jsonl'}
          onMutated={() => {
            reloadMembers();
            onDeleted?.();
          }}
        />
      );
    }
    if (!modalUrl) return null;
    if (PREVIEW_IMAGE.has(fmt)) {
      return (
        <img
          src={toBrowserFileUrl(modalUrl)}
          alt={m.name}
          onLoad={() => setModalLoading(false)}
          onError={() => setModalLoading(false)}
          style={{ maxWidth: '100%', display: 'block', margin: '0 auto' }}
        />
      );
    }
    if (PREVIEW_AUDIO.has(fmt)) {
      return (
        <Flex vertical align="center" gap={16} style={{ padding: '40px 24px' }}>
          {/* biome-ignore lint/a11y/useMediaCaption: 数据集音频原件预览,无字幕轨可提供 */}
          <audio
            controls
            preload="metadata"
            src={toBrowserFileUrl(modalUrl)}
            style={{ width: '100%' }}
          />
        </Flex>
      );
    }
    if (PREVIEW_VIDEO.has(fmt)) {
      return (
        // biome-ignore lint/a11y/useMediaCaption: 数据集视频原件预览,无字幕轨可提供
        <video
          controls
          preload="metadata"
          src={toBrowserFileUrl(modalUrl)}
          style={{
            width: '100%',
            maxHeight: '70vh',
            display: 'block',
            background: '#000',
          }}
        />
      );
    }
    // 其余非结构化走 kkFileView:onlinePreview?url={base64(presigned)},
    // kkFileView 从 MinIO 拉原件转换渲染(Office 转 PDF、媒体原生播放等)。
    const kkUrl = `${KK_FILEVIEW_BASE}/onlinePreview?url=${encodeURIComponent(
      btoa(modalUrl),
    )}`;
    return (
      <iframe
        src={kkUrl}
        title={m.name}
        onLoad={() => setModalLoading(false)}
        style={{ width: '100%', height: '80vh', border: 0 }}
      />
    );
  };

  if (!versionId) {
    return <Empty description={emptyText ?? '无版本'} />;
  }

  const allSelected =
    members.length > 0 && selectedKeys.size === members.length;
  const someSelected = selectedKeys.size > 0;

  return (
    <>
      <Spin spinning={loading}>
        {editable && members.length > 0 && (
          <Flex
            align="center"
            justify="space-between"
            style={{ marginBottom: 8 }}
          >
            <Checkbox
              indeterminate={someSelected && !allSelected}
              checked={allSelected}
              onChange={(e) => toggleSelectAll(e.target.checked)}
            >
              全选
            </Checkbox>
            <Popconfirm
              title={`确认删除所选 ${selectedKeys.size} 个文件？`}
              onConfirm={() => handleDeleteKeys([...selectedKeys])}
              disabled={!someSelected}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
            >
              <Button
                type="link"
                danger
                disabled={!someSelected}
                loading={deleting}
              >
                批量删除（{selectedKeys.size}）
              </Button>
            </Popconfirm>
          </Flex>
        )}
        {members.length > 0 ? (
          <List
            size="small"
            bordered
            dataSource={members}
            rowKey="key"
            renderItem={(m) => (
              <List.Item
                actions={[
                  <Button
                    key="preview"
                    type="link"
                    size="small"
                    onClick={() => openPreview(m)}
                  >
                    预览
                  </Button>,
                  ...(editable
                    ? [
                        <Popconfirm
                          key="del"
                          title="确认删除此文件？"
                          onConfirm={() => handleDeleteKeys([m.key])}
                          okText="删除"
                          okButtonProps={{ danger: true }}
                          cancelText="取消"
                        >
                          <a
                            style={{ color: 'var(--ant-color-error, #ff4d4f)' }}
                          >
                            删除
                          </a>
                        </Popconfirm>,
                      ]
                    : []),
                ]}
              >
                {editable && (
                  <Checkbox
                    checked={selectedKeys.has(m.key)}
                    onChange={(e) => toggleSelect(m.key, e.target.checked)}
                    style={{ marginRight: 8 }}
                  />
                )}
                <List.Item.Meta
                  title={m.name}
                  description={[
                    (m.format || '').toUpperCase(),
                    fmtSize(m.size),
                    ...(m.rows != null
                      ? [`${m.rows.toLocaleString()} 行`]
                      : []),
                  ].join(' · ')}
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty description={emptyText ?? '无文件'} />
        )}
      </Spin>

      <Modal
        open={modalOpen}
        title={modalMember?.name ?? '预览'}
        width="85%"
        style={{ maxWidth: 1600 }}
        footer={null}
        destroyOnHidden
        onCancel={() => setModalOpen(false)}
      >
        <Spin spinning={modalLoading}>{renderPreviewContent(modalMember)}</Spin>
      </Modal>
    </>
  );
};

export default VersionFilePreview;
