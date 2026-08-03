@echo off
REM Start local LLM (llama.cpp, GPU CUDA, OpenAI-compatible API on port 11434)
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

REM Ensure runtime + model (fast no-op if already present)
if exist "%PY%" "%PY%" get_llm.py

REM Launch llama-server in background (minimized window)
REM --n-gpu-layers 99 = offload all layers to GPU (RTX 4060)
REM -c 8192 = context window (Qwen3 thinking model needs headroom)
cd bin
start "llama-server" /MIN llama-server.exe -m "..\models\Qwen3-4B-Q4_K_M.gguf" --host 127.0.0.1 --port 11434 --n-gpu-layers 99 -c 8192
