@echo off
REM 启动本地 LLM（llama.cpp 便携版，OpenAI 兼容 API，端口 11434）
REM 运行前自动从上游获取缺失的运行时和模型（见 llm\get_llm.py）
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
if not exist "%PY%" (
    echo [X] 未找到 Python 环境，请先运行 setup.bat
    exit /b 1
)

REM 1. 从上游确保运行时与模型就绪（缺失才下载，断点续传）
"%PY%" "%~dp0get_llm.py"
if errorlevel 1 (
    echo [X] 模型获取失败，请检查网络后重试，或改用云端 API
    exit /b 1
)

REM 2. 若未运行则启动 llama-server
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server.exe" >nul
if %errorlevel%==1 (
    echo 启动本地模型服务 ...
    REM 参数说明（相对默认的性能优化）：
    REM   --n-gpu-layers 99  : 模型全量 offload 到 GPU
    REM   -c 8192            : 上下文 8K
    REM   -b 2048            : prefill batch size（prefill 吞吐 ~3x）
    REM   --flash-attn       : Flash Attention（降低显存带宽需求，GPU 推理更快）
    REM   -ctk q8_0 -ctv q8_0: KV cache Q8 量化（显存减半，推理速度不降反升）
    REM   --mlock            : 锁定模型在物理内存，避免 swap 抖动
    start "llama-server" /min "bin\llama-server.exe" -m "models\Qwen3-4B-Q4_K_M.gguf" --port 11434 -c 8192 --n-gpu-layers 99 -b 2048 --flash-attn -ctk q8_0 -ctv q8_0 --mlock --alias Qwen3-4B-Q4_K_M
    timeout /t 3 /nobreak >nul
) else (
    echo 本地模型服务已在运行
)
exit /b 0
