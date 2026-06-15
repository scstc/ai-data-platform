/** 数据接入类型栏定义(key 即数据集 dataType)。 */
export interface AccessType {
  /** 栏 key,等于落库的 dataType */
  key: string;
  /** 菜单标签 */
  label: string;
  /** 是否二进制(本地上传走 raw、预览置灰) */
  binary: boolean;
  /** 允许的扩展名(不含点,小写);sql 栏为空数组(纯引导) */
  extensions: string[];
}

/** 左侧 8 个类型栏(顺序即展示顺序,贴合 BCC 截图) */
export const ACCESS_TYPES: AccessType[] = [
  {
    key: 'csv-tsv',
    label: 'CSV/TSV接入',
    binary: false,
    extensions: ['csv', 'tsv'],
  },
  { key: 'sql', label: 'SQL接入', binary: false, extensions: [] },
  {
    key: 'image',
    label: '图像接入',
    binary: true,
    extensions: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'],
  },
  {
    key: 'audio',
    label: '音频接入',
    binary: true,
    extensions: ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg'],
  },
  {
    key: 'video',
    label: '视频接入',
    binary: true,
    extensions: ['mp4', 'avi', 'mov', 'mkv', 'webm'],
  },
  { key: 'pdf', label: 'PDF接入', binary: false, extensions: ['pdf'] },
  {
    key: 'json',
    label: 'JSON接入',
    binary: false,
    extensions: ['json', 'jsonl'],
  },
  { key: 'log', label: '日志接入', binary: false, extensions: ['log', 'txt'] },
];

/** Upload accept 属性值(带点号,逗号分隔) */
export const acceptOf = (t: AccessType): string =>
  t.extensions.map((ext) => `.${ext}`).join(',');

/** 从文件名取小写扩展名(无扩展名返回空串) */
export const getExtension = (filename: string): string => {
  const dotIndex = filename.lastIndexOf('.');
  if (dotIndex < 0 || dotIndex === filename.length - 1) return '';
  return filename.slice(dotIndex + 1).toLowerCase();
};

/** 文件扩展名是否在该类型栏白名单内 */
export const isExtAllowed = (filename: string, t: AccessType): boolean =>
  t.extensions.includes(getExtension(filename));

/** 字节数格式化为人类友好大小(B / KB / MB / GB) */
export const formatFileSize = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const fixed = value >= 100 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(fixed)} ${units[unitIndex]}`;
};
