package com.flipradar.app.ui

import android.os.Bundle
import android.widget.CheckBox
import android.widget.LinearLayout
import com.flipradar.app.R
import com.flipradar.app.data.CollectResult
import com.flipradar.app.data.SourceOut
import com.flipradar.app.data.TrackedSearchIn
import com.flipradar.app.data.TrackedSearchOut
import com.flipradar.app.util.Formats

/** Tracked searches (add / run now / enable / delete) and the data sources with their status. */
class SearchesActivity : BaseActivity() {
    private lateinit var searchesBox: LinearLayout
    private lateinit var sourcesBox: LinearLayout
    private var sources: List<SourceOut> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_searches)
        val root = vbox(16)
        val header = hbox()
        header.addView(sectionHeader(getString(R.string.searches_title)), lp(0, WRAP, 1f))
        header.addView(button(getString(R.string.searches_add)) { showAddDialog() }, lp(WRAP, WRAP))
        root.addView(header, lp())
        searchesBox = vbox()
        root.addView(searchesBox, lp())
        root.addView(sectionHeader(getString(R.string.sources_title)), lp())
        root.addView(muted(getString(R.string.sources_note), 12f), lp())
        sourcesBox = vbox()
        root.addView(sourcesBox, lp())
        setScreen(root, scrollable = true)
        load()
    }

    private fun load() {
        call({ api -> api.searches() }) { renderSearches(it) }
        call({ api -> api.sources() }) {
            sources = it
            renderSources(it)
        }
    }

    private fun renderSearches(list: List<TrackedSearchOut>) {
        searchesBox.removeAllViews()
        if (list.isEmpty()) searchesBox.addView(muted(getString(R.string.searches_empty)), lp())
        for (s in list) {
            val c = card()
            val top = hbox()
            top.addView(text(s.query, 15f, bold = true), lp(0, WRAP, 1f))
            top.addView(remoteSwitch(s.enabled, getString(R.string.enabled)) { checked, revert ->
                call({ api -> api.setSearchEnabled(s.id, checked) }, onError = { e ->
                    revert()
                    showError(e, toast = true)
                }) { }
            }, lp(WRAP, WRAP))
            c.addView(top, lp())
            val sourcesText = if (s.sourceKeys.isEmpty()) getString(R.string.searches_all_sources) else s.sourceKeys.joinToString(", ")
            c.addView(muted(getString(R.string.searches_meta, sourcesText, s.intervalMinutes, s.maxResults), 12f), lp())
            c.addView(muted(getString(R.string.searches_last_run, Formats.dateTime(s.lastRunAt)), 12f), lp())
            s.categoryHint?.let { c.addView(muted(getString(R.string.searches_category, it), 12f), lp()) }
            val actions = hbox()
            actions.addView(flatButton(getString(R.string.searches_run_now)) { runSearch(s) }, lp(WRAP, WRAP))
            actions.addView(flatButton(getString(R.string.action_delete)) {
                confirm(getString(R.string.searches_delete_confirm, s.query)) {
                    call({ api -> api.deleteSearch(s.id) }, toastErrors = true) { load() }
                }
            }, lp(WRAP, WRAP))
            c.addView(actions, lp())
            searchesBox.addView(c, cardLp())
        }
    }

    private fun runSearch(s: TrackedSearchOut) {
        toast(getString(R.string.searches_running, s.query))
        call({ api -> api.runSearch(s.id) }, toastErrors = true) { r ->
            showRunResult(s.query, r)
            load()
        }
    }

    private fun showRunResult(query: String, r: CollectResult) {
        val sb = StringBuilder()
        sb.append(getString(R.string.run_result_summary, r.opportunitiesCreated, r.opportunitiesUpdated, r.notificationsCreated)).append("\n\n")
        if (r.runs.isEmpty()) sb.append(getString(R.string.run_result_no_sources))
        for (run in r.runs) {
            sb.append(getString(R.string.run_result_line, run.sourceKey ?: "?", run.status, run.itemsFetched, run.itemsNew, run.itemsUpdated))
            run.error?.takeIf { it.isNotBlank() }?.let { sb.append("\n  ").append(it) }
            sb.append("\n")
        }
        info(getString(R.string.run_result_title, query), sb.toString().trim())
    }

    private fun renderSources(list: List<SourceOut>) {
        sourcesBox.removeAllViews()
        if (list.isEmpty()) sourcesBox.addView(muted(getString(R.string.none)), lp())
        for (s in list) {
            val c = card()
            val top = hbox()
            top.addView(text(s.name, 15f, bold = true), lp(0, WRAP, 1f))
            top.addView(remoteSwitch(s.enabled, getString(R.string.enabled)) { checked, revert ->
                call({ api -> api.setSourceEnabled(s.key, checked) }, onError = { e ->
                    revert()
                    showError(e, toast = true)
                }) { }
            }, lp(WRAP, WRAP))
            c.addView(top, lp())
            c.addView(
                badgeRow(
                    badge(
                        getString(if (s.configured) R.string.source_configured else R.string.source_not_configured),
                        getColor(if (s.configured) R.color.profit_badge else R.color.severity_warning),
                    ),
                    badge(s.kind, getColor(R.color.severity_none)),
                    if (s.requiresCredentials) badge(getString(R.string.source_needs_credentials), getColor(R.color.severity_info)) else null,
                ),
                lp().apply { topMargin = dp(4) },
            )
            c.addView(muted(getString(R.string.source_meta, s.key, s.country, s.currency, s.capabilities.joinToString(", ")), 12f), lp())
            if (s.notes.isNotBlank()) c.addView(muted(s.notes, 12f), lp())
            if (s.lastRunAt != null || s.lastStatus != null) {
                c.addView(muted(getString(R.string.source_last_run, Formats.dateTime(s.lastRunAt), s.lastStatus ?: "-"), 12f), lp())
            }
            s.lastError?.takeIf { it.isNotBlank() }?.let { c.addView(text(it, 12f, color = getColor(R.color.error_text)), lp()) }
            val actions = hbox()
            actions.addView(flatButton(getString(R.string.source_test)) {
                call({ api -> api.testSource(s.key) }, toastErrors = true) { r ->
                    val status = when (r.ok) {
                        true -> getString(R.string.source_test_ok)
                        false -> getString(R.string.source_test_failed)
                        null -> getString(R.string.source_test_skipped)
                    }
                    val details = if (r.details.isEmpty()) "" else "\n\n" + Formats.anyValue(r.details)
                    info(s.name, status + "\n" + r.message + details)
                }
            }, lp(WRAP, WRAP))
            if (!s.docsUrl.isNullOrBlank()) actions.addView(flatButton(getString(R.string.source_docs)) { openUrl(s.docsUrl) }, lp(WRAP, WRAP))
            c.addView(actions, lp())
            sourcesBox.addView(c, cardLp())
        }
    }

    private fun showAddDialog() {
        val form = vbox()
        val query = input(getString(R.string.searches_query_hint))
        val category = input(getString(R.string.optional))
        val maxResults = intInput("50", "50")
        val interval = intInput("180", "180")
        form.addView(labeled(getString(R.string.searches_query), query), lp())
        form.addView(labeled(getString(R.string.searches_category_label), category), lp())
        form.addView(labeled(getString(R.string.searches_max_results), maxResults), lp())
        form.addView(labeled(getString(R.string.searches_interval), interval), lp())
        form.addView(muted(getString(R.string.searches_sources_label), 12f), lp())
        val boxes = ArrayList<Pair<String, CheckBox>>()
        val searchable = sources.filter { it.canSearch || it.capabilities.isEmpty() }
        if (searchable.isEmpty()) form.addView(muted(getString(R.string.searches_all_sources), 12f), lp())
        for (s in searchable) {
            val cb = checkbox("${s.name} (${s.key})" + if (!s.configured) " — " + getString(R.string.source_not_configured) else "")
            boxes.add(s.key to cb)
            form.addView(cb, lp())
        }
        formDialog(getString(R.string.searches_add), form, getString(R.string.action_save)) {
            val q = query.text.toString().trim()
            if (q.length < 2) {
                query.error = getString(R.string.err_query_too_short)
                return@formDialog false
            }
            val mr = optionalInt(maxResults, 1..200) ?: return@formDialog false
            val iv = optionalInt(interval, 15..10080) ?: return@formDialog false
            val body = TrackedSearchIn(
                query = q,
                categoryHint = category.text.toString().trim(),
                sourceKeys = boxes.filter { it.second.isChecked }.map { it.first },
                maxResults = mr.value ?: 50,
                intervalMinutes = iv.value ?: 180,
            )
            call({ api -> api.createSearch(body) }, toastErrors = true) {
                toast(getString(R.string.searches_added))
                load()
            }
            true
        }
    }
}
