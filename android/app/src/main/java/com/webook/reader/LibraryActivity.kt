package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
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
    private var currentTab = 0          // 0=Полки, 1=Авторы, 2=Все (для режима books)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityLibraryBinding.inflate(layoutInflater)
        setContentView(b.root)

        setSupportActionBar(b.toolbar)

        val toggle = ActionBarDrawerToggle(
            this, b.drawer, b.toolbar,
            android.R.string.ok, android.R.string.cancel
        )
        b.drawer.addDrawerListener(toggle)
        toggle.syncState()

        b.navView.setCheckedItem(R.id.nav_books)
        b.navView.setNavigationItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_books -> { setMode("books"); b.drawer.closeDrawer(GravityCompat.START) }
                R.id.nav_notes -> { setMode("notes"); b.drawer.closeDrawer(GravityCompat.START) }
                R.id.nav_profile -> {
                    b.drawer.closeDrawer(GravityCompat.START)
                    startActivity(Intent(this, ProfileActivity::class.java))
                }
            }
            true
        }

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

        setMode("books")
    }

    override fun onBackPressed() {
        if (b.drawer.isDrawerOpen(GravityCompat.START)) {
            b.drawer.closeDrawer(GravityCompat.START)
        } else {
            super.onBackPressed()
        }
    }

    private fun setMode(m: String) {
        mode = m
        if (m == "books") {
            b.toolbar.title = "Книги"
            b.tabs.visibility = View.VISIBLE
        } else {
            b.toolbar.title = "Заметки"
            b.tabs.visibility = View.GONE
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
                        else -> "Книг для озвучки нет.\nЗагрузите EPUB / FB2 / PDF на сайте."
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

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Обновить").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { load(); true }
            else -> super.onOptionsItemSelected(item)
        }
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
