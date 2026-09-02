package com.xiaoni.persona

import android.content.Context
import android.content.res.AssetManager
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URL
import java.net.URLDecoder
import java.util.concurrent.Executors

/**
 * 极简 HTTP 代理服务，监听 127.0.0.1:<随机端口>。
 *
 * 职责：
 * - 静态资源（/pages/ 目录、css、favicon 等）直接从 APK assets 提供 —— 界面秒开、离线可见
 * - /api/、/uploads/ 及未知路径：原样转发到远程服务器，自动附加登录 cookie；
 *   遇到 401/302 跳登录页时自动重新登录并重试一次；SSE 流、音频流、multipart 上传均流式透传
 * - /login：返回友好提示页（正常情况下登录由应用原生层完成，用户看不到网页登录页）
 *
 * 所有响应统一 `Connection: close`（流式体用连接关闭定界），无第三方依赖。
 */
class LocalServer private constructor() {

    companion object {
        val instance = LocalServer()
        /** 请求体缓冲上限：超过则流式透传（不重试） */
        private const val MAX_BUFFER_BODY = 8L * 1024 * 1024
    }

    @Volatile private var serverSocket: ServerSocket? = null
    @Volatile var port: Int = 0
        private set
    private lateinit var assets: AssetManager
    private val executor = Executors.newCachedThreadPool()

    private val MIME = mapOf(
        "html" to "text/html; charset=utf-8",
        "css" to "text/css; charset=utf-8",
        "js" to "application/javascript; charset=utf-8",
        "mjs" to "application/javascript; charset=utf-8",
        "json" to "application/json; charset=utf-8",
        "svg" to "image/svg+xml",
        "png" to "image/png",
        "jpg" to "image/jpeg",
        "jpeg" to "image/jpeg",
        "webp" to "image/webp",
        "gif" to "image/gif",
        "ico" to "image/x-icon",
        "wav" to "audio/wav",
        "mp3" to "audio/mpeg",
        "webm" to "audio/webm",
        "m4a" to "audio/mp4",
        "txt" to "text/plain; charset=utf-8",
        "woff2" to "font/woff2",
    )

    fun start(context: Context) {
        if (serverSocket != null) return
        assets = context.assets
        val ss = ServerSocket(0, 64, InetAddress.getByName("127.0.0.1"))
        port = ss.localPort
        serverSocket = ss
        executor.execute { acceptLoop(ss) }
    }

    private fun acceptLoop(ss: ServerSocket) {
        while (!ss.isClosed) {
            try {
                val sock = ss.accept()
                executor.execute { handle(sock) }
            } catch (_: IOException) {
                break
            }
        }
    }

