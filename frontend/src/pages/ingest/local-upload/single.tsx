import {
  CodeOutlined,
  FileExcelOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  InboxOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import type { UploadFile, UploadProps } from 'antd';
import {
  Alert,
  Button,
  Card,
  Col,
  Modal,
  message,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  theme,
  Upload,
} from 'antd';
import { type ReactNode, useCallback, useState } from 'react';
import { listDatasets, uploadBatchDataset } from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const { Text } = Typography;
const { Dragger } = Upload;

/** 单一格式可选项(对齐后端 landing.LANDABLE_FORMATS 的非二进制、可规范化格式)。
 *  媒体(图/音/视频)走「多模态」接入,不在此列。
 *  icon 用文件类型图标 + 品牌色,卡片化展示一眼可辨。 */
const FORMAT_GROUPS: {
  label: string;
  options: { value: string; label: string; icon: ReactNode; color: string }[];
}[] = [
  {
    label: '表格类',
    options: [
      { value: 'csv', label: 'CSV', icon: <TableOutlined />, color: '#16A34A' },
      { value: 'tsv', label: 'TSV', icon: <TableOutlined />, color: '#16A34A' },
      {
        value: 'xlsx',
        label: 'Excel',
        icon: <FileExcelOutlined />,
        color: '#16A34A',
      },
      {
        value: 'xls',
        label: 'Excel',
        icon: <FileExcelOutlined />,
        color: '#16A34A',
      },
    ],
  },
  {
    label: '文档类',
    options: [
      {
        value: 'pdf',
        label: 'PDF',
        icon: <FilePdfOutlined />,
        color: '#E5484D',
      },
      {
        value: 'doc',
        label: 'Word',
        icon: <FileWordOutlined />,
        color: '#2D7FF9',
      },
      {
        value: 'docx',
        label: 'Word',
        icon: <FileWordOutlined />,
        color: '#2D7FF9',
      },
      {
        value: 'ppt',
        label: 'PPT',
        icon: <FilePptOutlined />,
        color: '#E5701A',
      },
      {
        value: 'pptx',
        label: 'PPT',
        icon: <FilePptOutlined />,
        color: '#E5701A',
      },
      {
        value: 'html',
        label: 'HTML',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
    ],
  },
  {
    label: '文本类',
    options: [
      {
        value: 'txt',
        label: '纯文本',
        icon: <FileTextOutlined />,
        color: '#6B7280',
      },
      {
        value: 'log',
        label: '日志',
        icon: <FileTextOutlined />,
        color: '#6B7280',
      },
    ],
  },
  {
    label: '结构化',
    options: [
      {
        value: 'json',
        label: 'JSON',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
      {
        value: 'jsonl',
        label: 'JSONL',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
    ],
  },
];

/** 内容安全:违规类别 / 严重度展示标签(与后端 review category/severity 对齐) */
const CATEGORY_LABEL: Record<string, string> = {
  porn: '黄',
  gambling: '赌',
  drugs: '毒',
  politics: '政',
  terrorism: '恐',
  pii: '隐私',
  other: '其他',
};
const SEVERITY_LABEL: Record<string, string> = {
  high: '高危',
  medium: '中危',
  low: '低危',
};
const SEVERITY_TAG_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'default',
};

/** 单文件体积上限,与后端 _MAX_MEDIA_FILE_BYTES(200MB)对齐 */
const MAX_FILE_BYTES = 200 * 1024 * 1024;

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf('.');
  return i >= 0 ? filename.slice(i + 1).toLowerCase() : '';
};

/** 单一数据接入:一批同格式文件 → 原件存内置 MinIO + 合并生成一个 jsonl 数据集。 */
const SingleUploadPage: React.FC = () => {
  const [format, setFormat] = useState<string>();
  const [datasetId, setDatasetId] = useState<string>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [safetyCheck, setSafetyCheck] = useState(true);
  const [safetyUseLlm, setSafetyUseLlm] = useState(false);
  const [datasetOptions, setDatasetOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [datasetLoading, setDatasetLoading] = useState(false);

  // 目标数据集下拉:按关键词拉取已有数据集(数据集优先流程,上传落入其 draft 版本)
  const loadDatasetOptions = useCallback((keyword?: string) => {
    setDatasetLoading(true);
    listDatasets({ name: keyword || undefined, pageSize: 50 })
      .then((res) =>
        setDatasetOptions(
          (res.data ?? []).map((d) => ({ label: d.name, value: d.id })),
        ),
      )
      .catch(() => {
        /* 拉取失败不阻断,留空列表 */
      })
      .finally(() => setDatasetLoading(false));
  }, []);
  const [blockReport, setBlockReport] = useState<{
    report: DataPlatform.ReviewReportBody;
    findings: DataPlatform.ReviewFinding[];
    ratio: number;
    highSeverity: number;
  } | null>(null);
  const { token } = theme.useToken();

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

  const onSubmit = async () => {
    if (!format) {
      message.warning('请选择数据格式');
      return;
    }
    if (fileList.length === 0) {
      message.warning('请至少添加一个文件');
      return;
    }
    if (!datasetId) {
      message.warning('请选择目标数据集');
      return;
    }
    const fd = new FormData();
    fileList.forEach((f) => {
      if (f.originFileObj) fd.append('files', f.originFileObj as File);
    });
    fd.append('datasetId', datasetId);
    fd.append('safety_check', String(safetyCheck));
    fd.append('safety_use_llm', String(safetyUseLlm));

    setSubmitting(true);
    const hide = message.loading(
      safetyCheck ? '正在上传并审核内容安全…' : '正在上传并落入数据集…',
      0,
    );
    try {
      const res = await uploadBatchDataset(fd, { skipErrorHandler: true });
      hide();
      message.success(
        `已上传并作为表成员落入数据集「${res.data?.name ?? ''}」`,
      );
      setFileList([]);
      history.push(`/datasets/detail?id=${datasetId}`);
    } catch (e: any) {
      hide();
      const body = e?.response?.data ?? e?.data;
      if (body?.reviewReport) {
        setBlockReport({
          report: body.reviewReport,
          findings: body.findings ?? [],
          ratio: body.ratio ?? 0,
          highSeverity: body.highSeverity ?? 0,
        });
      } else {
        message.error(body?.message ?? e?.message ?? '上传失败,请重试');
      }
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
            <div style={{ marginTop: 8 }}>
              {FORMAT_GROUPS.map((group) => (
                <div key={group.label} style={{ marginBottom: 14 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {group.label}
                  </Text>
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 8,
                      marginTop: 6,
                    }}
                  >
                    {group.options.map((opt) => {
                      const selected = format === opt.value;
                      return (
                        <div
                          key={opt.value}
                          onClick={() => onFormatChange(opt.value)}
                          style={{
                            width: 84,
                            padding: '10px 8px',
                            textAlign: 'center',
                            cursor: 'pointer',
                            borderRadius: 8,
                            border: `1px solid ${
                              selected
                                ? token.colorPrimary
                                : token.colorBorderSecondary
                            }`,
                            background: selected
                              ? token.colorPrimaryBg
                              : token.colorBgContainer,
                            transition: 'all 0.2s',
                          }}
                        >
                          <span style={{ fontSize: 22, color: opt.color }}>
                            {opt.icon}
                          </span>
                          <div
                            style={{
                              marginTop: 4,
                              fontSize: 13,
                              fontWeight: 500,
                            }}
                          >
                            {opt.label}
                          </div>
                          <div
                            style={{
                              fontSize: 11,
                              color: token.colorTextTertiary,
                            }}
                          >
                            .{opt.value}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <Text strong>
              目标数据集 <Text type="danger">*</Text>
            </Text>
            <div style={{ marginTop: 8 }}>
              <Select
                style={{ width: '100%' }}
                placeholder="选择已有数据集(上传的文件将作为表成员加入)"
                value={datasetId}
                onChange={setDatasetId}
                showSearch
                allowClear
                filterOption={false}
                onSearch={(kw) => loadDatasetOptions(kw)}
                onFocus={() => loadDatasetOptions()}
                options={datasetOptions}
                notFoundContent={
                  datasetLoading
                    ? '加载中…'
                    : '无匹配数据集,请先到「数据集」页新建'
                }
              />
            </div>
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
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 8,
            }}
          >
            <Space align="center">
              <Switch checked={safetyCheck} onChange={setSafetyCheck} />
              <Text strong>内容安全预检</Text>
            </Space>
            {safetyCheck && (
              <Space align="center" size={6}>
                <Switch
                  size="small"
                  checked={safetyUseLlm}
                  onChange={setSafetyUseLlm}
                />
                <Text type="secondary" style={{ fontSize: 13 }}>
                  LLM 深度审核(较慢)
                </Text>
              </Space>
            )}
          </div>
          {safetyCheck && (
            <Text type="secondary" style={{ fontSize: 12, marginTop: -8 }}>
              高危内容或违规占比 ≥10% 将拦截创建数据集
            </Text>
          )}
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
      <Modal
        open={!!blockReport}
        title="内容安全预检未通过"
        width={640}
        onCancel={() => setBlockReport(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setBlockReport(null)}>
            知道了
          </Button>,
        ]}
      >
        {blockReport && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="error"
              showIcon
              message="数据集未创建,原始文件已回收"
              description={`扫描 ${blockReport.report.scannedRows} 行,命中违规 ${blockReport.report.flaggedRows} 行(占比 ${(blockReport.ratio * 100).toFixed(1)}%),其中高危 ${blockReport.highSeverity} 条。请清理后重新上传。`}
            />
            <Row gutter={16}>
              <Col span={8}>
                <Statistic
                  title="违规行数"
                  value={blockReport.report.flaggedRows}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="违规占比"
                  value={`${(blockReport.ratio * 100).toFixed(1)}%`}
                  valueStyle={{ color: '#cf1322' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="高危命中"
                  value={blockReport.highSeverity}
                  valueStyle={{ color: '#cf1322' }}
                />
              </Col>
            </Row>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                违规类别
              </Text>
              <div style={{ marginTop: 4 }}>
                {Object.entries(blockReport.report.byCategory || {}).map(
                  ([k, v]) => (
                    <Tag key={k} color="red" style={{ marginBottom: 4 }}>
                      {CATEGORY_LABEL[k] ?? k}: {v}
                    </Tag>
                  ),
                )}
              </div>
            </div>
            {blockReport.findings.length > 0 && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  命中样例(前 {blockReport.findings.length} 条)
                </Text>
                <Table<DataPlatform.ReviewFinding>
                  size="small"
                  rowKey={(r) => `${r.rowIndex}-${r.source}`}
                  pagination={false}
                  dataSource={blockReport.findings}
                  style={{ marginTop: 4 }}
                  columns={[
                    { title: '行', dataIndex: 'rowIndex', width: 60 },
                    {
                      title: '类别',
                      dataIndex: 'category',
                      width: 90,
                      render: (c: string) => CATEGORY_LABEL[c] ?? c,
                    },
                    {
                      title: '严重度',
                      dataIndex: 'severity',
                      width: 80,
                      render: (s: string) => (
                        <Tag color={SEVERITY_TAG_COLOR[s]}>
                          {SEVERITY_LABEL[s] ?? s}
                        </Tag>
                      ),
                    },
                    { title: '片段', dataIndex: 'snippet', ellipsis: true },
                  ]}
                />
              </div>
            )}
          </Space>
        )}
      </Modal>
    </PageContainer>
  );
};

export default SingleUploadPage;
