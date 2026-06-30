import { UploadOutlined } from '@ant-design/icons';
import type { UploadFile, UploadProps } from 'antd';
import {
  Alert,
  Button,
  Modal,
  message,
  Radio,
  Select,
  Space,
  TreeSelect,
  Upload,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import {
  hostPlatformFiles,
  listCategories,
  listDatasets,
  uploadDataset,
  uploadMediaDataset,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
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
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  const [mode, setMode] = useState<'local' | 'platform'>('local');
  const [sel, setSel] = useState<PlatformSelection>({ bucket: '', keys: [] });
  const [submitting, setSubmitting] = useState(false);
  // 媒体批量接入(图/音/视频):暂存文件 → 落入所选数据集的 manifest 版本
  const isMediaBatch = accessType.binary;
  const [datasetId, setDatasetId] = useState<string>();
  const [datasetOptions, setDatasetOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [fileList, setFileList] = useState<UploadFile[]>([]);

  // 目标数据集下拉:按关键词拉取(数据集优先,上传落入其 draft 版本)
  const loadDatasetOptions = useCallback((keyword?: string) => {
    listDatasets({ name: keyword || undefined, pageSize: 50 })
      .then((res) =>
        setDatasetOptions(
          (res.data ?? []).map((d) => ({ label: d.name, value: d.id })),
        ),
      )
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!open) return;
    setMode('local');
    setSel({ bucket: '', keys: [] });
    setCategoryId(undefined);
    setDatasetId(undefined);
    setFileList([]);
    loadDatasetOptions();
    listCategories()
      .then((res) => setCategoryTreeData(toCategoryTreeData(res.data)))
      .catch(() => undefined);
  }, [open, loadDatasetOptions]);

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
    if (!datasetId) {
      messageApi.error('请先选择目标数据集');
      onError?.(new Error('no dataset'));
      return;
    }
    const formData = new FormData();
    formData.append('file', file as File);
    formData.append('datasetId', datasetId);
    try {
      const res = await uploadDataset(formData);
      onSuccess?.(res);
      messageApi.success(
        `${(file as File).name} 已作为表成员落入数据集「${res.data.name}」`,
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

  // 媒体批量接入:一次提交所有暂存文件,后端建一个 manifest 数据集(一文件一行)
  const handleMediaBatchOk = async () => {
    const files = fileList
      .map((f) => f.originFileObj)
      .filter((f): f is NonNullable<typeof f> => !!f);
    if (files.length === 0) {
      messageApi.error('请先选择至少一个文件');
      return;
    }
    if (!datasetId) {
      messageApi.error('请先选择目标数据集');
      return;
    }
    setSubmitting(true);
    try {
      const formData = new FormData();
      for (const f of files) formData.append('files', f);
      formData.append('data_type', accessType.key);
      formData.append('datasetId', datasetId);
      const res = await uploadMediaDataset(formData);
      messageApi.success(
        `已接入 ${files.length} 个文件为数据集「${res.data.name}」的成员版本`,
      );
      onDone();
      onClose();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '媒体批量接入失败,请重试'));
    } finally {
      setSubmitting(false);
    }
  };

  // 暂存校验:扩展名/大小不符直接忽略;合法则 return false(只暂存不自动上传)
  const stageBeforeUpload: NonNullable<UploadProps['beforeUpload']> = (
    file,
  ) => {
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
    return false;
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
          : isMediaBatch
            ? [
                <Button key="cancel" onClick={onClose}>
                  取消
                </Button>,
                <Button
                  key="ok"
                  type="primary"
                  loading={submitting}
                  disabled={fileList.length === 0}
                  onClick={handleMediaBatchOk}
                >
                  接入为一个数据集
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
          <TreeSelect
            allowClear
            showSearch
            treeNodeFilterProp="title"
            treeDefaultExpandAll
            placeholder="请选择分类(可选)"
            style={{ width: 320 }}
            treeData={categoryTreeData}
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

        {mode === 'local' && (
          <Space>
            <span>
              目标数据集 <span style={{ color: '#ff4d4f' }}>*</span>:
            </span>
            <Select
              style={{ width: 360 }}
              placeholder="选择已有数据集(上传文件作为表成员加入)"
              value={datasetId}
              onChange={setDatasetId}
              showSearch
              allowClear
              filterOption={false}
              onSearch={(kw) => loadDatasetOptions(kw)}
              options={datasetOptions}
              notFoundContent="无匹配数据集,请先到「数据集」页新建"
            />
          </Space>
        )}

        {mode === 'local' && isMediaBatch ? (
          <>
            <Alert
              type="info"
              showIcon
              title={`一批${accessType.label.replace('接入', '')}将作为一个清单版本落入所选数据集,可直接用于数据加工。`}
            />
            <Upload
              multiple
              accept={acceptOf(accessType)}
              beforeUpload={stageBeforeUpload}
              fileList={fileList}
              onChange={({ fileList: fl }) => setFileList(fl)}
              listType="picture"
            >
              <Button icon={<UploadOutlined />}>选择文件(可多选)</Button>
            </Upload>
          </>
        ) : mode === 'local' ? (
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
