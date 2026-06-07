package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.LinearLayoutManager
import com.webook.reader.databinding.ActivityBookListBinding
import kotlinx.coroutines.launch

/** Список аудиокниг пользователя. */
class AudioListActivity : AppCompatActivity() {

    private lateinit var b: ActivityBookListBinding
    private val adapter = RowAdapter { row ->
        startActivity(
            Intent(this, AudioPlayerActivity::class.java)
                .putExtra("id", row.id)
                .putExtra("title", row.title)
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityBookListBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.toolbar.title = "Аудиокниги"
        b.toolbar.setNavigationOnClickListener { finish() }
        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter
        b.list.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))

        load()
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        b.progress.visibility = View.VISIBLE
        b.empty.visibility = View.GONE
        lifecycleScope.launch {
            try {
                val items = Api.audiobooks(
                    Prefs.baseUrl(this@AudioListActivity),
                    Prefs.token(this@AudioListActivity),
                )
                adapter.submit(items.map {
                    val parts = buildList {
                        if (it.author.isNotBlank()) add(it.author)
                        add("${it.trackCount} гл.")
                        if (it.duration > 0) add(fmtTime(it.duration))
                    }
                    Row("audiobook", it.id, it.title, parts.joinToString(" · "), "🎧")
                })
                if (items.isEmpty()) {
                    b.empty.text = "Аудиокниг нет.\nЗагрузите их на сайте."
                    b.empty.visibility = View.VISIBLE
                }
            } catch (e: Exception) {
                b.empty.text = "Не удалось загрузить список"
                b.empty.visibility = View.VISIBLE
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }
}
