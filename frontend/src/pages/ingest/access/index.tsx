import { AppstoreAddOutlined, UploadOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import {
  Button,
  Drawer,
  Empty,
  Input,
  Layout,
  Menu,
  message,
  Popconfirm,
  Result,
  Select,
  Space,
  Spin,
  Table,
  Tag,
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
import { ACCESS_TYPES } from './constants';
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
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [catOpen, setCatOpen] = useState(false);
  // 预览抽屉
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewName, setPreviewName] = useState<string>();

  // typeKey 恒为有效栏 key,这里仍做兜底避免任何来源扩展导致的运行时崩溃
  const accessType =
    ACCESS_TYPES.find((t) => t.key === typeKey) ?? ACCESS_TYPES[0];

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
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

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    { title: '文件名', dataIndex: 'name', ellipsis: true },
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
      valueType: 'dateTime',
    },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      render: (_, r) => {
        const ops = [
          // 二进制类无表格预览(后端 raw 存,不规范化)→ 置灰
          accessType.binary ? (
            <span
              key="preview"
              style={{ color: 'var(--ant-color-text-disabled)' }}
            >
              预览
            </span>
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
          {accessType.key === 'sql' ? (
            <Result
              status="info"
              title="SQL 接入走「数据源管理」"
              subTitle="数据库接入请在数据源管理中创建 SQL 连接,再用采集任务拉取入库。"
              extra={[
                <Button
                  key="ds"
                  type="primary"
                  onClick={() => history.push('/ingest/datasources')}
                >
                  去数据源管理
                </Button>,
                <Button
                  key="task"
                  onClick={() => history.push('/ingest/tasks')}
                >
                  去采集任务
                </Button>,
              ]}
            />
          ) : (
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
                  <Select
                    allowClear
                    placeholder="选择分类"
                    style={{ width: 180 }}
                    options={categoryOptions}
                    value={categoryId}
                    onChange={setCategoryId}
                  />
                  <Input.Search
                    allowClear
                    placeholder="文件名搜索"
                    style={{ width: 200 }}
                    onSearch={(v) => setKeyword(v || undefined)}
                  />
                </Space>,
                <Button
                  key="upload"
                  type="primary"
                  icon={<UploadOutlined />}
                  onClick={() => setUploadOpen(true)}
                >
                  上传
                </Button>,
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
          )}
        </Content>
      </Layout>

      <UploadModal
        open={uploadOpen}
        accessType={accessType}
        onClose={() => setUploadOpen(false)}
        onDone={() => actionRef.current?.reload()}
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
