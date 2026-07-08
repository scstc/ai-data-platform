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

/** yamlToSteps 的解析结果:text_keys 未出现在 YAML 里则不返回该字段
 *  (与「留空=后端自动探测」的既有语义保持一致,不误写成空数组)。 */
export type ParsedYamlSteps = {
  steps: DataPlatform.PipelineStep[];
  textKeys?: string[];
};

/** stepsToYaml 的逆运算:手动编辑 YAML 后解析回算子链,供编辑器「应用」按钮使用。
 *  只认 process(必需)与 text_keys(可选)两个键,dataset_path/export_path/
 *  头部注释等纯展示字段解析时直接忽略(编辑无效,不回写)。
 *  opMap 用于校验算子名真实存在于算子目录,避免把手滑打错的算子名悄悄提交。
 *  校验失败一律 throw Error(message 可直接展示给用户)。 */
export function yamlToSteps(
  yamlText: string,
  opMap: Record<string, DataPlatform.CatalogOperator>,
): ParsedYamlSteps {
  let doc: unknown;
  try {
    doc = yaml.load(yamlText);
  } catch (e) {
    throw new Error(`YAML 语法错误:${(e as Error).message}`);
  }
  if (doc === null || doc === undefined) {
    return { steps: [] };
  }
  if (typeof doc !== 'object' || Array.isArray(doc)) {
    throw new Error('YAML 顶层必须是一个对象(需包含 process 字段)');
  }
  const record = doc as Record<string, unknown>;

  const process = record.process;
  if (process === undefined) {
    throw new Error('缺少 process 字段');
  }
  if (!Array.isArray(process)) {
    throw new Error('process 必须是数组');
  }
  const steps: DataPlatform.PipelineStep[] = process.map((item, i) => {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error(
        `第 ${i + 1} 个算子格式不对,应为「算子名: 参数」或「算子名: null」`,
      );
    }
    const keys = Object.keys(item as Record<string, unknown>);
    if (keys.length !== 1) {
      throw new Error(
        `第 ${i + 1} 个算子必须只含一个算子名(实际 ${keys.length} 个键)`,
      );
    }
    const [opName] = keys;
    if (!opMap[opName]) {
      throw new Error(`未知算子「${opName}」,请检查名称是否正确`);
    }
    const rawParams = (item as Record<string, unknown>)[opName];
    if (
      rawParams !== null &&
      rawParams !== undefined &&
      (typeof rawParams !== 'object' || Array.isArray(rawParams))
    ) {
      throw new Error(`算子「${opName}」的参数必须是对象或 null`);
    }
    return {
      name: opName,
      params: (rawParams as Record<string, unknown>) ?? {},
    };
  });

  const rawTextKeys = record.text_keys;
  if (rawTextKeys === undefined) {
    return { steps };
  }
  if (
    !Array.isArray(rawTextKeys) ||
    !rawTextKeys.every((x) => typeof x === 'string')
  ) {
    throw new Error('text_keys 必须是字符串数组');
  }
  return { steps, textKeys: rawTextKeys as string[] };
}
