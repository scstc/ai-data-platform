import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Input,
  Modal,
  message,
  Row,
  Select,
  Space,
  Typography,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createQualityJob,
  generateQuality,
  getDataset,
  listDatasets,
  listOperatorCatalog,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import OperatorLibrary from '../../processing/editor/OperatorLibrary';
import PipelineSteps from '../../processing/editor/PipelineSteps';
import StepParamsForm from '../../processing/editor/StepParamsForm';
import { useOpCartIntake } from '../../processing/editor/useOpCartIntake';

const { Text } = Typography;

const QualityEditor: React.FC = () => {
  // 页面内本地 state 管理已选算子(质量评估不走全局 opCart)
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);
  const add = useCallback(
    (name: string) => setSteps((prev) => [...prev, { name, params: {} }]),
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
  const replaceAll = useCallback(
    (next: DataPlatform.PipelineStep[]) => setSteps(next),
    [],
  );
  // 市场购物车交接:质量评估无业务桶,接受任意算子(bucket=undefined → 全收)
  useOpCartIntake(undefined, '质量评估', replaceAll);
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

  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
  const [activeIdx, setActiveIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);

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

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  // 自动任务名:数据集/算子变化时重算,用户改过(nameDirty)则不再覆盖
  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = suggestTaskName(selectedDatasetName, '质量评估');
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  const onGenerate = () => {
    let goal = '';
    Modal.confirm({
      title: 'AI 生成质量评估算子',
      content: (
        <Input.TextArea
          placeholder="描述评估目标,如:评估中文语料的文本质量与重复度"
          onChange={(e) => {
            goal = e.target.value;
          }}
        />
      ),
      onOk: async () => {
        if (!goal.trim()) {
          message.warning('请填写评估目标');
          return Promise.reject();
        }
        const r = await generateQuality({ goal, datasetVersionId: versionId });
        const ops = r.data.operators;
        if (!ops.length) {
          // 空结果不覆盖已选算子,提示而非伪装成功
          message.warning(
            r.data.explanation || '未生成可用算子,请调整目标后重试',
          );
          return Promise.reject();
        }
        replaceAll(ops);
        setActiveIdx(0);
        message.success(r.data.explanation || '已生成质量评估算子');
      },
    });
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
    if (!steps.length) {
      message.warning('至少添加一个算子');
      return;
    }
    setSubmitting(true);
    try {
      await createQualityJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
      });
      message.success('质量评估任务已创建');
      history.push('/assessment/quality');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建质量评估' }}
      extra={[
        <Button key="ai" onClick={onGenerate}>
          ✨ AI 生成
        </Button>,
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
          style={{ width: 220 }}
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

      <Row gutter={16}>
        <Col span={7}>
          <Card
            title="算子库"
            size="small"
            styles={{ body: { height: 460, padding: 12 } }}
          >
            <OperatorLibrary category="filter" onAdd={add} />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="已选质量算子"
            size="small"
            styles={{ body: { height: 460, overflow: 'auto' } }}
          >
            <PipelineSteps
              steps={steps}
              labelOf={labelOf}
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
          质量评估对每条数据计算质量指标(不删除数据),结果写入该版本的
          stats,可在评估任务详情查看报告。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default QualityEditor;
