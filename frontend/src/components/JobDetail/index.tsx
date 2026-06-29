import { ProDescriptions } from '@ant-design/pro-components';
import { Space, Typography } from 'antd';
import { formatDateTime } from '@/utils/format';
import { renderJobType, renderState } from '@/utils/jobState';
import VersionFilePreview from '../VersionFilePreview';

export interface JobDetailProps {
  job: DataPlatform.Job;
}

/** 任务详情统一展示:基础信息(ProDescriptions)+ 算子配置 + 数据集 + 输入/产物版本
 *  按文件预览。抽自数据任务(任务中心)详情抽屉,供 governance 各任务列表抽屉共用,
 *  保证治理/评估各页"点任务看详情"的样式一致。 */
const JobDetail: React.FC<JobDetailProps> = ({ job }) => {
  const inputVer = job.input;
  const outputVer = job.output;
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <ProDescriptions<DataPlatform.Job>
        column={2}
        dataSource={job}
        columns={[
          { title: '任务名', dataIndex: 'name' },
          {
            title: '类型',
            dataIndex: 'type',
            render: (_, r) => renderJobType(r.type),
          },
          {
            title: '状态',
            dataIndex: 'state',
            render: (_, r) => renderState(r.state),
          },
          {
            title: '进度',
            dataIndex: 'progress',
            render: (_, r) => `${r.progress ?? 0}%`,
          },
          {
            title: '创建时间',
            dataIndex: 'createdAt',
            render: (_, r) => formatDateTime(r.createdAt),
          },
          {
            title: '开始时间',
            dataIndex: 'startedAt',
            render: (_, r) => (r.startedAt ? formatDateTime(r.startedAt) : '-'),
          },
          {
            title: '结束时间',
            dataIndex: 'finishedAt',
            render: (_, r) =>
              r.finishedAt ? formatDateTime(r.finishedAt) : '-',
          },
          {
            title: '错误',
            dataIndex: 'error',
            span: 2,
            render: (_, r) =>
              r.error ? (
                <Typography.Text type="danger">{r.error}</Typography.Text>
              ) : (
                '-'
              ),
          },
        ]}
      />

      {job.configYaml && (
        <div>
          <Typography.Title level={5}>算子配置</Typography.Title>
          <pre
            style={{
              background: 'var(--ant-color-fill-quaternary, #f5f5f5)',
              padding: 12,
              borderRadius: 6,
              overflow: 'auto',
              fontSize: 12,
              margin: 0,
            }}
          >
            {job.configYaml}
          </pre>
        </div>
      )}

      {(inputVer || outputVer) && (
        <Typography.Text strong>
          数据集：{inputVer?.datasetName ?? outputVer?.datasetName ?? '-'}
        </Typography.Text>
      )}

      <div>
        <Typography.Title level={5}>输入版本</Typography.Title>
        {inputVer ? (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            {inputVer.versionLabel ?? `v${inputVer.versionNo}`}
          </Typography.Paragraph>
        ) : null}
        <VersionFilePreview
          versionId={inputVer?.versionId}
          emptyText="无输入版本"
        />
      </div>

      {outputVer && (
        <div>
          <Typography.Title level={5}>产物版本</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            {outputVer.versionLabel ?? `v${outputVer.versionNo}`}
          </Typography.Paragraph>
          <VersionFilePreview versionId={outputVer.versionId} />
        </div>
      )}
    </Space>
  );
};

export default JobDetail;
