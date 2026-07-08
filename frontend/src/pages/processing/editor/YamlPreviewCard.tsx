// YAML 预览/编辑卡片:清洗/质量/蒸馏/增强四个编辑器共用。默认展示由算子链算出的
// YAML;用户直接编辑文本框后点「应用」才解析回算子链(手动应用,非实时双向同步,
// 避免打字过程中因 YAML 语法未闭合而不断报错/闪烁)。
import { Button, Card, Input, message, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';

const { Text } = Typography;

export type YamlPreviewCardProps = {
  /** 当前算子链渲染出的规范 YAML;未编辑(或刚应用完)时原样展示 */
  computedYaml: string;
  /** 解析并应用编辑后的 YAML 文本;解析/校验失败请 throw Error(message 会展示给用户) */
  onApply: (yamlText: string) => void;
  /** 切换编排对象(如成员/文件、数据集版本)时传入变化的 key,借此清空未提交的草稿,
   *  避免把上一个对象的草稿误用到新对象上 */
  resetKey?: string;
  /** 为真时额外提示 text_keys 字段也会生效(蒸馏/增强场景支持文本字段回填) */
  supportsTextKeys?: boolean;
};

const YamlPreviewCard: React.FC<YamlPreviewCardProps> = ({
  computedYaml,
  onApply,
  resetKey,
  supportsTextKeys,
}) => {
  const [draft, setDraft] = useState<string | null>(null);

  // 编排对象变化(如切换文件/版本):草稿只对应上一个对象,清空回到「跟随预览」状态
  useEffect(() => {
    setDraft(null);
  }, [resetKey]);

  const dirty = draft !== null;
  const displayValue = draft ?? computedYaml;

  const handleApply = () => {
    try {
      onApply(displayValue);
      setDraft(null);
      message.success('已应用 YAML 编排');
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <Card
      title="YAML 预览"
      size="small"
      extra={
        <Space>
          {dirty && (
            <Button size="small" onClick={() => setDraft(null)}>
              还原
            </Button>
          )}
          <Button size="small" type="primary" onClick={handleApply}>
            应用
          </Button>
        </Space>
      }
    >
      <Input.TextArea
        value={displayValue}
        onChange={(e) => setDraft(e.target.value)}
        autoSize={{ minRows: 6, maxRows: 24 }}
        spellCheck={false}
        style={{ fontFamily: 'monospace', fontSize: 12 }}
      />
      <Text type="secondary" style={{ fontSize: 12 }}>
        仅 process{supportsTextKeys ? ' / text_keys' : ''}{' '}
        字段会生效(算子名须在算子目录中已注册);dataset_path
        等路径字段与注释仅供预览,编辑不生效。
      </Text>
    </Card>
  );
};

export default YamlPreviewCard;
