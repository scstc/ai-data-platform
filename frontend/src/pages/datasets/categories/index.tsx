import { PageContainer } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Card } from 'antd';
import { CategoryPanel } from '@/components';
import { buildBreadcrumb } from '@/utils/breadcrumb';

/** 分类管理:分类词表的唯一权威入口(#15)。
 *  列表所有登录用户可见;新增/编辑/删除仅管理员。各上传/采集表单内的「管理分类」
 *  快捷入口复用同一 CategoryPanel(抽屉形态)。 */
const CategoriesPage: React.FC = () => {
  const access = useAccess();
  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据集仓库', path: '/datasets/list' },
        { title: '分类管理' },
      ])}
      title="分类管理"
      content="维护用于组织数据集 / 采集任务的分类词表(新增 / 编辑 / 删除仅管理员)。"
    >
      <Card>
        <CategoryPanel canAdmin={!!access.canAdmin} />
      </Card>
    </PageContainer>
  );
};

export default CategoriesPage;
