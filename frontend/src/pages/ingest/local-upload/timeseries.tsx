import {
  AppstoreOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DotChartOutlined,
  DownloadOutlined,
  LineChartOutlined,
  LineOutlined,
  MinusCircleOutlined,
  MoreOutlined,
  ProfileOutlined,
  SaveOutlined,
  SyncOutlined,
  UndoOutlined,
  WarningOutlined,
  ZoomInOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Button,
  Card,
  Checkbox,
  Input,
  message,
  Progress,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text } = Typography;

/** 时间序列配置(设计稿高保真脚手架)。
 *  采样频率 / 窗口聚合 / 离群值检测 / 时间戳同步均为本地状态;
 *  实时数据预览、最近摄取日志为静态演示;真实采集/聚合逻辑待后端接入逻辑确定后接通,
 *  「保存并运行」暂以提示占位。 */

const PLACEHOLDER = '待接入';

/** 实时预览柱状图高度(px);索引 5 为离群值。 */
const BARS: { id: string; h: number; outlier: boolean }[] = [
  120, 150, 90, 175, 110, 210, 130, 165, 100, 140, 70, 185, 115,
].map((h, i) => ({ id: `bar-${i}`, h, outlier: i === 5 }));

/** X 轴时间刻度 */
const X_AXIS = ['14:00:00', '14:00:15', '14:00:30', '14:00:45', '14:01:00'];

/** 聚合函数候选 */
const AGG_FUNCS = ['AVG', 'MAX', 'MIN', 'SUM'];

/** 异常处理策略 */
const OUTLIER_STRATEGIES: { key: string; icon: ReactNode }[] = [
  { key: '丢弃', icon: <MinusCircleOutlined /> },
  { key: '线性插值', icon: <LineOutlined /> },
  { key: '仅告警', icon: <WarningOutlined /> },
];

type LogStatus = 'SUCCESS' | 'DROPPED_OUTLIER';
type LogRow = {
  key: string;
  ts: string;
  batch: string;
  records: string;
  cost: string;
  status: LogStatus;
};
const LOG_ROWS: LogRow[] = [
  {
    key: 'l1',
    ts: '2023-11-20 14:05:01',
    batch: 'BATCH_001_8829',
    records: '1,240 pkts',
    cost: '12ms',
    status: 'SUCCESS',
  },
  {
    key: 'l2',
    ts: '2023-11-20 14:04:46',
    batch: 'BATCH_001_8828',
    records: '1,198 pkts',
    cost: '14ms',
    status: 'SUCCESS',
  },
  {
    key: 'l3',
    ts: '2023-11-20 14:04:31',
    batch: 'BATCH_001_8827',
    records: '842 pkts',
    cost: '22ms',
    status: 'DROPPED_OUTLIER',
  },
];

const labelStyle: CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  display: 'block',
  marginBottom: 8,
};

