/**
 * 算子参数名 → 中文说明 兜底字典。
 *
 * 详情页参数表 "中文说明" 列的渲染规则:
 *   `PARAM_ZH_DESC[name] ?? desc`
 *
 * 后端 DJ 算子注册信息里 `params[].desc` 已经是英文描述;命中下表时
 * 显示中文(更易读),未命中时回退到 `desc` 保持英文原始信息不被吞掉。
 *
 * 约束:只覆盖高频通用参数(across operators),不逐算子翻译;
 *     后续如要扩成全量翻译,把字典挪到后端或 i18n 文件更合适。
 */
export const PARAM_ZH_DESC: Record<string, string> = {
  // ===== 通用阈值与区间 =====
  threshold: '判定阈值',
  thresh: '判定阈值',
  thr: '判定阈值',
  ratio: '占比阈值',
  ratio_threshold: '占比阈值',
  min: '最小值',
  max: '最大值',
  min_v: '最小值',
  max_v: '最大值',
  lower: '下界',
  upper: '上界',
  lower_threshold: '下界',
  upper_threshold: '上界',
  min_val: '最小取值',
  max_val: '最大取值',

  // ===== 文本长度/字数 =====
  min_length: '最小长度',
  max_length: '最大长度',
  min_words: '最少词数',
  max_words: '最多词数',
  min_char: '最少字符数',
  max_char: '最多字符数',
  min_num: '最少样本数',
  max_num: '最多样本数',
  word_limit: '词数上限',
  char_limit: '字符上限',
  max_token_num: '最大 token 数',
  max_token: '最大 token 数',
  token_num: 'token 数',

  // ===== 重复率/相似度 =====
  max_repeat: '最大重复次数',
  repeat_times: '重复次数',
  max_diff: '最大差异比例',
  similarity_threshold: '相似度阈值',
  similarity_thresh: '相似度阈值',
  min_score: '最低分',
  max_score: '最高分',

  // ===== 字段名 / 数据列 =====
  text_keys: '文本字段名',
  text_key: '文本字段名',
  image_keys: '图像字段名',
  image_key: '图像字段名',
  audio_keys: '音频字段名',
  audio_key: '音频字段名',
  video_keys: '视频字段名',
  video_key: '视频字段名',
  input_key: '输入字段',
  output_key: '输出字段',
  field_key: '字段名',
  key_field: '字段名',
  cot_key: '推理链写入字段名',
  rejected_key: '拒绝回答写入字段名',
  reason_key: '生成理由写入字段名',

  // ===== 输入/输出路径与文件 =====
  input_path: '输入路径',
  output_path: '输出路径',
  dataset_path: '数据集路径',
  file_path: '文件路径',
  output_dir: '输出目录',
  save_dir: '保存目录',
  output_file: '输出文件',
  output_format: '输出格式',

  // ===== 行为开关 =====
  any_or_all: '全部/任一条件',
  keep_order: '保留原始顺序',
  keep_original: '保留原始样本',
  drop_no_head: '丢弃无标题行',
  drop_text: '丢弃文本行',
  inplace: '原地修改',
  in_place: '原地修改',
  overwrite: '覆盖输出',
  recursive: '递归子目录',
  enable_vllm: '启用 vLLM',

  // ===== 模型/API 配置 =====
  api_model: '模型名称',
  api_endpoint: 'API 服务地址',
  api_or_hf_model: 'API 或 HF 模型',
  model_path: '模型路径',
  model_name: '模型名称',
  model_id: '模型 ID',
  endpoint_url: '请求端点 URL',
  hf_model: 'HF 模型',
  device: '运行设备',

  // ===== LLM prompt/样例 =====
  seed_file: '种子示例文件路径(chatml 格式)',
  example_num: '选取示例数量',
  qa_pair_template: '单条 QA 对格式模板',
  system_prompt: '系统提示词',
  system_prompt_template: '系统提示词模板',
  input_template: '输入模板',
  input_pattern: '输入模式',
  output_pattern: '输出模式',
  output_pattern_template: '输出模式模板',
  example_prompt: '示例提示词',
  example_template: '示例模板',
  candidate_template: '候选模板',
  attr_pattern_template: '属性抽取模板',
  target_tag_template: '目标标签模板',
  tag_template: '标签模板',
  field_template: '字段模板',
  continue_prompt: '继续生成提示词',

  // ===== 媒体处理 =====
  frame_num: '抽帧数量',
  fps: '采样帧率',
  frame_sample_num: '采样帧数',
  sample_rate: '采样率',
  audio_bit_rate: '音频比特率',
  bit_rate: '比特率',
  crf: '视频质量参数',
  duration: '时长',
  min_duration: '最小时长',
  max_duration: '最大时长',
  width: '宽度',
  height: '高度',
  size: '尺寸',
  bbox_thresh: '边界框置信度',
  conf_thr: '置信度阈值',
  conf: '置信度',
  canny_threshold1: 'Canny 低阈值',
  canny_threshold2: 'Canny 高阈值',
  alpha_matting: '是否启用 alpha 抠图',
  alpha_matting_foreground_threshold: '前景阈值',
  alpha_matting_background_threshold: '背景阈值',
  alpha_matting_erode_size: '腐蚀尺寸',
  angle_tolerance: '角度容差',
  bgcolor: '背景颜色',

  // ===== 哈希/去重 =====
  hash_func: '哈希函数',
  num_perm: '排列数量',
  band: '分带数',
  rows: '分行数',
  jaccard_threshold: 'Jaccard 阈值',
  hamming_threshold: '汉明距离阈值',
  dedup_set_num: '去重块数',

  // ===== 图像滤镜/变换 =====
  blur_type: '模糊方式',
  blur_radius: '模糊半径',
  kernel_size: '卷积核大小',
  consider_text: '考虑文本',
  consider_video_caption_from_audio: '音频转写作为视频描述',
  consider_video_caption_from_frames: '抽帧作为视频描述',
  consider_video_caption_from_video: '视频模型产出作为视频描述',
  consider_video_tags_from_audio: '音频转写参与打标签',
  consider_video_tags_from_frames: '抽帧参与打标签',

  // ===== 抽取/打标 =====
  entity: '实体名',
  entity_key: '实体字段',
  attribute: '属性名',
  attribute_key: '属性字段',
  attribute_desc_key: '属性描述字段',
  event_desc_key: '事件描述字段',
  entity_types: '实体类型',
  query_entity_type: '查询实体类型',
  meta_tag_key: '元标签字段',
  target_tags: '目标标签',
  feature_keys: '特征字段',
  context_key: '上下文字段',
  candidate_key: '候选字段',

  // ===== LLM 调用参数 =====
  try_num: '失败重试次数',
  try_times: '尝试次数',
  model_params: '模型参数(JSON)',
  sampling_params: '采样参数',
  temperature: '温度',
  top_p: 'top-p',
  top_k: 'top-k',
  max_tokens: '最大生成 token',
  timeout: '超时时间(秒)',
  response_path: '响应字段路径',

  // ===== 命名/计数 =====
  aug_num: '增强倍数',
  aug_rounds: '增强轮数',
  replication: '复制份数',
  batch_size: '批大小',
  batched: '是否分批',
  num_workers: '并发数',
  seed: '随机种子',

  // ===== 词典/词表 =====
  word_dict: '词典路径',
  words_file: '词表文件',
  flagged_words_dir: '敏感词目录',
  stop_words_dir: '停用词目录',

  // ===== 编码/字符集 =====
  pattern: '正则模式',
  rules: '规则集',
  chars_to_remove: '需删除字符',
  replace: '替换串',
  replacement: '替换为',
  contain_all: '必须全包含',
  contain: '包含关键词',
  contain_any: '包含任一',
  contain_none: '不能包含',
  completion_delimiter: '分隔符',
  forbidden_words: '禁用词',

  // ===== 其他 =====
  kwargs: '透传参数',
  args: '参数',
  attr: '属性',
  label: '标签',
  name: '名称',
  description: '描述',
  enabled: '启用',
};
