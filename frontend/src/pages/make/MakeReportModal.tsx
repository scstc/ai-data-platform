// 数据合成报告 Modal
import { Alert, Modal, Space, Statistic, Tag, Typography } from 'antd';

interface Props {
  open: boolean;
  jobId?: string;
  report?: DataPlatform.MakeReport;
  onClose: () => void;
}

const MakeReportModal: React.FC<Props> = ({ open, jobId, report, onClose }) => {
  const ratio =
    report?.expansionRatio != null
      ? Number(report.expansionRatio.toFixed(2))
      : null;

  return (
    <Modal
      open={open}
      title={jobId ? `合成报告 · ${jobId}` : '合成报告'}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {!report ? (
        <Alert type="info" showIcon message="暂无报告(任务可能尚未完成)" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Space size={32}>
            <Statistic title="输入条数" value={report.inputCount} />
            <Statistic title="输出条数" value={report.outputCount ?? '-'} />
            <Statistic title="耗时(s)" value={report.elapsedSeconds ?? '-'} />
            <Statistic title="模式" value="合成" />
          </Space>

          {ratio != null && (
            <Typography.Text type="secondary">
              扩增比:&nbsp;
              <Typography.Text strong>
                {ratio}
                {ratio > 1 ? ' ↑(扩增)' : ratio < 1 ? ' ↓(压缩)' : ' = (等量)'}
              </Typography.Text>
            </Typography.Text>
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

export default MakeReportModal;
