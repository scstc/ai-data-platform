import { LinkOutlined } from '@ant-design/icons';
import type { Settings as LayoutSettings } from '@ant-design/pro-components';
import { SettingDrawer } from '@ant-design/pro-components';
import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history, Link } from '@umijs/max';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import React from 'react';

// Initialize dayjs plugins globally
dayjs.extend(relativeTime);

import {
  AvatarDropdown,
  ErrorBoundary,
  Footer,
  LangDropdown,
  OfflineBanner,
  THEME_STORAGE_KEY,
  ThemeSwitch,
} from '@/components';
import { currentUser as queryCurrentUser } from '@/services/ant-design-pro/api';
import { getRouters } from '@/services/system';
import { resolveIcon } from '@/utils/menuIcons';
import defaultSettings from '../config/defaultSettings';
import { errorConfig } from './requestErrorConfig';

const isDev = process.env.NODE_ENV === 'development';
const loginPath = '/user/login';

// 动态菜单项(后端 RouterNode → ProLayout MenuDataItem 的中间形态)
type DynamicMenuItem = {
  path: string;
  name: string;
  icon?: React.ReactNode;
  children?: DynamicMenuItem[];
};

/** 在默认设置上合并持久化的明暗主题（ThemeSwitch 写入 localStorage） */
const loadSettings = (): Partial<LayoutSettings> => {
  const navTheme = localStorage.getItem(THEME_STORAGE_KEY);
  return {
    ...(defaultSettings as Partial<LayoutSettings>),
    ...(navTheme === 'realDark' || navTheme === 'light' ? { navTheme } : {}),
  };
};

/**
 * @see https://umijs.org/docs/api/runtime-config#getinitialstate
 * */
export async function getInitialState(): Promise<{
  settings?: Partial<LayoutSettings>;
  currentUser?: API.CurrentUser;
  routers?: System.RouterNode[];
  loading?: boolean;
  fetchUserInfo?: () => Promise<API.CurrentUser | undefined>;
  settingDrawerOpen?: boolean;
}> {
  const fetchUserInfo = async () => {
    try {
      const msg = await queryCurrentUser({
        skipErrorHandler: true,
      });
      return msg.data;
    } catch (_error) {
      const { pathname, search, hash } = history.location;
      history.replace(
        `${loginPath}?redirect=${encodeURIComponent(pathname + search + hash)}`,
      );
    }
    return undefined;
  };
  // 动态菜单:拉当前用户可见菜单树(失败回退空,侧边栏为空但仍可按 URL 路由)
  const fetchRouters = async () => {
    try {
      const res = await getRouters({ skipErrorHandler: true });
      return res.data ?? [];
    } catch (_error) {
      return [];
    }
  };
  // 如果不是登录页面，执行
  const { location } = history;
  if (
    ![loginPath, '/user/register', '/user/register-result'].includes(
      location.pathname,
    )
  ) {
    const currentUser = await fetchUserInfo();
    const routers = currentUser ? await fetchRouters() : [];
    return {
      fetchUserInfo,
      currentUser,
      routers,
      settings: loadSettings(),
      settingDrawerOpen: false,
    };
  }
  return {
    fetchUserInfo,
    settings: defaultSettings as Partial<LayoutSettings>,
    settingDrawerOpen: false,
  };
}

