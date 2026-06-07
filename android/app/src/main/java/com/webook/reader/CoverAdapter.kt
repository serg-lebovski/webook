package com.webook.reader

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

/** Сетка обложек книг (как в референсе): обложка, название, автор, формат, рейтинг. */
class CoverAdapter(
    private val base: String,
    private val token: String,
    private val onClick: (BookItem) -> Unit,
) : RecyclerView.Adapter<CoverAdapter.VH>() {

    private val items = ArrayList<BookItem>()

    fun submit(list: List<BookItem>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

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
        val b = items[position]
        holder.title.text = b.title
        holder.author.text = b.author.ifBlank { "—" }
        holder.badge.text = b.format.uppercase()
        holder.stars.text = if (b.rating > 0) "★".repeat(b.rating) else ""
        if (b.hasCover) {
            ImageLoader.load(holder.cover, Api.bookCoverUrl(base, b.id), token, R.drawable.ic_launcher)
        } else {
            holder.cover.setTag(R.id.tag_cover_url, null)
            holder.cover.setImageResource(R.drawable.ic_launcher)
        }
        holder.itemView.setOnClickListener { onClick(b) }
    }

    override fun getItemCount() = items.size
}
