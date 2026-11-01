package com.flipradar.app

import android.app.Application
import com.flipradar.app.notify.Notifier

class FlipRadarApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannel(this)
    }
}
