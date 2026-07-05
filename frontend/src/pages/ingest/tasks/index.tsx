import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDependency,
  ProFormDigit,
  ProFormRadio,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProFormTextArea,
  ProFormTreeSelect,
  ProTable,
  StepsForm,
} from '@ant-design/pro-components';
import { Access, useAccess } from '@umijs/max';
import {
  Button,
  Collapse,
  Drawer,
  Form,
  Modal,
  message,
  Popconfirm,
  Progress,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  createIngestTask,
  deleteIngestTask,
  getIngestTask,
  ingestTaskStats,
  listCategories,
  listDataLakes,
  listDataSources,
  listDatasourceTables,
  listIngestRuns,
  listIngestTasks,
  rerunIngestTask,
  stopIngestTask,
  updateIngestTask,
} from '@/services/data-platform';
import {
  type CategoryTreeNode,
  toCategoryTreeData,
} from '@/utils/categoryTree';
import { formatDateTime } from '@/utils/format';
import FilterOperatorPicker from './components/FilterOperatorPicker';
import Dashboard, { type IngestTaskStatsData } from './Dashboard';

/** 状态 → 中文标签与 Tag 颜色 */
const STATUS_META: Record<
  DataPlatform.IngestTask['status'],
  { text: string; color: string }
> = {
  pending: { text: '待运行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
};

/** 质量门结论 → 中文标签 / Tag 颜色 / Timeline 节点颜色（切片 B）
 *  导出供 index.test.tsx 断言映射正确性（ProDescriptions 在测试中被桩代，
 *  无法直接断言 Drawer 内 DOM，故把映射列为契约） */
export const VERDICT_META: Record<
  DataPlatform.QualityVerdict,
  { text: string; color: string; dot: string }
> = {
  skipped: { text: '未校验', color: 'default', dot: 'gray' },
  passed: { text: '已通过', color: 'success', dot: 'green' },
  failed: { text: '未通过', color: 'error', dot: 'red' },
};

/** 格式化调度展示：单次 / cron 表达式 */
const renderSchedule = (schedule: DataPlatform.IngestSchedule) =>
  schedule.mode === 'once' ? (
    <Tag>单次</Tag>
  ) : (
    <span>
      <Tag color="blue">Cron</Tag>
      <Typography.Text code>{schedule.cron}</Typography.Text>
    </span>
  );

/** Cron 表达式客户端轻校验(5 字段标准 crontab:分 时 日 月 周)。
 *  仅作格式初检,严格语义校验由后端 APScheduler CronTrigger.from_crontab 完成。
 *  返回 undefined=通过,字符串=错误提示(供 ProFormText rules validator 使用)。
 *  导出供 index.test.tsx 断言校验契约(5 段格式 + 字符集)。 */
export const validateCronFields = (
  value: string | undefined,
): string | undefined => {
  if (!value || !value.trim()) return '请输入 cron 表达式';
  const fields = value.trim().split(/\s+/);
  if (fields.length !== 5) return 'cron 表达式须为 5 段(分 时 日 月 周)';
  // 每段允许 * / - , 数字(含 L/W step 等扩展字符由后端判定),这里只做粗筛
  if (!fields.every((f) => /^[*/\d,-]+$/.test(f))) {
    return 'cron 段只能含数字与 * / - , 字符';
  }
  return undefined;
};

/** 调度方式选项(切片 C:Cron 已启用) */
const SCHEDULE_OPTIONS = [
  { label: '单次', value: 'once' },
  { label: 'Cron 周期', value: 'cron' },
];

/** 增量配置(切片 C,任务级,与 qualityPolicy 并列)。
 *  - DB 数据源:column(列名)+ type(timestamp|integer)
 *  - s3/hdfs 数据源:by(mtime|name)
 *  - 空=全量采集(incremental 不写入载荷)
 *  说明:此片段共享给新建向导 step-4「落地确认」与编辑 ModalForm。
 *  按 datasource.type 条件渲染(由调用方包 ProFormDependency 切数据源)。 */
const renderIncrementalFields = (ds: DataPlatform.DataSource | undefined) => {
  if (!ds) return null;
  if (ds.type === 'database') {
    return (
      <>
        <ProFormText
          name={['incremental', 'column']}
          label="增量列（可选）"
          tooltip="DB 数据库按此列的高水位推进;留空=全量。须与下方「类型」同时填写,否则后端拒绝"
          placeholder="如 updated_at 或 id"
        />
        <ProFormSelect
          name={['incremental', 'type']}
          label="增量列类型"
          tooltip="timestamp=按时间戳水位;integer=按自增主键水位"
          options={[
            { label: 'timestamp', value: 'timestamp' },
            { label: 'integer', value: 'integer' },
          ]}
          placeholder="与「增量列」配套选择"
          allowClear
        />
      </>
    );
  }
  if (ds.type === 's3' || ds.type === 'hdfs') {
    return (
      <ProFormSelect
        name={['incremental', 'by']}
        label="增量按（可选）"
        tooltip="按对象 mtime(修改时间)或 name(字典序)推进;留空=全量"
        options={[
          { label: 'mtime（修改时间）', value: 'mtime' },
          { label: 'name（文件名）', value: 'name' },
        ]}
        placeholder="选择增量维度"
        allowClear
      />
    );
  }
  return null;
};

/** 把 qualityPolicy 全空（未填阈值 + 未开开关）与 incremental 全空(未填任何字段)
 *  的策略字段从载荷中剔除——避免后端存入形式上配置了但实际无任何检查/过滤的载荷
 *  (会让质量门 verdict 落 passed 而非 skipped;incremental 会被后端 model_validator 拒绝)。
 *  保留原函数名以最小化改动;incremental 剪枝是切片 C 在此基础上的扩展。 */
const pruneEmptyQualityPolicy = <
  T extends {
    qualityPolicy?: DataPlatform.QualityPolicy;
    incremental?: Record<string, unknown>;
  },
>(
  values: T,
): T => {
  let out = values;
  const p = out.qualityPolicy;
  if (p && p.maxNullRate == null && !p.blockOnSchemaDrift) {
    out = { ...out, qualityPolicy: undefined };
  }
  const inc = out.incremental;
  // incremental 任一子字段都没有值 → 视为未配置,从载荷剔除
  if (inc && !Object.values(inc).some((v) => v != null && v !== '')) {
    out = { ...out, incremental: undefined };
  }
  return out;
};

const IngestTasksPage: React.FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('ingest:task:add');
  const canEdit = access.hasPerm('ingest:task:edit');
  const canRun = access.hasPerm('ingest:task:run');
  const canStop = access.hasPerm('ingest:task:stop');
  const canRemove = access.hasPerm('ingest:task:remove');
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentRow, setCurrentRow] = useState<DataPlatform.IngestTask>();
  // 页顶概览统计,驱动 Dashboard。随列表 request(含 reload/轮询)一同刷新。
  const [stats, setStats] = useState<IngestTaskStatsData>();
  const refreshStats = () => {
    ingestTaskStats()
      .then((res) => {
        if (res?.success) setStats(res);
      })
      .catch(() => undefined);
  };
  // 数据源 id → 数据源（用于按所选数据源类型条件渲染"采集对象"）
  const [dsMap, setDsMap] = useState<Record<string, DataPlatform.DataSource>>(
    {},
  );
  const [runs, setRuns] = useState<DataPlatform.IngestRun[]>([]);
  const [editRow, setEditRow] = useState<DataPlatform.IngestTask>();
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryTreeData, setCategoryTreeData] = useState<CategoryTreeNode[]>(
    [],
  );
  // 新建向导：Modal 可见性 + 步骤间上下文（step-1 完成时记 datasourceId，
  // 供后续步骤按数据源类型条件渲染——StepsForm 各步是独立 form，
  // 跨步值不自动透传，故用 React state 承载）
  const [createOpen, setCreateOpen] = useState(false);
  const [wizardCtx, setWizardCtx] = useState<{
    datasourceId?: string;
  }>({});

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryTreeData(toCategoryTreeData(res.data));
    } catch {
      // 静默：分类筛选不可用不应阻断列表
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  /** 打开详情 Drawer：拉取最新单任务（GET 只读，如实返回状态/产物）+ 运行记录 */
  const openDetail = async (id: string) => {
    const res = await getIngestTask(id);
    if (res?.success) {
      setCurrentRow(res.data);
      setDetailOpen(true);
      const runRes = await listIngestRuns(id, { pageSize: 50 }).catch(
        () => null,
      );
      setRuns(runRes?.data ?? []);
    }
  };

  /** 停止运行中的任务 */
  const handleStop = async (id: string) => {
    const hide = message.loading('正在停止…', 0);
    try {
      await stopIngestTask(id);
      hide();
      message.success('任务已停止');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('停止失败，请重试');
    }
  };

  /** 重跑任务 */
  const handleRerun = async (id: string) => {
    const hide = message.loading('正在运行…', 0);
    try {
      await rerunIngestTask(id);
      hide();
      message.success('任务已启动');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('运行失败，请重试');
    }
  };

  /** 删除任务 */
  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteIngestTask(id);
      hide();
      message.success('删除成功');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  /** 渲染采集对象字段：按数据源类型(database/s3/hdfs)分支
   * 共享给编辑弹窗(走 ProFormDependency 读表单 datasourceId)与新建向导 step-2(读 wizardCtx)
   * includeFilterOp=true 时把过滤算子内联（编辑弹窗旧行为）；向导中过滤算子后移到 step-4
   */
  const renderExtractFieldsForDs = (
    ds: DataPlatform.DataSource | undefined,
    datasourceId: string | undefined,
    includeFilterOp: boolean,
  ) => {
    if (!ds) return null;
    // 数据库类型：table / sql 模式
    if (ds.type === 'database') {
      return (
        <>
          <ProFormRadio.Group
            name={['extract', 'mode']}
            label="采集对象"
            tooltip="数据库类型：整张表或自定义 SQL。PostgreSQL 可真连，其余品牌视驱动状态"
            options={[
              { label: '整张表', value: 'table' },
              { label: '自定义 SQL', value: 'sql' },
            ]}
          />
          <ProFormDependency name={[['extract', 'mode']]}>
            {({ extract }) =>
              extract?.mode === 'sql' ? (
                <ProFormTextArea
                  name={['extract', 'sql']}
                  label="SQL"
                  placeholder="如 SELECT * FROM your_table"
                  fieldProps={{ rows: 3 }}
                  rules={[{ required: true, message: '请输入 SQL' }]}
                />
              ) : extract?.mode === 'table' ? (
                <ProFormSelect
                  name={['extract', 'tables']}
                  label="选择表"
                  mode="multiple"
                  placeholder="选择一张或多张表（各表作为成员落入目标数据集的一个版本）"
                  rules={[{ required: true, message: '请至少选择一张表' }]}
                  params={{ datasourceId }}
                  request={async () => {
                    if (!datasourceId) return [];
                    try {
                      const res = await listDatasourceTables(datasourceId);
                      return (res.data ?? []).map((t) => ({
                        label: t,
                        value: t,
                      }));
                    } catch {
                      return [];
                    }
                  }}
                  fieldProps={{ showSearch: true }}
                />
              ) : null
            }
          </ProFormDependency>
          {includeFilterOp && (
            <Form.Item
              name={['extract', 'operators']}
              label="过滤算子（可选）"
              tooltip="采集到的记录在落地前依次过算子过滤/清洗（仅数据库采集生效）"
            >
              <FilterOperatorPicker />
            </Form.Item>
          )}
        </>
      );
    }
    // S3 / HDFS 类型：路径/glob 模式
    if (ds.type === 's3' || ds.type === 'hdfs') {
      return (
        <>
          <ProFormRadio.Group
            name={['extract', 'mode']}
            label="采集模式"
            tooltip="path：按路径列表或 glob 匹配拉取对象/文件"
            options={[{ label: '路径 / Glob', value: 'path' }]}
            initialValue="path"
          />
          <ProFormSelect
            name={['extract', 'paths']}
            label="路径列表（可选）"
            placeholder={
              ds.type === 's3'
                ? '输入 S3 key 后按 Enter 添加，如 raw/2026/data.jsonl'
                : '输入 HDFS 路径后按 Enter 添加，如 /user/data/train.jsonl'
            }
            tooltip="每条路径按 Enter 确认；与 Glob 可同时填写"
            mode="tags"
            options={[]}
            fieldProps={{ tokenSeparators: [',', '\n'] }}
          />
          <ProFormText
            name={['extract', 'glob']}
            label="Glob 模式（可选）"
            placeholder={
              ds.type === 's3'
                ? '如 raw/2026/**/*.jsonl'
                : '如 /user/data/**/*.csv'
            }
            tooltip="支持 ** 递归匹配；与路径列表可同时填写"
          />
        </>
      );
    }
    return null;
  };

  // 建任务 / 编辑任务共用的质量策略字段（切片 B：maxNullRate + blockOnSchemaDrift）
  // 新建向导 step-4「落地确认」与编辑 ModalForm 都渲染此片段，避免重复
  const qualityPolicyFields = (
    <>
      <ProFormDigit
        name={['qualityPolicy', 'maxNullRate']}
        label="最大空值率阈值（可选）"
        tooltip="单列最大允许 null 率，闭区间 [0, 1]。任一列超出即标 failed。留空 = 不做此项检查"
        min={0}
        max={1}
        step={0.05}
        placeholder="如 0.20（留空 = 不检查）"
        fieldProps={{ precision: 2 }}
      />
      <ProFormSwitch
        name={['qualityPolicy', 'blockOnSchemaDrift']}
        label="Schema 漂移阻断"
        tooltip="开启后，与历史 schema 快照比较出现列增减或类型变化时，新版本标 failed"
      />
    </>
  );

  // 建任务 / 编辑任务共用的表单字段（编辑弹窗仍整体渲染所有字段；新建已迁至 StepsForm）
  const taskFormFields = (
    <>
      <ProFormText
        name="name"
        label="任务名"
        placeholder="请输入任务名称"
        rules={[{ required: true, message: '请输入任务名称' }]}
      />
      <ProFormSelect
        name="datasourceId"
        label="数据源"
        placeholder="请选择数据源"
        rules={[{ required: true, message: '请选择数据源' }]}
        request={async () => {
          const res = await listDataSources({ pageSize: 100 });
          setDsMap(Object.fromEntries(res.data.map((d) => [d.id, d])));
          return res.data.map((d) => ({
            label: `${d.name}（${d.type}${d.dbKind ? `/${d.dbKind}` : ''}）`,
            value: d.id,
          }));
        }}
      />
      <ProFormSelect
        name="lakeId"
        label="目标数据湖"
        placeholder="选择采集结果归档的数据湖"
        tooltip="采集数据入湖为不可变快照(source_v 版本);需要数据集时在湖详情「抽取生成数据集」"
        rules={[{ required: true, message: '请选择目标数据湖' }]}
        showSearch
        fieldProps={{ filterOption: false }}
        request={async ({ keyWords }) => {
          const res = await listDataLakes({
            name: keyWords || undefined,
            pageSize: 50,
          });
          return (res.data ?? []).map((d) => ({
            label: d.name,
            value: d.id,
          }));
        }}
      />
      <ProFormTreeSelect
        name="categoryId"
        label="分类"
        placeholder="请选择分类（可选）"
        fieldProps={{
          treeData: categoryTreeData,
          allowClear: true,
          showSearch: true,
          treeNodeFilterProp: 'title',
          treeDefaultExpandAll: true,
        }}
      />
      <ProFormRadio.Group
        name={['schedule', 'mode']}
        label="调度方式"
        rules={[{ required: true, message: '请选择调度方式' }]}
        options={SCHEDULE_OPTIONS}
      />
      <ProFormDependency name={[['schedule', 'mode']]}>
        {({ schedule }) =>
          schedule?.mode === 'cron' ? (
            <ProFormText
              name={['schedule', 'cron']}
              label="Cron 表达式"
              placeholder="如 0 2 * * *（每天凌晨 2 点，分 时 日 月 周）"
              rules={[
                { required: true, message: '请输入 cron 表达式' },
                {
                  validator: (_, value: string) => {
                    const err = validateCronFields(value);
                    return err
                      ? Promise.reject(new Error(err))
                      : Promise.resolve();
                  },
                },
              ]}
            />
          ) : null
        }
      </ProFormDependency>
      <ProFormDependency name={[['datasourceId']]}>
        {({ datasourceId }) =>
          renderExtractFieldsForDs(
            dsMap[datasourceId],
            datasourceId,
            // 编辑弹窗保留旧版"过滤算子内联在 db 分支"的行为
            true,
          )
        }
      </ProFormDependency>
      {qualityPolicyFields}
      <ProFormDependency name={[['datasourceId']]}>
        {({ datasourceId }) => renderIncrementalFields(dsMap[datasourceId])}
      </ProFormDependency>
    </>
  );

  const columns: ProColumns<DataPlatform.IngestTask>[] = [
    {
      title: '任务名',
      dataIndex: 'name',
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            openDetail(record.id);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '数据源',
      dataIndex: 'datasourceName',
      search: false,
    },
    {
      title: '数据湖',
      dataIndex: 'lakeName',
      search: false,
      render: (_, record) => record.lakeName || '-',
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'treeSelect',
      fieldProps: {
        treeData: categoryTreeData,
        allowClear: true,
        showSearch: true,
        treeNodeFilterProp: 'title',
        treeDefaultExpandAll: true,
      },
      render: (_, record) => record.categoryName || '-',
    },
    {
      title: '调度',
      dataIndex: 'schedule',
      search: false,
      render: (_, record) => renderSchedule(record.schedule),
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueType: 'select',
      valueEnum: {
        pending: { text: '待运行', status: 'Default' },
        running: { text: '运行中', status: 'Processing' },
        success: { text: '成功', status: 'Success' },
        failed: { text: '失败', status: 'Error' },
      },
      render: (_, record) => {
        const meta = STATUS_META[record.status];
        if (record.status === 'running') {
          return (
            <Progress
              percent={record.progress}
              size="small"
              status="active"
              style={{ minWidth: 120 }}
            />
          );
        }
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: '最近运行',
      dataIndex: 'lastRunAt',
      search: false,
      render: (_, record) => formatDateTime(record.lastRunAt),
    },
    {
      title: '运行次数',
      dataIndex: 'runCount',
      search: false,
      render: (_, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            openDetail(record.id);
          }}
        >
          {record.runCount ?? 0} 次
        </a>
      ),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      render: (_, record) => [
        <a key="detail" onClick={() => openDetail(record.id)}>
          详情
        </a>,
        canEdit && (
          <a key="edit" onClick={() => setEditRow(record)}>
            编辑
          </a>
        ),
        record.status === 'running'
          ? canStop && (
              <Popconfirm
                key="stop"
                title="确认停止该任务？"
                onConfirm={() => handleStop(record.id)}
              >
                <a>停止</a>
              </Popconfirm>
            )
          : canRun && (
              <a key="rerun" onClick={() => handleRerun(record.id)}>
                运行
              </a>
            ),
        canRemove && (
          <Popconfirm
            key="delete"
            title="确认删除该任务？"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(record.id)}
          >
            <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
          </Popconfirm>
        ),
      ],
    },
  ];

  return (
    <PageContainer>
      <Dashboard stats={stats} />
      <ProTable<DataPlatform.IngestTask, DataPlatform.IngestTaskListParams>
        headerTitle="采集任务"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 80 }}
        polling={5000}
        request={async (params) => {
          const { current, pageSize, name, status, categoryId } = params;
          const res = await listIngestTasks({
            current,
            pageSize,
            name,
            status,
            categoryId: categoryId || undefined,
          });
          // 对运行中的任务调用单任务接口推进进度，使轮询时进度可见
          const running = res.data.filter((t) => t.status === 'running');
          // 概览统计与列表同源刷新（首次加载、reload、轮询都会带上）
          refreshStats();
          if (running.length > 0) {
            const advanced = await Promise.all(
              running.map((t) => getIngestTask(t.id).catch(() => null)),
            );
            const map = new Map(
              advanced
                .filter(
                  (
                    r,
                  ): r is { data: DataPlatform.IngestTask; success: boolean } =>
                    Boolean(r?.success),
                )
                .map((r) => [r.data.id, r.data]),
            );
            return {
              data: res.data.map((t) => map.get(t.id) ?? t),
              total: res.total,
              success: res.success,
            };
          }
          return {
            data: res.data,
            total: res.total,
            success: res.success,
          };
        }}
        columns={columns}
        toolBarRender={() => [
          <Access key="category" accessible={!!access.canAdmin}>
            <Button onClick={() => setCategoryOpen(true)}>分类管理</Button>
          </Access>,
          canAdd && (
            <Button
              key="create"
              type="primary"
              onClick={() => {
                setWizardCtx({});
                setCreateOpen(true);
              }}
            >
              新建任务
            </Button>
          ),
        ]}
      />

      <Modal
        title="新建采集任务"
        width={800}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <StepsForm<DataPlatform.IngestTaskCreate>
          formProps={{ initialValues: { schedule: { mode: 'once' } } }}
          onFinish={async (values) => {
            try {
              await createIngestTask(pruneEmptyQualityPolicy(values));
              message.success('采集任务创建成功');
              setCreateOpen(false);
              actionRef.current?.reload();
              return true;
            } catch {
              message.error('创建失败，请重试');
              return false;
            }
          }}
        >
          <StepsForm.StepForm
            name="base"
            title="基本信息"
            onFinish={async (values) => {
              // 把 datasourceId 写入步骤间上下文，供 step-2 按 ds 类型渲染 extract 字段
              setWizardCtx((c) => ({
                ...c,
                datasourceId: values.datasourceId,
              }));
              return true;
            }}
          >
            <ProFormText
              name="name"
              label="任务名"
              placeholder="请输入任务名称"
              rules={[{ required: true, message: '请输入任务名称' }]}
            />
            <ProFormSelect
              name="datasourceId"
              label="数据源"
              placeholder="请选择数据源"
              rules={[{ required: true, message: '请选择数据源' }]}
              request={async () => {
                const res = await listDataSources({ pageSize: 100 });
                setDsMap(Object.fromEntries(res.data.map((d) => [d.id, d])));
                return res.data.map((d) => ({
                  label: `${d.name}（${d.type}${d.dbKind ? `/${d.dbKind}` : ''}）`,
                  value: d.id,
                }));
              }}
            />
            <ProFormSelect
              name="lakeId"
              label="目标数据湖"
              placeholder="选择采集结果归档的数据湖"
              tooltip="采集数据入湖为不可变快照(source_v 版本);需要数据集时在湖详情「抽取生成数据集」"
              rules={[{ required: true, message: '请选择目标数据湖' }]}
              showSearch
              fieldProps={{ filterOption: false }}
              request={async ({ keyWords }) => {
                const res = await listDataLakes({
                  name: keyWords || undefined,
                  pageSize: 50,
                });
                return (res.data ?? []).map((d) => ({
                  label: d.name,
                  value: d.id,
                }));
              }}
            />
            <ProFormTreeSelect
              name="categoryId"
              label="分类"
              placeholder="请选择分类（可选）"
              fieldProps={{
                treeData: categoryTreeData,
                allowClear: true,
                showSearch: true,
                treeNodeFilterProp: 'title',
                treeDefaultExpandAll: true,
              }}
            />
          </StepsForm.StepForm>

          <StepsForm.StepForm
            name="object"
            title="采集对象"
            onFinish={async (values) => {
              // S3 / HDFS 的 path 模式：路径列表与 Glob 至少填一项。两者皆空时后端会以
              // 「采集对象为空」400 拒绝，这里前置拦截，避免走到提交才报错。
              const ds = dsMap[wizardCtx.datasourceId ?? ''];
              if (ds?.type === 's3' || ds?.type === 'hdfs') {
                const ex = values.extract ?? {};
                const paths = (ex.paths ?? []).filter(
                  (p: string) => p && p.trim(),
                );
                const glob = (ex.glob ?? '').trim();
                if (!paths.length && !glob) {
                  message.error(
                    '请至少填写「路径列表」或「Glob 模式」之一（全量请在 Glob 填 *）',
                  );
                  return false;
                }
              }
              return true;
            }}
          >
            <ProFormRadio.Group
              name={['schedule', 'mode']}
              label="调度方式"
              rules={[{ required: true, message: '请选择调度方式' }]}
              options={SCHEDULE_OPTIONS}
            />
            <ProFormDependency name={[['schedule', 'mode']]}>
              {({ schedule }) =>
                schedule?.mode === 'cron' ? (
                  <ProFormText
                    name={['schedule', 'cron']}
                    label="Cron 表达式"
                    placeholder="如 0 2 * * *（每天凌晨 2 点，分 时 日 月 周）"
                    rules={[
                      { required: true, message: '请输入 cron 表达式' },
                      {
                        validator: (_, value: string) => {
                          const err = validateCronFields(value);
                          return err
                            ? Promise.reject(new Error(err))
                            : Promise.resolve();
                        },
                      },
                    ]}
                  />
                ) : null
              }
            </ProFormDependency>
            {renderExtractFieldsForDs(
              dsMap[wizardCtx.datasourceId ?? ''],
              wizardCtx.datasourceId,
              // 过滤算子后移到 step-4，step-2 仅渲染 schedule + extract 主体
              false,
            )}
          </StepsForm.StepForm>

          <StepsForm.StepForm name="confirm" title="落地确认">
            {dsMap[wizardCtx.datasourceId ?? '']?.type === 'database' && (
              <Form.Item
                name={['extract', 'operators']}
                label="过滤算子（可选）"
                tooltip="采集到的记录在落地前依次过算子过滤/清洗（仅数据库采集生效）"
              >
                <FilterOperatorPicker />
              </Form.Item>
            )}
            {qualityPolicyFields}
            {renderIncrementalFields(dsMap[wizardCtx.datasourceId ?? ''])}
            <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
              请确认以上配置；提交后将创建采集任务并立即进入待运行状态。
            </Typography.Paragraph>
          </StepsForm.StepForm>
        </StepsForm>
      </Modal>

      <ModalForm<DataPlatform.IngestTaskCreate>
        title="编辑采集任务"
        width={520}
        open={!!editRow}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(v) => {
          if (!v) setEditRow(undefined);
        }}
        initialValues={
          editRow
            ? {
                name: editRow.name,
                datasourceId: editRow.datasourceId,
                lakeId: editRow.lakeId ?? undefined,
                schedule: editRow.schedule,
                extract: editRow.extract,
                categoryId: editRow.categoryId ?? undefined,
                qualityPolicy: editRow.qualityPolicy,
                incremental: editRow.incremental,
              }
            : undefined
        }
        onFinish={async (values) => {
          if (!editRow) return false;
          try {
            await updateIngestTask(editRow.id, pruneEmptyQualityPolicy(values));
            message.success('已保存');
            setEditRow(undefined);
            actionRef.current?.reload();
            return true;
          } catch {
            message.error('保存失败，请重试');
            return false;
          }
        }}
      >
        {taskFormFields}
      </ModalForm>

      <Drawer
        width={900}
        open={detailOpen}
        title={currentRow?.name}
        onClose={() => {
          setDetailOpen(false);
          setCurrentRow(undefined);
          setRuns([]);
        }}
      >
        {currentRow && (
          <>
            <ProDescriptions<DataPlatform.IngestTask>
              column={1}
              dataSource={currentRow}
              columns={[
                { title: '任务名', dataIndex: 'name' },
                { title: '数据源', dataIndex: 'datasourceName' },
                {
                  title: '调度',
                  dataIndex: 'schedule',
                  render: (_, record) => renderSchedule(record.schedule),
                },
                {
                  title: '采集对象',
                  dataIndex: 'extract',
                  render: (_, record) => {
                    const ext = record.extract;
                    if (!ext) return '-';
                    if (ext.mode === 'sql') {
                      return <Typography.Text code>{ext.sql}</Typography.Text>;
                    }
                    if (ext.mode === 'table') {
                      return (
                        <span>
                          {(ext.tables ?? []).map((t) => (
                            <Tag key={t}>{t}</Tag>
                          ))}
                        </span>
                      );
                    }
                    // mode === 'path'
                    return (
                      <span>
                        {ext.glob && <Tag color="blue">glob: {ext.glob}</Tag>}
                        {(ext.paths ?? []).map((p) => (
                          <Tag key={p}>{p}</Tag>
                        ))}
                        {!ext.glob && (ext.paths ?? []).length === 0 && '-'}
                      </span>
                    );
                  },
                },
                {
                  title: '增量配置',
                  dataIndex: 'incremental',
                  render: (_, record) => {
                    const inc = record.incremental;
                    if (!inc)
                      return (
                        <Typography.Text type="secondary">全量</Typography.Text>
                      );
                    if ('column' in inc) {
                      return (
                        <span>
                          <Tag color="blue">DB 列</Tag>
                          <Typography.Text code>{inc.column}</Typography.Text>
                          <Typography.Text type="secondary">
                            {' '}
                            ({inc.type})
                          </Typography.Text>
                        </span>
                      );
                    }
                    return (
                      <span>
                        <Tag color="blue">文件</Tag>
                        <Typography.Text code>{inc.by}</Typography.Text>
                      </span>
                    );
                  },
                },
                {
                  title: '当前水位',
                  dataIndex: 'watermark',
                  render: (_, record) => {
                    const wm = record.watermark;
                    if (!wm?.value) {
                      return (
                        <Typography.Text type="secondary">-</Typography.Text>
                      );
                    }
                    return (
                      <span>
                        <Typography.Text code>{wm.value}</Typography.Text>
                        {wm.updatedAt && (
                          <Typography.Text type="secondary">
                            {' '}
                            ({formatDateTime(wm.updatedAt)})
                          </Typography.Text>
                        )}
                      </span>
                    );
                  },
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  render: (_, record) => {
                    const meta = STATUS_META[record.status];
                    return <Tag color={meta.color}>{meta.text}</Tag>;
                  },
                },
                {
                  title: '进度',
                  dataIndex: 'progress',
                  render: (_, record) => (
                    <Progress percent={record.progress} size="small" />
                  ),
                },
                {
                  title: '创建时间',
                  dataIndex: 'createdAt',
                  render: (_, record) => formatDateTime(record.createdAt),
                },
                {
                  title: '最近运行',
                  dataIndex: 'lastRunAt',
                  render: (_, record) => formatDateTime(record.lastRunAt),
                },
                {
                  title: '产物数据集',
                  dataIndex: 'output',
                  render: (_, record) =>
                    record.output && record.output.length > 0 ? (
                      <Timeline
                        items={record.output.map((o) => {
                          // 未携带 qualityVerdict 视为 skipped（兼容老数据/未配策略）
                          const verdict: DataPlatform.QualityVerdict =
                            o.qualityVerdict ?? 'skipped';
                          const meta = VERDICT_META[verdict];
                          const stats = o.qualityStats;
                          return {
                            key: o.versionId,
                            color: meta.dot,
                            children: (
                              <div>
                                <div>
                                  <Tag color={meta.color}>{meta.text}</Tag>
                                  <span>
                                    {o.datasetName}（{o.rows ?? '-'} 行 ·{' '}
                                    {o.datasetId}{' '}
                                    {o.versionLabel ?? `v${o.versionNo}`}）
                                  </span>
                                </div>
                                {/* 质量统计存在时给出可展开的列空值率明细；
                                    failed 默认展开，其余收起。 */}
                                {stats && stats.columns?.length > 0 && (
                                  <Collapse
                                    size="small"
                                    style={{ marginTop: 4 }}
                                    defaultActiveKey={
                                      verdict === 'failed'
                                        ? [`stats-${o.versionId}`]
                                        : undefined
                                    }
                                    items={[
                                      {
                                        key: `stats-${o.versionId}`,
                                        label: `列空值率（${stats.columns.length} 列）`,
                                        children: (
                                          <Table<DataPlatform.QualityStatColumn>
                                            size="small"
                                            pagination={false}
                                            rowKey="name"
                                            dataSource={stats.columns}
                                            columns={[
                                              {
                                                title: '列',
                                                dataIndex: 'name',
                                              },
                                              {
                                                title: '类型',
                                                dataIndex: 'type',
                                                width: 90,
                                              },
                                              {
                                                title: '空值率',
                                                dataIndex: 'null_rate',
                                                width: 90,
                                                render: (v) =>
                                                  `${(v * 100).toFixed(1)}%`,
                                              },
                                            ]}
                                          />
                                        ),
                                      },
                                    ]}
                                  />
                                )}
                                {/* 表结构快照(切片 B / B6):版本落地时的列名+类型,
                                    供直观核对结构。与列空值率明细独立展开。 */}
                                {o.schemaSnapshot &&
                                  o.schemaSnapshot.length > 0 && (
                                    <Collapse
                                      size="small"
                                      style={{ marginTop: 4 }}
                                      items={[
                                        {
                                          key: `schema-${o.versionId}`,
                                          label: `表结构（${o.schemaSnapshot.length} 列）`,
                                          children: (
                                            <Table<DataPlatform.SchemaSnapshotEntry>
                                              size="small"
                                              pagination={false}
                                              rowKey="name"
                                              dataSource={o.schemaSnapshot}
                                              columns={[
                                                {
                                                  title: '列',
                                                  dataIndex: 'name',
                                                },
                                                {
                                                  title: '类型',
                                                  dataIndex: 'type',
                                                  width: 90,
                                                },
                                              ]}
                                            />
                                          ),
                                        },
                                      ]}
                                    />
                                  )}
                              </div>
                            ),
                          };
                        })}
                      />
                    ) : (
                      '-'
                    ),
                },
              ]}
            />
            <Typography.Title level={5} style={{ marginTop: 16 }}>
              运行记录（共 {currentRow.runCount ?? runs.length} 次）
            </Typography.Title>
            {runs.length > 0 ? (
              <Table<DataPlatform.IngestRun>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={runs}
                columns={[
                  {
                    title: '开始时间',
                    dataIndex: 'startedAt',
                    render: (v) => formatDateTime(v),
                  },
                  {
                    title: '触发',
                    dataIndex: 'trigger',
                    render: (v) =>
                      v === 'cron' ? (
                        <Tag color="blue">cron</Tag>
                      ) : v === 'manual' ? (
                        <Tag>手动</Tag>
                      ) : (
                        <Typography.Text type="secondary">-</Typography.Text>
                      ),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    render: (v) =>
                      v === 'success' ? (
                        <Tag color="success">成功</Tag>
                      ) : (
                        <Tag color="error">失败</Tag>
                      ),
                  },
                  { title: '行数', dataIndex: 'rows' },
                  { title: '数据集数', dataIndex: 'datasetCount' },
                  {
                    title: '产物',
                    dataIndex: 'outputs',
                    render: (_, r) =>
                      r.outputs && r.outputs.length > 0
                        ? r.outputs.map((o) => o.datasetName).join('、')
                        : r.error
                          ? `失败：${r.error}`
                          : '-',
                  },
                  {
                    title: '操作',
                    key: 'action',
                    render: (_, r) =>
                      // 失败 run（含质量门阻断）给重试入口，复用列表「运行」的 rerunIngestTask；
                      // 成功 run 不重试（避免无意中重复落地新版本）。
                      r.status === 'failed' && currentRow && canRun ? (
                        <Popconfirm
                          title="重试该任务？"
                          onConfirm={() => handleRerun(currentRow.id)}
                        >
                          <a>重试</a>
                        </Popconfirm>
                      ) : null,
                  },
                ]}
              />
            ) : (
              <Typography.Text type="secondary">暂无运行记录</Typography.Text>
            )}

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              运行日志
            </Typography.Title>
            {currentRow.logs && currentRow.logs.length > 0 ? (
              <Timeline
                items={currentRow.logs.map((log, idx) => ({
                  key: idx,
                  color: log.includes('[ERROR]')
                    ? 'red'
                    : log.includes('[WARN]')
                      ? 'orange'
                      : 'blue',
                  children: log,
                }))}
              />
            ) : (
              <Typography.Text type="secondary">暂无日志</Typography.Text>
            )}
          </>
        )}
      </Drawer>

      <CategoryManager
        open={categoryOpen}
        canAdmin={!!access.canAdmin}
        onClose={() => setCategoryOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />
    </PageContainer>
  );
};

export default IngestTasksPage;
