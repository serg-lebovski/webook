package com.webook.reader

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.tabs.TabLayout
import com.webook.reader.databinding.ActivityLibraryBinding
import kotlinx.coroutines.launch

class LibraryActivity : AppCompatActivity() {

    private lateinit var b: ActivityLibraryBinding
    private val adapter = RowAdapter { row -> onRowClick(row) }

    private var mode = "books"          // "books" | "notes"
    private var currentTab = 0          // 0=Полки, 1=Авторы, 2=Все

    private val pickBook =
        registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
            if (uri != null) uploadBook(uri)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityLibraryBinding.inflate(layoutInflater)
        setContentView(b.root)

        setSupportActionBar(b.toolbar)

        b.list.layoutManager = LinearLayoutManager(this)
        b.list.adapter = adapter
        b.list.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))

        b.tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                currentTab = tab.position
                if (mode == "books") load()
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })

        b.bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_books -> { setMode("books"); true }
                R.id.nav_audio -> {
                    startActivity(Intent(this, AudioListActivity::class.java))
                    false   // аудио — отдельный экран
                }
                R.id.nav_notes -> { setMode("notes"); true }
                R.id.nav_profile -> {
                    startActivity(Intent(this, ProfileActivity::class.java))
                    false   // не «выбираем» профиль — это отдельный экран
                }
                else -> false
            }
        }

        b.fab.setOnClickListener { pickBook.launch("*/*") }

        setMode("books")
    }

    private fun setMode(m: String) {
        mode = m
        if (m == "books") {
            b.toolbar.title = "Книги"
            b.tabs.visibility = View.VISIBLE
            b.fab.show()
        } else {
            b.toolbar.title = "Заметки"
            b.tabs.visibility = View.GONE
            b.fab.hide()
        }
        load()
    }

    private fun load() {
        val base = Prefs.baseUrl(this)
        val token = Prefs.token(this)
        setLoading(true)
        b.empty.visibility = View.GONE
        adapter.submit(emptyList())
        lifecycleScope.launch {
            try {
                val rows: List<Row> = if (mode == "notes") {
                    Api.articles(base, token).map {
                        val sub = if (it.minutes > 0) "${it.minutes} мин чтения" else "заметка"
                        Row("article", it.id, it.title, sub, "TXT")
                    }
                } else when (currentTab) {
                    0 -> Api.shelves(base, token).map {
                        Row("shelf", it.id, it.name, plural(it.count), it.count.toString())
                    }
                    1 -> Api.authors(base, token).map {
                        Row("author", it.id, it.name, plural(it.count), it.count.toString())
                    }
                    else -> Api.books(base, token).map {
                        Row("book", it.id, it.title, it.author.ifBlank { "—" }, it.format.uppercase())
                    }
                }
                adapter.submit(rows)
                if (rows.isEmpty()) {
                    b.empty.text = when {
                        mode == "notes" -> "Сохранённых заметок с текстом нет."
                        currentTab == 0 -> "Полок с озвучиваемыми книгами нет."
                        currentTab == 1 -> "Авторов с озвучиваемыми книгами нет."
                        else -> "Книг для озвучки нет.\nНажмите + чтобы загрузить EPUB / FB2 / PDF."
                    }
                    b.empty.visibility = View.VISIBLE
                }
            } catch (e: ApiException) {
                if (e.code == 401) logout() else showEmpty("Ошибка: ${e.message}")
            } catch (e: Exception) {
                showEmpty("Нет связи с сервером")
            } finally {
                setLoading(false)
            }
        }
    }

    private fun uploadBook(uri: Uri) {
        val name = queryName(uri)
        val ext = name.substringAfterLast('.', "").lowercase()
        if (ext !in setOf("epub", "fb2", "pdf")) {
            Toast.makeText(this, "Поддерживаются EPUB, FB2, PDF", Toast.LENGTH_LONG).show()
            return
        }
        val bytes = try {
            contentResolver.openInputStream(uri)?.use { it.readBytes() }
        } catch (e: Exception) { null }
        if (bytes == null || bytes.isEmpty()) {
            Toast.makeText(this, "Не удалось прочитать файл", Toast.LENGTH_LONG).show()
            return
        }
        val mime = contentResolver.getType(uri)
        Toast.makeText(this, "Загрузка «$name»…", Toast.LENGTH_SHORT).show()
        setLoading(true)
        lifecycleScope.launch {
            try {
                val title = Api.uploadBook(
                    Prefs.baseUrl(this@LibraryActivity),
                    Prefs.token(this@LibraryActivity),
                    name, bytes, mime,
                )
                Toast.makeText(this@LibraryActivity, "Загружено: $title", Toast.LENGTH_LONG).show()
                if (mode == "books") { currentTab = 2; b.tabs.getTabAt(2)?.select(); load() }
            } catch (e: ApiException) {
                Toast.makeText(this@LibraryActivity, "Ошибка: ${e.message}", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(this@LibraryActivity, "Не удалось загрузить", Toast.LENGTH_LONG).show()
            } finally {
                setLoading(false)
            }
        }
    }

    private fun queryName(uri: Uri): String {
        var name = "book"
        if (uri.scheme == "content") {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) name = c.getString(idx) ?: name
            }
        } else {
            uri.lastPathSegment?.let { name = it }
        }
        return name
    }

    private fun plural(n: Int): String {
        val mod10 = n % 10
        val mod100 = n % 100
        val word = when {
            mod10 == 1 && mod100 != 11 -> "книга"
            mod10 in 2..4 && mod100 !in 12..14 -> "книги"
            else -> "книг"
        }
        return "$n $word"
    }

    private fun onRowClick(row: Row) {
        when (row.type) {
            "shelf" -> startActivity(
                Intent(this, BookListActivity::class.java)
                    .putExtra("title", row.title).putExtra("shelf_id", row.id)
            )
            "author" -> startActivity(
                Intent(this, BookListActivity::class.java)
                    .putExtra("title", row.title).putExtra("author_id", row.id)
            )
            else -> {
                val path = if (row.type == "book") "/api/books/${row.id}/text"
                else "/api/articles/${row.id}/text"
                startActivity(
                    Intent(this, ReaderActivity::class.java)
                        .putExtra("path", path)
                        .putExtra("resourceKey", "${row.type}:${row.id}")
                )
            }
        }
    }

    private fun showEmpty(msg: String) {
        b.empty.text = msg
        b.empty.visibility = View.VISIBLE
    }

    private fun setLoading(loading: Boolean) {
        b.progress.visibility = if (loading) View.VISIBLE else View.GONE
    }

    private fun logout() {
        Prefs.logout(this)
        startActivity(Intent(this, LoginActivity::class.java))
        finish()
    }
}

data class Row(
    val type: String,
    val id: Int,
    val title: String,
    val subtitle: String,
    val badge: String,
)

class RowAdapter(private val onClick: (Row) -> Unit) :
    RecyclerView.Adapter<RowAdapter.VH>() {

    private val items = ArrayList<Row>()

    fun submit(rows: List<Row>) {
        items.clear()
        items.addAll(rows)
        notifyDataSetChanged()
    }

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val badge: TextView = v.findViewById(R.id.badge)
        val title: TextView = v.findViewById(R.id.title)
        val subtitle: TextView = v.findViewById(R.id.subtitle)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_book, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val r = items[position]
        holder.badge.text = r.badge
        holder.title.text = r.title
        holder.subtitle.text = r.subtitle
        holder.itemView.setOnClickListener { onClick(r) }
    }

    override fun getItemCount() = items.size
}
