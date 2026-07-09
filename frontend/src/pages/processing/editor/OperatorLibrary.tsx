import { PlusOutlined } from '@ant-design/icons';
import { useDraggable } from '@dnd-kit/core';
import { Button, Input, List, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  getOperatorCatalogMeta,
  listOperatorCatalog,
} from '@/services/data-platform';

const { Text } = Typography;

/** 拖拽 id 前缀,供外层 PipelineDndArea 区分「来自算子库」与「流水线内部排序」。 */
const DRAG_ID_PREFIX = 'op:';

/** 单条算子:内容区可拖拽(拖入右侧流水线区即 append),「+」按钮保留点击加入。 */
const OperatorItem: React.FC<{
  op: DataPlatform.CatalogOperator;
  onAdd: (name: string) => void;
}> = ({ op, onAdd }) => {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `${DRAG_ID_PREFIX}${op.name}`,
  });
  return (
    <List.Item
      actions={[
        <Button
          key="add"
          type="text"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => onAdd(op.name)}
        />,
      ]}
    >
      <div
        ref={setNodeRef}
        {...attributes}
        {...listeners}
        style={{ flex: 1, cursor: 'grab', opacity: isDragging ? 0.4 : 1 }}
      >
        <List.Item.Meta
          title={<Text style={{ fontSize: 13 }}>{op.zhLabel}</Text>}
          description={
            <Text
              type="secondary"
              style={{ fontSize: 11, fontFamily: 'monospace' }}
            >
              {op.name}
            </Text>
          }
        />
      </div>
      {op.runnable !== 'ready' && <Tag>{op.runnable}</Tag>}
    </List.Item>
  );
};

/** 左栏:检索/场景,点 + 添加算子到流水线。默认展示全量算子目录,不按业务桶或
 *  可运行状态过滤(各任务均可自由选用任意算子)。
 *  传 ``bucket``(cleansing/distillation/make/augment/trainset)或 ``hideScenario`` 时
 *  隐藏自由「场景」下拉(任务已限定算子集,无需再按场景浏览);
 *  ``restrictToBucket`` 为真时进一步只拉该 bucket 的算子(数据合成:只列生成类算子)。 */
const OperatorLibrary: React.FC<{
  onAdd: (name: string) => void;
  category?: string;
  bucket?: string;
  restrictToBucket?: boolean;
  /** 隐藏自由「场景」下拉(任务已限定算子集、无需按场景浏览时用) */
  hideScenario?: boolean;
}> = ({ onAdd, category, bucket, restrictToBucket, hideScenario }) => {
  const [scenarios, setScenarios] = useState<Record<string, number>>({});
  const [scenario, setScenario] = useState<string>();
  const [keyword, setKeyword] = useState<string>();
  const [data, setData] = useState<DataPlatform.CatalogOperator[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getOperatorCatalogMeta().then((r) => setScenarios(r.data.byScenario ?? {}));
  }, []);

  useEffect(() => {
    setLoading(true);
    listOperatorCatalog({
      category,
      scenario,
      keyword,
      bucket: restrictToBucket ? bucket : undefined,
      current: 1,
      pageSize: 200,
    })
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, [category, scenario, keyword, restrictToBucket, bucket]);

  const scenarioOptions = useMemo(
    () =>
      Object.entries(scenarios)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => ({ label: `${name} (${count})`, value: name })),
    [scenarios],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Space direction="vertical" style={{ width: '100%', marginBottom: 8 }}>
        <Input.Search
          allowClear
          placeholder="搜索算子"
          onSearch={(v) => setKeyword(v || undefined)}
        />
        {!bucket && !hideScenario && (
          <Select
            allowClear
            placeholder="全部场景"
            style={{ width: '100%' }}
            options={scenarioOptions}
            value={scenario}
            onChange={setScenario}
          />
        )}
      </Space>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <List
          loading={loading}
          size="small"
          dataSource={data}
          renderItem={(op) => (
            <OperatorItem key={op.name} op={op} onAdd={onAdd} />
          )}
        />
      </div>
    </div>
  );
};

export default OperatorLibrary;
