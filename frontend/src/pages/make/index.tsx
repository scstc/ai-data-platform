// 数据合成任务列表页:复用 distillation 的 ProTable + 报告缓存模式
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Drawer, message, Popconfirm, Tag, Typography } from 'antd';
import { useRef, useState } from 'react';
import { JobDetail } from '@/components';
import {
  batchDeleteMakeJobs,
  deleteMakeJob,
  getMakeReport,
  listMakeJobs,
  rerunMakeJob,
  stopMakeJob,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { jobVersionColumns, renderState } from '@/utils/jobState';
import MakeReportModal from './MakeReportModal';

const Make: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentJob, setCurrentJob] = useState<DataPlatform.Job>();
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);
  const [reportJobId, setReportJobId] = useState<string>();
  const [reportCache, setReportCache] = useState<
    Record<string, DataPlatform.MakeReport>
  >({});
  const [polling, setPolling] = useState<number | undefined>(undefined);

  const openReport = async (jobId: string) => {
    if (reportCache[jobId]) {
      setReportJobId(jobId);
      return;
    }
    try {
      const res = await getMakeReport(jobId);
      if (res.data) {
        setReportCache((prev) => ({ ...prev, [jobId]: res.data }));
        setReportJobId(jobId);
      }
    } catch (e: any) {
      const msg = e?.info?.errorMessage || e?.data?.message || '读取报告失败';
      message.error(msg);
    }
  };

  const reloadReport = async (jobId: string) => {
    const res = await getMakeReport(jobId).catch(() => undefined);
    if (res?.data) setReportCache((prev) => ({ ...prev, [jobId]: res.data }));
  };

  const handleRerun = async (id: string) => {
    const hide = message.loading('正在重新运行…', 0);
    try {
      await rerunMakeJob(id);
      hide();
      message.success('已重新运行，产出新版本');
      setReportCache((prev) => {
        const n = { ...prev };
        delete n[id];
        return n;
      });
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('重新运行失败，请重试');
    }
  };

  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteMakeJob(id);
      hide();
      message.success('任务已删除');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  const handleStop = async (id: string) => {
    const hide = message.loading('正在停止…', 0);
    try {
      await stopMakeJob(id);
      hide();
      message.success('任务已停止');
      actionRef.current?.reload();
    } catch (e: any) {
      hide();
      message.error(
        e?.info?.errorMessage || e?.data?.message || '停止失败，请重试',
      );
    }
  };

  const handleBatchDelete = async () => {
    const ids = selectedRows.map((r) => r.id);
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteMakeJobs(ids);
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
    ...jobVersionColumns(),
    {
      title: '状态',
      dataIndex: 'state',
      width: 90,
      render: (_, r) => renderState(r.state),
    },
    {
      title: '扩增比',
      dataIndex: 'progress',
      width: 160,
      render: (_, r) => {
        if (r.state === 'success') {
          const report = reportCache[r.id];
          if (report?.expansionRatio != null) {
            const ratio = report.expansionRatio.toFixed(2);
            return (
              <span>
                <Tag color="purple">{ratio}x</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {report.outputCount}/{report.inputCount}
                </Typography.Text>
              </span>
            );
          }
          return <a onClick={() => openReport(r.id)}>查看报告</a>;
        }
        if (r.state === 'running') return <Tag color="processing">进行中</Tag>;
        return '-';
      },
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 220,
      render: (_, r) => {
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
        const actions: React.ReactNode[] = [];
        if (r.state === 'success') {
          actions.push(
            <a key="report" onClick={() => openReport(r.id)}>
              查看报告
            </a>,
          );
        }
        if (r.canRerun) {
          actions.push(
            <Popconfirm
              key="rerun"
              title="用原配置重新运行，产出新版本？"
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
        headerTitle="数据合成任务"
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
          const res = await listMakeJobs({
            current: params.current,
            pageSize: params.pageSize,
          });
          const active = res.data?.some(
            (j) => j.state === 'running' || j.state === 'pending',
          );
          setPolling(active ? 3000 : undefined);
          const successIds = (res.data ?? [])
            .filter((j) => j.state === 'success')
            .map((j) => j.id);
          void Promise.all(successIds.map((id) => reloadReport(id)));
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
        toolBarRender={() => [
          <Button
            type="primary"
            key="new"
            onClick={() => history.push('/governance/make/editor')}
          >
            新建合成
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

      <MakeReportModal
        open={!!reportJobId}
        jobId={reportJobId}
        report={reportJobId ? reportCache[reportJobId] : undefined}
        onClose={() => setReportJobId(undefined)}
      />
    </PageContainer>
  );
};

export default Make;
