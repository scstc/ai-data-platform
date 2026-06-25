import {
  PageContainer,
  type ProColumns,
  ProTable,
} from '@ant-design/pro-components';
import { Card, Space, Tag, Typography } from 'antd';
import { type FC, useRef } from 'react';
import { permissionOverview } from '@/services/system';

const { Text } = Typography;

type PermRow = {
  key: string;
  name: string;
  roleKey: string;
  perms: string[];
};

/** 权限管理:角色 × 权限授权总览(只读)。
 *  权限本体挂在「菜单管理」的 perms 上,授权在「角色管理-分配菜单」;本页只读呈现。 */
const PermissionPage: FC = () => {
  const allPermsRef = useRef<string[]>([]);

  const columns: ProColumns<PermRow>[] = [
    { title: '角色', dataIndex: 'name', width: 160 },
    { title: '标识', dataIndex: 'roleKey', width: 160 },
    {
      title: '授权权限',
      dataIndex: 'perms',
      render: (_, r) =>
        r.perms.length === 0 ? (
          <Text type="secondary">无</Text>
        ) : (
          <Space wrap size={[4, 4]}>
            {r.perms.map((p) => (
              <Tag key={p} color={p === '*:*:*' ? 'red' : 'blue'}>
                {p}
              </Tag>
            ))}
          </Space>
        ),
    },
  ];

  return (
    <PageContainer
      title="权限管理"
      content="角色 × 权限授权总览。权限标识在「菜单管理」维护;给角色授权在「角色管理 → 分配菜单」。"
    >
      <Card style={{ marginBottom: 16 }}>
        <ProTable<PermRow>
          rowKey="key"
          search={false}
          pagination={false}
          options={{ reload: true, density: false, setting: false }}
          request={async () => {
            const res = await permissionOverview();
            allPermsRef.current = res.data?.allPerms ?? [];
            const roles = res.data?.roles ?? [];
            return {
              data: roles.map((r) => ({
                key: r.id,
                name: r.name,
                roleKey: r.roleKey,
                perms: r.perms,
              })),
              success: res.success,
            };
          }}
          columns={columns}
        />
      </Card>
      <Card title="全量权限码目录">
        <Catalog />
      </Card>
    </PageContainer>
  );
};

/** 全量权限码目录(独立一次 overview 取 allPerms)。 */
const Catalog: FC = () => {
  return (
    <ProTable<{ key: string; label: string }>
      rowKey="key"
      search={false}
      pagination={false}
      options={false}
      toolBarRender={false}
      request={async () => {
        const res = await permissionOverview();
        const all = res.data?.allPerms ?? [];
        return {
          data: all.map((p) => ({ key: p, label: p })),
          success: res.success,
        };
      }}
      columns={[
        {
          title: '权限码',
          dataIndex: 'label',
          render: (_, r) => <Tag>{r.label}</Tag>,
        },
      ]}
    />
  );
};

export default PermissionPage;
