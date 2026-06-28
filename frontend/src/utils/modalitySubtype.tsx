import {
  PictureOutlined,
  SoundOutlined,
  SwapOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { ReactNode } from 'react';

/** 多模态子分类(图片/视频/音频/跨模态)的统一元数据。
 *  对齐后端 ``semantic_registry.classify_modalities`` —— 语义 B(图文配对算跨模态)。
 *  列表「数据类型」列的多模态子标签 + 模态筛选项统一从此取,避免各处漂移。 */
export type ModalitySubtypeMeta = {
  label: string;
  /** antd Tag color(预设色名) */
  color: string;
  icon: ReactNode;
};

export const MODALITY_SUBTYPE_META: Record<string, ModalitySubtypeMeta> = {
  image: { label: '图片', color: 'blue', icon: <PictureOutlined /> },
  video: { label: '视频', color: 'purple', icon: <VideoCameraOutlined /> },
  audio: { label: '音频', color: 'cyan', icon: <SoundOutlined /> },
  cross: { label: '跨模态', color: 'magenta', icon: <SwapOutlined /> },
};

/** ProTable 列筛选用的 valueEnum(key → { text }) */
export const MODALITY_ENUM: Record<string, { text: string }> =
  Object.fromEntries(
    Object.entries(MODALITY_SUBTYPE_META).map(([k, v]) => [
      k,
      { text: v.label },
    ]),
  );

// 媒体字段名(复数)→ 子分类 key
const _MEDIA_FIELD_KIND: Record<string, string> = {
  images: 'image',
  audios: 'audio',
  videos: 'video',
};

/** 与后端 classify_modalities 同口径(语义 B):text 计入模态计数。
 *  - ≥2 种(含 text)→ cross
 *  - 恰好 1 种媒体、无 text → image/video/audio
 *  - 空 / 仅 text / 无法判定 → null */
export function classifyModalities(
  modalities?: string[] | null,
): string | null {
  if (!modalities?.length) return null;
  const media = modalities.filter((m) => m in _MEDIA_FIELD_KIND);
  const hasText = modalities.includes('text');
  if (media.length + (hasText ? 1 : 0) >= 2) return 'cross';
  if (media.length === 1 && !hasText) return _MEDIA_FIELD_KIND[media[0]];
  return null;
}

/** 多模态数据集的模态子标签(图片/视频/音频/跨模态);无法判定 → 不渲染(null)。 */
export const ModalitySubtypeTag: React.FC<{
  modalities?: string[] | null;
}> = ({ modalities }) => {
  const kind = classifyModalities(modalities);
  if (!kind) return null;
  const meta = MODALITY_SUBTYPE_META[kind];
  return (
    <Tag color={meta.color} icon={meta.icon} style={{ marginLeft: 4 }}>
      {meta.label}
    </Tag>
  );
};
