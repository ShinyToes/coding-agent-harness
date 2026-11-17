# 本地 Langfuse 部署说明

Docker Hub 在本机不可达，镜像统一走 `docker.m.daocloud.io`；MinIO 官方镜像被镜像站拒绝，S3 用 SeaweedFS 代替。

```powershell
# 启动（6 个容器：web / worker / postgres / clickhouse / redis / seaweedfs）
docker compose -f deploy/langfuse/docker-compose.yml up -d

# 建 S3 bucket（SeaweedFS 暴露在 8333）
python deploy/langfuse/make_bucket.py

# 等 web 就绪（一般 30–60s）
curl http://127.0.0.1:3000/api/public/health

# 查询导出的 trace / span 树
python deploy/langfuse/verify.py
```

## 预置凭据（仅本地 demo）

| 项 | 值 |
|---|---|
| UI | <http://127.0.0.1:3000> |
| 登录邮箱 / 密码 | `dev@harness.local` / `harness-local-123` |
| public key | `pk-lf-harness-local` |
| secret key | `sk-lf-harness-local` |
| OTLP endpoint | `http://127.0.0.1:3000/api/public/otel/v1/traces` |

`LANGFUSE_INIT_*` 会在首次启动时自动创建组织/项目/账号，不需要打开页面注册。

## harness 侧配置

```powershell
$env:HARNESS_OTLP_ENDPOINT = "http://127.0.0.1:3000/api/public/otel/v1/traces"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("pk-lf-harness-local:sk-lf-harness-local"))
$env:HARNESS_OTLP_HEADERS = "Authorization=Basic $b64"
python examples/live_smoke.py --scenario bug
```

注意 endpoint 要带 `/v1/traces`（OTLP HTTP 路径），只写 `/api/public/otel` 会 404。

## 运维

```powershell
docker compose -f deploy/langfuse/docker-compose.yml ps
docker compose -f deploy/langfuse/docker-compose.yml logs -f langfuse-web
docker compose -f deploy/langfuse/docker-compose.yml down          # 保留数据卷
docker compose -f deploy/langfuse/docker-compose.yml down -v       # 连数据一起删
```

## 告警

这是本地演示配置：`SALT`、`NEXTAUTH_SECRET`、`ENCRYPTION_KEY`、账号密码都是明文默认值，**不要暴露到公网或用于生产**。
