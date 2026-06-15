import yaml from 'js-yaml';

/** 把有序步骤渲染为 data-juicer process 配置预览(与后端 build_config 同构)。 */
export function stepsToYaml(steps: DataPlatform.PipelineStep[]): string {
  const process = steps.map((s) => ({
    [s.name]: Object.keys(s.params).length ? s.params : null,
  }));
  return yaml.dump({ process }, { noRefs: true, sortKeys: false });
}
