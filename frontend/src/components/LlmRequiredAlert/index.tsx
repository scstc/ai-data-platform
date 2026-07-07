// LLM 未配提示:增强/训练集生成这类 needs_api 算子页面顶部统一提示。
// 挂载时拉一次 /llm-providers:已存在「激活且配了 Key」的供应商则不显示;
// 拉取失败(网络/权限)按未配置处理,保留提示(宁可多提示,不误吞)。

import { history } from '@umijs/max';
import { Alert, Button, Space } from 'antd';
import { useEffect, useState } from 'react';
import { listLlmProviders } from '@/services/data-platform';

interface Props {
  /** 是否显示提示;默认 true(子页面通常都需要 LLM) */
  show?: boolean;
  /** 描述,默认写明"数据增强需 LLM" */
  description?: string;
}

const LlmRequiredAlert: React.FC<Props> = ({
  show = true,
  description = '数据增强等场景依赖 LLM 生成能力。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。',
}) => {
  // undefined=加载中(先不渲染,避免"闪一下再消失")
  const [configured, setConfigured] = useState<boolean>();
  useEffect(() => {
    listLlmProviders()
      .then((res) =>
        setConfigured(
          Boolean(
            res?.data?.some((p) => p.isActive && p.apiKeyMasked !== '未配置'),
          ),
        ),
      )
      .catch(() => setConfigured(false));
  }, []);

  if (!show || configured !== false) return null;
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
