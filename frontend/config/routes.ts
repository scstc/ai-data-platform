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
  // 旧路径兼容(菜单已按数据工程流程重组,页面路径不变)
  { path: '/files', redirect: '/ingest/files' },
  { path: '/ingest/upload', redirect: '/ingest/access' },
  { path: '/processing/market', redirect: '/operators' },
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
      { path: '/ingest/tasks', name: 'tasks', component: './ingest/tasks' },
      { path: '/ingest/access', name: 'access', component: './ingest/access' },
      { path: '/ingest/files', name: 'files', component: './files' },
      {
        path: '/ingest/assistant',
        name: 'assistant',
        component: './ingest/assistant',
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
    ],
  },
  // ③ 数据治理(安全扫描 / 质量评估 / 数据加工 / 数据标注)
  // 纯菜单分组(无 path):页面路径不变,避免 RR「绝对子路径须含父前缀」约束
  {
    name: 'governance',
    icon: 'safety',
    routes: [
      {
        path: '/content-safety',
        name: 'contentSafety',
        component: './content-safety',
      },
      { path: '/quality', name: 'quality', component: './quality' },
      {
        path: '/quality/editor',
        name: 'quality-editor',
        component: './quality/editor',
        hideInMenu: true,
      },
      { path: '/processing', redirect: '/processing/jobs' },
      { path: '/processing/jobs', name: 'processing', component: './processing' },
      {
        path: '/processing/editor',
        name: 'processing-editor',
        component: './processing/editor',
        hideInMenu: true,
      },
      { path: '/operators', name: 'operators', component: './processing/market' },
      { path: '/annotation', name: 'annotation', component: './annotation' },
    ],
  },
  // ④ 运维监控(跨切面:任务 / 血缘 / 审计)— 纯菜单分组(无 path)
  {
    name: 'ops',
    icon: 'dashboard',
    routes: [
      { path: '/data-tasks', name: 'dataTasks', component: './data-tasks' },
      { path: '/lineage', name: 'lineage', component: './lineage' },
      {
        path: '/security',
        name: 'security',
        access: 'canAdmin',
        component: './security',
      },
    ],
  },
  {
    component: './exception/404',
    layout: false,
    path: './*',
  },
];
