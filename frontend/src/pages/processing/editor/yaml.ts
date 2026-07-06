import yaml from 'js-yaml';

/** YAML 预览上下文:用户已选的数据集/版本/文件/清洗字段,随选择即时反映到预览。 */
export interface YamlPreviewContext {
  datasetName?: string;
  versionLabel?: string;
  /** 编排的文件(版本成员);编排粒度=文件,与 dataset 分开渲染避免混淆 */
  fileName?: string;
  /** 文件格式(jsonl/parquet);用于渲染真实相对路径,非此二者按 jsonl 规范化 */
  fileFormat?: string;
  /** 清洗字段(对应 DJ 配置项 text_keys);留空=后端自动探测,不渲染 */
  textKeys?: string[];
}

/** 把有序步骤渲染为 data-juicer process 配置预览,与后端 build_config 同构:
 *  staging 相对路径 dataset_path/export_path(inputs/<file>.<fmt> →
 *  outputs/<file>/<file>.<fmt>)+ process 算子链。数据集/版本上下文非 DJ
 *  配置键,以注释行呈现,不混入配方。传入 ctx 时随选择即时更新。 */
export function stepsToYaml(
  steps: DataPlatform.PipelineStep[],
  ctx?: YamlPreviewContext,
): string {
  const process = steps.map((s) => ({
    [s.name]: Object.keys(s.params).length ? s.params : null,
  }));
  const doc: Record<string, unknown> = {};
  if (ctx?.fileName) {
    const fmt = ctx.fileFormat === 'parquet' ? 'parquet' : 'jsonl';
    doc.dataset_path = `inputs/${ctx.fileName}.${fmt}`;
    doc.export_path = `outputs/${ctx.fileName}/${ctx.fileName}.${fmt}`;
  }
  if (ctx?.textKeys?.length) doc.text_keys = ctx.textKeys;
  doc.process = process;
  const body = yaml.dump(doc, { noRefs: true, sortKeys: false });
  const header = ctx?.datasetName
    ? `# dataset: ${ctx.datasetName}${
        ctx.versionLabel ? ` / ${ctx.versionLabel}` : ''
      }\n`
    : '';
  return header + body;
}
