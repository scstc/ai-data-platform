import {
  ApiOutlined,
  CloudOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  FolderOpenOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation, useParams, useSearchParams } from '@umijs/max';
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  TreeSelect,
  Typography,
} from 'antd';
import { type FC, useEffect, useMemo, useState } from 'react';
import {
  createDataSource,
  listBuckets,
  listCategories,
  listDatasourceTables,
  listObjects,
  rotatePushToken,
  testDataSource,
  updateDataSource,
} from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import {
  CONFIG_TITLE,
  DB_KIND_LABEL,
  DB_KIND_OPTIONS,
  S3_PROVIDERS,
  type S3Provider,
} from '../components/constants';

const { Text, Title, Paragraph } = Typography;

/** 把拍平的表单字段还原成接口需要的 config(与创建流程对齐) */
function pickConfig(
  type: DataPlatform.DataSourceType,
  v: Record<string, any>,
): Record<string, any> {
  switch (type) {
    case 's3':
      return {
        endpoint: v.endpoint,
        bucket: v.bucket,
        accessKey: v.accessKey,
        secretKey: v.secretKey,
        ...(v.prefix ? { prefix: v.prefix } : {}),
      };
    case 'hdfs':
      return { nameNode: v.nameNode, path: v.path, kerberos: !!v.kerberos };
    case 'database':
      return {
        host: v.host,
        port: Number(v.port),
        database: v.database,
        username: v.username,
        password: v.password,
        ...(v.table ? { table: v.table } : {}),
      };
    default:
      return {};
  }
}

const TYPE_ICON: Record<DataPlatform.DataSourceType, React.ReactNode> = {
  s3: <CloudOutlined />,
  hdfs: <ClusterOutlined />,
  database: <DatabaseOutlined />,
  api: <ApiOutlined />,
};

/** 数据库品牌 → 连接名示例用的英文短标识(连接名称占位符按所选品牌变化) */
const DB_BRAND_TOKEN: Partial<Record<DataPlatform.DbKind, string>> = {
  postgresql: 'Postgres',
  goldendb: 'GoldenDB',
};

/** 编辑态标题用的中文短标识 */
const TYPE_SUBJECT: Record<DataPlatform.DataSourceType, string> = {
  s3: 'S3',
  hdfs: 'HDFS',
  database: '数据库',
  api: 'API',
};

/** 字节数转人类可读(与 datasets/list 的 fmtSize 对齐) */
const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

/** 从请求错误里抠后端给的原因(浏览请求跳过全局 toast,改在面板内友好展示) */
const extractMsg = (err: any): string =>
  err?.data?.message ||
  err?.response?.data?.message ||
  err?.info?.errorMessage ||
  err?.message ||
  '加载失败,请稍后重试';

const BROWSE_TITLE: Record<DataPlatform.DataSourceType, string> = {
  s3: '存储桶浏览',
  hdfs: 'HDFS 浏览',
  database: '连接预览',
  api: '推送端点',
};

/** 右侧浏览面板:create 态(无 id)显示空态;edit 态按类型拉真实目录/表
 *  · s3:桶下拉 + 桶内对象列表 · database:表列表 · hdfs:后端无浏览接口,诚实告知 */
