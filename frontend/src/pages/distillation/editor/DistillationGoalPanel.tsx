// 蒸馏目标面板:任务级参数(保留方式 / 排序字段 / 兜底策略),不写到算子链。
// 产物固定为输入数据集的新版本(不提供输出数据集选择,与生成/增强一致)。
import { Form, Input, InputNumber, Radio, Switch, Typography } from 'antd';

interface Props {
  value: DataPlatform.DistillationGoal;
  onChange: (v: DataPlatform.DistillationGoal) => void;
}

const DistillationGoalPanel: React.FC<Props> = ({ value, onChange }) => {
  const keepMode: 'ratio' | 'num' = value.keepNum != null ? 'num' : 'ratio';
  const setMode = (m: 'ratio' | 'num') => {
    if (m === 'ratio') onChange({ ...value, keepNum: undefined });
    else onChange({ ...value, keepNum: value.keepNum ?? 1000 });
  };

  return (
    <div>
      <Typography.Text strong style={{ color: '#1677ff', marginRight: 8 }}>
        蒸馏目标
      </Typography.Text>
      <Form layout="inline" size="small" style={{ marginTop: 8 }}>
        <Form.Item label="保留方式">
          <Radio.Group
            value={keepMode}
            onChange={(e) => setMode(e.target.value)}
            optionType="button"
            buttonStyle="solid"
          >
            <Radio.Button value="ratio">按比例</Radio.Button>
            <Radio.Button value="num">按条数</Radio.Button>
          </Radio.Group>
        </Form.Item>

        {keepMode === 'ratio' ? (
          <Form.Item label="保留比例">
            <InputNumber
              min={0}
              max={1}
              step={0.05}
              value={value.keepRatio ?? 0.3}
              onChange={(v) => onChange({ ...value, keepRatio: v ?? 0.3 })}
              formatter={(v) => `${Math.round(((v ?? 0) as number) * 100)}%`}
              parser={(v) =>
                (Number((v ?? '').toString().replace('%', '')) || 0) / 100
              }
            />
          </Form.Item>
        ) : (
          <Form.Item label="保留条数">
            <InputNumber
              min={1}
              step={100}
              value={value.keepNum ?? 1000}
              onChange={(v) => onChange({ ...value, keepNum: v ?? 1000 })}
            />
          </Form.Item>
        )}

        <Form.Item
          label="排序字段"
          tooltip="topk_specified_field_selector 取 top 时使用;必须先有算子写入此字段"
        >
          <Input
            value={value.scoreField ?? 'meta.score'}
            onChange={(e) => onChange({ ...value, scoreField: e.target.value })}
            placeholder="meta.score"
            style={{ width: 180 }}
          />
        </Form.Item>

        <Form.Item label="不足时随机补齐">
          <Switch
            checked={value.fallbackRandom ?? true}
            onChange={(c) => onChange({ ...value, fallbackRandom: c })}
          />
        </Form.Item>

        <Form.Item label="启用 minhash 去重">
          <Switch
            checked={value.enableDedup ?? true}
            onChange={(c) => onChange({ ...value, enableDedup: c })}
          />
        </Form.Item>
      </Form>
    </div>
  );
};

export default DistillationGoalPanel;
