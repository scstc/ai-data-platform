import {
  ApartmentOutlined,
  EditOutlined,
  PlayCircleOutlined,
  PlusCircleOutlined,
  SaveOutlined,
  SettingOutlined,
  TableOutlined,
  ZoomInOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Avatar,
  Button,
  Card,
  Input,
  message,
  Progress,
  Select,
  Slider,
  Tag,
  Typography,
} from 'antd';
import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text } = Typography;

/** 地理空间数据摄入(设计稿高保真脚手架)。
 *  坐标系/分辨率、图层映射、空间范围过滤为本地交互;地图预览、统计卡为静态演示。
 *  真实解析逻辑待后端接入逻辑确定后接通,「保存草稿 / 开始解析」暂以提示占位。 */

/** 坐标系候选 */
const CRS_OPTIONS = [
  'WGS 84 (EPSG:4326) - 全球标准',
  'Web Mercator (EPSG:3857)',
  'CGCS2000 (EPSG:4490)',
].map((v) => ({ label: v, value: v }));

/** 图层映射行(静态演示) */
const LAYERS: {
  key: string;
  icon: ReactNode;
  iconBg: string;
  iconColor: string;
  name: string;
  tagText: string;
  tagColor: string;
  meta: string;
  format: string;
}[] = [
  {
    key: 'vector',
    icon: <ApartmentOutlined />,
    iconBg: '#e6f0ff',
    iconColor: '#1677ff',
    name: 'Building_Footprints',
    tagText: 'VECTOR',
    tagColor: 'blue',
    meta: 'Geometry: Polygon  |  Attributes: 12 fields',
    format: 'GeoJSON',
  },
  {
    key: 'raster',
    icon: <TableOutlined />,
    iconBg: '#f6ffed',
    iconColor: '#52c41a',
    name: 'NDVI_Index_2023',
    tagText: 'RASTER',
    tagColor: 'green',
    meta: 'Bands: 4  |  Precision: Float32',
    format: 'CloudOptimizedGeoTIFF',
  },
];

const labelStyle: CSSProperties = {
  fontSize: 13,
  display: 'block',
  marginBottom: 8,
};

