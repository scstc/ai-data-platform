import { PageContainer } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Collapse,
  Drawer,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tooltip,
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
  previewJob,
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
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<DataPlatform.PreviewResult>();
  const [previewOpen, setPreviewOpen] = useState(false);

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

  const onPreview = async () => {
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    if (!steps.length) {
      message.warning('至少添加一个算子');
      return;
    }
    setPreviewing(true);
    try {
      const r = await previewJob({
        datasetVersionId: versionId,
        operators: steps,
        sampleSize: 20,
      });
      setPreview(r.data);
      setPreviewOpen(true);
    } catch (e: any) {
      // umi request 失败抛错:BizError 走 e.info.errorMessage;
      // 后端 400 非业务包则走 axios e.response.data.message
      const msg =
        e?.info?.errorMessage ||
        e?.response?.data?.message ||
        e?.data?.message;
      message.error(`试跑失败:${msg || '请查看算子与数据是否匹配'}`);
    } finally {
      setPreviewing(false);
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
        <Button key="preview" loading={previewing} onClick={onPreview}>
          🔍 试跑样例
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

      <Drawer
        width={900}
        title="样例试跑预览"
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
      >
        {preview && (() => {
          const { before, after, beforeCount, afterCount } = preview;
          const mainText = (row: Record<string, any>) =>
            typeof row.text === 'string' ? row.text : JSON.stringify(row);
          const ellCell = (v: string) => (
            <Tooltip title={v}>
              <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {v}
              </span>
            </Tooltip>
          );
          const unchanged = afterCount === beforeCount;
          const delta = beforeCount - afterCount;
          let trend = '';
          if (unchanged) {
            trend = '(行数不变·纯清洗)';
          } else if (delta > 0) {
            trend = `(删除 ${delta} 行)`;
          }
          return (
            <>
              <Statistic
                title="样例行数"
                value={`${beforeCount} 行 → 加工后 ${afterCount} 行 ${trend}`}
                valueStyle={{ fontSize: 16 }}
              />
              <div style={{ marginTop: 16 }}>
                {unchanged ? (
                  <Table
                    size="small"
                    pagination={false}
                    rowKey="key"
                    dataSource={before.map((b, i) => ({
                      key: i,
                      idx: i + 1,
                      before: mainText(b),
                      after: mainText(after[i]),
                    }))}
                    columns={[
                      { title: '序号', dataIndex: 'idx', width: 60 },
                      {
                        title: '加工前',
                        dataIndex: 'before',
                        ellipsis: true,
                        render: (v: string) => ellCell(v),
                      },
                      {
                        title: '加工后',
                        dataIndex: 'after',
                        ellipsis: true,
                        render: (v: string) => ellCell(v),
                      },
                    ]}
                  />
                ) : (
                  <>
                    <Typography.Title level={5}>加工后结果</Typography.Title>
                    <Table
                      size="small"
                      pagination={false}
                      rowKey="key"
                      dataSource={after.map((a, i) => ({
                        key: i,
                        idx: i + 1,
                        text: mainText(a),
                      }))}
                      columns={[
                        { title: '序号', dataIndex: 'idx', width: 60 },
                        {
                          title: '文本',
                          dataIndex: 'text',
                          ellipsis: true,
                          render: (v: string) => ellCell(v),
                        },
                      ]}
                    />
                    <Collapse
                      style={{ marginTop: 16 }}
                      items={[
                        {
                          key: 'before',
                          label: '查看加工前样例',
                          children: (
                            <Table
                              size="small"
                              pagination={false}
                              rowKey="key"
                              dataSource={before.map((b, i) => ({
                                key: i,
                                idx: i + 1,
                                text: mainText(b),
                              }))}
                              columns={[
                                { title: '序号', dataIndex: 'idx', width: 60 },
                                {
                                  title: '文本',
                                  dataIndex: 'text',
                                  ellipsis: true,
                                  render: (v: string) => ellCell(v),
                                },
                              ]}
                            />
                          ),
                        },
                      ]}
                    />
                  </>
                )}
              </div>
            </>
          );
        })()}
      </Drawer>
    </PageContainer>
  );
};

export default Editor;
