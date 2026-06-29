import { Select } from 'antd';
import { useEffect, useState } from 'react';
import { listDatasets } from '@/services/data-platform';

export interface DatasetFilterProps {
  /** 当前选中的数据集 id;undefined = 全部 */
  value?: string;
  /** 选择变化(清空时回传 undefined) */
  onChange: (datasetId?: string) => void;
  style?: React.CSSProperties;
}

/** 数据集筛选下拉:治理/评估各任务列表共用,按数据集过滤任务(输入或产物命中)。
 *  一次拉全量数据集(可搜索);留空=全部。 */
const DatasetFilter: React.FC<DatasetFilterProps> = ({
  value,
  onChange,
  style,
}) => {
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 })
      .then((r) => setDatasets(r.data ?? []))
      .catch(() => setDatasets([]));
  }, []);

  return (
    <Select
      allowClear
      showSearch
      optionFilterProp="label"
      placeholder="按数据集筛选"
      style={{ width: 220, ...style }}
      value={value}
      onChange={(v) => onChange(v || undefined)}
      options={datasets.map((d) => ({ label: d.name, value: d.id }))}
    />
  );
};

export default DatasetFilter;
