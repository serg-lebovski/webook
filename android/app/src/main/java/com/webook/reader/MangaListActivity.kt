package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.webook.reader.databinding.ActivityBookListBinding
import kotlinx.coroutines.launch

/** Список манги — сеткой обложек. */
class MangaListActivity : AppCompatActivity() {

    private lateinit var b: ActivityBookListBinding
    private lateinit var adapter: MangaAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityBookListBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.toolbar.title = "Манга"
        b.toolbar.setNavigationOnClickListener { finish() }
        adapter = MangaAdapter(Prefs.baseUrl(this), Prefs.token(this)) { m ->
            startActivity(Intent(this, MangaReaderActivity::class.java)
                .putExtra("id", m.id).putExtra("title", m.title))
        }
        b.list.layoutManager = GridLayoutManager(this, 3)
        b.list.adapter = adapter
        load()
    }

    private fun load() {
        b.progress.visibility = View.VISIBLE
        b.empty.visibility = View.GONE
        lifecycleScope.launch {
            try {
                val items = Api.mangaList(Prefs.baseUrl(this@MangaListActivity), Prefs.token(this@MangaListActivity))
                adapter.submit(items)
                if (items.isEmpty()) {
                    b.empty.text = "Манги нет. Добавьте её на сайте — она появится здесь."
                    b.empty.visibility = View.VISIBLE
                }
            } catch (e: Exception) {
                b.empty.text = "Не удалось загрузить мангу"
                b.empty.visibility = View.VISIBLE
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }
}

class MangaAdapter(
    private val base: String,
    private val token: String,
    private val onClick: (MangaItem) -> Unit,
) : RecyclerView.Adapter<MangaAdapter.VH>() {

    private val items = ArrayList<MangaItem>()
    fun submit(list: List<MangaItem>) { items.clear(); items.addAll(list); notifyDataSetChanged() }

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val cover: ImageView = v.findViewById(R.id.cover)
        val badge: TextView = v.findViewById(R.id.badge)
        val title: TextView = v.findViewById(R.id.title)
        val author: TextView = v.findViewById(R.id.author)
        val stars: TextView = v.findViewById(R.id.stars)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_cover, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val m = items[position]
        holder.title.text = m.title
        holder.author.text = m.author.ifBlank { "—" }
        holder.badge.text = "ГЛ ${m.chapterCount}"
        holder.stars.text = ""
        if (m.hasCover) ImageLoader.load(holder.cover, Api.mangaCoverUrl(base, m.id), token, R.drawable.ic_launcher)
        else { holder.cover.setTag(R.id.tag_cover_url, null); holder.cover.setImageResource(R.drawable.ic_launcher) }
        holder.itemView.setOnClickListener { onClick(m) }
    }

    override fun getItemCount() = items.size
}
