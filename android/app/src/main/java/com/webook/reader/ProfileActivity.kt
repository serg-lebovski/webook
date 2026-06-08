package com.webook.reader

import android.Manifest
import android.app.TimePickerDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.webook.reader.databinding.ActivityProfileBinding

class ProfileActivity : AppCompatActivity() {

    private lateinit var b: ActivityProfileBinding

    private val requestNotif =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) enableReminder() else b.reminderSwitch.isChecked = false
        }

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

        b.offlineBtn.setOnClickListener {
            startActivity(Intent(this, OfflineActivity::class.java))
        }

        // --- Напоминания ---
        b.reminderSwitch.isChecked = Reminders.isEnabled(this)
        updateTimeLabel()
        b.reminderTimeBtn.visibility =
            if (Reminders.isEnabled(this)) android.view.View.VISIBLE else android.view.View.GONE

        b.reminderSwitch.setOnCheckedChangeListener { _, checked ->
            if (checked) {
                if (Build.VERSION.SDK_INT >= 33 &&
                    ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED
                ) {
                    requestNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    enableReminder()
                }
            } else {
                Reminders.disable(this)
                b.reminderTimeBtn.visibility = android.view.View.GONE
            }
        }

        b.reminderTimeBtn.setOnClickListener {
            val hour = Reminders.hour(this)
            TimePickerDialog(this, { _, h, _ ->
                Reminders.enable(this, h)
                updateTimeLabel()
            }, hour, 0, true).show()
        }
    }

    private fun enableReminder() {
        Reminders.ensureChannel(this)
        Reminders.enable(this, Reminders.hour(this))
        b.reminderTimeBtn.visibility = android.view.View.VISIBLE
        updateTimeLabel()
    }

    private fun updateTimeLabel() {
        b.reminderTimeBtn.text = "Время: %02d:00".format(Reminders.hour(this))
    }
}
