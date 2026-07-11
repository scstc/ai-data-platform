import type { ProLayoutProps } from '@ant-design/pro-components';

/**
 * @name
 */
const Settings: ProLayoutProps & {
  logo?: string;
} = {
  navTheme: 'light',
  colorPrimary: '#1677ff',
  layout: 'mix',
  contentWidth: 'Fluid',
  fixedHeader: false,
  fixSiderbar: true,
  colorWeak: false,
  title: 'AI 数据平台',
  logo: '/logo.png',
  iconfontUrl: '',
  token: {
    // 菜单：选中态背景色(ProLayout token 结构为 sider.*,原 siderMenu 为非法 key 不生效)
    sider: {
      colorBgMenuItemSelected: 'rgba(22, 119, 255, 0.08)',
      colorTextMenuSelected: '#1677ff',
    },
  },
};

export default Settings;
