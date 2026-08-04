@echo off
REM Start local LLM (llama.cpp, GPU CUDA, OpenAI-compatible API on port 11434)
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

REM Ensure runtime + model (fast no-op if already present)
if exist "%PY%" "%PY%" get_llm.py

REM Launch llama-server in background (minimized window)
REM 模型：Qwen3.5-4B（Q4_K_M 权重 2.7GB，8GB 显存宽裕）
REM 显存优化（2026-08-04 v2）：
REM   --flash-attn          FlashAttention：加速 + 大幅降低 KV cache 显存
REM   --cache-type-k/v q8_0 KV cache 压到 8bit（质量损失极小，显存再省一半）
REM   --n-gpu-layers 99     全部层 offload 到 GPU
REM   -c 8192               人设 4000+ 字 + 历史记录，装得下
REM 采样平衡（人设特调 v7，针对 Qwen3.5-4B 复读/漂移优化）：
REM   --temp 0.85           暧昧温度（0.9 偶发漂移，0.85 稳中带味）
REM   --top-k 40            收紧候选词，防低质量 token 抢位（60 → 40）
REM   --top-p 0.92          核采样下限收紧（0.95 → 0.92，防回复飘离人设）
REM   --min-p 0.05          保留低概率词过滤
REM   --repeat-penalty 1.25 重复惩罚（1.2 → 1.25，4B 更易复读，加重抑制）
REM   --repeat-last-n 768   重复检测窗口拉长（512 → 768，覆盖整句+前句）
REM 模型缺失保护：模型文件不存在时跳过 llama-server（云端 API 模式不受影响）
if not exist "..\models\Qwen3.5-4B-Q4_K_M.gguf" (
    echo [提示] 本地对话模型未安装，跳过 llama-server 启动（可正常使用云端 API）
    exit /b 0
)
cd bin
start "llama-server" /MIN llama-server.exe -m "..\models\Qwen3.5-4B-Q4_K_M.gguf" --host 127.0.0.1 --port 11434 --n-gpu-layers 99 -c 8192 --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --temp 0.85 --top-k 40 --top-p 0.92 --min-p 0.05 --repeat-penalty 1.25 --repeat-last-n 768
