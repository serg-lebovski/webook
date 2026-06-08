package com.webook.reader

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import java.util.Calendar

/**
 * Локальные ежедневные напоминания «продолжить чтение».
 * Работают офлайн, без сервера: AlarmManager → ReminderReceiver → уведомление
 * по последней открытой книге (сохраняется ридером в prefs).
 */
object Reminders {
    const val CHANNEL = "reminders"
    private const val REQ = 4201
    private const val PREF_ENABLED = "reminder_enabled"
    private const val PREF_HOUR = "reminder_hour"

    fun isEnabled(c: Context) = Prefs.prefs(c).getBoolean(PREF_ENABLED, false)
    fun hour(c: Context) = Prefs.prefs(c).getInt(PREF_HOUR, 19)

    fun enable(c: Context, hour: Int) {
        Prefs.prefs(c).edit().putBoolean(PREF_ENABLED, true).putInt(PREF_HOUR, hour).apply()
        schedule(c)
    }

    fun disable(c: Context) {
        Prefs.prefs(c).edit().putBoolean(PREF_ENABLED, false).apply()
        cancel(c)
    }

    fun rescheduleIfEnabled(c: Context) {
        if (isEnabled(c)) schedule(c)
    }

    private fun pendingIntent(c: Context): PendingIntent {
        val intent = Intent(c, ReminderReceiver::class.java)
        return PendingIntent.getBroadcast(
            c, REQ, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun schedule(c: Context) {
        val am = c.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val now = Calendar.getInstance()
        val next = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour(c))
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            if (before(now)) add(Calendar.DAY_OF_MONTH, 1)
        }
        // Неточный повтор раз в сутки — не требует разрешения SCHEDULE_EXACT_ALARM
        am.setInexactRepeating(
            AlarmManager.RTC_WAKEUP,
            next.timeInMillis,
            AlarmManager.INTERVAL_DAY,
            pendingIntent(c),
        )
    }

    private fun cancel(c: Context) {
        val am = c.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(pendingIntent(c))
    }

    fun ensureChannel(c: Context) {
        val nm = c.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Напоминания", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "Ежедневное напоминание продолжить чтение" }
            )
        }
    }

    /** Сохраняется ридером, чтобы напоминание знало, что предложить открыть. */
    fun saveLastRead(c: Context, key: String, title: String, path: String) {
        Prefs.prefs(c).edit()
            .putString("last_key", key)
            .putString("last_title", title)
            .putString("last_path", path)
            .apply()
    }
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (!Reminders.isEnabled(context)) return
        Reminders.ensureChannel(context)

        val prefs = Prefs.prefs(context)
        val title = prefs.getString("last_title", "") ?: ""
        val key = prefs.getString("last_key", "") ?: ""
        val path = prefs.getString("last_path", "") ?: ""

        val open: Intent = if (key.isNotEmpty() && path.isNotEmpty()) {
            Intent(context, ReaderActivity::class.java)
                .putExtra("path", path)
                .putExtra("resourceKey", key)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        } else {
            Intent(context, LibraryActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val pi = PendingIntent.getActivity(
            context, 4202, open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val text = if (title.isNotEmpty()) "Продолжить «$title»?" else "Самое время почитать"
        val notif = NotificationCompat.Builder(context, Reminders.CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle("WeBook")
            .setContentText(text)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(4203, notif)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action == Intent.ACTION_BOOT_COMPLETED) {
            Reminders.rescheduleIfEnabled(context)
        }
    }
}
