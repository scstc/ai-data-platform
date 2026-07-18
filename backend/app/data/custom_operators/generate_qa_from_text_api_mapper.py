"""数据合成——文本生成 QA 对算子(平台自定义算子,API 型)。

DJ 原生 ``generate_qa_from_text_mapper`` 依赖本地 HF 模型(doc2qa 微调模型,需
GPU);本算子是其 API 版:对每条样本的主文本(text_key)单步调用 LLM 生成若干
问答对,按 1→N 展开成多条样本(query 写入 query_key、answer 写入 response_key,
其余字段原样复制)。解析口径与原生算子一致(Human:/Assistant: 正则)。

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
@OPERATORS.register_module("generate_qa_from_text_api_mapper")
class GenerateQAFromTextApiMapper(Mapper):
    """Generate question-answer pairs from text via an API model.

    API-based counterpart of data-juicer's ``generate_qa_from_text_mapper`` (which
    requires a local doc2qa HF model on GPU). For every sample it sends the main text
    (``text_key``) to the API model and parses ``Human:``/``Assistant:`` turns from the
    response into QA pairs, expanding one input row into N output rows (question into
    ``query_key``, answer into ``response_key``, all other fields copied as-is).
    Samples yielding no QA pairs are dropped with a warning, mirroring the original
    operator. Retries the API call/parse up to ``try_num`` times."""

    _batched_op = True

    DEFAULT_SYSTEM_PROMPT = (
        "你是问答数据构造助手。请根据用户提供的文本,生成若干组高质量问答对:\n"
        "- 问题须能从文本中找到明确答案,覆盖文本的不同要点,彼此不重复\n"
        "- 答案须忠于原文,准确、完整、自成一体(不依赖上下文也能读懂)\n"
        "- 与文本同语言作答\n"
        "严格按以下格式输出,不要输出任何其他内容:\n"
        "Human: 第一个问题\n"
        "Assistant: 第一个答案\n"
        "Human: 第二个问题\n"
        "Assistant: 第二个答案"
    )
    # 与 DJ 原生 generate_qa_from_text_mapper 相同的解析正则
    DEFAULT_OUTPUT_PATTERN = r"Human:(.*?)Assistant:(.*?)(?=Human|$)"

    def __init__(
        self,
        api_model: str = "gpt-4o",
        max_num: int | None = None,
        *,
        api_endpoint: str | None = None,
        response_path: str | None = None,
        system_prompt: str | None = None,
        output_pattern: str | None = None,
        try_num: int = 3,
        model_params: dict | None = None,
        sampling_params: dict | None = None,
        **kwargs,
    ):
        """
        :param api_model: API 模型名(平台自动注入激活模型)。
        :param max_num: 每条文本最多保留的 QA 对数;None 不限制。
        :param api_endpoint: API endpoint;留空走环境注入的 OPENAI_BASE_URL。
        :param response_path: 从 API 响应取内容的路径,默认 choices.0.message.content。
        :param system_prompt: 系统提示词(需约束模型按 Human:/Assistant: 格式输出)。
        :param output_pattern: 解析模型输出的正则(取问题与答案两组)。
        :param try_num: API 调用/解析失败时的重试次数。
        :param model_params: 模型初始化参数。
        :param sampling_params: 采样参数,如 {'temperature': 0.7}。
        """
        super().__init__(**kwargs)

        self.max_num = max_num
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.output_pattern = output_pattern or self.DEFAULT_OUTPUT_PATTERN
        self.try_num = try_num
        self.sampling_params = sampling_params or {}
        self.model_key = prepare_model(
            model_type="api",
            model=api_model,
            endpoint=api_endpoint,
            response_path=response_path,
            **(model_params or {}),
        )

    def parse_output(self, raw_output):
        qa_list = []
        for match in re.findall(self.output_pattern, raw_output, re.DOTALL):
            user, assistant = match
            if user.strip() and assistant.strip():
                qa_list.append((user.strip(), assistant.strip()))
        return qa_list

    def process_batched(self, samples, rank=None):
        client = get_model(self.model_key, rank=rank)

        input_keys = samples.keys()
        num_samples = len(samples[next(iter(input_keys))])
        output_keys = input_keys | {self.query_key, self.response_key}
        output_samples = {key: [] for key in output_keys}

        for i in range(num_samples):
            text = samples[self.text_key][i]
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": text},
            ]
            qa_list = []
            for _ in range(self.try_num):
                try:
                    output = client(messages, **self.sampling_params)
                    qa_list = self.parse_output(output)
                    if qa_list:
                        break
                except Exception as e:
                    logger.warning(f"generate_qa_from_text_api_mapper API error: {e}")

            if self.max_num is not None:
                qa_list = qa_list[: self.max_num]

            if qa_list:
                for q, a in qa_list:
                    # query/response 若已在输入字段中,由下方统一写入,不在此重复
                    for input_k in input_keys - {self.query_key, self.response_key}:
                        output_samples[input_k].append(samples[input_k][i])
                    output_samples[self.query_key].append(q)
                    output_samples[self.response_key].append(a)
            else:
                logger.warning(
                    "No question and answer was extracted from current sample!"
                )

        return output_samples
