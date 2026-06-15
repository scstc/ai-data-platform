import { InboxOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { UploadProps } from 'antd';
import { Alert, message, Select, Space, Tag, Typography, Upload } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listCategories,
  listDatasets,
  uploadDataset,
} from '@/services/data-platform';
import {
  ACCEPT,
  ALLOWED_EXTENSIONS,
  isAllowedExtension,
  MAX_FILE_SIZE,
} from './utils';

const { Dragger } = Upload;
const { Paragraph, Text } = Typography;

const UploadPage: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [categoryId, setCategoryId] = useState<string>();
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
    } catch {
      // 静默：分类不可用不应阻断上传
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  /** 上传前校验：扩展名白名单 + 单文件 ≤200MB；不合法则 message.error 并拒绝 */
  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    if (!isAllowedExtension(file.name)) {
      messageApi.error(
        `不支持的文件格式：${file.name}，仅支持 ${ALLOWED_EXTENSIONS.join('、')}`,
      );
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_SIZE) {
      messageApi.error(`文件 ${file.name} 超过 200MB 大小限制`);
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  /** 上传即落地为受管数据集（POST /datasets/upload），成功后刷新数据集列表 */
  const customRequest: NonNullable<UploadProps['customRequest']> = async (
    options,
  ) => {
    const { file, onSuccess, onError } = options;
    const formData = new FormData();
    formData.append('file', file as File);
    if (categoryId) formData.append('categoryId', categoryId);
    try {
      const res = await uploadDataset(formData);
      onSuccess?.(res);
      messageApi.success(
        `${(file as File).name} 已入库为数据集「${res.data.name}」，可在「数据加工」中选择它`,
      );
      actionRef.current?.reload();
    } catch (err) {
      onError?.(err as Error);
      messageApi.error(
        `${(file as File).name} 上传失败（文件可能损坏或内容无法解析）`,
      );
    }
  };

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    { title: '数据集名', dataIndex: 'name', ellipsis: true },
    {
      title: '类型',
      dataIndex: 'dataType',
      width: 120,
      render: (_, r) => (r.dataType ? <Tag color="blue">{r.dataType}</Tag> : '-'),
    },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '创建人', dataIndex: 'creator', width: 100 },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 180,
      valueType: 'dateTime',
    },
  ];

  return (
    <PageContainer>
      {contextHolder}
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title="上传的文件会自动成为受管数据集（v1）并进入数据集仓库；随后可在「数据加工」中选择它新建加工任务。"
      />
      <Space style={{ marginBottom: 16 }}>
        <Text type="secondary">分类（可选）：</Text>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="为本次上传的数据集选择分类"
          style={{ width: 280 }}
          options={categoryOptions}
          value={categoryId}
          onChange={(v) => setCategoryId(v)}
        />
      </Space>
      <Dragger
        name="file"
        multiple
        accept={ACCEPT}
        beforeUpload={beforeUpload}
        customRequest={customRequest}
        showUploadList
        style={{ marginBottom: 16 }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
        <p className="ant-upload-hint">支持多文件上传，单个文件不超过 200MB</p>
      </Dragger>

      <Paragraph style={{ marginBottom: 24 }}>
        <Text type="secondary">支持的文件格式（上传即落地为数据集）：</Text>
        <Space size={[4, 8]} wrap style={{ marginTop: 8 }}>
          {ALLOWED_EXTENSIONS.map((ext) => (
            <Tag key={ext}>.{ext}</Tag>
          ))}
        </Space>
      </Paragraph>

      <ProTable<DataPlatform.Dataset>
        headerTitle="数据集（上传即入库）"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        columns={columns}
        request={async (params) => {
          const { current, pageSize } = params;
          const res = await listDatasets({ current, pageSize });
          return {
            data: res.data,
            total: res.total,
            success: res.success,
          };
        }}
      />
    </PageContainer>
  );
};

export default UploadPage;
