// 数据蒸馏 / 数据合成 / 数据增强共用的 LLM 场景编辑器:三者原为独立镜像页面
// (仅目标面板组件、算子桶、创建接口、文案不同),收敛为一份参数化实现。
// 编排粒度=文件,master-detail 对齐 processing/quality 编辑器:左侧常驻文件清单
// (每个文件显示格式/大小/配置状态,点行切换),右侧画布编排当前文件;各文件配置
// 按文件名缓存,切文件不丢,行内「复制」可批量套用;提交走 memberConfigs(成员级)。
import { CopyOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  message,
  Select,
  Space,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import CollapsiblePanes from '@/pages/processing/editor/CollapsiblePanes';
import OperatorLibrary from '@/pages/processing/editor/OperatorLibrary';
import PipelineCanvas from '@/pages/processing/editor/PipelineCanvas';
import PipelineDndArea from '@/pages/processing/editor/PipelineDndArea';
import StepParamsForm from '@/pages/processing/editor/StepParamsForm';
import YamlPreviewCard from '@/pages/processing/editor/YamlPreviewCard';
import { stepsToYaml, yamlToSteps } from '@/pages/processing/editor/yaml';
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

type MemberConfig = {
  operators: DataPlatform.OperatorSpec[];
};

/** 字节数人类可读,与 processing/editor 的 formatSize 保持一致 */
const formatSize = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

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
  /** 成员级独立配置(优先);与 operators 二选一 */
  memberConfigs?: DataPlatform.MemberOperatorConfig[];
  /** 旧版统一配置(向后兼容,编辑旧任务回填用) */
  operators?: DataPlatform.OperatorSpec[];
  goal: TGoal;
  outputDatasetId?: string;
  textKeys?: string[];
};

export type LlmScenarioEditorProps<TGoal extends object> = {
  /** 流水线场景(对齐 Job.type / pipelines.scenario);
   *  不传 = 场景已退出治理工场(如增强),不展示「保存为流水线」 */
  scenario?: 'distillation' | 'synthesis' | 'augmentation';
  /** OperatorLibrary 业务桶(锁定算子库子集);历史命名与 scenario 不一致 */
  bucket: string;
  /** 为真时算子库只展示该 bucket 的算子(数据合成:只列生成类算子);
   *  默认 false 沿用各场景「展示全量算子」的既有行为。 */
  restrictToBucket?: boolean;
  pageTitle: string;
  submitLabel: string;
  /** 中栏「已选算子」卡片标题,如 "已选蒸馏算子" */
  selectedOperatorsTitle: string;
  successMessage: string;
  /** 创建成功后跳转的任务列表页 */
  jobsHref: string;
  /** suggestTaskName 第二参数,如 "数据蒸馏" */
  taskNameNoun: TaskType;
  /** 「文本字段」多选的提示;不传 = 隐藏该选择器,后端自动探测主文本字段 */
  textKeyTooltip?: string;
  /** 版本为二进制格式时禁用项的提示后缀,如 "二进制不可蒸馏" */
  binaryDisabledSuffix: string;
  defaultGoal: TGoal;
  /** 不传 = 该场景没有任务级目标参数(如蒸馏:行为完全由算子链自身参数决定) */
  GoalPanel?: React.ComponentType<GoalPanelProps<TGoal>>;
  createJob: (body: LlmJobBody<TGoal>) => Promise<{ data: DataPlatform.Job }>;
  /** 编辑模式(URL ?jobId=)所需的任务详情/更新接口;不传则该场景不支持编辑任务 */
  getJob?: (id: string) => Promise<{ data: DataPlatform.Job }>;
  updateJob?: (id: string, body: LlmJobBody<TGoal>) => Promise<unknown>;
  /** 提交前对 goal 做归一化(合成/增强强制写死 mode,蒸馏不需要) */
  normalizeGoal?: (goal: TGoal) => TGoal;
  /** 额外前置校验(蒸馏:算子链需含至少 1 个 selector);按已配置成员逐个执行,
   *  返回提示文案则中断提交 */
  validateSteps?: (
    steps: DataPlatform.PipelineStep[],
    opMap: Record<string, DataPlatform.CatalogOperator>,
  ) => string | undefined;
  /** LLM 前置提示(合成/增强需要;蒸馏不需要 LLM 故不传) */
  llmAlert?: React.ReactNode;
  footerNote: React.ReactNode;
};

