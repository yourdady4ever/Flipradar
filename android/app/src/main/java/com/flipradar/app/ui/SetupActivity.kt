package com.flipradar.app.ui

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.data.ApiClient
import com.flipradar.app.data.ApiException
import com.flipradar.app.data.SUPPORTED_API_VERSION
import com.flipradar.app.data.Settings
import com.flipradar.app.data.TokenBinding
import com.flipradar.app.notify.PollScheduler
import com.flipradar.app.util.Async
import com.flipradar.app.util.ServerUrl
import com.flipradar.app.util.TaskRunner

/** Server URL + API token configuration with a connection test. */
class SetupActivity : BaseActivity() {
    private lateinit var urlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var warning: TextView
    private lateinit var result: TextView
    private lateinit var pollSpinner: Spinner
    private lateinit var showToken: CheckBox
    private var testTask: TaskRunner.Task? = null

    override val showUp: Boolean get() = settings.isConfigured

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.setup_title)
        val form = vbox(16)
        form.addView(muted(getString(R.string.setup_intro)), lp())

        urlInput = input(getString(R.string.setup_url_hint), InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI, settings.serverUrl)
        form.addView(labeled(getString(R.string.setup_url_label), urlInput), lp())

        warning = text("", 13f, color = getColor(R.color.severity_warning_text)).apply { visibility = View.GONE }
        form.addView(warning, lp())

        tokenInput = input(
            getString(R.string.setup_token_hint),
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD,
        )
        form.addView(labeled(getString(R.string.setup_token_label), tokenInput), lp())
        showToken = checkbox(getString(R.string.setup_show_token)).apply {
            setOnCheckedChangeListener { _, checked ->
                val sel = tokenInput.selectionEnd
                tokenInput.inputType = InputType.TYPE_CLASS_TEXT or
                    if (checked) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD
                tokenInput.setSelection(maxOf(0, minOf(sel, tokenInput.text.length)))
            }
        }
        form.addView(showToken, lp())
        if (settings.hasToken) {
            form.addView(flatButton(getString(R.string.setup_forget_token)) {
                confirm(getString(R.string.setup_forget_token_confirm)) {
                    settings.setToken(null)
                    updateTokenHint()
                    toast(getString(R.string.setup_token_forgotten))
                }
            }, lp(WRAP, WRAP))
        }

        val pollLabels = Settings.POLL_CHOICES.map { m ->
            if (m == 0) getString(R.string.setup_poll_off) else getString(R.string.setup_poll_minutes, m)
        }
        val current = Settings.POLL_CHOICES.indexOf(settings.pollIntervalMinutes).coerceAtLeast(0)
        pollSpinner = spinner(pollLabels, current)
        form.addView(labeled(getString(R.string.setup_poll_label), pollSpinner), lp())

        val buttons = hbox()
        buttons.addView(button(getString(R.string.setup_test)) { testConnection() }, lp(0, WRAP, 1f))
        buttons.addView(button(getString(R.string.setup_save)) { save() }, lp(0, WRAP, 1f))
        form.addView(buttons, lp().apply { topMargin = dp(12) })

        result = text("", 14f).apply { setTextIsSelectable(true) }
        form.addView(result, lp().apply { topMargin = dp(12) })

        form.addView(muted(getString(R.string.setup_security_note), 12f), lp().apply { topMargin = dp(16) })

        urlInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                updateWarning()
                updateTokenHint()
            }
        })
        setScreen(form, scrollable = true)
        updateWarning()
        updateTokenHint()
    }

    override fun onDestroy() {
        testTask?.cancel()
        super.onDestroy()
    }

    private fun updateWarning() {
        val raw = urlInput.text.toString()
        val r = ServerUrl.normalize(raw)
        when {
            raw.isBlank() -> warning.visibility = View.GONE
            r is ServerUrl.Result.Invalid -> {
                warning.text = problemText(r.problem)
                warning.visibility = View.VISIBLE
            }
            r is ServerUrl.Result.Valid && !r.https && !ServerUrl.isPrivateHost(r.host) -> {
                warning.text = getString(R.string.setup_warn_cleartext_public)
                warning.visibility = View.VISIBLE
            }
            else -> warning.visibility = View.GONE
        }
    }

    /** The saved token is only offered for the server it was saved for (see [TokenBinding]). */
    private fun updateTokenHint() {
        tokenInput.hint = getString(
            when {
                !settings.hasToken -> R.string.setup_token_hint
                ServerUrl.sameServer(settings.serverUrl, urlInput.text.toString()) -> R.string.setup_token_hint_saved
                else -> R.string.setup_token_hint_other_server
            },
        )
    }

    private fun problemText(p: ServerUrl.Problem): String = getString(
        when (p) {
            ServerUrl.Problem.EMPTY -> R.string.url_err_empty
            ServerUrl.Problem.BAD_SCHEME -> R.string.url_err_scheme
            ServerUrl.Problem.NO_HOST -> R.string.url_err_host
            ServerUrl.Problem.BAD_PORT -> R.string.url_err_port
            ServerUrl.Problem.HAS_QUERY -> R.string.url_err_query
            ServerUrl.Problem.HAS_CREDENTIALS -> R.string.url_err_credentials
            ServerUrl.Problem.MALFORMED -> R.string.url_err_malformed
        },
    )

    /** Token typed in the form, or, when the field is blank, the saved one if [url] is the server it was saved for. */
    private fun effectiveToken(url: String): String? =
        TokenBinding.tokenFor(url, tokenInput.text.toString(), settings.serverUrl) { settings.getToken() }

    private fun validUrl(): String? {
        return when (val r = ServerUrl.normalize(urlInput.text.toString())) {
            is ServerUrl.Result.Valid -> r.url
            is ServerUrl.Result.Invalid -> {
                result.text = problemText(r.problem)
                null
            }
        }
    }

    private fun testConnection() {
        val url = validUrl() ?: return
        val client = ApiClient(url, effectiveToken(url), connectTimeoutMs = 8_000, readTimeoutMs = 15_000)
        result.text = getString(R.string.setup_testing)
        testTask?.cancel() // a replaced test never reports back: its onCancel releases its busy slot
        setBusy(true)
        testTask = Async.run(
            {
                val health = client.health()
                val auth = try {
                    Result.success(client.authCheck())
                } catch (e: ApiException) {
                    Result.failure(e)
                }
                health to auth
            },
            { (health, auth) ->
                setBusy(false)
                val lines = ArrayList<String>()
                lines.add(getString(R.string.setup_health_ok, health.status, health.version, health.database))
                if (health.apiVersion != SUPPORTED_API_VERSION) {
                    lines.add(getString(R.string.setup_api_version_mismatch, health.apiVersion, SUPPORTED_API_VERSION))
                }
                auth.fold(
                    onSuccess = { a ->
                        lines.add(
                            when {
                                !a.authRequired -> getString(R.string.setup_auth_not_required)
                                a.ok -> getString(R.string.setup_auth_ok, a.tokenName ?: "-")
                                else -> getString(R.string.setup_auth_failed)
                            },
                        )
                    },
                    onFailure = { e -> lines.add(Labels.error(this, e)) },
                )
                result.text = lines.joinToString("\n")
            },
            { e ->
                setBusy(false)
                result.text = Labels.error(this, e)
            },
            onCancel = { setBusy(false) },
        )
    }

    private fun save() {
        val url = validUrl() ?: return
        settings.changeServer(url) // another server: drops the saved token and notification cursor
        val typed = tokenInput.text.toString().trim()
        if (typed.isNotEmpty() && !settings.setToken(typed)) {
            toast(getString(R.string.setup_token_store_failed))
            return
        }
        val newPoll = Settings.POLL_CHOICES.getOrElse(pollSpinner.selectedItemPosition) { Settings.DEFAULT_POLL_MINUTES }
        settings.pollIntervalMinutes = newPoll
        PollScheduler.schedule(this)
        toast(getString(R.string.setup_saved))
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }
}
