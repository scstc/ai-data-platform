import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDependency,
  ProFormRadio,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { Access, useAccess } from '@umijs/max';
import {
  Button,
  Drawer,
  message,
  Popconfirm,
  Progress,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  createIngestTask,
  deleteIngestTask,
  getIngestTask,
  listCategories,
  listDataSources,
  listDatasourceTables,
  listIngestRuns,
  listIngestTasks,
  rerunIngestTask,
  stopIngestTask,
  updateIngestTask,
} from '@/services/data-platform';

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

/** 格式化调度展示：单次 / cron 表达式 */
const renderSchedule = (schedule: DataPlatform.IngestSchedule) =>
  schedule.mode === 'once' ? (
    <Tag>单次</Tag>
  ) : (
    <Typography.Text code>{schedule.cron}</Typography.Text>
  );

const IngestTasksPage: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [currentRow, setCurrentRow] = useState<DataPlatform.IngestTask>();
  // 数据源 id → 数据源（用于按所选数据源类型条件渲染"采集对象"）
  const [dsMap, setDsMap] = useState<Record<string, DataPlatform.DataSource>>(
    {},
  );
  const [runs, setRuns] = useState<DataPlatform.IngestRun[]>([]);
  const [editRow, setEditRow] = useState<DataPlatform.IngestTask>();
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
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

  // 建任务 / 编辑任务共用的表单字段
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
        name="categoryId"
        label="分类"
        placeholder="请选择分类（可选）"
        options={categoryOptions}
        fieldProps={{ allowClear: true, showSearch: true }}
      />
      <ProFormRadio.Group
        name={['schedule', 'mode']}
        label="调度方式"
        rules={[{ required: true, message: '请选择调度方式' }]}
        options={[
          { label: '单次', value: 'once' },
          { label: 'Cron 周期', value: 'cron' },
        ]}
      />
      <ProFormDependency name={[['schedule', 'mode']]}>
        {({ schedule }) =>
          schedule?.mode === 'cron' ? (
            <ProFormText
              name={['schedule', 'cron']}
              label="Cron 表达式"
              placeholder="如 0 2 * * *（每天凌晨 2 点，分 时 日 月 周）"
              rules={[{ required: true, message: '请输入 cron 表达式' }]}
            />
          ) : null
        }
      </ProFormDependency>
      <ProFormDependency name={[['datasourceId']]}>
        {({ datasourceId }) => {
          const ds = dsMap[datasourceId];
          if (ds?.type !== 'database') return null;
          return (
            <>
              <ProFormRadio.Group
                name={['extract', 'mode']}
                label="采集对象"
                tooltip="拉什么数据。真实拉取当前支持 PostgreSQL 数据源"
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
                      placeholder="选择一张或多张表（每张表各产一个数据集）"
                      rules={[
                        { required: true, message: '请至少选择一张表' },
                      ]}
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
            </>
          );
        }}
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
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'select',
      fieldProps: { options: categoryOptions, allowClear: true },
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
      render: (_, record) =>
        record.lastRunAt
          ? dayjs(record.lastRunAt).format('YYYY-MM-DD HH:mm:ss')
          : '-',
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
        <a key="edit" onClick={() => setEditRow(record)}>
          编辑
        </a>,
        record.status === 'running' ? (
          <Popconfirm
            key="stop"
            title="确认停止该任务？"
            onConfirm={() => handleStop(record.id)}
          >
            <a>停止</a>
          </Popconfirm>
        ) : (
          <a key="rerun" onClick={() => handleRerun(record.id)}>
            运行
          </a>
        ),
        <Popconfirm
          key="delete"
          title="确认删除该任务？"
          okText="删除"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleDelete(record.id)}
        >
          <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <PageContainer>
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
          <ModalForm<DataPlatform.IngestTaskCreate>
            key="create"
            title="新建采集任务"
            trigger={<Button type="primary">新建任务</Button>}
            modalProps={{ destroyOnHidden: true }}
            initialValues={{ schedule: { mode: 'once' } }}
            onFinish={async (values) => {
              try {
                await createIngestTask(values);
                message.success('采集任务创建成功');
                actionRef.current?.reload();
                return true;
              } catch {
                message.error('创建失败，请重试');
                return false;
              }
            }}
          >
            {taskFormFields}
          </ModalForm>,
        ]}
      />

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
                schedule: editRow.schedule,
                extract: editRow.extract,
                categoryId: editRow.categoryId ?? undefined,
              }
            : undefined
        }
        onFinish={async (values) => {
          if (!editRow) return false;
          try {
            await updateIngestTask(editRow.id, values);
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
        width={560}
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
                  render: (_, record) =>
                    record.extract ? (
                      record.extract.mode === 'sql' ? (
                        <Typography.Text code>
                          {record.extract.sql}
                        </Typography.Text>
                      ) : (
                        <span>
                          {(record.extract.tables ?? []).map((t) => (
                            <Tag key={t}>{t}</Tag>
                          ))}
                        </span>
                      )
                    ) : (
                      '-'
                    ),
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
                  valueType: 'dateTime',
                },
                {
                  title: '最近运行',
                  dataIndex: 'lastRunAt',
                  render: (_, record) =>
                    record.lastRunAt
                      ? dayjs(record.lastRunAt).format('YYYY-MM-DD HH:mm:ss')
                      : '-',
                },
                {
                  title: '产物数据集',
                  dataIndex: 'output',
                  render: (_, record) =>
                    record.output && record.output.length > 0 ? (
                      <Timeline
                        items={record.output.map((o) => ({
                          key: o.versionId,
                          color: 'green',
                          children: `${o.datasetName}（${o.rows ?? '-'} 行 · ${o.datasetId} v${o.versionNo}）`,
                        }))}
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
                    render: (v) => dayjs(v).format('YYYY-MM-DD HH:mm:ss'),
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
