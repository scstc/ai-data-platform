// 数据增强新建页:LLM 改写已有数据(Evol-Instruct / 通用改写 / 校准 / 打标)
// 复用 distillation 的三件套 + LLM 顶部提示;算子在 AUGMENT_OPS 白名单(9 个)
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
  Tooltip,
  Typography,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import LlmRequiredAlert from '@/components/LlmRequiredAlert';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createAugmentJob,
  getDataset,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import OperatorLibrary from '../../processing/editor/OperatorLibrary';
import PipelineSteps from '../../processing/editor/PipelineSteps';
import StepParamsForm from '../../processing/editor/StepParamsForm';
import { useOpCartIntake } from '../../processing/editor/useOpCartIntake';
import AugmentGoalPanel from './AugmentGoalPanel';

const { Text } = Typography;

const DEFAULT_GOAL: DataPlatform.AugmentGoal = {
  mode: 'augment',
};

const AugmentEditor: React.FC = () => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  // 选中版本的列名(供「文本字段」多选);留空=后端自动探测主文本字段
  const [columns, setColumns] = useState<string[]>([]);
  const [textKeys, setTextKeys] = useState<string[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  // 市场购物车交接:只带入 augment 桶的算子,其余跳过并提示
  useOpCartIntake('augment', '增强', setSteps);
  const [goal, setGoal] = useState<DataPlatform.AugmentGoal>(DEFAULT_GOAL);
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
    listDatasets({ current: 1, pageSize: 500 }).then((r) =>
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

  // 版本变化:拉一条预览取列名,供「文本字段」多选;切版本时清空已选(列可能不同)
  useEffect(() => {
    setTextKeys([]);
    if (!versionId) {
      setColumns([]);
      return;
    }
    previewDatasetVersion(versionId, { limit: 1 })
      .then((r) => setColumns(r.columns ?? []))
      .catch(() => setColumns([]));
  }, [versionId]);

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
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = suggestTaskName(selectedDatasetName, '数据增强');
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

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
      await createAugmentJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
        goal: { ...goal, mode: 'augment' },
        outputDatasetId,
        textKeys: textKeys.length ? textKeys : undefined,
      });
      message.success('增强任务已创建，正在后台运行');
      history.push('/governance/augment');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建数据增强' }}
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
      <LlmRequiredAlert description="数据增强(LLM 改写已有数据)需 LLM 支持。请先在运维监控 → LLM 配置页设置 OPENAI_API_KEY 并激活。" />

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
        <Tooltip title="算子作用的字段;留空则自动探测主文本字段。数据无 text 字段(如 GIS address)时在此显式指定。">
          <Select
            mode="multiple"
            allowClear
            placeholder="文本字段(留空=自动)"
            style={{ minWidth: 220, maxWidth: 360 }}
            value={textKeys}
            onChange={setTextKeys}
            disabled={!versionId || columns.length === 0}
            options={columns.map((c) => ({ label: c, value: c }))}
            maxTagCount="responsive"
          />
        </Tooltip>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <AugmentGoalPanel
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
            <OperatorLibrary onAdd={add} bucket="augment" />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="已选增强算子"
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
          数据增强走 LLM 改写类算子(optimize_qa/query/response
          进化指令、sentence_augmentation 通用改写、calibrate
          事实校准、llm_extract 结构化抽取、pair_preference DPO 偏好构造等);产物
          version 标记 origin=synthetic。需 LLM Key(见顶部提示)。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default AugmentEditor;
