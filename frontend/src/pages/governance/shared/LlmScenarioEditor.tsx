// 数据蒸馏 / 数据合成 / 数据增强共用的 LLM 场景编辑器:三者原为独立镜像页面
// (仅目标面板组件、算子桶、创建接口、文案不同),收敛为一份参数化实现。
// 布局对齐 quality/editor:扁平 Space 顶部 + Goal 面板 + 5/13/6 三栏(中栏为画布式编排)。
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  message,
  Select,
  Space,
  Tooltip,
  Typography,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import CollapsiblePanes from '@/pages/processing/editor/CollapsiblePanes';
import OperatorLibrary from '@/pages/processing/editor/OperatorLibrary';
import PipelineCanvas from '@/pages/processing/editor/PipelineCanvas';
import PipelineDndArea from '@/pages/processing/editor/PipelineDndArea';
import StepParamsForm from '@/pages/processing/editor/StepParamsForm';
import {
  createPipeline,
  getDataset,
  getPipeline,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
} from '@/services/data-platform';
import { suggestTaskName, type TaskType } from '@/utils/taskName';

const { Text } = Typography;

/** 目标面板组件的统一 props 形状(蒸馏/合成/增强三份 GoalPanel 原地保留,签名对齐)。 */
type GoalPanelProps<TGoal> = {
  value: TGoal;
  onChange: (v: TGoal) => void;
  datasets: DataPlatform.Dataset[];
  defaultDatasetId?: string;
  outputDatasetId?: string;
  onOutputDatasetChange?: (v: string | undefined) => void;
};

type LlmJobBody<TGoal> = {
  name: string;
  datasetVersionId: string;
  operators: DataPlatform.OperatorSpec[];
  goal: TGoal;
  outputDatasetId?: string;
  textKeys?: string[];
};

export type LlmScenarioEditorProps<TGoal extends object> = {
  /** 流水线场景(对齐 Job.type / pipelines.scenario) */
  scenario: 'distillation' | 'synthesis' | 'augmentation';
  /** OperatorLibrary 业务桶(锁定算子库子集);历史命名与 scenario 不一致 */
  bucket: string;
  pageTitle: string;
  submitLabel: string;
  /** 中栏「已选算子」卡片标题,如 "已选蒸馏算子" */
  selectedOperatorsTitle: string;
  successMessage: string;
  /** 创建成功后跳转的任务列表页 */
  jobsHref: string;
  /** suggestTaskName 第二参数,如 "数据蒸馏" */
  taskNameNoun: TaskType;
  textKeyTooltip: string;
  /** 版本为二进制格式时禁用项的提示后缀,如 "二进制不可蒸馏" */
  binaryDisabledSuffix: string;
  defaultGoal: TGoal;
  GoalPanel: React.ComponentType<GoalPanelProps<TGoal>>;
  createJob: (body: LlmJobBody<TGoal>) => Promise<{ data: DataPlatform.Job }>;
  /** 提交前对 goal 做归一化(合成/增强强制写死 mode,蒸馏不需要) */
  normalizeGoal?: (goal: TGoal) => TGoal;
  /** 额外前置校验(蒸馏:算子链需含至少 1 个 selector);返回提示文案则中断提交 */
  validateSteps?: (
    steps: DataPlatform.PipelineStep[],
    opMap: Record<string, DataPlatform.CatalogOperator>,
  ) => string | undefined;
  /** LLM 前置提示(合成/增强需要;蒸馏不需要 LLM 故不传) */
  llmAlert?: React.ReactNode;
  footerNote: React.ReactNode;
};

/** 数据蒸馏/合成/增强通用编辑器:复用 processing/editor 三件套 + 跨容器拖拽,
 *  支持保存为流水线、按 URL ?pipelineId= 预载。 */
