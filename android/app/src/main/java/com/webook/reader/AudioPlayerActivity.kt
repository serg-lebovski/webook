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
        val pad = (20 * resources.displayMetrics.density).toInt()
        val root = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(pad, pad, pad, 0)
        }
        val label = android.widget.TextView(this)
        val bar = SeekBar(this).apply { max = 45 }          // 0.5×..3.0× шаг 0.05
        fun fromBar(p: Int) = 0.5f + p * 0.05f
        bar.progress = (((svc.getSpeed() - 0.5f) / 0.05f).toInt()).coerceIn(0, 45)
        label.text = "Скорость: %.2f×".format(fromBar(bar.progress))
        bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?, p: Int, u: Boolean) {
                val v = fromBar(p); label.text = "Скорость: %.2f×".format(v); svc.setSpeed(v)
            }
            override fun onStartTrackingTouch(s: SeekBar?) {}
            override fun onStopTrackingTouch(s: SeekBar?) {}
        })
        root.addView(label); root.addView(bar)
        AlertDialog.Builder(this)
            .setTitle("Скорость воспроизведения")
            .setView(root)
            .setPositiveButton("Готово", null)
            .setNeutralButton("1.0×") { _, _ -> svc.setSpeed(1.0f) }
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

    override fun onCreateOptionsMenu(menu: android.view.Menu): Boolean {
        menu.add(0, 1, 0, "Скачать офлайн").setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 2, 1, "Удалить загрузку").setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_NEVER)
        return true
    }

    override fun onOptionsItemSelected(item: android.view.MenuItem): Boolean {
        when (item.itemId) {
            1 -> downloadOffline()
            2 -> {
                Downloads.deleteAudiobook(this, audiobookId)
                android.widget.Toast.makeText(this, "Загрузка удалена", android.widget.Toast.LENGTH_SHORT).show()
            }
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun downloadOffline() {
        val d = detail ?: return
        val base = Prefs.baseUrl(this); val token = Prefs.token(this)
        android.widget.Toast.makeText(this, "Загрузка ${d.tracks.size} глав…", android.widget.Toast.LENGTH_SHORT).show()
        lifecycleScope.launch {
            var ok = 0
            for (t in d.tracks) {
                val dest = Downloads.audioFile(this@AudioPlayerActivity, d.id, t.id)
                if (dest.exists() && dest.length() > 0) { ok++; continue }
                val done = try {
                    Api.downloadTo(base, token, "/api/audiobooks/${d.id}/tracks/${t.id}/serve", dest)
                } catch (e: Exception) { false }
                if (done) ok++
            }
            android.widget.Toast.makeText(this@AudioPlayerActivity,
                "Скачано глав: $ok из ${d.tracks.size}", android.widget.Toast.LENGTH_LONG).show()
        }
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
