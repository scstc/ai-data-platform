import yaml from 'js-yaml';

/** YAML 预览上下文:用户已选的数据集/版本/文件/清洗字段,随选择即时反映到预览。 */
export interface YamlPreviewContext {
  datasetName?: string;
  versionLabel?: string;
  /** 编排的文件(版本成员);编排粒度=文件,与 dataset 分开渲染避免混淆 */
  fileName?: string;
  /** 清洗字段(对应 DJ 配置项 text_keys);留空=后端自动探测,不渲染 */
  textKeys?: string[];
}

/** 把有序步骤渲染为 data-juicer process 配置预览(与后端 build_config 同构)。
 *  传入 ctx 时附带数据集/版本/清洗字段,使预览在选择后即时更新。 */
export function stepsToYaml(
  steps: DataPlatform.PipelineStep[],
  ctx?: YamlPreviewContext,
): string {
  const process = steps.map((s) => ({
    [s.name]: Object.keys(s.params).length ? s.params : null,
  }));
  const doc: Record<string, unknown> = {};
  if (ctx?.datasetName) {
    doc.dataset = ctx.versionLabel
      ? `${ctx.datasetName} / ${ctx.versionLabel}`
      : ctx.datasetName;
  }
  if (ctx?.fileName) doc.file = ctx.fileName;
  if (ctx?.textKeys?.length) doc.text_keys = ctx.textKeys;
  doc.process = process;
  return yaml.dump(doc, { noRefs: true, sortKeys: false });
}
