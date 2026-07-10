import type { ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Alert, Tag, Tooltip, Typography } from 'antd';
import dayjs from 'dayjs';
import type React from 'react';
import { listAuditLogs } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

/** 写操作方法枚举（搜索下拉；读请求不记审计，故不含 GET） */
const METHOD_ENUM = {
  POST: { text: 'POST' },
  PUT: { text: 'PUT' },
  PATCH: { text: 'PATCH' },
  DELETE: { text: 'DELETE' },
};

/** 动作码资源段 → 中文（含后端别名与未别名的原始路径段两种形态） */
const RESOURCE_ZH: Record<string, string> = {
  dataset: '数据集',
  datasets: '数据集',
  datasource: '数据源',
  datasources: '数据源',
  datasetVersion: '数据集版本',
  'dataset-versions': '数据集版本',
  dataLake: '数据湖',
  'data-lakes': '数据湖',
  ingestTask: '采集任务',
  'ingest-tasks': '采集任务',
  job: '任务',
  jobs: '任务',
  pipeline: '治理流水线',
  pipelines: '治理流水线',
  operator: '算子',
  operators: '算子',
  upload: '上传文件',
  uploads: '上传文件',
  reviewJob: '内容审核任务',
  'content-safety': '内容审核',
  tag: '标签',
  tags: '标签',
  category: '类目',
  categories: '类目',
  user: '用户',
  role: '角色',
  menu: '菜单',
  dept: '部门',
  permission: '权限',
  'llm-config': 'LLM 配置',
  quality: '质量评估',
  augment: '数据增强任务',
  make: '数据合并任务',
  trainset: '数据合成任务',
  construct: '数据构造任务',
  distillation: '数据蒸馏任务',
  evaluation: '评估任务',
  export: '导出任务',
  files: '文件',
  notifications: '通知',
  'ingest-push': '推送数据',
  'model-store': '模型',
  login: '登录会话',
};

/** 动作码动词段 → 中文 */
const VERB_ZH: Record<string, string> = {
  create: '新建',
  update: '编辑',
  delete: '删除',
  publish: '发布',
  unpublish: '取消发布',
  verdict: '安全结论覆盖',
  acl: '权限变更',
  cancel: '取消',
  run: '运行',
  retry: '重试',
  clean: '清洗',
  extract: '抽取',
};

/** 拆动作码 "resource.verb"（verb 取末段，其余归资源段） */
const splitAction = (action: string): [string, string] => {
  const idx = action.lastIndexOf('.');
  if (idx < 0) return [action, ''];
  return [action.slice(0, idx), action.slice(idx + 1)];
};

/** 组一句人话：admin 删除了数据集「客服语料」；映射不到时回退原始动作码 */
const describeLog = (r: DataPlatform.AuditLog): string => {
  const [resource, verb] = splitAction(r.action);
  const resourceZh = RESOURCE_ZH[resource];
  const verbZh = VERB_ZH[verb];
  const obj = r.targetName || r.target;
  const objPart = obj ? `「${obj}」` : '';
  const ok = r.statusCode < 400;
  if (!resourceZh || !verbZh) {
    return `${r.username} 执行了 ${r.action}${objPart}${ok ? '' : `（未成功，${r.statusCode}）`}`;
  }
  return ok
    ? `${r.username} ${verbZh}了${resourceZh}${objPart}`
    : `${r.username} 尝试${verbZh}${resourceZh}${objPart} 未成功（${r.statusCode}）`;
};

/** 动作码 → 中文短标签（表格「动作」列）；映射不到显示原始码 */
const actionLabel = (action: string): string => {
  const [resource, verb] = splitAction(action);
  const resourceZh = RESOURCE_ZH[resource];
  const verbZh = VERB_ZH[verb];
  return resourceZh && verbZh ? `${verbZh}${resourceZh}` : action;
};

/**
 * 动作搜索输入 → 后端动作码查询串（后端对动作码 ilike 模糊匹配）。
 * 表格展示的是中文，用户自然输中文，这里做反向翻译：
 * - 整句「删除标签」→ "tag.delete"（精确码）
 * - 只输动词「删除」→ "delete"（匹配所有删除类动作）
 * - 只输资源「标签」→ "tag."（匹配该资源的所有动作）
 * - 都不匹配则原样透传（支持直接输动作码）
 */
