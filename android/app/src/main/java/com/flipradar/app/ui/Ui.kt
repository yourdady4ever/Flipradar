package com.flipradar.app.ui

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.text.method.DigitsKeyListener
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import com.flipradar.app.R

/*
 * Programmatic view helpers.  The app uses framework widgets only (no AndroidX), built in code so
 * screens stay compact and need no layout inflation.
 */

const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
const val WRAP = ViewGroup.LayoutParams.WRAP_CONTENT

fun Context.dp(value: Int): Int = (value * resources.displayMetrics.density + 0.5f).toInt()

fun Context.themeColor(attr: Int): Int {
    val ta = obtainStyledAttributes(intArrayOf(attr))
    try {
        return ta.getColor(0, Color.GRAY)
    } finally {
        ta.recycle()
    }
}

fun Context.mutedColor(): Int = themeColor(android.R.attr.textColorSecondary)

fun lp(width: Int = MATCH, height: Int = WRAP, weight: Float = 0f): LinearLayout.LayoutParams =
    LinearLayout.LayoutParams(width, height, weight)

fun Context.vbox(paddingDp: Int = 0): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    val p = dp(paddingDp)
    setPadding(p, p, p, p)
}

fun Context.hbox(paddingDp: Int = 0): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.HORIZONTAL
    gravity = Gravity.CENTER_VERTICAL
    val p = dp(paddingDp)
    setPadding(p, p, p, p)
}

fun Context.text(value: CharSequence?, sizeSp: Float = 14f, bold: Boolean = false, color: Int? = null): TextView =
    TextView(this).apply {
        text = value ?: ""
        textSize = sizeSp
        if (bold) setTypeface(typeface, Typeface.BOLD)
        if (color != null) setTextColor(color)
    }

fun Context.muted(value: CharSequence?, sizeSp: Float = 13f): TextView = text(value, sizeSp, color = mutedColor())

fun Context.heading(value: CharSequence?): TextView = text(value, 18f, bold = true).apply {
    setPadding(0, dp(4), 0, dp(4))
}

fun Context.sectionHeader(value: CharSequence?): TextView = text(value, 14f, bold = true, color = getColor(R.color.brand_primary_text)).apply {
    setPadding(0, dp(16), 0, dp(4))
    isAllCaps = true
}

fun Context.button(label: CharSequence, onClick: () -> Unit): Button = Button(this).apply {
    text = label
    isAllCaps = false
    setOnClickListener { onClick() }
}

fun Context.flatButton(label: CharSequence, onClick: () -> Unit): Button =
    Button(this, null, android.R.attr.borderlessButtonStyle).apply {
        text = label
        isAllCaps = false
        setOnClickListener { onClick() }
    }

fun Context.input(hint: CharSequence, type: Int = InputType.TYPE_CLASS_TEXT, initial: String? = null): EditText =
    EditText(this).apply {
        this.hint = hint
        // setInputType alone makes the field single-line (no MULTI_LINE flag).  Do NOT call
        // setSingleLine(true) here: it replaces PasswordTransformationMethod and would reveal tokens.
        inputType = type
        if (initial != null) setText(initial)
    }

/** Numeric keyboard that still accepts the "," grouping separator and "$" (parsed by Money). */
fun Context.moneyInput(hint: CharSequence, initial: String? = null): EditText = EditText(this).apply {
    this.hint = hint
    setSingleLine(true)
    keyListener = DigitsKeyListener.getInstance("0123456789.,$")
    setRawInputType(InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL)
    if (initial != null) setText(initial)
}

fun Context.decimalInput(hint: CharSequence, initial: String? = null): EditText =
    input(hint, InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL, initial)

fun Context.intInput(hint: CharSequence, initial: String? = null): EditText =
    input(hint, InputType.TYPE_CLASS_NUMBER, initial)

/** A hint label above an input, as a vertical pair. */
fun Context.labeled(label: CharSequence, field: View): LinearLayout = vbox().apply {
    addView(muted(label, 12f), lp())
    addView(field, lp())
    setPadding(0, dp(4), 0, dp(4))
}

fun Context.checkbox(label: CharSequence, checked: Boolean = false): CheckBox = CheckBox(this).apply {
    text = label
    isChecked = checked
}

fun Context.spinner(labels: List<String>, selected: Int = 0, onSelected: ((Int) -> Unit)? = null): Spinner =
    Spinner(this).apply {
        val adapter = ArrayAdapter(this@spinner, android.R.layout.simple_spinner_item, labels)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        this.adapter = adapter
        if (selected in labels.indices) setSelection(selected, false)
        if (onSelected != null) {
            // Spinner reports the initial selection after layout; only forward real changes.
            var last = if (selected in labels.indices) selected else 0
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (position == last) return
                    last = position
                    onSelected(position)
                }
                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
    }

