import { Alert, Button, Table, Tag } from 'antd';
import { useEffect, useState } from 'react';
import { previewIngestSource } from '@/services/data-platform';

interface Props {
  datasourceId: string;
  extract: DataPlatform.IngestExtract;
  /** table=可勾列;其他=只读预览 */
  mode?: string;
  onColumnsChange?: (cols: string[]) => void;
}

/** 源数据预览:采集配置期无副作用采样渲染。
 * table 模式下列头 checkbox 默认全选,onColumnsChange 按预览列顺序回传选中列名。
 * 非 table 模式只读。预览失败显示 Alert + 重试。
 */
export function SourcePreview({
  datasourceId,
  extract,
  mode = 'table',
  onColumnsChange,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] =
    useState<DataPlatform.IngestSourcePreview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);

  const fetchPreview = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await previewIngestSource({ datasourceId, extract });
      setPreview(res.data);
      const all = (res.data.columns || []).map((c) => c.name);
      setSelected(all);
      onColumnsChange?.(all);
    } catch (e: any) {
      setError(e?.data?.message ?? e?.message ?? '预览失败');
    } finally {
      setLoading(false);
    }
  };

  // 依赖 datasourceId + extract 的 JSON 快照(对象引用每次 render 都会变)
  const extractKey = JSON.stringify(extract);
  useEffect(() => {
    fetchPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasourceId, extractKey]);

  const toggle = (name: string) => {
    const next = selected.includes(name)
      ? selected.filter((c) => c !== name)
      : [...selected, name];
    // 保持预览列顺序(而非勾选顺序)
    const ordered = (preview?.columns || [])
      .map((c) => c.name)
      .filter((c) => next.includes(c));
    setSelected(ordered);
    onColumnsChange?.(ordered);
  };

  if (error) {
    return (
      <Alert
        type="error"
        title="预览失败"
        description={error}
        action={
          <Button size="small" onClick={fetchPreview}>
            重试
          </Button>
        }
      />
    );
  }

  const selectable = mode === 'table';
  const columns = (preview?.columns || []).map((c) => ({
    title: selectable ? (
      <label>
        <input
          type="checkbox"
          checked={selected.includes(c.name)}
          aria-label={c.name}
          onChange={() => toggle(c.name)}
        />{' '}
        {c.name} <Tag>{c.type}</Tag>
      </label>
    ) : (
      <span>
        {c.name} <Tag>{c.type}</Tag>
      </span>
    ),
    dataIndex: c.name,
  }));

  return (
    <div>
      {preview?.truncated && (
        <Alert
          type="info"
          showIcon
          title={`仅展示前 50 行(取样自 ${preview.sampledFrom})`}
          style={{ marginBottom: 8 }}
        />
      )}
      <Table
        size="small"
        loading={loading}
        rowKey="__rk"
        columns={columns}
        dataSource={(preview?.rows || []).map((r, i) => ({
          ...r,
          __rk: String(i),
        }))}
        scroll={{ x: 'max-content' }}
        pagination={false}
      />
    </div>
  );
}
