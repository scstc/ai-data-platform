import { UploadOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import {
  Alert,
  Button,
  Modal,
  message,
  Radio,
  Select,
  Space,
  Upload,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  hostPlatformFiles,
  listCategories,
  uploadDataset,
} from '@/services/data-platform';
import type { AccessType } from './constants';
import { acceptOf, isExtAllowed } from './constants';
import FileManagerPicker, { type PlatformSelection } from './FileManagerPicker';

interface Props {
  open: boolean;
  accessType: AccessType;
  onClose: () => void;
  /** 成功入库后通知宿主刷新列表 */
  onDone: () => void;
}

const MAX_FILE_SIZE = 200 * 1024 * 1024;

/** 从后端错误对象里取 message(400/503 等业务错误,后端返回 {success,message}) */
const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/** 数据接入上传弹框:本地上传 或 文件管理零拷贝引入。 */
const UploadModal: React.FC<Props> = ({
  open,
  accessType,
  onClose,
  onDone,
}) => {
  const [messageApi, contextHolder] = message.useMessage();
  const [categoryId, setCategoryId] = useState<string>();
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [mode, setMode] = useState<'local' | 'platform'>('local');
  const [sel, setSel] = useState<PlatformSelection>({ bucket: '', keys: [] });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMode('local');
    setSel({ bucket: '', keys: [] });
    setCategoryId(undefined);
    listCategories()
      .then((res) =>
        setCategoryOptions(
          res.data.map((c) => ({ label: c.name, value: c.id })),
        ),
      )
      .catch(() => undefined);
  }, [open]);

  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    if (!isExtAllowed(file.name, accessType)) {
      messageApi.error(
        `不支持的文件格式:${file.name},「${accessType.label}」仅支持 ${accessType.extensions.join('、')}`,
      );
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_SIZE) {
      messageApi.error(`文件 ${file.name} 超过 200MB 大小限制`);
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const customRequest: NonNullable<UploadProps['customRequest']> = async (
    opts,
  ) => {
    const { file, onSuccess, onError } = opts;
    const formData = new FormData();
    formData.append('file', file as File);
    formData.append('data_type', accessType.key);
    if (categoryId) formData.append('categoryId', categoryId);
    try {
      const res = await uploadDataset(formData);
      onSuccess?.(res);
      messageApi.success(
        `${(file as File).name} 已接入为数据集「${res.data.name}」`,
      );
      onDone();
    } catch (err) {
      onError?.(err as Error);
      messageApi.error(
        pickErrMsg(
          err,
          `${(file as File).name} 接入失败(文件可能损坏或无法解析)`,
        ),
      );
    }
  };

  const handlePlatformOk = async () => {
    if (!sel.bucket || sel.keys.length === 0) {
      messageApi.error('请先在文件管理中勾选至少一个文件');
      return;
    }
    setSubmitting(true);
    try {
      const res = await hostPlatformFiles({
        bucket: sel.bucket,
        keys: sel.keys,
        dataType: accessType.key,
        categoryId,
      });
      messageApi.success(`已零拷贝接入 ${res.data.length} 个文件`);
      onDone();
      onClose();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '文件管理接入失败,请重试'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={`上传文件 · ${accessType.label}`}
      open={open}
      destroyOnHidden
      onCancel={onClose}
      width={760}
      footer={
        mode === 'platform'
          ? [
              <Button key="cancel" onClick={onClose}>
                取消
              </Button>,
              <Button
                key="ok"
                type="primary"
                loading={submitting}
                onClick={handlePlatformOk}
              >
                接入
              </Button>,
            ]
          : [
              <Button key="close" onClick={onClose}>
                关闭
              </Button>,
            ]
      }
    >
      {contextHolder}
      <Space orientation="vertical" style={{ width: '100%' }} size="middle">
        <Space>
          <span>选择分类:</span>
          <Select
            allowClear
            showSearch={{ optionFilterProp: 'label' }}
            placeholder="请选择分类(可选)"
            style={{ width: 320 }}
            options={categoryOptions}
            value={categoryId}
            onChange={setCategoryId}
          />
        </Space>
        <Space>
          <span>导入方式:</span>
          <Radio.Group
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            options={[
              { label: '本地文件上传', value: 'local' },
              { label: '文件管理文件上传', value: 'platform' },
            ]}
            optionType="button"
          />
        </Space>

        {mode === 'local' ? (
          <Upload
            multiple
            accept={acceptOf(accessType)}
            beforeUpload={beforeUpload}
            customRequest={customRequest}
            showUploadList
          >
            <Button icon={<UploadOutlined />}>选择文件</Button>
          </Upload>
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              title="从文件管理选择的文件为零拷贝引用,不复制副本;删除走「取消托管」,不会删源对象。"
            />
            <FileManagerPicker accessType={accessType} onChange={setSel} />
          </>
        )}
      </Space>
    </Modal>
  );
};

export default UploadModal;
