import { PlusOutlined } from '@ant-design/icons';
import { Button, Input, List, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  getOperatorCatalogMeta,
  listOperatorCatalog,
} from '@/services/data-platform';

const { Text } = Typography;

/** 左栏:检索/场景,点 + 添加算子到流水线。展示全量算子目录,不按业务桶或
 *  可运行状态过滤(各任务均可自由选用任意算子)。
 *  传 ``bucket``(cleansing/distillation/make/augment)时隐藏自由「场景」下拉
 *  (加工页不传 bucket → 展示全部算子)。 */
const OperatorLibrary: React.FC<{
  onAdd: (name: string) => void;
  category?: string;
  bucket?: string;
}> = ({ onAdd, category, bucket }) => {
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
      current: 1,
      pageSize: 200,
    })
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, [category, scenario, keyword]);

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
        {!bucket && (
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
              {op.runnable !== 'ready' && <Tag>{op.runnable}</Tag>}
            </List.Item>
          )}
        />
      </div>
    </div>
  );
};

export default OperatorLibrary;
