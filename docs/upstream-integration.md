# MoneyPrinterTurbo 对接说明

## 已核对的上游能力

当前工作区已经存在同级 `MoneyPrinterTurbo/` Git checkout。源码核对结果显示，它包含脚本和关键词生成服务（`app/services/llm.py`）、视频任务 API（`app/controllers/v1/video.py`）、素材服务（`app/services/material.py`）、配音/字幕/视频服务（`app/services/voice.py`、`subtitle.py`、`video.py`），以及 FastAPI 启动入口 `app/asgi.py`。

## 当前边界

两个项目都使用顶层 Python 包名 `app`，因此没有直接把上游目录加入当前进程的 `sys.path`。当前项目通过子进程隔离调用上游的 `app.services.llm.generate_script` 和 `generate_terms`，避免导入到错误的 `app` 包。上游依赖没有安装或 `MoneyPrinterTurbo/config.toml` 未配置时，调用会失败并回退到离线编排器。

## 当前适配器

配置本项目 `.env`：

```dotenv
DEFAULT_PROVIDER=moneyprinterturbo
MPT_ROOT=..\MoneyPrinterTurbo
MPT_PYTHON=
MPT_TIMEOUT_SECONDS=180
```

然后在上游项目中按其 README 安装依赖，并准备 `MoneyPrinterTurbo/config.toml`。例如使用上游项目推荐的 `uv` 工作流：

```powershell
Set-Location ..\MoneyPrinterTurbo
uv sync --frozen
```

适配器会自动优先使用 `MoneyPrinterTurbo/.venv/Scripts/python.exe`（Windows）或
`.venv/bin/python`（Linux/macOS）；如果依赖安装在其它环境，则填写
`MPT_PYTHON` 的绝对路径。本项目创建任务时会把城市、地标、主题、文化、节庆、
受众和风格拼为上游主题，调用上游脚本/关键词服务，再把纯文本脚本切分为现有
`Shot` 合约。`TraceRecord` 会记录 `provider=moneyprinterturbo`、桥接版本和上游
根目录。

适配器文件为 `backend/app/providers/moneyprinterturbo.py`。其中 `build_storyboard()` 是无外部依赖的映射函数，可独立测试；真正的上游调用通过隔离子进程完成。

## 兼容策略

本项目把可替换的能力放在 provider 边界：

| 能力 | 当前实现 | 上游/生产替换点 |
| --- | --- | --- |
| 文案/脚本 | `OfflineLLMProvider`；可选 `MoneyPrinterTurboLLMProvider` | MoneyPrinterTurbo 的 LLM 或 OpenAI 兼容网关 |
| 素材 | `OfflineAssetProvider`、`LocalUploadProvider` | Pexels、Pixabay、Coverr、校内授权素材库、上游素材模块 |
| 配音 | 离线合成音轨 | Edge TTS、Azure、火山、阿里云或上游 TTS 模块 |
| 音乐 | FFmpeg 合成音轨 | 本地版权音乐库或获授权音乐 provider |
| 视频 | 本项目 `VideoRenderer` + FFmpeg | 可复用上游 FFmpeg 模板或替换为队列 worker |

当前只接入了上游脚本和搜索关键词阶段，视频渲染仍由本项目 FFmpeg renderer 完成。这样可以保留现有素材版权字段、任务重试和验收产物。若上游依赖未安装、
`config.toml` 未配置或模型请求失败，任务会记录上游错误并回退到离线浙江文旅编排器。
下一步若要接入上游完整视频链路，应先把上游 `VideoParams` 映射为独立 DTO，再复用
其素材/配音/字幕服务，不应直接共享全局配置或任务目录。
