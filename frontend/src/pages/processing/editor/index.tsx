import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation, useModel } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Collapse,
  Drawer,
  Input,
  Modal,
  message,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  createJob,
  generatePipeline,
  getDataset,
  listDatasets,
  listOperatorCatalog,
  previewDatasetVersion,
  previewJob,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';
import OperatorLibrary from './OperatorLibrary';
import PipelineSteps from './PipelineSteps';
import StepParamsForm from './StepParamsForm';
import { stepsToYaml } from './yaml';

const { Text, Paragraph } = Typography;

/** 清洗任务编辑器(按 jobType 建任务)。数据清洗=clean。
 *  作为通用编辑器组件保留,cleaning/editor 渲染 <Editor jobType="clean" ... /> 复用。 */
const Editor: React.FC<{
  jobType?: string;
  title?: string;
  noun?: string;
  redirectHref?: string;
  /** 业务桶:清洗页传 "cleansing" 锁定算子库;不传 → 展示全部算子 */
  bucket?: string;
}> = ({
  jobType = 'clean',
  title = '新建清洗任务',
  noun = '清洗',
  redirectHref = '/governance/cleaning',
  bucket,
}) => {
  const { steps, add, remove, reorder, updateParams, replaceAll, clear } =
    useModel('opCart');
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false); // 用户改过则不再自动覆盖
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  // 选中版本的列名(供「清洗字段」多选);清洗字段留空=后端自动探测主文本字段
  const [columns, setColumns] = useState<string[]>([]);
  const [textKeys, setTextKeys] = useState<string[]>([]);
  const [opMap, setOpMap] = useState<
    Record<string, DataPlatform.CatalogOperator>
  >({});
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

  // 版本变化:拉一条预览取列名,供「清洗字段」多选;切版本时清空已选(列可能不同)
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
  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  // 自动任务名:数据集/算子变化时重算,用户手动改过(nameDirty)则不再覆盖
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
  const yamlText = useMemo(
    () =>
      stepsToYaml(steps, {
        datasetName: selectedDatasetName,
        versionLabel: selectedVersionLabel,
        textKeys,
      }),
    [steps, selectedDatasetName, selectedVersionLabel, textKeys],
  );

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
          message.warning(
            r.data.explanation || '未生成可用算子,请调整目标后重试',
          );
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
        textKeys: textKeys.length ? textKeys : undefined,
      });
      setPreview(r.data);
      setPreviewOpen(true);
    } catch (e: any) {
      // umi request 失败抛错:BizError 走 e.info.errorMessage;
      // 后端 400 非业务包则走 axios e.response.data.message
      const msg =
        e?.info?.errorMessage || e?.response?.data?.message || e?.data?.message;
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
        outputMode: 'version',
        type: jobType,
        // 留空=后端自动探测;选了字段则显式指定清洗作用字段
        textKeys: textKeys.length ? textKeys : undefined,
      });
      message.success(`${noun}任务已创建，正在后台运行`);
      clear();
      history.push(redirectHref);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title }}
      extra={[
        <Button key="ai" onClick={onGenerate}>
          ✨ AI 生成
        </Button>,
        <Button key="preview" loading={previewing} onClick={onPreview}>
          🔍 试跑样例
        </Button>,
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
        <Tooltip title="算子作用的字段;留空则自动探测主文本字段。脏字符不在标准字段(如 task)时在此显式指定。">
          <Select
            mode="multiple"
            allowClear
            placeholder="清洗字段(留空=自动)"
            style={{ minWidth: 220, maxWidth: 360 }}
            value={textKeys}
            onChange={setTextKeys}
            disabled={!versionId || columns.length === 0}
            options={columns.map((c) => ({ label: c, value: c }))}
            maxTagCount="responsive"
          />
        </Tooltip>
      </Space>

      <Row gutter={16}>
        <Col span={7}>
          <Card
            title="算子库"
            size="small"
            styles={{ body: { height: 460, padding: 12 } }}
          >
            <OperatorLibrary onAdd={add} bucket={bucket} />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="流水线"
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
          加工产物将作为所选数据集的新版本，可通过历史版本对比追溯加工效果。下方
          YAML 仅为本机编排预览；真实配置在创建时由后端生成。
        </Text>
      </Card>

      <Card title="YAML 预览" size="small" style={{ marginTop: 16 }}>
        <Paragraph>
          <pre style={{ margin: 0, fontSize: 12 }}>{yamlText}</pre>
        </Paragraph>
      </Card>

      <Drawer
        width={900}
        title="样例试跑预览"
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
      >
        {preview &&
          (() => {
            const { before, after, beforeCount, afterCount } = preview;
            const mainText = (row: Record<string, any>) =>
              typeof row.text === 'string' ? row.text : JSON.stringify(row);
            const ellCell = (v: string) => (
              <Tooltip title={v}>
                <span
                  style={{
                    display: 'block',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
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
            } else {
              trend = `(新增 ${-delta} 行)`;
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
                        after: mainText(after[i] ?? {}),
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
                                  {
                                    title: '序号',
                                    dataIndex: 'idx',
                                    width: 60,
                                  },
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