const BrowserPanel: FC<{
  type: DataPlatform.DataSourceType;
  datasourceId?: string;
}> = ({ type, datasourceId }) => {
  // 有已保存 id 才发请求;首屏直接给 spinner,避免空数据闪一下
  const [loading, setLoading] = useState(!!datasourceId);
  const [error, setError] = useState<string | null>(null);
  const [buckets, setBuckets] = useState<string[]>([]);
  const [bucket, setBucket] = useState<string | undefined>(undefined);
  const [objects, setObjects] = useState<DataPlatform.S3Object[]>([]);
  const [tables, setTables] = useState<string[]>([]);

  // s3:列桶(skipErrorHandler:连不上时不在全局弹 toast,面板内告知)
  useEffect(() => {
    if (!datasourceId || type !== 's3') return;
    setLoading(true);
    setError(null);
    listBuckets(datasourceId, { skipErrorHandler: true })
      .then((res) => setBuckets(res.data ?? []))
      .catch((err) => setError(extractMsg(err)))
      .finally(() => setLoading(false));
  }, [datasourceId, type]);

  // s3:选定桶后列对象
  useEffect(() => {
    if (!datasourceId || type !== 's3' || !bucket) return;
    setLoading(true);
    setError(null);
    listObjects(datasourceId, { bucket }, { skipErrorHandler: true })
      .then((res) => setObjects(res.data ?? []))
      .catch((err) => setError(extractMsg(err)))
      .finally(() => setLoading(false));
  }, [datasourceId, type, bucket]);

  // database:列表
  useEffect(() => {
    if (!datasourceId || type !== 'database') return;
    setLoading(true);
    setError(null);
    listDatasourceTables(datasourceId, { skipErrorHandler: true })
      .then((res) => setTables(res.data ?? []))
      .catch((err) => setError(extractMsg(err)))
      .finally(() => setLoading(false));
  }, [datasourceId, type]);

  let body: React.ReactNode;
  if (!datasourceId) {
    // create 态:尚未保存,浏览接口需已保存的数据源 id
    body = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4}>
            <Text strong>需先建立连接</Text>
            <Text type="secondary" style={{ fontSize: 13 }}>
              {type === 'database'
                ? '测试连接通过并保存后,可在此浏览库表结构。'
                : '测试连接凭证并保存后,可在此浏览目录 / 文件结构。'}
            </Text>
          </Space>
        }
      />
    );
  } else if (error) {
    // 加载失败(驱动未装 / SASL 不支持 等):面板内友好告知,不弹全局 toast
    body = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4}>
            <Text type="secondary" style={{ fontSize: 13 }}>
              该连接暂无法浏览
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {error}
            </Text>
          </Space>
        }
      />
    );
  } else if (type === 's3') {
    body = (
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Select
          showSearch
          placeholder="选择桶"
          style={{ width: '100%' }}
          value={bucket}
          onChange={(v) => {
            setBucket(v);
            setObjects([]);
          }}
          options={buckets.map((b) => ({ label: b, value: b }))}
        />
        {bucket &&
          (objects.length ? (
            <div style={{ maxHeight: 340, overflow: 'auto' }}>
              <List<DataPlatform.S3Object>
                size="small"
                dataSource={objects}
                renderItem={(o) => (
                  <List.Item>
                    <Space
                      style={{
                        width: '100%',
                        justifyContent: 'space-between',
                      }}
                    >
                      <Text style={{ wordBreak: 'break-all' }}>{o.key}</Text>
                      <Text type="secondary" style={{ flexShrink: 0 }}>
                        {fmtSize(o.size)}
                      </Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          ) : (
            !loading && <Empty description="桶内暂无对象" />
          ))}
      </Space>
    );
  } else if (type === 'database') {
    body = tables.length ? (
      <div style={{ maxHeight: 380, overflow: 'auto' }}>
        <Space wrap>
          {tables.map((t) => (
            <Tag key={t} color="blue">
              {t}
            </Tag>
          ))}
        </Space>
      </div>
    ) : (
      !loading && <Empty description="未读到表" />
    );
  } else {
    // hdfs:后端无浏览接口
    body = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="HDFS 暂不支持在线浏览"
      />
    );
  }

  return (
    <Card
      title={
        <Space>
          <FolderOpenOutlined />
          {BROWSE_TITLE[type]}
        </Space>
      }
      style={{ position: 'sticky', top: 16 }}
      styles={{ body: { minHeight: 420 } }}
    >
      <Spin spinning={loading}>{body}</Spin>
    </Card>
  );
};

/** 数据源配置页:左侧凭证表单 + 测试连接,右侧浏览面板(两栏布局)。
 *  新建走 query 模板(dbKind/provider);编辑走路由 state 传入的整条记录,回填后 PUT 更新。 */
