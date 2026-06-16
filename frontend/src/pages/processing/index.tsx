import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  PageContainer,
  ProDescriptions,
  ProTable,
} from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Drawer, Tag, Typography } from 'antd';
import { useRef, useState } from 'react';
import { listJobs } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

const STATE_META: Record<
  DataPlatform.Job['state'],
  { text: string; color: string }
> = {
  pending: { text: '待运行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
};

const renderOutput = (o?: DataPlatform.IngestOutput) =>
  o
    ? `${o.datasetName}（${o.rows ?? '-'} 行 · ${o.datasetId} ${o.versionLabel ?? `v${o.versionNo}`}）`
    : '-';

const Processing: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();

  const columns: ProColumns<DataPlatform.Job>[] = [
    {
      title: '任务名',
      dataIndex: 'name',
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            setCurrentJob(record);
            setDetailOpen(true);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      render: (_, r) => <Tag>{r.type}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'state',
      render: (_, r) => {
        const m = STATE_META[r.state];
        return <Tag color={m.color}>{m.text}</Tag>;
      },
    },
    {
      title: '产物数据集',
      dataIndex: 'output',
      render: (_, r) => renderOutput(r.output),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      render: (_, r) => formatDateTime(r.createdAt),
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Job>
        headerTitle="数据加工任务"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true }}
        request={async (params) => {
          const res = await listJobs({
            current: params.current,
            pageSize: params.pageSize,
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
        toolBarRender={() => [
          <Button
            type="primary"
            key="new"
            onClick={() => history.push('/processing/editor')}
          >
            新建任务
          </Button>,
        ]}
      />

      <Drawer
        width={640}
        open={detailOpen}
        title={currentJob?.name}
        onClose={() => {
          setDetailOpen(false);
          setCurrentJob(undefined);
        }}
      >
        {currentJob && (
          <>
            <ProDescriptions<DataPlatform.Job>
              column={1}
              dataSource={currentJob}
              columns={[
                { title: '任务名', dataIndex: 'name' },
                { title: '类型', dataIndex: 'type' },
                {
                  title: '状态',
                  dataIndex: 'state',
                  render: (_, r) => {
                    const m = STATE_META[r.state];
                    return <Tag color={m.color}>{m.text}</Tag>;
                  },
                },
                {
                  title: '产物数据集',
                  dataIndex: 'output',
                  render: (_, r) => renderOutput(r.output),
                },
                {
                  title: '创建时间',
                  dataIndex: 'createdAt',
                  render: (_, r) => formatDateTime(r.createdAt),
                },
                {
                  title: '错误',
                  dataIndex: 'error',
                  render: (_, r) =>
                    r.error ? (
                      <Typography.Text type="danger">{r.error}</Typography.Text>
                    ) : (
                      '-'
                    ),
                },
              ]}
            />
            {currentJob.configYaml && (
              <>
                <Typography.Title level={5} style={{ marginTop: 16 }}>
                  算子配置（生成的 data-juicer YAML）
                </Typography.Title>
                <Typography.Paragraph>
                  <pre
                    style={{
                      background: 'var(--ant-color-fill-quaternary, #f5f5f5)',
                      padding: 12,
                      borderRadius: 6,
                      overflow: 'auto',
                      fontSize: 12,
                    }}
                  >
                    {currentJob.configYaml}
                  </pre>
                </Typography.Paragraph>
              </>
            )}
          </>
        )}
      </Drawer>
    </PageContainer>
  );
};

export default Processing;
