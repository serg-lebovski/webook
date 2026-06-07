package com.webook.reader

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Сохранённые настройки: адрес сервера, токен, имя пользователя. */
object Prefs {
    private const val FILE = "webook"
    fun prefs(c: Context) = c.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun baseUrl(c: Context): String = prefs(c).getString("base_url", "") ?: ""
    fun token(c: Context): String = prefs(c).getString("token", "") ?: ""
    fun username(c: Context): String = prefs(c).getString("username", "") ?: ""

    fun save(c: Context, baseUrl: String, token: String, username: String) {
        prefs(c).edit()
            .putString("base_url", baseUrl)
            .putString("token", token)
            .putString("username", username)
            .apply()
    }

    fun logout(c: Context) {
        prefs(c).edit().remove("token").apply()
    }

    fun isLoggedIn(c: Context) = baseUrl(c).isNotEmpty() && token(c).isNotEmpty()

    fun normalizeUrl(raw: String): String {
        var u = raw.trim()
        if (u.isEmpty()) return u
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        while (u.endsWith("/")) u = u.dropLast(1)
        return u
    }
}

class ApiException(val code: Int, message: String) : Exception(message)

data class BookItem(
    val id: Int,
    val title: String,
    val author: String,
    val format: String,
    val isRead: Boolean,
)

data class ArticleItem(
    val id: Int,
    val title: String,
    val minutes: Int,
)

/** Полка или автор — узел для браузинга «как на сайте». */
data class GroupItem(
    val id: Int,
    val name: String,
    val count: Int,
)

data class TextResult(
    val id: Int,
    val title: String,
    val author: String,
    val paragraphs: List<String>,
)

/** Офлайн-кэш текста книг/статей (для озвучки и чтения без сети). */
object Offline {
    private fun dir(c: Context) = java.io.File(c.filesDir, "offline").apply { mkdirs() }
    private fun fileFor(c: Context, key: String) =
        java.io.File(dir(c), key.replace(Regex("[^A-Za-z0-9]"), "_") + ".json")

    fun save(c: Context, key: String, r: TextResult) {
        if (key.isEmpty()) return
        try {
            val arr = JSONArray()
            r.paragraphs.forEach { arr.put(it) }
            val o = JSONObject()
                .put("title", r.title).put("author", r.author).put("paragraphs", arr)
            fileFor(c, key).writeText(o.toString())
        } catch (e: Exception) { /* офлайн-кэш необязателен */ }
    }

    fun load(c: Context, key: String): TextResult? {
        return try {
            val f = fileFor(c, key)
            if (!f.exists()) return null
            val o = JSONObject(f.readText())
            val arr = o.getJSONArray("paragraphs")
            val list = ArrayList<String>(arr.length())
            for (i in 0 until arr.length()) list.add(arr.getString(i))
            TextResult(0, o.optString("title"), o.optString("author"), list)
        } catch (e: Exception) { null }
    }

    fun has(c: Context, key: String) = fileFor(c, key).exists()
}

/** Передаём абзацы между активити и сервисом без ограничения размера Intent. */
object BookHolder {
    var title: String = ""
    var paragraphs: List<String> = emptyList()
    var startIndex: Int = 0
    var resourceKey: String = ""  // напр. "book:12" — для сохранения позиции
}

object Api {
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(180, TimeUnit.SECONDS)
        .build()
    private val JSON = "application/json; charset=utf-8".toMediaType()

    suspend fun login(base: String, user: String, pass: String): String =
        withContext(Dispatchers.IO) {
            val payload = JSONObject().put("username", user).put("password", pass).toString()
            val req = Request.Builder()
                .url("$base/api/token")
                .post(payload.toRequestBody(JSON))
                .build()
            client.newCall(req).execute().use { resp ->
                val text = bodyUtf8(resp)
                if (!resp.isSuccessful) throw ApiException(resp.code, detail(text, resp.code))
                JSONObject(text).getString("access_token")
            }
        }

    /** Всегда декодируем ответ как UTF-8, не доверяя заголовку (иначе кириллица бьётся). */
    private fun bodyUtf8(resp: okhttp3.Response): String =
        resp.body?.bytes()?.toString(Charsets.UTF_8) ?: ""

    private suspend fun getBody(base: String, token: String, path: String): String =
        withContext(Dispatchers.IO) {
            val req = Request.Builder()
                .url("$base$path")
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            client.newCall(req).execute().use { resp ->
                val text = bodyUtf8(resp)
                if (!resp.isSuccessful) throw ApiException(resp.code, detail(text, resp.code))
                text
            }
        }

