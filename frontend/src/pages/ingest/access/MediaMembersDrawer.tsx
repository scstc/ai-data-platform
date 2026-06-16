import { DeleteOutlined, UploadOutlined } from '@ant-design/icons';
import type { UploadFile, UploadProps } from 'antd';
import {
  Button,
  Drawer,
  Empty,
  Image,
  message,
  Popconfirm,
  Space,
  Spin,
  Typography,
  Upload,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import {
  addDatasetMembers,
  deleteDatasetMember,
  getDatasetMemberUrl,
  listDatasetMembers,
} from '@/services/data-platform';

const AUDIO = new Set(['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg']);
const VIDEO = new Set(['mp4', 'avi', 'mov', 'mkv', 'webm']);
const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']);
// 一次最多渲染的成员数(每个成员各拉一次预签名 URL,避免大集一次性打出上百请求)
const MAX_PREVIEW = 60;

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

interface Props {
  open: boolean;
  versionId?: string;
  datasetId?: string;
  /** 是否可编辑成员(仅 manifest 媒体集) */
  editable?: boolean;
  /** 追加上传的 accept(扩展名白名单) */
  accept?: string;
  title?: string;
  onClose: () => void;
}

/** 单个成员预览:按格式渲染 图/音/视频,其余给下载链接;可编辑时带删除。 */
const MemberView: React.FC<{
  versionId: string;
  member: DataPlatform.DatasetMember;
  editable?: boolean;
  onDelete: (key: string) => void;
}> = ({ versionId, member, editable, onDelete }) => {
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getDatasetMemberUrl(versionId, member.key)
      .then((r) => alive && setUrl(r.data.url))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [versionId, member.key]);

  const fmt = (member.format || '').toLowerCase();
  const box: React.CSSProperties = {
    width: 180,
    border: '1px solid var(--ant-color-border-secondary, #f0f0f0)',
    borderRadius: 8,
    padding: 8,
  };
  const media: React.CSSProperties = {
    width: '100%',
    height: 120,
    objectFit: 'cover',
    borderRadius: 4,
  };

  let body: React.ReactNode;
  if (failed) {
    body = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="加载失败" />;
  } else if (!url) {
    body = <Spin />;
  } else if (IMAGE.has(fmt)) {
    body = (
      <Image
        src={url}
        width="100%"
        height={120}
        style={{ objectFit: 'cover' }}
        onError={() => setFailed(true)}
      />
    );
  } else if (VIDEO.has(fmt)) {
    // biome-ignore lint/a11y/useMediaCaption: 用户上传的原始媒体,无字幕轨
    body = <video src={url} controls style={media} onError={() => setFailed(true)} />;
  } else if (AUDIO.has(fmt)) {
    body = (
      // biome-ignore lint/a11y/useMediaCaption: 用户上传的原始媒体,无字幕轨
      <audio
        src={url}
        controls
        style={{ width: '100%' }}
        onError={() => setFailed(true)}
      />
    );
  } else {
    body = (
      <a href={url} target="_blank" rel="noreferrer">
        下载 / 打开
      </a>
    );
  }

  return (
    <div style={box}>
      <div style={{ height: 120, display: 'flex', alignItems: 'center' }}>
        {body}
      </div>
      <Typography.Text
        ellipsis={{ tooltip: member.name }}
        style={{ width: '100%', display: 'block', marginTop: 6 }}
      >
        {member.name}
      </Typography.Text>
      {editable && (
        <Popconfirm
          title="从数据集移除该文件?"
          okText="删除"
          okButtonProps={{ danger: true }}
          onConfirm={() => onDelete(member.key)}
        >
          <Button danger type="text" size="small" icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      )}
    </div>
  );
};

/** 数据集成员文件抽屉:列出成员并预览;manifest 媒体集可追加/删除成员。 */
const MediaMembersDrawer: React.FC<Props> = ({
  open,
  versionId,
  datasetId,
  editable,
  accept,
  title,
  onClose,
}) => {
  const [messageApi, contextHolder] = message.useMessage();
  const [loading, setLoading] = useState(false);
  const [members, setMembers] = useState<DataPlatform.DatasetMember[]>([]);
  const [staged, setStaged] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);

  const reload = useCallback(() => {
    if (!versionId) return;
    setLoading(true);
    listDatasetMembers(versionId)
      .then((r) => setMembers(r.data ?? []))
      .catch(() => setMembers([]))
      .finally(() => setLoading(false));
  }, [versionId]);

  useEffect(() => {
    if (!open || !versionId) return;
    setStaged([]);
    reload();
  }, [open, versionId, reload]);

  const handleDelete = async (key: string) => {
    if (!datasetId) return;
    try {
      await deleteDatasetMember(datasetId, key);
      messageApi.success('已删除');
      reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '删除失败,请重试'));
    }
  };

  const stage: NonNullable<UploadProps['beforeUpload']> = () => false;

  const handleAppend = async () => {
    if (!datasetId) return;
    const files = staged
      .map((f) => f.originFileObj)
      .filter((f): f is NonNullable<typeof f> => !!f);
    if (files.length === 0) return;
    setUploading(true);
    try {
      const formData = new FormData();
      for (const f of files) formData.append('files', f);
      await addDatasetMembers(datasetId, formData);
      messageApi.success(`已追加 ${files.length} 个文件`);
      setStaged([]);
      reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '追加失败,请重试'));
    } finally {
      setUploading(false);
    }
  };

  return (
    <Drawer
      width={780}
      open={open}
      title={`文件列表:${title ?? ''}`}
      onClose={onClose}
    >
      {contextHolder}
      {editable && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Upload
            multiple
            accept={accept}
            beforeUpload={stage}
            fileList={staged}
            onChange={({ fileList }) => setStaged(fileList)}
          >
            <Button icon={<UploadOutlined />}>选择要追加的文件</Button>
          </Upload>
          <Button
            type="primary"
            loading={uploading}
            disabled={staged.length === 0}
            onClick={handleAppend}
          >
            追加{staged.length ? ` ${staged.length} 个` : ''}
          </Button>
        </Space>
      )}
      <Spin spinning={loading}>
        {members.length > 0 && versionId ? (
          <>
            {members.length > MAX_PREVIEW && (
              <Typography.Paragraph type="secondary">
                共 {members.length} 个文件,仅预览前 {MAX_PREVIEW} 个。
              </Typography.Paragraph>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              {members.slice(0, MAX_PREVIEW).map((m) => (
                <MemberView
                  key={m.key}
                  versionId={versionId}
                  member={m}
                  editable={editable}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          </>
        ) : (
          <Empty description="暂无成员文件" />
        )}
      </Spin>
    </Drawer>
  );
};

export default MediaMembersDrawer;
