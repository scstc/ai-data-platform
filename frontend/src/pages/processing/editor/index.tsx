import { PageContainer } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Typography,
  message,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  createJob,
  generatePipeline,
  listDatasets,
  getDataset,
  listOperatorCatalog,
} from '@/services/data-platform';
import OperatorLibrary from './OperatorLibrary';
import PipelineSteps from './PipelineSteps';
import StepParamsForm from './StepParamsForm';
import { stepsToYaml } from './yaml';

const { Text, Paragraph } = Typography;

const Editor: React.FC = () => {
  const { steps, add, remove, reorder, updateParams, replaceAll, clear } =
    useModel('opCart');
  const [name, setName] = useState('');
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [opMap, setOpMap] = useState<Record<string, DataPlatform.CatalogOperator>>({});
  const [activeIdx, setActiveIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  // 算子元信息(供 label/params 渲染):一次取全量(212 ≤ 后端 pageSize 上限 500)
  useEffect(() => {
    listOperatorCatalog({ current: 1, pageSize: 500 }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 }).then((r) => setDatasets(r.data));
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  const yamlText = useMemo(() => stepsToYaml(steps), [steps]);

  const onGenerate = () => {
    let goal = '';
    Modal.confirm({
      title: 'AI 生成流水线',
      content: (
        <Input.TextArea
          placeholder="描述加工目标,如:中文语料清洗去重"
          onChange={(e) => {
            goal = e.target.value;
          }}
        />
      ),
      onOk: async () => {
        if (!goal.trim()) {
          message.warning('请填写加工目标');
          return Promise.reject();
        }
        const r = await generatePipeline({ goal, datasetVersionId: versionId });
        const ops = r.data.operators;
        if (!ops.length) {
          // 后端 sanitize 可能裁掉全部算子(返回空):不要清空已编排步骤,提示而非伪装成功
          message.warning(r.data.explanation || '未生成可用算子,请调整目标后重试');
          return Promise.reject();
        }
        replaceAll(ops);
        setActiveIdx(0);
        message.success(r.data.explanation || '已生成流水线');
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
      await createJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
      });
      message.success('加工任务已创建');
      clear();
      history.push('/processing/jobs');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建加工任务' }}
      extra={[
        <Button key="ai" onClick={onGenerate}>
          ✨ AI 生成
        </Button>,
        <Button key="submit" type="primary" loading={submitting} onClick={onSubmit}>
          创建任务
        </Button>,
      ]}
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="任务名"
          style={{ width: 220 }}
          value={name}
          onChange={(e) => setName(e.target.value)}
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
          style={{ width: 200 }}
          value={versionId}
          onChange={setVersionId}
          options={versions.map((v) => ({ label: `v${v.versionNo}`, value: v.id }))}
        />
      </Space>

      <Row gutter={16}>
        <Col span={7}>
          <Card title="算子库" size="small" styles={{ body: { height: 460, padding: 12 } }}>
            <OperatorLibrary onAdd={add} />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="流水线(拖拽排序)" size="small" styles={{ body: { height: 460, overflow: 'auto' } }}>
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
          <Card title="参数" size="small" styles={{ body: { height: 460, overflow: 'auto' } }}>
            <StepParamsForm
              op={activeOp}
              params={activeStep?.params ?? {}}
              onChange={(p) => updateParams(activeIdx, p)}
            />
          </Card>
        </Col>
      </Row>

      <Card title="YAML 预览" size="small" style={{ marginTop: 16 }}>
        <Paragraph>
          <pre style={{ margin: 0, fontSize: 12 }}>{yamlText}</pre>
        </Paragraph>
        <Text type="secondary" style={{ fontSize: 12 }}>
          仅预览;真实配置在创建时由后端生成。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default Editor;
