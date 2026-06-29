import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Drawer, message, Popconfirm, Tag } from 'antd';
import { useRef, useState } from 'react';
import { JobDetail } from '@/components';
import {
  batchDeleteJobs,
  deleteJob,
  listJobs,
  rerunJob,
  stopJob,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { jobVersionColumns, renderState } from '@/utils/jobState';

/** 清洗任务列表(可按 jobType 过滤)。数据清洗=clean。
 *  作为通用列表组件保留,cleaning/index.tsx 传 jobType="clean" 复用。 */
const Processing: React.FC<{
  jobType?: string;
  title?: string;
  createHref?: string;
}> = ({
  jobType = 'clean',
  title = '数据清洗任务',
  createHref = '/governance/cleaning/editor',
}) => {
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);
  // 有任务在跑/排队时自动轮询刷新状态(后台执行,进度异步推进)
  const [polling, setPolling] = useState<number | undefined>(undefined);

  /** 重跑:用原配置对原输入版本再跑一次,产出新版本(同步执行,完成后刷新列表) */
  const handleRerun = async (id: string) => {
    const hide = message.loading('正在重新运行…', 0);
    try {
      await rerunJob(id);
      hide();
      message.success('已重新运行，产出新版本');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('重新运行失败，请重试');
    }
  };

  /** 删除:只删任务记录,产出的数据集版本保留(有独立删除入口) */
  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteJob(id);
      hide();
      message.success('任务已删除');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  /** 停止:杀子进程并把任务标记为 cancelled(已产出的不受影响) */
  const handleStop = async (id: string) => {
    const hide = message.loading('正在停止…', 0);
    try {
      await stopJob(id);
      hide();
      message.success('任务已停止');
      actionRef.current?.reload();
    } catch (e: any) {
      hide();
      message.error(
        e?.response?.data?.message || e?.data?.message || '停止失败，请重试',
      );
    }
  };

  /** 批量删除:运行中/不存在的由后端跳过,产物版本保留 */
  const handleBatchDelete = async () => {
    const ids = selectedRows.map((r) => r.id);
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteJobs(ids);
      hide();
      message.success(`已删除 ${res?.data?.deleted ?? ids.length} 个任务`);
      setSelectedRows([]);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('批量删除失败，请重试');
    }
  };

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
    ...jobVersionColumns(),
    {
      title: '状态',
      dataIndex: 'state',
      render: (_, r) => renderState(r.state),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      render: (_, r) => {
        // 运行中/排队中:只给「停止」(删除会被后端 409,重跑无意义)
        if (r.state === 'running' || r.state === 'pending') {
          return [
            <Popconfirm
              key="stop"
              title="停止该任务？已产出的内容不受影响。"
              okText="停止"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleStop(r.id)}
            >
              <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>停止</a>
            </Popconfirm>,
          ];
        }
        // 终态:可重跑 + 可删除
        const actions = [];
        if (r.canRerun) {
          actions.push(
            <Popconfirm
              key="rerun"
              title="用原配置对原输入版本重新运行，产出新版本？"
              onConfirm={() => handleRerun(r.id)}
            >
              <a>重新运行</a>
            </Popconfirm>,
          );
        }
        actions.push(
          <Popconfirm
            key="delete"
            title="删除该任务记录？产出的数据集版本会保留。"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(r.id)}
          >
            <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
          </Popconfirm>,
        );
        return actions;
      },
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Job>
        headerTitle={title}
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true }}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Job[]),
        }}
        tableAlertOptionRender={() => (
          <Popconfirm
            title={`确认删除选中的 ${selectedRows.length} 个任务？`}
            description="只删任务记录，产出的数据集版本会保留。"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={handleBatchDelete}
          >
            <Button type="link" danger>
              批量删除
            </Button>
          </Popconfirm>
        )}
        polling={polling}
        request={async (params) => {
          const res = await listJobs({
            current: params.current,
            pageSize: params.pageSize,
            type: jobType,
          });
          // 有任务在跑/排队 → 每 3s 轮询;全部终态 → 停止轮询
          const active = res.data?.some(
            (j) => j.state === 'running' || j.state === 'pending',
          );
          setPolling(active ? 3000 : undefined);
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
        toolBarRender={() => [
          <Button
            type="primary"
            key="new"
            onClick={() => history.push(createHref)}
          >
            新建任务
          </Button>,
        ]}
      />

      <Drawer
        width={960}
        open={detailOpen}
        title={currentJob?.name}
        onClose={() => {
          setDetailOpen(false);
          setCurrentJob(undefined);
        }}
      >
        {currentJob && <JobDetail job={currentJob} />}
      </Drawer>
    </PageContainer>
  );
};

export default Processing;
