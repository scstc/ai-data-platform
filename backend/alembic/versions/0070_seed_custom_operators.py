# ruff: noqa: E501 —— 种子 SQL 为整行数据字面量,不折行
"""种子化 5 个自定义算子的目录条目(数据迁移,无 schema 变更)。

内容取自 adp_trace 库导出的 operators 快照(operators_260722171906.sql):
generate_sft_mapper / remove_watermark_text_mapper / jsonl_field_merge_mapper /
generate_qa_from_text_api_mapper / generate_cot_mapper。算子源码随镜像带在
app/data/custom_operators/,启动 lifespan 补齐到 <upload_dir>/custom_operators/。
幂等:ON CONFLICT (name) DO NOTHING,已有同名算子(含用户自行上传)不覆盖。
逐条 op.execute:asyncpg 预编译协议不接受一次多语句。
"""

from __future__ import annotations

from alembic import op

revision: str = "0070_seed_custom_operators"
down_revision: str | None = "0069_job_queue_push_idem"
branch_labels = None
depends_on = None

_SEED_STATEMENTS = [
r"""INSERT INTO "public"."operators" ("name", "category", "zh_label", "summary_en", "summary_zh", "desc_en", "desc_zh", "zh_usage_tip", "scenario_group", "resource_class", "modality", "frameworks", "params", "example", "detail_page", "recommend", "runnable", "usage_count", "created_at", "updated_at", "effect_demo", "is_custom", "source_object_key", "created_by", "visible", "star_count") VALUES ('generate_sft_mapper', 'mapper', 'SFT 微调样本生成', NULL, '对主文本单步 LLM 生成 instruction/input/output 三元组(API 型,免 GPU)', NULL, NULL, NULL, 'trainset', 'cpu', NULL, NULL, '[{"name":"api_model","type":"str","default":"gpt-4o","desc":"API 模型名(平台自动注入激活模型)"},{"name":"instruction_key","type":"str","default":"instruction","desc":"指令写入字段名"},{"name":"input_key","type":"str","default":"input","desc":"输入写入字段名(允许为空串)"},{"name":"output_key","type":"str","default":"output","desc":"输出写入字段名"},{"name":"try_num","type":"int","default":"3","desc":"API 调用/解析失败时的重试次数"}]', NULL, NULL, FALSE, 'ready', 0, '2026-07-07 06:05:34.731571', '2026-07-08 08:35:23.532741', NULL, TRUE, 'generate_sft_mapper.py', 'admin', TRUE, 1)
ON CONFLICT (name) DO NOTHING""",
r"""INSERT INTO "public"."operators" ("name", "category", "zh_label", "summary_en", "summary_zh", "desc_en", "desc_zh", "zh_usage_tip", "scenario_group", "resource_class", "modality", "frameworks", "params", "example", "detail_page", "recommend", "runnable", "usage_count", "created_at", "updated_at", "effect_demo", "is_custom", "source_object_key", "created_by", "visible", "star_count") VALUES ('remove_watermark_text_mapper', 'mapper', '自定义算子', NULL, NULL, NULL, NULL, NULL, NULL, 'cpu', NULL, NULL, 'null', NULL, NULL, FALSE, 'ready', 0, '2026-07-02 09:19:15.044018', '2026-07-08 07:52:01.021808', '[{"before":"text: ''关注公众号 XXX 获取更多资源,正文开始,这是文章主体...''","after":"text: ''正文开始,这是文章主体...''\n  [自定义规则:正则匹配 + 删除开头水印句]"}]', TRUE, 'remove_watermark_text_mapper.py', 'admin', TRUE, 0)
ON CONFLICT (name) DO NOTHING""",
r"""INSERT INTO "public"."operators" ("name", "category", "zh_label", "summary_en", "summary_zh", "desc_en", "desc_zh", "zh_usage_tip", "scenario_group", "resource_class", "modality", "frameworks", "params", "example", "detail_page", "recommend", "runnable", "usage_count", "created_at", "updated_at", "effect_demo", "is_custom", "source_object_key", "created_by", "visible", "star_count") VALUES ('jsonl_field_merge_mapper', 'mapper', '双文件字段合并', NULL, '把扩展 jsonl 文件的字段按行号或关联键合并进主数据集，合成完整样本', NULL, '数据合成算子：读取一个扩展 jsonl 文件，把其中的字段内容合并进主数据集每一行。支持两种对齐方式——key_field 非空时按两边共同的关联字段做键匹配（行序无关，推荐）；留空则按行号一一对齐（强制单进程保证顺序）。拼接默认「主文本+分隔符+扩展文本」，也可用 template 模板（占位符 {main}/{aux}，如 {main}。{aux}。）自定义格式。找不到匹配行时默认保留原文。', 'aux_path 必填，须是任务执行环境内可读的文件路径（容器部署时为 /data 下路径）。行号对齐模式要求两文件行序一一对应；跨文件合并优先用 key_field。', 'synthesis', 'cpu', NULL, NULL, '[{"name":"aux_path","type":"str","default":"","desc":"扩展 jsonl 文件路径（必填）"},{"name":"key_field","type":"str","default":"","desc":"两文件共有的关联字段；留空按行号对齐"},{"name":"aux_field","type":"str","default":"","desc":"取扩展文件的哪个字段；留空同 text 字段"},{"name":"target_field","type":"str","default":"","desc":"结果写入字段；留空覆盖 text 字段"},{"name":"separator","type":"str","default":"。","desc":"拼接分隔符"},{"name":"template","type":"str","default":"","desc":"模板，占位符 {main}/{aux}，如 {main}。{aux}。"},{"name":"keep_unmatched","type":"bool","default":"true","desc":"无匹配时保留原文"}]', '{"process": [{"jsonl_field_merge_mapper": {"aux_path": "/data/uploads/xxx/aux.jsonl", "template": "{main}。{aux}。"}}]}', NULL, FALSE, 'ready', 0, '2026-07-05 05:16:22.815345', '2026-07-08 08:12:44.055416', NULL, TRUE, 'jsonl_field_merge_mapper.py', 'admin', TRUE, 0)
ON CONFLICT (name) DO NOTHING""",
r"""INSERT INTO "public"."operators" ("name", "category", "zh_label", "summary_en", "summary_zh", "desc_en", "desc_zh", "zh_usage_tip", "scenario_group", "resource_class", "modality", "frameworks", "params", "example", "detail_page", "recommend", "runnable", "usage_count", "created_at", "updated_at", "effect_demo", "is_custom", "source_object_key", "created_by", "visible", "star_count") VALUES ('generate_qa_from_text_api_mapper', 'mapper', 'QA 问答对生成(API)', NULL, '对主文本单步 LLM 生成若干问答对并 1→N 展开(API 型,免 GPU)', NULL, NULL, '输入为无结构文本(text 字段),每条文本生成多组 QA 并展开成多条样本;api_model 留空自动用系统推理模型', 'trainset', 'cpu', NULL, NULL, '[{"desc":"API 模型名(平台自动注入激活模型)","name":"api_model","type":"str","default":"gpt-4o"},{"desc":"每条文本最多保留的 QA 对数;留空不限制","name":"max_num","type":"int","default":""},{"desc":"API 调用/解析失败时的重试次数","name":"try_num","type":"int","default":"3"}]', NULL, NULL, FALSE, 'ready', 0, '2026-07-13 02:43:56.917188', '2026-07-13 02:43:56.917188', NULL, TRUE, 'generate_qa_from_text_api_mapper.py', 'admin', TRUE, 0)
ON CONFLICT (name) DO NOTHING""",
r"""INSERT INTO "public"."operators" ("name", "category", "zh_label", "summary_en", "summary_zh", "desc_en", "desc_zh", "zh_usage_tip", "scenario_group", "resource_class", "modality", "frameworks", "params", "example", "detail_page", "recommend", "runnable", "usage_count", "created_at", "updated_at", "effect_demo", "is_custom", "source_object_key", "created_by", "visible", "star_count") VALUES ('generate_cot_mapper', 'mapper', 'CoT 推理链生成', NULL, '对问题单步 LLM 生成逐步推理链 + 答案(API 型,免 GPU)', NULL, NULL, NULL, 'trainset', 'cpu', NULL, NULL, '[{"desc":"API 模型名(平台自动注入激活模型)","name":"api_model","type":"str","default":"gpt-4o"},{"desc":"推理链写入字段名","name":"cot_key","type":"str","default":"cot"},{"desc":"API 调用/解析失败时的重试次数","name":"try_num","type":"int","default":"3"}]', NULL, NULL, FALSE, 'ready', 0, '2026-07-05 15:50:09.950893', '2026-07-06 07:39:15.936446', NULL, TRUE, 'generate_cot_mapper.py', 'admin', TRUE, 0)
ON CONFLICT (name) DO NOTHING""",
]


def upgrade() -> None:
    for stmt in _SEED_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute(
        "DELETE FROM operators WHERE is_custom AND name IN ("
        "'generate_sft_mapper', 'remove_watermark_text_mapper', "
        "'jsonl_field_merge_mapper', 'generate_qa_from_text_api_mapper', "
        "'generate_cot_mapper')"
    )
