package com.xiaoni.persona

import android.content.Context
import android.content.SharedPreferences

/**
 * 应用配置：远程服务器地址 + 访问口令，存 SharedPreferences。
 * 口令只在本机保存，用于登录远程服务后获取 cookie（cookie 同样只在本进程内存中）。
 */
object AppConfig {
    private const val PREFS = "persona_prefs"
    private const val K_URL = "server_url"
    private const val K_TOKEN = "access_token"

    /** 内置默认服务器地址（ngrok 固定域名，重启不变）；用户可在设置页改为其他地址 */
    const val DEFAULT_SERVER_URL = "https://filling-smirk-sternness.ngrok-free.dev"

    @Volatile private var prefs: SharedPreferences? = null

    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }
    }

    /** 当前生效地址：用户已配置的优先，未配置时用内置默认地址 */
    val serverUrl: String?
        get() = prefs?.getString(K_URL, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_SERVER_URL

    /** 是否已由用户显式配置过（决定首启是否进设置引导） */
    fun isConfigured(): Boolean = !prefs?.getString(K_URL, null).isNullOrBlank()

    val token: String
        get() = prefs?.getString(K_TOKEN, "") ?: ""

    fun save(url: String, token: String) {
        prefs?.edit()?.putString(K_URL, url.trimEnd('/'))?.putString(K_TOKEN, token.trim())?.apply()
    }

    /** 地址规范化：补 https:// 前缀、去尾部斜杠 */
    fun normalizeUrl(raw: String): String? {
        var u = raw.trim()
        if (u.isEmpty()) return null
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://$u"
        while (u.endsWith("/")) u = u.dropLast(1)
        return u
    }
}