    private fun handle(sock: Socket) {
        try {
            sock.tcpNoDelay = true
            val rawIn = BufferedInputStream(sock.getInputStream())
            val out = BufferedOutputStream(sock.getOutputStream())

            val requestLine = readLine(rawIn) ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) return
            val method = parts[0].uppercase()
            val target = parts[1]

            val headers = LinkedHashMap<String, String>()
            while (true) {
                val line = readLine(rawIn) ?: break
                if (line.isEmpty()) break
                val ci = line.indexOf(':')
                if (ci > 0) headers[line.substring(0, ci).trim().lowercase()] = line.substring(ci + 1).trim()
            }

            val contentLength = headers["content-length"]?.toLongOrNull() ?: -1L
            val qIdx = target.indexOf('?')
            val path = if (qIdx >= 0) target.substring(0, qIdx) else target
            val query = if (qIdx >= 0) target.substring(qIdx + 1) else ""

            when {
                path == "/" -> serveAsset(out, "pages/chat.html")
                path == "/login" -> serveLoginPage(out)
                path == "/favicon.ico" || path == "/favicon.svg" -> {
                    if (!serveAsset(out, "pages/favicon.svg")) writeError(out, 404)
                }
                path.startsWith("/api/") || path.startsWith("/uploads/") ->
                    proxy(out, method, path, query, headers, rawIn, contentLength)
                else -> {
                    // 先查本地 assets（页面/素材），未命中则尝试转发远程（如服务端新增路由）
                    if (!serveAsset(out, path.removePrefix("/"))) {
                        proxy(out, method, path, query, headers, rawIn, contentLength)
                    }
                }
            }
        } catch (_: IOException) {
            // 客户端断开等，直接关闭连接
        } catch (_: Exception) {
        } finally {
            try { sock.close() } catch (_: IOException) {}
        }
    }

    // ------------------------------------------------------------- 静态资源

    private fun serveAsset(out: OutputStream, assetPath: String): Boolean {
        val safe = sanitizeAssetPath(assetPath) ?: return false
        return try {
            assets.open(safe).use { input ->
                val bytes = input.readBytes()
                writeHead(out, 200, mapOf(
                    "Content-Type" to (MIME[extensionOf(safe)] ?: "application/octet-stream"),
                    "Content-Length" to bytes.size.toString(),
                    "Cache-Control" to (if (safe.endsWith(".html")) "no-cache" else "public, max-age=3600"),
                ))
                out.write(bytes)
                out.flush()
            }
            true
        } catch (_: IOException) {
            false
        }
    }

    /** 防目录穿越；允许 URL 编码（如空格 %20） */
    private fun sanitizeAssetPath(raw: String): String? {
        val decoded = try { URLDecoder.decode(raw, "UTF-8") } catch (_: Exception) { raw }
        if (decoded.contains("..") || decoded.startsWith("/")) return null
        val normalized = decoded.trimStart('/')
        if (normalized.isEmpty()) return null
        return normalized
    }

    private fun extensionOf(path: String): String {
        val idx = path.lastIndexOf('.')
        return if (idx >= 0) path.substring(idx + 1).lowercase() else ""
    }

    private fun serveLoginPage(out: OutputStream) {
        val html = """
            <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>登录失效</title>
            <style>
              body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
                   background:#0a0a0a;color:#f4f4f5;font-family:system-ui,sans-serif;text-align:center;padding:24px}
              .box{max-width:340px}
              h1{font-size:20px;margin:0 0 12px}
              p{font-size:14px;color:#a1a1aa;line-height:1.7;margin:0 0 20px}
              a{display:inline-block;background:#e879f9;color:#0a0a0a;text-decoration:none;
                padding:10px 22px;border-radius:10px;font-size:14px;font-weight:600}
            </style></head><body>
            <div class="box"><h1>登录已失效</h1>
            <p>连接服务器的口令已过期或服务器已重启更换口令。<br>请在应用内点击右下角「⚙」设置，重新填写服务器地址与访问口令。</p>
            <a href="/pages/chat.html">返回对话</a></div>
            </body></html>
        """.trimIndent()
        val bytes = html.toByteArray(Charsets.UTF_8)
        writeHead(out, 200, mapOf("Content-Type" to "text/html; charset=utf-8", "Content-Length" to bytes.size.toString()))
        out.write(bytes)
        out.flush()
    }

    // ------------------------------------------------------------- 远程转发

    /** 转发时跳过的请求头：hop-by-hop / 敏感头，以及浏览器 UA——
     *  ngrok 免费版对浏览器 UA 返回 ERR_NGROK_6024 警告页（不走代理的直连/下载不受影响）；
     *  改用 HttpURLConnection 默认 UA（Java/...）可绕过该拦截。 */
    private val SKIP_REQ_HEADERS = setOf(
        "host", "connection", "content-length", "transfer-encoding", "accept-encoding",
        "origin", "referer", "cookie", "proxy-connection", "upgrade", "keep-alive",
        // WebView 页面请求会带浏览器 UA（Mozilla/5.0...），ngrok 免费版对浏览器 UA 强制显示
        // 访问确认页（ERR_NGROK_6024）导致 API 返回 HTML 而非 JSON，页面误报"后端未连接"。
        // 转发时丢弃 UA，让 HttpURLConnection 使用默认 Dalvik UA（直通，不被拦截）。
        "user-agent",
    )
    /** 转发响应时跳过的头。
     *  注意：Content-Length 必须透传——MediaPlayer 播放 WAV 音频时依赖它拿到总时长，
     *  丢了会导致 audioDurationMs()=-1，前端"点击文字跳转音频位置"直接失效；
     *  SSE 等无长度响应里该头本来就不存在，透传无副作用。 */
    private val SKIP_RESP_HEADERS = setOf(
        "transfer-encoding", "connection", "keep-alive", "date", "set-cookie",
    )

    private fun proxy(
        out: OutputStream,
        method: String,
        path: String,
        query: String,
        reqHeaders: Map<String, String>,
        bodyIn: InputStream,
        bodyLen: Long,
    ) {
        val base = LoginManager.serverUrl
        if (base.isNullOrBlank()) {
            writeJsonError(out, 503, "未配置服务器地址，请在设置中填写")
            return
        }
        val urlStr = base + path + if (query.isNotEmpty()) "?$query" else ""
        // 转发目标仅允许 http/https（配置入口 normalizeUrl 已强制前缀；此处防御）
        // 注意：不拒绝 localhost/私有地址——本机、局域网、模拟器(10.0.2.2)访问是设计需求
        if (!urlStr.startsWith("http://") && !urlStr.startsWith("https://")) {
            writeJsonError(out, 400, "非法服务器地址")
            return
        }

        val hasBody = method == "POST" || method == "PUT" || method == "PATCH" || method == "DELETE"
        // 请求体预先缓冲（≤8MB），保证鉴权失败重试时 body 可重放；超大上传流式透传、失败不重试
        var bodyBuf: ByteArray? = null
        var canRetry = true
        if (hasBody) {
            if (bodyLen in 0..MAX_BUFFER_BODY) {
                bodyBuf = ByteArray(bodyLen.toInt())
                var off = 0
                while (off < bodyBuf.size) {
                    val n = bodyIn.read(bodyBuf, off, bodyBuf.size - off)
                    if (n < 0) break
                    off += n
                }
            } else {
                canRetry = false
            }
        }

        for (attempt in 0..1) {
            var conn: HttpURLConnection? = null
            try {
                conn = URL(urlStr).openConnection() as HttpURLConnection
                conn.requestMethod = method
                conn.instanceFollowRedirects = false
                conn.connectTimeout = 15000
                conn.readTimeout = 0 // 流式转发（SSE/长生成），不设读超时

                for ((k, v) in reqHeaders) {
                    if (k !in SKIP_REQ_HEADERS) conn.setRequestProperty(k, v)
                }
                LoginManager.attachCookie(conn)

                if (hasBody) {
                    conn.doOutput = true
                    if (bodyBuf != null) {
                        conn.setFixedLengthStreamingMode(bodyBuf.size)
                        conn.outputStream.write(bodyBuf)
                    } else {
                        conn.setChunkedStreamingMode(8192)
                        copyStream(bodyIn, conn.outputStream, -1)
                    }
                }

                val code = conn.responseCode
                val loc = conn.getHeaderField("Location") ?: ""
                val authFail = code == 401 || (code in 300..399 && loc.contains("/login"))

                if (authFail && attempt == 0 && canRetry && LoginManager.reLogin()) continue
                if (authFail) {
                    writeJsonError(out, 401, "登录失效，请在应用设置中更新访问口令")
                    return
                }

                // 透传重定向（相对 Location 由客户端解析回本地代理，继续走转发）
                if (code in 300..399) {
                    val respHeaders = LinkedHashMap<String, String>()
                    collectHeaders(conn, respHeaders)
                    respHeaders["Location"] = loc
                    writeHead(out, code, respHeaders)
                    drainSafely(if (code >= 400) conn.errorStream else conn.inputStream)
                    return
                }

                val respHeaders = LinkedHashMap<String, String>()
                collectHeaders(conn, respHeaders)
                writeHead(out, code, respHeaders)

                val respIn = if (code >= 400) conn.errorStream else conn.inputStream
                if (respIn != null) {
                    copyStream(respIn, out, -1)
                }
                out.flush()
                return
            } catch (e: IOException) {
                // 客户端断开等网络异常
                if (attempt == 0 && canRetry && LoginManager.reLogin()) continue
                if (e.message?.contains("Broken pipe") == true) return
                writeJsonError(out, 502, "连接服务器失败：${e.message ?: "网络异常"}")
                return
            } catch (e: Exception) {
                if (attempt == 0 && canRetry && LoginManager.reLogin()) continue
                writeJsonError(out, 502, "连接服务器失败：${e.message ?: e.javaClass.simpleName}")
                return
            } finally {
                conn?.disconnect()
            }
        }
    }

    private fun collectHeaders(conn: HttpURLConnection, target: MutableMap<String, String>) {
        var i = 0
        while (true) {
            val k = conn.getHeaderFieldKey(i) ?: break
            val v = conn.getHeaderField(i) ?: break
            if (k.equals("Set-Cookie", true)) {
                LoginManager.storeSetCookie(v)
            } else if (k.lowercase() !in SKIP_RESP_HEADERS) {
                target[k] = v
            }
            i++
        }
    }

    // ------------------------------------------------------------- 基础 IO

    /** 逐块复制流（-1 表示流式转发，每块立即 flush，SSE 依赖此行为） */
    private fun copyStream(input: InputStream, output: OutputStream, length: Long) {
        val buf = ByteArray(8192)
        var remaining = length
        while (true) {
            val want = if (remaining < 0) buf.size else minOf(remaining, buf.size.toLong()).toInt()
            if (want == 0) break
            val n = input.read(buf, 0, want)
            if (n < 0) break
            output.write(buf, 0, n)
            output.flush()
            if (remaining >= 0) {
                remaining -= n
                if (remaining == 0L) break
            }
        }
    }

    private fun drainSafely(input: InputStream?) {
        if (input == null) return
        try { input.close() } catch (_: IOException) {}
    }

    private fun readLine(input: InputStream): String? {
        val sb = StringBuilder(128)
        while (true) {
            val b = input.read()
            if (b < 0) return if (sb.isEmpty()) null else sb.toString()
            if (b == '\n'.code) {
                if (sb.isNotEmpty() && sb.last() == '\r') sb.setLength(sb.length - 1)
                return sb.toString()
            }
            sb.append(b.toChar())
            if (sb.length > 8192) return null
        }
    }

    private fun writeHead(out: OutputStream, status: Int, headers: Map<String, String>) {
        val reason = when (status) {
            200 -> "OK"; 204 -> "No Content"; 301 -> "Moved Permanently"
            302 -> "Found"; 307 -> "Temporary Redirect"; 308 -> "Permanent Redirect"
            400 -> "Bad Request"; 401 -> "Unauthorized"; 403 -> "Forbidden"
            404 -> "Not Found"; 405 -> "Method Not Allowed"; 408 -> "Request Timeout"
            429 -> "Too Many Requests"; 500 -> "Internal Server Error"
            502 -> "Bad Gateway"; 503 -> "Service Unavailable"; 504 -> "Gateway Timeout"
            else -> "Status"
        }
        val sb = StringBuilder(256)
        sb.append("HTTP/1.1 $status $reason\r\n")
        for ((k, v) in headers) sb.append("$k: $v\r\n")
        sb.append("Connection: close\r\n\r\n")
        out.write(sb.toString().toByteArray(Charsets.UTF_8))
        out.flush()
    }

    private fun writeJsonError(out: OutputStream, status: Int, message: String) {
        val body = """{"detail":"$message"}""".toByteArray(Charsets.UTF_8)
        writeHead(out, status, mapOf(
            "Content-Type" to "application/json; charset=utf-8",
            "Content-Length" to body.size.toString(),
        ))
        out.write(body)
        out.flush()
    }

    private fun writeError(out: OutputStream, status: Int) {
        writeHead(out, status, mapOf("Content-Type" to "text/plain; charset=utf-8", "Content-Length" to "0"))
        out.flush()
    }
}
