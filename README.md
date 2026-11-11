# 个人经历采集器

Windows 桌面采集工具：记录带时间的屏幕证据，按采集会话保存，用本地 Ollama 或兼容 API 分析，再查看时间线和经历草稿。

## 使用

1. 首次安装 Python 3.11+（官方 Windows 安装包含 Tkinter）。
2. 双击 **open-app.cmd** 打开窗口；首次会自动安装项目依赖。
3. 在窗口选择保存位置，点击开始采集；从系统托盘恢复窗口或停止。
4. 选择采集批次，配置本地模型或 API，点击开始分析。
5. 更新前退出软件，双击 **update.cmd**。

默认数据位于 `data/sessions/日期/会话编号/`，可从窗口打开结果，也可打开 `data/index.html` 查看批次。你的数据、设置和模型不提交到 Git。

## 仓库目录

| 位置 | 用途 |
| --- | --- |
| `open-app.cmd` | 日常打开软件的唯一推荐入口 |
| `update.cmd` | 更新代码 |
| `collector/` | 窗口、采集、分析与数据管理代码 |
| `config.json` | 采集间隔及应用排除配置 |
| `requirements.txt` | 桌面端依赖 |
| `tools/` | 安装、历史迁移、批量分析等辅助脚本 |
| `server/` | 可选的 PostgreSQL 与私有存储服务端 |
| `tests/` | 自动测试 |
| `docs/` | 使用、部署说明；规划文档在 planning 子目录 |

使用说明：[桌面窗口](docs/DESKTOP.md) · [数据存储与部署](docs/ONLINE_STORAGE.md) · [维护规则](docs/MAINTENANCE.md)

目前输出是屏幕观察与初步分组的经历草稿。跨截图关联真实操作、完整事件理解和可靠的选择预测训练数据仍未完成；不要把候选样本当成用户已确认的行为。