    suspend fun books(
        base: String, token: String,
        q: String = "", shelfId: Int? = null, authorId: Int? = null,
    ): List<BookItem> {
        val params = StringBuilder()
        if (q.isNotBlank()) params.append("&q=").append(java.net.URLEncoder.encode(q, "UTF-8"))
        if (shelfId != null) params.append("&shelf_id=").append(shelfId)
        if (authorId != null) params.append("&author_id=").append(authorId)
        val qs = if (params.isEmpty()) "" else "?" + params.substring(1)
        val arr = JSONArray(getBody(base, token, "/api/books$qs"))
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            BookItem(
                id = o.getInt("id"),
                title = o.optString("title"),
                author = o.optString("author"),
                format = o.optString("format"),
                isRead = o.optBoolean("is_read", false),
            )
        }
    }

    suspend fun shelves(base: String, token: String): List<GroupItem> =
        groups(getBody(base, token, "/api/shelves"))

    suspend fun authors(base: String, token: String): List<GroupItem> =
        groups(getBody(base, token, "/api/authors"))

    private fun groups(body: String): List<GroupItem> {
        val arr = JSONArray(body)
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            GroupItem(o.getInt("id"), o.optString("name"), o.optInt("count", 0))
        }
    }

    suspend fun articles(base: String, token: String, q: String = ""): List<ArticleItem> {
        val body = getBody(base, token, "/api/articles" + query(q))
        val arr = JSONArray(body)
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            ArticleItem(
                id = o.getInt("id"),
                title = o.optString("title"),
                minutes = o.optInt("minutes", 0),
            )
        }
    }

    suspend fun text(base: String, token: String, path: String): TextResult {
        val body = getBody(base, token, path)
        val o = JSONObject(body)
        val arr = o.getJSONArray("paragraphs")
        val paras = ArrayList<String>(arr.length())
        for (i in 0 until arr.length()) {
            val s = arr.getString(i).trim()
            if (s.isNotEmpty()) paras.add(s)
        }
        return TextResult(
            id = o.optInt("id"),
            title = o.optString("title"),
            author = o.optString("author"),
            paragraphs = paras,
        )
    }

    // --- Синхронизация позиции (доля 0..1) ---

    private suspend fun postJson(base: String, token: String, path: String, json: String): String =
        withContext(Dispatchers.IO) {
            val req = Request.Builder()
                .url("$base$path")
                .header("Authorization", "Bearer $token")
                .post(json.toRequestBody(JSON))
                .build()
            client.newCall(req).execute().use { resp ->
                val text = bodyUtf8(resp)
                if (!resp.isSuccessful) throw ApiException(resp.code, detail(text, resp.code))
                text
            }
        }

    suspend fun getProgress(base: String, token: String, path: String): Double {
        val o = JSONObject(getBody(base, token, path))
        return o.optDouble("percentage", 0.0)
    }

    suspend fun postProgress(base: String, token: String, path: String, percentage: Double) {
        postJson(base, token, path, JSONObject().put("percentage", percentage).toString())
    }

    /** Сохранить ссылку/статью (share из браузера). */
    suspend fun saveLink(base: String, token: String, url: String, title: String): String {
        val body = JSONObject().put("url", url).put("title", title).toString()
        val text = postJson(base, token, "/api/links", body)
        return JSONObject(text).optString("title", title)
    }

    /** Загрузить книгу (epub/fb2/pdf). Возвращает название созданной книги. */
    suspend fun uploadBook(
        base: String, token: String,
        filename: String, bytes: ByteArray, mime: String?,
    ): String = withContext(Dispatchers.IO) {
        val media = (mime ?: "application/octet-stream").toMediaTypeOrNull()
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, bytes.toRequestBody(media, 0, bytes.size))
            .build()
        val req = Request.Builder()
            .url("$base/api/books/upload")
            .header("Authorization", "Bearer $token")
            .post(body)
            .build()
        client.newCall(req).execute().use { resp ->
            val text = bodyUtf8(resp)
            if (!resp.isSuccessful) throw ApiException(resp.code, detail(text, resp.code))
            JSONObject(text).optString("title")
        }
    }

    private fun query(q: String) = if (q.isBlank()) "" else "?q=" + java.net.URLEncoder.encode(q, "UTF-8")

    private fun detail(text: String, code: Int): String {
        return try {
            JSONObject(text).optString("detail").ifBlank { "Ошибка $code" }
        } catch (e: Exception) {
            when (code) {
                401 -> "Неверный логин или пароль"
                429 -> "Слишком много попыток, адрес временно заблокирован"
                else -> "Ошибка $code"
            }
        }
    }
}
