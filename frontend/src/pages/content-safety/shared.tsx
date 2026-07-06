// 内容安全共用:类别/严重度/来源元数据、Tag 渲染与命中明细表,
// 供 index(配置+规则库)与 report(独立报告页)两页共用。
import type { ProColumns } from '@ant-design/pro-components';
import { ProTable } from '@ant-design/pro-components';
import { Space, Tag, Typography } from 'antd';
import { listReviewFindings } from '@/services/data-platform';

const { Text } = Typography;

export const CATEGORY_META: Record<
  DataPlatform.ReviewCategory,
  { text: string; color: string }
> = {
  porn: { text: '色情', color: 'magenta' },
  gambling: { text: '赌博', color: 'gold' },
  drugs: { text: '毒品', color: 'volcano' },
  politics: { text: '涉政', color: 'red' },
  terrorism: { text: '涉恐', color: 'red' },
  pii: { text: '隐私', color: 'blue' },
  other: { text: '其他', color: 'default' },
};

export const SEVERITY_META: Record<
  DataPlatform.ReviewSeverity,
  { text: string; color: string }
> = {
  high: { text: '高', color: 'error' },
  medium: { text: '中', color: 'warning' },
  low: { text: '低', color: 'default' },
};

export const SOURCE_META: Record<DataPlatform.ReviewSource, string> = {
  keyword: '自定义敏感词',
  regex: '自定义正则',
  flagged_words: '内置敏感词',
  llm: 'LLM 审核',
  pii: 'PII 识别',
};

// 类别多选项(黄赌毒政恐 + 隐私/其他)
export const CATEGORY_OPTIONS = (
  Object.keys(CATEGORY_META) as DataPlatform.ReviewCategory[]
).map((c) => ({ label: CATEGORY_META[c].text, value: c }));

export const renderCategory = (c: DataPlatform.ReviewCategory) => {
  const m = CATEGORY_META[c] ?? CATEGORY_META.other;
  return <Tag color={m.color}>{m.text}</Tag>;
};

export const renderSeverity = (s: DataPlatform.ReviewSeverity) => {
  const m = SEVERITY_META[s] ?? SEVERITY_META.low;
  return <Tag color={m.color}>{m.text}</Tag>;
};

export const renderSource = (s: DataPlatform.ReviewSource) =>
  SOURCE_META[s] ?? s;

/** 计数 map → Tag 列表(空则占位) */
export const CountTags: React.FC<{
  counts: Record<string, number>;
  label: (k: string) => React.ReactNode;
}> = ({ counts, label }) => {
  const entries = Object.entries(counts ?? {}).filter(([, v]) => v > 0);
  if (!entries.length) return <Text type="secondary">无</Text>;
  return (
    <Space size={[4, 8]} wrap>
      {entries.map(([k, v]) => (
        <Tag key={k}>
          {label(k)} {v}
        </Tag>
      ))}
    </Space>
  );
};

/** 命中明细表(按类别·来源·严重度筛,接 listReviewFindings)。
 * member:多表版本按左侧文件列表选中的成员表过滤(替代原「所属表」下拉筛选)。 */
export const FindingsTable: React.FC<{ jobId: string; member?: string }> = ({
  jobId,
  member,
}) => {
  const columns: ProColumns<DataPlatform.ReviewFinding>[] = [
    { title: '行号', dataIndex: 'rowIndex', width: 80, search: false },
    {
      title: '类别',
      dataIndex: 'category',
      width: 90,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(CATEGORY_META) as DataPlatform.ReviewCategory[]).map(
          (c) => [c, { text: CATEGORY_META[c].text }],
        ),
      ),
      render: (_, r) => renderCategory(r.category),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(SEVERITY_META) as DataPlatform.ReviewSeverity[]).map(
          (s) => [s, { text: SEVERITY_META[s].text }],
        ),
      ),
      render: (_, r) => renderSeverity(r.severity),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 120,
      valueType: 'select',
      valueEnum: Object.fromEntries(
        (Object.keys(SOURCE_META) as DataPlatform.ReviewSource[]).map((s) => [
          s,
          { text: SOURCE_META[s] },
        ]),
      ),
      render: (_, r) => renderSource(r.source),
    },
    { title: '命中详情', dataIndex: 'detail', width: 160, search: false },
    {
      title: '片段',
      dataIndex: 'snippet',
      ellipsis: true,
      search: false,
    },
  ];

  return (
    <ProTable<DataPlatform.ReviewFinding, DataPlatform.ReviewFindingListParams>
      key={member}
      rowKey={(r) => `${r.rowIndex}-${r.source}-${r.category}-${r.detail}`}
      size="small"
      options={false}
      search={{ labelWidth: 'auto' }}
      columns={columns}
      pagination={{ pageSize: 10 }}
      request={async (params) => {
        const res = await listReviewFindings(jobId, {
          current: params.current,
          pageSize: params.pageSize,
          category: params.category,
          source: params.source,
          severity: params.severity,
          tableName: member,
        });
        return { data: res.data, total: res.total, success: res.success };
      }}
    />
  );
};
