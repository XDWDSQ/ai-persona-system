# Android APK（AI 拟人系统手机端）

> ⚠️ **已停止开发（存档）**：手机端统一改用 **PWA 网页版**（浏览器"添加到主屏幕"，体验对齐原生）。本工程仅作历史存档，不再构建更新；`sync_pages.bat` 与 `data/tunnel_url_apk.txt` 已废弃。以下内容保留供参考。

把 AI 拟人系统打包成 Android 应用：**界面内置在 APK 里（秒开、离线可见），数据接口经 APK 内本地代理转发到远程服务器**（电脑上的服务经 ngrok 隧道 / 云电脑部署）。

## 架构

```
┌─────────────── Android APK ───────────────┐      ┌──────────────────────┐
│ WebView ──→ http://127.0.0.1:<随机端口>    │      │  远程服务器            │
│  (内置页面)        │  极简 HTTP 代理         │      │  (隧道/云电脑 8000)   │
│                   │  · /pages/* 读 APK assets│ ──→  │  · /api/* 转发        │
│                   │  · /api/* 转发+附加cookie│      │  · /uploads/* 转发    │
│                   │  · SSE/音频/上传流式透传 │      └──────────────────────┘
└───────────────────┴────────────────────────┘
```

- 登录：设置页填**服务器地址 + 访问口令**，应用原生层调 `/api/login` 拿 cookie 存进程内存，代理转发时自动附加；遇 401/跳登录页自动重登一次
- 前端与服务端**零改动**：页面里所有相对路径请求都指向本地代理，绕开 CORS / cookie 跨域问题
- `http://127.0.0.1` 是 secure context：剪贴板、TTS 自动朗读、EventSource 多端同步全部可用

## 目录

```
android/
├── app/src/main/assets/    ← 前端静态资源（从 xiaoni-ai-persona 拷贝，勿手改）
├── app/src/main/java/com/xiaoni/persona/
│   ├── MainActivity.kt     WebView 主界面（文件选择、下载、返回键）
│   ├── SettingsActivity.kt 设置页（地址+口令、测试连接）
│   ├── LocalServer.kt      本地代理服务（核心）
│   ├── LoginManager.kt     登录与 cookie 管理
│   └── AppConfig.kt        配置存储
├── make_icon.py            用角色头像生成启动图标
├── keystore/               release 签名（不入库）
└── keystore.properties     release 签名配置（不入库）
```

## 打包前端更新

改了 `xiaoni-ai-persona/` 前端后，运行同步脚本（xcopy pages → assets，并检测同步盘冲突副本）：

```bat
sync_pages.bat
```

> 前端已精简为 chat.html 单页 + pet.js + pet 素材（原 index/roles/status/architecture 页与 colors_and_type.css 已删除）。

## 构建 APK

前置：JDK 17、Android SDK（platform 35 + build-tools 35.0.0）、Gradle 8.9。

```bat
set JAVA_HOME=你的JDK17路径
set ANDROID_HOME=你的SDK路径
cd android
gradle assembleDebug        :: 调试版（未签名）
gradle assembleRelease      :: 正式版（需 android/keystore.properties 存在）
```

release 签名（首次）：

```bat
keytool -genkeypair -v -keystore keystore\persona-release.keystore -alias persona ^
  -keyalg RSA -keysize 2048 -validity 10000
:: 然后写 android/keystore.properties：
::   storeFile=keystore/persona-release.keystore
::   storePassword=口令  keyAlias=persona  keyPassword=口令
```

产物：`android/app/build/outputs/apk/debug/app-debug.apk`、`.../release/app-release.apk`

## 手机安装

1. 手机允许「安装未知来源应用」
2. 安装 `app-release.apk`（正式签名版）
3. 打开应用 → 填服务器地址（电脑运行 `start.bat` 启动服务、`ngrok http 8000` 开隧道后，`data/tunnel_url.txt` 里的地址；或云电脑地址）和访问口令 → 保存并进入

## 使用说明

- 应用**只保留对话框**：打开直接进入聊天页，无主界面；聊天页内导航到其他页面（首页/角色/状态等）会自动拦回聊天页
- **改服务器地址/口令**：长按桌面应用图标 →「服务器设置」；首次未配置时启动会显示提示，点击提示文字进入设置

## 注意事项

- 服务器重启后隧道地址会变，按上面方式重新设置地址即可（长按桌面图标 → 服务器设置）
- 访问口令在服务端 `config.json` 的 `access_token`（改口令后需在应用里同步更新）
- 聊天记录同步逻辑不变：多端共用服务端会话，localStorage 只在单机缓存
- 代理服务只监听 127.0.0.1，不对外暴露端口
