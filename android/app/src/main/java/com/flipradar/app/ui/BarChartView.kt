package com.flipradar.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.TypedValue
import android.view.View
import com.flipradar.app.R
import com.flipradar.app.util.Money

/**
 * Minimal Canvas bar chart for signed money values (centavos): positive bars above the zero axis,
 * negative bars below it, value labels on each bar and category labels under the plot.
 */
class BarChartView(context: Context) : View(context) {
    data class Bar(val label: String, val valueCents: Long)

    private var bars: List<Bar> = emptyList()
    private var emptyText: String = context.getString(R.string.chart_no_data)

    private val density = resources.displayMetrics.density
    private val positivePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.profit_positive) }
    private val negativePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.profit_negative) }
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.chart_axis)
        strokeWidth = density
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.chart_axis)
        textSize = sp(10f)
        textAlign = Paint.Align.CENTER
    }
    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.chart_axis)
        textSize = sp(9f)
        textAlign = Paint.Align.CENTER
    }
    private val rect = RectF()

    private fun sp(value: Float): Float = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, value, resources.displayMetrics)

    fun setData(data: List<Bar>, emptyMessage: String? = null) {
        bars = data
        if (emptyMessage != null) emptyText = emptyMessage
        contentDescription = data.joinToString("; ") { "${it.label}: ${Money.formatCents(it.valueCents)}" }
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val desired = (220 * density).toInt()
        val h = resolveSize(desired, heightMeasureSpec)
        setMeasuredDimension(w, h)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val left = paddingLeft + 4 * density
        val right = width - paddingRight - 4 * density
        val top = paddingTop + 14 * density // room for value labels above the tallest bar
        val labelArea = 18 * density
        val bottom = height - paddingBottom - labelArea - 12 * density
        if (bars.isEmpty() || right <= left || bottom <= top) {
            canvas.drawText(emptyText, width / 2f, height / 2f, labelPaint)
            return
        }
        val maxPos = bars.maxOf { it.valueCents }.coerceAtLeast(0L)
        val minNeg = bars.minOf { it.valueCents }.coerceAtMost(0L)
        val range = (maxPos - minNeg).toDouble().takeIf { it > 0 } ?: 1.0
        val plotHeight = bottom - top
        val zeroY = top + (maxPos / range * plotHeight).toFloat()
        canvas.drawLine(left, zeroY, right, zeroY, axisPaint)

        val slot = (right - left) / bars.size
        val barWidth = slot * 0.6f
        // Skip some category labels if they would overlap.
        val labelEvery = maxOf(1, Math.ceil((labelPaint.measureText("00-00") + 6 * density) / slot.toDouble()).toInt())
        bars.forEachIndexed { i, bar ->
            val cx = left + slot * i + slot / 2
            val h = (kotlin.math.abs(bar.valueCents) / range * plotHeight).toFloat()
            if (bar.valueCents >= 0) {
                rect.set(cx - barWidth / 2, zeroY - h, cx + barWidth / 2, zeroY)
                canvas.drawRect(rect, positivePaint)
                if (bar.valueCents != 0L && bars.size <= 16) canvas.drawText(Money.formatCompactCents(bar.valueCents), cx, zeroY - h - 3 * density, valuePaint)
            } else {
                rect.set(cx - barWidth / 2, zeroY, cx + barWidth / 2, zeroY + h)
                canvas.drawRect(rect, negativePaint)
                if (bars.size <= 16) canvas.drawText(Money.formatCompactCents(bar.valueCents), cx, zeroY + h + 10 * density, valuePaint)
            }
            if (i % labelEvery == 0) canvas.drawText(bar.label, cx, height - paddingBottom - 4 * density, labelPaint)
        }
    }
}
