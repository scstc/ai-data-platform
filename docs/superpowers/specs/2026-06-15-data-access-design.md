# 数据接入(Data Access)设计

- 日期:2026-06-15
- 状态:待评审
- 关联:#15(分类管理)、#18/#19(外部 S3 托管 / 平台 MinIO 文件管理)、本地上传(被替换)

## 1. 背景与目标

参考 BCC「数据管理 / 数据接入」页(见用户截图),在本平台 `数据管理(/ingest)` 菜单组内新增 **数据接入** 页,并 **替换** 现有 `本地上传(/ingest/upload)`。

核心能力:按文件类型分栏接入数据,每种类型支持两条导入路径——

1. **本地文件上传**:从本地磁盘选文件上传入库;
2. **文件管理文件上传**:从平台对象存储(文件管理)中选已有文件,**零拷贝**登记入库。

接入产物统一为 **受管数据集(Dataset)**,带 `dataType`(= 类型栏)、`category`(分类)等元信息,可在数据集仓库 / 数据加工中继续使用。

## 2. 范围

### 2.1 类型栏(左侧竖栏,8 项)

| 栏 key(=dataType) | 标签 | 扩展名白名单 | 本地上传落地 | 文件管理零拷贝 |
|---|---|---|---|---|
| `csv-tsv` | CSV/TSV接入 | csv, tsv | jsonl 规范化 | ✅ |
| `image` | 图像接入 | png, jpg, jpeg, gif, bmp, webp | **raw 原样存** | ✅ |
| `audio` | 音频接入 | mp3, wav, flac, m4a, aac, ogg | **raw 原样存** | ✅ |
| `video` | 视频接入 | mp4, avi, mov, mkv, webm | **raw 原样存** | ✅ |
| `pdf` | PDF接入 | pdf | jsonl 规范化 | ✅ |
| `json` | JSON接入 | json, jsonl | jsonl 规范化 | ✅ |
| `log` | 日志接入 | log, txt | jsonl 规范化 | ✅ |
| `sql` | SQL接入 | —(无上传) | — | — |

- **文本/结构化类**(csv-tsv / pdf / json / log)走现有 `land_upload`(规范化为 jsonl,带行数 `rows`,支持表格预览)。
- **二进制类**(image / audio / video)走新增的 **raw landing**(原样存储,`rows=null`,**不解析**,预览置灰,仅下载)。
- **SQL 栏特殊**:不做上传,渲染引导卡片 + 跳转按钮到 `数据源管理(/ingest/datasources)` 与 `采集任务(/ingest/tasks)`(已支持 DB 连接拉数,不重复造轮子)。

### 2.2 不在本期范围

- 图像缩略图 / 音视频在线播放预览(二进制仅"下载",预览置灰)。
- SQL 栏内联连接 + 查询(复用现有数据源管理)。
- 上传弹框中的「文件夹」批量上传 Tab(截图有,但本期只做「文件」;文件管理侧目录浏览仍支持进入子目录选单文件)。

## 3. 前端设计(方案 A:独立新页)

### 3.1 路由与菜单

- `config/routes.ts`:
  - 删除 `/ingest/upload` 路由,新增 `/ingest/access` → `./ingest/access`。
  - 旧路径兼容:顶层加 `{ path: '/ingest/upload', redirect: '/ingest/access' }`。
- 菜单 i18n:`menu.ingest.upload` → 改为 `menu.ingest.access`("数据接入" / "Data Access"),`zh-CN` 与 `en-US` 同步。

### 3.2 页面结构 `pages/ingest/access/index.tsx`

还原截图:`PageContainer` 内 `Layout`,左 `Sider`(竖向 `Menu`,8 个类型项),右内容区:

