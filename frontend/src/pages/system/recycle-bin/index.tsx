import {
  type ActionType,
  PageContainer,
  type ProColumns,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { App, Button, Popconfirm, Tag, Typography } from 'antd';
import { type FC, useRef } from 'react';
import {
  listRecycledDatasets,
  type RecycledDataset,
  restoreRecycledDataset,
} from './service';

const { Text } = Typography;

/** 回收站(仅超管):数据集过期后自动打删除标记并级联隐藏关联任务,在此恢复。
 *  恢复 = 清删除标记 + 有效期续到当前时间 +1 自然月 + 级联恢复因它隐藏的任务。 */
const RecycleBinPage: FC = () => {
  const actionRef = useRef<ActionType>(null);
  const access = useAccess();
  const { message } = App.useApp();

  const columns: ProColumns<RecycledDataset>[] = [
    { title: '数据集名称', dataIndex: 'name', ellipsis: true },
    { title: '归属人', dataIndex: 'owner', width: 120, search: false },
    {
      title: '有效期至',
      dataIndex: 'validUntil',
      valueType: 'dateTime',
      width: 160,
      search: false,
    },
    {
      title: '删除时间',
      dataIndex: 'deletedAt',
      valueType: 'dateTime',
      width: 160,
      search: false,
    },
    {
      title: '原因',
      dataIndex: 'deletedReason',
      width: 100,
      search: false,
      render: (_, r) =>
        r.deletedReason === 'expired' ? (
          <Tag color="orange">已过期</Tag>
        ) : (
          <Tag>{r.deletedReason ?? '-'}</Tag>
        ),
    },
    {
      title: '级联隐藏任务',
      width: 140,
      search: false,
      render: (_, r) => (
        <Text type="secondary">
          加工 {r.cascadedJobs} / 采集 {r.cascadedIngestTasks}
        </Text>
      ),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 100,
      render: (_, r) =>
        access.hasPerm('system:recycle:restore')
          ? [
              <Popconfirm
                key="restore"
                title={`恢复数据集「${r.name}」?`}
                description="将清除删除标记、有效期顺延 1 个自然月,并恢复因它隐藏的关联任务。"
                onConfirm={async () => {
                  const res = await restoreRecycledDataset(r.id);
                  if (res.success) {
                    message.success(
                      `已恢复「${res.data.name}」,级联恢复任务 ${res.data.restoredTasks} 个`,
                    );
                    actionRef.current?.reload();
                  }
                }}
              >
                <Button type="link" size="small">
                  恢复
                </Button>
              </Popconfirm>,
            ]
          : [],
    },
  ];

  return (
    <PageContainer
      title="数据集回收站"
      content="数据集过期后自动进入回收站并对所有用户隐藏(含关联任务);恢复后有效期自动顺延 1 个自然月。"
    >
      <ProTable<RecycledDataset>
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        search={{ filterType: 'light' }}
        request={async (params) => {
          const res = await listRecycledDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name,
          });
          return {
            data: res.data ?? [],
            total: res.total ?? 0,
            success: res.success,
          };
        }}
      />
    </PageContainer>
  );
};

export default RecycleBinPage;
