package com.webook.reader

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.media.app.NotificationCompat.MediaStyle
import java.util.Locale

class TtsService : Service(), TextToSpeech.OnInitListener {

    companion object {
        const val ACTION_PLAY = "com.webook.reader.PLAY"
        const val ACTION_PAUSE = "com.webook.reader.PAUSE"
        const val ACTION_NEXT = "com.webook.reader.NEXT"
        const val ACTION_PREV = "com.webook.reader.PREV"
        const val ACTION_STOP = "com.webook.reader.STOP"
        private const val CHANNEL = "tts_playback"
        private const val NOTIF_ID = 42
    }

    interface Listener {
        fun onIndex(index: Int)
        fun onState(playing: Boolean)
        fun onReady()
    }

    inner class LocalBinder : Binder() {
        val service: TtsService get() = this@TtsService
    }

    private val binder = LocalBinder()
    private var tts: TextToSpeech? = null
    private var ready = false

    private var paragraphs: List<String> = emptyList()
    var index = 0
        private set
    var playing = false
        private set
    private var rate = 1.0f
    private var title = ""
    var resourceKey: String = ""
        private set

    var listener: Listener? = null
    private lateinit var mediaSession: MediaSessionCompat
    private val handler = Handler(Looper.getMainLooper())

    val size: Int get() = paragraphs.size
    fun paragraphsList(): List<String> = paragraphs
    fun bookTitle(): String = title

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        tts = TextToSpeech(this, this)
        mediaSession = MediaSessionCompat(this, "WeBookTts").apply {
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = play()
                override fun onPause() = pause()
                override fun onSkipToNext() = next()
                override fun onSkipToPrevious() = prev()
                override fun onStop() = pause()
            })
            isActive = true
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            ready = true
            tts?.language = Locale.getDefault()
            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onError(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) {
                    val done = utteranceId?.toIntOrNull() ?: return
                    handler.post {
                        if (playing && done == index) advanceAuto()
                    }
                }
            })
            handler.post { listener?.onReady() }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PLAY -> play()
            ACTION_PAUSE -> pause()
            ACTION_NEXT -> next()
            ACTION_PREV -> prev()
            ACTION_STOP -> { pause(); stopSelf() }
        }
        return START_NOT_STICKY
    }

    // --- Управление воспроизведением -------------------------------------

    fun load(title: String, paragraphs: List<String>, startIndex: Int, resourceKey: String) {
        this.title = title
        this.paragraphs = paragraphs
        this.resourceKey = resourceKey
        this.index = startIndex.coerceIn(0, (paragraphs.size - 1).coerceAtLeast(0))
        this.playing = false
        tts?.stop()
        listener?.onReady()
    }

    fun isReady() = ready

    fun play() {
        if (!ready || paragraphs.isEmpty()) return
        playing = true
        speakCurrent()
        startForegroundNotif()
        listener?.onState(true)
    }

    fun pause() {
        playing = false
        tts?.stop()
        updateNotification()
        listener?.onState(false)
    }

    fun toggle() = if (playing) pause() else play()

    fun next() {
        if (index < paragraphs.size - 1) {
            index++
            listener?.onIndex(index)
            if (playing) speakCurrent() else updateNotification()
        }
    }

    fun prev() {
        if (index > 0) {
            index--
            listener?.onIndex(index)
            if (playing) speakCurrent() else updateNotification()
        }
    }

    fun seekTo(i: Int) {
        index = i.coerceIn(0, (paragraphs.size - 1).coerceAtLeast(0))
        listener?.onIndex(index)
        if (playing) speakCurrent() else updateNotification()
    }

    fun setRate(r: Float) {
        rate = r.coerceIn(0.3f, 3.0f)
        tts?.setSpeechRate(rate)
        if (playing) speakCurrent()
    }

    fun getRate() = rate

    fun availableVoices(): List<Voice> {
        return try {
            tts?.voices?.filter { !it.isNetworkConnectionRequired }
                ?.sortedBy { it.locale.displayName } ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }
    }

    fun currentVoiceName(): String? = try { tts?.voice?.name } catch (e: Exception) { null }

    fun setVoice(voice: Voice) {
        tts?.voice = voice
        tts?.language = voice.locale
        if (playing) speakCurrent()
    }

    private fun advanceAuto() {
        if (index < paragraphs.size - 1) {
            index++
            listener?.onIndex(index)
            speakCurrent()
            updateNotification()
        } else {
            playing = false
            listener?.onState(false)
            updateNotification()
        }
    }

    private fun speakCurrent() {
        val text = paragraphs.getOrNull(index) ?: return
        tts?.setSpeechRate(rate)
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), index.toString())
        updateNotification()
    }

    // --- Уведомление / foreground ----------------------------------------

    private fun startForegroundNotif() {
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceCompat.startForeground(
                this, NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
            )
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun updateNotification() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification())
    }

    private fun action(act: String): PendingIntent {
        val i = Intent(this, TtsService::class.java).setAction(act)
        return PendingIntent.getService(
            this, act.hashCode(), i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun buildNotification(): Notification {
        ensureChannel()

        mediaSession.setPlaybackState(
            PlaybackStateCompat.Builder()
                .setActions(
                    PlaybackStateCompat.ACTION_PLAY_PAUSE or
                        PlaybackStateCompat.ACTION_SKIP_TO_NEXT or
                        PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS
                )
                .setState(
                    if (playing) PlaybackStateCompat.STATE_PLAYING else PlaybackStateCompat.STATE_PAUSED,
                    PlaybackStateCompat.PLAYBACK_POSITION_UNKNOWN, 1f
                )
                .build()
        )

        val openIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, ReaderActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val playPauseIcon = if (playing)
            android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play
        val playPauseAction = if (playing) ACTION_PAUSE else ACTION_PLAY

        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title.ifBlank { "WeBook" })
            .setContentText("Абзац ${index + 1} из ${paragraphs.size}")
            .setContentIntent(openIntent)
            .setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .addAction(android.R.drawable.ic_media_previous, "Назад", action(ACTION_PREV))
            .addAction(playPauseIcon, "Пуск/пауза", action(playPauseAction))
            .addAction(android.R.drawable.ic_media_next, "Вперёд", action(ACTION_NEXT))
            .setStyle(
                MediaStyle()
                    .setMediaSession(mediaSession.sessionToken)
                    .setShowActionsInCompactView(0, 1, 2)
            )
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null) {
                val ch = NotificationChannel(
                    CHANNEL, "Озвучка", NotificationManager.IMPORTANCE_LOW
                )
                ch.setShowBadge(false)
                nm.createNotificationChannel(ch)
            }
        }
    }

    override fun onDestroy() {
        tts?.stop()
        tts?.shutdown()
        mediaSession.release()
        super.onDestroy()
    }
}
