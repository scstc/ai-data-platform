import { BellOutlined } from '@ant-design/icons';
import { history } from '@umijs/max';
import { Badge, Button, List, Popover, Typography } from 'antd';
import { createStyles } from 'antd-style';
import dayjs from 'dayjs';
import React, { useCallback, useEffect, useRef, useState } from 'react';

import {
  getUnreadCount,
  listNotifications,
  markAllRead,
  markRead,
} from '@/services/data-platform/api';

const POLL_MS = 30_000;

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
  list: css`
    width: 360px;
    max-height: 440px;
    overflow-y: auto;
  `,
  item: css`
    cursor: pointer;
    padding: 10px 16px !important;
    &:hover {
      background: ${token.colorFillTertiary};
    }
  `,
  unreadDot: css`
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: ${token.colorPrimary};
    margin-right: 8px;
    margin-top: 6px;
    flex-shrink: 0;
  `,
  errorText: css`
    color: ${token.colorError};
  `,
  footer: css`
    padding: 8px 16px;
    text-align: center;
    border-top: 1px solid ${token.colorBorderSecondary};
  `,
}));

/** 点击通知时跳转的目标路由 */
function resolveRoute(item: DataPlatform.NotificationItem): string | null {
  if (item.sourceType === 'ingest_task') return '/ingest/tasks';
  if (item.sourceType === 'job') return '/ops/data-tasks';
  return null;
}

const NotificationBell: React.FC = () => {
  const { styles } = useStyles();
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<DataPlatform.NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshCount = useCallback(async () => {
    try {
      const res = await getUnreadCount({ skipErrorHandler: true });
      setCount(res.count ?? 0);
    } catch (_) {
      // polling — silent
    }
  }, []);

  const refreshList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listNotifications(
        { pageSize: 10, page: 1 },
        { skipErrorHandler: true },
      );
      setItems(res.data ?? []);
    } catch (_) {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  // Start polling on mount; clear on unmount
  useEffect(() => {
    refreshCount();
    timerRef.current = setInterval(refreshCount, POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refreshCount]);

  // Refresh list + count whenever popover opens
  useEffect(() => {
    if (open) {
      refreshList();
      refreshCount();
    }
  }, [open, refreshList, refreshCount]);

  const handleItemClick = async (item: DataPlatform.NotificationItem) => {
    setOpen(false);
    if (!item.read) {
      try {
        await markRead(item.id, { skipErrorHandler: true });
        setCount((c) => Math.max(0, c - 1));
        setItems((prev) =>
          prev.map((n) => (n.id === item.id ? { ...n, read: true } : n)),
        );
      } catch (_) {
        // silent
      }
    }
    const route = resolveRoute(item);
    if (route) history.push(route);
  };

  const handleMarkAll = async () => {
    try {
      await markAllRead({ skipErrorHandler: true });
      setCount(0);
      setItems((prev) => prev.map((n) => ({ ...n, read: true })));
    } catch (_) {
      // silent
    }
  };

  const popoverContent = (
    <div>
      <List
        className={styles.list}
        loading={loading}
        dataSource={items}
        locale={{ emptyText: '暂无通知' }}
        renderItem={(item) => (
          <List.Item
            className={styles.item}
            onClick={() => handleItemClick(item)}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                width: '100%',
              }}
            >
              {!item.read && <span className={styles.unreadDot} />}
              <div style={{ flex: 1, minWidth: 0 }}>
                <Typography.Text
                  strong={!item.read}
                  className={
                    item.level === 'error' ? styles.errorText : undefined
                  }
                  ellipsis
                >
                  {item.title}
                </Typography.Text>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {dayjs(item.createdAt).fromNow()}
                  </Typography.Text>
                </div>
              </div>
            </div>
          </List.Item>
        )}
      />
      <div className={styles.footer}>
        <Typography.Link onClick={handleMarkAll}>全部已读</Typography.Link>
      </div>
    </div>
  );

  return (
    <Popover
      content={popoverContent}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomRight"
      arrow={false}
      styles={{ content: { padding: 0 } }}
    >
      <Button type="text" className={styles.action} aria-label="通知中心">
        <Badge count={count} size="small" offset={[2, -2]}>
          <BellOutlined />
        </Badge>
      </Button>
    </Popover>
  );
};

export default NotificationBell;
