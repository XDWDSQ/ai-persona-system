package com.xiaoni.persona

import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.View
import android.webkit.DownloadListener
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast

/**
 * 主界面：WebView 加载本地代理服务的页面（127.0.0.1），
 * API 请求由代理转发到远程服务器，并自动完成登录。
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar
    private lateinit var tvHint: TextView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var loaded = false
    private lateinit var audioPlayer: AudioPlayer

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppConfig.init(this)
        LoginManager.updateConfig(AppConfig.serverUrl, AppConfig.token)

        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webview)
        progress = findViewById(R.id.progress)
        tvHint = findViewById(R.id.tv_hint)
        // 未配置时点击提示进入设置页（内置默认服务器地址，只需填访问口令）
        tvHint.setOnClickListener {
            startActivityForResult(Intent(this, SettingsActivity::class.java), REQ_SETTINGS)
        }

        if (!AppConfig.isConfigured()) {
            tvHint.visibility = View.VISIBLE
        } else {
            initWebView()
        }
    }

    private fun initWebView() {
        if (loaded) return
        loaded = true
        tvHint.visibility = View.GONE

        LocalServer.instance.start(applicationContext)

        val ws = webView.settings
        ws.javaScriptEnabled = true
        ws.domStorageEnabled = true
        ws.databaseEnabled = true
        ws.mediaPlaybackRequiresUserGesture = false // 允许自动朗读 TTS
        ws.allowFileAccess = false
        ws.allowContentAccess = true
        ws.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        ws.useWideViewPort = true
        ws.loadWithOverviewMode = true

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                if (url == null) return false
                val uri = Uri.parse(url)
                if (uri.host == "127.0.0.1" || uri.host == "localhost") {
                    // 应用只保留对话框：非聊天页导航（首页/角色/状态/架构）一律拦回聊天页
                    if (!uri.path.orEmpty().contains("/pages/chat")) {
                        webView.loadUrl(CHAT_URL)
                    }
                    return true
                }
                // 外部链接交给系统浏览器
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, uri))
                } catch (_: Exception) {
                }
                return true
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progress.progress = newProgress
                progress.visibility = if (newProgress >= 100) View.GONE else View.VISIBLE
            }

            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = filePathCallback
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择附件"), REQ_FILE)
                } catch (_: Exception) {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = null
                    return false
                }
                return true
            }
        }

        // 附件/音频保存走系统下载管理器（本地代理 URL 自动附带登录 cookie）
        webView.setDownloadListener(DownloadListener { url, _, contentDisposition, mimetype, _ ->
            try {
                val dm = getSystemService(DOWNLOAD_SERVICE) as DownloadManager
                val req = DownloadManager.Request(Uri.parse(url))
                    .setMimeType(mimetype ?: "application/octet-stream")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(
                        Environment.DIRECTORY_DOWNLOADS,
                        android.webkit.URLUtil.guessFileName(url, contentDisposition, mimetype)
                    )
                dm.enqueue(req)
                Toast.makeText(this, "已开始下载", Toast.LENGTH_SHORT).show()
            } catch (_: Exception) {
                Toast.makeText(this, "下载失败", Toast.LENGTH_SHORT).show()
            }
        })

        // JS Bridge：聊天页设置面板 → 打开原生服务器设置页 / 读取当前地址
        audioPlayer = AudioPlayer(this)
        webView.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun openSettings() {
                runOnUiThread {
                    startActivityForResult(Intent(this@MainActivity, SettingsActivity::class.java), REQ_SETTINGS)
                }
            }

            @android.webkit.JavascriptInterface
            fun getServerUrl(): String = AppConfig.serverUrl ?: ""

            // ---- 音频播放（原生 MediaPlayer + MediaSession，支持系统媒体控件/流体云）----
            @android.webkit.JavascriptInterface
            fun playAudio(url: String, loop: Boolean): Boolean {
                audioPlayer.setCallbacks("window.__audioEnded&&window.__audioEnded()", "window.__audioError&&window.__audioError()")
                return audioPlayer.play(url, loop)
            }

            @android.webkit.JavascriptInterface
            fun playAudioBase64(b64: String, loop: Boolean): Boolean {
                audioPlayer.setCallbacks("window.__audioEnded&&window.__audioEnded()", "window.__audioError&&window.__audioError()")
                return audioPlayer.playBase64(b64, loop)
            }

            @android.webkit.JavascriptInterface
            fun setLoop(loop: Boolean) = audioPlayer.setLoop(loop)

            @android.webkit.JavascriptInterface
            fun pauseAudio() = audioPlayer.pause()

            @android.webkit.JavascriptInterface
            fun resumeAudio() = audioPlayer.resume()

            @android.webkit.JavascriptInterface
            fun stopAudio() = audioPlayer.stop()

            @android.webkit.JavascriptInterface
            fun isAudioPlaying(): Boolean = audioPlayer.isPlaying()

            @android.webkit.JavascriptInterface
            fun isAudioPaused(): Boolean = audioPlayer.isPaused()

            @android.webkit.JavascriptInterface
            fun currentAudioUrl(): String = audioPlayer.currentUrl()
        }, "AndroidBridge")

        // 后台预热登录（代理也会在 401 时自动重登）
        Thread { LoginManager.reLogin() }.start()

        webView.loadUrl(CHAT_URL)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQ_SETTINGS -> {
                if (!AppConfig.isConfigured()) {
                    finish()
                } else {
                    LoginManager.updateConfig(AppConfig.serverUrl, AppConfig.token)
                    // 延迟 reload：等窗口焦点切换完成（慢速设备上立即 reload 可能触发输入分发超时）
                    webView.postDelayed({
                        if (loaded) webView.reload() else initWebView()
                    }, 400)
                }
            }
            REQ_FILE -> {
                val cb = fileCallback
                fileCallback = null
                if (resultCode != RESULT_OK || data == null) {
                    cb?.onReceiveValue(null)
                    return
                }
                val uris = if (data.clipData != null) {
                    Array(data.clipData!!.itemCount) { i -> data.clipData!!.getItemAt(i).uri }
                } else {
                    arrayOf(data.data!!)
                }
                cb?.onReceiveValue(uris)
            }
        }
    }

    /** 音频播放结束/出错时回调前端 JS（必须在 UI 线程调用） */
    fun evaluateJs(js: String) {
        if (loaded) {
            try { webView.evaluateJavascript(js, null) } catch (_: Exception) {}
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        if (::audioPlayer.isInitialized) audioPlayer.stop()
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        private const val REQ_SETTINGS = 100
        private const val REQ_FILE = 101
        /** 应用只保留对话框：启动直达聊天页 */
        private val CHAT_URL get() = "http://127.0.0.1:${LocalServer.instance.port}/pages/chat.html"
    }
}
