import { useCallback, useState } from 'react';

/**
 * 算子市场 → 流水线编辑页的"待编排步骤"购物车。
 * Umi Max 全局 model:市场页加入算子(空参数步骤),编辑页带出并继续增删/排序/配参。
 */
export default function useOpCart() {
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);

  // 允许同一算子重复加入:流水线是有序步骤列表(data-juicer 线性算子语义),
  // 同一算子用不同参数先后跑两次是合法编排,不能按 name 去重降级成集合。
  const add = useCallback(
    (name: string) =>
      setSteps((prev) => [...prev, { name, params: {} }]),
    [],
  );
  const remove = useCallback(
    (idx: number) => setSteps((prev) => prev.filter((_, i) => i !== idx)),
    [],
  );
  const reorder = useCallback(
    (from: number, to: number) =>
      setSteps((prev) => {
        const next = [...prev];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      }),
    [],
  );
  const updateParams = useCallback(
    (idx: number, params: Record<string, unknown>) =>
      setSteps((prev) =>
        prev.map((s, i) => (i === idx ? { ...s, params } : s)),
      ),
    [],
  );
  const replaceAll = useCallback(
    (next: DataPlatform.PipelineStep[]) => setSteps(next),
    [],
  );
  const clear = useCallback(() => setSteps([]), []);

  return { steps, add, remove, reorder, updateParams, replaceAll, clear };
}
