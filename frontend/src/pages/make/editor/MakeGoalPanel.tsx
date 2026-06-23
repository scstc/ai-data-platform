// 合成目标面板:每样本生成数 + 总条数上限 + 输出数据集(mode 写死 synthesize 不让用户选)
import { Form, InputNumber, Select, Typography } from 'antd';

interface Props {
  value: DataPlatform.MakeGoal;
  onChange: (v: DataPlatform.MakeGoal) => void;
  datasets: DataPlatform.Dataset[];
  defaultDatasetId?: string;
  outputDatasetId?: string;
  onOutputDatasetChange?: (v: string | undefined) => void;
}

const MakeGoalPanel: React.FC<Props> = ({
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
        合成目标
      </Typography.Text>
      <Form layout="inline" size="small" style={{ marginTop: 8 }}>
        <Form.Item
          label="每样本生成数"
          tooltip="1 条输入产出 N 条结果;QA 类算子可填 >1"
        >
          <InputNumber
            min={1}
            max={10}
            step={1}
            value={value.targetPerSample ?? 1}
            onChange={(v) => onChange({ ...value, targetPerSample: v ?? 1 })}
          />
        </Form.Item>

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

export default MakeGoalPanel;
