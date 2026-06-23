// LLM 未配提示:合成/增强这类 needs_api 算子页面顶部统一提示。
// 走 useState + useEffect 拉一次 /llm-providers/current(若后端没有该端点则优雅降级)。
// 当前实现:纯前端静态提示 + 跳转 LLM 配置页(管理员可见)。

import { history } from '@umijs/max';
import { Alert, Button, Space } from 'antd';

interface Props {
  /** 是否显示提示;默认 true(子页面通常都需要 LLM) */
  show?: boolean;
  /** 描述,默认写明"数据合成/增强需 LLM" */
  description?: string;
}

const LlmRequiredAlert: React.FC<Props> = ({
  show = true,
  description = '数据合成与增强依赖 LLM 生成能力。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。',
}) => {
  if (!show) return null;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 16 }}
      message="需要 LLM 支持"
      description={
        <Space>
          <span>{description}</span>
          <Button
            size="small"
            type="link"
            onClick={() => history.push('/ops/llm-settings')}
          >
            前往配置 →
          </Button>
        </Space>
      }
    />
  );
};

export default LlmRequiredAlert;
