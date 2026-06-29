import {
  Button,
  Collapse,
  Empty,
  Input,
  List,
  Modal,
  Space,
  Spin,
  Tabs,
  Typography,
} from 'antd';
import hljs from 'highlight.js/lib/core';
import jsonLang from 'highlight.js/lib/languages/json';
import { useEffect, useState } from 'react';
import DatasetDataView from '@/pages/datasets/detail/views/DataView';
import {
  getDatasetMemberUrl,
  listDatasetMembers,
  previewDatasetVersion,
  queryDatasetVersion,
} from '@/services/data-platform';

hljs.registerLanguage('json', jsonLang);

/** 预览格式分流:结构化走表格;其余统一走 kkFileView(onlinePreview?url=base64(presigned))。
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

/** kkFileView 服务地址(60 上 docker compose 部署,KK_PORT 默认 8012)。 */
const KK_FILEVIEW_BASE = 'http://10.60.1.60:8012';

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
}

/** 单个数据集版本的「文件清单 + 按文件预览」组件。
 *
 * 给定 versionId,列出其成员文件;点「预览」按格式分流:
 * 结构化(csv/tsv/xlsx/json/jsonl/txt/log)→ preview?key= 表格(DatasetDataView);
 * 其余(pdf/office/媒体/图片)→ presigned URL 经 kkFileView onlinePreview 渲染。
 *
 * 抽自 datasets/detail,数据任务详情抽屉的「输入版本/产出版本」面板各用一个实例。
 */
/** 结构化预览的多视图:表格 / JSON(可折叠高亮) / SQL 查询(整个版本,只读)。
 *  解决"jsonl 只能显示成表格"——表格复用 DatasetDataView;JSON 用 highlight.js
 *  高亮 + Collapse 折叠(每行一面板);SQL 调 queryDatasetVersion,结果复用 DatasetDataView。 */
const StructuralViews: React.FC<{
  versionId?: string;
  semanticType?: DataPlatform.SemanticType;
  preview?: DataPlatform.DatasetPreview;
}> = ({ versionId, semanticType, preview }) => {
  const [sql, setSql] = useState('SELECT * FROM t LIMIT 50');
  const [sqlResult, setSqlResult] = useState<DataPlatform.DatasetPreview>();
  const [sqlLoading, setSqlLoading] = useState(false);
  const [sqlError, setSqlError] = useState<string>();

  const runSql = async () => {
    if (!versionId) return;
    setSqlLoading(true);
    setSqlError(undefined);
    try {
      const res = await queryDatasetVersion(versionId, { sql, limit: 100 });
      setSqlResult(res);
    } catch (e: any) {
      setSqlError(
        e?.info?.errorMessage || e?.response?.data?.message || '查询失败',
      );
    } finally {
      setSqlLoading(false);
    }
  };

  const jsonPanels = (preview?.data ?? []).map((row, i) => ({
    key: String(i),
    label: `第 ${i + 1} 行`,
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
    <Tabs
      defaultActiveKey="table"
      items={[
        {
          key: 'table',
          label: '表格',
          children: (
            <DatasetDataView semanticType={semanticType} preview={preview} />
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
            <Empty description="无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ),
        },
        {
          key: 'sql',
          label: 'SQL 查询',
          children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text type="secondary">
                对整个版本数据跑只读 SQL(表名 t,仅 SELECT)。例:SELECT count(*)
                FROM t
              </Typography.Text>
              <Input.TextArea
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                autoSize={{ minRows: 2, maxRows: 6 }}
              />
              <Button loading={sqlLoading} onClick={runSql}>
                执行
              </Button>
              {sqlError && (
                <Typography.Text type="danger">{sqlError}</Typography.Text>
              )}
              <DatasetDataView preview={sqlResult} />
            </Space>
          ),
        },
      ]}
    />
  );
};

const VersionFilePreview: React.FC<VersionFilePreviewProps> = ({
  versionId,
  semanticType,
  emptyText,
}) => {
  const [members, setMembers] = useState<DataPlatform.DatasetMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalMember, setModalMember] = useState<DataPlatform.DatasetMember>();
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalPreview, setModalPreview] =
    useState<DataPlatform.DatasetPreview>();
  const [modalUrl, setModalUrl] = useState<string>();

  useEffect(() => {
    if (!versionId) {
      setMembers([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setMembers([]);
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

  const openPreview = async (m: DataPlatform.DatasetMember) => {
    if (!versionId) return;
    setModalMember(m);
    setModalOpen(true);
    setModalPreview(undefined);
    setModalUrl(undefined);
    setModalLoading(true);
    const fmt = (m.format || '').toLowerCase();
    try {
      if (PREVIEW_STRUCTURAL.has(fmt)) {
        const res = await previewDatasetVersion(versionId, {
          key: m.key,
          limit: 50,
        });
        setModalPreview(res);
        setModalLoading(false); // 结构化:数据到即结束
      } else {
        const res = await getDatasetMemberUrl(versionId, m.key);
        setModalUrl(res.data?.url);
        // 非结构化:modalLoading 保持,等 iframe onLoad(kkFileView 就绪)再结束
      }
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
          semanticType={semanticType}
          preview={modalPreview}
        />
      );
    }
    if (!modalUrl) return null;
    // 非结构化统一走 kkFileView:onlinePreview?url={base64(presigned)},
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

  return (
    <>
      <Spin spinning={loading}>
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
                    size="small"
                    onClick={() => openPreview(m)}
                  >
                    预览
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={m.name}
                  description={`${(m.format || '').toUpperCase()} · ${fmtSize(
                    m.size,
                  )}`}
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
        width={960}
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
