// 算子链芯片:抽自血缘图 job 节点内联展示(原内联在 pages/lineage/index.tsx),
// 供血缘页(job/member 节点)与任务详情「资产清单」共用,保证"哪个算子+什么参数"
// 的展示样式统一。
import { Space, Tag, Tooltip, Typography } from 'antd';

/** 算子参数序列化为可读串 */
export const fmtParams = (p?: Record<string, any>) => {
  if (!p) return '';
  const keys = Object.keys(p);
  if (keys.length === 0) return '';
  return keys.map((k) => `${k}=${JSON.stringify(p[k])}`).join('  ');
};

/** 算子链芯片:每算子一个 Tag(name),Tooltip 显示参数;超 3 个折叠为 +N */
export const OperatorChips: React.FC<{
  ops?: { name: string; params: Record<string, any> }[];
}> = ({ ops }) => {
  if (!ops || ops.length === 0) return null;
  const shown = ops.slice(0, 3);
  const rest = ops.length - shown.length;
  return (
    <Space size={4} wrap>
      {shown.map((o) => {
        const ps = fmtParams(o.params);
        return (
          <Tooltip
            key={`${o.name}-${ps}`}
            title={
              <span style={{ whiteSpace: 'pre-wrap' }}>
                {o.name}
                {ps ? `:\n${ps}` : ':(无参数)'}
              </span>
            }
          >
            <Tag color="cyan" style={{ margin: 0, fontSize: 11 }}>
              {o.name}
            </Tag>
          </Tooltip>
        );
      })}
      {rest > 0 && <Tag style={{ margin: 0, fontSize: 11 }}>+{rest} 算子</Tag>}
    </Space>
  );
};

/** 任务算子展示:多表版本按成员分组渲染算子链(最多显示 2 个成员 + 折叠),
 *  否则回退为不分组的 OperatorChips */
export const JobOps: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => {
  if (!n.memberOperators || n.memberOperators.length === 0) {
    return <OperatorChips ops={n.operators} />;
  }
  const shown = n.memberOperators.slice(0, 2);
  const rest = n.memberOperators.length - shown.length;
  return (
    <Space direction="vertical" size={2} style={{ width: '100%' }}>
      {shown.map((m) => (
        <div
          key={m.memberName}
          style={{ display: 'flex', gap: 4, alignItems: 'center' }}
        >
          <Typography.Text
            type="secondary"
            ellipsis
            style={{ fontSize: 11, maxWidth: 60, flexShrink: 0 }}
          >
            {m.memberName}
          </Typography.Text>
          <OperatorChips ops={m.operators} />
        </div>
      ))}
      {rest > 0 && (
        <Tooltip
          title={
            <span style={{ whiteSpace: 'pre-wrap' }}>
              {n.memberOperators
                .map(
                  (m) =>
                    `${m.memberName}: ${m.operators.map((o) => o.name).join(', ')}`,
                )
                .join('\n')}
            </span>
          }
        >
          <Tag style={{ margin: 0, fontSize: 11, width: 'fit-content' }}>
            +{rest} 成员
          </Tag>
        </Tooltip>
      )}
    </Space>
  );
};

export default OperatorChips;
