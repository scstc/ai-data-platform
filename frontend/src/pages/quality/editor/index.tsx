import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Input,
  message,
  Row,
  Select,
  Space,
  Tabs,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import OperatorLibrary from '@/pages/processing/editor/OperatorLibrary';
import PipelineDndArea from '@/pages/processing/editor/PipelineDndArea';
import PipelineSteps from '@/pages/processing/editor/PipelineSteps';
import StepParamsForm from '@/pages/processing/editor/StepParamsForm';
import { stepsToYaml } from '@/pages/processing/editor/yaml';
import {
  createQualityJob,
  getDataset,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';

const { Text, Paragraph } = Typography;

type MemberConfig = {
  operators: DataPlatform.OperatorSpec[];
  textKeys: string[];
};

/** 质量评估编辑器:成员级 Tab 配置(对齐清洗页 processing/editor),
 *  每个数据集版本成员(表/文件)独立选择 filter 类算子。 */
const QualityEditor: React.FC = () => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
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

  // 版本变化:拉一条预览取列名(供「评估字段」多选);并按版本表成员初始化各自配置
  useEffect(() => {
    setVersionMembers([]);
    setMemberConfigs({});
    setActiveMember(undefined);
    setMemberActiveIdx({});
    if (!versionId || !datasetId) {
      setColumns([]);
      return;
    }

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

      previewDatasetVersion(versionId, { limit: 1 })
        .then((r) => setColumns(r.columns ?? []))
        .catch(() => setColumns([]));
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
      extra={[
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          onClick={onSubmit}
        >
          创建评估
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
                ? `${v.versionLabel}（${v.format}·二进制不可评估）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
        {/* 文本字段由后端自动探测,前端不再下发 text_keys 字段。 */}
      </Space>

      {versionMembers.length === 0 ? (
        <Card size="small">
          <Empty description="请先选择数据集和版本" />
        </Card>
      ) : (
        <Card title="成员级质量算子配置" size="small">
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
                    <Card title="评估字段（可选）" size="small">
                      <Select
                        mode="multiple"
                        allowClear
                        placeholder="选择评估字段（留空=自动探测）"
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
                        <Col span={7}>
                          <Card
                            title="质量算子库"
                            size="small"
                            styles={{ body: { height: 360, padding: 12 } }}
                          >
                            <OperatorLibrary
                              category="filter"
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
          质量评估对每条数据计算质量指标(不删除数据),结果按成员(表/文件)
          独立写入,可在评估任务详情按成员切换查看报告。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default QualityEditor;
