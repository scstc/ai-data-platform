// 数据蒸馏新建页:薄壳,把差异点传给共用的 LlmScenarioEditor(蒸馏/合成/增强共用)。
import LlmScenarioEditor from '@/pages/governance/shared/LlmScenarioEditor';
import {
  createDistillationJob,
  getDistillationJob,
  updateDistillationJob,
} from '@/services/data-platform';
import DistillationGoalPanel from './DistillationGoalPanel';

const DEFAULT_GOAL: DataPlatform.DistillationGoal = {
  keepRatio: 0.3,
  scoreField: 'meta.score',
  fallbackRandom: true,
  enableDedup: true,
  enableScoreFilter: true,
};

const DistillationEditor: React.FC = () => (
  <LlmScenarioEditor<DataPlatform.DistillationGoal>
    scenario="distillation"
    bucket="distillation"
    pageTitle="新建数据蒸馏"
    submitLabel="创建蒸馏"
    selectedOperatorsTitle="已选蒸馏算子"
    successMessage="蒸馏任务已创建，正在后台运行"
    jobsHref="/governance/distillation/jobs"
    taskNameNoun="数据蒸馏"
    textKeyTooltip="算子作用的字段;留空则自动探测主文本字段。蒸馏数据通常无 text 字段(如 instruction),建议在此显式指定。"
    binaryDisabledSuffix="二进制不可蒸馏"
    defaultGoal={DEFAULT_GOAL}
    GoalPanel={DistillationGoalPanel}
    createJob={createDistillationJob}
    getJob={getDistillationJob}
    updateJob={updateDistillationJob}
    validateSteps={(steps, opMap) => {
      // 蒸馏必须按某字段取子集:至少 1 个 selector(如 topk_specified_field_selector)
      const hasSelector = steps.some(
        (s) => opMap[s.name]?.category === 'selector',
      );
      return hasSelector
        ? undefined
        : '蒸馏算子链必须包含至少 1 个 selector(如 topk_specified_field_selector)';
    }}
    footerNote='蒸馏对所选数据集版本按"过滤 + 去重 + 打分截断"产出子集,落到原数据集新版本(可改输出数据集); 算子链需至少含 1 个 selector,且必须在算子市场"蒸馏场景"内选择。后台异步执行,完成后可在列表查看报告。'
  />
);

export default DistillationEditor;
