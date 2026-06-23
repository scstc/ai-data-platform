import { AppstoreAddOutlined, UploadOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Input,
  Layout,
  Menu,
  message,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  TreeSelect,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import CategoryManager from '@/components/CategoryManager';
import {
  deleteDataset,
  getDataset,
  listCategories,
  listDatasets,
  previewDatasetVersion,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import { ACCESS_TYPES, acceptOf } from './constants';
import MediaMembersDrawer from './MediaMembersDrawer';
import UploadModal from './UploadModal';

const { Sider, Content } = Layout;

/** 单元格值渲染:对象转 JSON,其余转字符串(与文件管理预览一致) */
const cellText = (v: unknown) =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

const AccessPage: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [typeKey, setTypeKey] = useState<string>(ACCESS_TYPES[0].key);
  const [keyword, setKeyword] = useState<string>();
  const [categoryId, setCategoryId] = useState<string>();
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  const [uploadOpen, setUploadOpen] = useState(false);
  const [catOpen, setCatOpen] = useState(false);
  // 预览抽屉
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewName, setPreviewName] = useState<string>();
  // 媒体集成员文件抽屉
  const [membersOpen, setMembersOpen] = useState(false);
  const [membersVersionId, setMembersVersionId] = useState<string>();
  const [membersDatasetId, setMembersDatasetId] = useState<string>();
  const [membersName, setMembersName] = useState<string>();
  const [membersEditable, setMembersEditable] = useState(false);

  // typeKey 恒为有效栏 key,这里仍做兜底避免任何来源扩展导致的运行时崩溃
  const accessType =
    ACCESS_TYPES.find((t) => t.key === typeKey) ?? ACCESS_TYPES[0];

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryTreeData(toCategoryTreeData(res.data));
    } catch {
      // 分类为可选筛选项,加载失败不阻断接入主流程(静默降级)
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  const handleDelete = async (id: string) => {
    try {
      await deleteDataset(id);
      message.success('删除成功');
      actionRef.current?.reload();
    } catch {
      message.error('删除失败,请重试');
    }
  };

  /** 预览数据集最新版本(文本类);二进制类不调用(按钮置灰) */
  const handlePreview = async (r: DataPlatform.Dataset) => {
    setPreviewName(r.name);
    setPreviewOpen(true);
    setPreview(undefined);
    setPreviewLoading(true);
    try {
      const detail = await getDataset(r.id);
      const versionId = detail.data.versions?.[0]?.id;
      if (!versionId) {
        setPreview({
          columns: [],
          data: [],
          total: 0,
          success: true,
          message: '该数据集暂无版本',
        });
        return;
      }
      const res = await previewDatasetVersion(versionId, { limit: 50 });
      setPreview(res);
    } catch {
      setPreview({
        columns: [],
        data: [],
        total: 0,
        success: false,
        message: '预览失败,文件可能无法解析',
      });
    } finally {
      setPreviewLoading(false);
    }
  };

  // 打开成员文件抽屉:取数据集首版本 id → 列成员并预览
  const openMembers = async (r: DataPlatform.Dataset) => {
    setMembersName(r.name);
    setMembersDatasetId(r.id);
    setMembersVersionId(undefined);
    setMembersEditable(false);
    setMembersOpen(true);
    try {
      const detail = await getDataset(r.id);
      const v = detail.data.versions?.[0];
      setMembersVersionId(v?.id);
      // 仅 manifest 媒体集可增删成员(平台自有);其它(单文件托管等)只读
      setMembersEditable(v?.format === 'manifest');
    } catch {
      // 抽屉内自行兜底为空
    }
  };

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    { title: '数据集名称', dataIndex: 'name', ellipsis: true },
    {
      title: '分类',
      dataIndex: 'categoryName',
      width: 140,
      render: (_, r) => (r.categoryName ? <Tag>{r.categoryName}</Tag> : '-'),
    },
    {
      title: '来源',
      width: 130,
      render: (_, r) =>
        r.hosted ? <Tag color="gold">文件管理引用</Tag> : <Tag>本地上传</Tag>,
    },
    {
      title: '类型',
      dataIndex: 'dataType',
      width: 110,
      render: (_, r) =>
        r.dataType ? <Tag color="blue">{r.dataType}</Tag> : '-',
    },
    { title: '创建人', dataIndex: 'creator', width: 100 },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, r) => {
        const ops = [
          // 媒体集(manifest)看成员文件并预览;文本集走表格预览
          accessType.binary ? (
            <a key="members" onClick={() => openMembers(r)}>
              查看文件
            </a>
          ) : (
            <a key="preview" onClick={() => handlePreview(r)}>
              预览
            </a>
          ),
        ];
        // 托管数据集禁止删除(#18,仅管理员走「取消托管」,本页不重复提供)→ 隐藏删除
        if (access.canAdmin && !r.hosted) {
          ops.push(
            <Popconfirm
              key="delete"
              title="确认删除该数据集?"
              description="将删除其全部版本与产物文件,不可恢复。"
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleDelete(r.id)}
            >
              <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
            </Popconfirm>,
          );
        }
        return ops;
      },
    },
  ];

  const previewColumns = (preview?.columns ?? []).map((c) => ({
    title: c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: unknown) => cellText(v),
  }));

  const menuItems = ACCESS_TYPES.map((t) => ({ key: t.key, label: t.label }));

  return (
    <PageContainer>
      <Layout style={{ background: 'transparent' }}>
        <Sider width={160} theme="light" style={{ borderRadius: 8 }}>
          <Menu
            mode="inline"
            selectedKeys={[typeKey]}
            items={menuItems}
            onClick={({ key }) => setTypeKey(key)}
            style={{ borderInlineEnd: 'none' }}
          />
        </Sider>
        <Content style={{ paddingInlineStart: 16 }}>
          {accessType.key === 'sql' && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              title="SQL/数据库接入走「数据源管理 + 采集任务」"
              description="在数据源管理创建数据库连接,在采集任务中选择表或填写 SQL 并运行;采集成功后数据会落为下方数据集,可直接用于数据加工。"
              action={
                <Space vertical>
                  <Button
                    size="small"
                    type="primary"
                    onClick={() => history.push('/ingest/datasources')}
                  >
                    去数据源管理
                  </Button>
                  <Button
                    size="small"
                    onClick={() => history.push('/ingest/tasks')}
                  >
                    去采集任务
                  </Button>
                </Space>
              }
            />
          )}
          <ProTable<DataPlatform.Dataset>
            headerTitle={accessType.label}
            actionRef={actionRef}
            rowKey="id"
            search={false}
            columns={columns}
            // 过滤项经 params 驱动:变化时 ProTable 自动重拉(避免手动 reload 的陈旧闭包)
            params={{
              dataType: accessType.key,
              name: keyword,
              categoryId,
            }}
            toolBarRender={() => [
              <Space key="filters">
                <TreeSelect
                  allowClear
                  placeholder="选择分类"
                  style={{ width: 180 }}
                  treeData={categoryTreeData}
                  value={categoryId}
                  onChange={setCategoryId}
                  showSearch
                  treeNodeFilterProp="title"
                  treeDefaultExpandAll
                />
                <Input.Search
                  allowClear
                  placeholder="名称搜索"
                  style={{ width: 200 }}
                  onSearch={(v) => setKeyword(v || undefined)}
                />
              </Space>,
              ...(accessType.extensions.length > 0
                ? [
                    <Button
                      key="upload"
                      type="primary"
                      icon={<UploadOutlined />}
                      onClick={() => setUploadOpen(true)}
                    >
                      上传
                    </Button>,
                  ]
                : []),
              <Button
                key="cat"
                icon={<AppstoreAddOutlined />}
                onClick={() => setCatOpen(true)}
              >
                分类管理
              </Button>,
            ]}
            request={async (params) => {
              const {
                current,
                pageSize,
                dataType,
                name,
                categoryId: cid,
              } = params as {
                current?: number;
                pageSize?: number;
                dataType?: string;
                name?: string;
                categoryId?: string;
              };
              const res = await listDatasets({
                current,
                pageSize,
                dataType,
                name,
                categoryId: cid,
              });
              return {
                data: res.data,
                total: res.total,
                success: res.success,
              };
            }}
          />
        </Content>
      </Layout>

      <UploadModal
        open={uploadOpen}
        accessType={accessType}
        onClose={() => setUploadOpen(false)}
        onDone={() => actionRef.current?.reload()}
      />
      <MediaMembersDrawer
        open={membersOpen}
        versionId={membersVersionId}
        datasetId={membersDatasetId}
        editable={membersEditable}
        accept={acceptOf(accessType)}
        title={membersName}
        onClose={() => setMembersOpen(false)}
      />
      <CategoryManager
        open={catOpen}
        canAdmin={access.canAdmin}
        onClose={() => setCatOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />

      <Drawer
        width={900}
        open={previewOpen}
        title={`预览:${previewName ?? ''}`}
        onClose={() => {
          setPreviewOpen(false);
          setPreview(undefined);
          setPreviewName(undefined);
        }}
      >
        <Spin spinning={previewLoading}>
          {preview && preview.data.length > 0 ? (
            <Table
              rowKey={(_, i) => String(i)}
              size="small"
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 10 }}
              dataSource={preview.data}
              columns={previewColumns}
            />
          ) : (
            <Empty
              description={preview?.message ?? '暂无可预览数据'}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
        </Spin>
      </Drawer>
    </PageContainer>
  );
};

export default AccessPage;
