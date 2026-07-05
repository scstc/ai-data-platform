/**
 * 抽取生成数据集的入参条目 —— 主表按文件(取最新版本快照)勾选,或版本历史抽屉
 * 按单个版本抽取,统一成这个形状喂给 detail.tsx 的抽取 ModalForm。
 */
export type ExtractItem = {
  snapshotId: string;
  displayName: string;
  dataCategory: DataPlatform.DataLakeDataCategory;
  storageFormat: string | null;
};
