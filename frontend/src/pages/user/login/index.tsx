import { LockOutlined, UserOutlined } from '@ant-design/icons';
import {
  LoginForm,
  ProFormCheckbox,
  ProFormText,
} from '@ant-design/pro-components';
import { FormattedMessage, Helmet, useIntl, useModel } from '@umijs/max';
import { Alert, App } from 'antd';
import { createStyles } from 'antd-style';
import gsap from 'gsap';
import React, {
  startTransition,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { login } from '@/services/ant-design-pro/api';
import Settings from '../../../../config/defaultSettings';

const useStyles = createStyles(() => ({
  container: {
    display: 'flex',
    height: '100dvh',
    overflow: 'hidden',
    background: '#fff',
  },
  // 左侧品牌区:深蓝渐变 + 装饰光晕,窄屏(<768px)隐藏,只留右侧表单
  left: {
    position: 'relative',
    flex: '1 1 55%',
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'space-between',
    padding: '48px',
    color: '#fff',
    background:
      'linear-gradient(135deg, #0b1b3a 0%, #15366e 55%, #1d4ed8 100%)',
    overflow: 'hidden',
    '@media (max-width: 768px)': { display: 'none' },
  },
  leftGlow: {
    position: 'absolute',
    top: '-20%',
    right: '-15%',
    width: 520,
    height: 520,
    borderRadius: '50%',
    background:
      'radial-gradient(circle, rgba(96,165,250,0.35) 0%, rgba(96,165,250,0) 70%)',
    filter: 'blur(20px)',
    pointerEvents: 'none',
  },
  brandTop: {
    position: 'relative',
    zIndex: 1,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    fontSize: 18,
    fontWeight: 600,
    '& img': { height: 30 },
  },
  brandHero: {
    position: 'relative',
    zIndex: 1,
    '& h1': {
      fontSize: 40,
      lineHeight: 1.2,
      margin: '0 0 16px',
      fontWeight: 700,
      letterSpacing: '-0.02em',
    },
    '& p': {
      fontSize: 15,
      lineHeight: 1.7,
      opacity: 0.8,
      margin: 0,
      maxWidth: 360,
    },
  },
  brandFoot: { position: 'relative', zIndex: 1, fontSize: 13, opacity: 0.6 },
  // 右侧表单区:白底居中
  right: {
    flex: '1 1 45%',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '32px 24px',
    '@media (max-width: 768px)': { flex: '1 1 100%' },
  },
  formWrap: { width: '100%', maxWidth: 360 },
}));

const LoginMessage: React.FC<{
  content: string;
}> = ({ content }) => {
  return (
    <Alert
      style={{
        marginBottom: 24,
      }}
      title={content}
      type="error"
      showIcon
    />
  );
};

const Login: React.FC = () => {
  const [userLoginState, setUserLoginState] = useState<API.LoginResult>({});
  const [type] = useState<string>('account');
  const { initialState, setInitialState } = useModel('@@initialState');
  const { styles } = useStyles();
  const { message } = App.useApp();
  const intl = useIntl();
  const cardRef = useRef<HTMLDivElement>(null);

  // 入场动效:右侧表单淡入上移(GSAP);尊重 prefers-reduced-motion
  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      gsap.from(cardRef.current, {
        y: 24,
        opacity: 0,
        duration: 0.6,
        ease: 'power3.out',
      });
    }, cardRef);
    return () => ctx.revert();
  }, []);

  /**
   * Validate redirect URL to prevent open redirect attacks
   * Only allow same-origin relative paths starting with '/'
   */
  const getSafeRedirectUrl = (redirect: string | null): string => {
    if (!redirect?.startsWith('/')) return '/';

    // Block protocol-relative URLs (//example.com)
    if (redirect.startsWith('//')) return '/';

    try {
      const parsed = new URL(redirect, window.location.origin);
      // Only allow same-origin URLs
      if (parsed.origin !== window.location.origin) return '/';
      // Return the path with query and hash preserved
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    } catch {
      return '/';
    }
  };

  const fetchUserInfo = async () => {
    const userInfo = await initialState?.fetchUserInfo?.();
    if (userInfo) {
      startTransition(() => {
        setInitialState((s) => ({
          ...s,
          currentUser: userInfo,
        }));
      });
    }
  };

  const handleSubmit = async (values: API.LoginParams) => {
    try {
      // 登录
      const msg = await login({ ...values, type });
      if (msg.status === 'ok') {
        const defaultLoginSuccessMessage = intl.formatMessage({
          id: 'pages.login.success',
          defaultMessage: '登录成功！',
        });
        message.success(defaultLoginSuccessMessage);
        await fetchUserInfo();
        const urlParams = new URL(window.location.href).searchParams;
        const redirectUrl = getSafeRedirectUrl(urlParams.get('redirect'));
        window.location.href = redirectUrl;
        return;
      }
      console.log(msg);
      // 如果失败去设置用户错误信息
      setUserLoginState(msg);
    } catch (error) {
      const defaultLoginFailureMessage = intl.formatMessage({
        id: 'pages.login.failure',
        defaultMessage: '登录失败，请重试！',
      });
      console.log(error);
      message.error(defaultLoginFailureMessage);
    }
  };
  const { status, type: loginType } = userLoginState;

  return (
    <div className={styles.container}>
      <Helmet>
        <title>
          {intl.formatMessage({
            id: 'menu.login',
            defaultMessage: '登录页',
          })}
          {Settings.title && ` - ${Settings.title}`}
        </title>
      </Helmet>
      <div className={styles.left}>
        <div className={styles.leftGlow} />
        <div className={styles.brandTop}>
          <img alt="logo" src="/logo.svg" />
          <span>AI 数据平台</span>
        </div>
        <div className={styles.brandHero}>
          <h1>从原始数据到训练就绪</h1>
          <p>端到端 LLM 数据处理、清洗与数据集管理平台。</p>
        </div>
        <div className={styles.brandFoot}>© 2026 AI 数据平台</div>
      </div>
      <div className={styles.right}>
        <div ref={cardRef} className={styles.formWrap}>
          <LoginForm
            contentStyle={{
              minWidth: 280,
              maxWidth: '75vw',
            }}
            title="欢迎回来"
            subTitle="登录您的账户继续"
            initialValues={{
              autoLogin: true,
            }}
            onFinish={async (values) => {
              await handleSubmit(values as API.LoginParams);
            }}
          >
            {status === 'error' && loginType === 'account' && (
              <LoginMessage
                content={intl.formatMessage({
                  id: 'pages.login.accountLogin.errorMessage',
                  defaultMessage: '账户或密码错误(admin/ant.design)',
                })}
              />
            )}
            {type === 'account' && (
              <>
                <ProFormText
                  name="username"
                  fieldProps={{
                    size: 'large',
                    prefix: <UserOutlined />,
                  }}
                  placeholder={intl.formatMessage({
                    id: 'pages.login.username.placeholder',
                    defaultMessage: '用户名: admin or user',
                  })}
                  rules={[
                    {
                      required: true,
                      message: (
                        <FormattedMessage
                          id="pages.login.username.required"
                          defaultMessage="请输入用户名!"
                        />
                      ),
                    },
                  ]}
                />
                <ProFormText.Password
                  name="password"
                  fieldProps={{
                    size: 'large',
                    prefix: <LockOutlined />,
                  }}
                  placeholder={intl.formatMessage({
                    id: 'pages.login.password.placeholder',
                    defaultMessage: '密码: ant.design',
                  })}
                  rules={[
                    {
                      required: true,
                      message: (
                        <FormattedMessage
                          id="pages.login.password.required"
                          defaultMessage="请输入密码！"
                        />
                      ),
                    },
                  ]}
                />
              </>
            )}

            <div
              style={{
                marginBottom: 24,
              }}
            >
              <ProFormCheckbox noStyle name="autoLogin">
                <FormattedMessage
                  id="pages.login.rememberMe"
                  defaultMessage="自动登录"
                />
              </ProFormCheckbox>
              <a
                href="#"
                style={{
                  float: 'right',
                }}
              >
                <FormattedMessage
                  id="pages.login.forgotPassword"
                  defaultMessage="忘记密码"
                />
              </a>
            </div>
          </LoginForm>
        </div>
      </div>
    </div>
  );
};

export default Login;
