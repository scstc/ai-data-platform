import { InboxOutlined, RobotOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import type { UploadFile, UploadProps } from 'antd';
import {
  Button,
  Card,
  Input,
  message,
  Select,
  Space,
  Typography,
  Upload,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  listCategories,
  suggestDatasetName,
  uploadBatchDataset,
} from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const { Text } = Typography;
const { Dragger } = Upload;

/** 单一格式可选项(对齐后端 landing.LANDABLE_FORMATS 的非二进制、可规范化格式)。
 *  媒体(图/音/视频)走「多模态」接入,不在此列。 */
const FORMAT_GROUPS: {
  label: string;
  options: { value: string; label: string }[];
}[] = [
  {
    label: '表格类',
    options: [
      { value: 'csv', label: 'CSV (.csv)' },
      { value: 'tsv', label: 'TSV (.tsv)' },
      { value: 'xlsx', label: 'Excel (.xlsx)' },
      { value: 'xls', label: 'Excel (.xls)' },
    ],
  },
  {
    label: '文档类',
    options: [
      { value: 'pdf', label: 'PDF (.pdf)' },
      { value: 'doc', label: 'Word (.doc)' },
      { value: 'docx', label: 'Word (.docx)' },
      { value: 'ppt', label: 'PPT (.ppt)' },
      { value: 'pptx', label: 'PPT (.pptx)' },
      { value: 'html', label: 'HTML (.html)' },
    ],
  },
  {
    label: '文本类',
    options: [
      { value: 'txt', label: '纯文本 (.txt)' },
      { value: 'log', label: '日志 (.log)' },
    ],
  },
  {
    label: '结构化',
    options: [
      { value: 'json', label: 'JSON (.json)' },
      { value: 'jsonl', label: 'JSONL (.jsonl)' },
    ],
  },
];

/** 单文件体积上限,与后端 _MAX_MEDIA_FILE_BYTES(200MB)对齐 */
const MAX_FILE_BYTES = 200 * 1024 * 1024;

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf('.');
  return i >= 0 ? filename.slice(i + 1).toLowerCase() : '';
};

/** 单一数据接入:一批同格式文件 → 原件存内置 MinIO + 合并生成一个 jsonl 数据集。 */
const SingleUploadPage: React.FC = () => {
  const [format, setFormat] = useState<string>();
  const [name, setName] = useState('');
  const [categoryId, setCategoryId] = useState<string>();
  const [categories, setCategories] = useState<
    { label: string; value: string }[]
  >([]);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [naming, setNaming] = useState(false);
  const [catMgrOpen, setCatMgrOpen] = useState(false);
  const access = useAccess();

  const loadCategories = useCallback(() => {
    listCategories()
      .then((res) =>
        setCategories(res.data.map((c) => ({ label: c.name, value: c.id }))),
      )
      .catch(() => {
        /* 分类拉取失败不阻断上传 */
      });
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  // 切换格式时清空已选文件(accept 与扩展名校验随之变化)
  const onFormatChange = (value: string) => {
    setFormat(value);
    setFileList([]);
  };

  // 仅暂存、不自动上传:校验扩展名一致 + 单文件 200MB,提交时统一发送
  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    if (!format) {
      message.warning('请先选择数据格式');
      return Upload.LIST_IGNORE;
    }
    if (getExt(file.name) !== format) {
      message.error(`「${file.name}」不是 .${format} 文件,已忽略`);
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_BYTES) {
      message.error(`「${file.name}」超过单文件 200MB 上限`);
      return Upload.LIST_IGNORE;
    }
    return false;
  };

  // AI 命名:据已选文件名 + 格式 + 分类生成一个数据集名(后端 LLM/启发式)
  const onAiName = async () => {
    if (fileList.length === 0) return;
    setNaming(true);
    try {
      const category = categories.find((c) => c.value === categoryId)?.label;
      const res = await suggestDatasetName({
        filenames: fileList.map((f) => f.name),
        dataType: format ?? '',
        category,
      });
      if (res.success && res.data.name) setName(res.data.name);
    } catch (e: any) {
      message.error(e?.data?.message ?? e?.message ?? 'AI 命名失败');
    } finally {
      setNaming(false);
    }
  };

  const onSubmit = async () => {
    if (!format) {
      message.warning('请选择数据格式');
      return;
    }
    if (fileList.length === 0) {
      message.warning('请至少添加一个文件');
      return;
    }
    const fd = new FormData();
    fileList.forEach((f) => {
      if (f.originFileObj) fd.append('files', f.originFileObj as File);
    });
    fd.append('data_type', format);
    if (name.trim()) fd.append('name', name.trim());
    if (categoryId) fd.append('categoryId', categoryId);

    setSubmitting(true);
    const hide = message.loading('正在上传并生成数据集…', 0);
    try {
      const res = await uploadBatchDataset(fd);
      hide();
      message.success(
        `已生成数据集「${res.data?.name ?? name}」,原件与 jsonl 已存入 MinIO`,
      );
      setFileList([]);
      setName('');
      history.push('/datasets/list');
    } catch (e: any) {
      hide();
      message.error(e?.data?.message ?? e?.message ?? '上传失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '单一数据' },
      ])}
      title="单一数据接入"
      content="一批同一格式文件:原始文件存入内置 MinIO,并合并解析生成一个 jsonl 数据集。"
      onBack={() => history.push('/ingest/local-upload')}
    >
      <Card style={{ maxWidth: 760 }}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Text strong>数据格式</Text>
            <Select
              style={{ width: '100%', marginTop: 8 }}
              placeholder="选择单一格式(同一批文件须为同一格式)"
              value={format}
              onChange={onFormatChange}
              options={FORMAT_GROUPS}
              showSearch
              optionFilterProp="label"
            />
          </div>
          <div>
            <Text strong>数据集名称</Text>
            <Space.Compact style={{ width: '100%', marginTop: 8 }}>
              <Input
                placeholder="可选,留空则取首个文件名"
                value={name}
                onChange={(e) => setName(e.target.value)}
                allowClear
              />
              <Button
                icon={<RobotOutlined />}
                loading={naming}
                disabled={fileList.length === 0}
                onClick={onAiName}
              >
                AI 命名
              </Button>
            </Space.Compact>
          </div>
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <Text strong>分类</Text>
              <a onClick={() => setCatMgrOpen(true)}>管理分类</a>
            </div>
            <Select
              style={{ width: '100%', marginTop: 8 }}
              placeholder="可选"
              value={categoryId}
              onChange={setCategoryId}
              options={categories}
              allowClear
              showSearch
              optionFilterProp="label"
            />
          </div>
          <Dragger
            multiple
            fileList={fileList}
            beforeUpload={beforeUpload}
            onChange={({ fileList: fl }) => setFileList(fl)}
            accept={format ? `.${format}` : undefined}
            disabled={!format}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">
              {format ? `点击或拖拽 .${format} 文件到此处` : '请先选择数据格式'}
            </p>
            <p className="ant-upload-hint">
              支持多文件批量上传,合并为一个数据集;单文件最大 200MB。
            </p>
          </Dragger>
          <Button
            type="primary"
            onClick={onSubmit}
            loading={submitting}
            disabled={!format || fileList.length === 0}
          >
            上传并生成数据集（{fileList.length}）
          </Button>
        </Space>
      </Card>
      <CategoryManager
        open={catMgrOpen}
        canAdmin={!!access.canAdmin}
        onClose={() => setCatMgrOpen(false)}
        onChanged={loadCategories}
      />
    </PageContainer>
  );
};

export default SingleUploadPage;
