package com.flipradar.app.ui

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.WindowInsets
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import com.flipradar.app.R
import com.flipradar.app.data.ApiClient
import com.flipradar.app.data.Settings
import com.flipradar.app.util.Async

/**
 * Shared screen plumbing: progress/error banner scaffold, background API calls bound to the Activity
 * lifecycle (callbacks are dropped after onDestroy), Up navigation and edge-to-edge insets.
 */
abstract class BaseActivity : Activity() {
    protected val settings: Settings by lazy { Settings(this) }
    private val tasks = ArrayList<Async.Task>()
    private var busyCount = 0
    private var progressBar: ProgressBar? = null
    private var errorView: TextView? = null

    /** Whether the action bar shows an Up arrow (false for the hub). */
    protected open val showUp: Boolean = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (showUp) actionBar?.setDisplayHomeAsUpEnabled(true)
    }

    override fun onDestroy() {
        for (t in tasks) t.cancel()
        tasks.clear()
        super.onDestroy()
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == android.R.id.home) {
            finish()
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    /** Sets the content: [progress bar][error banner][content (optionally scrollable)]. */
    protected fun setScreen(content: View, scrollable: Boolean = false) {
        val root = vbox()
        val bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true
            visibility = if (busyCount > 0) View.VISIBLE else View.INVISIBLE
        }
        progressBar = bar
        root.addView(bar, lp(MATCH, dp(4)))
        val err = text("", 14f, color = getColor(R.color.error_text)).apply {
            visibility = View.GONE
            setPadding(dp(16), dp(8), dp(16), dp(8))
            background = roundedBackground(getColor(R.color.error_background), 0)
            setOnClickListener { visibility = View.GONE }
        }
        errorView = err
        root.addView(err, lp())
        val body = if (scrollable) scroll(content) else content
        root.addView(body, lp(MATCH, 0, 1f))
        applyInsets(root)
        setContentView(root)
    }

    /**
     * Android 15+ draws apps edge-to-edge when targeting SDK 35+.  The framework action bar handles the
     * top inset; pad the content for the navigation bar, display cutout and the on-screen keyboard.
     */
    private fun applyInsets(root: LinearLayout) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            v.setPadding(bars.left, 0, bars.right, maxOf(bars.bottom, ime.bottom))
            insets
        }
    }

    protected fun setBusy(busy: Boolean) {
        busyCount = maxOf(0, busyCount + if (busy) 1 else -1)
        progressBar?.visibility = if (busyCount > 0) View.VISIBLE else View.INVISIBLE
    }

    /** API client for the configured server; opens the Setup screen (and finishes) if not configured. */
    protected fun apiOrSetup(): ApiClient? {
        val api = settings.apiClient()
        if (api == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
        }
        return api
    }

    /**
     * Runs [work] with the API client on a background thread and delivers the result on the main
     * thread.  Errors go to [onError] or, by default, the inline error banner (+ toast when [toastErrors]).
     */
    protected fun <T> call(
        work: (ApiClient) -> T,
        toastErrors: Boolean = false,
        onError: ((Throwable) -> Unit)? = null,
        onSuccess: (T) -> Unit,
    ) {
        val api = apiOrSetup() ?: return
        setBusy(true)
        var task: Async.Task? = null
        task = Async.run(
            { work(api) },
            { result ->
                setBusy(false)
                tasks.remove(task)
                onSuccess(result)
            },
            { error ->
                setBusy(false)
                tasks.remove(task)
                if (onError != null) onError(error) else showError(error, toastErrors)
            },
        )
        tasks.add(task)
    }

    protected fun showError(t: Throwable, toast: Boolean = false) {
        val msg = Labels.error(this, t)
        errorView?.let {
            it.text = msg
            it.visibility = View.VISIBLE
        }
        if (toast || errorView == null) toast(msg)
    }

    protected fun clearError() {
        errorView?.visibility = View.GONE
    }

    protected fun toast(msg: CharSequence) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
    }

    protected fun confirm(message: CharSequence, onYes: () -> Unit) {
        AlertDialog.Builder(this)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok) { _, _ -> onYes() }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    protected fun info(title: CharSequence, message: CharSequence) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    /** Opens an http(s) URL in the browser / marketplace app; ignores other schemes. */
    protected fun openUrl(url: String?) {
        if (url.isNullOrBlank()) {
            toast(getString(R.string.no_url))
            return
        }
        val uri = Uri.parse(url)
        if (uri.scheme != "http" && uri.scheme != "https") {
            toast(getString(R.string.no_url))
            return
        }
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: Exception) {
            toast(getString(R.string.no_app_for_url))
        }
    }

    /** Dialog with a custom form view; [onOk] returns true to close, false to keep it open (validation). */
    protected fun formDialog(title: CharSequence, form: View, okLabel: CharSequence, onOk: () -> Boolean) {
        val dialog = AlertDialog.Builder(this)
            .setTitle(title)
            .setView(scroll(form.apply { val p = dp(20); setPadding(p, dp(8), p, 0) }))
            .setPositiveButton(okLabel, null)
            .setNegativeButton(android.R.string.cancel, null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (onOk()) dialog.dismiss()
            }
        }
        dialog.show()
    }
}
