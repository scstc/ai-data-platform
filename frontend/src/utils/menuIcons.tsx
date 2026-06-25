import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BlockOutlined,
  ClusterOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileOutlined,
  FundOutlined,
  RobotOutlined,
  SafetyOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';

/**
 * 菜单图标字符串 → antd 图标组件 的映射。
 *
 * 静态 routes.ts 里的字符串图标由 Umi 构建期解析;但 `menuDataRender` 在运行时
 * 注入的菜单不走构建期,故需此映射表把后端下发的 icon 字符串显式解析成组件。
 */
const ICON_MAP: Record<string, ReactNode> = {
  api: <ApiOutlined />,
  appstore: <AppstoreOutlined />,
  audit: <AuditOutlined />,
  block: <BlockOutlined />,
  cluster: <ClusterOutlined />,
  dashboard: <DashboardOutlined />,
  database: <DatabaseOutlined />,
  file: <FileOutlined />,
  fund: <FundOutlined />,
  robot: <RobotOutlined />,
  safety: <SafetyOutlined />,
  setting: <SettingOutlined />,
  team: <TeamOutlined />,
  user: <UserOutlined />,
};

export const resolveIcon = (name?: string): ReactNode | undefined =>
  name ? ICON_MAP[name] : undefined;
