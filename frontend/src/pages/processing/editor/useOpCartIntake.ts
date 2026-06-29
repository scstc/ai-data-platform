import { useModel } from '@umijs/max';
import { message } from 'antd';
import { useEffect, useRef } from 'react';
import { listOperatorCatalog } from '@/services/data-platform';

/**
 * 算子市场 → 任务编辑器的「购物车交接」hook。
 *
 * 市场展示全量算子(212),但各治理任务只认自己业务桶的算子白名单
 * (cleansing/distillation/make/augment;质量评估无桶,接受任意算子)。
 * 用户在市场选一批算子点「去新建 X 任务」跳到本编辑器后,本 hook:
 *   1. 读全局 opCart 的 steps;
 *   2. 按 ``bucket`` 过滤——属于该桶的算子接收(accepted),其余跳过(skipped);
 *      ``bucket`` 为空(质量评估)= 全部接收,不过滤;
 *   3. 把 accepted 灌入编辑器本地 steps(经 ``onIntake`` 回调);
 *   4. 清空 opCart(用完即弃,避免污染下一个任务);
 *   5. skipped 非空时 message.info 诚实告知「N 个算子不适用于本任务,已跳过」。
 *
 * 仅在挂载后执行一次(opCart 是一次性交接,不随后续 add/clear 反复触发)。
 * 编辑器原有的「空 opCart 直接进入」交互零影响:steps 为空时本 hook 不灌不提示。
 */
export function useOpCartIntake(
  bucket: string | undefined,
  taskLabel: string,
  onIntake: (steps: DataPlatform.PipelineStep[]) => void,
): void {
  const { steps, clear } = useModel('opCart');
  // 仅消费挂载那一刻的购物车快照,避免 steps 引用变化重复触发
  const consumed = useRef(false);

  useEffect(() => {
    if (consumed.current) return;
    consumed.current = true;
    if (!steps.length) return;

    const incoming = steps;
    const run = async () => {
      // 无桶(质量评估):全部接收,不发请求过滤
      if (!bucket) {
        onIntake(incoming);
        clear();
        return;
      }
      // 取该业务桶的算子白名单 name 集合,与购物车求交
      let allowed: Set<string>;
      try {
        const res = await listOperatorCatalog({ bucket, pageSize: 500 });
        allowed = new Set((res.data ?? []).map((o) => o.name));
      } catch {
        // 白名单拉取失败:不静默吞掉用户的选择,全量接收并交由提交期后端校验
        onIntake(incoming);
        clear();
        return;
      }
      const accepted = incoming.filter((s) => allowed.has(s.name));
      const skipped = incoming.length - accepted.length;
      onIntake(accepted);
      clear();
      if (skipped > 0) {
        message.info(
          accepted.length > 0
            ? `已带入 ${accepted.length} 个算子;另有 ${skipped} 个不适用于${taskLabel}任务,已跳过`
            : `所选 ${skipped} 个算子均不适用于${taskLabel}任务,已跳过(可在左侧算子库重新挑选)`,
        );
      }
    };
    run();
    // 挂载一次性消费:依赖留空,内部用 consumed ref 兜底 StrictMode 双调用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
