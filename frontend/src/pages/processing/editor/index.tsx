import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Modal,
  message,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createJob,
  createPipeline,
  getDataset,
  getPipeline,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import OperatorLibrary from './OperatorLibrary';
import PipelineCanvas from './PipelineCanvas';
import PipelineDndArea from './PipelineDndArea';
import StepParamsForm from './StepParamsForm';
import { stepsToYaml } from './yaml';

const { Text, Paragraph } = Typography;

type MemberConfig = {
  operators: DataPlatform.OperatorSpec[];
  textKeys: string[];
};

/** 清洗任务编辑器(按 jobType 建任务)。数据清洗=clean。
 *  作为通用编辑器组件保留,cleaning/editor 渲染 <Editor jobType="clean" ... /> 复用。
 *  统一走成员级配置(dataset-first 架构下单表也是"1 个成员"),不再区分单表/多表两套 UI。 */
const Editor: React.FC<{
  jobType?: string;
  title?: string;
  noun?: string;
  redirectHref?: string;
  /** 业务桶:清洗页传 "cleansing" 锁定算子库;不传 → 展示全部算子 */
  bucket?: string;
  /** 流水线场景:传入时展示「保存为流水线」;不传(如通用加工页)则不展示 */
  scenario?: DataPlatform.Pipeline['scenario'];
}> = ({
  jobType = 'clean',
  title = '新建清洗任务',
  noun = '清洗',
  redirectHref = '/governance/cleaning',
  bucket,
  scenario,
}) => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false); // 用户改过则不再自动覆盖
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  // 选中版本的列名(供各成员「清洗字段」多选);清洗字段留空=后端自动探测主文本字段
  const [columns, setColumns] = useState<string[]>([]);
  const [versionMembers, setVersionMembers] = useState<
    DataPlatform.DatasetTable[]
  >([]);

  // 成员级配置:key = tableName
  const [memberConfigs, setMemberConfigs] = useState<
    Record<string, MemberConfig>
  >({});
  const [activeMember, setActiveMember] = useState<string>();
  // 每个成员独立维护当前选中的流水线步骤下标(供右侧参数表单定位)
  const [memberActiveIdx, setMemberActiveIdx] = useState<
    Record<string, number>
  >({});
  // 每个成员独立维护画布是否存在游离(未接入主链)算子节点,提交/保存前据此阻断
  const [memberHasOrphan, setMemberHasOrphan] = useState<
    Record<string, boolean>
  >({});

  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [submitting, setSubmitting] = useState(false);

  // 算子元信息(供 label/params 渲染):一次取全量(212 ≤ 后端 pageSize 上限 500)
  useEffect(() => {
    listOperatorCatalog({ current: 1, pageSize: 500 }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 }).then((r) =>
      setDatasets(r.data),
    );
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  // 版本变化:拉一条预览取列名,供「清洗字段」多选;并按版本的表成员初始化各自配置
  useEffect(() => {
    setVersionMembers([]);
    setMemberConfigs({});
    setActiveMember(undefined);
    setMemberActiveIdx({});
    setMemberHasOrphan({});
    if (!versionId || !datasetId) {
      setColumns([]);
      return;
    }

    // 获取版本详情（包含成员列表）
    getDataset(datasetId).then((r) => {
      const version = r.data.versions?.find((v) => v.id === versionId);
      const members = version?.tables ?? [];
      setVersionMembers(members);

      const configs: Record<string, MemberConfig> = {};
      for (const m of members) {
        configs[m.tableName] = { operators: [], textKeys: [] };
      }
      setMemberConfigs(configs);
      setActiveMember(members[0]?.tableName);

      // 预览第一个成员获取列名
      previewDatasetVersion(versionId, { limit: 1 })
        .then((r) => setColumns(r.columns ?? []))
        .catch(() => setColumns([]));
    });
  }, [versionId, datasetId]);

  // 从数据集版本表「流程」入口跳入时,按 URL 预选数据集 + 版本;不带参则维持原交互
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  // 流水线预载:URL 带 pipelineId 时,版本成员就绪后把 spec.operators/textKeys
  // 套用到每个成员(仅套用一次;加载失败不阻塞正常编辑)
  const pipelineAppliedRef = useRef(false);
  useEffect(() => {
    const pipelineId = new URLSearchParams(location.search).get('pipelineId');
    if (
      !pipelineId ||
      pipelineAppliedRef.current ||
      versionMembers.length === 0
    ) {
      return;
    }
    pipelineAppliedRef.current = true;
    getPipeline(pipelineId)
      .then((r) => {
        const spec = r.data.spec;
        const operators: DataPlatform.OperatorSpec[] = (
          spec.operators ?? []
        ).map((o) => ({ name: o.name, params: o.params ?? {} }));
        const textKeys = spec.textKeys ?? [];
        setMemberConfigs((prev) => {
          const next = { ...prev };
          for (const m of versionMembers) {
            next[m.tableName] = { operators, textKeys };
          }
          return next;
        });
      })
      .catch(() => message.error('加载流水线失败'));
  }, [versionMembers]);

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  const categoryOf = (n: string) => opMap[n]?.category || '';

  // 自动任务名:数据集变化时重算,用户手动改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = useMemo(
    () => suggestTaskName(selectedDatasetName, '数据清洗'),
    [selectedDatasetName],
  );
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  const selectedVersionLabel = versions.find(
    (v) => v.id === versionId,
  )?.versionLabel;
  // 每个成员各自独立生成 YAML,不拼在一起
  const memberYamlOf = (memberName: string): string => {
    const cfg = memberConfigs[memberName];
    if (!cfg?.operators.length) return '# (未配置算子)';
    const normalizedSteps: DataPlatform.PipelineStep[] = cfg.operators.map(
      (op) => ({ name: op.name, params: op.params ?? {} }),
    );
    return stepsToYaml(normalizedSteps, {
      datasetName: memberName,
      versionLabel: selectedVersionLabel,
      textKeys: cfg.textKeys,
    });
  };

  const setMemberOperators = (
    memberName: string,
    next: DataPlatform.OperatorSpec[],
  ) =>
    setMemberConfigs((prev) => ({
      ...prev,
      [memberName]: {
        ...(prev[memberName] ?? { textKeys: [] }),
        operators: next,
      },
    }));

  // 保存为流水线:取当前激活成员的 {operators, textKeys} 作为 spec,与 scenario 绑定
  const [pipelineModalOpen, setPipelineModalOpen] = useState(false);
  const [pipelineForm] = Form.useForm<{ name: string; description?: string }>();
  const [savingPipeline, setSavingPipeline] = useState(false);

  const openSavePipeline = () => {
    const cfg = activeMember ? memberConfigs[activeMember] : undefined;
    if (!cfg?.operators.length) {
      message.warning('请先为当前成员配置算子');
      return;
    }
    if (activeMember && memberHasOrphan[activeMember]) {
      message.warning('存在未接入流水线的算子');
      return;
    }
    setPipelineModalOpen(true);
  };

  const onSavePipeline = async (values: {
    name: string;
    description?: string;
  }) => {
    if (!activeMember || !scenario) return;
    const cfg = memberConfigs[activeMember];
    setSavingPipeline(true);
    try {
      await createPipeline({
        name: values.name,
        description: values.description,
        scenario,
        spec: { operators: cfg.operators, textKeys: cfg.textKeys },
      });
      message.success('已保存为流水线');
      setPipelineModalOpen(false);
      pipelineForm.resetFields();
    } finally {
      setSavingPipeline(false);
    }
  };

  const onSubmit = async () => {
    if (!name.trim()) {
      message.warning('请填写任务名');
      return;
    }
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }

    const configs = Object.entries(memberConfigs)
      .filter(([, cfg]) => cfg.operators.length > 0)
      .map(([memberName, cfg]) => ({
        memberName,
        operators: cfg.operators,
        textKeys: cfg.textKeys.length > 0 ? cfg.textKeys : undefined,
      }));

    if (configs.length === 0) {
      message.warning('请至少为一个成员配置算子');
      return;
    }
    if (configs.some((c) => memberHasOrphan[c.memberName])) {
      message.warning('存在未接入流水线的算子');
      return;
    }

    setSubmitting(true);
    try {
      await createJob({
        name,
        type: jobType,
        datasetVersionId: versionId,
        memberConfigs: configs,
        outputMode: 'version',
      });
      message.success(`${noun}任务已创建，正在后台运行`);
      history.push(redirectHref);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title }}
      extra={[
        scenario && (
          <Button key="save-pipeline" onClick={openSavePipeline}>
            保存为流水线
          </Button>
        ),
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          onClick={onSubmit}
        >
          创建任务
        </Button>,
      ]}
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="任务名(自动生成,可编辑)"
          style={{ width: 280 }}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setNameDirty(true);
          }}
        />
        <Select
          placeholder="选择数据集"
          style={{ width: 220 }}
          value={datasetId}
          onChange={(v) => setDatasetId(v)}
          options={datasets.map((d) => ({ label: d.name, value: d.id }))}
        />
        <Select
          placeholder="选择版本"
          style={{ width: 240 }}
          value={versionId}
          onChange={setVersionId}
          options={versions.map((v) => {
            const isBinary = isBinaryFormat(v.format);
            return {
              label: isBinary
                ? `${v.versionLabel}（${v.format}·二进制不可加工）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
      </Space>

      {versionMembers.length === 0 ? (
        <Card size="small">
          <Empty description="请先选择数据集和版本" />
        </Card>
      ) : (
        <Card title="成员级算子配置" size="small">
          <Tabs
            activeKey={activeMember}
            onChange={setActiveMember}
            items={versionMembers.map((m) => ({
              key: m.tableName,
              label: (
                <Space size="small">
                  <Text>{m.tableName}</Text>
                  <Badge
                    count={memberConfigs[m.tableName]?.operators.length || 0}
                    style={{ backgroundColor: '#52c41a' }}
                  />
                </Space>
              ),
              children: (() => {
                const cfg = memberConfigs[m.tableName] ?? {
                  operators: [],
                  textKeys: [],
                };
                const memberSteps: DataPlatform.PipelineStep[] =
                  cfg.operators.map((op) => ({
                    name: op.name,
                    params: op.params ?? {},
                  }));
                const idx = memberActiveIdx[m.tableName] ?? 0;
                const activeStepOfMember = memberSteps[idx];
                const activeOpOfMember = activeStepOfMember
                  ? opMap[activeStepOfMember.name]
                  : undefined;
                const appendOperator = (name: string) =>
                  setMemberOperators(m.tableName, [
                    ...cfg.operators,
                    { name, params: {} },
                  ]);

                return (
                  <Space
                    direction="vertical"
                    style={{ width: '100%' }}
                    size={16}
                  >
                    {/* 该成员的清洗字段选择 */}
                    <Card title="清洗字段（可选）" size="small">
                      <Tooltip title="算子作用的字段;留空则自动探测主文本字段。脏字符不在标准字段(如 task)时在此显式指定。">
                        <Select
                          mode="multiple"
                          allowClear
                          placeholder="选择清洗字段（留空=自动探测）"
                          style={{ width: '100%' }}
                          value={cfg.textKeys}
                          onChange={(vals) => {
                            setMemberConfigs((prev) => ({
                              ...prev,
                              [m.tableName]: { ...cfg, textKeys: vals },
                            }));
                          }}
                          options={columns.map((c) => ({ label: c, value: c }))}
                        />
                      </Tooltip>
                    </Card>

                    <PipelineDndArea
                      steps={memberSteps}
                      labelOf={labelOf}
                      onAppend={appendOperator}
                      onReorder={(from, to) => {
                        const next = [...cfg.operators];
                        const [moved] = next.splice(from, 1);
                        next.splice(to, 0, moved);
                        setMemberOperators(m.tableName, next);
                      }}
                    >
                      <Row gutter={16}>
                        <Col span={5}>
                          <Card
                            title="算子库"
                            size="small"
                            styles={{ body: { height: 440, padding: 12 } }}
                          >
                            <OperatorLibrary
                              onAdd={appendOperator}
                              bucket={bucket}
                            />
                          </Card>
                        </Col>
                        <Col span={13}>
                          <Card
                            title="算子流水线"
                            size="small"
                            styles={{ body: { height: 440, padding: 0 } }}
                          >
                            <PipelineCanvas
                              steps={memberSteps}
                              labelOf={labelOf}
                              categoryOf={categoryOf}
                              activeIdx={idx}
                              onSelect={(i) =>
                                setMemberActiveIdx((prev) => ({
                                  ...prev,
                                  [m.tableName]: i,
                                }))
                              }
                              onRemove={(i) => {
                                setMemberOperators(
                                  m.tableName,
                                  cfg.operators.filter((_, j) => j !== i),
                                );
                                setMemberActiveIdx((prev) => ({
                                  ...prev,
                                  [m.tableName]: 0,
                                }));
                              }}
                              onOrderChange={(perm) => {
                                if (perm.length !== cfg.operators.length) {
                                  setMemberHasOrphan((prev) => ({
                                    ...prev,
                                    [m.tableName]: true,
                                  }));
                                  return;
                                }
                                setMemberHasOrphan((prev) => ({
                                  ...prev,
                                  [m.tableName]: false,
                                }));
                                if (perm.every((v, i) => v === i)) return;
                                setMemberOperators(
                                  m.tableName,
                                  perm.map((i) => cfg.operators[i]),
                                );
                              }}
                              inputLabel={`${selectedVersionLabel ?? '版本'} · ${m.tableName}`}
                              outputLabel="新版本"
                            />
                          </Card>
                        </Col>
                        <Col span={6}>
                          <Card
                            title="参数"
                            size="small"
                            styles={{ body: { height: 440, overflow: 'auto' } }}
                          >
                            <StepParamsForm
                              op={activeOpOfMember}
                              params={activeStepOfMember?.params ?? {}}
                              onChange={(p) =>
                                setMemberOperators(
                                  m.tableName,
                                  cfg.operators.map((op, i) =>
                                    i === idx ? { ...op, params: p } : op,
                                  ),
                                )
                              }
                            />
                          </Card>
                        </Col>
                      </Row>
                    </PipelineDndArea>

                    <Card title="YAML 预览" size="small">
                      <Paragraph>
                        <pre style={{ margin: 0, fontSize: 12 }}>
                          {memberYamlOf(m.tableName)}
                        </pre>
                      </Paragraph>
                    </Card>
                  </Space>
                );
              })(),
            }))}
          />
        </Card>
      )}

      <Card size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          加工产物将作为所选数据集的新版本，可通过历史版本对比追溯加工效果。
          各成员的 YAML 预览见对应 Tab 内；真实配置在创建时由后端生成。
        </Text>
      </Card>

      <Modal
        title="保存为流水线"
        open={pipelineModalOpen}
        onCancel={() => setPipelineModalOpen(false)}
        onOk={() => pipelineForm.submit()}
        confirmLoading={savingPipeline}
        destroyOnHidden
      >
        <Form form={pipelineForm} layout="vertical" onFinish={onSavePipeline}>
          <Form.Item
            name="name"
            label="流水线名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：通用清洗流水线" />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
};

export default Editor;
