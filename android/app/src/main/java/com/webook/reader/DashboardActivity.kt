package com.webook.reader

import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.GridLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.card.MaterialCardView
import com.webook.reader.databinding.ActivityDashboardBinding
import kotlinx.coroutines.launch

/** Главный экран после входа — сводка библиотеки (как дашборд сайта). */
class DashboardActivity : AppCompatActivity() {

    private lateinit var b: ActivityDashboardBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityDashboardBinding.inflate(layoutInflater)
        setContentView(b.root)
        setSupportActionBar(b.toolbar)

        b.toolbar.navigationIcon = ContextCompat.getDrawable(this, R.drawable.ic_hamburger)
        b.toolbar.setNavigationOnClickListener { b.drawer.openDrawer(GravityCompat.START) }

        b.navView.setNavigationItemSelectedListener { item ->
            b.drawer.closeDrawers()
            when (item.itemId) {
                R.id.nav_home -> {}
                R.id.nav_books -> startActivity(Intent(this, LibraryActivity::class.java))
                R.id.nav_notes -> startActivity(Intent(this, LibraryActivity::class.java).putExtra("mode", "notes"))
                R.id.nav_audio -> startActivity(Intent(this, AudioListActivity::class.java))
                R.id.nav_manga -> startActivity(Intent(this, MangaListActivity::class.java))
                R.id.nav_offline -> startActivity(Intent(this, OfflineActivity::class.java))
                R.id.nav_profile -> startActivity(Intent(this, ProfileActivity::class.java))
            }
            true
        }
        b.navView.setCheckedItem(R.id.nav_home)

        onBackPressedDispatcher.addCallback(this, object : androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (b.drawer.isDrawerOpen(GravityCompat.START)) b.drawer.closeDrawers()
                else { isEnabled = false; onBackPressedDispatcher.onBackPressed() }
            }
        })

        load()
    }

    override fun onResume() {
        super.onResume()
        b.navView.setCheckedItem(R.id.nav_home)
    }

    private fun load() {
        b.progress.visibility = View.VISIBLE
        b.error.visibility = View.GONE
        b.grid.removeAllViews()
        lifecycleScope.launch {
            try {
                val d = Api.dashboard(Prefs.baseUrl(this@DashboardActivity), Prefs.token(this@DashboardActivity))
                render(d)
            } catch (e: ApiException) {
                if (e.code == 401) logout() else showError("Ошибка: ${e.message}")
            } catch (e: Exception) {
                showError("Нет связи с сервером")
            } finally {
                b.progress.visibility = View.GONE
            }
        }
    }

    private fun render(d: DashboardStats) {
        b.greeting.text = "Привет, ${d.username.ifBlank { "читатель" }}"
        b.subtitle.text = "Ваша библиотека"
        addCard("Книги", "${d.booksRead} / ${d.booksTotal}", "прочитано", 0xFF2563EB.toInt())
        addCard("Статьи", "${d.linksRead} / ${d.linksTotal}", "прочитано", 0xFF0EA5E9.toInt())
        addCard("Аудиокниги", "${d.audiobooksTotal}", "всего", 0xFF16A34A.toInt())
        addCard("Манга", "${d.mangaTotal}", "всего", 0xFFD946EF.toInt())

        val days = when {
            d.streak % 10 == 1 && d.streak % 100 != 11 -> "день"
            else -> "дн."
        }
        b.streakText.text = "🔥 Серия чтения: ${d.streak} $days подряд"
        b.goalText.text = if (d.goal > 0)
            "Цель на ${d.year}: ${d.booksYear} из ${d.goal} книг (${d.goalPct}%)"
        else
            "В ${d.year} прочитано: ${d.booksYear} книг · ${d.linksYear} статей"
    }

    private fun addCard(title: String, big: String, sub: String, color: Int) {
        val card = MaterialCardView(this).apply {
            radius = dp(16f)
            val lp = GridLayout.LayoutParams().apply {
                width = 0
                columnSpec = GridLayout.spec(GridLayout.UNDEFINED, 1f)
                setMargins(dp(6f).toInt(), dp(6f).toInt(), dp(6f).toInt(), dp(6f).toInt())
            }
            layoutParams = lp
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(16f).toInt(), dp(18f).toInt(), dp(16f).toInt(), dp(18f).toInt())
        }
        col.addView(TextView(this).apply {
            text = big; textSize = 26f; setTextColor(color)
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        col.addView(TextView(this).apply {
            text = title; textSize = 14f
        })
        col.addView(TextView(this).apply {
            text = sub; textSize = 11f
            setTextColor(0xFF888888.toInt())
        })
        card.addView(col)
        b.grid.addView(card)
    }

    private fun dp(v: Float) = v * resources.displayMetrics.density

    private fun showError(msg: String) {
        b.error.text = msg
        b.error.visibility = View.VISIBLE
    }

    private fun logout() {
        Prefs.logout(this)
        startActivity(Intent(this, LoginActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }
}
