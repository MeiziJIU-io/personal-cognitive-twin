# 两端工作流：实时采集 → 经历 → 训练候选

## 产品边界

采集端只负责观测与落盘，不依赖 Ollama。分析端在你的电脑运行 Ollama，可读取本机 data 或从其他电脑导入的数据。两端共享版本化证据格式；手机未来实现同一格式的采集适配器，目前没有手机采集实现。

实时是采集实时。模型异步处理，不能承诺每 5 秒截图就每 5 秒完成推理；当前为串行队列，保留全部事件，吞吐不足会积压。仅凭一次约 39 秒的终端测试不能推算稳定吞吐。分析器现在打印每条实际耗时，默认不偷偷跳过积压证据。

## 时间规范

新采集的每条记录都有：

- `event_uid`：全局唯一事件号；`device_id`：随机设备标识；`session_id`：本次启动标识。
- `at` / `capture_started_at`：UTC，开始读取界面的时间。
- `capture_finished_at`：UTC，读取和截图结束的时间。
- `screenshot_captured_at`：发生屏幕读取时记录 UTC 时间。
- `local_time`：采集设备当时的本地时间及 UTC 偏移。
- `monotonic_seconds`：同一会话内的单调时钟，辅助诊断系统时间变化；不能跨设备比较。
- 分析结果中的 `analyzed_at` 是分析时间，不能替换采集时间。

重复内容也保留每次成功观察的时间；只有图片文件做去重。空闲/暂停期间不产生观察。经历的起止只是首末观察时间，不表示中间一直活跃。设备时钟可能不准，当前不提供跨设备时钟校正。旧记录保留原 at；缺失的时区偏移、会话和完成时间不会编造，标为未知。旧版已去重丢掉的观察时刻无法恢复。

## 原目录内升级后的启动方式

保留旧 data，不需重建数据库。Python 依赖未增加。

- `start.cmd`：只采集，适合其他电脑。
- `start-with-analysis.cmd`：采集 + 本地异步分析；托盘退出时结束两个进程，未完成的分析仍留在队列。
- `analyze-local.cmd`：只分析当前积压，处理完成自动退出。

这三个入口应在已经安装依赖的原工程目录运行。本地模型默认 `collector-qwen:16k`，接口 `http://127.0.0.1:11434/api/chat`。新版使用原生 Ollama 接口、`think=false`、`num_ctx=16384`、`num_predict=2048`；记录实际耗时与原始响应。不要同时运行旧 collector.agent 和新 pipeline 分析器。

重测之前失败的截图事件：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline analyze --event-id 3
```

显式 event-id 可重新分析失败或已完成事件，保留原始证据并更新解释。默认只处理 pending。普通失败最多尝试三次。一次性批处理最多三次后保留 failed。

## 另一台电脑的数据带回来

采集电脑导出（可在采集运行时导出，SQLite 使用一致性备份）：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline pack --data data --out capture-20261110.zip
```

把该数据包通过你选择的方式带回本机；程序不自动上传。包里包含 SQLite、被引用的截图和 SHA-256 清单，不包含 Python 环境与模型权重。包本身未加密，校验和只能检测损坏，不是可信发送者签名。

本地导入新的独立目录，再分析：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline unpack capture-20261110.zip --data imports\deviceA-batch1
.\.venv\Scripts\python.exe -m collector.pipeline analyze --data imports\deviceA-batch1
```

不覆盖现有目录。每个包/设备独立存储，事件 UUID 可供后续跨批次去重；当前不自动合并重叠数据包，也不做跨设备同一人的身份推断。

## 自动产生什么

分析每条成功后更新 `data/experiences/`（导入场景在对应导入目录内）：

1. `experiences.jsonl`：时间连续、同设备同会话同应用的经历草稿。间隔超过 120 秒、超过 5 分钟窗口或换应用会分段；这是初始分段策略，不等于精确任务边界。
2. `timeline.html`：本地可打开的时间线，显示观察时间、描述、证据号、截图相对路径和未知项。
3. `training_candidates.jsonl`：自动生成的屏幕观察回顾样本，只有整段分析完成才输出；含未审核标签。用于试验的弱标注候选，不表示用户亲自确认了经历。

意图、实际行为、结果默认未知。屏幕文字或模型说明不能直接证明“我执行了命令”“我完成了任务”或“我认同某观点”。自动样本回答为“记录显示什么”，不冒充用户意图。图片细字、跨窗口关联和总结仍需抽查。

手动刷新时间线（包括尚未分析的证据）：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline build --data data --out data\experiences
```

## 高质量确认版训练导出

打开时间线，对照原图后将真实经历写入 UTF-8 文本文件 `confirmed.txt`（可包含用户补充的实际行动和结果），用对应 episode_id 登记：

```powershell
.\.venv\Scripts\python.exe -m collector.pipeline review EPISODE_ID --text-file confirmed.txt --reviewer owner
.\.venv\Scripts\python.exe -m collector.pipeline train-export --out training\confirmed-v1.jsonl
```

导出的是 messages 格式的经历回顾 SFT 数据，另有 `.provenance.json` 保存来源、时间和审核版本。不是偏好对齐数据或行为克隆轨迹，不自动启动模型训练。也可以用 review --reject 排除。只有当前版本已确认记录进入确认版；重建导致草稿内容改变时旧确认不会沿用。

整个过程不要求为每条记录调用云服务。自动化到经历草稿与训练候选已实现；实际微调还需明确目标模型、训练目的、训练/验证划分与评估标准。同一段经历的相邻记录不能随机拆到训练和验证集，否则会产生数据泄漏。

## 已知限制及验证状态

Linux 中完成调度、SQLite、传输校验、导入、模拟 Ollama 分析、时间分段、审核和训练导出的自动测试。Windows 实机新版尚未验证；此前用户只验证过旧版采集及单独的 Ollama 16K/关闭思考截图调用。

UI Automation 控件名称暂不能证明正文完整，因此新版保守地将其标为不完整，使用 5 秒最短截图间隔。截图仍为采样，不是每次点击的准确动作轨迹；前台窗口覆盖、极短弹窗可能造成缺失。每次观察保留时间会增加记录量，目前没有自动保留期限、增量跨包合并、磁盘配额或推理速度自适应。

## 与项目总体方案的对应关系

本次为采集及离线分析工程原型，不代表 TASKS.md 的阶段验收完成。尚未完成 screenpipe 对比、整日资源报告、自动原始帧清理、完整脱敏、磁盘配额和交互式审阅界面。当前训练导出是经历回顾样本，不是原方案的 Track P 选择预测数据；情境、选择集、实际行为与曝光来源还需后续结构化提取与验证。未在任务清单中勾选这些工作。