/** 数据蒸馏/合成/增强通用编辑器:复用 processing/editor 三件套 + 跨容器拖拽,
 *  成员级编排(左侧文件清单),支持保存为流水线、按 URL ?pipelineId= 预载。 */
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
  getJob,
  updateJob,
  normalizeGoal,
  validateSteps,
  llmAlert,
  footerNote,
  restrictToBucket,
}: LlmScenarioEditorProps<TGoal>) {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [versionMembers, setVersionMembers] = useState<
    DataPlatform.DatasetTable[]
  >([]);
  // 选中版本的列名(供「文本字段」多选);留空=后端自动探测主文本字段
  const [columns, setColumns] = useState<string[]>([]);
  const [textKeys, setTextKeys] = useState<string[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  // 成员级配置:key = tableName
  const [memberConfigs, setMemberConfigs] = useState<
    Record<string, MemberConfig>
  >({});
  const [activeMember, setActiveMember] = useState<string>();
  // 每个成员独立维护当前选中的流水线步骤下标(供右侧参数表单定位)
  const [memberActiveIdx, setMemberActiveIdx] = useState<
    Record<string, number>
  >({});
  // 每个成员独立维护画布是否存在游离(未接入主链)算子节点,提交/保存前据此阻断
  const [memberHasOrphan, setMemberHasOrphan] = useState<
    Record<string, boolean>
  >({});
  // 左「算子库」/右「参数」折叠状态:放编辑器顶层,跨成员共享
  const [libCollapsed, setLibCollapsed] = useState(false);
  const [paramsCollapsed, setParamsCollapsed] = useState(false);
  const [goal, setGoal] = useState<TGoal>(defaultGoal);
  const [outputDatasetId, setOutputDatasetId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const { token } = theme.useToken();

  // 算子元信息(供 label/params 渲染):pageSize ≤ 后端 le=500 上限。
  // includeHidden:编辑既有任务时步骤可能引用已隐藏算子,缺元信息参数面板渲染不出;
  // 可选列表由 OperatorLibrary 单独拉取(仍只出可见算子),此处不影响新编排。
  useEffect(() => {
    listOperatorCatalog({
      current: 1,
      pageSize: 500,
      includeHidden: true,
    }).then((r) => {
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

  // 版本变化:按版本的表成员初始化各自配置(getDataset 返回的版本已带 tables)
  useEffect(() => {
    const members = versions.find((v) => v.id === versionId)?.tables ?? [];
    setVersionMembers(members);
    const configs: Record<string, MemberConfig> = {};
    for (const m of members) {
      configs[m.tableName] = { operators: [] };
    }
    setMemberConfigs(configs);
    setActiveMember(members[0]?.tableName);
    setMemberActiveIdx({});
    setMemberHasOrphan({});
  }, [versionId, versions]);

  // 版本变化:拉一条预览取列名,供「文本字段」多选;切版本时清空已选(列可能不同)
  useEffect(() => {
    setTextKeys([]);
    if (!versionId || !textKeyTooltip) {
      setColumns([]);
      return;
    }
    previewDatasetVersion(versionId, { limit: 1 })
      .then((r) => setColumns(r.columns ?? []))
      .catch(() => setColumns([]));
  }, [versionId, textKeyTooltip]);

  // 从数据集版本表「流程」入口跳入时,按 URL 预选数据集 + 版本
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  // 无 URL 预选且非编辑模式时默认选中最新数据集(列表按创建时间倒序),
  // 进页即可直接编排提交
  useEffect(() => {
    if (datasetId || !datasets.length) return;
    const q = new URLSearchParams(location.search);
    if (q.get('datasetId') || q.get('jobId')) return;
    setDatasetId(datasets[0].id);
  }, [datasets, datasetId]);
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    const vId = q.get('versionId');
    if (vId && versions.some((v) => v.id === vId)) {
      setVersionId(vId);
      return;
    }
    if (!versions.length || q.get('jobId')) return;
    // 默认选最新的可用版本(版本列表升序,从尾部找非二进制);
    // 切换数据集后旧 versionId 不在新列表里,也走这里重选
    setVersionId((cur) =>
      cur && versions.some((v) => v.id === cur)
        ? cur
        : [...versions].reverse().find((v) => !isBinaryFormat(v.format))?.id,
    );
  }, [versions]);

  // 流水线预载:URL 带 pipelineId 时,goal 立即预填;spec.operators 待版本成员
  // 就绪后套用到每个成员(仅一次;加载失败不阻塞编辑)
  const pipelineOpsRef = useRef<DataPlatform.OperatorSpec[] | undefined>(
    undefined,
  );
  const pipelineAppliedRef = useRef(false);
  useEffect(() => {
    const pipelineId = new URLSearchParams(location.search).get('pipelineId');
    if (!pipelineId) return;
    getPipeline(pipelineId)
      .then((r) => {
        const spec = r.data.spec;
        pipelineOpsRef.current = (spec.operators ?? []).map((o) => ({
          name: o.name,
          params: o.params ?? {},
        }));
        if (spec.goal) {
          setGoal(spec.goal as TGoal);
        }
      })
      .catch(() => message.error('加载流水线失败'));
  }, []);
  useEffect(() => {
    const operators = pipelineOpsRef.current;
    if (!operators || pipelineAppliedRef.current || !versionMembers.length) {
      return;
    }
    pipelineAppliedRef.current = true;
    setMemberConfigs((prev) => {
      const next = { ...prev };
      for (const m of versionMembers) {
        next[m.tableName] = {
          operators: operators.map((op) => ({
            name: op.name,
            params: { ...(op.params ?? {}) },
          })),
        };
      }
      return next;
    });
  }, [versionMembers]);

  // 编辑模式:URL 带 jobId 时按任务的 editSpec 回填(名称/数据集/版本/成员算子链/goal),
  // 提交改走 updateJob(覆盖原任务配置并原地重跑,不新建记录)
  const editJobId = new URLSearchParams(location.search).get('jobId');
  const editing = Boolean(editJobId && getJob && updateJob);
  const [editSpec, setEditSpec] = useState<Record<string, any>>();
  useEffect(() => {
    if (!editJobId || !getJob) return;
    getJob(editJobId)
      .then((r) => {
        const job = r.data;
        if (!job.editSpec) {
          message.error('该任务无可编辑的配置(早于重跑特性创建)');
          return;
        }
        setEditSpec(job.editSpec);
        setName(job.name);
        setNameDirty(true);
        if (job.editSpec.goal) setGoal(job.editSpec.goal as TGoal);
        setOutputDatasetId(job.editSpec.outputDatasetId ?? undefined);
        const dsId = job.input?.datasetId;
        if (dsId) setDatasetId(dsId);
        else message.error('原输入数据集已不存在,无法回填,请重新选择');
      })
      .catch(() => message.error('加载任务失败'));
  }, []);
  useEffect(() => {
    const vId = editSpec?.datasetVersionId;
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions, editSpec]);
  // 任务配置回填:版本成员就绪后套用 editSpec 的成员算子链(仅一次,晚于成员初始化);
  // 旧任务只有统一 operators → 同一条算子链套用到所有成员
  const editSpecAppliedRef = useRef(false);
  useEffect(() => {
    if (
      !editSpec ||
      editSpecAppliedRef.current ||
      versionMembers.length === 0 ||
      versionId !== editSpec.datasetVersionId
    ) {
      return;
    }
    editSpecAppliedRef.current = true;
    setMemberConfigs((prev) => {
      const next = { ...prev };
      if (editSpec.memberConfigs?.length) {
        for (const cfg of editSpec.memberConfigs) {
          if (next[cfg.memberName]) {
            next[cfg.memberName] = { operators: cfg.operators ?? [] };
          }
        }
      } else if (editSpec.operators?.length) {
        for (const m of versionMembers) {
          next[m.tableName] = { operators: editSpec.operators };
        }
      }
      return next;
    });
  }, [versionMembers, versionId, editSpec]);
  // textKeys 回填:版本切换 effect 会先清空,此 effect 声明在其后,回填目标版本的已选字段
  useEffect(() => {
    if (editSpec?.textKeys?.length && versionId === editSpec.datasetVersionId) {
      setTextKeys(editSpec.textKeys);
    }
  }, [versionId, editSpec]);

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  const categoryOf = (n: string) => opMap[n]?.category || '';

  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const selectedVersionLabel = versions.find(
    (v) => v.id === versionId,
  )?.versionLabel;
  const suggestedName = suggestTaskName(selectedDatasetName, taskNameNoun);
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  // 每个成员各自独立生成 YAML,不拼在一起
  const memberYamlOf = (memberName: string): string => {
    const cfg = memberConfigs[memberName];
    if (!cfg?.operators.length) return '# (未配置算子)';
    const normalizedSteps: DataPlatform.PipelineStep[] = cfg.operators.map(
      (op) => ({ name: op.name, params: op.params ?? {} }),
    );
    return stepsToYaml(normalizedSteps, {
      datasetName: selectedDatasetName,
      versionLabel: selectedVersionLabel,
      fileName: memberName,
      fileFormat: versionMembers.find((m) => m.tableName === memberName)
        ?.format,
      textKeys: textKeys.length ? textKeys : undefined,
    });
  };

  const setMemberOperators = (
    memberName: string,
    next: DataPlatform.OperatorSpec[],
  ) =>
    setMemberConfigs((prev) => ({
      ...prev,
      [memberName]: { operators: next },
    }));

  // 复制配置:把 copySource 文件的算子链整体覆盖到勾选的目标文件
  const [copySource, setCopySource] = useState<string>();
  const [copyTargets, setCopyTargets] = useState<string[]>([]);

  const applyCopy = () => {
    if (!copySource) return;
    const src = memberConfigs[copySource]?.operators ?? [];
    setMemberConfigs((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) {
        next[t] = {
          operators: src.map((op) => ({
            name: op.name,
            params: { ...(op.params ?? {}) },
          })),
        };
      }
      return next;
    });
    // 覆盖后目标文件是一条干净的线性链,重置游离标记与选中步骤
    setMemberHasOrphan((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) next[t] = false;
      return next;
    });
    setMemberActiveIdx((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) next[t] = 0;
      return next;
    });
    message.success(`已复制到 ${copyTargets.length} 个文件`);
    setCopySource(undefined);
  };

  // 保存为流水线:取当前激活成员的算子链 + goal 作为 spec,与 scenario 绑定
  const [pipelineModalOpen, setPipelineModalOpen] = useState(false);
  const [pipelineForm] = Form.useForm<{ name: string; description?: string }>();
  const [savingPipeline, setSavingPipeline] = useState(false);

  const openSavePipeline = () => {
    const cfg = activeMember ? memberConfigs[activeMember] : undefined;
    if (!cfg?.operators.length) {
      message.warning('请先进入某个文件的编排并配置算子');
      return;
    }
    if (activeMember && memberHasOrphan[activeMember]) {
      message.warning('存在未接入流水线的算子');
      return;
    }
    setPipelineModalOpen(true);
  };

  const onSavePipeline = async (values: {
    name: string;
    description?: string;
  }) => {
    if (!activeMember || !scenario) return;
    const cfg = memberConfigs[activeMember];
    setSavingPipeline(true);
    try {
      await createPipeline({
        name: values.name,
        description: values.description,
        scenario,
        spec: { operators: cfg.operators, goal },
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
    const configs = Object.entries(memberConfigs)
      .filter(([, cfg]) => cfg.operators.length > 0)
      .map(([memberName, cfg]) => ({
        memberName,
        operators: cfg.operators,
      }));
    if (configs.length === 0) {
      message.warning('请至少为一个文件配置算子');
      return;
    }
    if (configs.some((c) => memberHasOrphan[c.memberName])) {
      message.warning('存在未接入流水线的算子');
      return;
    }
    if (validateSteps) {
      for (const c of configs) {
        const steps: DataPlatform.PipelineStep[] = c.operators.map((op) => ({
          name: op.name,
          params: op.params ?? {},
        }));
        const validationError = validateSteps(steps, opMap);
        if (validationError) {
          message.warning(`文件 ${c.memberName}:${validationError}`);
          return;
        }
      }
    }
    const body: LlmJobBody<TGoal> = {
      name,
      datasetVersionId: versionId,
      memberConfigs: configs,
      goal: normalizeGoal ? normalizeGoal(goal) : goal,
      outputDatasetId,
      textKeys: textKeys.length ? textKeys : undefined,
    };
    if (editing && editJobId && updateJob) {
      Modal.confirm({
        title: '确认保存并重新运行',
        content: '保存会覆盖原任务配置并原地重跑,不新建任务记录。',
        onOk: async () => {
          setSubmitting(true);
          try {
            await updateJob(editJobId, body);
            message.success('任务已更新，正在重新运行');
            history.push(jobsHref);
          } finally {
            setSubmitting(false);
          }
        },
      });
      return;
    }
    setSubmitting(true);
    try {
      await createJob(body);
      message.success(successMessage);
      history.push(jobsHref);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{
        title: editing ? pageTitle.replace('新建', '编辑') : pageTitle,
      }}
      extra={[
        ...(scenario
          ? [
              <Button key="save-pipeline" onClick={openSavePipeline}>
                保存为流水线
              </Button>,
            ]
          : []),
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          onClick={onSubmit}
        >
          {editing ? '保存并重新运行' : submitLabel}
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
        {textKeyTooltip && (
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
        )}
      </Space>

      {GoalPanel && (
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
      )}

      {versionMembers.length === 0 || !activeMember ? (
        <Card size="small">
          <Empty description="请先选择数据集和版本" />
        </Card>
      ) : (
        <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
          <Card
            size="small"
            style={{ width: 240, flexShrink: 0 }}
            styles={{ body: { padding: 8 } }}
            title={
              <Space>
                <span>文件</span>
                <Text
                  type="secondary"
                  style={{ fontWeight: 'normal', fontSize: 12 }}
                >
                  已配置{' '}
                  {
                    versionMembers.filter(
                      (m) => memberConfigs[m.tableName]?.operators.length,
                    ).length
                  }
                  /{versionMembers.length}
                </Text>
              </Space>
            }
          >
            {versionMembers.map((m) => {
              const count = memberConfigs[m.tableName]?.operators.length || 0;
              const active = m.tableName === activeMember;
              return (
                <div
                  key={m.tableName}
                  onClick={() => setActiveMember(m.tableName)}
                  style={{
                    padding: '6px 8px',
                    borderRadius: token.borderRadius,
                    cursor: 'pointer',
                    background: active ? token.colorPrimaryBg : undefined,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 4,
                    }}
                  >
                    <Space size={6} style={{ minWidth: 0 }}>
                      <Badge status={count ? 'processing' : 'default'} />
                      <Text
                        strong={active}
                        ellipsis={{ tooltip: m.tableName }}
                        style={{ maxWidth: 140 }}
                      >
                        {m.tableName}
                      </Text>
                    </Space>
                    {count > 0 && versionMembers.length > 1 && (
                      <Button
                        size="small"
                        type="text"
                        icon={<CopyOutlined />}
                        title="复制此配置到其他文件"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCopySource(m.tableName);
                          setCopyTargets([]);
                        }}
                      />
                    )}
                  </div>
                  <Text
                    type="secondary"
                    style={{ fontSize: 12, paddingLeft: 14 }}
                  >
                    {m.format} · {formatSize(m.size)} ·{' '}
                    {count ? `${count} 算子` : '不处理'}
                  </Text>
                </div>
              );
            })}
          </Card>
          <Card
            title={`${selectedOperatorsTitle} · ${activeMember}`}
            size="small"
            style={{ flex: 1, minWidth: 0 }}
          >
            {(() => {
              const memberName = activeMember;
              const cfg = memberConfigs[memberName] ?? { operators: [] };
              const memberSteps: DataPlatform.PipelineStep[] =
                cfg.operators.map((op) => ({
                  name: op.name,
                  params: op.params ?? {},
                }));
              const idx = memberActiveIdx[memberName] ?? 0;
              const activeStepOfMember = memberSteps[idx];
              const activeOpOfMember = activeStepOfMember
                ? opMap[activeStepOfMember.name]
                : undefined;
              const appendOperator = (opName: string) =>
                setMemberOperators(memberName, [
                  ...cfg.operators,
                  { name: opName, params: {} },
                ]);

              return (
                <Space
                  orientation="vertical"
                  style={{ width: '100%' }}
                  size={16}
                >
                  <PipelineDndArea
                    steps={memberSteps}
                    labelOf={labelOf}
                    onAppend={appendOperator}
                    onReorder={(from, to) => {
                      const next = [...cfg.operators];
                      const [moved] = next.splice(from, 1);
                      next.splice(to, 0, moved);
                      setMemberOperators(memberName, next);
                    }}
                  >
                    <CollapsiblePanes
                      leftTitle="算子库"
                      left={
                        <OperatorLibrary
                          onAdd={appendOperator}
                          bucket={bucket}
                          restrictToBucket={restrictToBucket}
                        />
                      }
                      centerTitle={selectedOperatorsTitle}
                      center={
                        <PipelineCanvas
                          steps={memberSteps}
                          labelOf={labelOf}
                          categoryOf={categoryOf}
                          activeIdx={idx}
                          onSelect={(i) =>
                            setMemberActiveIdx((prev) => ({
                              ...prev,
                              [memberName]: i,
                            }))
                          }
                          onRemove={(i) => {
                            setMemberOperators(
                              memberName,
                              cfg.operators.filter((_, j) => j !== i),
                            );
                            setMemberActiveIdx((prev) => ({
                              ...prev,
                              [memberName]: 0,
                            }));
                          }}
                          onOrderChange={(perm) => {
                            if (perm.length !== cfg.operators.length) {
                              setMemberHasOrphan((prev) => ({
                                ...prev,
                                [memberName]: true,
                              }));
                              return;
                            }
                            setMemberHasOrphan((prev) => ({
                              ...prev,
                              [memberName]: false,
                            }));
                            if (perm.every((v, i) => v === i)) return;
                            setMemberOperators(
                              memberName,
                              perm.map((i) => cfg.operators[i]),
                            );
                          }}
                          inputLabel={`${selectedVersionLabel ?? '版本'} · ${memberName}`}
                          outputLabel="新版本"
                        />
                      }
                      rightTitle="参数"
                      right={
                        <StepParamsForm
                          op={activeOpOfMember}
                          params={activeStepOfMember?.params ?? {}}
                          onChange={(p) =>
                            setMemberOperators(
                              memberName,
                              cfg.operators.map((op, i) =>
                                i === idx ? { ...op, params: p } : op,
                              ),
                            )
                          }
                        />
                      }
                      leftCollapsed={libCollapsed}
                      rightCollapsed={paramsCollapsed}
                      onLeftCollapsedChange={setLibCollapsed}
                      onRightCollapsedChange={setParamsCollapsed}
                    />
                  </PipelineDndArea>

                  <YamlPreviewCard
                    computedYaml={memberYamlOf(memberName)}
                    resetKey={`${versionId}:${memberName}`}
                    supportsTextKeys={Boolean(textKeyTooltip)}
                    onApply={(text) => {
                      const parsed = yamlToSteps(text, opMap);
                      setMemberOperators(memberName, parsed.steps);
                      setMemberActiveIdx((prev) => ({
                        ...prev,
                        [memberName]: 0,
                      }));
                      setMemberHasOrphan((prev) => ({
                        ...prev,
                        [memberName]: false,
                      }));
                      // 该场景未展示「文本字段」选择器时(如蒸馏),YAML 里的 text_keys
                      // 也不生效,与去掉手动选择器的口径保持一致,不留后门。
                      if (parsed.textKeys && textKeyTooltip)
                        setTextKeys(parsed.textKeys);
                    }}
                  />
                </Space>
              );
            })()}
          </Card>
        </div>
      )}

      <Card size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          每个文件独立生成 YAML、独立执行;写回原数据集时未配置文件原样结转,
          输出到其他数据集时新版本仅含已处理文件的产物。左侧文件列表可随时切换
          编排对象(配置不会丢失),行内复制按钮可把当前文件的算子链批量套用到
          其他文件。{footerNote}
        </Text>
      </Card>

      <Modal
        title={`复制「${copySource ?? ''}」的配置到…`}
        open={!!copySource}
        onCancel={() => setCopySource(undefined)}
        onOk={applyCopy}
        okButtonProps={{ disabled: copyTargets.length === 0 }}
        okText="复制"
      >
        <Checkbox.Group
          value={copyTargets}
          onChange={(v) => setCopyTargets(v as string[])}
          style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          options={versionMembers
            .filter((m) => m.tableName !== copySource)
            .map((m) => {
              const count = memberConfigs[m.tableName]?.operators.length || 0;
              return {
                label: count
                  ? `${m.tableName}（已有 ${count} 算子，将被覆盖）`
                  : m.tableName,
                value: m.tableName,
              };
            })}
        />
      </Modal>

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
