// 蒸馏报告 Modal:输入/输出条数 + 实际保留比例 + 算子链 + warnings
// 数据由父组件(列表页)通过 report 传入,避免重复请求。
import {
  Alert,
  Modal,
  Progress,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd';

interface Props {
  open: boolean;
  jobId?: string;
  report?: DataPlatform.DistillationReport;
  onClose: () => void;
}

const ReportModal: React.FC<Props> = ({ open, jobId, report, onClose }) => {
  const inputCount = report?.inputCount ?? 0;
  const outputCount = report?.outputCount ?? 0;
  const keepPct =
    report?.keepRatioActual != null
      ? Math.round(report.keepRatioActual * 100)
      : null;

  return (
    <Modal
      open={open}
      title={jobId ? `蒸馏报告 · ${jobId}` : '蒸馏报告'}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {!report ? (
        <Alert type="info" showIcon message="暂无报告(任务可能尚未完成)" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Space size={32}>
            <Statistic title="输入条数" value={inputCount} />
            <Statistic title="输出条数" value={outputCount} />
            <Statistic title="耗时(s)" value={report.elapsedSeconds ?? '-'} />
          </Space>

          {keepPct != null && (
            <div>
              <Typography.Text type="secondary">实际保留比例</Typography.Text>
              <Progress
                percent={keepPct}
                status={keepPct === 0 ? 'exception' : 'normal'}
                format={(p) => `${p}%`}
              />
            </div>
          )}

          <div>
            <Typography.Text type="secondary">算子链</Typography.Text>
            <div style={{ marginTop: 4 }}>
              {report.operatorChain.length === 0 ? (
                <Typography.Text type="secondary">-</Typography.Text>
              ) : (
                report.operatorChain.map((name, i) => (
                  <Tag key={`${name}-${i}`} style={{ marginBottom: 4 }}>
                    {i + 1}. {name}
                  </Tag>
                ))
              )}
            </div>
          </div>

          {report.warnings && report.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="警告"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {report.warnings.map((w, i) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: warnings 顺序即身份
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              }
            />
          )}
        </Space>
      )}
    </Modal>
  );
};

export default ReportModal;
