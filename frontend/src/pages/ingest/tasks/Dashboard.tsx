// 采集任务概览 dashboard(页顶,运维 cockpit)。
// 一排 KPI 卡(总任务/运行中/近24h完成/成功率/失败) + 三图(数据源类型×状态堆叠柱 / 近14天趋势 / 状态分布环形)。
// 数据来自 GET /ingest-tasks/stats(见 services/data-platform);随列表一同刷新(含 5s 轮询)。
import { Column, Line, Pie } from '@ant-design/plots';
import { Card, Col, Progress, Row, Statistic, Typography } from 'antd';
import { useMemo } from 'react';
import { STATE_META } from '@/utils/jobState';

/** /ingest-tasks/stats 响应(与后端 IngestTaskStats / 前端 service 对齐)。 */
export interface IngestTaskStatsData {
  total: number;
  byState: Record<string, number>;
  byDsTypeState: { dsType: string; state: string; count: number }[];
  completedLast24h: number;
  avgDurationSec: number | null;
  trend14d: {
    date: string;
    created: number;
    success: number;
    failed: number;
  }[];
}

// 数据源类型中文标签(与 datasources 页面一致)。
const DS_TYPE_LABEL: Record<string, string> = {
  database: '数据库',
  s3: '对象存储',
  hdfs: 'HDFS',
  api: 'API',
};

// 采集任务实际只走 4 个状态(pending/running/success/failed),其余 2 个留空,
// legend/分桶不会出现 paused/cancelled,但 STATE_HEX 全量定义以便后续扩展。
const STATE_ORDER = [
  'pending',
  'running',
  'paused',
  'success',
  'failed',
  'cancelled',
] as const;
const STATE_HEX: Record<string, string> = {
  pending: '#8c8c8c',
  running: '#1677ff',
  paused: '#faad14',
  success: '#52c41a',
  failed: '#ff4d4f',
  cancelled: '#fa8c16',
};
const STATE_LABELS = STATE_ORDER.map((s) => STATE_META[s].text);
const STATE_RANGE = STATE_ORDER.map((s) => STATE_HEX[s]);

const TREND_SERIES = ['创建', '成功', '失败'];
const TREND_RANGE = ['#8c8c8c', '#52c41a', '#ff4d4f'];

const INFO = 'var(--ant-color-info, #1677ff)';
const SUCCESS = 'var(--ant-color-success, #52c41a)';
const ERR = 'var(--ant-color-error, #ff4d4f)';

/** 秒 → 紧凑中文时长;空值显示「—」。 */
const fmtDuration = (sec?: number | null): string => {
  if (sec == null) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  const h = Math.floor(m / 60);
  return `${h} 时 ${m % 60} 分`;
};

/** "YYYY-MM-DD" → "M/D"(趋势 X 轴短标签)。 */
const shortDate = (iso: string): string => {
  const [, m, d] = iso.split('-');
  return `${Number(m)}/${Number(d)}`;
};

interface Props {
  stats?: IngestTaskStatsData;
}

const IngestTasksDashboard: React.FC<Props> = ({ stats }) => {
  const by = stats?.byState ?? {};
  const total = stats?.total ?? 0;
  const running = by.running ?? 0;
  const pending = by.pending ?? 0;
  const success = by.success ?? 0;
  const failed = by.failed ?? 0;
  const terminal = success + failed;
  const successRate = terminal ? (success / terminal) * 100 : null;

  // 数据源类型 × 状态(堆叠柱):后端只回非零组合,直接映射中文标签即可。
  const dsTypeStateData = useMemo(
    () =>
      (stats?.byDsTypeState ?? []).map((r) => ({
        dsTypeLabel: DS_TYPE_LABEL[r.dsType] ?? r.dsType,
        stateLabel:
          STATE_META[r.state as keyof typeof STATE_META]?.text ?? r.state,
        count: r.count,
      })),
    [stats?.byDsTypeState],
  );

  // 状态分布(环形):只取非零阶段,legend 不被空项占满。
  const stateData = useMemo(
    () =>
      STATE_ORDER.map((s) => ({
        stateLabel: STATE_META[s].text,
        count: by[s] ?? 0,
      })).filter((d) => d.count > 0),
    [by],
  );

  // 近 14 天趋势 → 长表(每天 3 条:创建/成功/失败)。
  const trendData = useMemo(
    () =>
      (stats?.trend14d ?? []).flatMap((p) => [
        { date: shortDate(p.date), series: '创建', value: p.created },
        { date: shortDate(p.date), series: '成功', value: p.success },
        { date: shortDate(p.date), series: '失败', value: p.failed },
      ]),
    [stats?.trend14d],
  );

  const stateScale = { color: { domain: STATE_LABELS, range: STATE_RANGE } };

  return (
    <div style={{ marginBottom: 16 }}>
      {/* KPI strip */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col flex="1 1 160px">
          <Card size="small">
            <Statistic title="采集任务" value={total} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已完成 {success}
            </Typography.Text>
          </Card>
        </Col>
        <Col flex="1 1 160px">
          <Card size="small">
            <Statistic
              title="运行中"
              value={running}
              styles={{ content: { color: INFO } }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              待运行 {pending}
            </Typography.Text>
          </Card>
        </Col>
        <Col flex="1 1 160px">
          <Card size="small">
            <Statistic
              title="近 24h 完成"
              value={stats?.completedLast24h ?? 0}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              平均时长 {fmtDuration(stats?.avgDurationSec)}
            </Typography.Text>
          </Card>
        </Col>
        <Col flex="1 1 160px">
          <Card size="small">
            <Statistic
              title="成功率"
              value={successRate == null ? '—' : successRate}
              precision={successRate == null ? undefined : 1}
              suffix={successRate == null ? undefined : '%'}
              styles={{ content: { color: SUCCESS } }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Progress
                percent={successRate ?? 0}
                showInfo={false}
                size="small"
                strokeColor={STATE_HEX.success}
                style={{ flex: 1, minWidth: 0, marginBottom: 0 }}
              />
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, whiteSpace: 'nowrap' }}
              >
                成功 {success} · 失败 {failed}
              </Typography.Text>
            </div>
          </Card>
        </Col>
        <Col flex="1 1 160px">
          <Card size="small">
            <Statistic
              title="失败"
              value={failed}
              styles={{ content: { color: ERR } }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              占比 {total ? ((failed / total) * 100).toFixed(1) : '0.0'}%
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      {/* 图表行 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card size="small" title="各数据源类型 × 状态分布">
            <Column
              data={dsTypeStateData}
              xField="dsTypeLabel"
              yField="count"
              colorField="stateLabel"
              stack
              height={240}
              autoFit
              scale={stateScale}
              legend={{ color: { position: 'bottom' } }}
              animate={false}
            />
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card size="small" title="近 14 天采集运行趋势">
            <Line
              data={trendData}
              xField="date"
              yField="value"
              colorField="series"
              height={240}
              autoFit
              scale={{ color: { domain: TREND_SERIES, range: TREND_RANGE } }}
              legend={{ color: { position: 'bottom' } }}
              animate={false}
            />
          </Card>
        </Col>
        <Col xs={24} lg={6}>
          <Card size="small" title="状态分布">
            <Pie
              data={stateData}
              angleField="count"
              colorField="stateLabel"
              innerRadius={0.6}
              height={240}
              autoFit
              scale={stateScale}
              legend={{ color: { position: 'bottom' } }}
              label={false}
              animate={false}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default IngestTasksDashboard;
