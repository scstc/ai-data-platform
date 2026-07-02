import { Button, Collapse, Empty, Modal, message, Spin, Tabs } from 'antd';
import hljs from 'highlight.js/lib/core';
import jsonLang from 'highlight.js/lib/languages/json';
import { type FC, useEffect, useState } from 'react';
import DatasetDataView from '@/pages/datasets/detail/views/DataView';
import {
  getSnapshotPresignedUrl,
  getSnapshotPreview,
} from '@/services/data-platform';

hljs.registerLanguage('json', jsonLang);

/**
 * 数据湖快照预览组件 —— 按 storageFormat 分流预览方式。
 *
 * 参照 VersionFilePreview,针对数据湖单快照(不可变、单文件):
 * - 结构化(csv/tsv/xlsx/xls/json/jsonl/parquet/txt/log)→ preview API,
 *   表格(DatasetDataView) + JSON(highlight.js 折叠高亮)双视图
 * - 图片(png/jpg/…)→ presigned URL 内嵌 <img>
 * - kkFileView 可渲染(pdf/office/媒体)→ presigned URL 经 kkFileView iframe
 * - 其余 → 「不支持预览,请下载」+ 下载按钮
 */

/** 结构化格式:走 preview API 表格/JSON,与 VersionFilePreview 保持一致。 */
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

/** 图片格式:直接 <img> 内嵌 presigned URL。 */
const PREVIEW_IMAGE = new Set([
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'bmp',
  'svg',
]);

/** kkFileView 可渲染格式:pdf / office 文档 / 媒体。 */
const PREVIEW_KK = new Set([
  'pdf',
  'doc',
  'docx',
  'ppt',
  'pptx',
  'mp4',
  'mp3',
  'wav',
  'avi',
  'mov',
  'flv',
  'mkv',
]);

/** kkFileView 服务地址(60 上 docker compose 部署,KK_PORT 默认 8012)。 */
const KK_FILEVIEW_BASE = 'http://10.60.1.60:8012';

export type LakeSnapshotPreviewProps = {
  /** 快照 ID */
  snapshotId: string;
  /** 快照文件名(展示 + 下载用) */
  filename?: string;
  /** 存储格式(如 csv/json/parquet/pdf/png,用于预览方式分流) */
  storageFormat?: string;
  /** 打开/关闭(受控) */
  open: boolean;
  onClose: () => void;
};

type PreviewMode = 'structural' | 'image' | 'kk' | 'download';

const resolveMode = (fmt: string): PreviewMode => {
  if (PREVIEW_STRUCTURAL.has(fmt)) return 'structural';
  if (PREVIEW_IMAGE.has(fmt)) return 'image';
  if (PREVIEW_KK.has(fmt)) return 'kk';
  return 'download';
};

const LakeSnapshotPreview: FC<LakeSnapshotPreviewProps> = ({
  snapshotId,
  filename,
  storageFormat,
  open,
  onClose,
}) => {
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [url, setUrl] = useState<string>();

  const fmt = (storageFormat || '').toLowerCase();
  const mode = resolveMode(fmt);

  useEffect(() => {
    if (!open || !snapshotId) return;

    let cancelled = false;
    setLoading(true);
    setPreview(undefined);
    setUrl(undefined);

    if (mode === 'structural') {
      getSnapshotPreview(snapshotId, { limit: 50, offset: 0 })
        .then((res) => {
          if (cancelled) return;
          setPreview({
            data: (res?.data ?? []) as Record<string, any>[],
            columns: res?.columns ?? [],
            total: res?.total ?? 0,
            success: !!res?.success,
            message: res?.message,
          });
        })
        .catch((err) => {
          if (cancelled) return;
          message.error(
            err?.response?.data?.detail ??
              err?.response?.data?.message ??
              '预览失败',
          );
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      // 图片 / kkFileView / 下载:都需要 presigned URL
      getSnapshotPresignedUrl(snapshotId)
        .then((res) => {
          if (!cancelled) setUrl(res?.url);
        })
        .catch((err) => {
          if (cancelled) return;
          message.error(
            err?.response?.data?.detail ??
              err?.response?.data?.message ??
              '获取文件地址失败',
          );
        })
        .finally(() => {
          // 图片 / kk 保持 loading,等 img/iframe onLoad 再结束;下载模式直接结束
          if (!cancelled && mode === 'download') setLoading(false);
        });
    }

    return () => {
      cancelled = true;
    };
  }, [open, snapshotId, mode]);

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

  const renderContent = () => {
    if (mode === 'structural') {
      if (!preview) return null;
      if (preview.data.length === 0) {
        return (
          <Empty
            description={preview.message ?? '暂无数据'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        );
      }
      return (
        <>
          <div style={{ marginBottom: 8, color: '#666' }}>
            共 {preview.total.toLocaleString()} 行，显示前{' '}
            {Math.min(50, preview.data.length)} 行
          </div>
          <Tabs
            defaultActiveKey="table"
            items={[
              {
                key: 'table',
                label: '表格',
                children: <DatasetDataView preview={preview} />,
              },
              {
                key: 'json',
                label: 'JSON',
                children: jsonPanels.length ? (
                  <Collapse
                    items={jsonPanels}
                    style={{ maxHeight: '60vh', overflow: 'auto' }}
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
        </>
      );
    }

    if (mode === 'image') {
      if (!url) return null;
      return (
        // biome-ignore lint/a11y/useAltText: alt 用文件名兜底
        <img
          src={url}
          alt={filename ?? '图片预览'}
          onLoad={() => setLoading(false)}
          onError={() => setLoading(false)}
          style={{ maxWidth: '100%', display: 'block', margin: '0 auto' }}
        />
      );
    }

    if (mode === 'kk') {
      if (!url) return null;
      const kkUrl = `${KK_FILEVIEW_BASE}/onlinePreview?url=${encodeURIComponent(
        btoa(url),
      )}`;
      return (
        <iframe
          src={kkUrl}
          title={filename ?? '文件预览'}
          onLoad={() => setLoading(false)}
          style={{ width: '100%', height: '75vh', border: 0 }}
        />
      );
    }

    // download 模式
    return (
      <Empty
        description={`当前格式(${fmt || '未知'})不支持在线预览`}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      >
        {url && (
          <Button
            type="primary"
            href={url}
            target="_blank"
            rel="noreferrer"
            download={filename}
          >
            下载文件
          </Button>
        )}
      </Empty>
    );
  };

  return (
    <Modal
      title={
        <div>
          <span>数据预览</span>
          {filename && (
            <span style={{ marginLeft: 8, fontSize: 14, color: '#666' }}>
              {filename}
            </span>
          )}
          {storageFormat && (
            <span
              style={{ marginLeft: 8, fontSize: 12, color: '#999' }}
            >{`.${storageFormat}`}</span>
          )}
        </div>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={1200}
      styles={{ body: { maxHeight: '80vh', overflow: 'auto' } }}
      destroyOnHidden
    >
      <Spin spinning={loading}>{renderContent()}</Spin>
    </Modal>
  );
};

export default LakeSnapshotPreview;
