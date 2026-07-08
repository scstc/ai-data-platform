// 治理工场:清洗/蒸馏/合成/增强四场景统一入口——场景 Tab + 流水线模板卡片。
// 命名导出 Workbench 供各场景菜单薄入口(cleaning/distillation/make/augment 的 index.tsx)
// 以固定 scenario 挂载;默认导出是路由 ./governance/workbench 本身用的包装,从 URL
// query ?scenario= 读取初始场景(未带参数则展示"全部")。
import { PlusOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess, useSearchParams } from '@umijs/max';
import {
  Card,
  Col,
  Empty,
  message,
  Popconfirm,
  Row,
  Space,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  deletePipeline,
  listOperatorCatalog,
  listPipelines,
} from '@/services/data-platform';

const { Paragraph } = Typography;

type ScenarioKey = 'clean' | 'distillation' | 'augmentation';

type ScenarioMeta = {
  key: ScenarioKey;
  label: string;
  jobsPath: string;
  editorPath: string;
};

/** 场景元信息(与后端 Job.type / Pipeline.scenario 对齐)。
 *  数据合成(改为多文件按行拼接,非算子流水线形态)仍走侧边栏独立菜单
 *  /governance/make;数据增强(LLM 改写,仍是算子流水线形态)已切回工场。 */
const SCENARIOS: ScenarioMeta[] = [
  {
    key: 'clean',
    label: '数据清洗',
    jobsPath: '/governance/cleaning/jobs',
    editorPath: '/governance/cleaning/editor',
  },
  {
    key: 'distillation',
    label: '数据蒸馏',
    jobsPath: '/governance/distillation/jobs',
    editorPath: '/governance/distillation/editor',
  },
  {
    key: 'augmentation',
    label: '数据增强',
    jobsPath: '/governance/augment/jobs',
    editorPath: '/governance/augment/editor',
  },
];

const SCENARIO_MAP: Record<string, ScenarioMeta> = Object.fromEntries(
  SCENARIOS.map((s) => [s.key, s]),
);

const ERR = 'var(--ant-color-error, #ff4d4f)';

/** 场景 → RBAC perm 前缀,门控按当前场景动态取。 */
const PERM_BASE: Record<ScenarioKey, string> = {
  clean: 'governance:cleaning',
  distillation: 'governance:distillation',
  augmentation: 'governance:augment',
};

