package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import com.webook.reader.databinding.ActivityBookListBinding
import kotlinx.coroutines.launch

/** Список книг конкретной полки или автора — сеткой обложек. */
class BookListActivity : AppCompatActivity() {

    private lateinit var b: ActivityBookListBinding
    private lateinit var coverAdapter: CoverAdapter

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

        coverAdapter = CoverAdapter(Prefs.baseUrl(this), Prefs.token(this), onClick = { item ->
            startActivity(
                Intent(this, ReaderActivity::class.java)
                    .putExtra("path", "/api/books/${item.id}/text")
                    .putExtra("resourceKey", "book:${item.id}")
            )
        })
        b.list.layoutManager = GridLayoutManager(this, 3)
        b.list.adapter = coverAdapter

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
                coverAdapter.submit(books)
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
}
