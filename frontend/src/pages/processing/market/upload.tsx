import {
  DeleteOutlined,
  DownloadOutlined,
  InboxOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import type { UploadFile } from 'antd';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Form,
  Input,
  message,
  Select,
  Space,
  Typography,
  Upload,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  getOperatorCatalogMeta,
  uploadCustomOperator,
} from '@/services/data-platform';

const { Text } = Typography;

const PARAM_TYPE_OPTIONS = [
  { label: 'str 字符串', value: 'str' },
  { label: 'int 整数', value: 'int' },
  { label: 'float 浮点数', value: 'float' },
  { label: 'bool 布尔', value: 'bool' },
];

/** 上传自定义算子:独立页(替代此前的弹框),补齐描述 / 使用提示 / 参数表 /
 *  用法示例等属性——这些字段驱动算子工厂详情页展示 + 加工任务编排页的动态表单
 *  (见 backend operator_catalog._ui_params)。 */
const UploadCustomOperatorPage: React.FC = () => {
  const access = useAccess();
  const canUploadOperator = access.hasPerm('operator:upload');
  const [form] = Form.useForm();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [scenarioOptions, setScenarioOptions] = useState<{ value: string }[]>(
    [],
  );
  const [submitting, setSubmitting] = useState(false);

  // 场景分组自动补全:拉现有分组名,避免用户手填出零散的一次性新分组
  useEffect(() => {
    getOperatorCatalogMeta()
      .then((res) => {
        const names = Object.keys(res.data?.byScenario ?? {});
        setScenarioOptions(names.map((v) => ({ value: v })));
      })
      .catch(() => undefined);
  }, []);

  const beforeUpload = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.py')) {
      message.error('请上传 .py 算子源文件');
      return Upload.LIST_IGNORE;
    }
    if (file.size > 256 * 1024) {
      message.error('源文件超过 256KB 上限');
      return Upload.LIST_IGNORE;
    }
    return false;
  };

  const onSubmit = async () => {
    const values = await form.validateFields();
    const raw = fileList[0]?.originFileObj as File | undefined;
    if (!raw) {
      message.warning('请选择 .py 算子源文件');
      return;
    }

    const fd = new FormData();
    fd.append('file', raw);
    fd.append('zhLabel', values.zhLabel);
    if (values.scenarioGroup) fd.append('scenarioGroup', values.scenarioGroup);
    if (values.summaryZh) fd.append('summaryZh', values.summaryZh);
    if (values.descZh) fd.append('descZh', values.descZh);
    if (values.zhUsageTip) fd.append('zhUsageTip', values.zhUsageTip);
    if (values.example) fd.append('example', values.example);
    const params = (values.params ?? []).filter((p: any) => p?.name?.trim());
    if (params.length) fd.append('params', JSON.stringify(params));

    setSubmitting(true);
    try {
      const res = await uploadCustomOperator(fd, { skipErrorHandler: true });
      message.success(`已上传自定义算子「${res.data?.zhLabel ?? ''}」`);
      history.push(`/operators/${res.data?.name}`);
    } catch (e: any) {
      const body = e?.response?.data ?? e?.data;
      message.error(body?.message ?? e?.message ?? '上传失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      title="上传自定义算子"
      onBack={() => history.push('/operators')}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16, maxWidth: 800 }}
        message="源文件须恰好定义一个继承 Mapper / Filter / Deduplicator / Selector 的类,并带 @OPERATORS.register_module(&quot;算子名&quot;) 装饰器——与 data-juicer 原生自定义算子写法一致。"
        description={
          <a href="/samples/custom_operator_example.py" download>
            <DownloadOutlined /> 下载示例算子(custom_operator_example.py)
          </a>
        }
      />
      <Card style={{ maxWidth: 800 }}>
        <Form form={form} layout="vertical">
          <Form.Item label="算子源文件(.py)" required>
            <Upload.Dragger
              maxCount={1}
              fileList={fileList}
              beforeUpload={beforeUpload}
              onChange={({ fileList: fl }) => setFileList(fl)}
              accept=".py"
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 .py 文件到此处</p>
              <p className="ant-upload-hint">单文件最大 256KB</p>
            </Upload.Dragger>
          </Form.Item>

          <Form.Item
            name="zhLabel"
            label="中文名称"
            rules={[{ required: true, message: '请输入算子中文名称' }]}
          >
            <Input placeholder="如:去除特定水印文本" />
          </Form.Item>

          <Form.Item name="scenarioGroup" label="场景分组(可选)">
            <AutoComplete
              options={scenarioOptions}
              filterOption={(input, option) =>
                (option?.value ?? '')
                  .toLowerCase()
                  .includes(input.toLowerCase())
              }
              placeholder="如:文本清洗——用于工厂左侧场景菜单归类,可选已有分组"
            />
          </Form.Item>

          <Form.Item name="summaryZh" label="简要说明(可选)">
            <Input placeholder="一句话概括算子作用,展示在工厂卡片上" />
          </Form.Item>

          <Form.Item name="descZh" label="详细描述(可选)">
            <Input.TextArea
              rows={4}
              placeholder="算子详细说明:处理逻辑、注意事项等"
            />
          </Form.Item>

          <Form.Item name="zhUsageTip" label="使用提示(可选)">
            <Input.TextArea
              rows={2}
              placeholder="什么场景下该用这个算子,展示在详情页「何时使用」提示框"
            />
          </Form.Item>

          <Form.Item label="参数表(可选)">
            <Text type="secondary" style={{ fontSize: 12 }}>
              与 __init__
              里的构造参数对应;声明后可在加工任务编排页填不同值覆盖默认值。
            </Text>
            <Form.List name="params">
              {(fields, { add, remove }) => (
                <div style={{ marginTop: 8 }}>
                  {fields.map((field) => (
                    <Space
                      key={field.key}
                      align="baseline"
                      style={{ display: 'flex', marginBottom: 8 }}
                      wrap
                    >
                      <Form.Item
                        name={[field.name, 'name']}
                        rules={[{ required: true, message: '参数名必填' }]}
                        noStyle
                      >
                        <Input placeholder="参数名" style={{ width: 140 }} />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, 'type']}
                        initialValue="str"
                        noStyle
                      >
                        <Select
                          options={PARAM_TYPE_OPTIONS}
                          style={{ width: 130 }}
                        />
                      </Form.Item>
                      <Form.Item name={[field.name, 'default']} noStyle>
                        <Input placeholder="默认值" style={{ width: 120 }} />
                      </Form.Item>
                      <Form.Item name={[field.name, 'desc']} noStyle>
                        <Input placeholder="说明" style={{ width: 200 }} />
                      </Form.Item>
                      <DeleteOutlined
                        onClick={() => remove(field.name)}
                        style={{ color: '#ff4d4f' }}
                      />
                    </Space>
                  ))}
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    onClick={() => add()}
                  >
                    添加参数
                  </Button>
                </div>
              )}
            </Form.List>
          </Form.Item>

          <Form.Item name="example" label="用法示例(可选)">
            <Input.TextArea
              rows={3}
              style={{ fontFamily: 'monospace' }}
              placeholder={'- 算子名:\n    参数: 值'}
            />
          </Form.Item>

          <Space>
            {canUploadOperator && (
              <Button type="primary" loading={submitting} onClick={onSubmit}>
                上传
              </Button>
            )}
            <Button onClick={() => history.push('/operators')}>取消</Button>
          </Space>
        </Form>
      </Card>
    </PageContainer>
  );
};

export default UploadCustomOperatorPage;