function LlmScenarioEditor<TGoal extends object>({
  scenario,
  bucket,
  pageTitle,
  submitLabel,
  selectedOperatorsTitle,
  successMessage,
  jobsHref,
  taskNameNoun,
  textKeyTooltip,
  binaryDisabledSuffix,
  defaultGoal,
  GoalPanel,
  createJob,
  normalizeGoal,
  validateSteps,
  llmAlert,
  footerNote,
}: LlmScenarioEditorProps<TGoal>) {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  // 选中版本的列名(供「文本字段」多选);留空=后端自动探测主文本字段
  const [columns, setColumns] = useState<string[]>([]);
  const [textKeys, setTextKeys] = useState<string[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  // 画布是否存在游离(未接入主链)算子节点,提交/保存前据此阻断
  const [hasOrphanSteps, setHasOrphanSteps] = useState(false);
  // 左「算子库」/右「参数」折叠状态
  const [libCollapsed, setLibCollapsed] = useState(false);
  const [paramsCollapsed, setParamsCollapsed] = useState(false);
  const [goal, setGoal] = useState<TGoal>(defaultGoal);
  const [outputDatasetId, setOutputDatasetId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  const appendOperator = useCallback(
    (op: string) => setSteps((prev) => [...prev, { name: op, params: {} }]),
    [],
  );
  const remove = useCallback(
    (idx: number) => setSteps((prev) => prev.filter((_, i) => i !== idx)),
    [],
  );
  const updateParams = useCallback(
    (idx: number, params: Record<string, unknown>) =>
      setSteps((prev) =>
        prev.map((s, i) => (i === idx ? { ...s, params } : s)),
      ),
    [],
  );
  const reorder = useCallback(
    (from: number, to: number) =>
      setSteps((prev) => {
        const next = [...prev];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      }),
    [],
  );

  // 算子元信息(供 label/params 渲染):pageSize ≤ 后端 le=500 上限
  useEffect(() => {
    listOperatorCatalog({ current: 1, pageSize: 500 }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 500 }).then((r) =>
      setDatasets(r.data),
    );
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  // 版本变化:拉一条预览取列名,供「文本字段」多选;切版本时清空已选(列可能不同)
  useEffect(() => {
    setTextKeys([]);
    if (!versionId) {
      setColumns([]);
      return;
    }
    previewDatasetVersion(versionId, { limit: 1 })
      .then((r) => setColumns(r.columns ?? []))
      .catch(() => setColumns([]));
  }, [versionId]);

  // 从数据集版本表「流程」入口跳入时,按 URL 预选数据集 + 版本
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  // 流水线预载:URL 带 pipelineId 时,把 spec.operators/goal 预填;加载失败不阻塞编辑
  useEffect(() => {
    const pipelineId = new URLSearchParams(location.search).get('pipelineId');
    if (!pipelineId) return;
    getPipeline(pipelineId)
      .then((r) => {
        const spec = r.data.spec;
        setSteps(
          (spec.operators ?? []).map((o) => ({
            name: o.name,
            params: o.params ?? {},
          })),
        );
        if (spec.goal) {
          setGoal(spec.goal as TGoal);
        }
      })
      .catch(() => message.error('加载流水线失败'));
  }, []);

  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;
  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  const categoryOf = (n: string) => opMap[n]?.category || '';

  const onOrderChange = (perm: number[]) => {
    if (perm.length !== steps.length) {
      setHasOrphanSteps(true);
      return;
    }
    setHasOrphanSteps(false);
    if (perm.every((v, i) => v === i)) return;
    setSteps(perm.map((i) => steps[i]));
  };

  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const selectedVersionLabel = versions.find(
    (v) => v.id === versionId,
  )?.versionLabel;
  const suggestedName = suggestTaskName(selectedDatasetName, taskNameNoun);
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  // 保存为流水线
  const [pipelineModalOpen, setPipelineModalOpen] = useState(false);
  const [pipelineForm] = Form.useForm<{ name: string; description?: string }>();
  const [savingPipeline, setSavingPipeline] = useState(false);

  const openSavePipeline = () => {
    if (!steps.length) {
      message.warning('请先添加算子');
      return;
    }
    if (hasOrphanSteps) {
      message.warning('存在未接入流水线的算子');
      return;
    }
    setPipelineModalOpen(true);
  };

  const onSavePipeline = async (values: {
    name: string;
    description?: string;
  }) => {
    setSavingPipeline(true);
    try {
      await createPipeline({
        name: values.name,
        description: values.description,
        scenario,
        spec: { operators: steps, goal },
      });
      message.success('已保存为流水线');
      setPipelineModalOpen(false);
      pipelineForm.resetFields();
    } finally {
      setSavingPipeline(false);
    }
  };

  const onSubmit = async () => {
    if (!name.trim()) {
      message.warning('请填写任务名');
      return;
    }
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    if (!steps.length) {
      message.warning('至少添加一个算子');
      return;
    }
    if (hasOrphanSteps) {
      message.warning('存在未接入流水线的算子');
      return;
    }
    const validationError = validateSteps?.(steps, opMap);
    if (validationError) {
      message.warning(validationError);
      return;
    }
    setSubmitting(true);
    try {
      await createJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
        goal: normalizeGoal ? normalizeGoal(goal) : goal,
        outputDatasetId,
        textKeys: textKeys.length ? textKeys : undefined,
      });
      message.success(successMessage);
      history.push(jobsHref);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: pageTitle }}
      extra={[
        <Button key="save-pipeline" onClick={openSavePipeline}>
          保存为流水线
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          onClick={onSubmit}
        >
          {submitLabel}
        </Button>,
      ]}
    >
      {llmAlert}

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="任务名(自动生成,可编辑)"
          style={{ width: 280 }}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setNameDirty(true);
          }}
        />
        <Select
          placeholder="选择数据集"
          style={{ width: 220 }}
          value={datasetId}
          onChange={(v) => setDatasetId(v)}
          options={datasets.map((d) => ({ label: d.name, value: d.id }))}
        />
        <Select
          placeholder="选择版本"
          style={{ width: 240 }}
          value={versionId}
          onChange={setVersionId}
          options={versions.map((v) => {
            const isBinary = isBinaryFormat(v.format);
            return {
              label: isBinary
                ? `${v.versionLabel}（${v.format}·${binaryDisabledSuffix}）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
        <Tooltip title={textKeyTooltip}>
          <Select
            mode="multiple"
            allowClear
            placeholder="文本字段(留空=自动)"
            style={{ minWidth: 220, maxWidth: 360 }}
            value={textKeys}
            onChange={setTextKeys}
            disabled={!versionId || columns.length === 0}
            options={columns.map((c) => ({ label: c, value: c }))}
            maxTagCount="responsive"
          />
        </Tooltip>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <GoalPanel
          value={goal}
          onChange={setGoal}
          datasets={datasets}
          defaultDatasetId={datasetId}
          outputDatasetId={outputDatasetId}
          onOutputDatasetChange={setOutputDatasetId}
        />
      </Card>

      <PipelineDndArea
        steps={steps}
        labelOf={labelOf}
        onAppend={appendOperator}
        onReorder={reorder}
      >
        <CollapsiblePanes
          leftTitle="算子库"
          left={<OperatorLibrary onAdd={appendOperator} bucket={bucket} />}
          centerTitle={selectedOperatorsTitle}
          center={
            <PipelineCanvas
              steps={steps}
              labelOf={labelOf}
              categoryOf={categoryOf}
              activeIdx={activeIdx}
              onSelect={setActiveIdx}
              onRemove={(i) => {
                remove(i);
                setActiveIdx(0);
              }}
              onOrderChange={onOrderChange}
              inputLabel={`${selectedDatasetName ?? '数据集'}${selectedVersionLabel ? ` · ${selectedVersionLabel}` : ''}`}
              outputLabel="新版本"
            />
          }
          rightTitle="参数"
          right={
            <StepParamsForm
              op={activeOp}
              params={activeStep?.params ?? {}}
              onChange={(p) => updateParams(activeIdx, p)}
            />
          }
          leftCollapsed={libCollapsed}
          rightCollapsed={paramsCollapsed}
          onLeftCollapsedChange={setLibCollapsed}
          onRightCollapsedChange={setParamsCollapsed}
        />
      </PipelineDndArea>

      <Card size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {footerNote}
        </Text>
      </Card>

      <Modal
        title="保存为流水线"
        open={pipelineModalOpen}
        onCancel={() => setPipelineModalOpen(false)}
        onOk={() => pipelineForm.submit()}
        confirmLoading={savingPipeline}
        destroyOnHidden
      >
        <Form form={pipelineForm} layout="vertical" onFinish={onSavePipeline}>
          <Form.Item
            name="name"
            label="流水线名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder={`如：通用${taskNameNoun}流水线`} />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

export default LlmScenarioEditor;
