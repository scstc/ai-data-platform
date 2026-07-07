"""数据合成——CoT 推理链生成算子(平台自定义算子,API 型)。

单步 LLM 调用:对每条样本的问题(query_key)生成「逐步推理过程 + 最终答案」,
推理写入 cot_key、答案写回 response_key。已有答案(若存在)作参考,保证结论一致。

镜像 data-juicer calibrate_qa_mapper 的 API 调用结构:声明 ``api_model`` 参数,
平台 engine 按激活的 LLM 配置注入模型名(见 engine.build_config),API key / endpoint
由 dj-process 子进程环境变量注入(engine._subprocess_env),故无需 GPU、免下模型。

经 ``POST /operators/custom`` 上传注册,任务执行时经 custom_operator_paths 动态加载。
"""

import re

from data_juicer.ops.base_op import OPERATORS, Mapper
from data_juicer.utils.model_utils import get_model, prepare_model
from loguru import logger


# 注意:自定义算子上传校验(custom_operators.parse_custom_operator)要求
# register_module 的算子名是字符串字面量,故此处不用变量。
@OPERATORS.register_module("generate_cot_mapper")
class GenerateCotMapper(Mapper):
    """Generate chain-of-thought reasoning for each question via an API model.

    For every sample it takes the question (``query_key``), asks the API model for a
    step-by-step reasoning chain plus a final answer, writes the reasoning into
    ``cot_key`` and the answer into ``response_key``. An existing answer (if any) is
    passed as a reference so the derived conclusion stays consistent. Retries the API
    call/parse up to ``try_num`` times."""

    DEFAULT_SYSTEM_PROMPT = (
        "你是严谨的推理助手。请针对【问题】给出清晰的逐步推理过程(Chain-of-Thought),"
        "最后给出结论。若提供了【参考答案】,推理须与其结论一致。\n"
        "按以下格式输出:\n"
        "【推理】\n"
        "分步推理过程\n"
        "【答案】\n"
        "最终答案"
    )
    DEFAULT_OUTPUT_PATTERN = r"【推理】\s*(.*?)\s*【答案】\s*(.*)"

    def __init__(
        self,
        api_model: str = "gpt-4o",
        *,
        api_endpoint: str | None = None,
        response_path: str | None = None,
        system_prompt: str | None = None,
        output_pattern: str | None = None,
        cot_key: str = "cot",
        try_num: int = 3,
        model_params: dict | None = None,
        sampling_params: dict | None = None,
        **kwargs,
    ):
        """
        :param api_model: API 模型名(平台自动注入激活模型)。
        :param api_endpoint: API endpoint;留空走环境注入的 OPENAI_BASE_URL。
        :param response_path: 从 API 响应取内容的路径,默认 choices.0.message.content。
        :param system_prompt: 系统提示词。
        :param output_pattern: 解析模型输出的正则(取推理与答案两组)。
        :param cot_key: 推理链写入字段名。
        :param try_num: API 调用/解析失败时的重试次数。
        :param model_params: 模型初始化参数。
        :param sampling_params: 采样参数,如 {'temperature': 0.7}。
        """
        super().__init__(**kwargs)

        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.output_pattern = output_pattern or self.DEFAULT_OUTPUT_PATTERN
        self.cot_key = cot_key
        self.sampling_params = sampling_params or {}
        self.try_num = try_num
        self.model_key = prepare_model(
            model_type="api",
            model=api_model,
            endpoint=api_endpoint,
            response_path=response_path,
            **(model_params or {}),
        )

    def build_input(self, sample):
        # 平台数据集主内容通常在 text_key 而非 query 字段;query 缺失/为空时回退
        # 主文本,避免向 LLM 发出空【问题】占位符白耗 API 调用
        question = sample.get(self.query_key) or sample.get(self.text_key, "")
        reference = sample.get(self.response_key, "")
        parts = [f"【问题】\n{question}"]
        if reference:
            parts.append(f"【参考答案】\n{reference}")
        return "\n".join(parts)

    def parse_output(self, raw_output):
        match = re.search(self.output_pattern, raw_output, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return None, None

    def process_single(self, sample, rank=None):
        client = get_model(self.model_key, rank=rank)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.build_input(sample)},
        ]
        cot, answer = None, None
        for _ in range(self.try_num):
            try:
                output = client(messages, **self.sampling_params)
                cot, answer = self.parse_output(output)
                if cot or answer:
                    break
            except Exception as e:
                logger.warning(f"generate_cot_mapper API error: {e}")
        if cot:
            sample[self.cot_key] = cot
        if answer:
            sample[self.response_key] = answer

        return sample
