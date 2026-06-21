import { DeleteOutlined } from '@ant-design/icons';
import {
  Card,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { listOperatorCatalog } from '@/services/data-platform';

const { Text } = Typography;

type Step = DataPlatform.PipelineStep;

interface Props {
  /** 受控值:算子步骤列表(由外层 Form.Item 注入) */
  value?: Step[];
  onChange?: (v: Step[]) => void;
}

const MEDIA = new Set(['image', 'video', 'audio', 'multimodal']);
// 适合对采集到的结构化/文本记录做过滤清洗的算子族
const FILTER_CATEGORIES = new Set(['filter', 'mapper', 'deduplicator']);

/** 采集落地前的过滤算子选择器:挑可运行的 filter/mapper 类算子 + 编辑参数。
 *
 * 仅暴露当前环境「可运行」且非媒体的算子(媒体算子对文本/结构化采集无意义)。
 * value/onChange 由外层 <Form.Item name={['extract','operators']}> 注入。
 */
const FilterOperatorPicker: React.FC<Props> = ({ value = [], onChange }) => {
  const [catalog, setCatalog] = useState<DataPlatform.CatalogOperator[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await listOperatorCatalog({
          pageSize: 500,
          runnable: 'ready',
        });
        const ops = (res.data ?? []).filter(
          (o) =>
            FILTER_CATEGORIES.has(o.category) &&
            !(o.modality ?? []).some((m) => MEDIA.has(m)),
        );
        if (alive) setCatalog(ops);
      } catch {
        if (alive) setCatalog([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const byName = useMemo(
    () => Object.fromEntries(catalog.map((o) => [o.name, o])),
    [catalog],
  );
  const options = useMemo(
    () =>
      catalog.map((o) => ({
        label: `${o.zhLabel}（${o.name}）`,
        value: o.name,
      })),
    [catalog],
  );

  const selectedNames = value.map((s) => s.name);

  // 选择变化:保留已配参数,新增的参数空对象,顺序按选择顺序
  const onSelect = (names: string[]) => {
    onChange?.(
      names.map(
        (n) => value.find((s) => s.name === n) ?? { name: n, params: {} },
      ),
    );
  };

  const setParam = (idx: number, key: string, val: unknown) => {
    onChange?.(
      value.map((s, i) =>
        i === idx ? { ...s, params: { ...s.params, [key]: val } } : s,
      ),
    );
  };

  const renderParamField = (
    idx: number,
    p: DataPlatform.CatalogParam,
    cur: Record<string, unknown>,
  ) => {
    const t = (p.type ?? '').toLowerCase();
    const v = cur[p.name];
    if (t.includes('bool')) {
      return (
        <Switch
          size="small"
          checked={!!v}
          onChange={(checked) => setParam(idx, p.name, checked)}
        />
      );
    }
    if (t.includes('int') || t.includes('float')) {
      return (
        <InputNumber
          size="small"
          style={{ width: 160 }}
          value={v as number}
          placeholder={p.default ?? ''}
          onChange={(num) => setParam(idx, p.name, num)}
        />
      );
    }
    return (
      <Input
        size="small"
        style={{ width: 220 }}
        value={v as string}
        placeholder={p.default ?? ''}
        onChange={(e) => setParam(idx, p.name, e.target.value || undefined)}
      />
    );
  };

  return (
    <div>
      <Select
        mode="multiple"
        allowClear
        showSearch
        optionFilterProp="label"
        style={{ width: '100%' }}
        placeholder="选择落地前要应用的过滤/清洗算子(仅显示当前环境可运行的)"
        value={selectedNames}
        options={options}
        onChange={onSelect}
      />
      {value.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="未选择算子(采集到的数据将原样落地)"
          style={{ margin: '12px 0' }}
        />
      ) : (
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          {value.map((step, idx) => {
            const meta = byName[step.name];
            const params = meta?.params ?? [];
            return (
              <Card
                key={step.name}
                size="small"
                title={
                  <Space>
                    <Text strong>{meta?.zhLabel ?? step.name}</Text>
                    <Text
                      type="secondary"
                      style={{ fontSize: 12, fontFamily: 'monospace' }}
                    >
                      {step.name}
                    </Text>
                  </Space>
                }
                extra={
                  <DeleteOutlined
                    style={{ color: '#ff4d4f', cursor: 'pointer' }}
                    onClick={() =>
                      onChange?.(value.filter((_, i) => i !== idx))
                    }
                  />
                }
              >
                {params.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    无参数
                  </Text>
                ) : (
                  <Space
                    direction="vertical"
                    size={8}
                    style={{ width: '100%' }}
                  >
                    {params
                      .filter((p) => !['args', 'kwargs'].includes(p.name))
                      .map((p) => (
                        <div
                          key={p.name}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 12,
                          }}
                        >
                          <Text
                            style={{ width: 180, fontSize: 12 }}
                            ellipsis={{ tooltip: p.desc }}
                          >
                            {p.name}
                          </Text>
                          {renderParamField(
                            idx,
                            p,
                            step.params as Record<string, unknown>,
                          )}
                        </div>
                      ))}
                  </Space>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default FilterOperatorPicker;
