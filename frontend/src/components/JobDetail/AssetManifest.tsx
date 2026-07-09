// 任务资产清单(数据集可溯源可复现整改 P1-②):汇总一个加工任务的可复现材料——
// 输入版本+成员、算子链+参数、LLM 快照、上游快照/数据源、产出版本。数据来自
// GET /api/v1/lineage?kind=job&jobId= 返回的该任务节点 + 完整输入链(湖/源已展开)。
import { Empty, Space, Spin, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { JobOps } from '@/pages/lineage/components/OperatorChips';
import { getLineageByAnchor } from '@/services/data-platform';

export interface AssetManifestProps {
  job: DataPlatform.Job;
}

const AssetManifest: React.FC<AssetManifestProps> = ({ job }) => {
  const [graph, setGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    getLineageByAnchor({ kind: 'job', jobId: job.id })
      .then((res) => setGraph(res.data))
      .catch(() => setGraph(undefined))
      .finally(() => setLoading(false));
  }, [job.id]);

  const jobNode = graph?.nodes.find((n) => n.id === job.id && n.kind === 'job');
  const versionById = new Map(
    (graph?.nodes ?? [])
      .filter((n) => n.kind === 'version')
      .map((n) => [n.id, n] as const),
  );
  const inputVersionIds = (graph?.edges ?? [])
    .filter((e) => e.kind === 'input' && e.to === job.id)
    .map((e) => e.from);
  const outputVersionIds = (graph?.edges ?? [])
    .filter((e) => e.kind === 'output' && e.from === job.id)
    .map((e) => e.to);
  const snapshotNodes = (graph?.nodes ?? []).filter(
    (n) => n.kind === 'lake_snapshot',
  );
  const datasourceNodes = (graph?.nodes ?? []).filter(
    (n) => n.kind === 'datasource',
  );

  return (
    <Spin spinning={loading}>
      {!graph && !loading ? (
        <Empty description="暂无血缘数据" />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div>
            <Typography.Title level={5}>输入版本 + 成员</Typography.Title>
            {inputVersionIds.length === 0 && (
              <Typography.Text type="secondary">
                无(从零合成 / 采集类任务)
              </Typography.Text>
            )}
            {inputVersionIds.map((vid) => {
              const v = versionById.get(vid);
              if (!v) return null;
              return (
                <div key={vid} style={{ marginBottom: 8 }}>
                  <Typography.Text strong>
                    {v.datasetName} · {v.versionLabel}
                  </Typography.Text>
                  {v.members && v.members.length > 0 && (
                    <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                      {v.members.map((m) => (
                        <li
                          key={m.tableName}
                          style={{
                            fontSize: 12,
                            color: 'var(--ant-color-text-secondary)',
                          }}
                        >
                          {m.tableName} · {m.rows ?? '-'} 行 · 来源：
                          {m.sourceName ??
                            m.sourceUploadChannel ??
                            '本任务加工'}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>

          <div>
            <Typography.Title level={5}>算子链 + 参数</Typography.Title>
            {jobNode ? (
              <JobOps n={jobNode} />
            ) : (
              <Typography.Text type="secondary">无算子记录</Typography.Text>
            )}
          </div>

          <div>
            <Typography.Title level={5}>LLM 快照</Typography.Title>
            {/* 后端把任务执行时的 model/base_url 固化进 job.spec.llm_snapshot(P0-①)
             *  并经血缘 job 节点的 llmSnapshot 出口暴露——resume 即按此端点/模型复现。
             *  老任务/无 LLM 算子的任务为 null,显示兜底文案。 */}
            {jobNode?.llmSnapshot ? (
              <Space size={4} wrap>
                <Tag color="geekblue">
                  模型：{jobNode.llmSnapshot.model ?? '-'}
                </Tag>
                <Tag>端点：{jobNode.llmSnapshot.baseUrl ?? '-'}</Tag>
              </Space>
            ) : (
              <Typography.Text type="secondary">
                未记录 / 老任务
              </Typography.Text>
            )}
          </div>

          <div>
            <Typography.Title level={5}>上游快照 / 数据源</Typography.Title>
            {snapshotNodes.length === 0 && datasourceNodes.length === 0 ? (
              <Typography.Text type="secondary">
                无(平台内加工产出，无外部湖/源直连)
              </Typography.Text>
            ) : (
              <Space size={4} wrap>
                {snapshotNodes.map((s) => (
                  <Tag key={s.id}>
                    {s.name}
                    {s.versionNo != null ? ` @v${s.versionNo}` : ''}
                  </Tag>
                ))}
                {datasourceNodes.map((d) => (
                  <Tag key={d.id} color="blue">
                    {d.name}
                  </Tag>
                ))}
              </Space>
            )}
          </div>

          <div>
            <Typography.Title level={5}>产出版本</Typography.Title>
            {outputVersionIds.length === 0 ? (
              <Typography.Text type="secondary">
                无(评估 / 审核类任务不产新版本)
              </Typography.Text>
            ) : (
              outputVersionIds.map((vid) => {
                const v = versionById.get(vid);
                return (
                  <Typography.Text key={vid} style={{ display: 'block' }}>
                    {v?.datasetName ?? job.output?.datasetName} ·{' '}
                    {v?.versionLabel ?? job.output?.versionLabel}
                  </Typography.Text>
                );
              })
            )}
          </div>
        </Space>
      )}
    </Spin>
  );
};

export default AssetManifest;
