"""data-juicer hot-patch for correlation_analysis.py StringDtype np.issubdtype.

背景:scstc/data-juicer dev 分支 PR#2 (commit d89d09c, "fix(analysis):
np.issubdtype 对 StringDtype 抛 TypeError 不再让 correlation 崩溃") 把这段
逻辑改成了 try/except 兜底。在 PR 合并到 scstc/data-juicer + 镜像 rebuild
之前,生产后端镜像里 DJ 这段还是旧代码,dj-analyze 在跑含字符串列的
dataset 时 correlation 阶段会崩。

本脚本:
1. 若 correlation_analysis.py 已含 PR#2 的 marker → ALREADY_PATCHED 跳过
2. 若 correlation_analysis.py 是 upstream 原始版本(needle 命中) → 应用 PR#2
3. 否则(scstc/data-juicer 后续又被改动,needle 不再匹配) → NEEDLE_NOT_FOUND
   不动文件,留给维护者手动处理

幂等,可重复执行。镜像 rebuild 时只需保留本脚本,后续 PR#2 合入后整段
RUN 可删除。
"""
import pathlib, sys

TARGET = pathlib.Path(
    "/opt/dj/.venv/lib/python3.12/site-packages/data_juicer/analysis/correlation_analysis.py"
)

# PR#2 后的 marker:只要命中就说明已合入,跳过
MARKER = "# 防御 np.issubdtype 对 pandas StringDtype(na_value=nan) 抛 TypeError"

# upstream 原始版本(对应 datajuicer/data-juicer main, 与 scstc/data-juicer
# 当前 dev 分支 PR 合并前一致)
NEEDLE = """        self.stats = pd.DataFrame(dataset[Fields.stats])
        # only keep the numeric columns
        for col_name in self.stats.columns:
            if np.issubdtype(self.stats[col_name].dtype, np.number):
                continue
            elif is_numeric_list_series(self.stats[col_name]):
                self.stats[col_name] = self.stats[col_name].apply(
                    lambda x: np.mean(x) if isinstance(x, list) and len(x) > 0 else 0
                )
            else:
                self.stats = self.stats.drop(col_name, axis=1)
"""

# PR#2 commit d89d09c 之后的形态
PATCHED = """        self.stats = pd.DataFrame(dataset[Fields.stats])
        # only keep the numeric columns
        # 防御 np.issubdtype 对 pandas StringDtype(na_value=nan) 抛 TypeError
        # (pandas 2.x StringArray 在 numpy 眼里不是合法 dtype,见 GH#52436)。
        # 抛错的列按非数值列走 drop 分支,与原语义一致。
        for col_name in list(self.stats.columns):
            try:
                is_numeric_dtype = np.issubdtype(
                    self.stats[col_name].dtype, np.number)
            except TypeError:
                is_numeric_dtype = False
            if is_numeric_dtype:
                continue
            elif is_numeric_list_series(self.stats[col_name]):
                self.stats[col_name] = self.stats[col_name].apply(
                    lambda x: np.mean(x) if isinstance(x, list) and len(x) > 0 else 0
                )
            else:
                self.stats = self.stats.drop(col_name, axis=1)
"""


def main() -> int:
    if not TARGET.exists():
        print(f"NOT_FOUND: {TARGET}", file=sys.stderr)
        return 1

    src = TARGET.read_text(encoding="utf-8")

    if MARKER in src:
        print("ALREADY_PATCHED")
        return 0

    if NEEDLE not in src:
        print(
            "NEEDLE_NOT_FOUND: upstream DJ 已偏离 PR#2 基线,无需/无法热补丁;"
            "请人工核对 correlation_analysis.py。"
        )
        return 2

    TARGET.write_text(src.replace(NEEDLE, PATCHED), encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    sys.exit(main())