const TimeSeriesIngestPage: React.FC = () => {
  const [interval, setInterval] = useState('15');
  const [intervalUnit, setIntervalUnit] = useState('秒 (s)');
  const [windowType, setWindowType] = useState('滑动窗口 (Sliding Window)');
  const [windowLen, setWindowLen] = useState('5 min');
  const [windowStep, setWindowStep] = useState('1 min');
  const [aggFuncs, setAggFuncs] = useState<string[]>(['AVG']);
  const [autoFilter, setAutoFilter] = useState(true);
  const [threshold, setThreshold] = useState(3.5);
  const [strategy, setStrategy] = useState('丢弃');
  const [timezone, setTimezone] = useState('UTC +08:00 (Asia/Shanghai)');
  const [lateArrival, setLateArrival] = useState('30');
  const [watermark, setWatermark] = useState<string[]>(['event']);

  const toggleAgg = (fn: string) =>
    setAggFuncs((prev) =>
      prev.includes(fn) ? prev.filter((f) => f !== fn) : [...prev, fn],
    );

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: '时间序列配置' },
      ])}
      title="时间序列配置 (Time Series)"
      content="配置采样频率、窗口聚合、离群值检测与时间戳同步。"
      onBack={() => history.push('/ingest/local-upload/scenario')}
      extra={[
        <Button
          key="undo"
          icon={<UndoOutlined />}
          data-testid="ts-undo"
          onClick={() => history.push('/ingest/local-upload/scenario')}
        >
          撤销更改
        </Button>,
        <Button
          key="save"
          type="primary"
          icon={<SaveOutlined />}
          data-testid="ts-save"
          onClick={() => message.info('接入逻辑待定,确定后开放保存并运行')}
        >
          保存并运行
        </Button>,
      ]}
    >
      <ScenarioImportCard semanticType="timeseries" />
      {/* ROW 1 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 0.9fr) minmax(0, 1.3fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* LEFT column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Card A: 采样频率 */}
          <Card
            title={
              <SectionTitle icon={<ClockCircleOutlined />}>
                采样频率 (Interval)
              </SectionTitle>
            }
            styles={{ body: { padding: 20 } }}
          >
            <Text style={labelStyle}>采样间隔</Text>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                style={{ width: '70%' }}
                value={interval}
                data-testid="ts-interval"
                onChange={(e) => setInterval(e.target.value)}
              />
              <Select
                style={{ width: '30%' }}
                value={intervalUnit}
                data-testid="ts-interval-unit"
                onChange={setIntervalUnit}
                options={[
                  { label: '毫秒 (ms)', value: '毫秒 (ms)' },
                  { label: '秒 (s)', value: '秒 (s)' },
                  { label: '分 (min)', value: '分 (min)' },
                ]}
              />
            </Space.Compact>
            <div
              style={{
                background: '#f5f7fa',
                borderRadius: 8,
                padding: 12,
                fontSize: 12,
                color: '#8c8c8c',
                marginTop: 12,
              }}
            >
              提示:当前数据流的平均到达速率为 2.4s。建议采样间隔不低于 5s
              以确保稳定性。
            </div>
          </Card>

          {/* Card B: 窗口聚合 */}
          <Card
            title={
              <SectionTitle icon={<AppstoreOutlined />}>
                窗口聚合 (Windowing)
              </SectionTitle>
            }
            styles={{ body: { padding: 20 } }}
          >
            <Text style={labelStyle}>窗口类型</Text>
            <Select
              style={{ width: '100%' }}
              value={windowType}
              data-testid="ts-window-type"
              onChange={setWindowType}
              options={[
                {
                  label: '滑动窗口 (Sliding Window)',
                  value: '滑动窗口 (Sliding Window)',
                },
                { label: '滚动窗口 (Tumbling)', value: '滚动窗口 (Tumbling)' },
                { label: '会话窗口 (Session)', value: '会话窗口 (Session)' },
              ]}
            />
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
                marginTop: 16,
              }}
            >
              <div>
                <Text style={labelStyle}>长度</Text>
                <Input
                  value={windowLen}
                  data-testid="ts-window-len"
                  onChange={(e) => setWindowLen(e.target.value)}
                />
              </div>
              <div>
                <Text style={labelStyle}>滑动步长</Text>
                <Input
                  value={windowStep}
                  data-testid="ts-window-step"
                  onChange={(e) => setWindowStep(e.target.value)}
                />
              </div>
            </div>
            <Text style={{ ...labelStyle, margin: '16px 0 8px' }}>
              聚合函数
            </Text>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {AGG_FUNCS.map((fn) => {
                const on = aggFuncs.includes(fn);
                return (
                  <Tag
                    key={fn}
                    data-testid={`ts-agg-${fn}`}
                    onClick={() => toggleAgg(fn)}
                    color={on ? 'blue' : undefined}
                    style={{
                      cursor: 'pointer',
                      margin: 0,
                      padding: '2px 12px',
                      borderRadius: 4,
                    }}
                  >
                    {fn}
                  </Tag>
                );
              })}
              <Tag
                data-testid="ts-agg-add"
                onClick={() => message.info(`添加聚合函数${PLACEHOLDER}`)}
                style={{
                  cursor: 'pointer',
                  margin: 0,
                  padding: '2px 12px',
                  borderRadius: 4,
                  borderStyle: 'dashed',
                  background: 'transparent',
                }}
              >
                + 添加
              </Tag>
            </div>
          </Card>
        </div>

        {/* RIGHT column: 实时数据预览 */}
        <Card
          title={
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              <SectionTitle icon={<LineChartOutlined />}>
                实时数据预览 (Live Preview)
              </SectionTitle>
              <span
                style={{
                  background: '#389e0d',
                  color: '#fff',
                  fontSize: 10,
                  padding: '1px 6px',
                  borderRadius: 3,
                  marginLeft: 8,
                }}
              >
                LIVE STREAM
              </span>
            </span>
          }
          extra={
            <Space size={14}>
              <a
                style={{ color: '#8c8c8c' }}
                data-testid="ts-preview-zoom"
                onClick={() => message.info(`放大预览${PLACEHOLDER}`)}
              >
                <ZoomInOutlined />
              </a>
              <a
                style={{ color: '#8c8c8c' }}
                data-testid="ts-preview-download"
                onClick={() => message.info(`下载预览${PLACEHOLDER}`)}
              >
                <DownloadOutlined />
              </a>
              <a
                style={{ color: '#8c8c8c' }}
                data-testid="ts-preview-more"
                onClick={() => message.info(`更多操作${PLACEHOLDER}`)}
              >
                <MoreOutlined />
              </a>
            </Space>
          }
          styles={{ body: { padding: 20 } }}
        >
          <div style={{ position: 'relative' }}>
            {/* bar chart */}
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                height: 220,
                gap: 10,
                padding: '0 8px',
              }}
            >
              {BARS.map((b) => (
                <div
                  key={b.id}
                  style={{
                    flex: 1,
                    height: b.h,
                    background: b.outlier ? '#ff7875' : '#69b1ff',
                    borderRadius: '4px 4px 0 0',
                  }}
                />
              ))}
            </div>
            {/* x-axis labels */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                color: '#8c8c8c',
                fontSize: 12,
                marginTop: 6,
              }}
            >
              {X_AXIS.map((t) => (
                <span key={t}>{t}</span>
              ))}
            </div>

            {/* floating 活跃节点详情 */}
            <div
              style={{
                position: 'absolute',
                top: 8,
                right: 8,
                background: '#fff',
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                padding: 10,
                fontSize: 12,
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.08)',
                minWidth: 150,
              }}
            >
              <div
                style={{ fontWeight: 700, color: '#1677ff', marginBottom: 6 }}
              >
                活跃节点详情
              </div>
              <DetailRow label="当前值" value="42.85 °C" />
              <DetailRow label="偏离度" value="+12.4%" valueColor="#fa8c16" />
              <DetailRow label="节点ID" value="SN-992-X" />
            </div>
          </div>
        </Card>
      </div>

      {/* ROW 2 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 16,
          marginTop: 16,
        }}
      >
        {/* LEFT: 离群值检测 */}
        <Card
          title={
            <SectionTitle icon={<DotChartOutlined />}>
              离群值检测 (Outlier Detection)
            </SectionTitle>
          }
          styles={{ body: { padding: 20 } }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <Text strong style={{ fontSize: 13 }}>
              启用自动过滤
            </Text>
            <Switch
              checked={autoFilter}
              data-testid="ts-auto-filter"
              onChange={setAutoFilter}
            />
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            基于 Z-Score 或 MAD 算法自动标记异常点
          </Text>

          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              margin: '20px 0 4px',
            }}
          >
            <Text style={{ fontSize: 13, fontWeight: 600 }}>
              阈值系数 (Threshold)
            </Text>
            <Tag style={{ margin: 0 }}>{threshold} σ</Tag>
          </div>
          <Slider
            min={2}
            max={5}
            step={0.1}
            value={threshold}
            data-testid="ts-threshold"
            onChange={setThreshold}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              更宽松 (2.0)
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              严格控制 (5.0)
            </Text>
          </div>

          <Text style={{ ...labelStyle, margin: '20px 0 8px' }}>
            异常处理策略
          </Text>
          <div style={{ display: 'flex', gap: 8 }}>
            {OUTLIER_STRATEGIES.map((s) => (
              <Button
                key={s.key}
                block
                icon={s.icon}
                type={strategy === s.key ? 'primary' : 'default'}
                data-testid={`ts-strategy-${s.key}`}
                onClick={() => setStrategy(s.key)}
              >
                {s.key}
              </Button>
            ))}
          </div>
        </Card>

        {/* RIGHT: 时间戳同步 */}
        <Card
          title={
            <SectionTitle icon={<SyncOutlined />} iconColor="#52c41a">
              时间戳同步 (Timestamp Sync)
            </SectionTitle>
          }
          style={{ borderLeft: '3px solid #52c41a' }}
          styles={{ body: { padding: 20 } }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 12,
            }}
          >
            <div>
              <Text style={labelStyle}>目标时区</Text>
              <Select
                style={{ width: '100%' }}
                value={timezone}
                data-testid="ts-timezone"
                onChange={setTimezone}
                options={[
                  {
                    label: 'UTC +08:00 (Asia/Shanghai)',
                    value: 'UTC +08:00 (Asia/Shanghai)',
                  },
                  { label: 'UTC +00:00 (UTC)', value: 'UTC +00:00 (UTC)' },
                  {
                    label: 'UTC -05:00 (America/New_York)',
                    value: 'UTC -05:00 (America/New_York)',
                  },
                ]}
              />
            </div>
            <div>
              <Text style={labelStyle}>最大延迟允许 (Late Arrival)</Text>
              <Input
                value={lateArrival}
                addonAfter="ms"
                data-testid="ts-late-arrival"
                onChange={(e) => setLateArrival(e.target.value)}
              />
            </div>
          </div>

          <div
            style={{
              background: '#f6ffed',
              border: '1px solid #b7eb8f',
              borderRadius: 8,
              padding: 12,
              marginTop: 16,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontWeight: 600,
              }}
            >
              <CheckCircleFilled style={{ color: '#52c41a' }} />
              同步完整性校验
            </div>
            <div
              style={{ fontSize: 12, color: '#389e0d', margin: '6px 0 10px' }}
            >
              所有节点时钟已通过 NTP 服务同步,当前最大漂移量为 0.02ms。
            </div>
            <Progress
              percent={99}
              showInfo={false}
              strokeColor="#52c41a"
              size="small"
            />
          </div>

          <Text style={{ ...labelStyle, margin: '16px 0 8px' }}>
            水印生成策略 (Watermark)
          </Text>
          <Checkbox.Group
            value={watermark}
            data-testid="ts-watermark"
            onChange={(v) => setWatermark(v as string[])}
            options={[
              { label: '按事件时间 (Event Time)', value: 'event' },
              { label: '按处理时间 (Processing Time)', value: 'processing' },
            ]}
          />
        </Card>
      </div>

      {/* ROW 3: 最近摄取日志 */}
      <Card
        style={{ marginTop: 16 }}
        title={
          <SectionTitle icon={<ProfileOutlined />}>
            最近摄取日志 (Recent Ingestion Logs)
          </SectionTitle>
        }
        extra={
          <Space size={16}>
            <span style={{ color: '#52c41a', fontSize: 12 }}>● 运行正常</span>
            <a
              data-testid="ts-view-logs"
              onClick={() => message.info(`查看完整日志${PLACEHOLDER}`)}
            >
              查看完整日志
            </a>
          </Space>
        }
        styles={{ body: { padding: 0 } }}
      >
        <Table<LogRow>
          size="small"
          pagination={false}
          dataSource={LOG_ROWS}
          columns={[
            { title: '时间戳', dataIndex: 'ts', key: 'ts' },
            {
              title: '批次 ID',
              dataIndex: 'batch',
              key: 'batch',
              render: (v: string) => (
                <span style={{ fontFamily: 'monospace' }}>{v}</span>
              ),
            },
            { title: '记录数', dataIndex: 'records', key: 'records' },
            { title: '聚合耗时', dataIndex: 'cost', key: 'cost' },
            {
              title: '状态',
              dataIndex: 'status',
              key: 'status',
              render: (status: LogStatus) => (
                <Tag color={status === 'SUCCESS' ? 'green' : 'red'}>
                  {status}
                </Tag>
              ),
            },
          ]}
        />
      </Card>
    </PageContainer>
  );
};

/** 卡片小节标题(竖条 + 文字),竖条与图标默认蓝色,可覆盖。 */
const SectionTitle: React.FC<{
  icon: ReactNode;
  iconColor?: string;
  children: string;
}> = ({ icon, iconColor = '#1677ff', children }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
    <span
      style={{
        width: 4,
        height: 18,
        borderRadius: 2,
        background: iconColor,
        display: 'inline-block',
      }}
    />
    <span style={{ color: iconColor }}>{icon}</span>
    <Text strong style={{ fontSize: 16 }}>
      {children}
    </Text>
  </span>
);

/** 活跃节点详情行(标签左 / 值右) */
const DetailRow: React.FC<{
  label: string;
  value: string;
  valueColor?: string;
}> = ({ label, value, valueColor }) => (
  <div
    style={{
      display: 'flex',
      justifyContent: 'space-between',
      gap: 16,
      marginTop: 4,
    }}
  >
    <span style={{ color: '#8c8c8c' }}>{label}</span>
    <span style={{ color: valueColor, fontWeight: 600 }}>{value}</span>
  </div>
);

export default TimeSeriesIngestPage;