/**
 * Switch whose change is applied remotely; [onChange] receives a `revert` callback that flips the
 * switch back WITHOUT re-triggering [onChange] (used when the server call fails).
 */
fun Context.remoteSwitch(initial: Boolean, description: CharSequence, onChange: (checked: Boolean, revert: () -> Unit) -> Unit): Switch =
    Switch(this).apply {
        isChecked = initial
        contentDescription = description
        var reverting = false
        setOnCheckedChangeListener { sw, checked ->
            if (reverting) return@setOnCheckedChangeListener
            onChange(checked) {
                reverting = true
                sw.isChecked = !checked
                reverting = false
            }
        }
    }

fun Context.roundedBackground(fill: Int, radiusDp: Int = 8, strokeColor: Int? = null): GradientDrawable =
    GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(radiusDp).toFloat()
        if (strokeColor != null) setStroke(dp(1), strokeColor)
    }

fun Context.badge(label: CharSequence, bgColor: Int): TextView = TextView(this).apply {
    text = label
    textSize = 11f
    setTypeface(typeface, Typeface.BOLD)
    setTextColor(Color.WHITE)
    background = roundedBackground(bgColor, 8)
    setPadding(dp(6), dp(2), dp(6), dp(2))
    maxLines = 1
}

/** Horizontal row of badges with spacing. */
fun Context.badgeRow(vararg badges: TextView?): LinearLayout = hbox().apply {
    for (b in badges) {
        if (b == null) continue
        addView(b, lp(WRAP, WRAP).apply { marginEnd = dp(6) })
    }
}

fun Context.card(): LinearLayout = vbox(12).apply {
    background = roundedBackground(getColor(R.color.card_background), 10, getColor(R.color.card_stroke))
}

fun Context.cardLp(): LinearLayout.LayoutParams = lp().apply { setMargins(0, dp(6), 0, dp(6)) }

fun Context.divider(): View = View(this).apply {
    setBackgroundColor(getColor(R.color.card_stroke))
    layoutParams = lp(MATCH, maxOf(1, dp(1) / 2)).apply { setMargins(0, dp(4), 0, dp(4)) }
}

/** label ............ value  (value right-aligned, optional color / bold). */
fun Context.keyValue(label: CharSequence, value: CharSequence?, valueColor: Int? = null, bold: Boolean = false): LinearLayout =
    hbox().apply {
        setPadding(0, dp(2), 0, dp(2))
        addView(text(label, 14f, bold = bold), lp(0, WRAP, 1f))
        addView(text(value ?: "—", 14f, bold = bold, color = valueColor).apply { gravity = Gravity.END }, lp(WRAP, WRAP))
    }

fun Context.scroll(child: View): ScrollView = ScrollView(this).apply {
    isFillViewport = true
    addView(child, ViewGroup.LayoutParams(MATCH, WRAP))
}

fun TextView.clampLines(lines: Int): TextView = apply {
    maxLines = lines
    ellipsize = TextUtils.TruncateAt.END
}

fun Context.profitColor(cents: Long): Int = getColor(if (cents >= 0) R.color.profit_positive else R.color.profit_negative)

fun Context.severityColor(severity: String?): Int = getColor(
    when (severity?.lowercase()) {
        "critical", "high" -> R.color.severity_critical
        "warning", "medium" -> R.color.severity_warning
        "info", "low" -> R.color.severity_info
        else -> R.color.severity_none
    },
)

fun Context.confidenceColor(confidence: String?): Int = getColor(
    when (confidence?.lowercase()) {
        "high" -> R.color.profit_badge
        "medium" -> R.color.severity_info
        else -> R.color.severity_none
    },
)

/** Generic BaseAdapter over a mutable list with a view-creation and bind step (view recycling). */
abstract class SimpleAdapter<T>(val items: MutableList<T> = ArrayList()) : BaseAdapter() {
    override fun getCount(): Int = items.size
    override fun getItem(position: Int): T = items[position]
    override fun getItemId(position: Int): Long = position.toLong()

    abstract fun createView(parent: ViewGroup): View
    abstract fun bind(view: View, item: T, position: Int)

    override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
        val v = convertView ?: createView(parent)
        bind(v, items[position], position)
        return v
    }

    fun replace(newItems: List<T>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    fun append(more: List<T>) {
        items.addAll(more)
        notifyDataSetChanged()
    }
}
