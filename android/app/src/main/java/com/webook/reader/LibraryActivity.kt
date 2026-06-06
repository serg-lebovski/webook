package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
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
    private val adapter = RowAdapter { row -> openReader(row) }

    // tab 0 — книги, tab 1 — статьи
    private var currentTab = 0

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
                load()
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })

        load()
    }

    override fun onResume() {
        super.onResume()
        // Обновляем при возврате (вдруг что-то добавили на сайте).
        if (adapter.itemCount == 0) load()
    }

    private fun load() {
        val base = Prefs.baseUrl(this)
        val token = Prefs.token(this)
        setLoading(true)
        b.empty.visibility = View.GONE
        adapter.submit(emptyList())
        lifecycleScope.launch {
            try {
                val rows: List<Row> = if (currentTab == 0) {
                    Api.books(base, token).map {
                        Row("book", it.id, it.title, it.author.ifBlank { "—" }, it.format.uppercase())
                    }
                } else {
                    Api.articles(base, token).map {
                        val sub = if (it.minutes > 0) "${it.minutes} мин чтения" else "статья"
                        Row("article", it.id, it.title, sub, "TXT")
                    }
                }
                adapter.submit(rows)
                if (rows.isEmpty()) {
                    b.empty.text = if (currentTab == 0)
                        "Книг для озвучки нет.\nЗагрузите EPUB / FB2 / PDF на сайте."
                    else
                        "Сохранённых статей с текстом нет."
                    b.empty.visibility = View.VISIBLE
                }
            } catch (e: ApiException) {
                if (e.code == 401) {
                    logout()
                } else {
                    showEmpty("Ошибка: ${e.message}")
                }
            } catch (e: Exception) {
                showEmpty("Нет связи с сервером")
            } finally {
                setLoading(false)
            }
        }
    }

    private fun openReader(row: Row) {
        val path = if (row.type == "book") "/api/books/${row.id}/text"
        else "/api/articles/${row.id}/text"
        val intent = Intent(this, ReaderActivity::class.java)
            .putExtra("path", path)
            .putExtra("resourceKey", "${row.type}:${row.id}")
        startActivity(intent)
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
        menu.add(0, 2, 1, "Выйти").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { load(); true }
            2 -> { logout(); true }
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
