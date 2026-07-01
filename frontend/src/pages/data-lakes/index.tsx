import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { Access, history, useAccess } from '@umijs/max';
import { Button, message, Popconfirm } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef, useState } from 'react';
import {
  batchDeleteDataLakes,
  createDataLake,
  deleteDataLake,
  listDataLakes,
} from '@/services/data-platform';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as {
    response?: { data?: { detail?: string; message?: string } };
  };
  return e?.response?.data?.detail ?? e?.response?.data?.message ?? fallback;
};

/** 数据源类型 → 显示标签 + 颜色。 */
/**
 * 数据湖列表页 —— 湖集分离架构的 ODS 原始数据层入口。
 *
 * 数据湖是多源汇聚的容器:一个湖可承接数据库/对象存储/HDFS/本地/API 多种来源
 * 的快照,类型和来源属于每一次接入(快照层),不属于湖本身。
 * 见 docs/数据治理.md、docs/data-lake-implementation.md。
 */
const DataLakesPage: FC = () => {
  const access = useAccess();
  const canAdmin = !!access.canAdmin;
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedRows, setSelectedRows] = useState<DataPlatform.DataLake[]>([]);

  const reload = () => actionRef.current?.reload();
  const selectedRowKeys = selectedRows.map((r) => r.id);

  const handleBatchDelete = async () => {
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteDataLakes(selectedRowKeys);
      hide();
      message.success(
        `已删除 ${res?.data?.deleted ?? selectedRowKeys.length} 个数据湖`,
      );
      setSelectedRows([]);
      reload();
    } catch {
      hide();
      message.error('批量删除失败,请重试');
    }
  };

  const columns: ProColumns<DataPlatform.DataLake>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, r) => (
        <a onClick={() => history.push(`/data-lakes/${r.id}`)}>{r.name}</a>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      ellipsis: true,
      search: false,
      render: (_, r) => r.description ?? '-',
    },
    {
      title: '创建人',
      dataIndex: 'creator',
      width: 100,
      search: false,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 168,
      search: false,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 160,
      render: (_text, record) => [
        <a
          key="detail"
          onClick={() => history.push(`/data-lakes/${record.id}`)}
        >
          详情
        </a>,
        canAdmin ? (
          <Popconfirm
            key="delete"
            title="删除数据湖?"
            description="仅删元数据,物理文件保留。"
            onConfirm={async () => {
              try {
                await deleteDataLake(record.id);
                message.success('已删除');
                reload();
              } catch (err) {
                message.error(pickErrMsg(err, '删除失败'));
              }
            }}
          >
            <a style={{ color: '#ff4d4f' }}>删除</a>
          </Popconfirm>
        ) : null,
      ],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.DataLake>
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        headerTitle="数据湖列表"
        search={{ labelWidth: 90 }}
        rowSelection={
          canAdmin
            ? {
                selectedRowKeys,
                onChange: (_keys, rows) =>
                  setSelectedRows(rows as DataPlatform.DataLake[]),
              }
            : undefined
        }
        tableAlertOptionRender={() => (
          <Access accessible={canAdmin}>
            <Popconfirm
              title={`确认删除选中的 ${selectedRowKeys.length} 个数据湖?`}
              description="仅删元数据,快照物理文件保留。不可恢复。"
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={handleBatchDelete}
            >
              <Button type="link" danger>
                批量删除
              </Button>
            </Popconfirm>
          </Access>
        )}
        request={async (params) => {
          const { current, pageSize, name } = params;
          const res = await listDataLakes({
            page: current,
            pageSize,
            name,
          });
          return {
            data: res.data ?? [],
            total: res.total ?? 0,
            success: res.success ?? true,
          };
        }}
        toolBarRender={() =>
          canAdmin
            ? [
                <Button
                  key="new"
                  type="primary"
                  onClick={() => setCreateOpen(true)}
                >
                  新建数据湖
                </Button>,
              ]
            : []
        }
      />

      <ModalForm<DataPlatform.DataLakeCreate>
        title="新建数据湖"
        open={createOpen}
        onOpenChange={setCreateOpen}
        width={520}
        modalProps={{ destroyOnHidden: true }}
        onFinish={async (values) => {
          try {
            await createDataLake(values);
            message.success('已创建');
            reload();
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '创建失败'));
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="名称"
          rules={[{ required: true, message: '请填写名称' }]}
          placeholder="如:财务系统数据湖、用户行为日志湖"
        />
        <ProFormTextArea
          name="description"
          label="描述"
          placeholder="选填,说明该数据湖的用途。同一湖可承接数据库/对象存储/HDFS/本地/API 多种来源"
          fieldProps={{ rows: 3 }}
        />
      </ModalForm>
    </PageContainer>
  );
};

export default DataLakesPage;
