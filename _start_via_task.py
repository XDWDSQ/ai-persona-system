# -*- coding: utf-8 -*-
"""通过任务计划程序分离式拉起 8000 服务（独立于当前会话/job，不会被回收）。
用 Python subprocess 传参（CreateProcessW / UTF-16），中文路径不丢。"""
import subprocess, sys, time, socket
from pathlib import Path

# 项目根 = 本脚本所在目录（旧版硬编码 D:\\Users\\31557\\Desktop 旧工作区，已失效）
PROJ = str(Path(__file__).resolve().parent)
LAUNCHER = str(Path(PROJ) / "_run_service_detached.bat")
TASK = "AIRestart8000"


def run(args):
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="mbcs", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# 先清掉可能残留的同名任务
run(["schtasks", "/Delete", "/TN", TASK, "/F"])

# 创建：ONCE + 当前用户（非 SYSTEM，免管理员）。触发靠 /Run，计划时间仅为通过校验。
code, out = run(["schtasks", "/Create", "/TN", TASK, "/TR", LAUNCHER,
                 "/SC", "ONCE", "/ST", "23:59", "/F"])
print("[create]", code)
print(out[:300])
if code != 0:
    sys.exit("TASK CREATE FAILED")

code, out = run(["schtasks", "/Run", "/TN", TASK])
print("[run]", code)
print(out[:200])
if code != 0:
    sys.exit("TASK RUN FAILED")


def listening(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


# 等待端口起来（uvicorn 冷启动 + TTS 预热可能要一会儿，最多等 90s）
for i in range(90):
    if listening(8000):
        print(f"PORT 8000 LISTENING after ~{i+1}s")
        break
    time.sleep(1)
else:
    print("PORT 8000 NOT listening after 90s; check data\\service_err.log")
    sys.exit(2)

print("OK")
