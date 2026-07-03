"""手写 60+ 个算子的文本示例,覆盖 agent / dialog / PII / 优化 / 检测 / 自定义 / pipeline / aggregator 等。

DJ 仓 .md 没示例的算子 + 后端 71 neither 剩余部分。覆盖规则:
- 文本可表达效果 → 写 1-2 条 before/after 文本 demo
- 需要 GPU/模型效果(CV/video) → 写文本描述输入 + 输出,引导用户到 render_op_demos.py
- 已是 alpha(API 形态变化大) → 写 input 形态 + expected output 形态,标注 alpha 阶段

跑:cd backend && ./.venv/Scripts/python.exe scripts/import_text_demos.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.operator import Operator

# 通用:文本可表达效果的算子(agent / dialog / PII / 优化 / 检测 等)
# 每条:{"before": "输入", "after": "处理后"}
# 标记 _category:
#   "text"     — 纯文本 demo
#   "alpha"    — alpha 阶段,只描述 API 形态,效果不可预期
#   "model"    — 需要 LLM/ML 模型,只写"输入 + 期望输出形态"
#   "meta"     — 元数据操作,统计/复制字段类
TEXT_DEMOS: dict[str, list[dict]] = {
    # === aggregator(剩 1 个) ===
    "meta_tags_aggregator": [
        {
            "before": "5 条样本的 meta.tags:\n  [{'env': 'prod'}, {'env': 'staging'}, {'env': 'prod'}, {'env': 'prod'}, {'env': 'dev'}]",
            "after": "统一语义相近的 tag:\n  [{'env': 'production'}, {'env': 'staging'}, {'env': 'production'}, {'env': 'production'}, {'env': 'development'}]\n[由 LLM 把 prod → production、dev → development 等合并]",
        },
    ],
    # === filter(剩 2 个 LLM 评估) ===
    "in_context_influence_filter": [
        {
            "before": "500 条文本样本(预训练语料)\n[text, label, val_loss_diff]",
            "after": "每条文本被 LLM 评估对验证集的影响(ICIF 指标)\n保留 |ICIF| 高的样本(对模型泛化贡献大):\n  保留 187 条,过滤 313 条",
        },
    ],
    "llm_task_relevance_filter": [
        {
            "before": "1 万条候选指令样本(meta.task_candidates)\n[text, candidate_task]",
            "after": "LLM 给每条样本打分(对目标 task 的相关度)\n保留相关度 > 0.7 的样本:\n  保留 3240 条,过滤 6760 条",
        },
    ],
    # === mapper:agent(dialog)类 (alpha,API 形态) ===
    "agent_bad_case_signal_mapper": [
        {
            "before": "样本: {text: '...', meta: {tools_invoked: ['web_search', 'calculator']}}",
            "after": "输出: {text: '...', meta: {bad_case_signals: ['tool_error_after_goal_met'], bad_case_tier: 'minor'}}",
        },
    ],
    "agent_insight_llm_mapper": [
        {
            "before": "样本: {text: '...', meta: {tool_stats: {...}, llm_judges: ['A', 'B']}}",
            "after": "LLM 综合 stats + judges 输出:\n  meta.agent_insight_llm: '{\"decision\": \"keep\", \"reason\": \"工具调用有效,目标达成\"}'",
        },
    ],
    "agent_skill_insight_mapper": [
        {
            "before": "样本: {meta: {agent_tool_types: ['code_exec', 'web_search'], agent_skill_types: ['planning', 'error_recovery']}}",
            "after": "LLM 总结为 insight:\n  meta.agent_skill_insight: '该轨迹展示了一个具备规划与错误恢复能力的代码执行型 agent'",
        },
    ],
    "agent_tool_relevance_mapper": [
        {
            "before": "样本: {text: '用户: 查询杭州天气', meta: {agent_tool_types: ['weather_api', 'translate']}}",
            "after": "输出评分(0-1):\n  meta.tool_relevance: 0.92 (weather_api 高度匹配,translate 不必要)",
        },
    ],
    "agent_tool_type_mapper": [
        {
            "before": "样本: {meta: {agent_tool_types: ['code_exec', 'web_search', 'code_exec']}}",
            "after": "派别推导:\n  meta.primary_tool_type: 'code_exec'  // 出现频次最高\n  meta.dominant_tool_types: ['code_exec', 'web_search']  // 多于阈值",
        },
    ],
    "agent_trace_coherence_mapper": [
        {
            "before": "扁平化会话文本: '目标:查询天气→查询 API→返回温度→用户追问明天→查询 API→返回'",
            "after": "LLM 打分(0-1):\n  meta.trace_coherence: 0.85  // 目标集中,无偏离",
        },
    ],
    "dialog_clarification_quality_mapper": [
        {
            "before": "用户: '帮我查一下' (含糊)\n助手: '请问您想查什么?' (澄清)\n助手后续: '北京今天晴 25度'",
            "after": "LLM 评估:\n  meta.clarification_quality: 0.78  // 澄清必要且有效",
        },
    ],
    "dialog_coreference_mapper": [
        {
            "before": "用户: '把它删了'\n助手: '好的,文件 /tmp/x.txt 已删除'",
            "after": "LLM 评估:\n  meta.coreference_resolution: 0.92  // '它' 准确指代上轮的文件",
        },
    ],
    "dialog_error_recovery_mapper": [
        {
            "before": "助手: '北京人口 3000 万'\n用户: '不对,实际 2100 多万'\n助手: '抱歉,北京常住人口约 2185 万(2023 数据)'",
            "after": "LLM 评估:\n  meta.error_recovery: 0.88  // 错误被承认并纠正",
        },
    ],
    "dialog_memory_consistency_mapper": [
        {
            "before": "用户(首轮): '我不吃辣'\n助手(10 轮后): '推荐川菜给您'",
            "after": "LLM 评估:\n  meta.memory_consistency: 0.15  // 严重违反首轮约束",
        },
    ],
    "dialog_non_repetition_mapper": [
        {
            "before": "助手(第 1-3 轮): '好的' / '我帮您' / '马上处理'",
            "after": "LLM 评估:\n  meta.non_repetition: 0.45  // 模板化,缺信息量",
        },
    ],
    "dialog_proactivity_mapper": [
        {
            "before": "用户: '推荐餐厅'\n助手: '附近有 3 家: A、B、C'",
            "after": "LLM 评估:\n  meta.proactivity: 0.72  // 主动给出多选项,适度不啰嗦",
        },
    ],
    "dialog_topic_shift_mapper": [
        {
            "before": "对话: 聊北京天气→聊旅游→聊美食",
            "after": "LLM 评估:\n  meta.topic_shift_score: 0.65  // 渐变,非硬切",
        },
    ],
    "tool_success_tagger_mapper": [
        {
            "before": "样本: {meta: {tool_invocations: [{'name':'web_search','success':True},{'name':'web_search','success':False}]}}",
            "after": "派生:\n  meta.tool_success_count: 1\n  meta.tool_fail_count: 1\n  meta.tool_success_ratio: 0.5",
        },
    ],
    "usage_counter_mapper": [
        {
            "before": "样本: {choices: [{'usage':{'prompt_tokens':10,'completion_tokens':20}}]}",
            "after": "写入 meta:\n  meta.prompt_tokens: 10\n  meta.completion_tokens: 20\n  meta.total_tokens: 30",
        },
    ],
    # === mapper:PII ===
    "pii_llm_suspect_mapper": [
        {
            "before": "text: '我叫张三,电话 13800138000,邮箱 zhang@example.com'",
            "after": "LLM 审计是否漏掉 PII:\n  meta.pii_suspects: [{'type':'phone','span':'13800138000','confidence':0.99}]",
        },
    ],
    "pii_redaction_mapper": [
        {
            "before": "text: '我叫张三,电话 13800138000,住北京海淀'",
            "after": "text: '我叫<NAME>,电话<PHONE>,住北京海淀'\n[正则 + 词典双路,敏感实体替换为占位符]",
        },
    ],
    # === mapper:优化 / 合成 (LLM) ===
    "optimize_prompt_mapper": [
        {
            "before": "prompt: '写一首关于秋天的诗'",
            "after": "LLM 扩展为 few-shot 详细 prompt:\n  '你是一位唐代诗人,请写一首五言绝句,主题为秋天,押平声韵,首句点明时间...'",
        },
    ],
    "optimize_qa_mapper": [
        {
            "before": "{q: 'Python 怎么用?', a: 'import 一下'}",
            "after": "{q: 'Python 中如何导入一个模块?', a: '使用 import 语句,例如 import os 导入标准库 os 模块;使用 from os import path 导入子模块'}",
        },
    ],
    "optimize_query_mapper": [
        {
            "before": "query: '修电脑'",
            "after": "query: '我的笔记本电脑(Win11)开机后蓝屏,显示错误代码 IRQL_NOT_LESS_OR_EQUAL,如何排查和修复?'",
        },
    ],
    "optimize_response_mapper": [
        {
            "before": "response: '北京是首都'",
            "after": "response: '北京是中华人民共和国的首都,位于华北平原北部,下辖 16 个区,常住人口约 2185 万(2023 年末)'",
        },
    ],
    "generate_qa_from_examples_mapper": [
        {
            "before": "示例(种子): [{q:'Python 怎么注释?',a:'用 # 单行,三引号多行'}]",
            "after": "LLM 基于示例扩展生成 N 个新 QA 对(典型 Self-Instruct):\n  [{q:'JS 怎么注释?',a:'// 单行,/* */ 多行'}, ...]",
        },
    ],
    "pair_preference_mapper": [
        {
            "before": "{prompt: '解释量子计算', response: '量子计算用量子比特...' (chosen)}",
            "after": "{prompt, chosen, rejected: '量子计算就是比传统快', rejected_reason: '表述过于简单,缺关键概念'}\n[构造 DPO 训练偏好对]",
        },
    ],
    "llm_extract_mapper": [
        {
            "before": "text: '张三,男,32 岁,阿里巴巴高级工程师,杭州'",
            "after": "meta: {extracted: {name:'张三', gender:'男', age:32, company:'阿里巴巴', title:'高级工程师', city:'杭州'}}",
        },
    ],
    # === mapper:检测 / 视觉描述 (需模型,但输入输出文本可表达) ===
    "detect_character_attributes_mapper": [
        {
            "before": "输入: {image: <人物图>, caption: '图中三人,李莲花居中', names: ['李莲花','笛飞声','方多病']}",
            "after": "MLLM 输出:\n  meta.character_attrs: {\n    '李莲花': {'age':'30s','clothes':'白袍','expression':'淡然','weapon':'少师剑'},\n    ...\n  }",
        },
    ],
    "detect_character_locations_mapper": [
        {
            "before": "输入: {image: <场景图>, names: ['李莲花','笛飞声']}",
            "after": "YOLOE + MLLM 输出 bbox:\n  meta.character_boxes: {\n    '李莲花': [120, 80, 280, 420],  // [x1,y1,x2,y2]\n    '笛飞声': [450, 100, 600, 430]\n  }",
        },
    ],
    "detect_main_character_mapper": [
        {
            "before": "输入: {image: <封面图>, caption: '三人在论剑,中间白衣者为主角'}",
            "after": "MLLM 输出主角列表:\n  meta.main_characters: ['李莲花']\n  meta.character_confidence: {'李莲花': 0.93, '笛飞声': 0.41}",
        },
    ],
    "mllm_mapper": [
        {
            "before": "输入: {image: <图>, question: '图中的人在做什么?'}",
            "after": "MLLM (Qwen-VL / LLaVA) 输出:\n  text: '图中的工程师正在调试一台服务器,屏幕上显示代码编辑器'",
        },
    ],
    # === mapper:意图 / 情感 / 主题(HF 模型) ===
    "query_intent_detection_mapper": [
        {
            "before": "query: '北京明天会下雨吗?'",
            "after": "meta: {intent: 'weather_query', intent_scores: {weather_query: 0.96, chitchat: 0.03, navigation: 0.01}}",
        },
    ],
    "query_sentiment_detection_mapper": [
        {
            "before": "query: '这个功能做得太烂了'",
            "after": "meta: {sentiment: 'negative', sentiment_score: 0.91}",
        },
    ],
    "query_topic_detection_mapper": [
        {
            "before": "query: 'Transformer 的 self-attention 怎么算?'",
            "after": "meta: {topic: 'deep_learning', topic_scores: {deep_learning: 0.88, nlp: 0.78, math: 0.12}}",
        },
    ],
    # === mapper:视频 captioning (模型不可达,但输入输出结构可写) ===
    "video_captioning_from_audio_mapper": [
        {
            "before": "输入: {video: <含人声视频>}",
            "after": "Qwen-Audio 听音频流 → 视频描述:\n  text: '一段采访视频,主持人询问嘉宾对 AI 行业的看法,嘉宾回答...'\n[仅基于音频内容,无视觉信息]",
        },
    ],
    "video_captioning_from_frames_mapper": [
        {
            "before": "输入: {video: <视频>, sample_frames: 8}",
            "after": "image-to-text 模型对每帧生成描述后融合:\n  text: '视频展示一个城市的延时摄影,从清晨到夜晚...'",
        },
    ],
    "video_captioning_from_summarizer_mapper": [
        {
            "before": "输入: {video: <视频>, captions_from: [audio, frames, tags]}",
            "after": "LLM 融合多路描述生成最终 caption:\n  text: '一个人物专访视频,嘉宾介绍机器学习在医疗影像中的应用,穿插多张 CT 图演示'",
        },
    ],
    "video_captioning_from_video_mapper": [
        {
            "before": "输入: {video: <视频>}",
            "after": "Video-LLaVA 等端到端模型输出:\n  text: '一个女孩在公园里教小狗接飞盘,背景是樱花盛开的春天'",
        },
    ],
    "video_captioning_from_vlm_mapper": [
        {
            "before": "输入: {video: <视频>, vllm_model: 'Qwen2-VL-7B'}",
            "after": "vLLM 服务消费视频,生成描述:\n  text: '烹饪教学节目,主厨演示如何做意式肉酱面...'",
        },
    ],
    "video_camera_pose_mapper": [
        {
            "before": "输入: {video: <动态场景视频>}",
            "after": "MegaSaM + MoGe-2 输出每帧相机位姿:\n  meta.camera_poses: [(R1,t1), (R2,t2), ...]  // 旋转 + 平移\n  meta.point_clouds: <n×3 数组>",
        },
    ],
    "video_depth_estimation_mapper": [
        {
            "before": "输入: {video: <视频>}",
            "after": "depth 模型输出每帧深度图:\n  meta.depth_maps: [<h×w float32>]\n[用于 3D 重建 / 特效]",
        },
    ],
    "video_extract_frames_mapper": [
        {
            "before": "输入: {video: <30 秒 30fps 视频 = 900 帧>}",
            "after": "按 method 抽帧:\n  - 'uniform': 抽 8 帧均匀分布\n  - 'key_frame': 抽 3 个关键帧\n  images: [<8 张图>]\n[输出到 images 字段,可接下游 captioning]",
        },
    ],
    "video_face_blur_mapper": [
        {
            "before": "输入: {video: <含人脸视频>}",
            "after": "OpenCV 检测每帧人脸 → 高斯模糊:\n  video: <同长度视频,人脸区域被模糊>\n[需 opencv-python 完整版(headless 缺 cascade)]",
        },
    ],
    "video_remove_watermark_mapper": [
        {
            "before": "输入: {video: <含水印视频>, region: [x,y,w,h]}",
            "after": "Inpainting 修复水印区域:\n  video: <同长度视频,水印被自然纹理替换>\n[需 inpainting 模型]",
        },
    ],
    "video_resize_aspect_ratio_mapper": [
        {
            "before": "输入: {video: 1920x1080, target_ratio: (16,9) → (1,1)}",
            "after": "视频: 1080x1080 (居中裁切)\n  meta.aspect_ratio: 1.0",
        },
    ],
    "video_resize_resolution_mapper": [
        {
            "before": "输入: {video: 4K 3840x2160, target: 1920x1080}",
            "after": "视频: 1920x1080 (双线性下采样)\n  size: 1920x1080\n  meta.original_resolution: '3840x2160'",
        },
    ],
    "video_undistort_mapper": [
        {
            "before": "输入: {video: <畸变视频>, intrinsics: {fx,fy,cx,cy}, distortion: [k1,k2,p1,p2,k3]}",
            "after": "OpenCV undistort:\n  video: <校正后视频>\n  meta.applied_undistortion: true",
        },
    ],
    "video_hand_reconstruction_hawor_mapper": [
        {
            "before": "输入: {video: <含手部视频>}",
            "after": "HaWoR + MoGe-2 重建每帧 3D 手部 mesh:\n  meta.hand_meshes: [<n×vertices×3>]\n  meta.hand_poses: [<n×关节参数>]",
        },
    ],
    "video_hand_reconstruction_mapper": [
        {
            "before": "输入: {video: <含手部视频>, model: 'WiLoR-mini'}",
            "after": "WiLoR 模型输出 2D/3D 手部关键点 + mesh:\n  meta.hand_landmarks: [<n×21×2>]\n  meta.hand_meshes: [<n×778×3>]",
        },
    ],
    # === mapper:图像类(模型,文本表达输入输出) ===
    "image_blur_mapper": [
        {
            "before": "(本地资源)\n  原图: /operator-demos/image_blur_mapper/before.png\n参数: p=1.0, blur_type=gaussian, radius=3",
            "after": "(本地资源)\n  模糊后: /operator-demos/image_blur_mapper/after.png\n  [实际像素被 GaussianBlur kernel 替换,本地真实渲染]",
        },
    ],
    "image_face_blur_mapper": [
        {
            "before": "输入: image: <含人脸图>, radius=15",
            "after": "输出: image: <同尺寸图,人脸区域被高斯模糊>\n  [需 OpenCV 完整版 cascade 数据]",
        },
    ],
    "image_remove_background_mapper": [
        {
            "before": "输入: image: <人物图>",
            "after": "输出: image: <PNG 透明背景,人物前景保留>\n  [需 rembg/u2net 模型]",
        },
    ],
    "image_captioning_mapper": [
        {
            "before": "输入: image: <图>",
            "after": "BLIP 模型输出:\n  text: 'a cat sitting on a wooden table looking at the camera'\n  [HF blip-image-captioning-base]",
        },
    ],
    "image_diffusion_mapper": [
        {
            "before": "输入: caption: 'a futuristic city at sunset'",
            "after": "Stable Diffusion 输出:\n  images: [<512x512 SD 生成图>]\n  meta.seed: 42\n  [需 GPU + diffusers]",
        },
    ],
    "image_segment_mapper": [
        {
            "before": "输入: image: <街景图>",
            "after": "SAM 输出每物体 mask + bbox:\n  meta.segmentation_masks: [<n×H×W bool>]\n  meta.boxes: [[x1,y1,x2,y2], ...]",
        },
    ],
    "image_tagging_vlm_mapper": [
        {
            "before": "输入: image: <图>",
            "after": "VLM 输出 tags + 置信度:\n  meta.tags: [{'tag':'outdoor','score':0.93}, {'tag':'sunset','score':0.87}, ...]",
        },
    ],
    "image_detection_yolo_mapper": [
        {
            "before": "输入: image: <街景图>, model: 'yolov8n'",
            "after": "YOLO 输出:\n  meta.detections: [{'class':'car','bbox':[120,80,300,200],'conf':0.92}, ...]",
        },
    ],
    "image_mmpose_mapper": [
        {
            "before": "输入: image: <人物图>",
            "after": "MMPose 输出人体关键点:\n  meta.keypoints: [[x1,y1,conf1], [x2,y2,conf2], ...]  // 17 个 COCO 关节点",
        },
    ],
    "image_sam_3d_body_mapper": [
        {
            "before": "输入: image: <人物图>",
            "after": "SAM 3D Body 输出:\n  meta.body_mesh: <n_vertices×3 mesh>\n  meta.smpl_params: <shape + pose 参数>",
        },
    ],
    "image_tagging_mapper": [
        {
            "before": "输入: image: <图>",
            "after": "WD Tagger 输出:\n  meta.tags: [{'tag':'1girl','score':0.95}, ...]\n  [动漫/插画场景专用]",
        },
    ],
    # === mapper:图像差异(双图) ===
    "imgdiff_difference_area_generator_mapper": [
        {
            "before": "输入: images: [<图A>, <图B>]",
            "after": "输出每对差异区域 bbox:\n  meta.diff_areas: [{'bbox':[x1,y1,x2,y2],'score':0.85}, ...]",
        },
    ],
    "imgdiff_difference_caption_generator_mapper": [
        {
            "before": "输入: images: [<图A>, <图B>], bbox: [x1,y1,x2,y2]",
            "after": "MLLM 描述差异:\n  text: '图 B 中此区域新增了一棵樱花树,图 A 中为空地'",
        },
    ],
    # === mapper:文件 IO ===
    "download_file_mapper": [
        {
            "before": "text: 'https://example.com/data.zip'",
            "after": "下载到本地 + 替换文本:\n  text: '/tmp/dj_cache/data_<hash>.zip'\n  meta.download_path: '/tmp/dj_cache/data_<hash>.zip'",
        },
    ],
    "s3_download_file_mapper": [
        {
            "before": "text: 's3://bucket/key/data.jsonl'",
            "after": "从 S3 下载:\n  text: '/tmp/s3_cache/data_<hash>.jsonl'\n  [需 S3 凭证]",
        },
    ],
    "s3_upload_file_mapper": [
        {
            "before": "text: '/local/path/data.jsonl'",
            "after": "上传到 S3,文本替换为 S3 URL:\n  text: 's3://bucket/uploaded/data_<hash>.jsonl'",
        },
    ],
    # === mapper:Python 自定义 ===
    "python_file_mapper": [
        {
            "before": "样本: {text: 'Hello, World!'}\n[用户上传脚本 my_mapper.py 含 transform(sample) 函数]",
            "after": "执行 transform 后:\n  {text: 'HELLO, WORLD!'}  // 假设脚本 .upper()\n  [用户自定义任意处理逻辑]",
        },
    ],
    "python_lambda_mapper": [
        {
            "before": "样本: {text: 'foo bar baz'}\n[lambda: sample: {'text': sample['text'].replace(' ', '_')}]",
            "after": "{text: 'foo_bar_baz'}\n[直接传 lambda 表达式,无需上传文件]",
        },
    ],
    # === audio mappers(模型/IO) ===
    "audio_ffmpeg_wrapped_mapper": [
        {
            "before": "audio: <原始 wav>, ffmpeg_args: 'atempo=2.0'",
            "after": "audio: <2 倍速 wav>\n  [调用 ffmpeg 处理任意音频效果]",
        },
    ],
    # === pipeline(需 Ray+vLLM 集群) ===
    "llm_inference_with_ray_vllm_pipeline": [
        {
            "before": "输入: dataset 1000 条 prompt 样本",
            "after": "Ray 集群 + vLLM 引擎,生成 response:\n  输出 dataset: 1000 条 {prompt, response} 样本\n  [需 Ray cluster + vLLM server 已起]",
        },
    ],
    "vlm_inference_with_ray_vllm_pipeline": [
        {
            "before": "输入: dataset 1000 条 {image, question} 样本",
            "after": "vLLM-Engine 处理图文对:\n  输出 dataset: 1000 条 {image, question, answer} 样本",
        },
    ],
    # === 用户上传的自定义算子 ===
    "remove_watermark_text_mapper": [
        {
            "before": "text: '关注公众号 XXX 获取更多资源,正文开始,这是文章主体...'",
            "after": "text: '正文开始,这是文章主体...'\n  [自定义规则:正则匹配 + 删除开头水印句]",
        },
    ],
}


def main() -> None:
    engine = create_engine(str(settings.database_url).replace("+asyncpg", "+psycopg2"))
    Session = sessionmaker(bind=engine)
    updated, missing, skipped = 0, [], []
    with Session() as session:
        for name, demos in TEXT_DEMOS.items():
            op = session.get(Operator, name)
            if op is None:
                missing.append(name)
                continue
            # 仅当 effect_demo 为空才覆盖(保护已有真实 demo)
            if op.effect_demo:
                skipped.append(name)
                continue
            op.effect_demo = demos
            updated += 1
        session.commit()
    print(f"已写入: {updated} 个算子")
    print(f"DB 不存在(跳过): {len(missing)}")
    if missing:
        print(f"  示例: {missing[:5]}")
    print(f"已有 effect_demo(保留): {len(skipped)}")


if __name__ == "__main__":
    main()
