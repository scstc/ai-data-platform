// 回收站 API(仅超管):过期打删除标记的数据集列表 + 恢复
import { request } from '@umijs/max';

export type RecycledDataset = {
  id: string;
  name: string;
  owner: string;
  creator: string;
  validUntil?: string;
  deletedAt: string;
  deletedReason?: string;
  cascadedJobs: number;
  cascadedIngestTasks: number;
};

export async function listRecycledDatasets(
  params: { current?: number; pageSize?: number; name?: string } = {},
) {
  return request<{ data: RecycledDataset[]; total: number; success: boolean }>(
    '/api/v1/recycle-bin/datasets',
    { method: 'GET', params },
  );
}

export async function restoreRecycledDataset(id: string) {
  return request<{
    success: boolean;
    data: {
      id: string;
      name: string;
      validUntil?: string;
      restoredTasks: number;
    };
  }>(`/api/v1/recycle-bin/datasets/${id}/restore`, { method: 'POST' });
}
