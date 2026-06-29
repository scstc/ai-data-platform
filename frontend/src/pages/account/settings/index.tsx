import { PageContainer, ProCard } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Button, Form, Input, message, Tag } from 'antd';
import { useEffect, useState } from 'react';
import {
  changePassword,
  getProfile,
  updateProfile,
} from '@/services/data-platform';

/** 个人设置:当前登录用户自助查看/修改资料(昵称)与密码。
 *  对应后端 /api/v1/profile(仅作用于当前用户,require_user)。 */
const AccountSettings: React.FC = () => {
  const { initialState, setInitialState } = useModel('@@initialState');
  const [profile, setProfile] = useState<DataPlatform.Profile>();
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPwd, setSavingPwd] = useState(false);
  const [profileForm] = Form.useForm();
  const [pwdForm] = Form.useForm();

  useEffect(() => {
    getProfile()
      .then((r) => {
        setProfile(r.data);
        profileForm.setFieldsValue({ displayName: r.data.displayName });
      })
      .catch(() => {});
  }, [profileForm]);

  const onSaveProfile = async (values: { displayName: string }) => {
    setSavingProfile(true);
    try {
      const r = await updateProfile(values);
      setProfile(r.data);
      message.success('资料已更新');
      // 同步顶栏头像显示的名称(app.tsx avatarProps.title 取自 currentUser.name)
      setInitialState((s) => ({
        ...s,
        currentUser: s?.currentUser
          ? { ...s.currentUser, name: r.data.displayName }
          : s?.currentUser,
      }));
    } catch {
      message.error('更新失败，请重试');
    } finally {
      setSavingProfile(false);
    }
  };

  const onChangePwd = async (values: {
    oldPassword: string;
    newPassword: string;
  }) => {
    setSavingPwd(true);
    try {
      const r = await changePassword({
        oldPassword: values.oldPassword,
        newPassword: values.newPassword,
      });
      if (r.success) {
        message.success('密码已修改');
        pwdForm.resetFields();
      } else {
        message.error(r.message || '修改失败');
      }
    } catch (e: any) {
      message.error(e?.response?.data?.message || '原密码不正确');
    } finally {
      setSavingPwd(false);
    }
  };

  return (
    <PageContainer>
      <ProCard title="基本信息" style={{ marginBottom: 16 }}>
        <Form
          form={profileForm}
          layout="vertical"
          style={{ maxWidth: 420 }}
          onFinish={onSaveProfile}
        >
          <Form.Item label="用户名">
            <Input value={profile?.username} disabled />
          </Form.Item>
          <Form.Item label="角色">
            <Tag color={profile?.role === 'admin' ? 'gold' : 'blue'}>
              {profile?.role === 'admin' ? '管理员' : '普通用户'}
            </Tag>
          </Form.Item>
          <Form.Item
            label="昵称"
            name="displayName"
            rules={[
              { required: true, message: '请输入昵称' },
              { max: 64, message: '昵称不超过 64 个字符' },
            ]}
          >
            <Input placeholder="昵称（显示名）" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={savingProfile}>
              保存
            </Button>
          </Form.Item>
        </Form>
      </ProCard>

      <ProCard title="修改密码">
        <Form
          form={pwdForm}
          layout="vertical"
          style={{ maxWidth: 420 }}
          onFinish={onChangePwd}
        >
          <Form.Item
            label="原密码"
            name="oldPassword"
            rules={[{ required: true, message: '请输入原密码' }]}
          >
            <Input.Password placeholder="原密码" />
          </Form.Item>
          <Form.Item
            label="新密码"
            name="newPassword"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '新密码至少 6 位' },
            ]}
          >
            <Input.Password placeholder="新密码（至少 6 位）" />
          </Form.Item>
          <Form.Item
            label="确认新密码"
            name="confirmPassword"
            dependencies={['newPassword']}
            rules={[
              { required: true, message: '请再次输入新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'));
                },
              }),
            ]}
          >
            <Input.Password placeholder="确认新密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={savingPwd}>
              修改密码
            </Button>
          </Form.Item>
        </Form>
      </ProCard>
    </PageContainer>
  );
};

export default AccountSettings;
