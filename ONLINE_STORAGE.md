# 会话采集与线上 PostgreSQL

## 更新后你会看到什么

从现有分支运行 update.cmd 更新。每次启动 start.cmd 或 start-with-analysis.cmd 都新建一个会话；暂停/恢复仍是同一个会话。正常从托盘退出会关闭会话、生成本次 ZIP 包。强制关机可能留下 recording 状态，不能直接上传；确认采集已结束后用 `finish-session --data 会话路径` 标记 interrupted。

```
data/
  device.json
  index.html                         # 双击查看全部会话
  sessions/2026-11-11/<session_id>/
    session.json                     # 设备、起止时间、状态、记录数量
    lifecycle.jsonl                  # active/paused/idle/locked/closed 时间
    events.sqlite3                   # 当前会话的证据和分析
    screenshots/
    frame-pairs.jsonl                # 相邻截图对，附时间和初始标签
    experiences/timeline.html
    experiences/experiences.jsonl
    experiences/training_candidates.jsonl
    capture-<timestamp>.zip           # 结束时的快照包
    sync.json                        # 上传状态；失败不删除本地记录
```

UTC 时间和本地偏移继续保留；图片重复时复用文件，但每次观察仍保留时间。每次开始捕获新的 session_id，共享设备 device_id。在线同步只接受已结束或明确标记中断的会话。结束包是当时的快照，后续分析结果可能更新；再次 sync 会上传新版，不覆盖线上历史快照。

旧数据：退出所有采集/分析程序后双击 `migrate-history.cmd`。按旧 session_id 复制到 sessions/legacy；没有编号的旧数据归入一个“边界未知”的历史组。原 SQLite 和图片不删除，迁移可重复运行。迁移后 `analyze-local.cmd` 依次处理各会话；没有新目录时仍兼容旧 data。不要同时从旧库和迁移副本分析同一份数据。

迁移是一次性快照，不会把后来写入旧库的记录合并进已迁移目录。更新后应使用新采集器，不再运行旧版本。旧 records 的实际启动/停止时间无法恢复，记录为首末观察范围。

## 服务端架构

采集端 → HTTPS Bearer API → PostgreSQL 元数据/证据/分析 + 私有对象存储图片/数据包。

这是一位数据持有人的私有服务，支持多设备，不是多租户 SaaS。API token 拥有本实例全部数据的上传/读取权限；不能把它发给不可信第三方。采集端没有数据库密码。当前没有公开网页、公开图片桶或匿名下载。

`server/schema.sql` 为可重复执行的 v1 schema，包含 devices、capture_sessions、capture_events、blobs、analysis_results、capture_bundles、dataset_artifacts、schema_versions。时间使用 timestamptz，事件正文使用 JSONB；每条事件唯一编号、每包 SHA-256。更新分析结果会保留多个版本。

服务端在全部文件校验并存储成功后，用事务登记元数据。重复提交同一包不会新增重复行；相同事件号但证据不同会拒绝并回滚。对象写入后数据库失败可能留下未引用对象，不能删除仍被历史包引用的对象；当前未自动清理孤立对象。

## 部署方式 A：自己的 Linux 服务器，Docker Compose

需要 Docker Engine 和 Compose。先在服务器克隆本仓库的工作分支。进入 server 文件夹，将 `.env.example` 复制为 `.env`，分别填写随机密码和 token（可分别运行 `python -c "import secrets; print(secrets.token_hex(32))"` 生成；不要把值提交到 Git）。Compose 的数据库密码建议使用 hex，避免 URL 转义问题。

```sh
cd server
cp .env.example .env
# 在服务器本地编辑 .env
 docker compose --env-file .env up -d --build
 docker compose logs --tail 50 api
```

默认 API 只绑定服务器 127.0.0.1:8000，PostgreSQL 不对外映射端口。需要用 Caddy/Nginx 在你的域名上配置 HTTPS 反向代理到这个端口；先配置 token，再开放域名。反向代理上传限制需至少 256 MiB，并设置足够的请求超时。不要把 HTTP 开放到公网，也不要关闭客户端 TLS 校验。

容器重启保留两个命名卷：postgres-data 和 object-data。备份必须同时包含数据库与对象卷；不要运行 `docker compose down -v`，它会删除数据卷。上线前完成一次备份恢复演练。

