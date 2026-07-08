import { CopyOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  message,
  Select,
  Space,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createJob,
  createPipeline,
  getDataset,
  getJob,
  getPipeline,
  listDatasets,
  listOperatorCatalog,
  updateJob,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import CollapsiblePanes from './CollapsiblePanes';
import OperatorLibrary from './OperatorLibrary';
import PipelineCanvas from './PipelineCanvas';
import PipelineDndArea from './PipelineDndArea';
import StepParamsForm from './StepParamsForm';
import YamlPreviewCard from './YamlPreviewCard';
import { stepsToYaml, yamlToSteps } from './yaml';

const { Text } = Typography;

type MemberConfig = {
  operators: DataPlatform.OperatorSpec[];
};

/** 字节数人类可读,与 data-lakes/detail.tsx 的 formatSize 保持一致 */
const formatSize = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

/** 清洗任务编辑器(按 jobType 建任务)。数据清洗=clean。
 *  作为通用编辑器组件保留,cleaning/editor 渲染 <Editor jobType="clean" ... /> 复用。
 *  编排粒度=文件,master-detail:左侧常驻文件清单(每个文件显示格式/大小/
 *  配置状态,点行切换),右侧画布编排当前文件,所有文件状态一屏可见;
 *  各文件配置按文件名缓存,切文件不丢,行内「复制」可批量套用同一条流水线;
 *  提交时每个已配置文件独立生成 YAML 分别执行,产物与未配置文件(原样结转)
 *  合并为同一个新版本。 */
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
  // 左「算子库」/右「参数」折叠状态:放编辑器顶层,跨成员 Tab 共享
  const [libCollapsed, setLibCollapsed] = useState(false);
  const [paramsCollapsed, setParamsCollapsed] = useState(false);

  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const { token } = theme.useToken();

  // 算子元信息(供 label/params 渲染):一次取全量(212 ≤ 后端 pageSize 上限 500)。
  // includeHidden:编辑既有任务时步骤可能引用已隐藏算子,缺元信息无法渲染
  useEffect(() => {
    listOperatorCatalog({
      current: 1,
      pageSize: 500,
      includeHidden: true,
    }).then((r) => {
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

  // 版本变化:按版本的表成员初始化各自配置
  useEffect(() => {
    setVersionMembers([]);
    setMemberConfigs({});
    setActiveMember(undefined);
    setMemberActiveIdx({});
    setMemberHasOrphan({});
    if (!versionId || !datasetId) return;

    // 获取版本详情（包含成员列表）
    getDataset(datasetId).then((r) => {
      const version = r.data.versions?.find((v) => v.id === versionId);
      const members = version?.tables ?? [];
      setVersionMembers(members);

      const configs: Record<string, MemberConfig> = {};
      for (const m of members) {
        configs[m.tableName] = { operators: [] };
      }
      setMemberConfigs(configs);
      setActiveMember(members[0]?.tableName);
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

  // 编辑模式:URL 带 jobId 时按任务的 editSpec 回填(名称/数据集/版本/成员算子链),
  // 提交改走 updateJob(覆盖原任务配置并原地重跑,不新建记录)
  const editJobId = new URLSearchParams(location.search).get('jobId');
  const [editSpec, setEditSpec] = useState<Record<string, any>>();
  useEffect(() => {
    if (!editJobId) return;
    getJob(editJobId)
      .then((r) => {
        const job = r.data;
        if (!job.editSpec) {
          message.error('该任务无可编辑的配置(早于重跑特性创建)');
          return;
        }
        setEditSpec(job.editSpec);
        setName(job.name);
        setNameDirty(true);
        const dsId = job.input?.datasetId;
        if (dsId) setDatasetId(dsId);
        else message.error('原输入数据集已不存在,无法回填,请重新选择');
      })
      .catch(() => message.error('加载任务失败'));
  }, []);
  useEffect(() => {
    const vId = editSpec?.datasetVersionId;
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions, editSpec]);

  // 任务配置回填:版本成员就绪后套用 editSpec 的成员算子链(仅一次,晚于成员初始化)
  const editSpecAppliedRef = useRef(false);
  useEffect(() => {
    if (
      !editSpec ||
      editSpecAppliedRef.current ||
      versionMembers.length === 0 ||
      versionId !== editSpec.datasetVersionId
    ) {
      return;
    }
    editSpecAppliedRef.current = true;
    setMemberConfigs((prev) => {
      const next = { ...prev };
      if (editSpec.memberConfigs?.length) {
        for (const cfg of editSpec.memberConfigs) {
          if (next[cfg.memberName]) {
            next[cfg.memberName] = { operators: cfg.operators ?? [] };
          }
        }
      } else if (editSpec.operators?.length) {
        // 旧版统一配置:同一条算子链套用到所有成员
        for (const m of versionMembers) {
          next[m.tableName] = { operators: editSpec.operators };
        }
      }
      return next;
    });
  }, [versionMembers, versionId, editSpec]);

  // 流水线预载:URL 带 pipelineId 时,版本成员就绪后把 spec.operators
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
        setMemberConfigs((prev) => {
          const next = { ...prev };
          for (const m of versionMembers) {
            next[m.tableName] = { operators };
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
      datasetName: selectedDatasetName,
      versionLabel: selectedVersionLabel,
      fileName: memberName,
      fileFormat: versionMembers.find((m) => m.tableName === memberName)
        ?.format,
    });
  };

  const setMemberOperators = (
    memberName: string,
    next: DataPlatform.OperatorSpec[],
  ) =>
    setMemberConfigs((prev) => ({
      ...prev,
      [memberName]: { operators: next },
    }));

  // 复制配置:把 copySource 文件的算子链整体覆盖到勾选的目标文件
  const [copySource, setCopySource] = useState<string>();
  const [copyTargets, setCopyTargets] = useState<string[]>([]);

  const applyCopy = () => {
    if (!copySource) return;
    const src = memberConfigs[copySource]?.operators ?? [];
    setMemberConfigs((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) {
        next[t] = {
          operators: src.map((op) => ({
            name: op.name,
            params: { ...(op.params ?? {}) },
          })),
        };
      }
      return next;
    });
    // 覆盖后目标文件是一条干净的线性链,重置游离标记与选中步骤
    setMemberHasOrphan((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) next[t] = false;
      return next;
    });
    setMemberActiveIdx((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) next[t] = 0;
      return next;
    });
    message.success(`已复制到 ${copyTargets.length} 个文件`);
    setCopySource(undefined);
  };

  // 保存为流水线:取当前激活成员的 {operators} 作为 spec,与 scenario 绑定
  const [pipelineModalOpen, setPipelineModalOpen] = useState(false);
  const [pipelineForm] = Form.useForm<{ name: string; description?: string }>();
  const [savingPipeline, setSavingPipeline] = useState(false);

  const openSavePipeline = () => {
    const cfg = activeMember ? memberConfigs[activeMember] : undefined;
    if (!cfg?.operators.length) {
      message.warning('请先进入某个文件的编排并配置算子');
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
        spec: { operators: cfg.operators },
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
      }));

    if (configs.length === 0) {
      message.warning('请至少为一个成员配置算子');
      return;
    }
    if (configs.some((c) => memberHasOrphan[c.memberName])) {
      message.warning('存在未接入流水线的算子');
      return;
    }

    const carryCount = versionMembers.length - configs.length;
    const body = {
      name,
      type: jobType,
      datasetVersionId: versionId,
      memberConfigs: configs,
      outputMode: 'version' as const,
    };
    Modal.confirm({
      title: editJobId ? '确认保存并重新运行' : '确认创建任务',
      content: `将对 ${configs.length} 个文件执行${noun}${
        carryCount > 0 ? `,其余 ${carryCount} 个未配置文件原样结转` : ''
      },产物合并为新版本。${
        editJobId ? '保存会覆盖原任务配置并原地重跑,不新建任务记录。' : ''
      }`,
      onOk: async () => {
        setSubmitting(true);
        try {
          if (editJobId) {
            await updateJob(editJobId, body);
            message.success(`${noun}任务已更新，正在重新运行`);
          } else {
            await createJob(body);
            message.success(`${noun}任务已创建，正在后台运行`);
          }
          history.push(redirectHref);
        } finally {
          setSubmitting(false);
        }
      },
    });
  };

  return (
    <PageContainer
      header={{ title: editJobId ? title.replace('新建', '编辑') : title }}
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
          {editJobId ? '保存并重新运行' : '创建任务'}
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

      {versionMembers.length === 0 || !activeMember ? (
        <Card size="small">
          <Empty description="请先选择数据集和版本" />
        </Card>
      ) : (
        <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
          <Card
            size="small"
            style={{ width: 240, flexShrink: 0 }}
            styles={{ body: { padding: 8 } }}
            title={
              <Space>
                <span>文件</span>
                <Text
                  type="secondary"
                  style={{ fontWeight: 'normal', fontSize: 12 }}
                >
                  已配置{' '}
                  {
                    versionMembers.filter(
                      (m) => memberConfigs[m.tableName]?.operators.length,
                    ).length
                  }
                  /{versionMembers.length}
                </Text>
              </Space>
            }
          >
            {versionMembers.map((m) => {
              const count = memberConfigs[m.tableName]?.operators.length || 0;
              const active = m.tableName === activeMember;
              return (
                <div
                  key={m.tableName}
                  onClick={() => setActiveMember(m.tableName)}
                  style={{
                    padding: '6px 8px',
                    borderRadius: token.borderRadius,
                    cursor: 'pointer',
                    background: active ? token.colorPrimaryBg : undefined,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 4,
                    }}
                  >
                    <Space size={6} style={{ minWidth: 0 }}>
                      <Badge status={count ? 'processing' : 'default'} />
                      <Text
                        strong={active}
                        ellipsis={{ tooltip: m.tableName }}
                        style={{ maxWidth: 140 }}
                      >
                        {m.tableName}
                      </Text>
                    </Space>
                    {count > 0 && versionMembers.length > 1 && (
                      <Button
                        size="small"
                        type="text"
                        icon={<CopyOutlined />}
                        title="复制此配置到其他文件"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCopySource(m.tableName);
                          setCopyTargets([]);
                        }}
                      />
                    )}
                  </div>
                  <Text
                    type="secondary"
                    style={{ fontSize: 12, paddingLeft: 14 }}
                  >
                    {m.format} · {formatSize(m.size)} ·{' '}
                    {count ? `${count} 算子` : '原样结转'}
                  </Text>
                </div>
              );
            })}
          </Card>
          <Card
            title={`算子编排 · ${activeMember}`}
            size="small"
            style={{ flex: 1, minWidth: 0 }}
          >
            {(() => {
              const memberName = activeMember;
              const cfg = memberConfigs[memberName] ?? { operators: [] };
              const memberSteps: DataPlatform.PipelineStep[] =
                cfg.operators.map((op) => ({
                  name: op.name,
                  params: op.params ?? {},
                }));
              const idx = memberActiveIdx[memberName] ?? 0;
              const activeStepOfMember = memberSteps[idx];
              const activeOpOfMember = activeStepOfMember
                ? opMap[activeStepOfMember.name]
                : undefined;
              const appendOperator = (name: string) =>
                setMemberOperators(memberName, [
                  ...cfg.operators,
                  { name, params: {} },
                ]);

              return (
                <Space
                  orientation="vertical"
                  style={{ width: '100%' }}
                  size={16}
                >
                  <PipelineDndArea
                    steps={memberSteps}
                    labelOf={labelOf}
                    onAppend={appendOperator}
                    onReorder={(from, to) => {
                      const next = [...cfg.operators];
                      const [moved] = next.splice(from, 1);
                      next.splice(to, 0, moved);
                      setMemberOperators(memberName, next);
                    }}
                  >
                    <CollapsiblePanes
                      leftTitle="算子库"
                      left={
                        <OperatorLibrary
                          onAdd={appendOperator}
                          bucket={bucket}
                        />
                      }
                      centerTitle="算子流水线"
                      center={
                        <PipelineCanvas
                          steps={memberSteps}
                          labelOf={labelOf}
                          categoryOf={categoryOf}
                          activeIdx={idx}
                          onSelect={(i) =>
                            setMemberActiveIdx((prev) => ({
                              ...prev,
                              [memberName]: i,
                            }))
                          }
                          onRemove={(i) => {
                            setMemberOperators(
                              memberName,
                              cfg.operators.filter((_, j) => j !== i),
                            );
                            setMemberActiveIdx((prev) => ({
                              ...prev,
                              [memberName]: 0,
                            }));
                          }}
                          onOrderChange={(perm) => {
                            if (perm.length !== cfg.operators.length) {
                              setMemberHasOrphan((prev) => ({
                                ...prev,
                                [memberName]: true,
                              }));
                              return;
                            }
                            setMemberHasOrphan((prev) => ({
                              ...prev,
                              [memberName]: false,
                            }));
                            if (perm.every((v, i) => v === i)) return;
                            setMemberOperators(
                              memberName,
                              perm.map((i) => cfg.operators[i]),
                            );
                          }}
                          inputLabel={`${selectedVersionLabel ?? '版本'} · ${memberName}`}
                          outputLabel="新版本"
                        />
                      }
                      rightTitle="参数"
                      right={
                        <StepParamsForm
                          op={activeOpOfMember}
                          params={activeStepOfMember?.params ?? {}}
                          onChange={(p) =>
                            setMemberOperators(
                              memberName,
                              cfg.operators.map((op, i) =>
                                i === idx ? { ...op, params: p } : op,
                              ),
                            )
                          }
                        />
                      }
                      leftCollapsed={libCollapsed}
                      rightCollapsed={paramsCollapsed}
                      onLeftCollapsedChange={setLibCollapsed}
                      onRightCollapsedChange={setParamsCollapsed}
                    />
                  </PipelineDndArea>

                  <YamlPreviewCard
                    computedYaml={memberYamlOf(memberName)}
                    resetKey={memberName}
                    onApply={(text) => {
                      const parsed = yamlToSteps(text, opMap);
                      setMemberOperators(memberName, parsed.steps);
                      setMemberActiveIdx((prev) => ({
                        ...prev,
                        [memberName]: 0,
                      }));
                      setMemberHasOrphan((prev) => ({
                        ...prev,
                        [memberName]: false,
                      }));
                    }}
                  />
                </Space>
              );
            })()}
          </Card>
        </div>
      )}

      <Card size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          每个文件独立生成
          YAML、独立执行；已配置文件的产物与未配置文件（原样保留）
          合并为所选数据集的新版本，可通过历史版本对比追溯加工效果。
          左侧文件列表可随时切换编排对象（配置不会丢失）；行内复制按钮
          可把当前文件的流水线批量套用到其他文件。
        </Text>
      </Card>

      <Modal
        title={`复制「${copySource ?? ''}」的配置到…`}
        open={!!copySource}
        onCancel={() => setCopySource(undefined)}
        onOk={applyCopy}
        okButtonProps={{ disabled: copyTargets.length === 0 }}
        okText="复制"
      >
        <Checkbox.Group
          value={copyTargets}
          onChange={(v) => setCopyTargets(v as string[])}
          style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          options={versionMembers
            .filter((m) => m.tableName !== copySource)
            .map((m) => {
              const count = memberConfigs[m.tableName]?.operators.length || 0;
              return {
                label: count
                  ? `${m.tableName}（已有 ${count} 算子，将被覆盖）`
                  : m.tableName,
                value: m.tableName,
              };
            })}
        />
      </Modal>

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
