"""数据合成——SFT 微调样本生成算子(平台自定义算子,API 型)。

单步 LLM 调用:对每条样本的主文本生成一条 SFT 监督微调样本
{"instruction","input","output"},分别写入 instruction_key / input_key /
output_key 三个字段(input 允许为空,与 Alpaca 格式约定一致)。

镜像 generate_cot_mapper 的 API 调用结构:声明 ``api_model`` 参数,平台 engine
按激活的 LLM 配置注入模型名(见 engine.build_config),API key / endpoint 由
dj-process 子进程环境变量注入(engine._subprocess_env),故无需 GPU、免下模型。

经 ``POST /operators/custom`` 上传注册,任务执行时经 custom_operator_paths 动态加载。
"""

import re

from data_juicer.ops.base_op import OPERATORS, Mapper
from data_juicer.utils.model_utils import get_model, prepare_model
from loguru import logger


# 注意:自定义算子上传校验(custom_operators.parse_custom_operator)要求
# register_module 的算子名是字符串字面量,故此处不用变量。
@OPERATORS.register_module("generate_sft_mapper")
class GenerateSftMapper(Mapper):
    """Generate one SFT sample (instruction/input/output) per record via an API model.

    For every sample it takes the main text (``text_key``), asks the API model to
    construct a supervised fine-tuning triple grounded in that text, then writes the
    parts into ``instruction_key`` / ``input_key`` / ``output_key``. The input part
    may legitimately be empty (Alpaca convention). Retries the API call/parse up to
    ``try_num`` times."""

    DEFAULT_SYSTEM_PROMPT = (
        "你是训练数据构造助手。请基于【原文】构造一条 SFT 监督微调样本,包含三部分:\n"
        "- 指令:清晰描述要执行的任务(如问答、总结、抽取、改写等,任务须能由原文支撑);\n"
        "- 输入:完成该任务所需的具体内容或上下文,没有则留空;\n"
        "- 输出:高质量的理想回答,内容须忠实于原文,不得编造。\n"
        "严格按以下格式输出:\n"
        "【指令】\n"
        "指令内容\n"
        "【输入】\n"
        "输入内容(可为空)\n"
        "【输出】\n"
        "输出内容"
    )
    DEFAULT_OUTPUT_PATTERN = r"【指令】\s*(.*?)\s*【输入】\s*(.*?)\s*【输出】\s*(.*)"

    def __init__(
        self,
        api_model: str = "gpt-4o",
        *,
        api_endpoint: str | None = None,
        response_path: str | None = None,
        system_prompt: str | None = None,
        output_pattern: str | None = None,
        instruction_key: str = "instruction",
        input_key: str = "input",
        output_key: str = "output",
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
        :param output_pattern: 解析模型输出的正则(取指令/输入/输出三组)。
        :param instruction_key: 指令写入字段名。
        :param input_key: 输入写入字段名(允许为空串)。
        :param output_key: 输出写入字段名。
        :param try_num: API 调用/解析失败时的重试次数。
        :param model_params: 模型初始化参数。
        :param sampling_params: 采样参数,如 {'temperature': 0.7}。
        """
        super().__init__(**kwargs)

        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.output_pattern = output_pattern or self.DEFAULT_OUTPUT_PATTERN
        self.instruction_key = instruction_key
        self.input_key = input_key
        self.output_key = output_key
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
        # 主内容取 text_key;缺失时回退 query 字段,避免向 LLM 发空【原文】白耗调用
        source = sample.get(self.text_key) or sample.get(self.query_key, "")
        return f"【原文】\n{source}"

    def parse_output(self, raw_output):
        match = re.search(self.output_pattern, raw_output, re.DOTALL)
        if match:
            return (
                match.group(1).strip(),
                match.group(2).strip(),
                match.group(3).strip(),
            )
        return None, None, None

    def process_single(self, sample, rank=None):
        client = get_model(self.model_key, rank=rank)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.build_input(sample)},
        ]
        instruction, inp, output = None, None, None
        for _ in range(self.try_num):
            try:
                raw = client(messages, **self.sampling_params)
                instruction, inp, output = self.parse_output(raw)
                if instruction and output:
                    break
            except Exception as e:
                logger.warning(f"generate_sft_mapper API error: {e}")
        # 指令与输出缺一不算有效样本;输入按 Alpaca 约定允许为空串
        if instruction and output:
            sample[self.instruction_key] = instruction
            sample[self.input_key] = inp or ""
            sample[self.output_key] = output

        return sample
