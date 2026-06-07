package com.webook.reader

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.media.app.NotificationCompat.MediaStyle
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** Фоновое воспроизведение аудиокниг (стриминг с сервера по Bearer). */
class AudioService : Service() {

    companion object {
        const val ACTION_PLAY = "com.webook.reader.A_PLAY"
        const val ACTION_PAUSE = "com.webook.reader.A_PAUSE"
        const val ACTION_NEXT = "com.webook.reader.A_NEXT"
        const val ACTION_PREV = "com.webook.reader.A_PREV"
        private const val CHANNEL = "audio_playback"
        private const val NOTIF_ID = 43
    }

    interface Listener {
        fun onTrack(index: Int)
        fun onState(playing: Boolean)
        fun onProgress(positionMs: Int, durationMs: Int)
        fun onPrepared()
    }

    inner class LocalBinder : Binder() {
        val service: AudioService get() = this@AudioService
    }

    private val binder = LocalBinder()
    private var mp: MediaPlayer? = null
    private var prepared = false

    private var audiobookId = 0
    var title = ""
        private set
    private var tracks: List<AudioTrack> = emptyList()
    var trackIndex = 0
        private set
    var playing = false
        private set
    private var speed = 1.0f
    private var pendingSeekMs = 0

    var listener: Listener? = null
    private lateinit var mediaSession: MediaSessionCompat
    private val handler = Handler(Looper.getMainLooper())
    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var sleepRunnable: Runnable? = null
    var sleepEndAt: Long = 0L
        private set
    private var saveTick = 0

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        mediaSession = MediaSessionCompat(this, "WeBookAudio").apply {
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = play()
                override fun onPause() = pause()
                override fun onSkipToNext() = nextTrack(true)
                override fun onSkipToPrevious() = prevTrack()
                override fun onStop() = pause()
            })
            isActive = true
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PLAY -> play()
            ACTION_PAUSE -> pause()
            ACTION_NEXT -> nextTrack(true)
            ACTION_PREV -> prevTrack()
        }
        return START_NOT_STICKY
    }

    val currentAudiobookId: Int get() = audiobookId
    fun trackList(): List<AudioTrack> = tracks

    fun load(id: Int, title: String, tracks: List<AudioTrack>, startTrackIndex: Int, startPosSec: Double) {
        this.audiobookId = id
        this.title = title
        this.tracks = tracks
        this.trackIndex = startTrackIndex.coerceIn(0, (tracks.size - 1).coerceAtLeast(0))
        prepareTrack(trackIndex, (startPosSec * 1000).toInt(), autoplay = false)
    }

    private fun prepareTrack(index: Int, posMs: Int, autoplay: Boolean) {
        if (tracks.isEmpty()) return
        trackIndex = index.coerceIn(0, tracks.size - 1)
        prepared = false
        pendingSeekMs = posMs.coerceAtLeast(0)
        releasePlayer()
        val base = Prefs.baseUrl(this)
        val token = Prefs.token(this)
        val url = Api.trackUrl(base, audiobookId, tracks[trackIndex].id)
        mp = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            try {
                setDataSource(applicationContext, Uri.parse(url), mapOf("Authorization" to "Bearer $token"))
            } catch (e: Exception) {
                return
            }
            setOnPreparedListener {
                prepared = true
                if (pendingSeekMs > 0 && pendingSeekMs < it.duration) it.seekTo(pendingSeekMs)
                pendingSeekMs = 0
                applySpeed()
                if (autoplay || playing) { it.start(); playing = true; startForegroundNotif() }
                listener?.onPrepared()
                listener?.onTrack(trackIndex)
                listener?.onState(playing)
                startProgressLoop()
                updateNotification()
            }
            setOnCompletionListener { nextTrack(autoplay = true) }
            setOnErrorListener { _, _, _ -> true }
            prepareAsync()
        }
    }

    fun play() {
        val player = mp
        if (player == null || !prepared) {
            prepareTrack(trackIndex, pendingSeekMs, autoplay = true)
            return
        }
        player.start()
        playing = true
        applySpeed()
        startForegroundNotif()
        startProgressLoop()
        listener?.onState(true)
    }

    fun pause() {
        if (prepared) mp?.pause()
        playing = false
        saveProgress(false)
        updateNotification()
        listener?.onState(false)
    }

    fun toggle() = if (playing) pause() else play()

    fun nextTrack(autoplay: Boolean) {
        if (trackIndex < tracks.size - 1) {
            prepareTrack(trackIndex + 1, 0, autoplay)
        } else {
            playing = false
            saveProgress(true)
            listener?.onState(false)
            updateNotification()
        }
    }

    fun prevTrack() {
        prepareTrack((trackIndex - 1).coerceAtLeast(0), 0, playing)
    }

    fun playTrack(index: Int) {
        prepareTrack(index, 0, autoplay = true)
    }

    fun seekTo(ms: Int) {
        if (prepared) mp?.seekTo(ms.coerceAtLeast(0))
        else pendingSeekMs = ms
    }

    fun skip(deltaMs: Int) {
        val p = mp ?: return
        if (prepared) p.seekTo((p.currentPosition + deltaMs).coerceIn(0, p.duration))
    }

    fun positionMs(): Int = if (prepared) (mp?.currentPosition ?: 0) else pendingSeekMs
    fun durationMs(): Int = if (prepared) (mp?.duration ?: 0) else (tracks.getOrNull(trackIndex)?.durationSec?.times(1000))?.toInt() ?: 0

    fun setSpeed(rate: Float) {
        speed = rate.coerceIn(0.5f, 3.0f)
        applySpeed()
    }

    fun getSpeed() = speed

    private fun applySpeed() {
        val p = mp ?: return
        if (Build.VERSION.SDK_INT >= 23 && prepared) {
            try {
                val params = p.playbackParams
                params.speed = speed
                p.playbackParams = params
                if (!playing) p.pause()  // выставление params может запустить плеер
            } catch (e: Exception) { /* устройство не поддерживает */ }
        }
    }

    fun setSleepTimer(minutes: Int) {
        sleepRunnable?.let { handler.removeCallbacks(it) }
        sleepRunnable = null
        if (minutes <= 0) { sleepEndAt = 0L; return }
        val ms = minutes * 60_000L
        sleepEndAt = System.currentTimeMillis() + ms
        sleepRunnable = Runnable { pause(); sleepEndAt = 0L; sleepRunnable = null }
        handler.postDelayed(sleepRunnable!!, ms)
    }

    // --- Прогресс / сохранение -------------------------------------------

    private val progressRunnable = object : Runnable {
        override fun run() {
            if (prepared) {
                listener?.onProgress(positionMs(), durationMs())
                saveTick++
                if (playing && saveTick >= 5) { saveTick = 0; saveProgress(false) }
            }
            handler.postDelayed(this, 1000)
        }
    }

    private fun startProgressLoop() {
        handler.removeCallbacks(progressRunnable)
        handler.post(progressRunnable)
    }

    private fun saveProgress(finished: Boolean) {
        if (tracks.isEmpty()) return
        val base = Prefs.baseUrl(this)
        val token = Prefs.token(this)
        if (base.isEmpty() || token.isEmpty()) return
        val trackId = tracks[trackIndex].id
        val posSec = positionMs() / 1000.0
        io.launch { try { Api.postAudioProgress(base, token, audiobookId, trackId, posSec, finished) } catch (e: Exception) {} }
    }

    // --- Уведомление ------------------------------------------------------

    private fun startForegroundNotif() {
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceCompat.startForeground(
                this, NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
            )
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification())
    }

    private fun action(act: String): PendingIntent {
        val i = Intent(this, AudioService::class.java).setAction(act)
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
                    positionMs().toLong(), speed
                )
                .build()
        )
        val openIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, AudioPlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val playPauseIcon = if (playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play
        val playPauseAction = if (playing) ACTION_PAUSE else ACTION_PLAY
        val chapter = tracks.getOrNull(trackIndex)?.title ?: ""
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title.ifBlank { "Аудиокнига" })
            .setContentText(chapter)
            .setContentIntent(openIntent)
            .setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .addAction(android.R.drawable.ic_media_previous, "Назад", action(ACTION_PREV))
            .addAction(playPauseIcon, "Пуск/пауза", action(playPauseAction))
            .addAction(android.R.drawable.ic_media_next, "Вперёд", action(ACTION_NEXT))
            .setStyle(
                MediaStyle().setMediaSession(mediaSession.sessionToken).setShowActionsInCompactView(0, 1, 2)
            )
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Аудиокниги", NotificationManager.IMPORTANCE_LOW)
                        .apply { setShowBadge(false) }
                )
            }
        }
    }

    private fun releasePlayer() {
        try { mp?.reset(); mp?.release() } catch (e: Exception) {}
        mp = null
        prepared = false
    }

    override fun onDestroy() {
        handler.removeCallbacks(progressRunnable)
        sleepRunnable?.let { handler.removeCallbacks(it) }
        saveProgress(false)
        io.cancel()
        releasePlayer()
        mediaSession.release()
        super.onDestroy()
    }
}
