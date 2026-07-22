"""数据合成算子:把扩展 jsonl 文件的字段按行号或关联键合并进主数据集。

按算子目录(operators 表)留存的规格重写:
- key_field 非空:按两边共同的关联字段做键匹配(行序无关,推荐)
- key_field 留空:按行号一一对齐(强制单进程保证顺序)
- 拼接默认「主文本 + 分隔符 + 扩展文本」;template 非空时用模板
  (占位符 {main}/{aux},如 "{main}。{aux}。")自定义格式
- 找不到匹配行时默认保留原文(keep_unmatched=False 则直接报错,fail-loud)
"""

import json
from pathlib import Path

from data_juicer.ops.base_op import OPERATORS, Mapper


@OPERATORS.register_module("jsonl_field_merge_mapper")
class JsonlFieldMergeMapper(Mapper):
    """读取扩展 jsonl,把其中字段内容合并进主数据集每一行。"""

    def __init__(
        self,
        aux_path: str = "",
        key_field: str = "",
        aux_field: str = "",
        target_field: str = "",
        separator: str = "。",
        template: str = "",
        keep_unmatched: bool = True,
        *args,
        **kwargs,
    ):
        """
        :param aux_path: 扩展 jsonl 文件路径(必填,须任务执行环境内可读)。
        :param key_field: 两文件共有的关联字段;留空按行号对齐。
        :param aux_field: 取扩展文件的哪个字段;留空同 text 字段。
        :param target_field: 结果写入字段;留空覆盖 text 字段。
        :param separator: 拼接分隔符,默认「。」。
        :param template: 模板,占位符 {main}/{aux};非空时优先于 separator。
        :param keep_unmatched: 无匹配时保留原文(False 则报错)。
        """
        super().__init__(*args, **kwargs)
        if not aux_path:
            raise ValueError("jsonl_field_merge_mapper: aux_path 必填")
        self.aux_path = aux_path
        self.key_field = key_field
        self.aux_field = aux_field
        self.target_field = target_field
        self.separator = separator
        self.template = template
        self.keep_unmatched = keep_unmatched
        # 行号对齐依赖处理顺序,强制单进程
        if not key_field:
            self.num_proc = 1
        # 扩展文件懒加载(首次 process 时读,兼容多进程 worker 反序列化)
        self._aux_rows = None
        self._aux_by_key = None
        self._row_no = 0

    def _load_aux(self):
        if self._aux_rows is not None:
            return
        text = Path(self.aux_path).read_text(encoding="utf-8")
        self._aux_rows = [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
        if self.key_field:
            self._aux_by_key = {}
            for row in self._aux_rows:
                if self.key_field in row:
                    # 同键多行取首行(与"按键匹配到一行"语义一致)
                    self._aux_by_key.setdefault(str(row[self.key_field]), row)

    def _aux_value(self, row) -> str:
        field = self.aux_field or self.text_key
        v = row.get(field)
        return "" if v is None else str(v)

    def process_single(self, sample):
        self._load_aux()
        if self.key_field:
            key = sample.get(self.key_field)
            aux_row = (
                self._aux_by_key.get(str(key)) if key is not None else None
            )
        else:
            aux_row = (
                self._aux_rows[self._row_no]
                if self._row_no < len(self._aux_rows)
                else None
            )
            self._row_no += 1
        if aux_row is None:
            if self.keep_unmatched:
                return sample
            raise ValueError(
                "jsonl_field_merge_mapper: 主数据行在扩展文件中无匹配"
                f"(key_field={self.key_field or '按行号'})"
            )
        main_text = str(sample.get(self.text_key, ""))
        aux_text = self._aux_value(aux_row)
        if self.template:
            merged = self.template.format(main=main_text, aux=aux_text)
        else:
            merged = f"{main_text}{self.separator}{aux_text}"
        sample[self.target_field or self.text_key] = merged
        return sample