- 工具条:`上传` 按钮(打开上传弹框)、`分类管理` 按钮(打开 `CategoryManager` 抽屉,复用现成组件)。
- 筛选:分类下拉(`listCategories`)+ 文件名搜索框。
- `ProTable<Dataset>` 列:文件名(name)、分类(categoryName)、来源/路径(storageUri 或"本地上传")、大小(size)、类型(dataType)、创建人(creator)、创建时间(createdAt)、操作(下载 / 预览 / 删除)。
  - `request` 调 `listDatasets({ dataType: 当前栏key, name, categoryId, current, pageSize })`。
  - 切换左侧类型栏 → 改 `dataType` 入参并 `reload`。
  - 预览:仅文本/结构化类可用;二进制类按钮置灰。删除:复用现有数据集删除(hosted 版本走"取消托管"门控,沿用 #18 既有逻辑)。
- **SQL 栏**:不渲染表格/工具条,渲染引导卡片(`Result`/`Empty` + 两个跳转按钮)。

### 3.3 子组件

- `pages/ingest/access/constants.ts`:类型栏分类法(key / label / 扩展名 / 是否二进制 / accept 串),迁移自旧 `upload/utils.ts` 的白名单与 `getExtension`/`formatFileSize` 等纯函数。
- `pages/ingest/access/UploadModal.tsx`:上传弹框。
  - 字段:选择分类(Select,可空)、导入方式(Radio:本地文件上传 | 文件管理文件上传)。
  - **本地**:`Upload`,`accept` = 当前栏扩展名;`customRequest` → `uploadDataset(FormData{file, dataType=栏key, categoryId})`。
  - **文件管理**:内嵌 `FileManagerPicker`,选中 1+ 对象后 → `hostPlatformFiles({ bucket, keys, dataType=栏key, categoryId })`。
  - 成功后关闭弹框 + 通知宿主 `reload`。
- `pages/ingest/access/FileManagerPicker.tsx`:平台对象存储浏览器(复用 `listPlatformBuckets`/`listFiles`),支持选桶、进目录、勾选文件;按当前栏扩展名过滤可选项。

### 3.4 删除

- 删除目录 `pages/ingest/upload/`(`index.tsx` + `utils.ts`);纯函数迁入 `access/constants.ts`。确认无其他业务引用(`.umi` 为自动生成,重启重建)。

### 3.5 服务层

- `services/data-platform/api.ts` 新增 `hostPlatformFiles(body)` → `POST /datasets/host-platform`;`typings.d.ts` 增对应入参/出参类型(参照现有 `hostS3`)。

## 4. 后端设计

### 4.1 新增 raw landing(二进制本地上传)

- `app/services/engine.py` 新增 `land_upload_raw(session, *, content, filename, data_type, category_id)`:
  - 把原始字节存到数据集存储(沿用现有 dataset 存储目录 / 平台 MinIO,与 `land_upload` 的落地介质一致)。
  - 建 `Dataset` + `DatasetVersion`:`format=扩展名`、`rows=null`、`size=len(content)`、`origin='managed'`(与既有 `land_upload` 一致;`origin` 取值仅 `managed`/`hosted`)、`storage_uri` 指向原始对象。**不做 jsonl 规范化、不解析**。磁盘写失败时回滚 + 清理半成品,抛 `LandingError`。
- `app/api/v1/datasets.py` 的 `POST /datasets/upload`:按 `data_type`/扩展名判定走 `land_upload`(文本类)或 `land_upload_raw`(二进制类)。

### 4.2 新增 `POST /datasets/host-platform`(平台对象零拷贝)

- 仿 `host_s3`,但用 `external_store.platform_config()` 取连接(而非数据源 config),`source_datasource_id=NULL`。
- 入参:`bucket`、`keys[]`、`dataType`、`categoryId`、可选 `name`。
- 逐 key:`stat_object(platform_config(), bucket, key)` 取 size、扩展名定 format → 建 `Dataset` + `DatasetVersion`(`origin='hosted'`、`storage_uri='s3://bucket/key'`、`source_datasource_id=NULL`、`rows=null`)。**不下载**。
- 格式白名单需含本期二进制类型(见 4.3)。

### 4.3 格式白名单扩展

- `LANDABLE_FORMATS`(及上传/托管校验用到的白名单)新增:image(png/jpg/jpeg/gif/bmp/webp)、audio(mp3/wav/flac/m4a/aac/ogg)、video(mp4/avi/mov/mkv/webm)、log(log)。
- 文本类已含 csv/tsv/json/jsonl/txt/pdf,无需新增。

### 4.4 hosted 预览/加工回退到平台存储

- 现状:hosted 版本预览/物化按 `source_datasource_id` 取数据源 config,为空时报"数据源不存在"。
- 改动:当 `version.origin=='hosted'` 且 `source_datasource_id` 为空且 `storage_uri` 以 `s3://` 开头 → 用 `external_store.platform_config()` 作为连接 config(`datasets.py` 预览分支 + `engine`/物化路径同步)。
- 二进制 hosted/upload 版本:预览直接返回不可预览态(不尝试 `head_records`)。

### 4.5 删除门控

- 沿用 #18:含 hosted 版本的数据集走"取消托管"(`unhost`),绝不删源对象。平台对象零拷贝登记的数据集同样适用(取消托管只删平台记录,不动 MinIO 对象)。

## 5. 数据流

**本地上传(文本类)**:选文件 → `POST /datasets/upload`(land_upload→jsonl) → Dataset(rows≠null) → 列表刷新。

**本地上传(二进制类)**:选文件 → `POST /datasets/upload`(land_upload_raw→原样存) → Dataset(rows=null) → 列表刷新。

**文件管理零拷贝(任意类)**:选平台对象 → `POST /datasets/host-platform`(stat→登记,不下载) → Dataset(origin=hosted, rows=null) → 列表刷新。

**预览**:文本类 → 表格预览;hosted 文本类 → platform_config 取前 N 行;二进制类 → 置灰。

## 6. 测试

### 后端
- `host-platform`:零拷贝登记成功(mock `stat_object`,**断言未调用下载**);未配置平台存储 → 503/400;非白名单格式 → 400。
- `land_upload_raw`:二进制上传 → Dataset `rows=null`、`format=ext`、`size` 正确、内容原样可取回。
- 预览回退:hosted + `source_datasource_id=NULL` → 用 platform_config 取前 N 行(不报"数据源不存在")。
- 白名单:新增 image/audio/video/log 扩展名被接受;非法扩展名被拒。

### 前端
- 类型栏切换 → `listDatasets` 入参 `dataType` 改变并 reload(断言请求参数)。
- 上传弹框:本地路径 `accept` 限定当前栏扩展名;文件管理路径选中对象 → 调 `hostPlatformFiles` 携带正确 `dataType`/`keys`。
- SQL 栏:渲染引导卡片 + 跳转按钮(无表格/上传)。
- 分类管理抽屉增改删后宿主分类筛选与列表刷新。

## 7. 验收标准

1. `数据管理` 菜单出现「数据接入」,「本地上传」消失;旧 `/ingest/upload` 自动重定向到 `/ingest/access`。
2. 8 个类型栏齐全;7 个文件栏可上传(本地 + 文件管理两条路径均可),SQL 栏为引导页。
3. 二进制类型(图像/音视频)本地上传成功入库且不报解析错误;列表正确显示类型/大小/创建信息。
4. 文件管理零拷贝登记不产生二次存储(后端断言不下载)。
5. 分类下拉 + 文件名搜索可过滤列表;分类管理可增改删并联动。
6. `npm run lint`、`npx antd lint ./src`、前后端测试全绿。
