import { PlusOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { Access, history, useAccess } from '@umijs/max';
import { Badge, Button, message, Popconfirm, Tag, Typography } from 'antd';
import { type FC, useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  deleteDataSource,
  downloadDatasource,
  exportDatasourceToS3,
  listBuckets,
  listCategories,
  listDataSources,
  recheckDataSource,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import { DB_KIND_LABEL, STATUS_META, TYPE_META } from './components/constants';

const DataSourcesPage: FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  // 正在「重新检测」的数据源 id(防重复点击 + 行内 loading 文案)
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  // 正在打开「导出到 S3」表单的数据源(单选:每行点击时设置,关闭时清空)
  const [exportDatasource, setExportDatasource] = useState<
    DataPlatform.DataSource | undefined
  >();

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryTreeData(toCategoryTreeData(res.data));
    } catch {
      // 静默：分类筛选不可用不应阻断列表
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  // 新建/编辑都走分类型配置页(/new/:type):编辑把整条记录经路由 state 传入回填,?id= 标记编辑态
  const openCreate = () => history.push('/ingest/datasources/new');

  const openEdit = (record: DataPlatform.DataSource) => {
    history.push(
      `/ingest/datasources/new/${record.type}?id=${encodeURIComponent(record.id)}`,
      { record },
    );
  };

  // 重新检测:按数据源当前配置真连一次并回写状态,结果即时反映到列表
  const handleRecheck = async (id: string) => {
    setRecheckingId(id);
    try {
      const res = await recheckDataSource(id, { skipErrorHandler: true });
      const meta = STATUS_META[res.data.status];
      if (res.data.status === 'connected') {
        message.success(`检测完成：${meta.label}`);
      } else {
        message.warning(`检测完成：${meta.label}`);
      }
      actionRef.current?.reload();
    } catch {
      message.error('检测失败，请重试');
    } finally {
      setRecheckingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await deleteDataSource(id);
      if (res.success) {
        message.success('数据源已删除');
        actionRef.current?.reload();
      } else {
        message.error('删除失败');
      }
    } catch {
      message.error('删除失败，请重试');
    }
  };

  // 下载/导出 S3:仅 s3 真正可用,其它类型按钮常驻但前置拦截给诚实文案
  // (与后端 400 消息保持一致;非 s3 类型后端会拒绝,前端拦截避免多走一次往返)。
  const unsupportedMsg = (record: DataPlatform.DataSource, action: string) =>
    `数据源类型「${record.type}」暂不支持${action}(当前仅 s3 完整支持;` +
    'hdfs/database/api 类型无文件语义或尚未接入)';

  // 下载:新窗口打开,浏览器自然跟随 302(单 s3 对象)或流式接 zip(多对象)。
  const handleDownload = (record: DataPlatform.DataSource) => {
    if (record.type !== 's3') {
      message.warning(unsupportedMsg(record, '下载'));
      return;
    }
    window.open(downloadDatasource(record.id), '_blank');
  };

  // 导出 S3:同下载前置拦截,非 s3 提前文案提示,避免打开空表单。
  const handleOpenExport = (record: DataPlatform.DataSource) => {
    if (record.type !== 's3') {
      message.warning(unsupportedMsg(record, '导出 S3'));
      return;
    }
    setExportDatasource(record);
  };

  const columns: ProColumns<DataPlatform.DataSource>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'type',
      valueType: 'select',
      valueEnum: {
        s3: { text: TYPE_META.s3.label },
        hdfs: { text: TYPE_META.hdfs.label },
        database: { text: TYPE_META.database.label },
        api: { text: TYPE_META.api.label },
      },
      render: (_, record) => {
        const meta = TYPE_META[record.type];
        const label =
          record.type === 'database' && record.dbKind
            ? `数据库 · ${DB_KIND_LABEL[record.dbKind] ?? record.dbKind}`
            : meta.label;
        return <Tag color={meta.color}>{label}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      search: false,
      render: (_, record) => {
        const meta = STATUS_META[record.status];
        return <Badge status={meta.status} text={meta.label} />;
      },
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'treeSelect',
      fieldProps: {
        treeData: categoryTreeData,
        allowClear: true,
        showSearch: true,
        treeNodeFilterProp: 'title',
        treeDefaultExpandAll: true,
      },
      render: (_, record) => record.categoryName || '-',
    },
    {
      title: '创建人',
      dataIndex: 'creator',
      search: false,
      width: 100,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      render: (_, r) => formatDateTime(r.createdAt),
      search: false,
      width: 180,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 280,
      // 重新检测/编辑/删除/下载/导出 S3 仅 admin 可见(后端 require_admin 双层防护);
      // 非 admin 此列为空。
      render: (_, record) =>
        access.canAdmin
          ? [
              // api 推送无在线探测语义,不显示「重新检测」
              record.type !== 'api' ? (
                <a
                  key="recheck"
                  style={
                    recheckingId === record.id
                      ? { pointerEvents: 'none', color: '#aaa' }
                      : undefined
                  }
                  onClick={() => handleRecheck(record.id)}
                >
                  {recheckingId === record.id ? '检测中…' : '重新检测'}
                </a>
              ) : null,
              <a key="download" onClick={() => handleDownload(record)}>
                下载
              </a>,
              <a key="export-s3" onClick={() => handleOpenExport(record)}>
                导出到 S3
              </a>,
              <a
                key="edit"
                onClick={() => {
                  openEdit(record);
                }}
              >
                编辑
              </a>,
              <Popconfirm
                key="delete"
                title="确认删除该数据源？"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record.id)}
              >
                <a style={{ color: '#ff4d4f' }}>删除</a>
              </Popconfirm>,
            ]
          : [<span key="readonly">-</span>],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.DataSource, DataPlatform.DataSourceListParams>
        headerTitle="数据源列表"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        toolBarRender={() => [
          <Access key="category" accessible={!!access.canAdmin}>
            <Button onClick={() => setCategoryOpen(true)}>分类管理</Button>
          </Access>,
          <Access key="create" accessible={!!access.canAdmin}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建数据源
            </Button>
          </Access>,
        ]}
        request={async (params) => {
          const { current, pageSize, name, type, categoryId } = params;
          const res = await listDataSources({
            current,
            pageSize,
            name,
            type,
            categoryId: categoryId || undefined,
          });
          return {
            data: res.data,
            total: res.total,
            success: res.success,
          };
        }}
        columns={columns}
      />
      <CategoryManager
        open={categoryOpen}
        canAdmin={!!access.canAdmin}
        onClose={() => setCategoryOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />
      <ModalForm<DataPlatform.ExportS3Params>
        title={`导出「${exportDatasource?.name ?? ''}」到 S3`}
        width={520}
        open={!!exportDatasource}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(o) => {
          if (!o) setExportDatasource(undefined);
        }}
        onFinish={async (values) => {
          if (!exportDatasource) return false;
          if (exportDatasource.type !== 's3') {
            message.warning(unsupportedMsg(exportDatasource, '导出 S3'));
            setExportDatasource(undefined);
            return false;
          }
          try {
            const res = await exportDatasourceToS3(exportDatasource.id, {
              datasourceId: values.datasourceId,
              bucket: values.bucket,
              prefix: values.prefix || undefined,
            });
            message.success(
              `已导出 ${res.data.exported} 个对象到 ${res.data.target}`,
            );
            setExportDatasource(undefined);
            return true;
          } catch (e: any) {
            const msg =
              e?.info?.errorMessage ||
              e?.response?.data?.message ||
              e?.data?.message;
            message.error(msg || '导出失败，请重试');
            return false;
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          把该数据源在 <code>config.bucket[/prefix]</code> 下的对象导出到目标 S3
          数据源(读源、写目标,绝不回写源)。
        </Typography.Paragraph>
        <ProFormSelect
          name="datasourceId"
          label="目标 S3 数据源"
          rules={[{ required: true, message: '请选择目标 S3 数据源' }]}
          request={async () => {
            const res = await listDataSources({ type: 's3', pageSize: 200 });
            // 同源自写(同 id)留给后端红线拦截,前端不必屏蔽——让用户看到清晰错误。
            return (res.data ?? []).map((d) => ({
              label: d.name,
              value: d.id,
            }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormSelect
          name="bucket"
          label="目标桶"
          rules={[{ required: true, message: '请选择目标桶' }]}
          dependencies={['datasourceId']}
          request={async (params) => {
            const dsId = (params as { datasourceId?: string }).datasourceId;
            if (!dsId) return [];
            const res = await listBuckets(dsId);
            return (res.data ?? []).map((b) => ({ label: b, value: b }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormText
          name="prefix"
          label="目标前缀(可选)"
          placeholder="如 exports/my-source；对象将落在「前缀/文件名」"
        />
      </ModalForm>
    </PageContainer>
  );
};

export default DataSourcesPage;
