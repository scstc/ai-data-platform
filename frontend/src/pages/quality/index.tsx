import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import { Button, message, Popconfirm } from 'antd';
import { useRef, useState } from 'react';
import { batchDeleteJobs, deleteJob, listJobs } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { jobVersionColumns, renderState } from '@/utils/jobState';

/** 质量评估任务列表。任务详情/报告在独立页面(./report,?jobId= 定位),
 *  不再使用右侧抽屉。 */
const Quality: React.FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('assessment:quality:add');
  const canRemove = access.hasPerm('assessment:quality:remove');
  const canBatchRemove = access.hasPerm('assessment:quality:batch-remove');
  const actionRef = useRef<ActionType | null>(null);
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Job[]>([]);

  /** 删除单个质量任务(通用 /jobs/:id;后端只挡 running,前端对 running 隐藏入口)。 */
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

  /** 批量删除质量任务(后端跳过 running 的,返回实际删除数)。 */
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
            history.push(`/assessment/quality/report?jobId=${record.id}`);
          }}
        >
          {dom}
        </a>
      ),
    },
    // 与其他任务列表统一的 数据集/输入版本 两列;质量评估不产新版本,不显产物版本列
    ...jobVersionColumns().slice(0, 2),
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
      width: 120,
      render: (_, r) => [
        <a
          key="report"
          onClick={(e) => {
            e.preventDefault();
            history.push(`/assessment/quality/report?jobId=${r.id}`);
          }}
        >
          查看报告
        </a>,
        // 运行中的任务后端拒绝删除(需先停止);其余状态给删除入口
        ...(r.state === 'running' || !canRemove
          ? []
          : [
              <Popconfirm
                key="delete"
                title="删除该任务记录？"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(r.id)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>,
            ]),
      ],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.Job>
        headerTitle="质量评估任务"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true }}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Job[]),
        }}
        tableAlertOptionRender={() =>
          canBatchRemove ? (
            <Popconfirm
              title={`确认删除选中的 ${selectedRows.length} 个任务？`}
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={handleBatchDelete}
            >
              <Button type="link" danger>
                批量删除
              </Button>
            </Popconfirm>
          ) : null
        }
        request={async (params) => {
          const res = await listJobs({
            current: params.current,
            pageSize: params.pageSize,
            type: 'quality',
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
        toolBarRender={() =>
          canAdd
            ? [
                <Button
                  key="create"
                  type="primary"
                  onClick={() => history.push('/assessment/quality/editor')}
                >
                  新建质量评估
                </Button>,
              ]
            : []
        }
      />
    </PageContainer>
  );
};

export default Quality;
