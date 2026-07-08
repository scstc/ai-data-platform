import {
  AutoComplete,
  Empty,
  Form,
  Input,
  InputNumber,
  Switch,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';
import { listLlmProviders, listLocalModels } from '@/services/data-platform';
import { PARAM_ZH_DESC } from '../market/_paramZhDict';

const { Text } = Typography;

/** 参数提示语:优先快照全量翻译 descZh;desc 本身是中文(自定义算子)直接用;
 *  再查中文字典(与市场详情页同一份),未命中回退英文原文,不吞信息。 */
const paramTooltip = (p: DataPlatform.CatalogParam) => {
  if (p.descZh) return p.descZh;
  if (p.desc && /[一-鿿]/.test(p.desc)) return p.desc;
  return PARAM_ZH_DESC[p.name] ?? p.desc;
};

// LLM 模型参数(DJ 各算子命名不统一):留空时后端按 LLM 配置页生效模型注入
// (见 engine.build_config,激活项优先,无激活回退最近配置);用户显式填写则尊重。
// 下拉可选各提供商模型,api_or_hf_model 还可选模型仓库已就位的本地模型。
const MODEL_PARAM_NAMES = new Set(['api_model', 'api_or_hf_model']);

/** 本地 HF 模型参数(hf_model / hf_nsfw_model / sam2_hf_model…):
 *  下拉列出模型仓库已就位的模型,仍可自由输入其他 repo id。 */
const isLocalModelParam = (name: string) =>
  name.includes('hf_') &&
  name.includes('model') &&
  !MODEL_PARAM_NAMES.has(name);

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
  const [providers, setProviders] = useState<DataPlatform.LlmProvider[]>();
  useEffect(() => {
    if (!needsModel || providers !== undefined) return;
    listLlmProviders()
      .then((res) => setProviders(res?.data ?? []))
      .catch(() => setProviders([]));
  }, [needsModel, providers]);
  // 与后端 refresh_cache 同序:激活项优先,无激活回退最近更新的已配 Key 提供商
  const configured = (providers ?? []).filter(
    (p) => p.apiKeyMasked !== '未配置',
  );
  const effectiveModel =
    configured.find((p) => p.isActive)?.model ??
    [...configured].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]
      ?.model;

  // 本地模型仓库已就位的 HF 模型(供 hf_* 与 api_or_hf_model 参数下拉;未配置仓库则为空)
  const needsLocalModel = fields.some(
    (p) => isLocalModelParam(p.name) || p.name === 'api_or_hf_model',
  );
  const [localModels, setLocalModels] = useState<string[]>();
  useEffect(() => {
    if (!needsLocalModel || localModels !== undefined) return;
    listLocalModels()
      .then((res) =>
        setLocalModels(
          (res?.data?.models ?? [])
            .filter((m) => m.kind === 'hf' && m.present)
            .map((m) => m.id),
        ),
      )
      .catch(() => setLocalModels([]));
  }, [needsLocalModel, localModels]);

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
          const seen = new Set<string>();
          const options = [
            ...configured.map((prov) => ({
              value: prov.model,
              label: `${prov.model}（${prov.name}${
                prov.model === effectiveModel ? '，默认' : ''
              }）`,
            })),
            ...(p.name === 'api_or_hf_model'
              ? (localModels ?? []).map((id) => ({
                  value: id,
                  label: `${id}（本地）`,
                }))
              : []),
          ].filter((o) => !seen.has(o.value) && seen.add(o.value));
          return (
            <Form.Item
              key={p.name}
              label={p.name}
              tooltip={paramTooltip(p)}
              help={
                effectiveModel
                  ? `留空自动用 LLM 配置页生效模型 ${effectiveModel}`
                  : '留空自动用 LLM 配置页生效模型'
              }
            >
              <AutoComplete
                value={(val as string) ?? ''}
                onChange={(v) => {
                  // 清空 = 删键:留着空串会挡掉后端的生效模型注入
                  const next = { ...params };
                  if (v) next[p.name] = v;
                  else delete next[p.name];
                  onChange(next);
                }}
                placeholder={effectiveModel ?? '未配置 LLM'}
                options={options}
                filterOption={(input, option) =>
                  String(option?.value ?? '')
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
              />
            </Form.Item>
          );
        }
        if (isLocalModelParam(p.name)) {
          const defaultId =
            typeof p.default === 'string'
              ? p.default.replace(/^['"]|['"]$/g, '')
              : '';
          const ready = new Set(localModels ?? []);
          const current = (val as string) || defaultId;
          return (
            <Form.Item
              key={p.name}
              label={p.name}
              tooltip={paramTooltip(p)}
              help={
                current && ready.size
                  ? ready.has(current)
                    ? `${current} 已在模型仓库就位`
                    : `${current} 不在模型仓库,离线环境将无法运行`
                  : defaultId
                    ? `默认 ${defaultId}`
                    : undefined
              }
            >
              <AutoComplete
                value={(val as string) ?? ''}
                onChange={(v) => set(p.name, v)}
                placeholder={defaultId ? `默认 ${defaultId}` : '输入模型 ID'}
                options={(localModels ?? []).map((id) => ({
                  value: id,
                  label: id === defaultId ? `${id}(默认)` : id,
                }))}
                filterOption={(input, option) =>
                  String(option?.value ?? '')
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
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
