// 增强目标面板:目标总条数(可选截断)+ 输出数据集
// mode 写死 augment 不让用户选;每样本生成数固定 1(增强语义 1→1)
import { Form, InputNumber, Select, Typography } from 'antd';

interface Props {
  value: DataPlatform.AugmentGoal;
  onChange: (v: DataPlatform.AugmentGoal) => void;
  datasets: DataPlatform.Dataset[];
  defaultDatasetId?: string;
  outputDatasetId?: string;
  onOutputDatasetChange?: (v: string | undefined) => void;
}

const AugmentGoalPanel: React.FC<Props> = ({
  value,
  onChange,
  datasets,
  defaultDatasetId,
  outputDatasetId,
  onOutputDatasetChange,
}) => {
  return (
    <div>
      <Typography.Text strong style={{ color: '#1677ff', marginRight: 8 }}>
        增强目标
      </Typography.Text>
      <Form layout="inline" size="small" style={{ marginTop: 8 }}>
        <Form.Item label="目标总条数" tooltip="截断上限;留空表示不限">
          <InputNumber
            min={1}
            step={100}
            placeholder="不限"
            value={value.targetTotal}
            onChange={(v) =>
              onChange({ ...value, targetTotal: v ?? undefined })
            }
          />
        </Form.Item>

        <Form.Item label="输出数据集">
          <Select
            allowClear
            placeholder={defaultDatasetId ? '默认沿用输入数据集' : '请选择'}
            value={outputDatasetId ?? defaultDatasetId}
            onChange={(v) => onOutputDatasetChange?.(v)}
            style={{ width: 200 }}
            options={datasets.map((d) => ({ label: d.name, value: d.id }))}
          />
        </Form.Item>
      </Form>
    </div>
  );
};

export default AugmentGoalPanel;
