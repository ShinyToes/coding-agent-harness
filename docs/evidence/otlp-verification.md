# OTLP 导出验证（B-026）

## 结论

harness 的 OTel span 能通过 OTLP/HTTP 导出到 **本地自托管的 Langfuse v3**，并按 `session.id` 归组、`turn → tool` 形成父子树。已实测（2026-11-05）。

## 本地 Langfuse 栈

- Docker daemon 可用，但 Docker Hub 直连超时 → 镜像走 `docker.m.daocloud.io` 镜像站
- MinIO 官方镜像被镜像站拒绝 → 用 **SeaweedFS** 做 S3 兼容存储
- 组成：`langfuse-web` + `langfuse-worker` + `postgres` + `clickhouse` + `redis` + `seaweedfs`
- 部署方式与凭据：见 [deploy/langfuse/README.md](../../deploy/langfuse/README.md)

## 复现

```powershell
docker compose -f deploy/langfuse/docker-compose.yml up -d
python deploy/langfuse/make_bucket.py

$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("pk-lf-harness-local:sk-lf-harness-local"))
$env:HARNESS_OTLP_ENDPOINT = "http://127.0.0.1:3000/api/public/otel/v1/traces"
$env:HARNESS_OTLP_HEADERS  = "Authorization=Basic $b64"
$env:DEEPSEEK_API_KEY = "..."
python examples\live_smoke.py --scenario feature

python deploy/langfuse/verify.py
```

## 实测结果

真模型会话 `b7c5852f62a7`（feature 场景）导出后在 Langfuse 中：

```
traces=5
- 11391afe4b96db91 name=harness.turn session=b7c5852f62a7 latency=1.376 observations=2
- 6450c7b64d5ef5ad name=harness.turn session=b7c5852f62a7 latency=1.49  observations=2
- 7d660d0a3e950128 name=harness.turn session=b7c5852f62a7 latency=0.796 observations=2
- a31171e322b4f622 name=harness.turn session=b7c5852f62a7 latency=1.211 observations=2
- 44a51ad49f071990 name=harness.turn session=b7c5852f62a7 latency=2.365 observations=3
```

单个 trace 的 observation 树：

```
harness.turn   parent=<root>        type=GENERATION latency=1.376
harness.tool   parent=harness.turn  type=SPAN       latency=0.002

usage: {'unit': 'TOKENS', 'input': 2348, 'output': 89, 'total': 2437}
```

- 同一 session 的多个 turn 共享 `sessionId`，Langfuse 的 Sessions 视图可直接聚合
- `harness.turn` 被识别为 `GENERATION`（因为带 `gen_ai.*` 属性），`harness.tool` 是 `SPAN`
- UI：<http://127.0.0.1:3000/traces/11391afe4b96db9166f5564c679e2620>

## 另一条已验证的路径（Phoenix）

在 Langfuse 栈搭好之前，先用 Arize Phoenix（本地 pip、无账号）验证过同样的 OTLP 链路：两次真模型会话共 23 spans / 10 traces，父子关系与 `session.id` 均正确。说明 harness 侧不绑定任何特定后端。

## 未完成

- 没有自动化截图：Langfuse UI 是前端 SPA，命令行环境没有浏览器；本文件用 Langfuse 公开 API 的返回作为证据。
- 本地凭据是演示值（`pk-lf-harness-local` / `sk-lf-harness-local`），未做生产加固。
