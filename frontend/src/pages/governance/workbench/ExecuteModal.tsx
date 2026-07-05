// 流水线一键执行弹窗:选数据集版本 + 任务名 → POST /pipelines/{id}/execute。
// 二进制格式版本(图像/音频/视频)不可加工,选项禁用(与各场景 editor 的版本选择规则一致)。
import { history } from '@umijs/max';
import { Input, Modal, message, Select } from 'antd';
import { useEffect, useState } from 'react';
import { isBinaryFormat } from '@/pages/ingest/access/constants';
import {
  executePipeline,
  getDataset,
  listDatasets,
} from '@/services/data-platform';
import { suggestTaskName, type TaskType } from '@/utils/taskName';

const ExecuteModal: React.FC<{
  pipeline?: DataPlatform.Pipeline;
  taskType: TaskType;
  onClose: () => void;
}> = ({ pipeline, taskType, onClose }) => {
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const open = !!pipeline;

  useEffect(() => {
    if (!open) return;
    setName('');
    setNameDirty(false);
    setDatasetId(undefined);
    setVersionId(undefined);
    listDatasets({ current: 1, pageSize: 1000 }).then((r) =>
      setDatasets(r.data ?? []),
    );
  }, [open]);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  const selectedDatasetName = datasets.find((d) => d.id === datasetId)?.name;
  const suggestedName = suggestTaskName(selectedDatasetName, taskType);
  useEffect(() => {
    if (!nameDirty) setName(suggestedName);
  }, [suggestedName, nameDirty]);

  const handleSubmit = async () => {
    if (!pipeline) return;
    if (!name.trim()) {
      message.warning('请填写任务名');
      return;
    }
    if (!versionId) {
      message.warning('请选择数据集版本');
      return;
    }
    setSubmitting(true);
    try {
      await executePipeline(pipeline.id, { name, datasetVersionId: versionId });
      message.success('任务已创建，正在后台运行');
      onClose();
      history.push('/ops/data-tasks');
    } catch (e: any) {
      message.error(
        e?.info?.errorMessage || e?.data?.message || '执行失败，请重试',
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={`一键执行「${pipeline?.name ?? ''}」`}
      confirmLoading={submitting}
      onOk={handleSubmit}
      onCancel={onClose}
      destroyOnHidden
    >
      <Input
        placeholder="任务名(自动生成,可编辑)"
        style={{ marginBottom: 12 }}
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          setNameDirty(true);
        }}
      />
      <Select
        placeholder="选择数据集"
        style={{ width: '100%', marginBottom: 12 }}
        value={datasetId}
        onChange={setDatasetId}
        options={datasets.map((d) => ({ label: d.name, value: d.id }))}
      />
      <Select
        placeholder="选择版本"
        style={{ width: '100%' }}
        value={versionId}
        onChange={setVersionId}
        disabled={!datasetId}
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
    </Modal>
  );
};

export default ExecuteModal;
