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

/** Список книг конкретной полки или автора (браузинг как на сайте). */
class BookListActivity : AppCompatActivity() {

    private lateinit var b: ActivityBookListBinding
    private val adapter = RowAdapter { row -> openReader(row) }

    private var shelfId: Int = -1
    private var authorId: Int = -1

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityBookListBinding.inflate(layoutInflater)
        setContentView(b.root)

        shelfId = intent.getIntExtra("shelf_id", -1)
        authorId = intent.getIntExtra("author_id", -1)
        b.toolbar.title = intent.getStringExtra("title") ?: "Книги"
        b.toolbar.setNavigationOnClickListener { finish() }

        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter
        b.list.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))

        load()
    }

    private fun load() {
        b.progress.visibility = View.VISIBLE
        b.empty.visibility = View.GONE
        lifecycleScope.launch {
            try {
                val books = Api.books(
                    Prefs.baseUrl(this@BookListActivity),
                    Prefs.token(this@BookListActivity),
                    shelfId = if (shelfId >= 0) shelfId else null,
                    authorId = if (authorId >= 0) authorId else null,
                )
                adapter.submit(books.map {
                    Row("book", it.id, it.title, it.author.ifBlank { "—" }, it.format.uppercase())
                })
                if (books.isEmpty()) {
                    b.empty.text = "Книг нет"
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

    private fun openReader(row: Row) {
        startActivity(
            Intent(this, ReaderActivity::class.java)
                .putExtra("path", "/api/books/${row.id}/text")
                .putExtra("resourceKey", "book:${row.id}")
        )
    }
}
