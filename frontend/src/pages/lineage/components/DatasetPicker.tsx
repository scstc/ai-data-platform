// 数据血缘的数据集选择器:按钮触发 Drawer,内含搜索 + 分类筛选 + 富信息列表 + 翻页。
// 替换原 PageContainer.header 里那个 380px 宽的 antd Select —— 后者只能显示名称,
// 在 200+ 数据集下既难翻页也难区分(同名前缀、来源不同等)。Drawer 内每项展示三行:
// 名称 + 来源·格式·分类 + 最新版本·创建人·创建时间,选中即关 Drawer 并触发 onChange。
import {
  ApiOutlined,
  CaretRightOutlined,
  CloudOutlined,
  DatabaseOutlined,
  HddOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  Button,
  Drawer,
  Empty,
  Input,
  Pagination,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { getDataset, listCategories, listDatasets } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

// 来源 → 中文标签 + 图标。后端 sourceKind 自由串,未识别时退化为原始值。

/** 防抖包装:返回 debounce 后的函数 + cancel 方法(避免引入 lodash)。 */
function debounce<T extends (...args: any[]) => void>(fn: T, ms: number) {
  let t: ReturnType<typeof setTimeout> | undefined;
  const wrapped = (...args: Parameters<T>) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => {
    if (t) clearTimeout(t);
  };
  return wrapped as T & { cancel: () => void };
}

const SOURCE_META: Record<
  string,
  { label: string; icon: React.ReactNode; color: string }
> = {
  database: { label: '数据库', icon: <DatabaseOutlined />, color: 'blue' },
  object_store: { label: '对象存储', icon: <CloudOutlined />, color: 'cyan' },
  hdfs: { label: 'HDFS', icon: <HddOutlined />, color: 'purple' },
  local_upload: { label: '本地上传', icon: <UploadOutlined />, color: 'gold' },
  api_push: { label: 'API', icon: <ApiOutlined />, color: 'magenta' },
};

interface Props {
  value?: string;
  onChange: (id: string) => void;
}

const DatasetPicker: React.FC<Props> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [selected, setSelected] = useState<DataPlatform.Dataset | undefined>();
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [current, setCurrent] = useState(1);
  const [pageSize] = useState(10);
  const [searchInput, setSearchInput] = useState(''); // 输入框即时值
  const [search, setSearch] = useState(''); // 实际查询值(debounce 后)
  const [sourceKind, setSourceKind] = useState<string | undefined>();
  const [categoryId, setCategoryId] = useState<string | undefined>();
  const [categories, setCategories] = useState<DataPlatform.Category[]>([]);

  // 防抖:输入停顿 300ms 后再更新查询(避免每键击都打一次接口)
  const debouncedSetSearch = useMemo(
    () => debounce((v: string) => setSearch(v.trim()), 300),
    [],
  );
  useEffect(() => () => debouncedSetSearch.cancel(), [debouncedSetSearch]);

  // 打开 Drawer 时拉分类;选中项未带过来时也补一次详情
  useEffect(() => {
    if (!open) return;
    listCategories()
      .then((res) => setCategories(res?.data ?? []))
      .catch(() => setCategories([]));
  }, [open]);

  // 选中值变化时同步 selected(可能在外部切换后 Drawer 还没打开过):
  // 列表里命中直接复用,否则拉一次详情(轻量)。
  useEffect(() => {
    if (!value) {
      setSelected(undefined);
      return;
    }
    if (selected?.id === value) return;
    const fromList = datasets.find((d) => d.id === value);
    if (fromList) {
      setSelected(fromList);
      return;
    }
    let cancelled = false;
    getDataset(value)
      .then((res) => {
        if (!cancelled && res?.data) setSelected(res.data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [value, datasets, selected?.id]);

  // 拉列表(search/sourceKind/categoryId/current 变化时)
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    listDatasets({
      current,
      pageSize,
      name: search || undefined,
      sourceKind,
      categoryId,
    })
      .then((res) => {
        setDatasets(res.data ?? []);
        setTotal(res.total ?? 0);
        if (value) {
          const hit = (res.data ?? []).find((d) => d.id === value);
          if (hit) setSelected(hit);
        }
        setLoading(false);
      })
      .catch(() => {
        setDatasets([]);
        setTotal(0);
        setLoading(false);
      });
  }, [open, search, sourceKind, categoryId, current, pageSize, value]);

  const reset = () => {
    setSearchInput('');
    setSearch('');
    setSourceKind(undefined);
    setCategoryId(undefined);
    setCurrent(1);
  };

  const pick = (d: DataPlatform.Dataset) => {
    setSelected(d);
    onChange(d.id);
    setOpen(false);
  };

  // 来源 Segmented 选项(预定义 5 种 + "全部")
  const sourceOptions = [
    { label: '全部来源', value: '' },
    ...Object.entries(SOURCE_META).map(([v, m]) => ({
      label: (
        <Space size={4}>
          {m.icon}
          {m.label}
        </Space>
      ),
      value: v,
    })),
  ];

  return (
    <>
      <Tooltip title="点击更换数据集">
        <Button
          size="large"
          onClick={() => setOpen(true)}
          style={{
            width: 'fit-content',
            minWidth: 360,
            maxWidth: 520,
            height: 'auto',
            padding: '6px 12px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: 2,
          }}
        >
          {selected ? (
            <>
              <Space size={6} style={{ width: '100%' }}>
                <Typography.Text strong ellipsis style={{ fontSize: 14 }}>
                  {selected.name}
                </Typography.Text>
                {selected.hosted && (
                  <Tag color="cyan" style={{ margin: 0 }}>
                    S3 托管
                  </Tag>
                )}
              </Space>
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, lineHeight: 1.4 }}
              >
                {SOURCE_META[selected.sourceKind ?? '']?.label ??
                  selected.sourceKind ??
                  '—'}
                {selected.sourceFormat && ` · ${selected.sourceFormat}`}
                {selected.categoryName && ` · ${selected.categoryName}`}
                {selected.latestVersionLabel &&
                  ` · ${selected.latestVersionLabel}`}
              </Typography.Text>
            </>
          ) : (
            <Typography.Text type="secondary">
              选择数据集查看其版本与血缘
            </Typography.Text>
          )}
        </Button>
      </Tooltip>

      <Drawer
        title="选择数据集"
        placement="right"
        width={560}
        open={open}
        onClose={() => setOpen(false)}
        destroyOnHidden
        styles={{ body: { padding: 0 } }}
      >
        {/* 筛选条 */}
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--ant-color-border-secondary)',
            background: 'var(--ant-color-fill-quaternary)',
          }}
        >
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索名称 / 描述 / 分类"
            value={searchInput}
            onChange={(e) => {
              setSearchInput(e.target.value);
              debouncedSetSearch(e.target.value);
              setCurrent(1);
            }}
            style={{ marginBottom: 8 }}
          />
          <Space size={8} wrap style={{ width: '100%' }}>
            <Select
              placeholder="全部分类"
              allowClear
              value={categoryId}
              onChange={(v) => {
                setCategoryId(v);
                setCurrent(1);
              }}
              style={{ minWidth: 140, flex: 1 }}
              options={categories.map((c) => ({
                label: c.name,
                value: c.id,
              }))}
              showSearch
              optionFilterProp="label"
            />
            <Button size="small" onClick={reset}>
              重置
            </Button>
          </Space>
          <Segmented
            block
            size="small"
            value={sourceKind ?? ''}
            onChange={(v) => {
              setSourceKind(v || undefined);
              setCurrent(1);
            }}
            options={sourceOptions}
            style={{ marginTop: 8 }}
          />
        </div>

        {/* 列表 */}
        <div
          style={{
            padding: '8px 16px',
            minHeight: 320,
            maxHeight: 'calc(100vh - 280px)',
            overflowY: 'auto',
          }}
        >
          {loading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : datasets.length === 0 ? (
            <Empty description="无匹配数据集" style={{ marginTop: 48 }} />
          ) : (
            datasets.map((d) => {
              const sm = SOURCE_META[d.sourceKind ?? ''];
              const isSelected = d.id === selected?.id;
              return (
                <div
                  key={d.id}
                  onClick={() => pick(d)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') pick(d);
                  }}
                  style={{
                    cursor: 'pointer',
                    padding: '10px 12px',
                    marginBottom: 6,
                    borderRadius: 6,
                    border: `1px solid ${
                      isSelected
                        ? 'var(--ant-color-primary)'
                        : 'var(--ant-color-border-secondary)'
                    }`,
                    background: isSelected
                      ? 'var(--ant-color-primary-bg)'
                      : 'var(--ant-color-bg-container)',
                    transition: 'background 120ms',
                  }}
                >
                  <Space size={6} style={{ width: '100%' }} wrap>
                    <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
                      {d.name}
                    </Typography.Text>
                    {d.hosted && (
                      <Tag color="cyan" style={{ margin: 0 }}>
                        S3 托管
                      </Tag>
                    )}
                    <CaretRightOutlined
                      style={{
                        marginLeft: 'auto',
                        color: 'var(--ant-color-text-tertiary)',
                        fontSize: 12,
                      }}
                    />
                  </Space>
                  <div style={{ marginTop: 2 }}>
                    <Space size={4} wrap>
                      {sm ? (
                        <Tag
                          color={sm.color}
                          icon={sm.icon}
                          style={{ margin: 0 }}
                        >
                          {sm.label}
                        </Tag>
                      ) : (
                        d.sourceKind && (
                          <Tag style={{ margin: 0 }}>{d.sourceKind}</Tag>
                        )
                      )}
                      {d.sourceFormat && (
                        <Tag style={{ margin: 0 }}>{d.sourceFormat}</Tag>
                      )}
                      {d.categoryName && (
                        <Tag color="blue" style={{ margin: 0 }}>
                          {d.categoryName}
                        </Tag>
                      )}
                    </Space>
                  </div>
                  <Typography.Text
                    type="secondary"
                    style={{ fontSize: 12, marginTop: 4, display: 'block' }}
                  >
                    {d.latestVersionLabel
                      ? `${d.latestVersionLabel} · `
                      : '尚无版本 · '}
                    {d.creator || '—'} · {formatDateTime(d.createdAt)}
                  </Typography.Text>
                </div>
              );
            })
          )}
        </div>

        {/* 分页 */}
        <div
          style={{
            padding: '8px 16px',
            borderTop: '1px solid var(--ant-color-border-secondary)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: 'var(--ant-color-fill-quaternary)',
          }}
        >
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            共 {total} 条
          </Typography.Text>
          <Pagination
            size="small"
            current={current}
            pageSize={pageSize}
            total={total}
            showSizeChanger={false}
            onChange={setCurrent}
          />
        </div>
      </Drawer>
    </>
  );
};

export default DatasetPicker;