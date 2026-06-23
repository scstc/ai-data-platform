import { Button, Empty, List, Modal, Spin } from 'antd';
import { useEffect, useState } from 'react';
import DatasetDataView from '@/pages/datasets/detail/views/DataView';
import {
  getDatasetMemberUrl,
  listDatasetMembers,
  previewDatasetVersion,
} from '@/services/data-platform';

/** 预览格式分流:结构化走表格;其余统一走 kkFileView(onlinePreview?url=base64(presigned))。
 *  与 datasets/detail 原内联逻辑一致——抽到这里供「数据集详情」与「数据任务详情抽屉」共用。 */
const PREVIEW_STRUCTURAL = new Set([
  'csv',
  'tsv',
  'xlsx',
  'xls',
  'json',
  'jsonl',
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
        <DatasetDataView semanticType={semanticType} preview={modalPreview} />
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
