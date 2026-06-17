import {
  ProForm,
  ProFormDigit,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProFormTextArea,
  StepsForm,
} from '@ant-design/pro-components';
import type { FormInstance } from 'antd';
import { Alert, Button, Card, Drawer, message, Space, Typography } from 'antd';
import { type FC, useEffect, useRef, useState } from 'react';
import {
  createDataSource,
  rotatePushToken,
  testDataSource,
  updateDataSource,
} from '@/services/data-platform';
import { DB_KIND_OPTIONS, TYPE_CARDS } from './constants';

type FormValues = {
  name: string;
  description?: string;
  dbKind?: DataPlatform.DbKind;
} & Record<string, any>;

interface DataSourceFormDrawerProps {
  open: boolean;
  /** 有值即编辑模式，无值为新建 */
  record?: DataPlatform.DataSource;
  /** 受控分类下拉选项（来自宿主页 listCategories） */
  categoryOptions?: { label: string; value: string }[];
  onClose: () => void;
  /** 保存成功后回调（刷新列表） */
  onSuccess: () => void;
}

const { Text, Paragraph } = Typography;

/** 把拍平的表单字段还原成接口需要的 config */
function pickConfig(
  type: DataPlatform.DataSourceType,
  values: Record<string, any>,
): Record<string, any> {
  switch (type) {
    case 's3':
      return {
        endpoint: values.endpoint,
        bucket: values.bucket,
        accessKey: values.accessKey,
        secretKey: values.secretKey,
        ...(values.prefix ? { prefix: values.prefix } : {}),
      };
    case 'hdfs':
      return {
        nameNode: values.nameNode,
        path: values.path,
        kerberos: !!values.kerberos,
      };
    case 'database':
      return {
        host: values.host,
        port: Number(values.port),
        database: values.database,
        username: values.username,
        password: values.password,
        ...(values.table ? { table: values.table } : {}),
      };
    default:
      // api：推送地址由平台生成，不从表单字段取（新建时后端生成，编辑时保留现有值）
      return values.url ? { url: values.url } : {};
  }
}

