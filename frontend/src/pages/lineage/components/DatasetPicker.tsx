// 数据血缘的数据集选择器:按钮触发 Drawer,内含搜索 + 分类筛选 + 富信息列表 + 翻页。
// 替换原 PageContainer.header 里那个 380px 宽的 antd Select —— 后者只能显示名称,
// 在 200+ 数据集下既难翻页也难区分(同名前缀、来源不同等)。Drawer 内每项展示三行:
// 名称 + 来源·格式·分类 + 最新版本·创建人·创建时间,选中即关 Drawer 并触发 onChange。
import {
  CaretRightOutlined,
  DatabaseOutlined,
  DownOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import {
  Button,
  Drawer,
  Empty,
  Input,
  Pagination,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  getDataset,
  listCategories,
  listDatasets,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { SOURCE_KIND_META } from '@/utils/sourceKind';
import { UPLOAD_CHANNEL_META } from '@/utils/uploadChannel';

/** 从完整版本标签 "v2026.6.22 (#7)" 抽出括号里的内部版本号 "7"。 */
const extractVersionNo = (label: string): string => {
  const m = /\(#(\d+)\)/.exec(label);
  return m ? m[1] : label;
};

// 来源 → 中文标签 + 图标,复用 @/utils/sourceKind(三轴 source_kind)与
// @/utils/uploadChannel(湖抽取 source_channels)的统一定义,避免与数据集列表页各自维护漂移。
const SOURCE_META: Record<
  string,
  { label: string; icon: React.ReactNode; color: string }
> = { ...SOURCE_KIND_META, ...UPLOAD_CHANNEL_META };

/** 三轴 sourceKind 缺失时回退湖抽取 sourceChannels 首个渠道(同数据集列表页的回退逻辑)。 */
const resolveSourceMeta = (d?: {
  sourceKind?: string | null;
  sourceChannels?: string[] | null;
}) => {
  const key = d?.sourceKind || d?.sourceChannels?.[0];
  return key ? SOURCE_META[key] : undefined;
};

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

interface Props {
  value?: string;
  onChange: (id: string) => void;
}

const DatasetPicker: React.FC<Props> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
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

  // 来源下拉选项(三轴 source_kind + 湖抽取 source_channels 合并去重,11 种,
  // Segmented 装不下这么多项会挤成一排省略号,改用下拉更稳妥)
  const sourceOptions = Object.entries(SOURCE_META).map(([v, m]) => ({
    label: (
      <Space size={4}>
        {m.icon}
        {m.label}
      </Space>
    ),
    value: v,
  }));

  return (
    <>
      <Tooltip title={selected ? '点击更换数据集' : '点击选择数据集'}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              setOpen(true);
            }
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          style={{
            // 紧凑组合框:前导来源图标 + 两行文字 + 尾部 chevron。
            // 定宽 645,近似对齐左侧版本管理列;minWidth:0 让内层 ellipsis 稳定生效。
            width: 645,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '7px 12px',
            borderRadius: 8,
            cursor: 'pointer',
            background: 'var(--ant-color-bg-container)',
            border: `1px solid ${
              hovered || open
                ? 'var(--ant-color-primary)'
                : 'var(--ant-color-border)'
            }`,
            boxShadow:
              hovered || open
                ? '0 0 0 2px var(--ant-color-primary-bg)'
                : 'none',
            transition: 'border-color 0.2s, box-shadow 0.2s',
          }}
        >
          {/* 前导图标块:按来源着色,给控件一个视觉锚点 */}
          <div
            style={{
              flex: 'none',
              width: 34,
              height: 34,
              borderRadius: 7,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 16,
              background: 'var(--ant-color-primary-bg)',
              color: 'var(--ant-color-primary)',
            }}
          >
            {selected ? (
              (resolveSourceMeta(selected)?.icon ?? <DatabaseOutlined />)
            ) : (
              <DatabaseOutlined />
            )}
          </div>

          {/* 主体两行:名称 + 元信息,均可省略 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {selected ? (
              <>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    width: '100%',
                  }}
                >
                  <Typography.Text
                    strong
                    ellipsis
                    style={{ fontSize: 14, flex: 1, minWidth: 0 }}
                  >
                    {selected.name}
                  </Typography.Text>
                  {selected.hosted && (
                    <Tag
                      color="cyan"
                      style={{ margin: 0, flex: 'none', fontSize: 11 }}
                    >
                      S3 托管
                    </Tag>
                  )}
                </div>
                <Typography.Text
                  type="secondary"
                  ellipsis
                  style={{
                    fontSize: 12,
                    lineHeight: 1.4,
                    width: '100%',
                    display: 'block',
                  }}
                >
                  {resolveSourceMeta(selected)?.label ??
                    selected.sourceKind ??
                    selected.sourceChannels?.[0] ??
                    '—'}
                  {selected.sourceFormat && ` · ${selected.sourceFormat}`}
                  {selected.categoryName && ` · ${selected.categoryName}`}
                  {/* 只显示版本号 #N(去掉日期前缀,避免 sub text 过长) */}
                  {selected.latestVersionLabel &&
                    ` · #${extractVersionNo(selected.latestVersionLabel)}`}
                </Typography.Text>
              </>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                选择数据集查看其版本与血缘
              </Typography.Text>
            )}
          </div>

          {/* 尾部 chevron:明确"可展开"信号 */}
          <DownOutlined
            style={{
              flex: 'none',
              fontSize: 12,
              color: 'var(--ant-color-text-quaternary)',
            }}
          />
        </div>
      </Tooltip>

      <Drawer
        title="选择数据集"
        placement="right"
        width={640}
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
              placeholder="全部来源"
              allowClear
              value={sourceKind}
              onChange={(v) => {
                setSourceKind(v);
                setCurrent(1);
              }}
              style={{ minWidth: 140, flex: 1 }}
              options={sourceOptions}
            />
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
              const sm = resolveSourceMeta(d);
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
                        (d.sourceKind || d.sourceChannels?.[0]) && (
                          <Tag style={{ margin: 0 }}>
                            {d.sourceKind || d.sourceChannels?.[0]}
                          </Tag>
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