// ProLayout 支持的api https://procomponents.ant.design/components/layout
export const layout: RunTimeLayoutConfig = ({
  initialState,
  setInitialState,
}) => {
  return {
    // 动态菜单:侧边栏由后端 getRouters 下发的菜单树驱动(按角色授权裁剪)。
    // routes.ts 仍提供路由;此处替换默认菜单数据为后端树。
    menuDataRender: () => {
      const toMenu = (nodes: System.RouterNode[]): DynamicMenuItem[] =>
        nodes
          .filter((n) => n.path)
          .map((n) => ({
            path: n.path as string,
            name: n.name,
            icon: resolveIcon(n.icon),
            children: n.children?.length ? toMenu(n.children) : undefined,
          }));
      return toMenu(initialState?.routers ?? []);
    },
    menuItemRender: (item, dom) => {
      if (item.path) {
        // 算子市场:置顶 + 琥珀色特殊标注,突出显示(图标与文字一并变色)
        const highlighted = item.path === '/operators';
        return (
          <Link to={item.path} prefetch>
            {highlighted ? (
              <span style={{ color: '#fa8c16', fontWeight: 600 }}>{dom}</span>
            ) : (
              dom
            )}
          </Link>
        );
      }
      return dom;
    },
    actionsRender: () => {
      // `locale: false` opts out of the language switcher. ProLayout's own
      // `locale` prop is a locale string, so narrow to the boolean toggle here.
      const localeEnabled =
        (initialState?.settings as { locale?: boolean })?.locale !== false;
      return [
        <ThemeSwitch key="theme" />,
        localeEnabled && <LangDropdown key="lang" />,
      ].filter(Boolean);
    },
    avatarProps: {
      src: initialState?.currentUser?.avatar,
      title: 'ProUser',
      render: (_, avatarChildren) => (
        <AvatarDropdown>{avatarChildren}</AvatarDropdown>
      ),
    },
    // waterMarkProps: {
    //   content: initialState?.currentUser?.name,
    // },
    footerRender: () => <Footer />,
    onPageChange: () => {
      const { location } = history;
      // 如果没有登录，重定向到 login
      if (!initialState?.currentUser && location.pathname !== loginPath) {
        history.replace(
          `${loginPath}?redirect=${encodeURIComponent(location.pathname + location.search + location.hash)}`,
        );
      }
    },
    bgLayoutImgList: [
      {
        src: 'https://mdn.alipayobjects.com/yuyan_qk0oxh/afts/img/D2LWSqNny4sAAAAAAAAAAAAAFl94AQBr',
        left: 85,
        bottom: 100,
        height: '303px',
      },
      {
        src: 'https://mdn.alipayobjects.com/yuyan_qk0oxh/afts/img/C2TWRpJpiC0AAAAAAAAAAAAAFl94AQBr',
        bottom: -68,
        right: -45,
        height: '303px',
      },
      {
        src: 'https://mdn.alipayobjects.com/yuyan_qk0oxh/afts/img/F6vSTbj8KpYAAAAAAAAAAAAAFl94AQBr',
        bottom: 0,
        left: 0,
        width: '331px',
      },
    ],
    links: isDev
      ? [
          <Link key="openapi" to="/umi/plugin/openapi" target="_blank">
            <LinkOutlined />
            <span>OpenAPI 文档</span>
          </Link>,
        ]
      : [],
    // Replace ProLayout's default ErrorBoundary with our offline-aware version,
    // so chunk load errors show friendly messages instead of "Something went wrong."
    ErrorBoundary,
    menuHeaderRender: undefined,
    // 左侧菜单:默认全部展开,切换路由时不自动收起其它分组
    menu: {
      defaultOpenAll: true,
      autoClose: false,
    },
    // 自定义 403 页面
    // unAccessible: <div>unAccessible</div>,
    // 增加一个 loading 的状态
    childrenRender: (children) => {
      // if (initialState?.loading) return <PageLoading />;
      return (
        <>
          {children}
          <SettingDrawer
            disableUrlParams
            enableDarkTheme
            collapse={initialState?.settingDrawerOpen}
            onCollapseChange={(open) => {
              setInitialState((s) => ({
                ...s,
                settingDrawerOpen: open,
              }));
            }}
            settings={initialState?.settings}
            onSettingChange={(settings) => {
              setInitialState((s) => ({
                ...s,
                settings,
              }));
            }}
          />
        </>
      );
    },
    ...initialState?.settings,
  };
};

/**
 * @name request 配置，可以配置错误处理
 * 它基于 axios 提供了一套统一的网络请求和错误处理方案。
 * @doc https://umijs.org/docs/max/request#配置
 */
export const request: RequestConfig = {
  // 同源部署:dev 走 config/proxy.ts,生产走 nginx 反代(均为相对 /api),
  // 不再指向 Ant Design Pro 的 demo API(否则跨域被 CORS 拦截,登录失败)。
  baseURL: '',
  ...errorConfig,
};

export function rootContainer(container: React.ReactNode) {
  return (
    <>
      <OfflineBanner />
      <ErrorBoundary>{container}</ErrorBoundary>
    </>
  );
}
