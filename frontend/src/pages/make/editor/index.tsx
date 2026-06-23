// 数据合成新建页:LLM 造新数据(Self-Instruct / QA 生成 / few-shot prompt)
// 复用 distillation 的三件套 + LLM 顶部提示;算子在 MAKE_OPS 白名单(3 个)
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Input,
  message,
  Row,
  Select,
  Space,
  Typography,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import LlmRequiredAlert from '@/components/LlmRequiredAlert';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import { suggestTaskName } from '@/utils/taskName';
import {
  createMakeJob,
  getDataset,
  listDatasets,
  listOperatorCatalog,
} from '@/services/data-platform';
import OperatorLibrary from '../../processing/editor/OperatorLibrary';
import PipelineSteps from '../../processing/editor/PipelineSteps';
import StepParamsForm from '../../processing/editor/StepParamsForm';
import MakeGoalPanel from './MakeGoalPanel';

const { Text } = Typography;

const DEFAULT_GOAL: DataPlatform.MakeGoal = {
  mode: 'synthesize',
  targetPerSample: 1,
};

const MakeEditor: React.FC = () => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [goal, setGoal] = useState<DataPlatform.MakeGoal>(DEFAULT_GOAL);
  const [outputDatasetId, setOutputDatasetId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  const add = useCallback(
    (op: string) => setSteps((prev) => [...prev, { name: op, params: {} }]),
    [],
  );
  const remove = useCallback(
    (idx: number) => setSteps((prev) => prev.filter((_, i) => i !== idx)),
    [],
  );
  const updateParams = useCallback(
    (idx: number, params: Record<string, unknown>) =>
      setSteps((prev) =>
        prev.map((s, i) => (i === idx ? { ...s, params } : s)),
      ),
    [],
  );
  const reorder = useCallback(
    (from: number, to: number) =>
      setSteps((prev) => {
        const next = [...prev];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      }),
    [],
  );

  useEffect(() => {
    listOperatorCatalog({ current: 1, pageSize: 500 }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 500 }).then((r) => setDatasets(r.data));
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  const activeStep = steps[activeIdx];
  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = suggestTaskName(
    selectedDatasetName,
    '数据合成',
    steps.map((s) => s.name),
  );
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  const onSubmit = async () => {
    if (!name.trim()) {
      message.warning('请填写任务名');
      return;
    }
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    if (!steps.length) {
      message.warning('至少添加一个算子');
      return;
    }
    setSubmitting(true);
    try {
      await createMakeJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
        goal: { ...goal, mode: 'synthesize' },
        outputDatasetId,
      });
      message.success('合成任务已创建，正在后台运行');
      history.push('/governance/make');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建数据合成' }}
      extra={[
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
      <LlmRequiredAlert description="数据合成(LLM 造新数据)需 LLM 支持。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。" />

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
                ? `${v.versionLabel}（${v.format}·二进制不支持）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <MakeGoalPanel
          value={goal}
          onChange={setGoal}
          datasets={datasets}
          defaultDatasetId={datasetId}
          outputDatasetId={outputDatasetId}
          onOutputDatasetChange={setOutputDatasetId}
        />
      </Card>

      <Row gutter={16}>
        <Col span={7}>
          <Card
            title="算子库"
            size="small"
            styles={{ body: { height: 460, padding: 12 } }}
          >
            <OperatorLibrary onAdd={add} />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="已选合成算子"
            size="small"
            styles={{ body: { height: 460, overflow: 'auto' } }}
          >
            <PipelineSteps
              steps={steps}
              labelOf={(n) => opMap[n]?.zhLabel || n}
              activeIdx={activeIdx}
              onSelect={setActiveIdx}
              onRemove={(i) => {
                remove(i);
                setActiveIdx(0);
              }}
              onReorder={reorder}
            />
          </Card>
        </Col>
        <Col span={7}>
          <Card
            title="参数"
            size="small"
            styles={{ body: { height: 460, overflow: 'auto' } }}
          >
            <StepParamsForm
              op={activeOp}
              params={activeStep?.params ?? {}}
              onChange={(p) => updateParams(activeIdx, p)}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          数据合成走 LLM 造新数据类算子(generate_qa_from_* 抽取 QA 对、optimize_prompt
          上下文扩展等);产物 version 标记 origin=synthetic,可被前端按 origin
          区分「原始数据 vs 合成数据」。需 LLM Key(见顶部提示)。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default MakeEditor;
