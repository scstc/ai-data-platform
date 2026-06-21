import { Link } from '@umijs/max';
import type { BreadcrumbProps } from 'antd';
import type { ReactNode } from 'react';

type Crumb = { title: ReactNode; path?: string };

/**
 * 生成 PageContainer 的 breadcrumb 配置:带 path 且非末级的项渲染为 SPA <Link>(可点击跳转),
 * 末级(当前页)或无 path 的项渲染为纯文本。
 *
 * 用于手动指定面包屑的页面(hideInMenu / 动态层级等)。自动生成的面包屑由布局默认 itemRender
 * 处理,本 helper 仅补齐手动 `breadcrumb={{ items }}` 各项不可点击的问题。
 */
export const buildBreadcrumb = (items: Crumb[]): BreadcrumbProps => ({
  items,
  itemRender: (route, _params, routes) =>
    route.path && routes[routes.length - 1] !== route ? (
      <Link to={route.path}>{route.title}</Link>
    ) : (
      <span>{route.title}</span>
    ),
});
