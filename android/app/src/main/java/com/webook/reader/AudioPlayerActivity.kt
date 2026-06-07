package com.webook.reader

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.IBinder
import android.widget.SeekBar
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.webook.reader.databinding.ActivityAudioBinding
import kotlinx.coroutines.launch

class AudioPlayerActivity : AppCompatActivity(), AudioService.Listener {

    private lateinit var b: ActivityAudioBinding
    private var service: AudioService? = null
    private var bound = false
    private var detail: AudiobookDetail? = null
    private var audiobookId = 0
    private var loadedIntoService = false
    private var seekDragging = false

    private val chapters = RowAdapter { row ->
        val idx = detail?.tracks?.indexOfFirst { it.id == row.id } ?: -1
        if (idx >= 0) {
            ensureStarted()
            service?.playTrack(idx)
        }
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as AudioService.LocalBinder).service
            service?.listener = this@AudioPlayerActivity
            bound = true
            tryInit()
        }
        override fun onServiceDisconnected(name: ComponentName?) { service = null; bound = false }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityAudioBinding.inflate(layoutInflater)
        setContentView(b.root)

        audiobookId = intent.getIntExtra("id", 0)
        b.toolbar.title = intent.getStringExtra("title") ?: "Аудиокнига"
        b.toolbar.setSubtitleTextColor(android.graphics.Color.WHITE)
        b.toolbar.setNavigationOnClickListener { finish() }

        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = chapters

        b.playBtn.setOnClickListener { ensureStarted(); service?.toggle() }
        b.prevBtn.setOnClickListener { service?.prevTrack() }
        b.nextBtn.setOnClickListener { service?.nextTrack(true) }
        b.back15.setOnClickListener { service?.skip(-15000) }
        b.fwd15.setOnClickListener { service?.skip(15000) }
        b.speedBtn.setOnClickListener { showSpeedDialog() }
        b.sleepBtn.setOnClickListener { showSleepDialog() }

        b.seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) b.curTime.text = fmtTime(progress / 1000.0)
            }
            override fun onStartTrackingTouch(sb: SeekBar?) { seekDragging = true }
            override fun onStopTrackingTouch(sb: SeekBar?) {
                seekDragging = false
                service?.seekTo(b.seek.progress)
            }
        })

        loadDetail()
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, AudioService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    override fun onStop() {
        super.onStop()
        service?.listener = null
        if (bound) { unbindService(connection); bound = false }
    }

    private fun loadDetail() {
        lifecycleScope.launch {
            try {
                val d = Api.audiobookDetail(
                    Prefs.baseUrl(this@AudioPlayerActivity),
                    Prefs.token(this@AudioPlayerActivity), audiobookId,
                )
                detail = d
                b.toolbar.title = d.title
                chapters.submit(d.tracks.map {
                    Row("track", it.id, it.title, fmtTime(it.durationSec), "${it.order + 1}")
                })
                tryInit()
            } catch (e: Exception) {
                android.widget.Toast.makeText(this@AudioPlayerActivity,
                    "Не удалось загрузить аудиокнигу", android.widget.Toast.LENGTH_LONG).show()
                finish()
            }
        }
    }

    private fun tryInit() {
        val svc = service ?: return
        val d = detail ?: return
        // сервис уже играет эту книгу — подхватываем
        if (svc.currentAudiobookId == audiobookId && svc.trackList().isNotEmpty()) {
            loadedIntoService = true
            updateChapter(svc.trackIndex)
            updatePlayIcon(svc.playing)
            b.seek.max = svc.durationMs().coerceAtLeast(1)
            b.seek.progress = svc.positionMs()
            return
        }
        if (loadedIntoService) return
        val startIndex = d.tracks.indexOfFirst { it.id == d.currentTrackId }.coerceAtLeast(0)
        svc.load(d.id, d.title, d.tracks, startIndex, d.position)
        loadedIntoService = true
        updateChapter(startIndex)
    }

    private fun ensureStarted() {
        if (service?.playing != true) {
            ContextCompat.startForegroundService(this, Intent(this, AudioService::class.java))
        }
    }

    private fun showSpeedDialog() {
        val svc = service ?: return
        val labels = arrayOf("0.75×", "1.0×", "1.25×", "1.5×", "1.75×", "2.0×", "2.5×", "3.0×")
        val rates = floatArrayOf(0.75f, 1.0f, 1.25f, 1.5f, 1.75f, 2.0f, 2.5f, 3.0f)
        val checked = rates.indexOfFirst { kotlin.math.abs(it - svc.getSpeed()) < 0.01f }.coerceAtLeast(0)
        AlertDialog.Builder(this)
            .setTitle("Скорость")
            .setSingleChoiceItems(labels, checked) { dlg, w -> svc.setSpeed(rates[w]); dlg.dismiss() }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun showSleepDialog() {
        val svc = service ?: return
        val labels = arrayOf("Выключить", "5 минут", "10 минут", "15 минут", "30 минут", "45 минут", "60 минут")
        val minutes = intArrayOf(0, 5, 10, 15, 30, 45, 60)
        AlertDialog.Builder(this)
            .setTitle("Таймер сна")
            .setItems(labels) { _, w -> svc.setSleepTimer(minutes[w]) }
            .show()
    }

    private fun updateChapter(index: Int) {
        val t = detail?.tracks?.getOrNull(index) ?: return
        b.chapter.text = t.title
        b.toolbar.subtitle = "Глава ${index + 1} из ${detail?.tracks?.size ?: 0}"
    }

    private fun updatePlayIcon(playing: Boolean) {
        b.playBtn.setIconResource(
            if (playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play
        )
    }

    // --- AudioService.Listener ---
    override fun onTrack(index: Int) {
        updateChapter(index)
        b.seek.max = service?.durationMs()?.coerceAtLeast(1) ?: 1
    }
    override fun onState(playing: Boolean) = updatePlayIcon(playing)
    override fun onProgress(positionMs: Int, durationMs: Int) {
        if (durationMs > 0) b.seek.max = durationMs
        if (!seekDragging) {
            b.seek.progress = positionMs
            b.curTime.text = fmtTime(positionMs / 1000.0)
        }
        b.durTime.text = fmtTime(durationMs / 1000.0)
    }
    override fun onPrepared() {
        b.seek.max = service?.durationMs()?.coerceAtLeast(1) ?: 1
    }
}
