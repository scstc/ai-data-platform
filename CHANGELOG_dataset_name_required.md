# 数据集名称改为必填 — 变更记录

## 概述
数据集名称从"可选+自动派生"改为"必填+用户显式输入",覆盖三大上传入口(前后端双重校验)。

## 变更范围

### 后端 API (backend/app/api/v1/datasets.py)

#### 1. `/datasets/upload-batch` (单一数据接入 + 场景数据导入)
- **校验逻辑** (L555-560):
  ```python
  if not name or not name.strip():
      return JSONResponse(
          status_code=400,
          content={"success": False, "message": "数据集名称不能为空"},
      )
  ```
- **Dataset 创建** (L680-682):
  ```python
  dataset = Dataset(
      id=dataset_id,
      name=name.strip(),  # 移除 fallback: or Path(...).stem
  ```

#### 2. `/datasets/upload-media` (媒体批量接入)
- **校验逻辑** (L379-384):
  ```python
  if not name or not name.strip():
      return JSONResponse(
          status_code=400,
          content={"success": False, "message": "数据集名称不能为空"},
      )
  ```
- **Dataset 创建** (L474-476):
  ```python
  dataset = Dataset(
      id=dataset_id,
      name=name.strip(),  # 移除 fallback: or Path(...).name
  ```

### 前端 UI

#### 1. 单一数据接入 (frontend/src/pages/ingest/local-upload/single.tsx)
- **UI 标签** (L395): `数据集名称 <Text type="danger">*</Text>`
- **占位符** (L398): `placeholder="请输入数据集名称"` (移除"可选,留空则取首个文件名")
- **前端校验** (L268-271):
  ```typescript
  if (!name.trim()) {
    message.warning('请输入数据集名称');
    return;
  }
  ```
- **提交时强制携带** (L277): `fd.append('name', name.trim());` (移除 if 判断)

#### 2. 场景数据文件导入 (frontend/src/pages/ingest/local-upload/ImportCard.tsx)
- **新增 state** (L132): `const [name, setName] = useState('');`
- **新增 UI** (L216-227):
  ```tsx
  <div style={{ marginBottom: 12 }}>
    <Text strong>
      数据集名称 <Text type="danger">*</Text>
    </Text>
    <Input
      placeholder="请输入数据集名称"
      value={name}
      onChange={(e) => setName(e.target.value)}
      allowClear
      style={{ marginTop: 8 }}
    />
  </div>
  ```
- **前端校验** (L168-171):
  ```typescript
  if (!name.trim()) {
    message.warning('请输入数据集名称');
    return;
  }
  ```
- **提交时强制携带** (L176): `fd.append('name', name.trim());`

#### 3. 数据接入弹窗-媒体批量 (frontend/src/pages/ingest/access/UploadModal.tsx)
- **UI 标签** (L271-272): `接入名称 <Text type="danger">*</Text>:`
- **占位符** (L275): `placeholder="请输入数据集名称"` (移除"默认取首个文件名")
- **前端校验** (L149-152):
  ```typescript
  if (!mediaName.trim()) {
    messageApi.error('请输入数据集名称');
    return;
  }
  ```
- **提交时强制携带** (L158): `formData.append('name', mediaName.trim());` (移除 if 判断)

## 未变更部分(保留自动派生)

以下入口**不在本次范围内**,仍保留原有派生逻辑:

1. **数据接入弹窗-逐文件上传** (`/datasets/upload`) — 仅覆盖媒体批量,逐文件上传未改
2. **托管 S3 / 文件管理零拷贝** (`/datasets/host-s3`, `/datasets/host-platform`) — 多对象场景,派生名为`基础名/对象名`
3. **采集任务生成** (`/ingest-tasks/{id}/generate-dataset`) — 使用 `task.name`
4. **对象存储采集** (S3/HDFS connectors) — 使用 `task.name or datasource.name`
5. **API 推送入站** (`/ingest/push/{token}`) — 自动派生名
6. **加工任务产物** (processing/distillation/augment/make editors) — 产物版本追加到现有数据集或用户选择的输出数据集

## 校验行为

### 前端
- 提交时拦截空值/纯空格,显示友好提示 `message.warning('请输入数据集名称')`
- 不阻止用户输入,仅在提交时检查

### 后端
- 校验 `if not name or not name.strip()`,返回 `400 {"success": False, "message": "数据集名称不能为空"}`
- 创建时使用 `name.strip()` 去除首尾空格
- 与前端形成双重防护,防止 API 直接调用时绕过

## 测试覆盖

### 单元测试 (test_name_required.py)
```
✓ upload-batch | ✓ upload-media | 空值 (None)
✓ upload-batch | ✓ upload-media | 空字符串 ('')
✓ upload-batch | ✓ upload-media | 纯空格 ('  ')
✓ upload-batch | ✓ upload-media | 正常名称 ('valid_name')
✓ upload-batch | ✓ upload-media | 带空格(可 trim) ('  trimmed  ')
```

### 手动测试检查项
- [ ] 单一数据接入:不填名称时提交被拦截
- [ ] 单一数据接入:AI 命名按钮仍正常工作
- [ ] 场景数据导入:各语义类型(COT/QA/preference/多模态等)都有名称输入框
- [ ] 媒体批量接入:图像/音频/视频三种模态都要求填名称
- [ ] 后端 400 错误:直接 POST 空 name 时收到明确错误消息

## 兼容性

- **破坏性变更**: 是(API 签名未变,但行为变化 — 空 name 从 200 成功变为 400 失败)
- **影响面**: 仅影响三个用户上传入口,不影响自动化采集/加工流程
- **迁移成本**: 无需迁移(纯增量行为,存量数据不受影响)

## 回滚方案

若需回滚,恢复以下四处改动:
1. `datasets.py:555-560` — 删除 upload-batch 校验
2. `datasets.py:682` — 恢复 `or Path(...).stem`
3. `datasets.py:379-384` — 删除 upload-media 校验
4. `datasets.py:476` — 恢复 `or Path(...).name`
5. 前端三个文件回退(或在 UI 层保留必填提示,仅依赖后端兜底)

## 相关链接

- 用户需求: "数据集名称要改成必填的,程序不会自动生成名称"
- 变更 PR: (待补充)
- 测试文件: `test_name_required.py`
