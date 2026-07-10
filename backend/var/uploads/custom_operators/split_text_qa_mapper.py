"""拆分 text 字段 → query/response 字段,供 calibrate_qa_mapper 等 SFT 标准算子使用。

适用数据形态:源样本只有一个 text 字段,内容形如
    用户问题：农业银行手机银行单日转账限额多少？, 银行回答：农业银行手机银行默认...

标准格式: "用户问题：{问题}, 银行回答：{回答}"
按 query_pattern + response_pattern 切出 query 与 response 两个标准字段。

无需 LLM,纯文本正则切分,resource_class=cpu。
"""

import re

from data_juicer.ops.base_op import OPERATORS, Mapper

# 标准格式正则: "用户问题：{}, 银行回答：{}"
# 支持:
#   - 中英文冒号: ：或 :
#   - 中英文逗号分隔: ，或 ,
#   - 任意数量的空格
DEFAULT_QUERY_PATTERN = r"用户问题[:：]\s*"
DEFAULT_RESPONSE_PATTERN = r"[,，]\s*银行回答[:：]\s*"


@OPERATORS.register_module("split_text_qa_mapper")
class SplitTextQAMapper(Mapper):
    """把 ``text`` 字段切分到 ``query`` + ``response`` 两个 SFT 标准字段。

    参数:
        text_key: 输入字段名(默认 ``"text"``,由 Mapper 基类提供)
        query_key: 输出问题字段名(默认 ``"query"``,由 Mapper 基类提供)
        response_key: 输出回答字段名(默认 ``"response"``,由 Mapper 基类提供)
        query_pattern: 标定"问题"起始的正则(默认匹配 ``用户提问:`` / ``用户提问：``)
        response_pattern: 标定"回答"起始的正则(默认匹配 ``,用户回答:`` /
            ``，用户回答:`` 等)
        keep_text: 是否保留原 text 字段(True 默认)

    失败处理:若文本不匹配任一前缀,该样本原样返回(query/response 不写入),
    避免污染下游。
    """

    def __init__(
        self,
        query_pattern=DEFAULT_QUERY_PATTERN,
        response_pattern=DEFAULT_RESPONSE_PATTERN,
        keep_text=True,
        **kwargs,
    ):
        # batch_mode / num_proc=1 让 DJ 走单进程非 batched 模式(逐条调用 process_single)
        # 同时确保 input 是单条 dict,而不是 dict-of-lists。
        kwargs["batch_mode"] = False
        kwargs["num_proc"] = 1
        super().__init__(**kwargs)
        self._query_re = re.compile(query_pattern)
        self._response_re = re.compile(response_pattern)
        self._keep_text = keep_text

    def process_single(self, sample):
        """逐条切分文本为 query/response。

        sample 是单条 dict(text 字段是 str)。
        """
        text = sample.get(self.text_key)
        if not isinstance(text, str) or not text:
            return sample

        m_q = self._query_re.search(text)
        if m_q is None:
            return sample
        after_query = text[m_q.end():]

        m_a = self._response_re.search(after_query)
        if m_a is None:
            return sample

        query = after_query[: m_a.start()].strip()
        response = after_query[m_a.end():].strip()

        sample[self.query_key] = query
        sample[self.response_key] = response
        if not self._keep_text:
            sample.pop(self.text_key, None)
        return sample
