package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import com.webook.reader.databinding.ActivityBookListBinding

/** Офлайн-библиотека: материалы, сохранённые для чтения/озвучки без сети. */
class OfflineActivity : AppCompatActivity() {

    private lateinit var b: ActivityBookListBinding
    private val adapter = RowAdapter { row -> onRowClick(row) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityBookListBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.toolbar.title = "Офлайн-библиотека"
        b.toolbar.setNavigationOnClickListener { finish() }
        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter

        load()
    }

    private fun load() {
        b.progress.visibility = View.GONE
        val items = Offline.list(this)
        if (items.isEmpty()) {
            adapter.submit(emptyList())
            b.empty.text = "Здесь появятся книги и статьи, которые вы открыли в приложении —\n" +
                "их текст сохраняется для чтения и озвучки без интернета."
            b.empty.visibility = View.VISIBLE
            return
        }
        b.empty.visibility = View.GONE
        adapter.submit(items.map { it ->
            val type = it.key.substringBefore(":")
            val id = it.key.substringAfter(":").toIntOrNull() ?: 0
            Row(type, id, it.title, it.author.ifBlank { "$type · ${it.paragraphs} абз." },
                if (type == "book") "КНИГА" else "СТАТЬЯ")
        })
    }

    private fun onRowClick(row: Row) {
        val key = "${row.type}:${row.id}"
        AlertDialog.Builder(this)
            .setTitle(row.title)
            .setItems(arrayOf("Открыть", "Удалить из офлайна")) { _, which ->
                if (which == 0) {
                    startActivity(
                        Intent(this, ReaderActivity::class.java)
                            .putExtra("path", Offline.pathForKey(key))
                            .putExtra("resourceKey", key)
                    )
                } else {
                    Offline.delete(this, key)
                    load()
                }
            }
            .show()
    }
}