const GisIngestPage: React.FC = () => {
  const [targetCrs, setTargetCrs] = useState('WGS 84 (EPSG:4326) - 全球标准');
  const [resolution, setResolution] = useState('0.5');
  const [zoom, setZoom] = useState(50);
  const [minLon, setMinLon] = useState('121.4737');
  const [minLat, setMinLat] = useState('31.1002');

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: '地理空间数据摄入' },
      ])}
      title="地理空间数据摄入"
      content="配置坐标系统、分辨率与空间图层转换规则。"
      onBack={() => history.push('/ingest/local-upload/scenario')}
      extra={[
        <Button
          key="draft"
          icon={<SaveOutlined />}
          data-testid="gis-save-draft"
          onClick={() => message.info('保存草稿待接入')}
        >
          保存草稿
        </Button>,
        <Button
          key="parse"
          type="primary"
          icon={<PlayCircleOutlined />}
          data-testid="gis-start-parse"
          onClick={() => message.info('接入逻辑待定,确定后开放开始解析')}
        >
          开始解析
        </Button>,
      ]}
    >
      <ScenarioImportCard semanticType="gis" />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左列:坐标系与分辨率 + 图层映射 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card styles={{ body: { padding: 24 } }}>
            <SectionTitle>坐标系与分辨率设置</SectionTitle>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 16,
                marginTop: 20,
              }}
            >
              <div>
                <Text strong style={labelStyle}>
                  目标坐标系 (Target CRS)
                </Text>
                <Select
                  value={targetCrs}
                  onChange={setTargetCrs}
                  options={CRS_OPTIONS}
                  style={{ width: '100%' }}
                  data-testid="gis-target-crs"
                />
              </div>
              <div>
                <Text strong style={labelStyle}>
                  空间分辨率 (Grid Resolution)
                </Text>
                <Input
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  addonAfter="Meters/PX"
                  data-testid="gis-resolution"
                />
              </div>
            </div>

            <Text
              type="secondary"
              style={{ fontSize: 12, marginTop: 8, display: 'block' }}
            >
              检测到源数据使用: EPSG:3857
            </Text>

            <Slider
              value={zoom}
              onChange={setZoom}
              style={{ width: '100%', marginTop: 8 }}
              data-testid="gis-zoom"
            />
          </Card>

          <Card
            styles={{ body: { padding: 24 } }}
            extra={
              <a
                data-testid="gis-add-layer"
                onClick={() => message.info('添加新层待接入')}
              >
                <PlusCircleOutlined /> 添加新层
              </a>
            }
            title={<SectionTitle>图层映射与数据转换</SectionTitle>}
          >
            <div style={{ display: 'grid', gap: 12 }}>
              {LAYERS.map((l) => (
                <div
                  key={l.key}
                  style={{
                    border: '1px solid #f0f0f0',
                    borderRadius: 8,
                    padding: 12,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                  }}
                >
                  <span
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 8,
                      background: l.iconBg,
                      color: l.iconColor,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 18,
                      flexShrink: 0,
                    }}
                  >
                    {l.icon}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{ display: 'flex', alignItems: 'center', gap: 8 }}
                    >
                      <Text strong>{l.name}</Text>
                      <Tag color={l.tagColor} style={{ margin: 0 }}>
                        {l.tagText}
                      </Tag>
                    </div>
                    <Text
                      type="secondary"
                      style={{ fontSize: 12, display: 'block', marginTop: 4 }}
                    >
                      {l.meta}
                    </Text>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: 11, color: '#8c8c8c' }}>FORMAT</div>
                    <div style={{ fontSize: 13 }}>{l.format}</div>
                  </div>
                  <a
                    data-testid={`gis-layer-settings-${l.key}`}
                    style={{ color: '#8c8c8c', flexShrink: 0 }}
                    onClick={() => message.info('图层设置待接入')}
                  >
                    <SettingOutlined />
                  </a>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* 右列:空间范围过滤 */}
        <Card
          title="空间范围过滤 (Bounding Box)"
          styles={{ body: { padding: 16 } }}
          extra={
            <span style={{ display: 'inline-flex', gap: 12 }}>
              <a
                data-testid="gis-bbox-edit"
                style={{ color: '#8c8c8c' }}
                onClick={() => message.info('编辑边界框待接入')}
              >
                <EditOutlined />
              </a>
              <a
                data-testid="gis-bbox-zoom"
                style={{ color: '#8c8c8c' }}
                onClick={() => message.info('放大地图待接入')}
              >
                <ZoomInOutlined />
              </a>
            </span>
          }
        >
          {/* 深色地图预览 */}
          <div
            style={{
              height: 220,
              borderRadius: 8,
              position: 'relative',
              background:
                'radial-gradient(circle at 50% 50%, #1e293b, #0f172a)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                position: 'absolute',
                top: '25%',
                left: '30%',
                width: '40%',
                height: '45%',
                border: '2px solid #22d3ee',
                background: 'rgba(34,211,238,0.08)',
              }}
            />
            <div
              style={{
                position: 'absolute',
                bottom: 0,
                left: 0,
                right: 0,
                padding: 10,
                background: 'rgba(15,23,42,0.85)',
                color: '#cbd5e1',
                fontSize: 12,
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 4,
              }}
            >
              <span>North: 31.2304</span>
              <span>West: 121.4737</span>
              <span>South: 31.1002</span>
              <span>East: 121.6231</span>
            </div>
          </div>

          {/* 手动输入坐标 */}
          <Text strong style={{ display: 'block', marginTop: 12 }}>
            手动输入坐标
          </Text>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 12,
              marginTop: 8,
            }}
          >
            <div>
              <Text
                type="secondary"
                style={{ fontSize: 12, display: 'block', marginBottom: 6 }}
              >
                Min Longitude
              </Text>
              <Input
                value={minLon}
                onChange={(e) => setMinLon(e.target.value)}
                data-testid="gis-min-lon"
              />
            </div>
            <div>
              <Text
                type="secondary"
                style={{ fontSize: 12, display: 'block', marginBottom: 6 }}
              >
                Min Latitude
              </Text>
              <Input
                value={minLat}
                onChange={(e) => setMinLat(e.target.value)}
                data-testid="gis-min-lat"
              />
            </div>
          </div>

          <Button
            block
            type="dashed"
            style={{ marginTop: 12 }}
            data-testid="gis-region-template"
            onClick={() => message.info('从现有区域模板选择待接入')}
          >
            从现有区域模板选择
          </Button>
        </Card>
      </div>

      {/* 底部三统计卡 */}
      <div
        style={{
          marginTop: 16,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          gap: 16,
        }}
      >
        <Card
          styles={{ body: { padding: 16 } }}
          style={{ borderLeft: '3px solid #52c41a' }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            数据源健康度
          </Text>
          <div style={{ fontSize: 26, fontWeight: 700, margin: '4px 0 8px' }}>
            98.4%
          </div>
          <Progress percent={98} showInfo={false} strokeColor="#52c41a" />
        </Card>

        <Card
          styles={{ body: { padding: 16 } }}
          style={{ borderLeft: '3px solid #1677ff' }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            预估存储占用
          </Text>
          <div style={{ fontSize: 26, fontWeight: 700, margin: '4px 0' }}>
            4.2 GB
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            GeoTIFF/Vector Composite
          </Text>
        </Card>

        <Card
          styles={{ body: { padding: 16 } }}
          style={{ borderLeft: '3px solid #1677ff' }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            实时处理节点
          </Text>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              marginTop: 4,
            }}
          >
            <div style={{ fontSize: 26, fontWeight: 700 }}>08</div>
            <Avatar.Group size="small" max={{ count: 2 }}>
              <Avatar style={{ background: '#1677ff' }}>N1</Avatar>
              <Avatar style={{ background: '#52c41a' }}>N2</Avatar>
              <Avatar>N3</Avatar>
              <Avatar>N4</Avatar>
              <Avatar>N5</Avatar>
              <Avatar>N6</Avatar>
              <Avatar>N7</Avatar>
              <Avatar>N8</Avatar>
            </Avatar.Group>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
};

/** 卡片小节标题(蓝色竖条 + 文字) */
const SectionTitle: React.FC<{ children: string }> = ({ children }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
    <span
      style={{
        width: 4,
        height: 18,
        borderRadius: 2,
        background: '#1677ff',
        display: 'inline-block',
      }}
    />
    <Text strong style={{ fontSize: 16 }}>
      {children}
    </Text>
  </span>
);

export default GisIngestPage;