/** 命名导出:供场景菜单薄入口以固定 scenario 挂载。 */
export const Workbench: React.FC<{ scenario?: string }> = ({ scenario }) => {
  const access = useAccess();
  const [activeKey, setActiveKey] = useState<string>(scenario ?? 'all');
  const [pipelines, setPipelines] = useState<DataPlatform.Pipeline[]>([]);
  const [loading, setLoading] = useState(false);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  useEffect(() => {
    // includeHidden:既有任务/流水线可能引用已隐藏算子,缺元信息无法渲染
    listOperatorCatalog({
      current: 1,
      pageSize: 500,
      includeHidden: true,
    }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  const reload = () => {
    setLoading(true);
    listPipelines({
      scenario: activeKey === 'all' ? undefined : (activeKey as ScenarioKey),
      current: 1,
      pageSize: 100,
    })
      // 场景已退出工场的流水线(如存量 synthesis)无 Tab/编辑器可挂,不展示
      .then((r) =>
        setPipelines((r.data ?? []).filter((p) => SCENARIO_MAP[p.scenario])),
      )
      .finally(() => setLoading(false));
  };

  useEffect(reload, [activeKey]);

  const handleDelete = async (id: string) => {
    try {
      await deletePipeline(id);
      message.success('流水线已删除');
      reload();
    } catch (e: any) {
      message.error(
        e?.info?.errorMessage || e?.data?.message || '删除失败，请重试',
      );
    }
  };

  const labelOf = (name: string) => opMap[name]?.zhLabel || name;

  const activeMeta = SCENARIO_MAP[activeKey];
  const historyHref = activeMeta ? activeMeta.jobsPath : '/ops/data-tasks';

  return (
    <PageContainer header={{ title: '治理工场' }}>
      <Tabs
        activeKey={activeKey}
        onChange={setActiveKey}
        items={[
          { key: 'all', label: '全部' },
          ...SCENARIOS.map((s) => ({ key: s.key, label: s.label })),
        ]}
      />

      <div style={{ marginBottom: 16, textAlign: 'right' }}>
        <a onClick={() => history.push(historyHref)}>历史任务 →</a>
      </div>

      <Row gutter={[16, 16]}>
        {activeMeta && access.hasPerm(`${PERM_BASE[activeMeta.key]}:add`) && (
          <Col span={6}>
            <Card
              hoverable
              style={{ height: '100%', borderStyle: 'dashed' }}
              styles={{ body: { textAlign: 'center', padding: '32px 0' } }}
              onClick={() => history.push(activeMeta.editorPath)}
            >
              <PlusOutlined style={{ fontSize: 24 }} />
              <div style={{ marginTop: 8 }}>新建空白编排</div>
            </Card>
          </Col>
        )}

        {pipelines.map((p) => {
          const meta = SCENARIO_MAP[p.scenario];
          return (
            <Col span={6} key={p.id}>
              <Card
                size="small"
                // 等高卡片:撑满 Col 行高,body 弹性伸展把 actions 钉在底部
                style={{
                  height: '100%',
                  display: 'flex',
                  flexDirection: 'column',
                }}
                styles={{ body: { flex: 1 } }}
                title={
                  <Space>
                    <span>{p.name}</span>
                    {p.isPreset && <Tag color="gold">预置</Tag>}
                  </Space>
                }
                extra={
                  activeKey === 'all' && meta ? (
                    <Tag>{meta.label}</Tag>
                  ) : undefined
                }
                actions={[
                  meta && access.hasPerm(`${PERM_BASE[meta.key]}:edit`) && (
                    <a
                      key="edit"
                      onClick={() =>
                        history.push(`${meta.editorPath}?pipelineId=${p.id}`)
                      }
                    >
                      编辑编排
                    </a>
                  ),
                  meta &&
                    access.hasPerm(`${PERM_BASE[meta.key]}:remove`) &&
                    (p.isPreset ? (
                      <span
                        key="delete"
                        style={{ color: 'var(--ant-color-text-disabled)' }}
                      >
                        删除
                      </span>
                    ) : (
                      <Popconfirm
                        key="delete"
                        title="删除该流水线模板？"
                        okText="删除"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => handleDelete(p.id)}
                      >
                        <a style={{ color: ERR }}>删除</a>
                      </Popconfirm>
                    )),
                ].filter(Boolean)}
              >
                {p.description && (
                  <Paragraph
                    type="secondary"
                    ellipsis={{ rows: 2 }}
                    style={{ marginBottom: 8 }}
                  >
                    {p.description}
                  </Paragraph>
                )}
                <Space size={[4, 4]} wrap>
                  {p.spec.operators.slice(0, 4).map((op, i) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: 算子链顺序即身份,同名算子可重复出现
                    <Tag key={`${op.name}-${i}`}>{labelOf(op.name)}</Tag>
                  ))}
                  {p.spec.operators.length > 4 && (
                    <Tag>+{p.spec.operators.length - 4}</Tag>
                  )}
                </Space>
              </Card>
            </Col>
          );
        })}

        {!loading && pipelines.length === 0 && (
          <Col span={24}>
            <Empty
              description={
                activeMeta
                  ? `暂无「${activeMeta.label}」流水线模板，新建编排后可保存为流水线复用`
                  : '暂无流水线模板'
              }
            />
          </Col>
        )}
      </Row>
    </PageContainer>
  );
};

/** 默认导出:路由 /governance/workbench 本身,从 query ?scenario= 读取初始场景。 */
const WorkbenchPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  return <Workbench scenario={searchParams.get('scenario') ?? undefined} />;
};

export default WorkbenchPage;
