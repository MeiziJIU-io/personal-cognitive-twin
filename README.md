# Windows 实时采集 demo

**桌面窗口：** 更新后双击 `open-app.cmd`，设置保存路径、开始/停止采集、查看批次，并选择本地 Ollama 或 API 分析。详见 [DESKTOP.md](DESKTOP.md)。

**新版（会话隔离与线上存储）：** 先阅读 [ONLINE_STORAGE.md](ONLINE_STORAGE.md)。新采集按批次保存在 `data/sessions/日期/会话编号/`，总入口为 `data/index.html`。下面的扁平 `data` 路径描述的是兼容旧版模式。

新版两端流程、时间字段、离线迁移和训练导出以 [WORKFLOW.md](WORKFLOW.md) 为准。下方 collector.agent 是旧版诊断入口；新流程使用 collector.pipeline。

第一阶段：采集电脑前台上下文，保留证据，再交给视觉 agent 整理。需要 Windows 10/11、Python 3.11+，在当前登录用户的交互桌面运行。

## 启动

下载整个项目后双击 `start.cmd`。首次启动会创建虚拟环境并安装依赖，需要网络。蓝色系统托盘图标表示采集程序正在运行；右键可暂停、切换阅读模式、停止并退出。也可以在项目目录执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m collector
```

这是本地常驻进程，启动后持续采集，无需重复点击。此 demo 不自动设置开机启动。关闭/退出后不再采集；重新启动继续追加到同一个数据库。

## 当前机制

- 每秒检测空闲、锁屏和前台窗口。无键鼠操作 60 秒暂停，恢复操作立即进入采集周期。锁屏始终暂停。阅读模式允许静止阅读时继续采集。
- 窗口切换触发采集，同一窗口每 3 秒读取一次 UI Automation 的可见控件名称。读取最多等待 2.5 秒，失败转为截图补充。只读取前台窗口，不记录按键内容或剪贴板。
- 结构化文字不足或读取失败时截图，最短间隔 5 秒；文字较充分时每 30 秒进行一次视觉补充。实际时刻受 3 秒文字周期和系统调用耗时影响，不是硬实时。窗口切换也遵守截图速率限制。
- 截图只截前台窗口矩形，保存完整分辨率 PNG。像素完全相同时复用/跳过，避免丢掉小文字变化。UIA 读取前后及截图之后校验前台上下文，切窗时丢弃本轮证据。
- `complete` 在新版中保守设为 false：菜单文字量不能证明正文完整。界面文字来自控件名称，可能漏掉编辑器正文、Canvas、远程桌面；定期截图补充这些缺口。
- 直接描述只记录“前台应用/窗口标题”，不宣称用户正在阅读或完成某项工作。agent 将观察与不确定性分开，保留原始证据。
- 检测到密码控件时跳过整轮；默认排除常见密码管理器，可在 `config.json` 添加进程名（小写）。这不是完整的敏感信息识别，其他屏幕内容仍可能被保存。

参数在 `config.json` 中，修改后重启生效。空闲只表示没有键鼠输入，无法精确判断人是否在看屏幕。截图是周期采样，极短弹窗和两次采样之间的操作可能漏掉；窗口矩形中也可能包含覆盖窗口。

## 交给 agent 整理

采集和推理解耦：默认只在本地落盘，不会自动调用云端。另开 PowerShell，配置你自己的支持图片和 JSON 输出的 Chat Completions 接口（密钥只设置在本地环境变量中）：

```powershell
$env:COLLECTOR_AGENT_URL = 'https://YOUR-HOST/v1/chat/completions'
$env:COLLECTOR_AGENT_MODEL = 'YOUR-VISION-MODEL'
$env:COLLECTOR_AGENT_KEY = 'YOUR-KEY'
.\.venv\Scripts\python.exe -m collector.agent
```

启动此 worker 会把队列中的文字和对应截图发送到所配置的服务。`--once` 只处理一条。Ctrl+C 停止 worker，不影响采集进程。每条最多尝试 3 次，失败保留证据并标记 failed；成功结果保存在独立 result 字段，绝不覆盖 evidence。进程崩溃后 pending 条目可继续处理；请求成功但本地未提交时可能重复调用服务。每个数据目录只允许一个 worker。

## 数据和查看

- `data/events.sqlite3`：SQLite 证据库，events 表包括 UTC 时间、原始 JSON、pending/done/failed 状态、agent 结果及错误类型。
- `data/screenshots/`：原始截图；记录中的相对路径可用于逐条核对。
- `data/collector.log`：本地异常日志。

可用任意 SQLite 查看器运行 `SELECT id, at, status, evidence, result, error FROM events ORDER BY id DESC LIMIT 50;`。每次成功观察保留时间，图片内容重复时去重文件；记录是离散观察点，不应据此推断精确的停留时长。截图去重仅减少写盘和推理，不减少必要的屏幕读取。

数据不会自动过期，本地没有额外加密，建议放在启用 BitLocker 的个人目录中。停止采集与 worker 后，可删除 `data` 清空本次 demo。当前尚未实现手机端、长期存储治理、自动安装和开机自启。

## 验收

1. 打开记事本输入非敏感测试文字，切换到浏览器。核对数据库中的应用、窗口和 UTC 时间；正文读取不足时应有截图。
2. 60 秒不操作，确认不新增证据/截图；移动鼠标后恢复。开启阅读模式验证静止时可继续采样。
3. 托盘暂停、锁屏，分别确认不新增证据；恢复后继续。
4. 保持静态窗口，检查相同原图不会反复落盘；改动小段文字，检查新证据和新图。
5. 配置视觉接口，确认 pending 变为 done，输出引用的 event_id 与原始证据对应；使用错误凭据验证最终 failed 且证据保留。
6. 逐条抽查截图与描述，统计漏采、错配、错误推断，再调整间隔。不能以 agent 自报置信度替代人工对照。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

开发环境为 Linux，已提供跨平台调度/证据库测试；Win32、UI Automation、托盘、锁屏和真实视觉接口仍须按上述步骤在 Windows 实测，不能把单元测试视为整机验证。

## 使用本机 Ollama（无需云端 API）

Ollama 与采集器都在同一台 Windows 电脑运行时，可以通过本机接口处理数据，不需要云端密钥。先运行 `ollama list`，使用其中完整的模型名称；不要根据“7b”等简称猜测标签。截图处理要求模型和 Ollama 版本都支持图片输入，纯文本模型只能整理文字，不能替代视觉识别。

```powershell
ollama list
$env:COLLECTOR_AGENT_URL = 'http://127.0.0.1:11434/v1/chat/completions'
$env:COLLECTOR_AGENT_MODEL = '替换成 ollama list 中的完整模型名称'
Remove-Item Env:COLLECTOR_AGENT_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m collector.agent --once
```

单条验证成功后去掉 `--once` 持续处理。Ollama 需已运行（必要时单独运行 `ollama serve`）。接口只连接本机且绕过代理，本地请求超时为 180 秒。选用本地下载的模型，避免 Ollama 云模型标签，才能确保推理也在本机执行。尚未连接用户电脑验证具体模型。
