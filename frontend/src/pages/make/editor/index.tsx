// 数据合成新建页:薄壳,把差异点传给共用的 LlmScenarioEditor(蒸馏/合成/增强共用)。
import LlmRequiredAlert from '@/components/LlmRequiredAlert';
import LlmScenarioEditor from '@/pages/governance/shared/LlmScenarioEditor';
import { createMakeJob } from '@/services/data-platform';
import MakeGoalPanel from './MakeGoalPanel';

const DEFAULT_GOAL: DataPlatform.MakeGoal = {
  mode: 'synthesize',
  targetPerSample: 1,
};

const MakeEditor: React.FC = () => (
  <LlmScenarioEditor<DataPlatform.MakeGoal>
    scenario="synthesis"
    bucket="make"
    pageTitle="新建数据合成"
    submitLabel="创建任务"
    selectedOperatorsTitle="已选合成算子"
    successMessage="合成任务已创建，正在后台运行"
    jobsHref="/governance/make/jobs"
    taskNameNoun="数据合成"
    textKeyTooltip="算子作用的字段;留空则自动探测主文本字段。数据无 text 字段(如 GIS address)时在此显式指定。"
    binaryDisabledSuffix="二进制不支持"
    defaultGoal={DEFAULT_GOAL}
    GoalPanel={MakeGoalPanel}
    createJob={createMakeJob}
    normalizeGoal={(goal) => ({ ...goal, mode: 'synthesize' })}
    llmAlert={
      <LlmRequiredAlert description="数据合成(LLM 造新数据)需 LLM 支持。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。" />
    }
    footerNote="数据合成走 LLM 造新数据类算子(generate_qa_from_* 抽取 QA 对、optimize_prompt 上下文扩展等);产物 version 标记 origin=synthetic,可被前端按 origin 区分「原始数据 vs 合成数据」。需 LLM Key(见顶部提示)。"
  />
);

export default MakeEditor;
