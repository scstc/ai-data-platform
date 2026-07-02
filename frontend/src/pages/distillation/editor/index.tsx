// 数据蒸馏新建/编辑:复用 processing/editor 的三件套(OperatorLibrary / PipelineSteps
// / StepParamsForm);本页面用本地 state。
// 布局对齐 quality/editor:扁平 Space 顶部 + Goal 面板 + 7/10/7 三栏。
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
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createDistillationJob,
  getDataset,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import OperatorLibrary from '../../processing/editor/OperatorLibrary';
import PipelineSteps from '../../processing/editor/PipelineSteps';
import StepParamsForm from '../../processing/editor/StepParamsForm';
import DistillationGoalPanel from './DistillationGoalPanel';

const { Text } = Typography;

const DEFAULT_GOAL: DataPlatform.DistillationGoal = {
  keepRatio: 0.3,
  scoreField: 'meta.score',
  fallbackRandom: true,
  enableDedup: true,
  enableScoreFilter: true,
};

const DistillationEditor: React.FC = () => {
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
  const [goal, setGoal] = useState<DataPlatform.DistillationGoal>(DEFAULT_GOAL);
  const [outputDatasetId, setOutputDatasetId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  const add = useCallback(
    (op: string) => setSteps((prev) => [...prev, { name: op, params: {} }]),
    [],
  );
  const remove = useCallback(
    (idx: number) =>
      setSteps((prev) => {
        const next = prev.filter((_, i) => i !== idx);
        return next;
      }),
    [],
  );
  const updateParams = useCallback(
    (idx: number, params: Record<string, unknown>) =>
      setSteps((prev) =>
        prev.map((s, i) => (i === idx ? { ...s, params } : s)),
      ),
    [],
  );
  const replaceAll = useCallback(
    (next: DataPlatform.PipelineStep[]) => setSteps(next),
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

  // 算子元信息(供 label/params 渲染):pageSize ≤ 后端 le=500 上限
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

  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = suggestTaskName(selectedDatasetName, '数据蒸馏');
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
    // 至少 1 个 selector(蒸馏必须按某字段取子集)
    const hasSelector = steps.some(
      (s) => (opMap[s.name] as any)?.category === 'selector',
    );
    if (!hasSelector) {
      message.warning(
        '蒸馏算子链必须包含至少 1 个 selector(如 topk_specified_field_selector)',
      );
      return;
    }
    setSubmitting(true);
    try {
      await createDistillationJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
        goal,
        outputDatasetId,
        textKeys: textKeys.length ? textKeys : undefined,
      });
      message.success('蒸馏任务已创建，正在后台运行');
      history.push('/governance/distillation');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建数据蒸馏' }}
      extra={[
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          onClick={onSubmit}
        >
          创建蒸馏
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
                ? `${v.versionLabel}（${v.format}·二进制不可蒸馏）`
                : `${v.versionLabel}（${v.format}）`,
              value: v.id,
              disabled: isBinary,
            };
          })}
        />
        <Tooltip title="算子作用的字段;留空则自动探测主文本字段。蒸馏数据通常无 text 字段(如 instruction),建议在此显式指定。">
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
        <DistillationGoalPanel
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
            <OperatorLibrary onAdd={add} bucket="distillation" />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="已选蒸馏算子"
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
          蒸馏对所选数据集版本按"过滤 + 去重 +
          打分截断"产出子集,落到原数据集新版本(可改输出数据集); 算子链需至少含 1
          个
          selector,且必须在算子市场"蒸馏场景"内选择。后台异步执行,完成后可在列表查看报告。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default DistillationEditor;
