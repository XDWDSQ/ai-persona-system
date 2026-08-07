package com.xiaoni.persona

import android.media.AudioAttributes
import android.media.MediaMetadata
import android.media.MediaPlayer
import android.media.session.MediaSession
import android.media.session.PlaybackState
/**
 * 原生音频播放器：MediaPlayer + MediaSession。
 *
 * 前端（WebView）播放 TTS 时经 JS Bridge 调到这里，
 * MediaSession 注册后系统会显示媒体控件（通知栏 / 华为流体云等），
 * 且支持系统侧播放/暂停/停止控制（经 Callback 回写）。
 * 播放结束/出错通过 evaluateJavascript 回调前端（__audioEnded / __audioError）。
 */
class AudioPlayer(private val activity: MainActivity) {

    private var player: MediaPlayer? = null
    private var session: MediaSession? = null
    @Volatile private var currentUrl: String? = null
    private var endedJs = ""
    private var errorJs = ""

    /** 开始播放（自动停止旧播放）。成功返回 true。 */
    @Synchronized
    fun play(url: String, loop: Boolean): Boolean {
        stopInternal()
        currentUrl = url
        return try {
            val mp = MediaPlayer()
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .build()
            )
            mp.setDataSource(url)
            mp.isLooping = loop
            mp.setOnPreparedListener { it.start() }
            mp.setOnCompletionListener {
                // isLooping=true 时不会触发 onCompletion
                if (!mp.isLooping) onEnded()
            }
            mp.setOnErrorListener { _, _, _ ->
                onError()
                true
            }
            mp.prepareAsync()
            player = mp
            ensureSession()
            session?.isActive = true
            updatePlaybackState(PlaybackState.STATE_PLAYING)
            true
        } catch (e: Exception) {
            currentUrl = null
            false
        }
    }

    /** 播放 base64 音频（blob 兜底：写入缓存文件后播放）。成功返回 true。 */
    @Synchronized
    fun playBase64(b64: String, loop: Boolean): Boolean {
        return try {
            val bytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
            val f = java.io.File(activity.cacheDir, "tts_tmp.wav")
            f.writeBytes(bytes)
            play(f.absolutePath, loop)
        } catch (e: Exception) {
            false
        }
    }

    @Synchronized
    fun setLoop(loop: Boolean) {
        player?.isLooping = loop
    }

    @Synchronized
    fun pause() {
        player?.pause()
        session?.isActive = true
        updatePlaybackState(PlaybackState.STATE_PAUSED)
    }

    @Synchronized
    fun resume() {
        player?.start()
        session?.isActive = true
        updatePlaybackState(PlaybackState.STATE_PLAYING)
    }

    @Synchronized
    fun stop() {
        stopInternal()
    }

    private fun stopInternal() {
        try { player?.release() } catch (_: Exception) {}
        player = null
        currentUrl = null
        session?.isActive = false
    }

    fun isPlaying(): Boolean = player?.isPlaying == true

    fun isPaused(): Boolean = player?.let { !it.isPlaying } ?: false

    fun currentUrl(): String = currentUrl ?: ""

    /** 播放中对齐：跳到指定毫秒位置（播放中调用保持继续播放） */
    @Synchronized
    fun seekTo(ms: Long) {
        try {
            player?.seekTo(ms.toInt())
            android.util.Log.d("AudioPlayer", "seekTo $ms")
        } catch (_: Exception) {}
    }

    /** 音频总时长（毫秒），未就绪返回 -1 */
    fun durationMs(): Long {
        val d = try { player?.duration?.toLong() ?: -1L } catch (_: Exception) { -1L }
        android.util.Log.d("AudioPlayer", "durationMs=$d")
        return d
    }

    /** 当前播放位置（毫秒） */
    fun positionMs(): Long = try { player?.currentPosition?.toLong() ?: -1L } catch (_: Exception) { -1L }

    /** 前端注册 JS 回调（每次 play 时由前端设置） */
    fun setCallbacks(endedJs: String, errorJs: String) {
        this.endedJs = endedJs
        this.errorJs = errorJs
    }

    private fun onEnded() {
        runOnUiThread { activity.evaluateJs(endedJs) }
        updatePlaybackState(PlaybackState.STATE_NONE)
        session?.isActive = false
    }

    private fun onError() {
        runOnUiThread { activity.evaluateJs(errorJs) }
        session?.isActive = false
    }

    private fun runOnUiThread(block: () -> Unit) {
        activity.runOnUiThread { block() }
    }

    private fun ensureSession() {
        if (session != null) return
        val s = MediaSession(activity, "ai-persona")
        s.setCallback(object : MediaSession.Callback() {
            override fun onPlay() = resume()
            override fun onPause() = pause()
            override fun onStop() = stop()
            override fun onSeekTo(pos: Long) {
                try { player?.seekTo(pos.toInt()) } catch (_: Exception) {}
            }
        })
        s.setMetadata(
            MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, "小拟 · 语音")
                .build()
        )
        session = s
    }

    private fun updatePlaybackState(state: Int) {
        val pb = PlaybackState.Builder()
            .setActions(
                PlaybackState.ACTION_PLAY or
                    PlaybackState.ACTION_PAUSE or
                    PlaybackState.ACTION_STOP or
                    PlaybackState.ACTION_SEEK_TO
            )
            .setState(state, PlaybackState.PLAYBACK_POSITION_UNKNOWN, 1f)
            .build()
        session?.setPlaybackState(pb)
    }
}
