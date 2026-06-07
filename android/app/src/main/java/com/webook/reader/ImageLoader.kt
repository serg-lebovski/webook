package com.webook.reader

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import android.widget.ImageView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/** Лёгкая загрузка обложек по Bearer-URL с кэшем в памяти (без Glide/Coil). */
object ImageLoader {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val cache = object : LruCache<String, Bitmap>(
        (Runtime.getRuntime().maxMemory() / 8).toInt()
    ) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun load(iv: ImageView, url: String, token: String, placeholder: Int) {
        iv.setTag(R.id.tag_cover_url, url)
        cache.get(url)?.let { iv.setImageBitmap(it); return }
        iv.setImageResource(placeholder)
        scope.launch {
            try {
                val req = Request.Builder().url(url)
                    .header("Authorization", "Bearer $token").get().build()
                client.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) return@launch
                    val bytes = resp.body?.bytes() ?: return@launch
                    val bmp = decodeSampled(bytes, 320, 480) ?: return@launch
                    cache.put(url, bmp)
                    withContext(Dispatchers.Main) {
                        if (iv.getTag(R.id.tag_cover_url) == url) iv.setImageBitmap(bmp)
                    }
                }
            } catch (e: Exception) { /* оставляем placeholder */ }
        }
    }

    private fun decodeSampled(data: ByteArray, reqW: Int, reqH: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
        var sample = 1
        var (h, w) = bounds.outHeight to bounds.outWidth
        while (h / sample > reqH * 2 || w / sample > reqW * 2) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return BitmapFactory.decodeByteArray(data, 0, data.size, opts)
    }
}
