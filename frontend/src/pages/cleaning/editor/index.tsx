// 数据清洗任务编辑器(clean 类型)——复用加工编辑器(算子流水线 → 产新版本),建 type=clean 任务。
import Editor from '@/pages/processing/editor';

export default () => (
  <Editor
    jobType="clean"
    title="新建清洗任务"
    noun="清洗"
    redirectHref="/governance/cleaning/jobs"
    bucket="cleansing"
    scenario="clean"
  />
);