const actionQueryOf = (input: string): string => {
  const t = input.trim();
  if (!t) return t;
  for (const [resourceKey, resourceZh] of Object.entries(RESOURCE_ZH)) {
    for (const [verbKey, verbZh] of Object.entries(VERB_ZH)) {
      if (`${verbZh}${resourceZh}` === t) return `${resourceKey}.${verbKey}`;
    }
  }
  for (const [verbKey, verbZh] of Object.entries(VERB_ZH)) {
    if (verbZh === t) return verbKey;
  }
  for (const [resourceKey, resourceZh] of Object.entries(RESOURCE_ZH)) {
    if (resourceZh === t) return `${resourceKey}.`;
  }
  return t;
};

/** HTTP 状态码配色：2xx 成功 / 4xx 客户端错误（含 403 越权拦截）/ 5xx 服务端错误 */
const statusColor = (code: number) => {
  if (code >= 500) return 'error';
  if (code >= 400) return 'warning';
  if (code >= 200 && code < 300) return 'success';
  return 'default';
};

const Security: React.FC = () => {
  const columns: ProColumns<DataPlatform.AuditLog>[] = [
    {
      title: '时间',
      dataIndex: 'createdAt',
      search: false,
      width: 160,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      // 仅作搜索条件用的时间区间（不在表格中展示列）
      title: '时间',
      dataIndex: 'createdAt',
      key: 'createdAtRange',
      valueType: 'dateRange',
      hideInTable: true,
    },
    {
      title: '操作描述',
      key: 'description',
      search: false,
      ellipsis: true,
      render: (_, r) => describeLog(r),
    },
    { title: '用户', dataIndex: 'username', width: 100 },
    {
      title: '动作',
      dataIndex: 'action',
      width: 150,
      fieldProps: { placeholder: '如：删除标签 / 删除 / tag.delete' },
      render: (_, r) => (
        <Tooltip title={r.action}>
          <Tag>{actionLabel(r.action)}</Tag>
        </Tooltip>
      ),
    },
    {
      title: '方法',
      dataIndex: 'method',
      valueType: 'select',
      valueEnum: METHOD_ENUM,
      width: 90,
      render: (_, r) => <Tag>{r.method}</Tag>,
    },
    {
      title: '对象',
      dataIndex: 'targetName',
      search: false,
      ellipsis: true,
      width: 160,
      render: (_, r) =>
        r.targetName ? (
          <Tooltip title={r.target}>{r.targetName}</Tooltip>
        ) : (
          (r.target ?? '-')
        ),
    },
    { title: 'IP', dataIndex: 'ip', search: false, width: 120 },
    {
      title: '路径',
      dataIndex: 'path',
      search: false,
      ellipsis: true,
      render: (_, r) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {r.path}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'statusCode',
      search: false,
      width: 80,
      render: (_, r) => (
        <Tag color={statusColor(r.statusCode)}>{r.statusCode}</Tag>
      ),
    },
  ];

  return (
    <PageContainer>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="仅管理员可见，记录所有写操作（谁 / 何时 / 从哪个 IP / 对哪个对象 / 做了什么）。读请求不记录。"
      />
      <ProTable<DataPlatform.AuditLog>
        headerTitle="操作审计日志"
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        options={{ reload: true }}
        request={async (params) => {
          const range = params.createdAt as [string, string] | undefined;
          const res = await listAuditLogs({
            current: params.current,
            pageSize: params.pageSize,
            username: params.username || undefined,
            action: params.action ? actionQueryOf(params.action) : undefined,
            method: params.method || undefined,
            // dateRange 给的是纯日期:起取当日 0 点、止取当日 23:59:59,
            // 否则会漏掉当天的记录(与数据集列表一致)
            createdStart: range?.[0]
              ? dayjs(range[0]).startOf('day').toISOString()
              : undefined,
            createdEnd: range?.[1]
              ? dayjs(range[1]).endOf('day').toISOString()
              : undefined,
          });
          // 倒序由后端保证
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />
    </PageContainer>
  );
};

export default Security;
