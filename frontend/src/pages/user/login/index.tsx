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
    // 博云 logo 为深蓝色，深色背景下需白色底衬保证可读
    '& img': {
      height: 30,
      padding: '3px 7px',
      background: '#fff',
      borderRadius: 6,
    },
  },
  brandHero: {
    position: 'relative',
    zIndex: 1,
    '& h1': {
      fontSize: 30,
      lineHeight: 1.25,
      margin: '0 0 12px',
      fontWeight: 700,
      letterSpacing: '-0.02em',
    },
    '& p': {
      fontSize: 13,
      lineHeight: 1.7,
      opacity: 0.75,
      margin: 0,
      maxWidth: 320,
    },
  },
  // 数据工程流水线动态 SVG:嵌在 hero 文案上方,放大展示
  // 宽度按左栏百分比走(左栏有 48px 内边距,取容器 84% ≈ 面板宽 ~80%),随面板缩放
  flow: {
    position: 'relative',
    zIndex: 1,
    display: 'block',
    width: '85%',
    maxWidth: 1440,
    marginBottom: 32,
    overflow: 'visible',
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
  formWrap: {
    width: '100%',
    maxWidth: 360,
    // ProComponents 默认给 .ant-pro-form-login-container 加了左右各 32px padding,
    // 但其子元素左缘被拉到 0,右侧因此凭空溢出 32px,产生一条横向滚动条;清零横向 padding 即消除。
    '& .ant-pro-form-login-container': { paddingInline: 0 },
  },
  formLogo: {
    display: 'block',
    height: 72,
    margin: '0 auto 8px',
  },
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
  const rootRef = useRef<HTMLDivElement>(null);

  // 入场动效(GSAP):光晕→品牌文案→表单卡片→字段逐项,一条时间线编排;
  // 光晕另起一条持续漂浮的氛围动画。尊重 prefers-reduced-motion。
  useLayoutEffect(() => {
    const ctx = gsap.context((self) => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const q = self.selector as (s: string) => Element[];

      gsap
        .timeline({ defaults: { ease: 'power3.out' } })
        .from(q('[data-anim="glow"]'), { scale: 0.6, opacity: 0, duration: 1 })
        .from(
          q('[data-anim="brand-top"]'),
          { y: -20, opacity: 0, duration: 0.5 },
          '-=0.7',
        )
        .from(
          q('[data-anim="hero-svg"]'),
          { y: 20, opacity: 0, duration: 0.6 },
          '-=0.3',
        )
        .from(
          q('[data-anim="hero-title"]'),
          { y: 30, opacity: 0, duration: 0.6 },
          '-=0.3',
        )
        .from(
          q('[data-anim="hero-sub"]'),
          { y: 20, opacity: 0, duration: 0.5 },
          '-=0.4',
        )
        .from(q('[data-anim="foot"]'), { opacity: 0, duration: 0.4 }, '-=0.2')
        .from(
          q('[data-anim="card"]'),
          { y: 24, opacity: 0, duration: 0.6 },
          '-=0.6',
        )
        .from(
          q('[data-anim="card"] .ant-form-item'),
          { y: 16, opacity: 0, duration: 0.4, stagger: 0.08 },
          '-=0.3',
        );

      // 光晕氛围漂浮(入场后接管,只动位移不动 scale,避免与入场冲突)
      gsap.to(q('[data-anim="glow"]'), {
        x: -24,
        y: 32,
        duration: 6,
        ease: 'sine.inOut',
        repeat: -1,
        yoyo: true,
        delay: 1.1,
      });

      // 数据工程流水线循环动效
      // 1) 所有虚线(传送带分段 / 算子喂入 / 运维轨道)流动
      gsap.to(q('[data-flow="line"]'), {
        strokeDashoffset: -20,
        duration: 1,
        ease: 'none',
        repeat: -1,
      });
      // 2) 运维监控扫描点左右巡检
      gsap.to(q('[data-flow="scan"]'), {
        attr: { cx: 452 },
        duration: 2.6,
        ease: 'sine.inOut',
        repeat: -1,
        yoyo: true,
      });
      // 3) 治理 hub 算子逐个明灭(并行处理)
      gsap.fromTo(
        q('[data-flow="op"]'),
        { opacity: 0.3 },
        {
          opacity: 1,
          duration: 0.7,
          ease: 'sine.inOut',
          stagger: { each: 0.12, repeat: -1, yoyo: true },
        },
      );
      // 4) 数据评估仪表盘进度环来回填充
      gsap.to(q('[data-flow="gauge"]'), {
        strokeDashoffset: 60,
        duration: 1.8,
        ease: 'sine.inOut',
        repeat: -1,
        yoyo: true,
      });
      // 5) 智能助手 chip 轻脉冲
      gsap.to(q('[data-flow="assistant"]'), {
        opacity: 0.5,
        duration: 1.4,
        ease: 'sine.inOut',
        repeat: -1,
        yoyo: true,
      });
      // 6) 数据粒子沿主传送带 60→396 流动,首尾淡入淡出,五颗错峰
      q('[data-flow="particle"]').forEach((p, i) => {
        gsap.to(p, {
          ease: 'none',
          repeat: -1,
          delay: i * 0.5,
          keyframes: [
            { attr: { cx: 60 }, opacity: 0, duration: 0.01 },
            { opacity: 1, duration: 0.25 },
            { attr: { cx: 396 }, duration: 2.2 },
            { opacity: 0, duration: 0.25 },
          ],
        });
      });
      // 7) 训练就绪网格逐点明灭,表现"数据填充"
      gsap.fromTo(
        q('[data-flow="grid"] circle'),
        { opacity: 0.25 },
        {
          opacity: 1,
          duration: 1,
          ease: 'sine.inOut',
          stagger: 0.12,
          repeat: -1,
          yoyo: true,
        },
      );
    }, rootRef);
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
        // 标记本次为「刚登录」,重载后由 ExpiryReminder 检查一次到期数据集并弹窗
        sessionStorage.setItem('adp_expiry_check_pending', '1');
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
    <div ref={rootRef} className={styles.container}>
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
        <div data-anim="glow" className={styles.leftGlow} />
        <div data-anim="brand-top" className={styles.brandTop}>
          <img alt="logo" src="/logo.png" />
          <span>AI 数据平台</span>
        </div>
        <div className={styles.brandHero}>
          {/* 数据工程流水线:映射平台菜单 —— 算子工厂 → 数据接入 → 数据治理 → 数据评估 → 数据集 → 训练就绪,
              横切层为运维监控(任务/血缘/审计)与智能助手(GSAP 驱动) */}
          <svg
            data-anim="hero-svg"
            className={styles.flow}
            viewBox="0 0 480 158"
            fill="none"
            role="img"
            aria-label="数据平台流程:数据接入经数据治理(内容安全/清洗/加工/蒸馏/合成/增强/标注)、数据评估、入数据集仓库,产出训练就绪数据集;运维监控与智能助手贯穿其中"
          >
            <title>从数据接入到训练就绪的数据工程流水线</title>

            {/* ── 运维监控:顶部贯穿的监控轨道 + 扫描点 ── */}
            <text x="20" y="20" fontSize="9" fill="rgba(255,255,255,0.5)">
              运维监控
            </text>
            <path
              data-flow="line"
              d="M86 16 H452"
              stroke="rgba(96,165,250,0.25)"
              strokeWidth="1"
              strokeDasharray="2 6"
            />
            <circle cx="150" cy="16" r="2.2" fill="rgba(96,165,250,0.55)" />
            <circle cx="264" cy="16" r="2.2" fill="rgba(96,165,250,0.55)" />
            <circle cx="380" cy="16" r="2.2" fill="rgba(96,165,250,0.55)" />
            <circle data-flow="scan" cx="86" cy="16" r="3" fill="#93c5fd" />

            {/* ── 智能助手:右上浮动机器人 chip ── */}
            <g data-flow="assistant">
              <rect
                x="398"
                y="28"
                width="64"
                height="18"
                rx="6"
                fill="rgba(96,165,250,0.1)"
                stroke="rgba(96,165,250,0.45)"
                strokeWidth="0.8"
              />
              <circle cx="408" cy="37" r="1.6" fill="#93c5fd" />
              <circle cx="414" cy="37" r="1.6" fill="#93c5fd" />
              <text
                x="440"
                y="40"
                textAnchor="middle"
                fontSize="7.5"
                fill="rgba(255,255,255,0.7)"
              >
                智能助手
              </text>
            </g>

            {/* ── 连接线(主传送带分段,虚线流动) ── */}
            <path
              data-flow="line"
              d="M58 96 H112"
              stroke="rgba(96,165,250,0.45)"
              strokeWidth="1.4"
              strokeDasharray="4 6"
            />
            <path
              data-flow="line"
              d="M220 96 H249"
              stroke="rgba(96,165,250,0.45)"
              strokeWidth="1.4"
              strokeDasharray="4 6"
            />
            <path
              data-flow="line"
              d="M283 96 H314"
              stroke="rgba(96,165,250,0.45)"
              strokeWidth="1.4"
              strokeDasharray="4 6"
            />
            <path
              data-flow="line"
              d="M372 96 H400"
              stroke="rgba(96,165,250,0.45)"
              strokeWidth="1.4"
              strokeDasharray="4 6"
            />

            {/* ── ① 数据接入:三类异构数据源 chip ── */}
            <g>
              <rect
                x="14"
                y="72"
                width="42"
                height="14"
                rx="4"
                fill="rgba(251,191,36,0.12)"
                stroke="rgba(251,191,36,0.5)"
                strokeWidth="0.8"
              />
              <circle cx="23" cy="79" r="2.4" fill="#fbbf24" />
              <rect
                x="14"
                y="90"
                width="42"
                height="14"
                rx="4"
                fill="rgba(52,211,153,0.12)"
                stroke="rgba(52,211,153,0.5)"
                strokeWidth="0.8"
              />
              <circle cx="23" cy="97" r="2.4" fill="#34d399" />
              <rect
                x="14"
                y="108"
                width="42"
                height="14"
                rx="4"
                fill="rgba(167,139,250,0.12)"
                stroke="rgba(167,139,250,0.5)"
                strokeWidth="0.8"
              />
              <circle cx="23" cy="115" r="2.4" fill="#a78bfa" />
            </g>
            <text
              x="35"
              y="142"
              textAnchor="middle"
              fontSize="9"
              fill="rgba(255,255,255,0.55)"
            >
              数据接入
            </text>

            {/* ── 算子工厂:从上方喂入治理 hub ── */}
            <rect
              x="140"
              y="40"
              width="52"
              height="16"
              rx="5"
              fill="rgba(96,165,250,0.1)"
              stroke="rgba(96,165,250,0.5)"
              strokeWidth="0.8"
            />
            <text
              x="166"
              y="51"
              textAnchor="middle"
              fontSize="7.5"
              fill="rgba(255,255,255,0.7)"
            >
              算子工厂
            </text>
            <path
              data-flow="line"
              d="M166 56 V66"
              stroke="rgba(96,165,250,0.45)"
              strokeWidth="1.2"
              strokeDasharray="3 5"
            />

            {/* ── ② 数据治理 hub:7+ 算子并行(内容安全/清洗/加工/蒸馏/合成/增强/标注) ── */}
            <rect
              x="112"
              y="66"
              width="108"
              height="60"
              rx="10"
              fill="rgba(255,255,255,0.04)"
              stroke="rgba(96,165,250,0.4)"
              strokeWidth="1"
            />
            <g data-flow="op-group">
              <circle data-flow="op" cx="132" cy="86" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="154" cy="86" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="176" cy="86" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="198" cy="86" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="132" cy="106" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="154" cy="106" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="176" cy="106" r="3.6" fill="#60a5fa" />
              <circle data-flow="op" cx="198" cy="106" r="3.6" fill="#60a5fa" />
            </g>
            <text
              x="166"
              y="142"
              textAnchor="middle"
              fontSize="9"
              fill="rgba(255,255,255,0.55)"
            >
              数据治理
            </text>

            {/* ── ③ 数据评估:质量评分仪表盘 ── */}
            <circle
              cx="266"
              cy="96"
              r="17"
              fill="rgba(56,189,248,0.06)"
              stroke="rgba(56,189,248,0.4)"
              strokeWidth="1"
            />
            <circle
              data-flow="gauge"
              cx="266"
              cy="96"
              r="12"
              fill="none"
              stroke="#38bdf8"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeDasharray="75.4"
              strokeDashoffset="22"
              transform="rotate(-90 266 96)"
            />
            <path
              d="M260 96 l4 4 l8 -9"
              stroke="#38bdf8"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <text
              x="266"
              y="142"
              textAnchor="middle"
              fontSize="9"
              fill="rgba(255,255,255,0.55)"
            >
              数据评估
            </text>

            {/* ── ④ 数据集仓库:版本化堆叠 ── */}
            <rect
              x="314"
              y="82"
              width="46"
              height="14"
              rx="3"
              fill="rgba(56,189,248,0.08)"
              stroke="rgba(56,189,248,0.4)"
              strokeWidth="0.8"
            />
            <rect
              x="319"
              y="92"
              width="46"
              height="14"
              rx="3"
              fill="rgba(56,189,248,0.1)"
              stroke="rgba(56,189,248,0.45)"
              strokeWidth="0.8"
            />
            <rect
              x="324"
              y="102"
              width="46"
              height="14"
              rx="3"
              fill="rgba(56,189,248,0.14)"
              stroke="rgba(56,189,248,0.5)"
              strokeWidth="0.8"
            />
            <text
              x="345"
              y="142"
              textAnchor="middle"
              fontSize="9"
              fill="rgba(255,255,255,0.55)"
            >
              数据集
            </text>

            {/* ── ⑤ 训练就绪:整齐对齐网格 ── */}
            <g data-flow="grid">
              <circle cx="404" cy="83" r="3.4" fill="#38bdf8" />
              <circle cx="420" cy="83" r="3.4" fill="#38bdf8" />
              <circle cx="436" cy="83" r="3.4" fill="#38bdf8" />
              <circle cx="404" cy="96" r="3.4" fill="#38bdf8" />
              <circle cx="420" cy="96" r="3.4" fill="#38bdf8" />
              <circle cx="436" cy="96" r="3.4" fill="#38bdf8" />
              <circle cx="404" cy="109" r="3.4" fill="#38bdf8" />
              <circle cx="420" cy="109" r="3.4" fill="#38bdf8" />
              <circle cx="436" cy="109" r="3.4" fill="#38bdf8" />
            </g>
            <text
              x="420"
              y="142"
              textAnchor="middle"
              fontSize="9"
              fill="rgba(255,255,255,0.55)"
            >
              训练就绪
            </text>

            {/* ── 沿主传送带流动的数据粒子 ── */}
            <circle
              data-flow="particle"
              cy="96"
              r="2.6"
              fill="#7dd3fc"
              opacity="0"
            />
            <circle
              data-flow="particle"
              cy="96"
              r="2.6"
              fill="#7dd3fc"
              opacity="0"
            />
            <circle
              data-flow="particle"
              cy="96"
              r="2.6"
              fill="#7dd3fc"
              opacity="0"
            />
            <circle
              data-flow="particle"
              cy="96"
              r="2.6"
              fill="#7dd3fc"
              opacity="0"
            />
            <circle
              data-flow="particle"
              cy="96"
              r="2.6"
              fill="#7dd3fc"
              opacity="0"
            />
          </svg>
          <h1 data-anim="hero-title">从原始数据到训练就绪</h1>
          <p data-anim="hero-sub">
            端到端 LLM 数据处理、清洗与数据集管理平台。
          </p>
        </div>
        <div data-anim="foot" className={styles.brandFoot}>
          © 2025 AI 数据平台
        </div>
      </div>
      <div className={styles.right}>
        <div data-anim="card" className={styles.formWrap}>
          <img alt="logo" src="/logo.png" className={styles.formLogo} />
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
