# api-push-mock

模拟"外部系统"向 AI 数据平台的 **API 推送** 端点投递数据,用于端到端验证「API 推送」类数据源的接入链路。

## 平台契约

```
POST /api/v1/ingest/push/{token}
```
- `token` 即凭证(无登录态),创建 api 数据源时由平台生成并回填到 `config.url`。
- body 支持三种形态:`{records:[...]}` / 裸数组 `[{...}]` / jsonl(每行一条 JSON)。
- 成功 → `{data:{datasetId,versionId,versionNo,rows},success:true}`;坏 token → 401;超限(60/60s)→ 429。

实现见 `backend/app/api/v1/ingest_push.py`。

## 运行

```bash
# 需 Go 1.26+(若机器有多套 Go 导致编译版本不匹配,用:
#   env -u GOROOT "/c/Program Files/Go/bin/go" build -o api-push-mock.exe .)
go build -o api-push-mock.exe .

./api-push-mock.exe --port 18080 \
  --push-url "http://127.0.0.1:18003/api/v1/ingest/push/<token>"
# 或用环境变量:PLATFORM_PUSH_URL=... ./api-push-mock.exe
```

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 服务状态与端点说明 |
| GET | `/sample?n=5` | 预览生成的模拟记录(不推送) |
| POST | `/push?n=10&format=json\|jsonl&url=` | 生成 n 条记录推送到平台,返回平台响应 |

`?url=` 可覆盖启动时的 `--push-url`。

## 端到端测试流程

```bash
# 1. 登录拿 cookie
curl -s -c cookies.txt -X POST http://127.0.0.1:18003/api/login/account \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"ant.design","type":"account"}'

# 2. 创建 api 数据源,响应里 data.config.url 即推送地址
curl -s -b cookies.txt -X POST http://127.0.0.1:18003/api/v1/datasources \
  -H "Content-Type: application/json" \
  -d '{"name":"mock-api-push-test","type":"api","config":{}}'

# 3. 启动本服务(填上一步的 url),触发推送
./api-push-mock.exe --push-url "<上一步的 url>" &
curl -s -X POST "http://127.0.0.1:18080/push?n=20"
# → 平台返回 {data:{datasetId,versionId,versionNo,rows},success:true}
```
