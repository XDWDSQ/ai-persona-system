# -*- coding: utf-8 -*-
"""服务端拆分包：从 server.py 下沉的无状态纯函数。

拆分原则（2026-09 后端优化）：
- 只收「纯函数 + 模块级正则/常量」：不读 config/sessions/data 目录、不碰
  FastAPI app、不依赖 server.py 任何全局变量，可独立单测。
- 有状态逻辑（config 缓存、会话读写、TTS 合成、LLM 调用、路由）保留在
  server.py，避免跨模块共享可变状态。
- server.py 用 `from server_pkg.xxx import ...` 重导出，`import server`
  的旧引用（含全部离线测试）零改动可用。
- 例外：角色现实动态区块留在 server.py —— test_role_news_v2.py 按源码
  区间截取该区块，搬走会破坏其 `src.index` 断言。
"""
