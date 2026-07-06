// 训练集生成新建页:薄壳,把差异点传给共用的 LlmScenarioEditor。
// 不传 scenario:训练集生成走独立侧边栏菜单(非治理工场 Tab),不展示「保存为流水线」。
import LlmRequiredAlert from '@/components/LlmRequiredAlert';
import LlmScenarioEditor from '@/pages/governance/shared/LlmScenarioEditor';
import { createTrainsetJob } from '@/services/data-platform';
import TrainsetGoalPanel from './TrainsetGoalPanel';

const DEFAULT_GOAL: DataPlatform.TrainsetGoal = {
  mode: 'synthesize',
};

const TrainsetEditor: React.FC = () => (
  <LlmScenarioEditor<DataPlatform.TrainsetGoal>
    bucket="trainset"
    restrictToBucket
    pageTitle="新建训练集生成"
    submitLabel="创建任务"
    selectedOperatorsTitle="已选生成算子"
    successMessage="训练集生成任务已创建，正在后台运行"
    jobsHref="/governance/trainset"
    taskNameNoun="训练集生成"
    binaryDisabledSuffix="二进制不支持"
    defaultGoal={DEFAULT_GOAL}
    GoalPanel={TrainsetGoalPanel}
    createJob={createTrainsetJob}
    normalizeGoal={(goal) => ({ ...goal, mode: 'synthesize' })}
    llmAlert={
      <LlmRequiredAlert description="训练集生成(LLM 从源数据造 QA/COT/偏好训练样本)需 LLM 支持。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。" />
    }
    footerNote="训练集生成走 LLM 生成类算子(generate_qa_from_text 无结构文本→QA、generate_qa_from_examples 种子示例→新 QA、pair_preference DPO 偏好构造、generate_cot 单步推理链生成等);产物 version 标记 origin=synthetic。需 LLM Key(见顶部提示)。"
  />
);

export default TrainsetEditor;
