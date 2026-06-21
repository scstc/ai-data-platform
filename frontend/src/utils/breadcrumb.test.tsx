import { render } from '@testing-library/react';
import type { ReactElement, ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

// Link 渲染成普通 <a>,便于断言"可点击跳转"
vi.mock('@umijs/max', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));

import { buildBreadcrumb } from './breadcrumb';

const renderCrumb = (
  bc: ReturnType<typeof buildBreadcrumb>,
  items: { title: string; path?: string }[],
  index: number,
) => {
  // biome-ignore lint/style/noNonNullAssertion: itemRender 由 buildBreadcrumb 必定提供
  const node = bc.itemRender!(
    items[index] as never,
    {} as never,
    items as never,
    [],
  );
  return render(node as ReactElement);
};

describe('buildBreadcrumb', () => {
  it('把带 path 的非末级项渲染为可点击链接,末级渲染为纯文本', () => {
    const items = [
      { title: '数据接入', path: '/ingest/datasources' },
      { title: '本地上传', path: '/ingest/local-upload' },
      { title: '单一数据' },
    ];
    const bc = buildBreadcrumb(items);
    expect(bc.items).toBe(items);

    const a0 = renderCrumb(bc, items, 0).container.querySelector('a');
    expect(a0?.getAttribute('href')).toBe('/ingest/datasources');
    expect(a0?.textContent).toBe('数据接入');

    const a1 = renderCrumb(bc, items, 1).container.querySelector('a');
    expect(a1?.getAttribute('href')).toBe('/ingest/local-upload');

    const last = renderCrumb(bc, items, 2).container;
    expect(last.querySelector('a')).toBeNull();
    expect(last.textContent).toBe('单一数据');
  });

  it('末级项即使带 path 也是纯文本(当前页不应可跳转)', () => {
    const items = [
      { title: '父级', path: '/p' },
      { title: '当前页', path: '/p/current' },
    ];
    const bc = buildBreadcrumb(items);
    const last = renderCrumb(bc, items, 1).container;
    expect(last.querySelector('a')).toBeNull();
    expect(last.textContent).toBe('当前页');
  });

  it('无 path 的中间项渲染为纯文本(无可点击暗示)', () => {
    const items = [{ title: '无链接项' }, { title: '当前页' }];
    const bc = buildBreadcrumb(items);
    const mid = renderCrumb(bc, items, 0).container;
    expect(mid.querySelector('a')).toBeNull();
    expect(mid.textContent).toBe('无链接项');
  });
});
