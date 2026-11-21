# 浙江文旅 AIGC 视频系统  音频优化版

这是用于 Windows 10/11 64 位电脑的可转交版本。项目代码、知识库和 FFmpeg 已放在本文件夹，默认配置可离线生成脚本与技术测试画面；联网的 Edge 中文配音在首次启动时自动安装。真实 AI 脚本、图片或视频仍需接收方自行配置相应服务的 API 凭据。

## 接收方快速启动

1. 安装 **Python 3.11 或 3.12**，安装时勾选 **Add python.exe to PATH**。Windows 自带 PowerShell；FFmpeg 和 FFprobe 已随包提供，无需另装。
2. 保持整个文件夹的目录结构，双击 `START.cmd`。首次启动会创建 `.venv-audio`，联网安装 `edge-tts==7.3.1`，运行简短自检，然后启动服务；后续启动直接运行。
3. 浏览器打开 <http://127.0.0.1:8790>。在默认离线模式中，选择“无音乐”即可不依赖额外音乐文件完成技术流程。

如果双击窗口立即关闭，可在 PowerShell 中进入本目录后运行 `powershell -ExecutionPolicy Bypass -File .\first_setup.ps1` 查看报错，再运行 `powershell -ExecutionPolicy Bypass -File .\start_here.ps1`。首次安装 Edge 配音需要联网；也可以在界面选择 Windows 系统语音或上传自己的配音。

## 真实创作与素材

- 本包的 `.env` 不含任何 API key，预设 `DEFAULT_PROVIDER=offline` 和 `ASSET_PROVIDER=offline`。该模式用于验收流程，离线场景图不等同于真实旅游照片。
- 真实脚本生成：将 `.env` 中的 `DEFAULT_PROVIDER` 改为 `deepseek`，填写自己的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。配置字段见 `.env.example`。
- 真实画面：上传自己有权使用的图片/视频，或配置图片、视频服务及密钥；相关字段见 `.env.example`。上传素材可直接使用焦点运镜与转场。
- 背景音乐：默认曲库为空。把有权使用的音乐放入 `runtime/music` 并完善 `runtime/music/catalog.json`，或在页面上传音乐；也可选“无音乐”。
- “天依 · 本地声库”是可选高级功能，未随轻量包复制约 4.6 GB 模型及不可跨机复用的虚拟环境。需要时用 Python 3.11 运行 `scripts/setup-tianyi.ps1`；依赖和模型需另行下载，详见 `docs/voice-studio.md`。

## 文件结构

- `backend`、`frontend`、`data_zhejiang_kb`：服务、页面和浙江知识库。
- `tools/ffmpeg`：便携版 FFmpeg 8.1.2，附上游 `LICENSE` 和 `README.txt`；该构建使用 GPL v3。
- `scripts`：服务启动及可选配音安装脚本。
- `tests`：随包自检测试。
- `runtime`：接收方首次运行后写入的新任务与缓存；本包不含原电脑的任务数据库、历史成片、日志和音频缓存。
- `11月13日优化说明.docx`：本次版本的功能、打包范围和验证记录。

## 故障定位

- 找不到 Python：重新安装并启用 PATH，或从 PowerShell 执行 `python --version` 检查。
- 首次配音安装失败：检查网络后运行 `FIRST_SETUP.cmd`；Edge TTS 是联网服务。无需联网时可选择本机已安装的 Windows 中文语音或“无配音”。
- 找不到音乐：在页面选择“无音乐”，或上传音乐并确认曲库条目与所选情绪和风格相符。
- 云端生成失败：检查 `.env` 中的服务地址、密钥、模型和账号额度；离线模式无需密钥。
