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
import {
  listLlmProviders,
  listLlmSystemModels,
  listLocalModels,
  listProviderModels,
} from '@/services/data-platform';
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
// 下拉与「系统模型设置」同款按供应商分组列各家模型清单,
// api_or_hf_model 还可选模型仓库已就位的本地模型。
const MODEL_PARAM_NAMES = new Set(['api_model', 'api_or_hf_model']);

/** 本地 HF 模型参数(hf_model / hf_nsfw_model / sam2_hf_model…):
 *  下拉列出模型仓库已就位的模型,仍可自由输入其他 repo id。
 *  排除 bool 类型(如 is_hf_model 开关),避免误命中渲染成模型选择器。 */
const isLocalModelParam = (p: DataPlatform.CatalogParam) =>
  p.name.includes('hf_') &&
  p.name.includes('model') &&
  !MODEL_PARAM_NAMES.has(p.name) &&
  !(p.type || '').includes('bool');

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
  const [providerModels, setProviderModels] = useState<
    Record<string, string[]>
  >({});
  const [systemModels, setSystemModels] = useState<
    DataPlatform.LlmSystemModelItem[]
  >([]);
  useEffect(() => {
    if (!needsModel || providers !== undefined) return;
    (async () => {
      try {
        const res = await listLlmProviders();
        const list = res?.data ?? [];
        setProviders(list);
        const confd = list.filter((p) => p.apiKeyMasked !== '未配置');
        const [sysRes, ...modelRes] = await Promise.all([
          listLlmSystemModels().catch(() => null),
          ...confd.map((p) => listProviderModels(p.id).catch(() => null)),
        ]);
        if (sysRes?.success) setSystemModels(sysRes.data);
        const pm: Record<string, string[]> = {};
        confd.forEach((p, i) => {
          const r = modelRes[i];
          const models = r?.success ? r.data.map((m) => m.model) : [];
          // 当前生效模型可能不在清单里,始终可选(与系统模型设置弹窗同处理)
          if (p.model && !models.includes(p.model)) models.unshift(p.model);
          pm[p.id] = models;
        });
        setProviderModels(pm);
      } catch {
        setProviders([]);
      }
    })();
  }, [needsModel, providers]);
  const configured = (providers ?? []).filter(
    (p) => p.apiKeyMasked !== '未配置',
  );
  // 默认模型 = 「系统模型设置」chat 位(系统推理模型);未设置时与后端
  // refresh_cache 同序回退:激活项优先,再回退最近更新的已配 Key 提供商
  const chatItem = systemModels.find(
    (i) => i.capability === 'chat' && i.providerId && i.model,
  );
  const fallbackProv =
    configured.find((p) => p.isActive) ??
    [...configured].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];
  const effectiveProviderId = chatItem?.providerId ?? fallbackProv?.id;
  const effectiveModel = chatItem?.model ?? fallbackProv?.model;

  // 本地模型仓库已就位的 HF 模型(供 hf_* 与 api_or_hf_model 参数下拉;未配置仓库则为空)
  const needsLocalModel = fields.some(
    (p) => isLocalModelParam(p) || p.name === 'api_or_hf_model',
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
          // 与「系统模型设置」同款:按供应商分组,组内列该供应商全部模型。
          // 任务级凭证按生效供应商解析(后端建任务同规则拦截),非生效供应商
          // 的模型置灰不可选;生效供应商排最前,同名模型归属其名下。
          const seen = new Set<string>();
          const ordered = [...configured].sort(
            (a, b) =>
              Number(b.id === effectiveProviderId) -
              Number(a.id === effectiveProviderId),
          );
          const options = [
            ...ordered.map((prov) => {
              const isEffective = prov.id === effectiveProviderId;
              let models = providerModels[prov.id] ?? [];
              if (
                isEffective &&
                effectiveModel &&
                !models.includes(effectiveModel)
              ) {
                models = [effectiveModel, ...models];
              }
              return {
                label: isEffective ? prov.name : `${prov.name}（非生效供应商）`,
                options: models
                  .filter((m) => !seen.has(m) && seen.add(m))
                  .map((m) => ({
                    value: m,
                    disabled: !isEffective,
                    label:
                      isEffective && m === effectiveModel
                        ? `${m}（系统推理模型）`
                        : m,
                  })),
              };
            }),
            ...(p.name === 'api_or_hf_model'
              ? [
                  {
                    label: '本地模型',
                    options: (localModels ?? [])
                      .filter((m) => !seen.has(m) && seen.add(m))
                      .map((id) => ({ value: id, label: id })),
                  },
                ]
              : []),
          ].filter((g) => g.options.length > 0);
          return (
            <Form.Item
              key={p.name}
              label={p.name}
              tooltip={paramTooltip(p)}
              help={
                effectiveModel
                  ? `留空自动用系统推理模型 ${effectiveModel}`
                  : '留空自动用「LLM 配置-系统模型设置」的系统推理模型'
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
                  // 分组结构下仅叶子选项有 value(组对象只有 label+options)
                  String(
                    (option as { value?: string } | undefined)?.value ?? '',
                  )
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
              />
            </Form.Item>
          );
        }
        if (isLocalModelParam(p)) {
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
