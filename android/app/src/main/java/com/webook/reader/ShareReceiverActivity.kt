package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/** Приём ссылок из браузера (share) → сохранение как статья. */
class ShareReceiverActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val shared = intent?.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
        val subject = intent?.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
        val url = Regex("https?://\\S+").find(shared)?.value

        if (url.isNullOrEmpty()) {
            Toast.makeText(this, "Ссылка не найдена", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        if (!Prefs.isLoggedIn(this)) {
            Toast.makeText(this, "Сначала войдите в WeBook", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
            return
        }

        val title = subject.ifEmpty { shared.replace(url, "").trim() }
        Toast.makeText(this, "Сохраняю ссылку…", Toast.LENGTH_SHORT).show()
        lifecycleScope.launch {
            try {
                val saved = Api.saveLink(
                    Prefs.baseUrl(this@ShareReceiverActivity),
                    Prefs.token(this@ShareReceiverActivity),
                    url, title,
                )
                Toast.makeText(this@ShareReceiverActivity, "Сохранено: $saved", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(this@ShareReceiverActivity, "Не удалось сохранить", Toast.LENGTH_LONG).show()
            } finally {
                finish()
            }
        }
    }
}
