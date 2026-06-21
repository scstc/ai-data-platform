import {
  ApartmentOutlined,
  BranchesOutlined,
  CommentOutlined,
  EnvironmentOutlined,
  HeartOutlined,
  LineChartOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Alert, Card, message, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const { Text } = Typography;

/** 场景数据类型,对齐后端 semantic_type 设计(docs/plan/14):
 *  cot / qa / preference / timeseries / gis / multimodal。
 *  「多模态」跳转到「文件源与预处理」设计页;其余类型接入逻辑待定,先占位。 */
const SCENARIO_TYPES: {
  key: string; // 未来对应 semanticType
  title: string;
  desc: string;
  icon: ReactNode;
  color: string; // 卡片主题色(图标底色 + 强调)
  route?: string; // 有 route 的直接跳转,否则占位提示
}[] = [
  {
    key: 'cot',
    title: 'COT 思维数据',
    desc: '思维链:问题 / 推理过程 / 答案',
    icon: <BranchesOutlined />,
    color: '#722ed1',
    route: '/ingest/local-upload/cot',
  },
  {
    key: 'qa',
    title: '问答对数据',
    desc: '问题 / 答案成对',
    icon: <CommentOutlined />,
    color: '#1677ff',
    route: '/ingest/local-upload/qa',
  },
  {
    key: 'preference',
    title: '偏好数据',
    desc: '优劣对比(RLHF 偏好对)',
    icon: <HeartOutlined />,
    color: '#eb2f96',
    route: '/ingest/local-upload/preference',
  },
  {
    key: 'timeseries',
    title: '时序数据',
    desc: '带时间戳的序列数据',
    icon: <LineChartOutlined />,
    color: '#13c2c2',
    route: '/ingest/local-upload/timeseries',
  },
  {
    key: 'gis',
    title: 'GIS 位置数据',
    desc: '经纬度 / 地理位置',
    icon: <EnvironmentOutlined />,
    color: '#52c41a',
    route: '/ingest/local-upload/gis',
  },
  {
    key: 'multimodal',
    title: '多模态数据',
    desc: '视频 / 音频 / 图像 / PDF 跨模态解析',
    icon: <ApartmentOutlined />,
    color: '#fa541c',
    route: '/ingest/local-upload/multimodal',
  },
];

/** 场景数据接入落地页:列出各数据类型供选择。
 *  「多模态」进入「文件源与预处理」页;其余类型接入逻辑待定,先占位。 */
const ScenarioDataPage: React.FC = () => {
  const onPick = (t: (typeof SCENARIO_TYPES)[number]) => {
    if (t.route) history.push(t.route);
    else message.info(`「${t.title}」接入逻辑待定,确定后开放`);
  };

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据' },
      ])}
      title="场景数据接入"
      content="选择数据类型开始接入。"
      onBack={() => history.push('/ingest/local-upload')}
    >
      <Alert
        type="info"
        showIcon
        style={{ maxWidth: 1280, marginBottom: 20 }}
        message="「多模态」已开放配置页;其余类型的接入逻辑待定,确定后逐项开放。"
      />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 20,
          maxWidth: 1280,
        }}
      >
        {SCENARIO_TYPES.map((t) => (
          <Card
            key={t.key}
            hoverable
            data-testid={`scenario-type-${t.key}`}
            onClick={() => onPick(t)}
            style={{ height: '100%', borderRadius: 12 }}
            styles={{
              body: {
                padding: 28,
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
              },
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: 14,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 28,
                color: t.color,
                background: `${t.color}1a`,
                marginBottom: 18,
              }}
            >
              {t.icon}
            </div>
            <Text strong style={{ fontSize: 17 }}>
              {t.title}
            </Text>
            <Text
              type="secondary"
              style={{
                fontSize: 13,
                display: 'block',
                marginTop: 8,
                lineHeight: 1.6,
                flex: 1,
              }}
            >
              {t.desc}
            </Text>
            <div style={{ marginTop: 18 }}>
              <Tag color={t.route ? 'purple' : 'default'}>
                {t.route ? '可配置' : '待开放'}
              </Tag>
            </div>
          </Card>
        ))}
      </div>
    </PageContainer>
  );
};

export default ScenarioDataPage;
