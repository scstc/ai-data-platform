// 数据合成编辑器:多 jsonl 文件按行拼接合并(后端纯 Python 执行,不走 data-juicer/LLM)。
// 选版本 → 勾选 ≥2 个 jsonl 成员 → 自动探测各文件字段并求共同字段 →
// 配置合并字段/分隔符 → 首行示例预览 → 提交异步任务,产物落新版本。
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  message,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createMakeJob,
  getDataset,
  getMakeJob,
  listDatasets,
  previewDatasetVersion,
  updateMakeJob,
} from '@/services/data-platform';
import { suggestTaskName } from '@/utils/taskName';

const { Text, Paragraph } = Typography;

/** 与后端 make.merge_records 一致的句末标点集合(补尾判定) */
const TERMINAL_SEPARATORS = ['。', '.', '!', '?', '！', '？', ';', '；'];

/** 客户端镜像后端拼接规则,仅用于首行示例预览 */
const mergePreview = (fragments: string[], separator: string): string => {
  const terminal = TERMINAL_SEPARATORS.includes(separator) ? separator : '';
  const parts = fragments
    .map((f) => {
      let s = (f ?? '').trim();
      while (terminal && s.endsWith(terminal)) s = s.slice(0, -terminal.length);
      return s;
    })
    .filter(Boolean);
  return parts.length ? parts.join(separator) + terminal : '';
};

