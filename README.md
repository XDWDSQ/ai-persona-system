# AI 拟人系统

本地优先的 AI 拟人对话系统：FastAPI 后端 + 前端交互页面，集成 LLM 对话、TTS 语音合成（音色克隆）、ASR 语音识别。

## 功能

- 多角色人设对话（本地 LLM 或云端 API）
- 语音合成与音色克隆（本地 TTS / 云端 TTS）
- 语音识别（本地 ASR）
- 人设、语音参数可视化配置

## 目录结构

```
server.py              FastAPI 后端主程序（端口 8000）
adapters/asr/          ASR 适配服务源码
llm/                   llama.cpp 本地 LLM 启动脚本（模型运行时不入库）
xiaoni-ai-persona/     前端页面源码
docs/                  GitHub Pages 静态托管副本（页面互相引用已改为相对路径）
config.example.json    配置模板（复制为 config.json 使用，勿提交真实配置）
```

## 启动

```bat
setup.bat          # 首次初始化依赖
start.bat          # 一键启动（含本地 LLM）
restart_service.bat  # 重启 8000 端口服务
```

启动后访问 http://127.0.0.1:8000

## 配置

复制 `config.example.json` 为 `config.json`，填写 API 密钥（也可写入根目录 `.env`）。

## 在线演示

本仓库通过 GitHub Pages 托管前端页面（仅静态展示，后端 API 需本地运行）：

- https://xdwdsq.github.io/ai-persona-system/
