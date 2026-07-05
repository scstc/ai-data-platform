// 数据清洗任务列表(clean 类型)——与数据加工同构,复用 Processing 按 jobType 过滤。
import Processing from '@/pages/processing';

export default () => (
  <Processing
    jobType="clean"
    title="数据清洗任务"
    createHref="/governance/cleaning/editor"
  />
);
