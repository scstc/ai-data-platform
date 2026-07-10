import { MoonOutlined, SunOutlined } from '@ant-design/icons';
import { useModel } from '@umijs/max';
import { Button } from 'antd';
import { createStyles } from 'antd-style';
import React from 'react';

const useStyles = createStyles(({ token, css }) => ({
  action: css`
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    height: 36px !important;
    min-width: 36px;
    padding-inline: 8px !important;
    padding-block: 0 !important;
    border-radius: ${token.borderRadius}px !important;
  `,
}));

/** 明暗主题持久化键，app.tsx 启动时读取恢复 */
export const THEME_STORAGE_KEY = 'adp-navTheme';

export const ThemeSwitch: React.FC = () => {
  const { styles } = useStyles();
  const { initialState, setInitialState } = useModel('@@initialState');
  const isDark = initialState?.settings?.navTheme === 'realDark';

  const toggle = () => {
    const navTheme = isDark ? 'light' : 'realDark';
    localStorage.setItem(THEME_STORAGE_KEY, navTheme);
    setInitialState((s) => ({
      ...s,
      settings: { ...s?.settings, navTheme },
    }));
  };

  return (
    <Button
      type="text"
      className={styles.action}
      aria-label={isDark ? '切换为亮色主题' : '切换为暗色主题'}
      onClick={toggle}
    >
      {isDark ? <SunOutlined /> : <MoonOutlined />}
    </Button>
  );
};
