import type { TablePaginationConfig } from 'antd';
import { Empty, Table } from 'antd';

/** 各类型数据视图的统一入参:版本预览(列 + 行 + 总数)。
 *  pagination:透传给内部 Table;不传保持默认前端分页(pageSize 20),
 *  传 false 关闭(由外层做服务端分页,如 VersionFilePreview)。 */
export type DataViewProps = {
  preview?: DataPlatform.DatasetPreview;
  pagination?: TablePaginationConfig | false;
};

/** 单元格值渲染:对象转 JSON,其余转字符串。 */
export const cellText = (v: unknown): string =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

/** 通用扁平表格视图(默认 / text / structured / 未知语义类型)。 */
export const TableView: React.FC<DataViewProps> = ({ preview, pagination }) => {
  if (!preview || preview.data.length === 0) {
    return (
      <Empty
        description={preview?.message ?? '暂无数据'}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }
  const columns = (preview.columns ?? []).map((c) => ({
    title: c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: unknown) => cellText(v),
  }));
  return (
    <Table
      rowKey={(_, i) => String(i)}
      size="small"
      scroll={{ x: 'max-content' }}
      pagination={pagination ?? { pageSize: 20 }}
      dataSource={preview.data}
      columns={columns}
    />
  );
};

/** 按语义类型分发到对应数据视图;未命中回退通用表格。
 *  专用视图(cot/qa/preference/timeseries/gis/multimodal)由各自文件提供,
 *  接入后在此 switch 中路由。 */
const DatasetDataView: React.FC<
  DataViewProps & { semanticType?: string | null }
> = ({ semanticType, preview, pagination }) => {
  switch (semanticType) {
    default:
      return <TableView preview={preview} pagination={pagination} />;
  }
};

export default DatasetDataView;
