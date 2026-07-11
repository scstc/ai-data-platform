import { ProDescriptions } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Alert, Button, Space, Tabs, Typography } from 'antd';
import { formatDateTime } from '@/utils/format';
import { renderJobType, renderState } from '@/utils/jobState';
import VersionFilePreview from '../VersionFilePreview';
import AssetManifest from './AssetManifest';

export interface JobDetailProps {
  job: DataPlatform.Job;
}

/** 成功任务的「下一步」动作条:按任务类型给出承接动作,把用户从"看完详情"顺畅
 *  带到下一环节(评估产物 / 查看新版本 / 看报告),避免回列表手动找入口。
 *  - clean(数据加工/清洗):[评估新版本][查看新版本]
 *  - quality(质量评估):[查看报告](报告页按 jobId 定位)
 *  - synthesis(数据合并)/ trainset(数据合成):[查看产物版本][发起评估]
 *  评估/查看均深链到对应新建页/详情页并预选产物数据集+版本(编辑器支持 query 预选)。 */
const NextStepBar: React.FC<{ job: DataPlatform.Job }> = ({ job }) => {
  if (job.state !== 'success') return null;
  const out = job.output;
  const evalBtn = out ? (
    <Button
      key="eval"
      type="primary"
      onClick={() =>
        history.push(
          `/assessment/quality/editor?datasetId=${out.datasetId}&versionId=${out.versionId}`,
        )
      }
    >
      {job.type === 'clean' ? '评估新版本' : '发起评估'}
    </Button>
  ) : null;
  const viewBtn = out ? (
    <Button
      key="view"
      onClick={() =>
        history.push(`/datasets/${out.datasetId}?version=${out.versionId}`)
      }
    >
      {job.type === 'clean' ? '查看新版本' : '查看产物版本'}
    </Button>
  ) : null;

  let actions: React.ReactNode[] = [];
  if (job.type === 'quality') {
    actions = [
      <Button
        key="report"
        type="primary"
        onClick={() =>
          history.push(`/assessment/quality/report?jobId=${job.id}`)
        }
      >
        查看报告
      </Button>,
    ];
  } else if (job.type === 'clean') {
    actions = [evalBtn, viewBtn];
  } else if (job.type === 'synthesis' || job.type === 'trainset') {
    actions = [viewBtn, evalBtn];
  }
  const filtered = actions.filter(Boolean);
  if (filtered.length === 0) return null;
  return (
    <Alert
      type="success"
      showIcon
      message="任务已完成,继续下一步"
      description={<Space wrap>{filtered}</Space>}
    />
  );
};

/** 概览:基础信息(ProDescriptions)+ 算子配置 + 数据集 + 输入/产物版本按文件预览。
 *  原 JobDetail 的全部内容,现作为「概览」Tab 与「资产清单」Tab 并列。 */
const JobOverview: React.FC<{ job: DataPlatform.Job }> = ({ job }) => {
  const inputVer = job.input;
  const outputVer = job.output;
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <NextStepBar job={job} />

      {(job.warnings?.length ?? 0) > 0 && (
        <Alert
          type="warning"
          showIcon
          message="运行告警"
          description={
            <ul style={{ margin: 0, paddingInlineStart: 20 }}>
              {job.warnings?.map((w, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: warnings 为无 id 的纯文本行
                <li key={i}>{w}</li>
              ))}
            </ul>
          }
        />
      )}

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

/** 任务详情统一展示:概览 + 资产清单(可溯源可复现整改 P1-②:算子链参数/LLM 快照/
 *  上游快照数据源全链路汇总)两个 Tab。抽自数据任务(任务中心)详情抽屉,供 governance
 *  各任务列表抽屉共用,保证治理/评估各页"点任务看详情"的样式一致。 */
const JobDetail: React.FC<JobDetailProps> = ({ job }) => (
  <Tabs
    items={[
      { key: 'overview', label: '概览', children: <JobOverview job={job} /> },
      {
        key: 'assets',
        label: '资产清单',
        children: <AssetManifest job={job} />,
      },
    ]}
  />
);

export default JobDetail;
