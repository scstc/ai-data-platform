// 数据增强新建页:薄壳,把差异点传给共用的 LlmScenarioEditor(蒸馏/合成/增强共用)。
import LlmRequiredAlert from '@/components/LlmRequiredAlert';
import LlmScenarioEditor from '@/pages/governance/shared/LlmScenarioEditor';
import {
  createAugmentJob,
  getAugmentJob,
  updateAugmentJob,
} from '@/services/data-platform';
import AugmentGoalPanel from './AugmentGoalPanel';

const DEFAULT_GOAL: DataPlatform.AugmentGoal = {
  mode: 'augment',
};

const AugmentEditor: React.FC = () => (
  <LlmScenarioEditor<DataPlatform.AugmentGoal>
    scenario="augmentation"
    bucket="augment"
    pageTitle="新建数据增强"
    submitLabel="创建任务"
    selectedOperatorsTitle="已选增强算子"
    successMessage="增强任务已创建，正在后台运行"
    jobsHref="/governance/augment/jobs"
    taskNameNoun="数据增强"
    binaryDisabledSuffix="二进制不支持"
    defaultGoal={DEFAULT_GOAL}
    GoalPanel={AugmentGoalPanel}
    createJob={createAugmentJob}
    getJob={getAugmentJob}
    updateJob={updateAugmentJob}
    normalizeGoal={(goal) => ({ ...goal, mode: 'augment' })}
    llmAlert={
      <LlmRequiredAlert description="数据增强(LLM 改写已有数据)需 LLM 支持。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。" />
    }
    footerNote="数据增强走 LLM 改写类算子(optimize_qa/query/response 进化指令、sentence_augmentation 通用改写、calibrate 事实校准、llm_extract 结构化抽取、pair_preference DPO 偏好构造等);产物 version 标记 origin=synthetic。需 LLM Key(见顶部提示)。"
  />
);

export default AugmentEditor;
