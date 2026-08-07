package com.xiaoni.persona

import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/**
 * 远程服务登录与会话 cookie 管理。
 *
 * 本地代理转发请求前会附加这里保存的 cookie（服务端用 httponly cookie `ai_token` 鉴权）。
 * cookie 只在进程内存中，随应用重启重新登录获取。
 */
object LoginManager {
    @Volatile var serverUrl: String? = null
    @Volatile var token: String = ""

    private val cookies = HashMap<String, String>()
    /** 保护 cookies 读写（短暂持锁，毫秒级） */
    private val cookieLock = Any()
    /** 保护登录流程（网络 I/O 期间持锁，最多数十秒；与 cookieLock 分离避免阻塞主线程） */
    private val loginLock = Any()

    /** 配置变化时调用：清空旧 cookie，下次请求自动重新登录 */
    fun updateConfig(url: String?, tok: String) {
        serverUrl = url
        token = tok
        synchronized(cookieLock) {
            cookies.clear()
        }
    }

    fun attachCookie(conn: HttpURLConnection) {
        synchronized(cookieLock) {
            if (cookies.isNotEmpty()) {
                conn.setRequestProperty(
                    "Cookie",
                    cookies.entries.joinToString("; ") { "${it.key}=${it.value}" }
                )
            }
        }
    }

    fun storeSetCookie(setCookie: String) {
        val pair = setCookie.substringBefore(';')
        val eq = pair.indexOf('=')
        if (eq > 0) {
            synchronized(cookieLock) {
                cookies[pair.substring(0, eq).trim()] = pair.substring(eq + 1).trim()
            }
        }
    }

    fun isLoggedIn(): Boolean = synchronized(cookieLock) { cookies.isNotEmpty() }

    /** 预热登录（应用启动时后台执行）：单次尝试、快速失败；失败由代理路径的 reLogin 兜底 */
    fun reLoginOnce(): Boolean = synchronized(loginLock) { doLogin() }

    /** 重新登录（代理在 401/跳登录页时调用）。成功返回 true。登录期间不阻塞 cookie 读写。
     *  内部重试：ngrok 等慢速通道首次连接可能失败/超时，重试两次再判定失败。 */
    fun reLogin(): Boolean = synchronized(loginLock) {
        var ok = false
        for (i in 0..2) {
            if (doLogin()) {
                ok = true
                break
            }
            if (i < 2) {
                try { Thread.sleep(1500L * (i + 1)) } catch (_: InterruptedException) {}
            }
        }
        ok
    }

    /** 只测试连接是否可用（不写 cookie、不影响当前会话），用于设置页「测试连接」。 */
    fun testLogin(url: String, tok: String): String? {
        val err = doLoginRaw(url, tok)
        return err
    }

    private fun doLogin(): Boolean {
        val url = serverUrl ?: return false
        return doLoginRaw(url, token) == null
    }

    /** 尝试登录，成功返回 null，失败返回中文错误信息 */
    private fun doLoginRaw(url: String, tok: String): String? {
        if (url.isBlank() || tok.isEmpty()) return "未配置服务器地址或访问口令"
        var conn: HttpURLConnection? = null
        return try {
            conn = URL("$url/api/login").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 30000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = "{\"token\":\"${tok.replace("\"", "\\\"")}\"}"
            conn.outputStream.write(body.toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            if (code == 200) {
                var i = 0
                while (true) {
                    val k = conn.getHeaderFieldKey(i) ?: break
                    if (k.equals("Set-Cookie", true)) storeSetCookie(conn.getHeaderField(i))
                    i++
                }
                null
            } else {
                when (code) {
                    401 -> "访问口令错误（401）"
                    404 -> "地址不存在（404），检查服务器地址是否完整"
                    else -> "服务器返回 $code"
                }
            }
        } catch (e: Exception) {
            "无法连接服务器：${e.message ?: e.javaClass.simpleName}"
        } finally {
            conn?.disconnect()
        }
    }
}