## 部署方式 B：托管 PostgreSQL + S3 兼容存储

在你选择的主机/容器平台运行 API；数据库与 S3 供应商可替换。配置环境变量：

- `CAPTURE_DATABASE_URL`：托管 PostgreSQL DSN，使用服务商要求的 TLS 配置（通常包含 sslmode=require 或 verify-full）。
- `CAPTURE_API_TOKEN`：至少 32 字符随机值。
- `CAPTURE_S3_BUCKET`：私有桶。
- `CAPTURE_S3_ENDPOINT`：非 AWS 的 S3 兼容 HTTPS endpoint；AWS 可省略。
- `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_DEFAULT_REGION`：按供应商配置，仅给此桶必要权限。

```sh
python -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/python -m server.init_db
.venv/bin/uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

API 前仍需 HTTPS。没有 CAPTURE_S3_BUCKET 时使用 CAPTURE_OBJECT_DIR 指定的服务器私有磁盘。无论哪种后端，数据库不存截图二进制。数据库软件免费，不代表托管主机、存储、备份或流量免费。

## Windows 配置与同步

在本机 PowerShell 设置服务 URL 和 token（真实值只在本机填写）：

```powershell
$env:CAPTURE_SERVER_URL = 'https://你的服务域名'
$env:CAPTURE_API_TOKEN = '在本机填入服务端的token'
.\.venv\Scripts\python.exe -m collector.pipeline sync --data data
```

环境变量只对当前 PowerShell 及其子进程生效。要双击 sync-online.cmd 使用，请在 Windows 用户环境变量设置中保存这两个值，再重新打开资源管理器/登录会话。不要把 token 放进仓库中的 cmd 或 config.json。

可选设置 CAPTURE_AUTO_SYNC=1：采集结束或 analyze-local 完成会话分析后自动尝试上传。没有配置时完全本地运行。每个包最多自动重试 3 次（网络错误、429、部分 5xx），最终失败写 sync.json，之后双击 sync-online.cmd 重试。上传成功不会删除本地原始数据。收到回执才标记成功；连接中断后重传可安全去重。当前是整包重传，不是字节级断点续传。

`sync` 默认遍历已知会话；正在采集的会话跳过并提示。单包压缩大小上限 256 MiB，展开上限 1 GiB，超限会明确失败并保留本地包；当前需使用更短会话，不自动分片。

## 换设备采集，拿回本机分析

```powershell
# 看到线上批次及其 bundle_sha256
.\.venv\Scripts\python.exe -m collector.pipeline remote-list
# 下载到新的本地目录，校验后解包，不覆盖现有目录
.\.venv\Scripts\python.exe -m collector.pipeline download SHA256 --data imports\batch-001
# 用本地 Ollama 分析该会话，再上传新版结果
.\.venv\Scripts\python.exe -m collector.pipeline analyze --data imports\batch-001
.\.venv\Scripts\python.exe -m collector.pipeline sync --data imports\batch-001
```

未部署服务时仍可用 pack/unpack，通过自己的方式传 ZIP，数据格式相同。

## 截图筛选与标注

默认保留所有不同像素的截图，完全相同的画面复用图片文件。`frame-pairs.jsonl` 包含前后时间、截图引用、exact_duplicate、change、continuous。完全相同只能证明像素相同，不能证明属于同一段经历，因此 continuous 仍为 unknown。

可用已验证的本地 Ollama 16K/关闭思考，为非重复图片对生成弱标签：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline label-pairs --data data\sessions\日期\会话编号 --limit 20
```

输出 frame-labels.jsonl，包含 small/large/unknown、yes/no/unknown、理由、模型来源、未审核状态。不会据此删除图片或自动改变采集规则。先人工抽查漏掉关键变化的比例，再考虑 CNN；本次没有声称训练了一个尚无可靠标注数据的小模型。页面空闲、崩溃与错误的语义识别也尚未实现，目前只记录系统空闲/锁屏/手动暂停。

## 验证范围

自动测试覆盖：独立会话、暂停状态、旧库无损迁移、打包还原、认证拒绝、真实 PostgreSQL 入库、重复上传、哈希损坏、身份冲突回滚、下载还原。使用合成数据，未上传用户截图。S3 适配器提供了实现，但当前测试后端是服务器私有磁盘；需要在选定供应商上完成联调。Windows 新会话流程仍需本机验收。
