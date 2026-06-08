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
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
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
    private val adapter = ParagraphAdapter { i -> jumpTo(i) }

    private var service: TtsService? = null
    private var bound = false
    private var text: TextResult? = null
    private var resourceKey: String = ""
    private var pendingPath: String = ""
    private var loadedIntoService = false
    private var serverPercent: Double = 0.0

    private var ttsEnabled = false
    private var ttsMenuItem: MenuItem? = null
    private var userTouching = false      // пользователь тащит список
    private var currentIndex = 0
    private var lastQuery = ""

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

        setSupportActionBar(b.toolbar)
        b.toolbar.setNavigationOnClickListener { finish() }

        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter
        b.list.addOnScrollListener(scrollListener)

        b.playBtn.setOnClickListener { onPlayClicked() }
        b.prevBtn.setOnClickListener { service?.prev() }
        b.nextBtn.setOnClickListener { service?.next() }

        b.seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                currentIndex = progress
                (b.list.layoutManager as LinearLayoutManager)
                    .scrollToPositionWithOffset(progress, 0)
                setStatusText(progress)
            }
            override fun onStartTrackingTouch(sb: SeekBar?) {}
            override fun onStopTrackingTouch(sb: SeekBar?) {
                persistLocal(currentIndex)
                if (service?.playing == true) service?.seekTo(currentIndex)
                else service?.setIndexSilent(currentIndex)
            }
        })

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (pendingPath.isNotEmpty()) loadText(pendingPath)
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, TtsService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    override fun onPause() {
        super.onPause()
        savePosition()   // onPause вызывается надёжнее, чем onStop
    }

    override fun onStop() {
        super.onStop()
        savePosition()
        service?.listener = null
        if (bound) {
            unbindService(connection)
            bound = false
        }
    }

    // --- Загрузка текста + позиции ---------------------------------------

    private fun loadText(path: String) {
        if (path.isEmpty()) { finish(); return }
        b.progress.visibility = View.VISIBLE
        lifecycleScope.launch {
            try {
                val base = Prefs.baseUrl(this@ReaderActivity)
                val token = Prefs.token(this@ReaderActivity)
                val res = Api.text(base, token, path)
                text = res
                Offline.save(this@ReaderActivity, resourceKey, res)  // авто-кэш для офлайна
                if (resourceKey.isNotEmpty())
                    Reminders.saveLastRead(this@ReaderActivity, resourceKey, res.title, path)
                serverPercent = try { Api.getProgress(base, token, progressPath()) }
                    catch (e: Exception) { 0.0 }
                b.toolbar.title = res.title
                adapter.submit(res.paragraphs)
                tryInit()
            } catch (e: Exception) {
                // нет сети — пробуем офлайн-копию
                val cached = Offline.load(this@ReaderActivity, resourceKey)
                if (cached != null) {
                    text = cached
                    serverPercent = 0.0
                    if (resourceKey.isNotEmpty())
                        Reminders.saveLastRead(this@ReaderActivity, resourceKey, cached.title, path)
                    b.toolbar.title = cached.title
                    adapter.submit(cached.paragraphs)
                    Toast.makeText(this@ReaderActivity, "Офлайн-режим", Toast.LENGTH_SHORT).show()
                    tryInit()
                } else {
                    val msg = if (e is ApiException) "Не удалось загрузить: ${e.message}"
                    else "Нет связи и нет офлайн-копии"
                    toastFinish(msg)
                }
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }

    private fun tryInit() {
        val svc = service ?: return
        val sameLive = svc.size > 0 && (resourceKey.isEmpty() || svc.resourceKey == resourceKey)

        if (loadedIntoService || sameLive) {
            if (text == null) {
                resourceKey = svc.resourceKey
                text = TextResult(0, svc.bookTitle(), "", svc.paragraphsList())
                b.toolbar.title = svc.bookTitle()
                adapter.submit(svc.paragraphsList())
            }
            loadedIntoService = true
            configureSeekMax()
            currentIndex = svc.index
            ttsEnabled = svc.playing
            b.transportRow.visibility = if (ttsEnabled) View.VISIBLE else View.GONE
            adapter.setHighlight(if (ttsEnabled) currentIndex else -1)
            (b.list.layoutManager as LinearLayoutManager)
                .scrollToPositionWithOffset(currentIndex, 0)
            setStatusText(currentIndex)
            updatePlayIcon(svc.playing)
            ttsMenuItem?.isChecked = ttsEnabled
            return
        }

        val t = text ?: return
        val last = (t.paragraphs.size - 1).coerceAtLeast(0)
        val localPara = restorePosition()
        val serverPara = if (serverPercent > 0.0) Math.round(serverPercent * last).toInt() else 0
        val start = maxOf(localPara, serverPara).coerceIn(0, last)
        svc.load(t.title, t.paragraphs, start, resourceKey)
        applySavedVoice(svc)
        svc.setRate(Prefs.prefs(this).getFloat("rate", 1.0f))
        svc.setPitch(Prefs.prefs(this).getFloat("pitch", 1.0f))

        loadedIntoService = true
        configureSeekMax()
        currentIndex = start
        ttsEnabled = false
        b.transportRow.visibility = View.GONE
        adapter.setHighlight(-1)
        (b.list.layoutManager as LinearLayoutManager).scrollToPositionWithOffset(start, 0)
        setStatusText(start)
        if (start > 0) {
            Toast.makeText(this, "Продолжаем с абзаца ${start + 1}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun configureSeekMax() {
        val total = text?.paragraphs?.size ?: 0
        b.seek.max = (total - 1).coerceAtLeast(1)
    }

    // --- Прокрутка списка вручную ----------------------------------------

    private val scrollListener = object : RecyclerView.OnScrollListener() {
        override fun onScrollStateChanged(rv: RecyclerView, newState: Int) {
            if (newState == RecyclerView.SCROLL_STATE_DRAGGING) {
                userTouching = true
            } else if (newState == RecyclerView.SCROLL_STATE_IDLE && userTouching) {
                val top = firstVisible()
                currentIndex = top
                persistLocal(top)
                // озвучка должна продолжиться с верхней видимой строки
                if (service?.playing == true) service?.seekTo(top)
                else service?.setIndexSilent(top)
                if (ttsEnabled) adapter.setHighlight(top)
                setStatusText(top)
                userTouching = false
            }
        }
        override fun onScrolled(rv: RecyclerView, dx: Int, dy: Int) {
            if (userTouching) {
                val top = firstVisible()
                currentIndex = top
                setStatusText(top)
            }
        }
    }

    private fun firstVisible(): Int {
        val lm = b.list.layoutManager as LinearLayoutManager
        return lm.findFirstVisibleItemPosition().coerceAtLeast(0)
    }

    // --- Меню -------------------------------------------------------------

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.reader_menu, menu)
        ttsMenuItem = menu.findItem(R.id.action_tts)
        ttsMenuItem?.isChecked = ttsEnabled
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_tts -> { toggleTts(); true }
            R.id.action_speed -> { showSpeedDialog(); true }
            R.id.action_voice -> { showVoiceDialog(); true }
            R.id.action_search -> { showSearchDialog(); true }
            R.id.action_sleep -> { showSleepDialog(); true }
            R.id.action_offline -> { downloadOffline(); true }
            R.id.action_about -> { showAboutDialog(); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    private fun toggleTts() {
        val svc = service ?: return
        ttsEnabled = !ttsEnabled
        ttsMenuItem?.isChecked = ttsEnabled
        if (ttsEnabled) {
            b.transportRow.visibility = View.VISIBLE
            svc.setIndexSilent(currentIndex)
            adapter.setHighlight(currentIndex)
            if (svc.isReady()) {
                ContextCompat.startForegroundService(this, Intent(this, TtsService::class.java))
                svc.play()
            } else {
                Toast.makeText(this, "Готовлю синтез речи…", Toast.LENGTH_SHORT).show()
            }
        } else {
            svc.pause()
            b.transportRow.visibility = View.GONE
            adapter.setHighlight(-1)
        }
    }

    private fun onPlayClicked() {
        val svc = service ?: return
        if (!svc.isReady()) return
        if (!svc.playing) {
            ContextCompat.startForegroundService(this, Intent(this, TtsService::class.java))
        }
        svc.toggle()
    }

    private fun showSpeedDialog() {
        val svc = service ?: return
        val pad = (20 * resources.displayMetrics.density).toInt()
        val root = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(pad, pad, pad, 0)
        }

        // --- Скорость 0.5×..2.0× (шаг 0.05) ---
        val rateLabel = TextView(this)
        val rateBar = SeekBar(this).apply { max = 30 }
        fun rateFromBar(p: Int) = 0.5f + p * 0.05f
        rateBar.progress = (((svc.getRate() - 0.5f) / 0.05f).toInt()).coerceIn(0, 30)
        rateLabel.text = "Скорость: %.2f×".format(rateFromBar(rateBar.progress))
        rateBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?, p: Int, u: Boolean) {
                val v = rateFromBar(p); rateLabel.text = "Скорость: %.2f×".format(v)
                svc.setRate(v); Prefs.prefs(this@ReaderActivity).edit().putFloat("rate", v).apply()
            }
            override fun onStartTrackingTouch(s: SeekBar?) {}
            override fun onStopTrackingTouch(s: SeekBar?) {}
        })

        // --- Тембр 0.5..2.0 (шаг 0.05) ---
        val pitchLabel = TextView(this).apply { setPadding(0, pad, 0, 0) }
        val pitchBar = SeekBar(this).apply { max = 30 }
        fun pitchFromBar(p: Int) = 0.5f + p * 0.05f
        pitchBar.progress = (((svc.getPitch() - 0.5f) / 0.05f).toInt()).coerceIn(0, 30)
        pitchLabel.text = "Тембр: %.2f".format(pitchFromBar(pitchBar.progress))
        pitchBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar?, p: Int, u: Boolean) {
                val v = pitchFromBar(p); pitchLabel.text = "Тембр: %.2f".format(v)
                svc.setPitch(v); Prefs.prefs(this@ReaderActivity).edit().putFloat("pitch", v).apply()
            }
            override fun onStartTrackingTouch(s: SeekBar?) {}
            override fun onStopTrackingTouch(s: SeekBar?) {}
        })

        root.addView(rateLabel); root.addView(rateBar)
        root.addView(pitchLabel); root.addView(pitchBar)

        AlertDialog.Builder(this)
            .setTitle("Озвучка")
            .setView(root)
            .setPositiveButton("Готово", null)
            .setNeutralButton("Сброс") { _, _ ->
                svc.setRate(1.0f); svc.setPitch(1.0f)
                Prefs.prefs(this).edit().putFloat("rate", 1.0f).putFloat("pitch", 1.0f).apply()
            }
            .show()
    }

    private fun showVoiceDialog() {
        val svc = service ?: return
        val voices = svc.availableVoices()
        if (voices.isEmpty()) {
            AlertDialog.Builder(this)
                .setTitle("Голоса")
                .setMessage("Голосовые движки не найдены. Установите голоса: Настройки Android → Язык и ввод → Синтез речи.")
                .setPositiveButton("OK", null)
                .show()
            return
        }
        val labels = voices.map { v ->
            val q = if (v.quality >= 400) " ★" else ""
            "${v.locale.displayName}$q"
        }.toTypedArray()
        val checked = voices.indexOfFirst { it.name == svc.currentVoiceName() }
        AlertDialog.Builder(this)
            .setTitle("Выбор голоса")
            .setSingleChoiceItems(labels, checked) { dialog, which ->
                svc.setVoice(voices[which])
                Prefs.prefs(this).edit().putString("voice", voices[which].name).apply()
                dialog.dismiss()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun showSleepDialog() {
        val svc = service ?: return
        val labels = arrayOf("Выключить", "5 минут", "10 минут", "15 минут",
            "30 минут", "45 минут", "60 минут")
        val minutes = intArrayOf(0, 5, 10, 15, 30, 45, 60)
        AlertDialog.Builder(this)
            .setTitle("Таймер сна")
            .setItems(labels) { _, which -> svc.setSleepTimer(minutes[which]) }
            .show()
    }

    private fun downloadOffline() {
        val t = text ?: return
        Offline.save(this, resourceKey, t)
        Toast.makeText(this, "Сохранено для офлайна (${t.paragraphs.size} абз.)", Toast.LENGTH_SHORT).show()
    }

    private fun showAboutDialog() {
        val t = text ?: return
        val total = t.paragraphs.size
        val kind = if (resourceKey.startsWith("article")) "Статья" else "Книга"
        val msg = buildString {
            append("Название: ${t.title}\n")
            if (t.author.isNotBlank()) append("Автор: ${t.author}\n")
            append("Тип: $kind\n")
            append("Абзацев: $total")
        }
        AlertDialog.Builder(this)
            .setTitle("О книге")
            .setMessage(msg)
            .setPositiveButton("OK", null)
            .show()
    }

    private fun showSearchDialog() {
        val t = text ?: return
        val input = EditText(this).apply {
            setText(lastQuery)
            hint = "Текст для поиска"
            setSingleLine()
        }
        AlertDialog.Builder(this)
            .setTitle("Поиск по книге")
            .setView(input)
            .setPositiveButton("Найти") { _, _ ->
                val q = input.text.toString().trim()
                if (q.isNotEmpty()) doSearch(q, t.paragraphs)
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun doSearch(query: String, paragraphs: List<String>) {
        lastQuery = query
        val q = query.lowercase()
        val total = paragraphs.size
        // ищем со следующего абзаца, по кругу
        for (off in 1..total) {
            val i = (currentIndex + off) % total
            if (paragraphs[i].lowercase().contains(q)) {
                jumpTo(i)
                Toast.makeText(this, "Найдено в абзаце ${i + 1}", Toast.LENGTH_SHORT).show()
                return
            }
        }
        Toast.makeText(this, "Не найдено", Toast.LENGTH_SHORT).show()
    }

    private fun jumpTo(i: Int) {
        val last = ((text?.paragraphs?.size ?: 1) - 1).coerceAtLeast(0)
        currentIndex = i.coerceIn(0, last)
        (b.list.layoutManager as LinearLayoutManager)
            .scrollToPositionWithOffset(currentIndex, 0)
        setStatusText(currentIndex)
        if (ttsEnabled) adapter.setHighlight(currentIndex)
        persistLocal(currentIndex)
        if (service?.playing == true) service?.seekTo(currentIndex)
        else service?.setIndexSilent(currentIndex)
    }

    // --- TtsService.Listener ---------------------------------------------

    override fun onIndex(index: Int) {
        currentIndex = index
        if (ttsEnabled) adapter.setHighlight(index)
        (b.list.layoutManager as LinearLayoutManager)
            .scrollToPositionWithOffset(index, 120)
        setStatusText(index)
    }

    override fun onState(playing: Boolean) = updatePlayIcon(playing)

    override fun onReady() {
        val svc = service ?: return
        currentIndex = svc.index
        setStatusText(svc.index)
        updatePlayIcon(svc.playing)
    }

    override fun onSleep(endAtMs: Long) {}

    private fun updatePlayIcon(playing: Boolean) {
        b.playBtn.setIconResource(
            if (playing) android.R.drawable.ic_media_pause
            else android.R.drawable.ic_media_play
        )
    }

    /** Статус чтения в реальном времени + полоса прогресса. */
    private fun setStatusText(index: Int) {
        val total = text?.paragraphs?.size ?: return
        if (total <= 0) return
        val last = (total - 1).coerceAtLeast(1)
        val pct = (index * 100) / last
        b.toolbar.subtitle = "Абзац ${index + 1} из $total · $pct%"
        b.pctLabel.text = "$pct%"
        if (b.seek.progress != index) b.seek.progress = index.coerceIn(0, b.seek.max)
    }

    // --- Сохранение / прочее ---------------------------------------------

    /** Сохраняем текущую позицию чтения напрямую (не зависит от состояния сервиса). */
    private fun savePosition() {
        if (resourceKey.isEmpty()) return
        Prefs.prefs(this).edit()
            .putInt("pos_$resourceKey", currentIndex)
            .putFloat("rate", service?.getRate() ?: 1.0f)
            .apply()
    }

    private fun persistLocal(i: Int) {
        if (resourceKey.isEmpty()) return
        Prefs.prefs(this).edit().putInt("pos_$resourceKey", i).apply()
    }

    private fun restorePosition(): Int = Prefs.prefs(this).getInt("pos_$resourceKey", 0)

    private fun progressPath(): String {
        val parts = resourceKey.split(":")
        if (parts.size != 2) return ""
        return if (parts[0] == "book") "/api/books/${parts[1]}/progress"
        else "/api/articles/${parts[1]}/progress"
    }

    private fun applySavedVoice(svc: TtsService) {
        val name = Prefs.prefs(this).getString("voice", null) ?: return
        svc.availableVoices().firstOrNull { it.name == name }?.let { svc.setVoice(it) }
    }

    private fun toastFinish(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
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
        holder.tv.setBackgroundColor(if (position == highlight) 0x332563EB else Color.TRANSPARENT)
        holder.itemView.setOnClickListener { onClick(position) }
    }

    override fun getItemCount() = items.size
}
