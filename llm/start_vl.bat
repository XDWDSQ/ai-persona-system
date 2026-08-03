@echo off
REM Start local VLM (Qwen2.5-VL, OpenAI-compatible API on port 11435)
cd /d "%~dp0"

cd bin
start "llama-server-vl" /MIN llama-server.exe -m "..\models\Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf" --mmproj "..\models\mmproj-F16.gguf" --host 127.0.0.1 --port 11435 --n-gpu-layers 99 -c 8192 -ctk q8_0 -ctv q8_0 --image-min-tokens 1024
