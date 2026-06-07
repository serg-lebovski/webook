package com.webook.reader

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.SeekBar
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.webook.reader.databinding.ActivityReaderBinding
import kotlinx.coroutines.launch

class ReaderActivity : AppCompatActivity(), TtsService.Listener {

    private lateinit var b: ActivityReaderBinding
    private val adapter = ParagraphAdapter { i -> service?.seekTo(i) }

    private var service: TtsService? = null
    private var bound = false
    private var text: TextResult? = null
    private var resourceKey: String = ""
    private var pendingPath: String = ""
    private var loadedIntoService = false
    private var serverPercent: Double = 0.0
    private val uiHandler = android.os.Handler(android.os.Looper.getMainLooper())

    private val requestNotif =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as TtsService.LocalBinder).service
            service?.listener = this@ReaderActivity
            bound = true
            tryInit()
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            bound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityReaderBinding.inflate(layoutInflater)
        setContentView(b.root)

        resourceKey = intent.getStringExtra("resourceKey") ?: ""
        pendingPath = intent.getStringExtra("path") ?: ""

        b.toolbar.setNavigationOnClickListener { finish() }
        b.toolbar.setSubtitleTextColor(Color.WHITE)
        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter

        b.playBtn.setOnClickListener { onPlayClicked() }
        b.prevBtn.setOnClickListener { service?.prev() }
        b.nextBtn.setOnClickListener { service?.next() }
        b.voiceBtn.setOnClickListener { showVoiceDialog() }
        b.sleepBtn.setOnClickListener { showSleepDialog() }

        b.speed.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                val rate = progressToRate(progress)
                b.speedLabel.text = String.format("%.1f×", rate)
                if (fromUser) service?.setRate(rate)
            }
            override fun onStartTrackingTouch(sb: SeekBar?) {}
            override fun onStopTrackingTouch(sb: SeekBar?) {}
        })

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        // Если есть путь — грузим текст с сервера; иначе (открыто из уведомления)
        // подхватим уже играющий сервис в tryInit().
        if (pendingPath.isNotEmpty()) loadText(pendingPath)
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, TtsService::class.java), connection, Context.BIND_AUTO_CREATE)
        uiHandler.post(sleepTick)
    }

    override fun onStop() {
        super.onStop()
        uiHandler.removeCallbacks(sleepTick)
        savePosition()
        service?.listener = null
        if (bound) {
            unbindService(connection)
            bound = false
        }
    }

    private fun loadText(path: String) {
        if (path.isEmpty()) { finish(); return }
        b.progress.visibility = View.VISIBLE
        lifecycleScope.launch {
            try {
                val base = Prefs.baseUrl(this@ReaderActivity)
                val token = Prefs.token(this@ReaderActivity)
                val res = Api.text(base, token, path)
                text = res
                // позиция с сервера (чтобы продолжить с ПК)
                serverPercent = try { Api.getProgress(base, token, progressPath()) }
                    catch (e: Exception) { 0.0 }
                b.toolbar.title = res.title
                adapter.submit(res.paragraphs)
                tryInit()
            } catch (e: ApiException) {
                toastFinish("Не удалось загрузить текст: ${e.message}")
            } catch (e: Exception) {
                toastFinish("Нет связи с сервером")
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }

    /** Когда и сервис подключён, и текст готов — синхронизируемся с сервисом. */
    private fun tryInit() {
        val svc = service ?: return

        // Сервис уже озвучивает этот ресурс (вернулись из уведомления/свернули) —
        // подхватываем его состояние, ничего не сбрасываем.
        val sameLive = svc.size > 0 &&
            (resourceKey.isEmpty() || svc.resourceKey == resourceKey)
        if (loadedIntoService || sameLive) {
            if (text == null) {
                resourceKey = svc.resourceKey
                text = TextResult(0, svc.bookTitle(), "", svc.paragraphsList())
                b.toolbar.title = svc.bookTitle()
                adapter.submit(svc.paragraphsList())
                b.speed.progress = rateToProgress(svc.getRate())
                b.speedLabel.text = String.format("%.1f×", svc.getRate())
            }
            loadedIntoService = true
            highlight(svc.index)
            updatePlayIcon(svc.playing)
            return
        }

        // Первый запуск этого ресурса — отдаём абзацы сервису.
        val t = text ?: return
        val total = t.paragraphs.size
        val last = (total - 1).coerceAtLeast(0)
        // берём дальнюю из позиций: локальную (точную) и серверную (с ПК)
        val localPara = restorePosition()
        val serverPara = if (serverPercent > 0.0) Math.round(serverPercent * last).toInt() else 0
        val start = maxOf(localPara, serverPara).coerceIn(0, last)
        svc.load(t.title, t.paragraphs, start, resourceKey)
        applySavedVoice(svc)
        val savedRate = Prefs.prefs(this).getFloat("rate", 1.0f)
        b.speed.progress = rateToProgress(savedRate)
        b.speedLabel.text = String.format("%.1f×", savedRate)
        svc.setRate(savedRate)
        highlight(start)
        loadedIntoService = true
    }

    private fun onPlayClicked() {
        val svc = service ?: return
        if (!svc.isReady()) return
        if (!svc.playing) {
            ContextCompat.startForegroundService(this, Intent(this, TtsService::class.java))
        }
        svc.toggle()
    }

    private fun showVoiceDialog() {
        val svc = service ?: return
        val voices = svc.availableVoices()
        if (voices.isEmpty()) {
            AlertDialog.Builder(this)
                .setTitle("Голоса")
                .setMessage("Голосовые движки не найдены. Установите голоса в настройках Android: Настройки → Язык и ввод → Синтез речи.")
                .setPositiveButton("OK", null)
                .show()
            return
        }
        val labels = voices.map { v ->
            val q = if (v.quality >= 400) " ★" else ""
            "${v.locale.displayName}$q"
        }.toTypedArray()
        val currentName = svc.currentVoiceName()
        val checked = voices.indexOfFirst { it.name == currentName }
        AlertDialog.Builder(this)
            .setTitle("Выбор голоса")
            .setSingleChoiceItems(labels, checked) { dialog, which ->
                val v = voices[which]
                svc.setVoice(v)
                Prefs.prefs(this).edit().putString("voice", v.name).apply()
                dialog.dismiss()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun applySavedVoice(svc: TtsService) {
        val name = Prefs.prefs(this).getString("voice", null) ?: return
        svc.availableVoices().firstOrNull { it.name == name }?.let { svc.setVoice(it) }
    }

    private fun showSleepDialog() {
        val svc = service ?: return
        val labels = arrayOf("Выключить", "5 минут", "10 минут", "15 минут",
            "30 минут", "45 минут", "60 минут")
        val minutes = intArrayOf(0, 5, 10, 15, 30, 45, 60)
        AlertDialog.Builder(this)
            .setTitle("Таймер сна")
            .setItems(labels) { _, which ->
                svc.setSleepTimer(minutes[which])
            }
            .show()
    }

    private val sleepTick = object : Runnable {
        override fun run() {
            updateSleepLabel()
            uiHandler.postDelayed(this, 20_000)
        }
    }

    private fun updateSleepLabel() {
        val end = service?.sleepEndAt ?: 0L
        if (end > 0L) {
            val left = ((end - System.currentTimeMillis()) / 60000L).toInt() + 1
            b.sleepBtn.text = "Сон: $left мин"
        } else {
            b.sleepBtn.text = "Таймер сна"
        }
    }

    // --- TtsService.Listener ---------------------------------------------

    override fun onIndex(index: Int) = highlight(index)
    override fun onState(playing: Boolean) = updatePlayIcon(playing)
    override fun onReady() {
        val svc = service ?: return
        highlight(svc.index)
        updatePlayIcon(svc.playing)
    }
    override fun onSleep(endAtMs: Long) = updateSleepLabel()

    private fun highlight(index: Int) {
        adapter.setHighlight(index)
        (b.list.layoutManager as LinearLayoutManager)
            .scrollToPositionWithOffset(index, 120)
        updateStatus(index)
    }

    /** Статус чтения в реальном времени: «Абзац X из N · Y%». */
    private fun updateStatus(index: Int) {
        val total = text?.paragraphs?.size ?: return
        if (total <= 0) return
        val pct = if (total > 1) (index * 100) / (total - 1) else 100
        b.toolbar.subtitle = "Абзац ${index + 1} из $total · $pct%"
    }

    private fun progressPath(): String {
        val parts = resourceKey.split(":")
        if (parts.size != 2) return ""
        return if (parts[0] == "book") "/api/books/${parts[1]}/progress"
        else "/api/articles/${parts[1]}/progress"
    }

    private fun updatePlayIcon(playing: Boolean) {
        b.playBtn.setIconResource(
            if (playing) android.R.drawable.ic_media_pause
            else android.R.drawable.ic_media_play
        )
    }

    // --- Сохранение позиции / скорость -----------------------------------

    private fun savePosition() {
        val svc = service ?: return
        Prefs.prefs(this).edit()
            .putInt("pos_$resourceKey", svc.index)
            .putFloat("rate", svc.getRate())
            .apply()
    }

    private fun restorePosition(): Int =
        Prefs.prefs(this).getInt("pos_$resourceKey", 0)

    private fun progressToRate(progress: Int): Float = 0.5f + (progress / 30f) * 1.5f
    private fun rateToProgress(rate: Float): Int = (((rate - 0.5f) / 1.5f) * 30f).toInt().coerceIn(0, 30)

    private fun toastFinish(msg: String) {
        android.widget.Toast.makeText(this, msg, android.widget.Toast.LENGTH_LONG).show()
        finish()
    }
}

class ParagraphAdapter(private val onClick: (Int) -> Unit) :
    RecyclerView.Adapter<ParagraphAdapter.VH>() {

    private val items = ArrayList<String>()
    private var highlight = -1

    fun submit(list: List<String>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

    fun setHighlight(index: Int) {
        val old = highlight
        highlight = index
        if (old in items.indices) notifyItemChanged(old)
        if (highlight in items.indices) notifyItemChanged(highlight)
    }

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val tv: TextView = v.findViewById(R.id.paragraph)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_paragraph, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.tv.text = items[position]
        if (position == highlight) {
            holder.tv.setBackgroundColor(0x332563EB)
        } else {
            holder.tv.setBackgroundColor(Color.TRANSPARENT)
        }
        holder.itemView.setOnClickListener { onClick(position) }
    }

    override fun getItemCount() = items.size
}
