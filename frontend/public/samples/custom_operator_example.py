"""自定义算子示例:把 text 字段里的指定水印文本替换成空字符串。

上传规则(平台静态校验,见 backend/app/services/custom_operators.py):
1. 一个文件只能定义一个算子类,且必须继承下列基类之一:
   Mapper(编辑) / Filter(过滤) / Deduplicator(去重) / Selector(选择)
2. 算子类必须带 @OPERATORS.register_module("算子名") 装饰器——算子名就是你在
   算子工厂里搜索/编排时用到的 name,建议用 snake_case 并以类型后缀结尾
   (如 xxx_mapper / xxx_filter)。
3. 不要 import os / sys / subprocess / socket 等系统级模块,也不要用
   eval/exec/open 等——静态校验会拒绝。
4. __init__ 里能声明的参数(如下面的 watermark)会显示在上传表单的「参数」列表里,
   跑任务时可在编排页面填不同的值覆盖默认值。
"""

from data_juicer.ops.base_op import Mapper, OPERATORS


@OPERATORS.register_module("remove_watermark_text_mapper")
class RemoveWatermarkTextMapper(Mapper):
    """去除文本中的指定水印字符串(演示:自定义 Mapper 算子)。"""

    def __init__(self, watermark: str = "[SAMPLE WATERMARK]", *args, **kwargs):
        """
        :param watermark: 待移除的水印文本,默认 "[SAMPLE WATERMARK]"。
        """
        super().__init__(*args, **kwargs)
        self.watermark = watermark

    def process_single(self, sample):
        if self.watermark and self.text_key in sample:
            sample[self.text_key] = sample[self.text_key].replace(
                self.watermark, ""
            )
        return sample