const DataSourceConfigPage: FC = () => {
  const params = useParams();
  const [search] = useSearchParams();
  const location = useLocation();
  const type = (params.type ?? 's3') as DataPlatform.DataSourceType;
  const dbKindFromQuery = search.get('dbKind') as DataPlatform.DbKind | null;
  // S3 兼容厂商档(s3 / minio / oss / obs):仅影响标题与默认 Endpoint / AK-SK 标注
  const s3Provider = (search.get('provider') ?? 's3') as S3Provider;
  const s3Meta = S3_PROVIDERS[s3Provider] ?? S3_PROVIDERS.s3;

  // 编辑模式:list 行已含 config,整条记录经路由 state 传入;?id= 仅作"编辑态"URL 信号(刷新丢 state 时提示)
  const editRecord = (
    location.state as { record?: DataPlatform.DataSource } | null
  )?.record;
  const editId = search.get('id');
  const isEdit = !!editRecord;

  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] =
    useState<DataPlatform.TestConnectionResult | null>(null);
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  // api 编辑:推送地址由后端生成,只读展示 + 可轮换 token
  const [pushUrl, setPushUrl] = useState<string>(
    (editRecord?.config?.url as string) || '',
  );
  const [rotating, setRotating] = useState(false);

  useEffect(() => {
    listCategories()
      .then((res) => setCategoryTreeData(toCategoryTreeData(res.data)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (type === 'database' && dbKindFromQuery) {
      form.setFieldValue('dbKind', dbKindFromQuery);
    }
  }, [type, dbKindFromQuery, form]);

  // 编辑回填:从记录还原 name/config/dbKind/categoryId(create 走 query 模板,不进这里)
  useEffect(() => {
    if (!editRecord) return;
    form.setFieldsValue({
      name: editRecord.name,
      description: editRecord.description,
      categoryId: editRecord.categoryId ?? undefined,
      dbKind: editRecord.dbKind,
      ...editRecord.config,
    });
  }, [editRecord, form]);

  const pageTitle = useMemo(() => {
    if (isEdit) {
      const subject =
        type === 'database' && editRecord?.dbKind
          ? (DB_KIND_LABEL[editRecord.dbKind] ?? TYPE_SUBJECT.database)
          : TYPE_SUBJECT[type];
      return `编辑 ${subject} 连接`;
    }
    if (type === 's3') {
      return s3Meta.title;
    }
    if (type === 'database' && dbKindFromQuery) {
      return `配置 ${DB_KIND_LABEL[dbKindFromQuery] ?? '数据库'} 连接`;
    }
    return CONFIG_TITLE[type] ?? '配置数据源连接';
  }, [isEdit, type, editRecord, dbKindFromQuery, s3Meta]);

  // 连接名称示例:按类型给不同提示(S3 按厂商档、数据库按所选品牌),避免千篇一律
  const watchedDbKind = Form.useWatch('dbKind', form) as
    | DataPlatform.DbKind
    | undefined;
  const namePlaceholder = useMemo(() => {
    if (type === 's3') {
      const byProvider: Record<S3Provider, string> = {
        s3: 'Production_S3_Warehouse',
        minio: 'MinIO_DataLake_Prod',
        oss: 'Aliyun_OSS_Warehouse',
        obs: 'Huawei_OBS_Warehouse',
      };
      return byProvider[s3Provider] ?? byProvider.s3;
    }
    if (type === 'database') {
      const kind = watchedDbKind ?? dbKindFromQuery ?? undefined;
      const brand = (kind && DB_BRAND_TOKEN[kind]) || 'Database';
      return `${brand}_Orders_Prod`;
    }
    if (type === 'hdfs') return 'Hadoop_HDFS_RawZone';
    return 'API_Push_OrderStream';
  }, [type, s3Provider, watchedDbKind, dbKindFromQuery]);

  const handleTest = async () => {
    let values: Record<string, any>;
    try {
      values = await form.validateFields();
    } catch {
      message.warning('请先填写必填项再测试连接');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testDataSource(
        {
          type,
          dbKind: type === 'database' ? values.dbKind : undefined,
          config: pickConfig(type, values),
        },
        { skipErrorHandler: true },
      );
      setTestResult(result);
      if (result.success) {
        message.success(`连接成功,延迟 ${result.latencyMs}ms`);
      }
      // 失败:结果进 testResult,由下方 Alert 展示真实原因(不另弹 toast)
    } catch (err) {
      // success:false 被 errorThrower 抛出(已 skipErrorHandler,无全局 toast):还原真实原因进 Alert
      setTestResult({ success: false, message: extractMsg(err), latencyMs: 0 });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    let values: Record<string, any>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        type,
        dbKind: type === 'database' ? values.dbKind : undefined,
        config: pickConfig(type, values),
        description: values.description,
        categoryId: values.categoryId,
      };
      if (isEdit && editRecord) {
        await updateDataSource(editRecord.id, payload);
        message.success('数据源已更新');
      } else {
        await createDataSource(payload);
        message.success('数据源已创建');
      }
      history.push('/ingest/datasources');
    } catch {
      message.error(isEdit ? '更新失败,请重试' : '创建失败,请重试');
    } finally {
      setSaving(false);
    }
  };

  const handleRotateToken = async () => {
    if (!editRecord) return;
    setRotating(true);
    try {
      const res = await rotatePushToken(editRecord.id);
      if (res.success) {
        setPushUrl(res.data.url);
        message.success('推送 token 已轮换,旧 token 立即失效');
      } else {
        message.error('轮换失败,请重试');
      }
    } catch {
      message.error('轮换失败,请重试');
    } finally {
      setRotating(false);
    }
  };

  // API 推送:无需凭证浏览面板,单栏说明
  const isApi = type === 'api';
  const crumbLast =
    type === 's3' ? s3Provider.toUpperCase() : type.toUpperCase();

  // 编辑态刷新(?id= 还在但 state 丢失):无 detail 接口无法回填,提示返回列表
  if (editId && !editRecord) {
    return (
      <PageContainer>
        <Alert
          type="warning"
          showIcon
          message="编辑态已失效"
          description="编辑数据源不支持直接刷新。请返回数据源列表,重新点击「编辑」进入。"
          action={
            <Button
              size="small"
              onClick={() => history.push('/ingest/datasources')}
            >
              返回列表
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb(
        // 与菜单层级对齐:数据接入 › 数据源管理 › <被编辑名 / 新建连接+类型>
        [
          { title: '数据接入', path: '/ingest' },
          { title: '数据源管理', path: '/ingest/datasources' },
          ...(isEdit
            ? [{ title: editRecord?.name || '编辑' }]
            : [
                { title: '新建连接', path: '/ingest/datasources/new' },
                { title: crumbLast },
              ]),
        ],
      )}
      title={
        <Space>
          {TYPE_ICON[type]}
          {pageTitle}
        </Space>
      }
      extra={[
        <Button
          key="cancel"
          onClick={() => history.push('/ingest/datasources')}
        >
          取消
        </Button>,
        <Button
          key="save"
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={handleSave}
        >
          {isEdit ? '保存修改' : '保存连接'}
        </Button>,
      ]}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: isApi
            ? '1fr'
            : 'minmax(0, 1.1fr) minmax(0, 1fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左:凭证表单 */}
        <div>
          <Card title="连接凭证">
            <Form form={form} layout="vertical" requiredMark={false}>
              <Form.Item
                name="name"
                label="连接名称"
                rules={[{ required: true, message: '请输入连接名称' }]}
              >
                <Input placeholder={`如 ${namePlaceholder}`} />
              </Form.Item>

              {type === 's3' && (
                <>
                  <Form.Item
                    name="endpoint"
                    label="Endpoint URL"
                    rules={[{ required: true, message: '请输入 Endpoint' }]}
                  >
                    <Input placeholder={`如 ${s3Meta.endpointPlaceholder}`} />
                  </Form.Item>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 1fr',
                      gap: 12,
                    }}
                  >
                    <Form.Item
                      name="accessKey"
                      label={s3Meta.akLabel}
                      rules={[{ required: true, message: '请输入 Access Key' }]}
                    >
                      <Input placeholder="LTAI5t..." />
                    </Form.Item>
                    <Form.Item
                      name="secretKey"
                      label={s3Meta.skLabel}
                      rules={[{ required: true, message: '请输入 Secret Key' }]}
                    >
                      <Input.Password placeholder="••••••••••••" />
                    </Form.Item>
                  </div>
                  <Form.Item
                    name="bucket"
                    label="Bucket 名称"
                    rules={[{ required: true, message: '请输入 Bucket' }]}
                  >
                    <Input placeholder="如 data-warehouse-prod" />
                  </Form.Item>
                </>
              )}

              {type === 'hdfs' && (
                <>
                  <Form.Item
                    name="nameNode"
                    label="NameNode URI"
                    rules={[
                      { required: true, message: '请输入 NameNode 地址' },
                    ]}
                  >
                    <Input placeholder="hdfs://namenode:8020" />
                  </Form.Item>
                  <Form.Item
                    name="path"
                    label="源根路径"
                    rules={[{ required: true, message: '请输入 HDFS 路径' }]}
                  >
                    <Input placeholder="/data/ingestion/raw/production" />
                  </Form.Item>
                  <Form.Item
                    name="kerberos"
                    label="启用 Kerberos 认证"
                    valuePropName="checked"
                  >
                    <Switch />
                  </Form.Item>
                </>
              )}

              {type === 'database' && (
                <>
                  <Form.Item
                    name="dbKind"
                    label="数据库类型"
                    rules={[{ required: true, message: '请选择数据库类型' }]}
                  >
                    <Select
                      options={DB_KIND_OPTIONS}
                      placeholder="选择数据库品牌"
                    />
                  </Form.Item>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '2fr 1fr',
                      gap: 12,
                    }}
                  >
                    <Form.Item
                      name="host"
                      label="主机"
                      rules={[{ required: true, message: '请输入主机地址' }]}
                    >
                      <Input placeholder="如 10.60.1.119" />
                    </Form.Item>
                    <Form.Item
                      name="port"
                      label="端口"
                      rules={[{ required: true, message: '请输入端口' }]}
                    >
                      <InputNumber
                        min={1}
                        max={65535}
                        precision={0}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  </div>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 1fr',
                      gap: 12,
                    }}
                  >
                    <Form.Item
                      name="username"
                      label="用户名"
                      rules={[{ required: true, message: '请输入用户名' }]}
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item
                      name="password"
                      label="密码"
                      rules={[{ required: true, message: '请输入密码' }]}
                    >
                      <Input.Password />
                    </Form.Item>
                  </div>
                  <Form.Item
                    name="database"
                    label="库名"
                    rules={[{ required: true, message: '请输入库名' }]}
                  >
                    <Input />
                  </Form.Item>
                </>
              )}

              {isApi && isEdit && pushUrl ? (
                <Form.Item label="推送地址">
                  <Space
                    direction="vertical"
                    style={{ width: '100%' }}
                    size={12}
                  >
                    <Text
                      code
                      copyable={{ text: pushUrl }}
                      style={{ wordBreak: 'break-all' }}
                    >
                      {pushUrl}
                    </Text>
                    <Button
                      size="small"
                      loading={rotating}
                      danger
                      onClick={handleRotateToken}
                    >
                      轮换 Token
                    </Button>
                    <Paragraph type="secondary" style={{ margin: 0 }}>
                      外部系统向推送地址 POST 数据(JSON 数组或
                      jsonl)即可接入。Token 即鉴权凭证,泄露后点「轮换
                      Token」立即失效旧 token。
                    </Paragraph>
                  </Space>
                </Form.Item>
              ) : (
                isApi && (
                  <Alert
                    type="info"
                    showIcon
                    icon={<ThunderboltOutlined />}
                    style={{ marginBottom: 16 }}
                    message="保存后自动生成推送地址与 token"
                    description="外部系统向生成的地址 POST 数据(JSON 数组或 jsonl)即可接入;在数据源编辑页查看地址、token 并按需轮换。"
                  />
                )
              )}

              <Form.Item name="categoryId" label="分类(可选)">
                <TreeSelect
                  allowClear
                  showSearch
                  treeNodeFilterProp="title"
                  treeDefaultExpandAll
                  placeholder="选择分类"
                  treeData={categoryTreeData}
                />
              </Form.Item>
              <Form.Item name="description" label="描述(可选)">
                <Input.TextArea rows={2} />
              </Form.Item>
            </Form>

            {/* 高级配置(仅 s3 / database 有可选项) */}
            {(type === 's3' || type === 'database') && (
              <Collapse
                ghost
                items={[
                  {
                    key: 'adv',
                    label: '高级配置',
                    children: (
                      <Form form={form} layout="vertical" requiredMark={false}>
                        {type === 's3' && (
                          <Form.Item name="prefix" label="路径前缀(可选)">
                            <Input placeholder="如 raw/2026/" />
                          </Form.Item>
                        )}
                        {type === 'database' && (
                          <Form.Item name="table" label="表名(可选)">
                            <Input />
                          </Form.Item>
                        )}
                      </Form>
                    ),
                  },
                ]}
              />
            )}

            {!isApi && (
              <>
                <div
                  style={{
                    borderTop: '1px solid var(--ant-color-split, #f0f0f0)',
                    margin: '8px 0 16px',
                  }}
                />
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <Badge
                    status={
                      testResult
                        ? testResult.success
                          ? 'success'
                          : 'error'
                        : 'default'
                    }
                    text={
                      testResult
                        ? testResult.success
                          ? `已连接(${testResult.latencyMs}ms)`
                          : '连接失败'
                        : '未连接'
                    }
                  />
                  <Button loading={testing} onClick={handleTest}>
                    测试连接
                  </Button>
                </div>
                {testResult && !testResult.success && (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginTop: 12 }}
                    message={testResult.message}
                  />
                )}
              </>
            )}
          </Card>
        </div>

        {/* 右:浏览面板(API 类型不显示) */}
        {!isApi && <BrowserPanel type={type} datasourceId={editRecord?.id} />}
      </div>

      {isApi && (
        <Paragraph type="secondary" style={{ marginTop: 16 }}>
          <Title level={5}>API 推送说明</Title>
          推送式接入由外部系统主动把数据 POST
          到平台生成的端点。保存后在数据源编辑页可见: 推送 URL、X-API-Key /
          Bearer Token、以及最近推送事件记录。
        </Paragraph>
      )}
    </PageContainer>
  );
};

export default DataSourceConfigPage;
