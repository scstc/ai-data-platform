"""文档抽取分段/清洗逻辑单测(数据湖抽取:分段标识符 + 最大长度 + 重叠 + 预处理)。

为什么测:数据湖抽取 word/pdf 时用户可配分段与清洗规则;默认配置必须与历史
段落切分行为完全一致(否则既有抽取结果漂移);ppt 契约是"一页一条 text"。
"""

from app.services.landing import (
    DocSegmentOptions,
    _clean_doc_text,
    _segment_doc_text,
    _split_ppt_slides,
    _strip_html_tags,
    normalize_to_records,
)


class TestSegmentDefault:
    def test_default_equals_legacy_paragraph_split(self):
        """默认配置 = 历史行为:按空行切段,不清洗不限长。"""
        text = "第一段内容\n\n第二段内容\n\n\n\n第三段"
        assert _segment_doc_text(text, DocSegmentOptions()) == [
            "第一段内容",
            "第二段内容",
            "第三段",
        ]

    def test_custom_separator_with_escape(self):
        """前端传字面量 \\n 转义,后端解码为真实换行。"""
        text = "A\nB\nC"
        opts = DocSegmentOptions(separator="\\n")
        assert _segment_doc_text(text, opts) == ["A", "B", "C"]

    def test_custom_literal_separator(self):
        opts = DocSegmentOptions(separator="###")
        assert _segment_doc_text("甲###乙###丙", opts) == ["甲", "乙", "丙"]


class TestMaxLengthOverlap:
    def test_long_segment_chunked_with_overlap(self):
        """超长段按 max_length 切窗,相邻窗重叠 overlap 个字符。"""
        text = "0123456789"
        opts = DocSegmentOptions(max_length=4, overlap=2)
        chunks = _segment_doc_text(text, opts)
        assert chunks == ["0123", "2345", "4567", "6789"]
        # 重叠语义:后一窗的头 2 字符 = 前一窗的尾 2 字符
        assert chunks[1][:2] == chunks[0][-2:]

    def test_short_segment_not_chunked(self):
        opts = DocSegmentOptions(max_length=100, overlap=10)
        assert _segment_doc_text("短段落", opts) == ["短段落"]

    def test_no_max_length_no_chunking(self):
        long_text = "x" * 5000
        assert _segment_doc_text(long_text, DocSegmentOptions()) == [long_text]


class TestCleanRules:
    def test_clean_whitespace_collapses_runs(self):
        """规则1:连续空格/换行/制表符 → 单个空格。"""
        opts = DocSegmentOptions(clean_whitespace=True)
        assert _clean_doc_text("a  b\t\tc\n\nd", opts) == "a b c d"

    def test_remove_urls_and_emails(self):
        """规则2:删除 URL 与邮箱。"""
        opts = DocSegmentOptions(remove_urls_emails=True)
        text = "联系 admin@example.com 或访问 https://example.com/a?b=1 了解"
        cleaned = _clean_doc_text(text, opts)
        assert "example.com" not in cleaned
        assert "@" not in cleaned
        assert "联系" in cleaned and "了解" in cleaned

    def test_rules_off_by_default(self):
        text = "a  b https://x.com"
        assert _clean_doc_text(text, DocSegmentOptions()) == text

    def test_clean_applied_per_segment_after_split(self):
        """清洗在切分之后:whitespace 收敛不破坏 \\n\\n 分段标识符。"""
        text = "第一段  多空格\n\n第二段\t制表"
        opts = DocSegmentOptions(clean_whitespace=True)
        assert _segment_doc_text(text, opts) == ["第一段 多空格", "第二段\t制表"]


class TestPptSlides:
    MD = (
        "<!-- Slide number: 1 -->\n# 封面标题\n\n"
        "<!-- Slide number: 2 -->\n- 要点一\n- 要点二\n\n"
        "<!-- Slide number: 3 -->\n\n\n"  # 空页应跳过
        "<!-- Slide number: 4 -->\n结束页"
    )

    def test_one_record_per_slide(self):
        slides = _split_ppt_slides(self.MD, DocSegmentOptions())
        assert slides == ["封面标题", "要点一\n要点二", "结束页"]

    def test_slide_ignores_max_length(self):
        """ppt 契约:一页一条,分段参数不拆页内文本。"""
        opts = DocSegmentOptions(max_length=2, overlap=1)
        slides = _split_ppt_slides(self.MD, opts)
        assert slides[0] == "封面标题"


