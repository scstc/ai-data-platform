import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as dpApi from '@/services/data-platform';
import { useOpCartIntake } from './useOpCartIntake';

// opCart：可控的购物车 steps + clear 间谍
const cart = { steps: [] as DataPlatform.PipelineStep[], clear: vi.fn() };
vi.mock('@umijs/max', () => ({
  useModel: () => cart,
}));

vi.mock('@/services/data-platform', () => ({
  listOperatorCatalog: vi.fn(),
}));

const messageInfo = vi.fn();
vi.mock('antd', () => ({
  message: { info: (...a: unknown[]) => messageInfo(...a) },
}));

const step = (name: string): DataPlatform.PipelineStep => ({
  name,
  params: {},
});

describe('useOpCartIntake', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cart.steps = [];
    cart.clear = vi.fn();
  });

  it('空购物车：不灌入、不提示、不清空（保护「直接进入编辑器」交互）', async () => {
    const onIntake = vi.fn();
    renderHook(() => useOpCartIntake('make', '合成', onIntake));
    // 给微任务一轮机会
    await Promise.resolve();
    expect(onIntake).not.toHaveBeenCalled();
    expect(cart.clear).not.toHaveBeenCalled();
    expect(dpApi.listOperatorCatalog).not.toHaveBeenCalled();
  });

  it('无桶（质量评估）：全部接收，不发目录请求，消费后清空', async () => {
    cart.steps = [step('a'), step('b')];
    const onIntake = vi.fn();
    renderHook(() => useOpCartIntake(undefined, '质量评估', onIntake));
    await waitFor(() => expect(onIntake).toHaveBeenCalled());
    expect(onIntake).toHaveBeenCalledWith([step('a'), step('b')]);
    expect(dpApi.listOperatorCatalog).not.toHaveBeenCalled();
    expect(cart.clear).toHaveBeenCalledTimes(1);
    expect(messageInfo).not.toHaveBeenCalled();
  });

  it('有桶：只带入桶内算子，桶外跳过并提示，且清空购物车', async () => {
    cart.steps = [step('in_bucket'), step('out_bucket'), step('also_in')];
    vi.mocked(dpApi.listOperatorCatalog).mockResolvedValue({
      data: [
        { name: 'in_bucket' },
        { name: 'also_in' },
      ] as DataPlatform.CatalogOperator[],
      total: 2,
      success: true,
    });
    const onIntake = vi.fn();
    renderHook(() => useOpCartIntake('make', '合成', onIntake));
    await waitFor(() => expect(onIntake).toHaveBeenCalled());
    // 只保留桶内的两个，桶外的 out_bucket 被过滤
    expect(onIntake).toHaveBeenCalledWith([step('in_bucket'), step('also_in')]);
    expect(cart.clear).toHaveBeenCalledTimes(1);
    // 跳过 1 个 → 提示
    expect(messageInfo).toHaveBeenCalledTimes(1);
    expect(messageInfo.mock.calls[0][0]).toContain('合成');
  });

  it('全部桶外：accepted 为空，提示「均不适用」', async () => {
    cart.steps = [step('x'), step('y')];
    vi.mocked(dpApi.listOperatorCatalog).mockResolvedValue({
      data: [{ name: 'z' }] as DataPlatform.CatalogOperator[],
      total: 1,
      success: true,
    });
    const onIntake = vi.fn();
    renderHook(() => useOpCartIntake('augment', '增强', onIntake));
    await waitFor(() => expect(onIntake).toHaveBeenCalled());
    expect(onIntake).toHaveBeenCalledWith([]);
    expect(messageInfo.mock.calls[0][0]).toContain('均不适用');
  });

  it('目录拉取失败：不静默丢弃用户选择，全量接收交由提交期校验', async () => {
    cart.steps = [step('a'), step('b')];
    vi.mocked(dpApi.listOperatorCatalog).mockRejectedValue(new Error('boom'));
    const onIntake = vi.fn();
    renderHook(() => useOpCartIntake('distillation', '蒸馏', onIntake));
    await waitFor(() => expect(onIntake).toHaveBeenCalled());
    expect(onIntake).toHaveBeenCalledWith([step('a'), step('b')]);
    expect(cart.clear).toHaveBeenCalledTimes(1);
  });
});
