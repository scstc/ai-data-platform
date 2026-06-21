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
  { path: '/quality', redirect: '/governance/quality' },
  { path: '/quality/editor', redirect: '/governance/quality/editor' },
  { path: '/processing', redirect: '/governance/processing' },
  { path: '/processing/jobs', redirect: '/governance/processing' },
  { path: '/processing/editor', redirect: '/governance/processing/editor' },
  { path: '/processing/market', redirect: '/operators' },
  { path: '/governance/operators', redirect: '/operators' },
  { path: '/annotation', redirect: '/governance/annotation' },
  { path: '/data-tasks', redirect: '/ops/data-tasks' },
  { path: '/lineage', redirect: '/ops/lineage' },
  { path: '/security', redirect: '/ops/security' },
  { path: '/ingest/assistant', redirect: '/assistant' },
  // 算子市场(置顶 · 独立顶级入口:data-juicer 算子目录,供加工任务编排选用)
  {
    path: '/operators',
    name: 'operators',
    icon: 'appstore',
    component: './processing/market',
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
      {
        path: '/ingest/datasources/new',
        name: 'datasource-new',
        hideInMenu: true,
        component: './ingest/datasources/new',
      },
      {
        path: '/ingest/datasources/new/:type',
        name: 'datasource-config',
        hideInMenu: true,
        component: './ingest/datasources/config',
      },
      { path: '/ingest/tasks', name: 'tasks', component: './ingest/tasks' },
      { path: '/ingest/access', name: 'access', component: './ingest/access' },
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
      {
        path: '/ingest/categories',
        name: 'categories',
        component: './ingest/categories',
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
        path: '/datasets/:id',
        name: 'dataset-detail',
        hideInMenu: true,
        component: './datasets/detail',
      },
    ],
  },
  // ③ 数据治理(安全扫描 / 质量评估 / 数据加工 / 数据标注)
  {
    path: '/governance',
    name: 'governance',
    icon: 'safety',
    routes: [
      { path: '/governance', redirect: '/governance/content-safety' },
      {
        path: '/governance/content-safety',
        name: 'contentSafety',
        component: './content-safety',
      },
      { path: '/governance/quality', name: 'quality', component: './quality' },
      {
        path: '/governance/quality/editor',
        name: 'quality-editor',
        component: './quality/editor',
        hideInMenu: true,
      },
      {
        path: '/governance/processing',
        name: 'processing',
        component: './processing',
      },
      {
        path: '/governance/processing/editor',
        name: 'processing-editor',
        component: './processing/editor',
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
  {
    component: './exception/404',
    layout: false,
    path: './*',
  },
];
