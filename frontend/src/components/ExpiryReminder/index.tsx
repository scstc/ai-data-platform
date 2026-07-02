import { history } from '@umijs/max';
import { App, List, Typography } from 'antd';
import React, { useEffect } from 'react';

import { listExpiringDatasets } from '@/services/data-platform/api';

/** 提前多少天算「即将到期」(与后端默认一致) */
const EXPIRY_DAYS = 14;
/** 登录成功时由登录页写入的一次性标记 */
const PENDING_KEY = 'adp_expiry_check_pending';

/**
 * 登录后到期提醒:仅在「刚登录」的那次会话加载时检查一次「我负责的」数据集是否
 * 即将到期(含已过期),命中则弹窗提醒。挂在 ProLayout 的 childrenRender 里
 * (只在登录态、非登录页渲染)。用 sessionStorage 标记确保只在登录后触发一次,
 * 普通刷新/切页不再打扰。
 */
const ExpiryReminder: React.FC = () => {
  const { modal } = App.useApp();

  useEffect(() => {
    // 同步取并清标记:即便 StrictMode 二次执行 effect,也只会真正跑一次
    if (sessionStorage.getItem(PENDING_KEY) !== '1') return;
    sessionStorage.removeItem(PENDING_KEY);

    let cancelled = false;
    (async () => {
      try {
        const res = await listExpiringDatasets(
          { days: EXPIRY_DAYS },
          { skipErrorHandler: true },
        );
        const items = res.data ?? [];
        if (cancelled || items.length === 0) return;

        const instance = modal.warning({
          title: `有 ${items.length} 个数据集即将到期`,
          width: 480,
          okText: '知道了',
          content: (
            <List
              size="small"
              dataSource={items}
              style={{ maxHeight: 320, overflowY: 'auto', marginTop: 8 }}
              renderItem={(d) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => {
                    instance.destroy();
                    history.push(`/datasets/${d.id}`);
                  }}
                >
                  <Typography.Text ellipsis style={{ flex: 1 }}>
                    {d.name}
                  </Typography.Text>
                  <Typography.Text type={d.expired ? 'danger' : 'warning'}>
                    {d.expired ? '已过期' : `${d.daysLeft} 天后到期`}
                  </Typography.Text>
                </List.Item>
              )}
            />
          ),
        });
      } catch {
        // 提醒是旁路,失败静默,不打断进入应用
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [modal]);

  return null;
};

export default ExpiryReminder;