const DataSourceFormDrawer: FC<DataSourceFormDrawerProps> = ({
  open,
  record,
  categoryOptions,
  onClose,
  onSuccess,
}) => {
  const isEdit = !!record;
  const [type, setType] = useState<DataPlatform.DataSourceType>(
    record?.type ?? 's3',
  );
  const [testResult, setTestResult] =
    useState<DataPlatform.TestConnectionResult | null>(null);
  const [testing, setTesting] = useState(false);
  // api 类型：推送 url（后端回填真实地址；编辑态从 config.url 取）
  const [pushUrl, setPushUrl] = useState<string>(
    (record?.config?.url as string) || '',
  );
  const [rotating, setRotating] = useState(false);

  // 各步骤 form 实例集合，用于在测试步骤读取配置步骤的实时值
  const formMapRef = useRef<
    React.MutableRefObject<FormInstance<any> | undefined>[]
  >([]);

  // 每次打开重置类型与测试结果
  useEffect(() => {
    if (open) {
      setType(record?.type ?? 's3');
      setTestResult(null);
      setPushUrl((record?.config?.url as string) || '');
    }
  }, [open, record]);

  // 配置步骤初值（编辑回填）
  const configInitialValues: Record<string, any> = record
    ? { ...record.config, dbKind: record.dbKind, categoryId: record.categoryId ?? undefined }
    : {};

  /** 读取配置步骤当前填写的全部值 */
  const collectValues = (): Record<string, any> => {
    // formMapRef[1] 对应第二步（配置步骤）
    const configForm = formMapRef.current?.[1]?.current;
    return { ...configInitialValues, ...(configForm?.getFieldsValue() ?? {}) };
  };

  const handleTest = async () => {
    const values = collectValues();
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testDataSource({
        type,
        dbKind: type === 'database' ? values.dbKind : undefined,
        config: pickConfig(type, values),
      });
      setTestResult(result);
      if (result.success) {
        message.success(`连接成功，延迟 ${result.latencyMs}ms`);
      } else {
        message.error(result.message);
      }
    } catch {
      message.error('测试连接请求失败');
    } finally {
      setTesting(false);
    }
  };

  const handleFinish = async (allValues: FormValues) => {
    const config = pickConfig(type, allValues);
    const dbKind = type === 'database' ? allValues.dbKind : undefined;
    try {
      if (isEdit && record) {
        await updateDataSource(record.id, {
          name: allValues.name,
          type,
          dbKind,
          config,
          description: allValues.description,
          categoryId: allValues.categoryId,
        });
        message.success('数据源已更新');
      } else {
        await createDataSource({
          name: allValues.name,
          type,
          dbKind,
          config,
          description: allValues.description,
          categoryId: allValues.categoryId,
        });
        message.success('数据源已创建');
      }
      onSuccess();
      onClose();
      return true;
    } catch {
      message.error(isEdit ? '更新失败，请重试' : '创建失败，请重试');
      return false;
    }
  };

  return (
    <StepsForm<FormValues>
      formMapRef={formMapRef}
      onFinish={handleFinish}
      stepsFormRender={(dom, submitter) => (
        <Drawer
          title={isEdit ? '编辑数据源' : '新建数据源'}
          width={640}
          open={open}
          onClose={onClose}
          destroyOnClose
          footer={
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              {submitter}
            </div>
          }
        >
          {dom}
        </Drawer>
      )}
    >
      {/* 步骤一：选择类型 */}
      <StepsForm.StepForm name="type" title="选择类型">
        <Text type="secondary">
          选择要接入的数据源类型（编辑时类型不可变更）
        </Text>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 16,
            marginTop: 16,
          }}
        >
          {TYPE_CARDS.map((card) => {
            const active = type === card.type;
            return (
              <Card
                key={card.type}
                hoverable={!isEdit}
                size="small"
                onClick={() => {
                  if (!isEdit) {
                    setType(card.type);
                    setTestResult(null);
                  }
                }}
                style={{
                  cursor: isEdit ? 'not-allowed' : 'pointer',
                  borderColor: active ? '#1677ff' : undefined,
                  borderWidth: active ? 2 : 1,
                  opacity: isEdit && !active ? 0.5 : 1,
                }}
                data-testid={`type-card-${card.type}`}
              >
                <Card.Meta title={card.title} description={card.desc} />
              </Card>
            );
          })}
        </div>
      </StepsForm.StepForm>

      {/* 步骤二：按类型动态参数 */}
      <StepsForm.StepForm
        name="config"
        title="连接配置"
        initialValues={configInitialValues}
      >
        <ProFormText
          name="name"
          label="数据源名称"
          rules={[{ required: true, message: '请输入数据源名称' }]}
          initialValue={record?.name}
          placeholder="请输入易识别的名称"
        />

        {type === 's3' && (
          <>
            <ProFormText
              name="endpoint"
              label="Endpoint"
              rules={[{ required: true, message: '请输入 endpoint' }]}
              placeholder="如 s3.cn-north-1.amazonaws.com.cn"
            />
            <ProFormText
              name="bucket"
              label="Bucket"
              rules={[{ required: true, message: '请输入 bucket' }]}
            />
            <ProFormText
              name="accessKey"
              label="Access Key"
              rules={[{ required: true, message: '请输入 access key' }]}
            />
            <ProFormText.Password
              name="secretKey"
              label="Secret Key"
              rules={[{ required: true, message: '请输入 secret key' }]}
            />
            <ProFormText
              name="prefix"
              label="路径前缀（可选）"
              placeholder="如 raw/2026/"
            />
          </>
        )}

        {type === 'hdfs' && (
          <>
            <ProFormText
              name="nameNode"
              label="NameNode 地址"
              rules={[{ required: true, message: '请输入 NameNode 地址' }]}
              placeholder="如 hdfs://namenode.internal:8020"
            />
            <ProFormText
              name="path"
              label="路径"
              rules={[{ required: true, message: '请输入 HDFS 路径' }]}
              placeholder="如 /user/data-platform/raw"
            />
            <ProFormSwitch name="kerberos" label="启用 Kerberos 认证" />
          </>
        )}

        {type === 'database' && (
          <>
            <ProFormSelect
              name="dbKind"
              label="数据库类型"
              options={DB_KIND_OPTIONS}
              rules={[{ required: true, message: '请选择数据库类型' }]}
            />
            <ProFormText
              name="host"
              label="主机"
              rules={[{ required: true, message: '请输入主机地址' }]}
            />
            <ProFormDigit
              name="port"
              label="端口"
              min={1}
              max={65535}
              fieldProps={{ precision: 0 }}
              rules={[{ required: true, message: '请输入端口' }]}
            />
            <ProFormText
              name="username"
              label="用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            />
            <ProFormText.Password
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            />
            <ProFormText
              name="database"
              label="库名"
              rules={[{ required: true, message: '请输入库名' }]}
            />
            <ProFormText name="table" label="表名（可选）" />
          </>
        )}

        {type === 'api' && (
          <>
            {isEdit && pushUrl ? (
              <ProForm.Item label="推送地址">
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space wrap>
                    <Text
                      code
                      copyable={{ text: pushUrl }}
                      style={{ wordBreak: 'break-all' }}
                    >
                      {pushUrl}
                    </Text>
                  </Space>
                  <Button
                    size="small"
                    loading={rotating}
                    danger
                    onClick={async () => {
                      setRotating(true);
                      try {
                        const res = await rotatePushToken(record!.id);
                        if (res.success) {
                          setPushUrl(res.data.url);
                          message.success('推送 token 已轮换，旧 token 立即失效');
                        }
                      } catch {
                        message.error('轮换失败，请重试');
                      } finally {
                        setRotating(false);
                      }
                    }}
                  >
                    轮换 Token
                  </Button>
                  <Paragraph type="secondary" style={{ margin: 0 }}>
                    外部系统向上方地址 POST 数据（JSON 数组或 jsonl）即可接入。Token
                    即鉴权凭证，泄露后点「轮换 Token」立即失效旧 token。
                  </Paragraph>
                </Space>
              </ProForm.Item>
            ) : (
              <Paragraph type="secondary">
                保存后平台将自动生成推送地址与 token，在编辑页面查看。
                <br />
                外部系统向推送地址 POST 数据（JSON 数组或 jsonl）即可接入。
              </Paragraph>
            )}
          </>
        )}

        <ProFormSelect
          name="categoryId"
          label="分类（可选）"
          placeholder="请选择分类"
          options={categoryOptions}
          fieldProps={{ allowClear: true, showSearch: true }}
        />

        <ProFormTextArea
          name="description"
          label="描述（可选）"
          initialValue={record?.description}
          fieldProps={{ rows: 2 }}
        />
      </StepsForm.StepForm>

      {/* 步骤三：测试连接并保存 */}
      <StepsForm.StepForm name="test" title="测试连接">
        <ProForm.Item>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text type="secondary">
              点击「上一步」可返回修改配置；点击下方「测试连接」校验连通性，确认后提交保存。
            </Text>
            <Button
              data-testid="run-test"
              loading={testing}
              onClick={handleTest}
            >
              测试连接
            </Button>
            {testResult && (
              <Alert
                type={testResult.success ? 'success' : 'error'}
                showIcon
                message={testResult.success ? '连接成功' : '连接失败'}
                description={`${testResult.message}（延迟 ${testResult.latencyMs}ms）`}
              />
            )}
          </Space>
        </ProForm.Item>
      </StepsForm.StepForm>
    </StepsForm>
  );
};

export default DataSourceFormDrawer;
