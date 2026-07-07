// 数据合成目标面板:产物固定为「输入数据集的新版本」(origin=synthetic),
// 无需额外目标配置——数据集/版本在顶部选择,算子在画布编排。
// (原「输出数据集」选择器与「目标总条数」均已去除:前者对生成场景冗余,
//  后者后端未接任何截断逻辑,属死控件。)
import { Typography } from 'antd';

const TrainsetGoalPanel: React.FC = () => (
  <div>
    <Typography.Text strong style={{ color: '#1677ff', marginRight: 8 }}>
      生成目标
    </Typography.Text>
    <Typography.Text type="secondary">
      产物将作为所选输入数据集的
      <Typography.Text strong>新版本</Typography.Text>
      入库(origin=synthetic)。
    </Typography.Text>
  </div>
);

export default TrainsetGoalPanel;
