# -*- coding: utf-8 -*-
"""本地视觉模型（VLM）使用说明。

Qwen2.5-VL-3B-Instruct（GGUF 量化版）+ mmproj 视觉投影，基于 llama.cpp 运行。

## 文件
- llm/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf  （主模型，约1.8GB）
- llm/models/mmproj-F16.gguf                      （视觉投影，约1.25GB）
- 运行时：llm/bin/llama-server.exe（llama.cpp CUDA 版）

## 下载源（已下载，如需重下）
- https://hf-mirror.com/unsloth/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf
- https://hf-mirror.com/unsloth/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/mmproj-F16.gguf

## 启动服务（OpenAI 兼容 API，端口 11435）
llm\bin\llama-server.exe -m llm\models\Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf \
    --mmproj llm\models\mmproj-F16.gguf --port 11435 -c 8192 -ngl 99 \
    -ctk q8_0 -ctv q8_0 --image-min-tokens 1024

也可以直接双击 `llm\start_vl.bat` 一键启动。

## 接入聊天页面

聊天页发送栏新增了「发送文件」按钮（图片/文档/文件）。图片走视觉链路，默认 `auto`
模式：**优先**用当前云端 provider 的多模态能力（如 MiMo mimo-v2.5 原生识图），
云端不可用时再回退到本地 `http://127.0.0.1:11435/v1` 的视觉模型，让角色真正看到图；
两者都不可用时退回普通文本模型，仅附带文件名提示。可在 `config.json` 的 `vision` 节点
配置：`enabled`（true/false）、`provider`（`auto`/`cloud`/`local`/`off`）、`base_url`、
`model`（本地默认 `Qwen2.5-VL-3B`）。本地 VL 服务未启动时自动走云端或文本兜底。

## 关键注意事项（实测踩坑）
1. 显存仅 8GB：必须加 -ctk q8_0 -ctv q8_0（KV 量化），否则显存不足导致图像编码
   静默失败，模型持续输出问号乱码。
2. 上下文 -c 8192：大图（>1500 像素边）图像 token 会超过 4096，上下文太小会 400 报错。
3. 大图建议先缩放到最长边 1600 以内再识别，速度和稳定性更好。
4. 中文对话 API：POST http://127.0.0.1:11435/v1/chat/completions，
   图片用 data:image/jpeg;base64,<b64> 格式。

## 调用示例（curl）
curl -X POST http://127.0.0.1:11435/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"Qwen2.5-VL-3B\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/jpeg;base64,...\"}},{\"type\":\"text\",\"text\":\"描述这张图\"}]}]}"
"""
