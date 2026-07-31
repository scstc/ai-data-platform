import {
  ApiOutlined,
  ArrowRightOutlined,
  CloudOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  InboxOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { history } from '@umijs/max';
import { Badge, Card, Tag, Tooltip, Typography } from 'antd';
import type { FC } from 'react';
import { DB_KIND_CARDS, STORAGE_CARDS } from './constants';

const { Text, Title } = Typography;

/** 小节标题:左侧竖蓝条 + 文案 */
const SectionTitle: FC<{ children: string }> = ({ children }) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      margin: '28px 0 16px',
    }}
  >
    <span
      style={{ width: 4, height: 16, background: '#1677ff', borderRadius: 2 }}
    />
    <Text strong style={{ fontSize: 15 }}>
      {children}
    </Text>
  </div>
);

const STORAGE_ICON: Record<string, React.ReactNode> = {
  s3: <CloudOutlined style={{ fontSize: 22, color: '#1677ff' }} />,
  minio: <CloudOutlined style={{ fontSize: 22, color: '#c72e49' }} />,
  oss: <CloudOutlined style={{ fontSize: 22, color: '#ff6a00' }} />,
  obs: <CloudOutlined style={{ fontSize: 22, color: '#c7000b' }} />,
  hdfs: <ClusterOutlined style={{ fontSize: 22, color: '#1677ff' }} />,
};

/** 接入方式选择:按类目分组的卡片,点选跳到对应配置页(渲染在数据源管理页顶部) */
const AccessMethodPicker: FC = () => {
  const go = (path: string) => history.push(path);

  return (
    <>
      {/* 云 / 分布式存储 */}
      <SectionTitle>云 / 分布式存储</SectionTitle>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 360px))',
          gap: 16,
        }}
      >
        {STORAGE_CARDS.map((c) => (
          <Card
            key={c.key}
            hoverable
            data-testid={`method-${c.key}`}
            onClick={() => go(c.route)}
            styles={{ body: { padding: 20 } }}
          >
            <div style={{ marginBottom: 12 }}>{STORAGE_ICON[c.key]}</div>
            <Title level={5} style={{ margin: '0 0 6px' }}>
              {c.title}
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              {c.desc}
            </Text>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginTop: 16,
              }}
            >
              <Tag style={{ fontSize: 11, letterSpacing: 0.5 }}>{c.tag}</Tag>
              <ArrowRightOutlined style={{ color: '#1677ff' }} />
            </div>
          </Card>
        ))}
      </div>

      {/* 数据库连接 */}
      <SectionTitle>数据库连接</SectionTitle>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 12,
        }}
      >
        {DB_KIND_CARDS.map((c) => (
          <Card
            key={c.kind}
            hoverable={c.ready}
            size="small"
            data-testid={`method-db-${c.kind}`}
            onClick={
              c.ready
                ? () => go(`/ingest/datasources/new/database?dbKind=${c.kind}`)
                : undefined
            }
            styles={{ body: { padding: 16 } }}
            style={
              c.ready ? undefined : { opacity: 0.5, cursor: 'not-allowed' }
            }
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 10,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <DatabaseOutlined style={{ fontSize: 18, color: '#1677ff' }} />
                <Text strong>{c.title}</Text>
              </div>
              {c.ready ? (
                <Tooltip title="已实测可连">
                  <Badge status="success" />
                </Tooltip>
              ) : (
                <Tooltip title={c.reason ?? '暂未就绪'}>
                  <Tag style={{ margin: 0, fontSize: 11 }}>暂未就绪</Tag>
                </Tooltip>
              )}
            </div>
          </Card>
        ))}
      </div>

      {/* 文件接入 + 外部 API */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 16,
          marginTop: 4,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <SectionTitle>文件接入</SectionTitle>
          <Card
            hoverable
            data-testid="method-local-upload"
            onClick={() => go('/ingest/local-upload')}
            style={{ borderStyle: 'dashed', flex: 1 }}
            styles={{
              body: {
                padding: '32px 20px',
                textAlign: 'center',
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
              },
            }}
          >
            <InboxOutlined style={{ fontSize: 32, color: '#1677ff' }} />
            <Title level={5} style={{ margin: '12px 0 4px' }}>
              本地上传
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              从本机上传文件 / 选择文件管理中的文件接入
            </Text>
          </Card>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <SectionTitle>外部 API</SectionTitle>
          <Card
            hoverable
            data-testid="method-api"
            onClick={() => go('/ingest/datasources/new/api')}
            style={{ flex: 1 }}
            styles={{
              body: {
                padding: 20,
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
              },
            }}
          >
            <div style={{ marginBottom: 12 }}>
              <ThunderboltOutlined style={{ fontSize: 22, color: '#1677ff' }} />
            </div>
            <Title level={5} style={{ margin: '0 0 6px' }}>
              API 推送
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              由外部系统主动 POST 数据到平台生成的推送端点,适合实时数据流。
            </Text>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginTop: 'auto',
                paddingTop: 16,
              }}
            >
              <Tag color="blue" style={{ fontSize: 11, letterSpacing: 0.5 }}>
                <ApiOutlined /> REAL-TIME
              </Tag>
              <ArrowRightOutlined style={{ color: '#1677ff' }} />
            </div>
          </Card>
        </div>
      </div>
    </>
  );
};

export default AccessMethodPicker;
