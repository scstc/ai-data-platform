import { Empty, Form, Input, InputNumber, Switch, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { listLlmProviders } from '@/services/data-platform';
import { PARAM_ZH_DESC } from '../market/_paramZhDict';

const { Text } = Typography;

/** 参数提示语:优先快照全量翻译 descZh;desc 本身是中文(自定义算子)直接用;
 *  再查中文字典(与市场详情页同一份),未命中回退英文原文,不吞信息。 */
const paramTooltip = (p: DataPlatform.CatalogParam) => {
  if (p.descZh) return p.descZh;
  if (p.desc && /[一-鿿]/.test(p.desc)) return p.desc;
  return PARAM_ZH_DESC[p.name] ?? p.desc;
};

// LLM 模型参数(DJ 各算子命名不统一):执行时后端按 LLM 配置页的激活模型注入
// (见 engine.build_config),表单只读展示注入结果,不让用户改出不一致。
const MODEL_PARAM_NAMES = new Set(['api_model', 'api_or_hf_model']);

/** 右栏:按选中算子的参数定义渲染表单,改动回填到该步骤的 params。 */
const StepParamsForm: React.FC<{
  op?: DataPlatform.CatalogOperator;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>) => void;
}> = ({ op, params, onChange }) => {
  // 过滤 args/kwargs 变长占位项(非可配置参数,后端 sanitize 也会裁掉)
  const fields = (op?.params ?? []).filter(
    (p) => p.name !== 'args' && p.name !== 'kwargs',
  );
  const needsModel = fields.some((p) => MODEL_PARAM_NAMES.has(p.name));
  const [activeModel, setActiveModel] = useState<string>();
  useEffect(() => {
    if (!needsModel || activeModel !== undefined) return;
    listLlmProviders()
      .then((res) =>
        setActiveModel(res?.data?.find((p) => p.isActive)?.model ?? ''),
      )
      .catch(() => undefined);
  }, [needsModel, activeModel]);

  if (!op) {
    return <Empty description="从中间选择一个步骤以配置参数" />;
  }
  if (!fields.length) {
    return <Text type="secondary">该算子无可配参数</Text>;
  }
  const set = (k: string, v: unknown) => onChange({ ...params, [k]: v });
  return (
    <Form layout="vertical">
      {fields.map((p) => {
        const val = params[p.name];
        const t = p.type || '';
        if (MODEL_PARAM_NAMES.has(p.name)) {
          return (
            <Form.Item
              key={p.name}
              label={p.name}
              tooltip={paramTooltip(p)}
              help="自动使用 LLM 配置页的激活模型"
            >
              <Input
                value={(val as string) || activeModel}
                disabled
                placeholder="未激活 LLM 配置"
              />
            </Form.Item>
          );
        }
        return (
          <Form.Item
            key={p.name}
            label={p.name}
            tooltip={paramTooltip(p)}
            help={p.default ? `默认 ${p.default}` : undefined}
          >
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
