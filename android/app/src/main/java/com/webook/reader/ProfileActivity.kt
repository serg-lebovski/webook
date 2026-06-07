package com.webook.reader

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.webook.reader.databinding.ActivityProfileBinding

class ProfileActivity : AppCompatActivity() {

    private lateinit var b: ActivityProfileBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityProfileBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.toolbar.setNavigationOnClickListener { finish() }
        b.username.text = Prefs.username(this).ifBlank { "—" }
        b.server.text = Prefs.baseUrl(this).ifBlank { "—" }

        b.logoutBtn.setOnClickListener {
            Prefs.logout(this)
            val i = Intent(this, LoginActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            startActivity(i)
            finish()
        }
    }
}
