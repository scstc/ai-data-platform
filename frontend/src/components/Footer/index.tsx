import { createStyles } from 'antd-style';
import React from 'react';

const useStyles = createStyles(({ token, css }) => ({
  /* 固定在视口底部;pointer-events none 避免挡住底部内容的点击 */
  footer: css`
    position: fixed;
    inset-inline: 0;
    bottom: 0;
    padding: 8px 24px;
    text-align: center;
    color: ${token.colorTextDescription};
    font-size: ${token.fontSizeSM}px;
    line-height: ${token.lineHeight};
    background: transparent;
    pointer-events: none;
    z-index: 10;
  `,
}));

const Footer: React.FC = () => {
  const { styles } = useStyles();
  const year = new Date().getFullYear();

  return <div className={styles.footer}>AI Data Platform &copy; {year}</div>;
};

export default Footer;
