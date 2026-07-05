import {
  ModalForm,
  ProFormDependency,
  ProFormRadio,
  ProFormSelect,
  ProFormText,
} from '@ant-design/pro-components';
import { message } from 'antd';
import type { FC } from 'react';
import { listLakeObjects, mergeLakeObjects } from '@/services/data-platform';

export type MergeObjectsModalProps = {
  lakeId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 已勾选、待合并的文件(需全部 storageFormat === 'parquet') */
  selected: DataPlatform.DataLakeObject[];
  onSuccess: () => void;
};

type MergeFormValues = {
  mode: 'union' | 'join';
  joinKeys?: string[];
  target: 'new' | 'existing';
  name?: string;
  targetObjectId?: string;
};

/**
 * 数据合并 ModalForm —— 勾选 ≥2 个 parquet 文件后触发。
 * union 纵向拼接(列并集)/ join 按键关联(需填 joinKeys),产出新文件或追加到
 * 已有合并文件(仅可选该湖内 origin === 'merged' 的对象)。
 */
const MergeObjectsModal: FC<MergeObjectsModalProps> = ({
  lakeId,
  open,
  onOpenChange,
  selected,
  onSuccess,
}) => {
  return (
    <ModalForm<MergeFormValues>
      title="数据合并"
      open={open}
      onOpenChange={onOpenChange}
      width={520}
      modalProps={{ destroyOnHidden: true }}
      initialValues={{ mode: 'union', target: 'new' }}
      onFinish={async (values) => {
        const hide = message.loading('正在合并...', 0);
        try {
          const res = await mergeLakeObjects(lakeId, {
            mode: values.mode,
            inputs: selected.map((o) => ({ objectId: o.id })),
            joinKeys: values.mode === 'join' ? values.joinKeys : undefined,
            name: values.target === 'new' ? values.name : undefined,
            targetObjectId:
              values.target === 'existing' ? values.targetObjectId : undefined,
          });
          hide();
          message.success(`合并完成,新版本 v${res?.data?.versionNo ?? ''}`);
          onSuccess();
          return true;
        } catch (err) {
          hide();
          const e = err as {
            response?: { data?: { detail?: string; message?: string } };
          };
          message.error(
            e?.response?.data?.detail ??
              e?.response?.data?.message ??
              '合并失败',
          );
          return false;
        }
      }}
    >
      <div style={{ marginBottom: 16, color: '#666' }}>
        将合并 <b>{selected.length}</b> 个文件,合并只增不改,源文件不受影响。
      </div>
      <ProFormRadio.Group
        name="mode"
        label="合并方式"
        options={[
          { label: '纵向拼接(union)', value: 'union' },
          { label: '按键关联(join)', value: 'join' },
        ]}
      />
      <ProFormDependency name={['mode']}>
        {({ mode }) =>
          mode === 'join' ? (
            <ProFormSelect
              name="joinKeys"
              label="关联键"
              mode="tags"
              placeholder="输入各文件都含有的列名,回车确认"
              rules={[{ required: true, message: '请填写关联键' }]}
            />
          ) : null
        }
      </ProFormDependency>
      <ProFormRadio.Group
        name="target"
        label="目标文件"
        options={[
          { label: '新建合并文件', value: 'new' },
          { label: '追加到已有合并文件', value: 'existing' },
        ]}
      />
      <ProFormDependency name={['target']}>
        {({ target }) =>
          target === 'existing' ? (
            <ProFormSelect
              name="targetObjectId"
              label="选择合并文件"
              placeholder="搜索该湖内已有的合并文件"
              rules={[{ required: true, message: '请选择目标文件' }]}
              showSearch
              fieldProps={{ filterOption: false }}
              request={async ({ keyWords }) => {
                const res = await listLakeObjects(lakeId, {
                  name: keyWords || undefined,
                  pageSize: 100,
                });
                return (res.data ?? [])
                  .filter((o) => o.origin === 'merged')
                  .map((o) => ({ label: o.displayName, value: o.id }));
              }}
            />
          ) : (
            <ProFormText
              name="name"
              label="文件名称"
              rules={[{ required: true, message: '请填写文件名称' }]}
              placeholder="如:门店销售宽表"
            />
          )
        }
      </ProFormDependency>
    </ModalForm>
  );
};

export default MergeObjectsModal;
