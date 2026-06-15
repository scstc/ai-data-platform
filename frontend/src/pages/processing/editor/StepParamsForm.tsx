import { InputNumber, Switch, Input, Form, Typography, Empty } from 'antd';

const { Text } = Typography;

/** 右栏:按选中算子的参数定义渲染表单,改动回填到该步骤的 params。 */
const StepParamsForm: React.FC<{
  op?: DataPlatform.CatalogOperator;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>) => void;
}> = ({ op, params, onChange }) => {
  if (!op) {
    return <Empty description="从中间选择一个步骤以配置参数" />;
  }
  // 过滤 args/kwargs 变长占位项(非可配置参数,后端 sanitize 也会裁掉)
  const fields = (op.params ?? []).filter(
    (p) => p.name !== 'args' && p.name !== 'kwargs',
  );
  if (!fields.length) {
    return <Text type="secondary">该算子无可配参数</Text>;
  }
  const set = (k: string, v: unknown) => onChange({ ...params, [k]: v });
  return (
    <Form layout="vertical">
      {fields.map((p) => {
        const val = params[p.name];
        const t = p.type || '';
        return (
          <Form.Item key={p.name} label={p.name} tooltip={p.desc} help={p.default ? `默认 ${p.default}` : undefined}>
            {t.includes('bool') ? (
              <Switch checked={Boolean(val)} onChange={(v) => set(p.name, v)} />
            ) : t.includes('int') || t.includes('float') ? (
              <InputNumber
                style={{ width: '100%' }}
                value={val as number}
                onChange={(v) => set(p.name, v)}
              />
            ) : (
              <Input
                value={val as string}
                onChange={(e) => set(p.name, e.target.value)}
              />
            )}
          </Form.Item>
        );
      })}
    </Form>
  );
};

export default StepParamsForm;
