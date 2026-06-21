import {
  ApartmentOutlined,
  ArrowRightOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Card, Tag, Typography } from 'antd';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const { Text, Title } = Typography;

/** 本地上传落地页:选择数据类别。
 *  左「单一数据」→ 单一格式文件合并为 jsonl 数据集(已实现);
 *  右「场景数据」→ COT/问答对/偏好/时序/GIS/多模态 等数据类型选择页
 *  (多模态已有配置页,其余接入逻辑待定)。 */
const LocalUploadHome: React.FC = () => {
  const go = (path: string) => history.push(path);

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传' },
      ])}
      title="本地上传"
      content="选择数据类别开始接入。"
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
          gap: 16,
          maxWidth: 860,
        }}
      >
        <Card
          hoverable
          data-testid="local-upload-single"
          onClick={() => go('/ingest/local-upload/single')}
          styles={{ body: { padding: 24 } }}
        >
          <FileTextOutlined style={{ fontSize: 28, color: '#1677ff' }} />
          <Title level={5} style={{ margin: '12px 0 6px' }}>
            单一数据
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            csv/tsv/word/pdf 等单一格式文件批量上传,原件存入内置
            MinIO,合并生成一个 jsonl 数据集。
          </Text>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 20,
            }}
          >
            <Tag color="blue" style={{ fontSize: 11 }}>
              单一格式
            </Tag>
            <ArrowRightOutlined style={{ color: '#1677ff' }} />
          </div>
        </Card>

        <Card
          hoverable
          data-testid="local-upload-scenario"
          onClick={() => go('/ingest/local-upload/scenario')}
          styles={{ body: { padding: 24 } }}
        >
          <ApartmentOutlined style={{ fontSize: 28, color: '#722ed1' }} />
          <Title level={5} style={{ margin: '12px 0 6px' }}>
            场景数据
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            COT 思维 / 问答对 / 偏好 / 时序 / GIS 位置 /
            多模态(图音视频)等数据,按数据类型分别接入。
          </Text>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 20,
            }}
          >
            <Tag color="purple" style={{ fontSize: 11 }}>
              多类型
            </Tag>
            <ArrowRightOutlined style={{ color: '#722ed1' }} />
          </div>
        </Card>
      </div>
    </PageContainer>
  );
};

export default LocalUploadHome;
