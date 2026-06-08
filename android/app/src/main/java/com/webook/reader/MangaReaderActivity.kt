package com.webook.reader

import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.webook.reader.databinding.ActivityMangaBinding
import kotlinx.coroutines.launch

/** Читалка манги: вертикальная прокрутка страниц с офлайн-кэшем. */
class MangaReaderActivity : AppCompatActivity() {

    private lateinit var b: ActivityMangaBinding
    private lateinit var pages: PageAdapter

    private var mangaId = 0
    private var detail: MangaDetail? = null
    private var chapterIdx = 0
    private var lastSaved = -1

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMangaBinding.inflate(layoutInflater)
        setContentView(b.root)

        mangaId = intent.getIntExtra("id", 0)
        b.toolbar.title = intent.getStringExtra("title") ?: "Манга"
        b.toolbar.setNavigationOnClickListener { finish() }

        pages = PageAdapter(Prefs.baseUrl(this), Prefs.token(this), mangaId)
        b.pages.layoutManager = LinearLayoutManager(this)
        b.pages.adapter = pages

        b.prevChapter.setOnClickListener { showChapter(chapterIdx - 1, 0) }
        b.nextChapter.setOnClickListener { showChapter(chapterIdx + 1, 0) }
        b.pages.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(rv: RecyclerView, dx: Int, dy: Int) = maybeSaveProgress()
        })

        loadDetail()
    }

    private fun loadDetail() {
        b.progress.visibility = View.VISIBLE
        lifecycleScope.launch {
            try {
                val d = Api.mangaDetail(Prefs.baseUrl(this@MangaReaderActivity),
                    Prefs.token(this@MangaReaderActivity), mangaId)
                detail = d
                b.toolbar.title = d.title
                val startIdx = d.chapters.indexOfFirst { it.id == d.currentChapterId }.coerceAtLeast(0)
                showChapter(startIdx, d.currentPage)
            } catch (e: Exception) {
                Toast.makeText(this@MangaReaderActivity, "Не удалось загрузить мангу", Toast.LENGTH_LONG).show()
                finish()
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }

    private fun showChapter(idx: Int, startPage: Int) {
        val d = detail ?: return
        if (idx < 0 || idx >= d.chapters.size) return
        chapterIdx = idx
        val ch = d.chapters[idx]
        b.chapterLabel.text = "${ch.title} · ${ch.pageCount} стр."
        b.prevChapter.isEnabled = idx > 0
        b.nextChapter.isEnabled = idx < d.chapters.size - 1
        pages.bind(ch, (0 until ch.pageCount).toList())
        b.pages.scrollToPosition(startPage.coerceIn(0, (ch.pageCount - 1).coerceAtLeast(0)))
        lastSaved = -1
        maybeSaveProgress()
    }

    private fun maybeSaveProgress() {
        val d = detail ?: return
        val ch = d.chapters.getOrNull(chapterIdx) ?: return
        val first = (b.pages.layoutManager as LinearLayoutManager).findFirstVisibleItemPosition()
        if (first < 0 || first == lastSaved) return
        lastSaved = first
        lifecycleScope.launch {
            try {
                Api.postMangaProgress(Prefs.baseUrl(this@MangaReaderActivity),
                    Prefs.token(this@MangaReaderActivity), mangaId, ch.id, first)
            } catch (e: Exception) {}
        }
    }

    override fun onPause() { super.onPause(); maybeSaveProgress() }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Скачать главу офлайн").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 2, 1, "Удалить загрузки манги").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> downloadChapter()
            2 -> {
                Downloads.deleteManga(this, mangaId)
                Toast.makeText(this, "Загрузки удалены", Toast.LENGTH_SHORT).show()
            }
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun downloadChapter() {
        val d = detail ?: return
        val ch = d.chapters.getOrNull(chapterIdx) ?: return
        val base = Prefs.baseUrl(this); val token = Prefs.token(this)
        Toast.makeText(this, "Загрузка ${ch.pageCount} стр.…", Toast.LENGTH_SHORT).show()
        lifecycleScope.launch {
            var ok = 0
            for (n in 0 until ch.pageCount) {
                val dest = Downloads.mangaPageFile(this@MangaReaderActivity, mangaId, ch.id, n)
                if (dest.exists() && dest.length() > 0) { ok++; continue }
                val done = try {
                    Api.downloadTo(base, token, "/api/manga/$mangaId/chapters/${ch.id}/pages/$n", dest)
                } catch (e: Exception) { false }
                if (done) ok++
            }
            Toast.makeText(this@MangaReaderActivity, "Скачано: $ok из ${ch.pageCount}", Toast.LENGTH_LONG).show()
        }
    }
}

class PageAdapter(
    private val base: String,
    private val token: String,
    private val mangaId: Int,
) : RecyclerView.Adapter<PageAdapter.VH>() {

    private var chapterId = 0
    private val pages = ArrayList<Int>()

    fun bind(chapter: MangaChapterItem, pageList: List<Int>) {
        chapterId = chapter.id
        pages.clear(); pages.addAll(pageList); notifyDataSetChanged()
    }

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val img: ImageView = v.findViewById(R.id.page)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_manga_page, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val n = pages[position]
        val url = Api.mangaPageUrl(base, mangaId, chapterId, n)
        val local = Downloads.mangaPageLocal(holder.itemView.context, mangaId, chapterId, n)
        ImageLoader.loadPage(holder.img, url, token, local)
    }

    override fun getItemCount() = pages.size
}