class TestHtmlStrip:
    """html 契约:剔除全部标签 → 纯文本 → 走 doc 同款分段逻辑(不经 markitdown)。"""

    HTML = (
        "<html><head><title>忽略</title><style>p{color:red}</style></head>"
        "<body><script>var x=1;</script><!-- 注释 -->"
        "<h1>标题</h1><p>第一段 &amp; 实体</p><p>第二段<br>换行续</p></body></html>"
    )

    def test_all_tags_removed_and_entities_unescaped(self):
        text = _strip_html_tags(self.HTML)
        assert "<" not in text and ">" not in text
        assert "var x=1" not in text and "color:red" not in text and "忽略" not in text
        assert "第一段 & 实体" in text

    def test_block_tags_become_paragraph_boundaries(self):
        """块级闭合标签转空行 → 默认 \\n\\n 分段能按段切开。"""
        text = _strip_html_tags(self.HTML)
        segs = _segment_doc_text(text, DocSegmentOptions())
        assert segs == ["标题", "第一段 & 实体", "第二段\n换行续"]

    def test_normalize_html_uses_doc_segment_options(self):
        """html 走 normalize_to_records 时 doc_options 生效(与 word 同款逻辑)。"""
        opts = DocSegmentOptions(max_length=4, overlap=1, clean_whitespace=True)
        records = normalize_to_records(
            self.HTML.encode(), "html", doc_options=opts
        )
        texts = [r["text"] for r in records]
        assert all(len(t) <= 4 for t in texts)
        assert texts[0] == "标题"

    def test_gb18030_decoded(self):
        html = "<p>中文段落</p>".encode("gb18030")
        records = normalize_to_records(html, "html")
        assert records == [{"text": "中文段落"}]


class TestLegacyDocAntiwordGarbling:
    """antiword 只有西欧码表,遇中文等 CJK 字符会原样吐字面 "?" 且 returncode=0
    (伪装成功);必须按问号占比识别并拒绝,否则中文 .doc 会被静默存成乱码
    (生产复现:银行业务文本.doc 抽取后 text 字段变成 "REV00001 ????(1?) [??]")。
    """

    GARBLED = (
        "REV00001  ????(1?)  [??]\r\n"
        "??:???? | ??:?? | ??:2024-01-21 23:46:52\r\n"
        "????????(1?),???????????,????"
    )

    @staticmethod
    def _which_antiword_only(name: str) -> str | None:
        return "/usr/bin/antiword" if name == "antiword" else None

    def test_is_garbled_detects_high_question_mark_ratio(self):
        from app.services.landing import _is_garbled_antiword_output

        assert _is_garbled_antiword_output(self.GARBLED) is True

    def test_is_garbled_false_for_normal_english_text(self):
        from app.services.landing import _is_garbled_antiword_output

        normal = "Is this correct? What about that? A normal paragraph."
        assert _is_garbled_antiword_output(normal) is False

    def test_is_garbled_false_for_empty(self):
        from app.services.landing import _is_garbled_antiword_output

        assert _is_garbled_antiword_output("") is False

    def test_legacy_doc_rejects_garbled_antiword_and_raises_without_soffice(
        self, monkeypatch
    ):
        """antiword 吐乱码时不采信;soffice 也不可用 → 明确抛 ParseError,
        不能静默把乱码当成功结果返回。"""
        import pytest

        from app.services import landing

        monkeypatch.setattr("shutil.which", self._which_antiword_only)
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: type(
                "R", (), {"returncode": 0, "stdout": self.GARBLED.encode()}
            )(),
        )

        with pytest.raises(landing.ParseError):
            landing._convert_legacy_doc_to_text(b"fake doc bytes")

    def test_legacy_doc_accepts_clean_antiword_output(self, monkeypatch):
        """antiword 输出正常(问号占比低)时按原逻辑直接采信,不影响既有英文 .doc 行为。"""
        from app.services import landing

        clean = "Quarterly report. Revenue is up. Is this final? Yes."
        monkeypatch.setattr("shutil.which", self._which_antiword_only)
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: type(
                "R", (), {"returncode": 0, "stdout": clean.encode()}
            )(),
        )

        assert landing._convert_legacy_doc_to_text(b"fake doc bytes") == clean


class TestSchemaValidation:
    def test_overlap_must_be_less_than_max_length(self):
        import pytest
        from pydantic import ValidationError

        from app.schemas.data_lake import DocSegmentConfig

        with pytest.raises(ValidationError):
            DocSegmentConfig(max_length=100, overlap=100)

    def test_valid_config_roundtrip_camel(self):
        from app.schemas.data_lake import DocSegmentConfig

        cfg = DocSegmentConfig.model_validate(
            {
                "separator": "\\n\\n",
                "maxLength": 1024,
                "overlap": 50,
                "cleanWhitespace": True,
                "removeUrlsEmails": True,
            }
        )
        assert cfg.max_length == 1024
        assert cfg.clean_whitespace and cfg.remove_urls_emails
