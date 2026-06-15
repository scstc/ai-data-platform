import type { ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Alert, Tag } from 'antd';
import dayjs from 'dayjs';
import type React from 'react';
import { listAuditLogs } from '@/services/data-platform';

/** 写操作方法枚举（搜索下拉；读请求不记审计，故不含 GET） */
const METHOD_ENUM = {
  POST: { text: 'POST' },
  PUT: { text: 'PUT' },
  PATCH: { text: 'PATCH' },
  DELETE: { text: 'DELETE' },
};

/** HTTP 状态码配色：2xx 成功 / 4xx 客户端错误（含 403 越权拦截）/ 5xx 服务端错误 */
const statusColor = (code: number) => {
  if (code >= 500) return 'error';
  if (code >= 400) return 'warning';
  if (code >= 200 && code < 300) return 'success';
  return 'default';
};

const Security: React.FC = () => {
  const columns: ProColumns<DataPlatform.AuditLog>[] = [
    {
      title: '时间',
      dataIndex: 'createdAt',
      search: false,
      width: 180,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      // 仅作搜索条件用的时间区间（不在表格中展示列）
      title: '时间',
      dataIndex: 'createdAt',
      key: 'createdAtRange',
      valueType: 'dateRange',
      hideInTable: true,
    },
    { title: '用户', dataIndex: 'username', width: 120 },
    { title: '动作', dataIndex: 'action', width: 160 },
    {
      title: '方法',
      dataIndex: 'method',
      valueType: 'select',
      valueEnum: METHOD_ENUM,
      width: 110,
      render: (_, r) => <Tag>{r.method}</Tag>,
    },
    { title: '路径', dataIndex: 'path', search: false, ellipsis: true },
    {
      title: '对象',
      dataIndex: 'target',
      search: false,
      ellipsis: true,
      render: (_, r) => r.target ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'statusCode',
      search: false,
      width: 90,
      render: (_, r) => (
        <Tag color={statusColor(r.statusCode)}>{r.statusCode}</Tag>
      ),
    },
  ];

  return (
    <PageContainer>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="仅管理员可见，记录所有写操作（新建 / 编辑 / 删除）。"
      />
      <ProTable<DataPlatform.AuditLog>
        headerTitle="操作审计日志"
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        options={{ reload: true }}
        request={async (params) => {
          const range = params.createdAt as [string, string] | undefined;
          const res = await listAuditLogs({
            current: params.current,
            pageSize: params.pageSize,
            username: params.username || undefined,
            action: params.action || undefined,
            method: params.method || undefined,
            // dateRange 给的是纯日期:起取当日 0 点、止取当日 23:59:59,
            // 否则会漏掉当天的记录(与数据集列表一致)
            createdStart: range?.[0]
              ? dayjs(range[0]).startOf('day').toISOString()
              : undefined,
            createdEnd: range?.[1]
              ? dayjs(range[1]).endOf('day').toISOString()
              : undefined,
          });
          // 倒序由后端保证
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />
    </PageContainer>
  );
};

export default Security;
