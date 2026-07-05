// 编辑器三栏布局:左「算子库」/右「参数」可各自收起成 36px 竖条,画布随之变宽。
// 折叠状态受控(由编辑器顶层持有):processing/editor 的三栏渲染在每个成员 Tab 内,
// 各 Tab 是独立实例,状态放本组件内部会随 Tab 各自为政,故上提到编辑器共享。
// 基准宽度对齐 5/13/6 栅格(5/24≈20.8%、6/24=25%),收起侧释放的宽度全部给画布。
import { DoubleLeftOutlined, DoubleRightOutlined } from '@ant-design/icons';
import { Button, Card } from 'antd';

const PANE_HEIGHT = 440;
const STRIP_WIDTH = 36;

/** 收起后的竖条:展开按钮 + 竖排标题 */
const CollapsedStrip: React.FC<{
  title: string;
  icon: React.ReactNode;
  onExpand: () => void;
}> = ({ title, icon, onExpand }) => (
  <div
    style={{
      flex: `0 0 ${STRIP_WIDTH}px`,
      border: '1px solid var(--ant-color-border)',
      borderRadius: 8,
      background: 'var(--ant-color-fill-quaternary)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      paddingTop: 6,
      gap: 8,
    }}
  >
    <Button
      type="text"
      size="small"
      icon={icon}
      onClick={onExpand}
      title={`展开${title}`}
    />
    <span
      style={{
        writingMode: 'vertical-rl',
        fontSize: 12,
        color: 'var(--ant-color-text-secondary)',
        userSelect: 'none',
      }}
    >
      {title}
    </span>
  </div>
);

const CollapsiblePanes: React.FC<{
  leftTitle: string;
  left: React.ReactNode;
  centerTitle: React.ReactNode;
  center: React.ReactNode;
  rightTitle: string;
  right: React.ReactNode;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  onLeftCollapsedChange: (collapsed: boolean) => void;
  onRightCollapsedChange: (collapsed: boolean) => void;
}> = ({
  leftTitle,
  left,
  centerTitle,
  center,
  rightTitle,
  right,
  leftCollapsed,
  rightCollapsed,
  onLeftCollapsedChange,
  onRightCollapsedChange,
}) => (
  <div style={{ display: 'flex', gap: 16, alignItems: 'stretch' }}>
    {leftCollapsed ? (
      <CollapsedStrip
        title={leftTitle}
        icon={<DoubleRightOutlined />}
        onExpand={() => onLeftCollapsedChange(false)}
      />
    ) : (
      <div style={{ flex: '0 0 20.833%', minWidth: 0 }}>
        <Card
          title={leftTitle}
          size="small"
          extra={
            <Button
              type="text"
              size="small"
              icon={<DoubleLeftOutlined />}
              onClick={() => onLeftCollapsedChange(true)}
              title={`收起${leftTitle}`}
            />
          }
          styles={{ body: { height: PANE_HEIGHT, padding: 12 } }}
        >
          {left}
        </Card>
      </div>
    )}
    <div style={{ flex: '1 1 0', minWidth: 0 }}>
      <Card
        title={centerTitle}
        size="small"
        styles={{ body: { height: PANE_HEIGHT, padding: 0 } }}
      >
        {center}
      </Card>
    </div>
    {rightCollapsed ? (
      <CollapsedStrip
        title={rightTitle}
        icon={<DoubleLeftOutlined />}
        onExpand={() => onRightCollapsedChange(false)}
      />
    ) : (
      <div style={{ flex: '0 0 25%', minWidth: 0 }}>
        <Card
          title={rightTitle}
          size="small"
          extra={
            <Button
              type="text"
              size="small"
              icon={<DoubleRightOutlined />}
              onClick={() => onRightCollapsedChange(true)}
              title={`收起${rightTitle}`}
            />
          }
          styles={{ body: { height: PANE_HEIGHT, overflow: 'auto' } }}
        >
          {right}
        </Card>
      </div>
    )}
  </div>
);

export default CollapsiblePanes;
