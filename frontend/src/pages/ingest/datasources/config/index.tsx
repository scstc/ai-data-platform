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
import { history, useParams, useSearchParams } from '@umijs/max';
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
  message,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd';
import { type FC, useEffect, useMemo, useState } from 'react';
import {
  createDataSource,
  listCategories,
  testDataSource,
} from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';
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
const DB_BRAND_TOKEN: Record<DataPlatform.DbKind, string> = {
  postgresql: 'Postgres',
  goldendb: 'GoldenDB',
  hologres: 'Hologres',
  kingbase: 'Kingbase',
  gaussdb: 'GaussDB',
  dameng: 'Dameng',
  sequoiadb: 'SequoiaDB',
  hive: 'Hive',
  doris: 'Doris',
};

/** 右侧浏览面板:保存前显示"需先连接"空态(浏览接口需已保存的数据源 id) */
const BrowserPanel: FC<{ type: DataPlatform.DataSourceType }> = ({ type }) => {
  const titleMap: Record<DataPlatform.DataSourceType, string> = {
    s3: '存储桶浏览',
    hdfs: 'HDFS 浏览',
    database: '连接预览',
    api: '推送端点',
  };
  const hint =
    type === 'database'
      ? '测试连接通过并保存后,可在编辑页浏览库表结构。'
      : '测试连接凭证并保存后,可在编辑页浏览目录 / 文件结构。';
  return (
    <Card
      title={
        <Space>
          <FolderOpenOutlined />
          {titleMap[type]}
        </Space>
      }
      style={{ position: 'sticky', top: 16 }}
      styles={{
        body: {
          minHeight: 420,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        },
      }}
    >
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4}>
            <Text strong>需先建立连接</Text>
            <Text type="secondary" style={{ fontSize: 13 }}>
              {hint}
            </Text>
          </Space>
        }
      />
    </Card>
  );
};

/** 数据源配置页:左侧凭证表单 + 测试连接,右侧浏览面板(两栏布局) */
const DataSourceConfigPage: FC = () => {
  const params = useParams();
  const [search] = useSearchParams();
  const type = (params.type ?? 's3') as DataPlatform.DataSourceType;
  const dbKindFromQuery = search.get('dbKind') as DataPlatform.DbKind | null;
  // S3 兼容厂商档(s3 / minio / oss / obs):仅影响标题与默认 Endpoint / AK-SK 标注
  const s3Provider = (search.get('provider') ?? 's3') as S3Provider;
  const s3Meta = S3_PROVIDERS[s3Provider] ?? S3_PROVIDERS.s3;

  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] =
    useState<DataPlatform.TestConnectionResult | null>(null);
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  useEffect(() => {
    listCategories()
      .then((res) =>
        setCategoryOptions(
          res.data.map((c) => ({ label: c.name, value: c.id })),
        ),
      )
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (type === 'database' && dbKindFromQuery) {
      form.setFieldValue('dbKind', dbKindFromQuery);
    }
  }, [type, dbKindFromQuery, form]);

  const pageTitle = useMemo(() => {
    if (type === 's3') {
      return s3Meta.title;
    }
    if (type === 'database' && dbKindFromQuery) {
      return `配置 ${DB_KIND_LABEL[dbKindFromQuery]} 连接`;
    }
    return CONFIG_TITLE[type] ?? '配置数据源连接';
  }, [type, dbKindFromQuery, s3Meta]);

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
      const brand = kind ? DB_BRAND_TOKEN[kind] : 'Database';
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
      const result = await testDataSource({
        type,
        dbKind: type === 'database' ? values.dbKind : undefined,
        config: pickConfig(type, values),
      });
      setTestResult(result);
      result.success
        ? message.success(`连接成功,延迟 ${result.latencyMs}ms`)
        : message.error(result.message);
    } catch {
      message.error('测试连接请求失败');
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
      await createDataSource({
        name: values.name,
        type,
        dbKind: type === 'database' ? values.dbKind : undefined,
        config: pickConfig(type, values),
        description: values.description,
        categoryId: values.categoryId,
      });
      message.success('数据源已创建');
      history.push('/ingest/datasources');
    } catch {
      message.error('创建失败,请重试');
    } finally {
      setSaving(false);
    }
  };

  // API 推送:无需凭证浏览面板,单栏说明
  const isApi = type === 'api';
  const crumbLast =
    type === 's3' ? s3Provider.toUpperCase() : type.toUpperCase();

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据源', path: '/ingest/datasources' },
        { title: '新建连接', path: '/ingest/datasources/new' },
        { title: crumbLast },
      ])}
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
          保存连接
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

              {isApi && (
                <Alert
                  type="info"
                  showIcon
                  icon={<ThunderboltOutlined />}
                  style={{ marginBottom: 16 }}
                  message="保存后自动生成推送地址与 token"
                  description="外部系统向生成的地址 POST 数据(JSON 数组或 jsonl)即可接入;在数据源编辑页查看地址、token 并按需轮换。"
                />
              )}

              <Form.Item name="categoryId" label="分类(可选)">
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择分类"
                  options={categoryOptions}
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
        {!isApi && <BrowserPanel type={type} />}
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
