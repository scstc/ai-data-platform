import { CopyOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Input,
  Modal,
  message,
  Row,
  Select,
  Space,
  Typography,
  theme,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import OperatorLibrary from '@/pages/processing/editor/OperatorLibrary';
import PipelineDndArea from '@/pages/processing/editor/PipelineDndArea';
import PipelineSteps from '@/pages/processing/editor/PipelineSteps';
import StepParamsForm from '@/pages/processing/editor/StepParamsForm';
import YamlPreviewCard from '@/pages/processing/editor/YamlPreviewCard';
import { stepsToYaml, yamlToSteps } from '@/pages/processing/editor/yaml';
import {
  createQualityJob,
  getDataset,
  listDatasets,
  listOperatorCatalog,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';

const { Text } = Typography;

type MemberConfig = {
  operators: DataPlatform.OperatorSpec[];
};

/** 字节数人类可读,与 processing/editor 的 formatSize 保持一致 */
const formatSize = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

/** 质量评估编辑器:布局对齐清洗页 processing/editor 的 master-detail——
 *  左侧常驻文件清单(每个文件显示格式/大小/配置状态,点行切换),右侧编排
 *  当前文件的 filter 类算子;各文件配置按文件名缓存,切文件不丢,行内
 *  「复制」可批量套用同一套算子。文本字段由后端自动探测,前端不下发 text_keys。 */
const QualityEditor: React.FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('assessment:quality:add');
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [versionMembers, setVersionMembers] = useState<
    DataPlatform.DatasetTable[]
  >([]);

  const [memberConfigs, setMemberConfigs] = useState<
    Record<string, MemberConfig>
  >({});
  const [activeMember, setActiveMember] = useState<string>();
  const [memberActiveIdx, setMemberActiveIdx] = useState<
    Record<string, number>
  >({});

  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const { token } = theme.useToken();

  useEffect(() => {
    // includeHidden:编辑既有任务时步骤可能引用已隐藏算子,缺元信息无法渲染
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

  // 版本变化:按版本表成员初始化各自配置
  useEffect(() => {
    setVersionMembers([]);
    setMemberConfigs({});
    setActiveMember(undefined);
    setMemberActiveIdx({});
    if (!versionId || !datasetId) return;

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

  // 从数据集版本表「流程」入口跳入时,按 URL 预选数据集 + 版本
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;

  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = useMemo(
    () => suggestTaskName(selectedDatasetName, '质量评估'),
    [selectedDatasetName],
  );
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  const selectedVersionLabel = versions.find(
    (v) => v.id === versionId,
  )?.versionLabel;
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
    setMemberActiveIdx((prev) => {
      const next = { ...prev };
      for (const t of copyTargets) next[t] = 0;
      return next;
    });
    message.success(`已复制到 ${copyTargets.length} 个文件`);
    setCopySource(undefined);
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
      message.warning('请至少为一个成员配置质量算子');
      return;
    }

    setSubmitting(true);
    try {
      await createQualityJob({
        name,
        datasetVersionId: versionId,
        memberConfigs: configs,
      });
      message.success('质量评估任务已创建，正在后台运行');
      history.push('/assessment/quality');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建质量评估' }}
      extra={
        canAdd
          ? [
              <Button
                key="submit"
                type="primary"
                loading={submitting}
                onClick={onSubmit}
              >
                创建评估
              </Button>,
            ]
          : []
      }
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
                ? `${v.versionLabel}（${v.format}·二进制不可评估）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
        {/* 文本字段由后端自动探测,前端不再下发 text_keys 字段。 */}
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
                    {count ? `${count} 算子` : '不评估'}
                  </Text>
                </div>
              );
            })}
          </Card>
          <Card
            title={`质量算子编排 · ${activeMember}`}
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
                    <Row gutter={16}>
                      <Col span={7}>
                        <Card
                          title="质量算子库"
                          size="small"
                          styles={{ body: { height: 360, padding: 12 } }}
                        >
                          <OperatorLibrary
                            category="filter"
                            bucket="quality"
                            onAdd={appendOperator}
                          />
                        </Card>
                      </Col>
                      <Col span={10}>
                        <Card
                          title="已选质量算子"
                          size="small"
                          styles={{ body: { height: 360, overflow: 'auto' } }}
                        >
                          <PipelineSteps
                            steps={memberSteps}
                            labelOf={labelOf}
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
                          />
                        </Card>
                      </Col>
                      <Col span={7}>
                        <Card
                          title="参数"
                          size="small"
                          styles={{ body: { height: 360, overflow: 'auto' } }}
                        >
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
                        </Card>
                      </Col>
                    </Row>
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
          质量评估对每条数据计算质量指标(不删除数据),结果按成员(表/文件)
          独立写入,可在评估任务详情按成员切换查看报告。评估字段由后端自动探测,
          无需手动指定。左侧文件列表可随时切换编排对象(配置不会丢失);
          行内复制按钮可把当前文件的算子配置批量套用到其他文件。
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
    </PageContainer>
  );
};

export default QualityEditor;
