export default [
  {
    path: '/user',
    layout: false,
    routes: [
      {
        name: '登录',
        path: '/user/login',
        component: './user/login',
      },
    ],
  },
  {
    path: '/',
    redirect: '/ingest/datasources',
  },
  // 旧路径兼容:菜单按数据工程流程分组后,治理/运维各页迁到 /governance、/ops 前缀
  // (分组父路由须是子路由前缀,菜单选中/展开态才正常)。老链接/书签重定向到新路径。
  { path: '/files', redirect: '/ingest/files' },
  { path: '/ingest/upload', redirect: '/ingest/access' },
  { path: '/content-safety', redirect: '/governance/content-safety' },
  // 质量评估已迁到 /assessment(只读评估,与治理分离);旧路径保留重定向
  { path: '/quality', redirect: '/assessment/quality' },
  { path: '/quality/editor', redirect: '/assessment/quality/editor' },
  { path: '/governance/quality', redirect: '/assessment/quality' },
  {
    path: '/governance/quality/editor',
    redirect: '/assessment/quality/editor',
  },
  // 数据加工(process)已并入数据清洗(clean):旧链接重定向到清洗页
  { path: '/processing', redirect: '/governance/cleaning' },
  { path: '/processing/jobs', redirect: '/governance/cleaning' },
  { path: '/processing/editor', redirect: '/governance/cleaning/editor' },
  { path: '/governance/processing', redirect: '/governance/cleaning' },
  {
    path: '/governance/processing/editor',
    redirect: '/governance/cleaning/editor',
  },
  { path: '/processing/market', redirect: '/operators' },
  { path: '/governance/operators', redirect: '/operators' },
  { path: '/annotation', redirect: '/governance/annotation' },
  { path: '/data-tasks', redirect: '/ops/data-tasks' },
  { path: '/lineage', redirect: '/ops/lineage' },
  { path: '/security', redirect: '/ops/security' },
  { path: '/ingest/assistant', redirect: '/assistant' },
  { path: '/ingest/categories', redirect: '/datasets/categories' },
  // 算子市场(置顶 · 独立顶级入口:data-juicer 算子目录,供加工任务编排选用)
  {
    path: '/operators',
    name: 'operators',
    icon: 'block',
    component: './processing/market',
  },
  // 上传自定义算子(独立页,须声明在 /operators/:name 之前,否则被当成 name 命中详情路由)
  {
    path: '/operators/upload',
    name: 'operators-upload',
    component: './processing/market/upload',
    hideInMenu: true,
  },
  // 算子详情(从市场卡片进入,不进左侧菜单)
  {
    path: '/operators/:name',
    component: './processing/market/detail',
    hideInMenu: true,
  },
  // 个人设置(头像下拉进入,不进左侧菜单)
  {
    path: '/account/settings',
    name: 'account-settings',
    component: './account/settings',
    hideInMenu: true,
  },
  // ① 数据接入
  {
    path: '/ingest',
    name: 'ingest',
    icon: 'api',
    routes: [
      { path: '/ingest', redirect: '/ingest/datasources' },
      {
        path: '/ingest/datasources',
        name: 'datasources',
        component: './ingest/datasources',
      },
      // 原接入方式选择落地页已并入数据源管理页顶部,旧链接重定向兜底
      {
        path: '/ingest/datasources/new',
        redirect: '/ingest/datasources',
      },
      {
        path: '/ingest/datasources/new/:type',
        name: 'datasource-config',
        hideInMenu: true,
        component: './ingest/datasources/config',
      },
      { path: '/ingest/tasks', name: 'tasks', component: './ingest/tasks' },
      {
        path: '/ingest/access',
        name: 'access',
        hideInMenu: true,
        component: './ingest/access',
      },
      {
        path: '/ingest/local-upload',
        name: 'local-upload',
        component: './ingest/local-upload',
      },
      {
        path: '/ingest/local-upload/single',
        name: 'local-upload-single',
        hideInMenu: true,
        component: './ingest/local-upload/single',
      },
      {
        path: '/ingest/local-upload/scenario',
        name: 'local-upload-scenario',
        hideInMenu: true,
        component: './ingest/local-upload/scenario',
      },
      {
        path: '/ingest/local-upload/cot',
        name: 'local-upload-cot',
        hideInMenu: true,
        component: './ingest/local-upload/cot',
      },
      {
        path: '/ingest/local-upload/timeseries',
        name: 'local-upload-timeseries',
        hideInMenu: true,
        component: './ingest/local-upload/timeseries',
      },
      {
        path: '/ingest/local-upload/qa',
        name: 'local-upload-qa',
        hideInMenu: true,
        component: './ingest/local-upload/qa',
      },
      {
        path: '/ingest/local-upload/preference',
        name: 'local-upload-preference',
        hideInMenu: true,
        component: './ingest/local-upload/preference',
      },
      {
        path: '/ingest/local-upload/gis',
        name: 'local-upload-gis',
        hideInMenu: true,
        component: './ingest/local-upload/gis',
      },
      {
        path: '/ingest/local-upload/multimodal',
        name: 'local-upload-multimodal',
        hideInMenu: true,
        component: './ingest/local-upload/multimodal',
      },
      { path: '/ingest/files', name: 'files', component: './files' },
    ],
  },
  // ①.5 数据湖(ODS 原始数据层 · 湖集分离架构 · 见 docs/数据治理.md)
  // 数据湖是所有外部数据源的统一入口,原样归档、版本固化、血缘追踪
  {
    path: '/data-lakes',
    name: 'data-lakes',
    icon: 'cluster',
    routes: [
      { path: '/data-lakes', redirect: '/data-lakes/list' },
      {
        path: '/data-lakes/list',
        name: 'list',
        component: './data-lakes',
      },
      {
        path: '/data-lakes/:id',
        name: 'detail',
        hideInMenu: true,
        component: './data-lakes/detail',
      },
    ],
  },
  // ② 数据集仓库(原始数据湖 · 草稿版本 + 发布门 + 算法工程师消费)
  {
    path: '/datasets',
    name: 'datasets',
    icon: 'database',
    routes: [
      { path: '/datasets', redirect: '/datasets/list' },
      { path: '/datasets/list', name: 'list', component: './datasets/list' },
      {
        path: '/datasets/presets',
        name: 'presets',
        component: './datasets/presets',
      },
      {
        path: '/datasets/categories',
        name: 'categories',
        component: './datasets/categories',
      },
      { path: '/datasets/tags', name: 'tags', component: './datasets/tags' },
      {
        path: '/datasets/:id',
        name: 'dataset-detail',
        hideInMenu: true,
        component: './datasets/detail',
      },
    ],
  },
  // ③ 数据评估(只读侧:不产新版本,只对原版本打分/打标)
  // 区别于 /governance 的"动数据/产新版本"语义,顶级菜单独立
  {
    path: '/assessment',
    name: 'assessment',
    icon: 'audit',
    routes: [
      { path: '/assessment', redirect: '/assessment/quality' },
      { path: '/assessment/quality', name: 'quality', component: './quality' },
      {
        path: '/assessment/quality/editor',
        name: 'quality-editor',
        component: './quality/editor',
        hideInMenu: true,
      },
      {
        path: '/assessment/quality/report',
        name: 'quality-report',
        component: './quality/report',
        hideInMenu: true,
      },
    ],
  },
  // ④ 数据治理(动数据侧:治理工场 / 内容安全 / 清洗 / 蒸馏 / 合成 / 增强 / 标注)
  // 治理工场(workbench)收口清洗/蒸馏/合成/增强四场景为统一入口(场景 Tab + 流水线模板);
  // 原四个菜单项评标期保留为薄入口(渲染 Workbench 并预设 scenario),编辑器/任务列表路径不变。
  {
    path: '/governance',
    name: 'governance',
    icon: 'safety',
    routes: [
      { path: '/governance', redirect: '/governance/workbench' },
      {
        path: '/governance/workbench',
        name: 'workbench',
        component: './governance/workbench',
      },
      {
        path: '/governance/content-safety',
        name: 'contentSafety',
        component: './content-safety',
      },
      {
        path: '/governance/content-safety/report',
        name: 'contentSafety-report',
        component: './content-safety/report',
        hideInMenu: true,
      },
      {
        path: '/governance/cleaning',
        name: 'cleaning',
        component: './cleaning',
      },
      {
        path: '/governance/cleaning/jobs',
        name: 'cleaning-jobs',
        component: './cleaning/jobs',
        hideInMenu: true,
      },
      {
        path: '/governance/cleaning/editor',
        name: 'cleaning-editor',
        component: './cleaning/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/distillation',
        name: 'distillation',
        component: './distillation',
      },
      {
        path: '/governance/distillation/jobs',
        name: 'distillation-jobs',
        component: './distillation/jobs',
        hideInMenu: true,
      },
      {
        path: '/governance/distillation/editor',
        name: 'distillation-editor',
        component: './distillation/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/make',
        name: 'make',
        component: './make',
      },
      {
        path: '/governance/make/jobs',
        name: 'make-jobs',
        component: './make/jobs',
        hideInMenu: true,
      },
      {
        path: '/governance/make/editor',
        name: 'make-editor',
        component: './make/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/augment',
        name: 'augment',
        component: './augment',
      },
      {
        path: '/governance/augment/jobs',
        name: 'augment-jobs',
        component: './augment/jobs',
        hideInMenu: true,
      },
      {
        path: '/governance/augment/editor',
        name: 'augment-editor',
        component: './augment/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/trainset',
        name: 'trainset',
        component: './trainset',
      },
      {
        path: '/governance/trainset/editor',
        name: 'trainset-editor',
        component: './trainset/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/annotation',
        name: 'annotation',
        component: './annotation',
      },
    ],
  },
  // ④ 运维监控(跨切面:任务 / 血缘 / 审计)
  {
    path: '/ops',
    name: 'ops',
    icon: 'dashboard',
    routes: [
      { path: '/ops', redirect: '/ops/data-tasks' },
      { path: '/ops/data-tasks', name: 'dataTasks', component: './data-tasks' },
      { path: '/ops/lineage', name: 'lineage', component: './lineage' },
      {
        path: '/ops/security',
        name: 'security',
        access: 'canAdmin',
        component: './security',
      },
      {
        path: '/ops/llm-settings',
        name: 'llmSettings',
        access: 'canAdmin',
        component: './ops/llm-settings',
      },
    ],
  },
  // 智能助手(独立顶级入口,放在菜单最后)
  {
    path: '/assistant',
    name: 'assistant',
    icon: 'robot',
    component: './ingest/assistant',
  },
  // 系统管理(RBAC:用户/角色/部门/菜单/权限)。access:'canSystem' ⇒
  // 仅 admin 或持 system:* 权限的角色可见;按钮级权限由各页 hasPerm 门控。
  {
    path: '/system',
    name: 'system',
    icon: 'setting',
    access: 'canSystem',
    routes: [
      { path: '/system', redirect: '/system/user' },
      {
        path: '/system/user',
        name: 'systemUser',
        component: './system/user',
      },
      {
        path: '/system/role',
        name: 'systemRole',
        component: './system/role',
      },
      {
        path: '/system/dept',
        name: 'systemDept',
        component: './system/dept',
      },
      {
        path: '/system/menu',
        name: 'systemMenu',
        component: './system/menu',
      },
      {
        path: '/system/permission',
        name: 'systemPermission',
        component: './system/permission',
      },
    ],
  },
  {
    component: './exception/404',
    layout: false,
    path: './*',
  },
];
