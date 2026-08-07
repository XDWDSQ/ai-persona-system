package com.xiaoni.persona

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast

/**
 * 设置页：服务器地址 + 访问口令。
 * 「测试连接」只验证不保存；「保存并进入」写配置并回主界面。
 */
class SettingsActivity : Activity() {

    private lateinit var etServer: EditText
    private lateinit var etToken: EditText
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppConfig.init(this)
        setContentView(R.layout.activity_settings)

        etServer = findViewById(R.id.et_server)
        etToken = findViewById(R.id.et_token)
        tvStatus = findViewById(R.id.tv_status)
        etServer.setText(AppConfig.serverUrl ?: "")
        etToken.setText(AppConfig.token)

        findViewById<Button>(R.id.btn_test).setOnClickListener {
            val url = AppConfig.normalizeUrl(etServer.text.toString())
            val tok = etToken.text.toString().trim()
            if (url == null) { toast("请输入服务器地址"); return@setOnClickListener }
            if (tok.isEmpty()) { toast("请输入访问口令"); return@setOnClickListener }
            tvStatus.text = "正在测试连接…"
            Thread {
                val err = LoginManager.testLogin(url, tok)
                runOnUiThread {
                    tvStatus.text = if (err == null) "✅ 连接成功，口令正确"
                    else "❌ $err"
                }
            }.start()
        }

        findViewById<Button>(R.id.btn_save).setOnClickListener {
            val url = AppConfig.normalizeUrl(etServer.text.toString())
            val tok = etToken.text.toString().trim()
            if (url == null) { toast("请输入服务器地址"); return@setOnClickListener }
            if (tok.isEmpty()) { toast("请输入访问口令"); return@setOnClickListener }
            AppConfig.save(url, tok)
            LoginManager.updateConfig(url, tok)
            toast("已保存")
            setResult(RESULT_OK)
            finish()
        }
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
