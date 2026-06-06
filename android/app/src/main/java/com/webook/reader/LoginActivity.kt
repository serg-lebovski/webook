package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.webook.reader.databinding.ActivityLoginBinding
import kotlinx.coroutines.launch

class LoginActivity : AppCompatActivity() {

    private lateinit var b: ActivityLoginBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Уже авторизованы — сразу в библиотеку.
        if (Prefs.isLoggedIn(this)) {
            startActivity(Intent(this, LibraryActivity::class.java))
            finish()
            return
        }

        b = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(b.root)

        // Подставляем ранее введённый адрес сервера (его указывают первым).
        b.serverUrl.setText(Prefs.baseUrl(this))
        b.username.setText(Prefs.username(this))

        b.loginBtn.setOnClickListener { doLogin() }
    }

    private fun doLogin() {
        val base = Prefs.normalizeUrl(b.serverUrl.text?.toString() ?: "")
        val user = b.username.text?.toString()?.trim() ?: ""
        val pass = b.password.text?.toString() ?: ""

        if (base.isEmpty()) {
            showError("Сначала укажите адрес сервера")
            return
        }
        if (user.isEmpty() || pass.isEmpty()) {
            showError("Введите логин и пароль")
            return
        }

        setLoading(true)
        lifecycleScope.launch {
            try {
                val token = Api.login(base, user, pass)
                Prefs.save(this@LoginActivity, base, token, user)
                startActivity(Intent(this@LoginActivity, LibraryActivity::class.java))
                finish()
            } catch (e: ApiException) {
                showError(e.message ?: "Ошибка входа")
            } catch (e: Exception) {
                showError("Не удалось подключиться к серверу. Проверьте адрес.")
            } finally {
                setLoading(false)
            }
        }
    }

    private fun setLoading(loading: Boolean) {
        b.progress.visibility = if (loading) View.VISIBLE else View.GONE
        b.loginBtn.isEnabled = !loading
    }

    private fun showError(msg: String) {
        b.error.text = msg
        b.error.visibility = View.VISIBLE
    }
}