/** s3://bucket/key → key(previewDatasetVersion 的成员定位参数) */
const keyOfUri = (uri: string) => uri.replace(/^s3:\/\/[^/]+\//, '');

const formatSize = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

const MakeEditor: React.FC = () => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [datasetId, setDatasetId] = useState<string>();
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [versionId, setVersionId] = useState<string>();
  const [members, setMembers] = useState<DataPlatform.DatasetTable[]>([]);

  // 勾选的成员(member 列表序);主文件默认第一个勾选项,可单独指定
  const [selected, setSelected] = useState<string[]>([]);
  const [primary, setPrimary] = useState<string>();

  // 字段探测:成员名 → 列名列表 / 首行样例;探测中的成员集合
  const [fieldsByMember, setFieldsByMember] = useState<
    Record<string, string[]>
  >({});
  const [sampleByMember, setSampleByMember] = useState<
    Record<string, Record<string, any>>
  >({});
  const [detecting, setDetecting] = useState(false);

  const [mergeField, setMergeField] = useState<string>();
  const [separator, setSeparator] = useState('。');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 }).then((r) =>
      setDatasets(r.data),
    );
  }, []);

  useEffect(() => {
    setVersions([]);
    setVersionId(undefined);
    if (!datasetId) return;
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  useEffect(() => {
    setMembers([]);
    setSelected([]);
    setPrimary(undefined);
    setFieldsByMember({});
    setSampleByMember({});
    setMergeField(undefined);
    if (!versionId) return;
    const version = versions.find((v) => v.id === versionId);
    setMembers(version?.tables ?? []);
  }, [versionId, versions]);

  // 从数据集详情/治理工场跳入时按 URL 预选
  const location = useLocation();
  useEffect(() => {
    const dsId = new URLSearchParams(location.search).get('datasetId');
    if (dsId) setDatasetId(dsId);
  }, []);
  useEffect(() => {
    const vId = new URLSearchParams(location.search).get('versionId');
    if (vId && versions.some((v) => v.id === vId)) setVersionId(vId);
  }, [versions]);

  // 编辑模式:URL 带 jobId 时按任务的 editSpec 回填(名称/数据集/版本/合并配置),
  // 提交改走 updateMakeJob(覆盖原任务配置并原地重跑,不新建记录)
  const editJobId = new URLSearchParams(location.search).get('jobId');
  const [editSpec, setEditSpec] = useState<Record<string, any>>();
  const editing = Boolean(editJobId && editSpec);
  useEffect(() => {
    if (!editJobId) return;
    getMakeJob(editJobId)
      .then((r) => {
        const job = r.data;
        if (!job.editSpec) {
          message.error('该任务无可编辑的配置(早于重跑特性创建)');
          return;
        }
        if (job.editSpec.goal?.mode !== 'merge') {
          message.warning('仅合并模式的合成任务支持编辑,将按新建处理');
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
  // 合并配置回填:成员就绪后套用勾选/主文件/分隔符(仅一次);合并字段需等
  // 字段探测出共同字段后再回填(见下方 commonFields effect),否则会被缺省逻辑覆盖
  const editApplied = useRef(false);
  const pendingMergeField = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (
      !editSpec ||
      editApplied.current ||
      members.length === 0 ||
      versionId !== editSpec.datasetVersionId
    ) {
      return;
    }
    editApplied.current = true;
    const goal = editSpec.goal ?? {};
    const names = members.map((m) => m.tableName);
    const picked = (goal.mergeMembers ?? []).filter((n: string) =>
      names.includes(n),
    );
    setSelected(picked);
    if (picked.length) setPrimary(picked[0]);
    if (goal.mergeSeparator) setSeparator(goal.mergeSeparator);
    pendingMergeField.current = goal.mergeField;
  }, [members, versionId, editSpec]);

  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = useMemo(
    () => suggestTaskName(selectedDatasetName, '数据合成'),
    [selectedDatasetName],
  );
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  // 勾选变化:主文件缺省跟随第一个勾选项;逐个探测新勾选成员的字段
  useEffect(() => {
    if (!primary || !selected.includes(primary)) setPrimary(selected[0]);
    const pending = selected.filter((n) => !fieldsByMember[n]);
    if (!versionId || pending.length === 0) return;
    setDetecting(true);
    Promise.all(
      pending.map(async (n) => {
        const m = members.find((x) => x.tableName === n);
        if (!m) return;
        const r = await previewDatasetVersion(versionId, {
          key: keyOfUri(m.storageUri),
          limit: 1,
        });
        setFieldsByMember((prev) => ({ ...prev, [n]: r.columns ?? [] }));
        setSampleByMember((prev) => ({ ...prev, [n]: r.data?.[0] ?? {} }));
      }),
    )
      .catch(() => message.error('字段探测失败,请重试'))
      .finally(() => setDetecting(false));
  }, [selected, versionId]);

  // 共同字段 = 已勾选成员字段的交集(全部探测完才有意义)
  const allDetected = selected.every((n) => fieldsByMember[n]);
  const commonFields = useMemo(() => {
    if (selected.length === 0 || !allDetected) return [];
    return selected
      .map((n) => fieldsByMember[n])
      .reduce((acc, cols) => acc.filter((c) => cols.includes(c)));
  }, [selected, fieldsByMember, allDetected]);

  // 共同字段就绪后缺省选 text;已选字段失效(换文件后不再共同)则重选
  useEffect(() => {
    if (mergeField && commonFields.includes(mergeField)) return;
    setMergeField(commonFields.includes('text') ? 'text' : commonFields[0]);
  }, [commonFields]);

  // 编辑回填的合并字段:等共同字段探测完成后套用(声明在缺省逻辑之后,覆盖其结果)
  useEffect(() => {
    const pending = pendingMergeField.current;
    if (pending && commonFields.includes(pending)) {
      pendingMergeField.current = undefined;
      setMergeField(pending);
    }
  }, [commonFields]);

  // 合并顺序:主文件在前,其余按成员列表序
  const orderedMembers = useMemo(() => {
    if (!primary) return selected;
    return [primary, ...selected.filter((n) => n !== primary)];
  }, [selected, primary]);

  const exampleText = useMemo(() => {
    if (!mergeField || !allDetected || orderedMembers.length < 2) return '';
    return mergePreview(
      orderedMembers.map((n) => sampleByMember[n]?.[mergeField]),
      separator,
    );
  }, [orderedMembers, sampleByMember, mergeField, separator, allDetected]);

  const rowCounts = orderedMembers.map(
    (n) => members.find((m) => m.tableName === n)?.rows,
  );
  const rowsMismatch =
    rowCounts.every((c) => typeof c === 'number') &&
    new Set(rowCounts).size > 1;

  const submit = async () => {
    if (!versionId) {
      message.warning('请先选择数据集和版本');
      return;
    }
    if (orderedMembers.length < 2) {
      message.warning('请至少勾选 2 个 jsonl 文件参与合并');
      return;
    }
    if (!mergeField) {
      message.warning('所选文件没有共同字段,无法合并');
      return;
    }
    const body: DataPlatform.MakeJobCreate = {
      name,
      datasetVersionId: versionId,
      goal: {
        mode: 'merge',
        mergeMembers: orderedMembers,
        mergeField,
        mergeSeparator: separator,
      },
    };
    if (editing && editJobId) {
      Modal.confirm({
        title: '确认保存并重新运行',
        content: '保存会覆盖原任务配置并原地重跑,不新建任务记录。',
        onOk: async () => {
          setSubmitting(true);
          try {
            await updateMakeJob(editJobId, body);
            message.success('任务已更新，正在重新运行');
            history.push('/governance/make/jobs');
          } finally {
            setSubmitting(false);
          }
        },
      });
      return;
    }
    setSubmitting(true);
    try {
      await createMakeJob(body);
      message.success('合成任务已创建，正在后台运行');
      history.push('/governance/make/jobs');
    } finally {
      setSubmitting(false);
    }
  };

  const jsonlMembers = members.filter((m) => m.format === 'jsonl');

  return (
    <PageContainer
      title={editing ? '编辑数据合成' : '新建数据合成'}
      extra={
        <Button
          type="primary"
          loading={submitting}
          disabled={orderedMembers.length < 2 || !mergeField}
          onClick={submit}
        >
          {editing ? '保存并重新运行' : '创建任务'}
        </Button>
      }
    >
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Card size="small" title="任务与数据">
          <Space wrap>
            <Input
              style={{ width: 280 }}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameDirty(true);
              }}
              placeholder="任务名称"
            />
            <Select
              showSearch={{ optionFilterProp: 'label' }}
              placeholder="选择数据集"
              style={{ width: 260 }}
              value={datasetId}
              onChange={setDatasetId}
              options={datasets.map((d) => ({ label: d.name, value: d.id }))}
            />
            <Select
              placeholder="选择版本"
              style={{ width: 220 }}
              value={versionId}
              onChange={setVersionId}
              disabled={!datasetId}
              options={versions.map((v) => ({
                label: `${v.versionLabel ?? v.id}（${v.tables?.length ?? 0} 个文件）`,
                value: v.id,
              }))}
            />
          </Space>
        </Card>

        {!versionId ? (
          <Card>
            <Empty description="请先选择数据集和版本" />
          </Card>
        ) : jsonlMembers.length < 2 ? (
          <Card>
            <Empty description="该版本的 jsonl 文件不足 2 个,无法合并;请选择含多个 jsonl 成员的版本" />
          </Card>
        ) : (
          <>
            <Card size="small" title="选择合并文件(按行号对齐拼接,勾选 ≥2 个)">
              <Table<DataPlatform.DatasetTable>
                rowKey="tableName"
                size="small"
                pagination={false}
                dataSource={members}
                rowSelection={{
                  selectedRowKeys: selected,
                  onChange: (keys) =>
                    setSelected(
                      members
                        .map((m) => m.tableName)
                        .filter((n) => (keys as string[]).includes(n)),
                    ),
                  getCheckboxProps: (m) => ({
                    disabled: m.format !== 'jsonl',
                  }),
                }}
                columns={[
                  {
                    title: '文件',
                    dataIndex: 'tableName',
                    render: (n: string) => (
                      <Space size="small">
                        <Text>{n}</Text>
                        {n === primary && selected.includes(n) && (
                          <Tag color="blue">主文件</Tag>
                        )}
                      </Space>
                    ),
                  },
                  { title: '格式', dataIndex: 'format', width: 90 },
                  {
                    title: '行数',
                    dataIndex: 'rows',
                    width: 100,
                    render: (r?: number) => r?.toLocaleString() ?? '-',
                  },
                  {
                    title: '大小',
                    dataIndex: 'size',
                    width: 100,
                    render: (s?: number) => formatSize(s),
                  },
                  {
                    title: '检测到的字段',
                    dataIndex: 'tableName',
                    render: (n: string) =>
                      selected.includes(n) ? (
                        fieldsByMember[n] ? (
                          fieldsByMember[n].map((c) => (
                            <Tag
                              key={c}
                              color={
                                commonFields.includes(c) ? 'green' : undefined
                              }
                            >
                              {c}
                            </Tag>
                          ))
                        ) : (
                          <Text type="secondary">探测中…</Text>
                        )
                      ) : (
                        <Text type="secondary">-</Text>
                      ),
                  },
                ]}
              />
              {selected.length >= 2 && allDetected && !detecting && (
                <div style={{ marginTop: 12 }}>
                  {commonFields.length === 0 ? (
                    <Alert
                      type="error"
                      showIcon
                      title="所选文件没有共同字段,无法合并;请检查各文件的字段(绿色标签为共同字段)"
                    />
                  ) : (
                    <Alert
                      type="success"
                      showIcon
                      title={
                        <Space size="small" wrap>
                          共同字段:
                          {commonFields.map((c) => (
                            <Tag key={c} color="green">
                              {c}
                            </Tag>
                          ))}
                        </Space>
                      }
                    />
                  )}
                  {rowsMismatch && (
                    <Alert
                      style={{ marginTop: 8 }}
                      type="warning"
                      showIcon
                      title="所选文件行数不一致:合并按行号对齐,以主文件行数为准;扩展文件多出的行会被丢弃,缺失的行不拼接(任务报告中会提示)"
                    />
                  )}
                </div>
              )}
            </Card>

            <Card size="small" title="合并配置">
              <Space wrap align="center">
                <span>主文件</span>
                <Select
                  style={{ width: 260 }}
                  value={primary}
                  onChange={setPrimary}
                  disabled={selected.length < 2}
                  options={selected.map((n) => ({ label: n, value: n }))}
                />
                <span>合并字段</span>
                <Select
                  style={{ width: 180 }}
                  value={mergeField}
                  onChange={setMergeField}
                  disabled={commonFields.length === 0}
                  options={commonFields.map((c) => ({ label: c, value: c }))}
                />
                <span>分隔符</span>
                <Input
                  style={{ width: 80 }}
                  value={separator}
                  onChange={(e) => setSeparator(e.target.value || '。')}
                />
              </Space>
              <Paragraph
                type="secondary"
                style={{ marginTop: 8, marginBottom: 0 }}
              >
                产物沿用主文件的文件名和其余字段;其余勾选文件仅贡献合并字段的拼接片段。
                句末标点分隔符(。 . !
                ?)会同时补到整段结尾。未勾选的文件原样结转进新版本。
              </Paragraph>
              {exampleText && mergeField && (
                <Card
                  size="small"
                  type="inner"
                  title="首行合并示例"
                  style={{ marginTop: 12 }}
                >
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify({ [mergeField]: exampleText }, null, 2)}
                  </pre>
                </Card>
              )}
            </Card>
          </>
        )}
      </Space>
    </PageContainer>
  );
};

export default MakeEditor;
