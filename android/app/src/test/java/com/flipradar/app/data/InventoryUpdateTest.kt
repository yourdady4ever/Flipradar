package com.flipradar.app.data

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for review finding android#3: the inventory "Change status" form preselected
 * "purchased" for sold items (every save then failed with 409) and could not clear the target price or
 * listed channel.
 */
class InventoryUpdateTest {
    private fun item(status: String, available: Long, target: Long?, channel: String?) = InventoryOut.parse(
        JSONObject()
            .put("id", 9).put("title", "Switch OLED").put("condition", "used")
            .put("quantity", 1).put("quantity_available", available)
            .put("unit_cost_cents", 500_000).put("extra_costs_cents", 0).put("total_cost_cents", 500_000)
            .put("status", status)
            .put("target_sale_cents", target ?: JSONObject.NULL)
            .put("listed_channel", channel ?: JSONObject.NULL),
    )

    @Test
    fun soldItemShowsSoldAsItsStatus() {
        val choices = InventoryStatus.editChoices("sold")
        assertEquals("sold", choices.first())
        assertTrue(choices.containsAll(InventoryStatus.UPDATE))
        assertEquals(InventoryStatus.UPDATE, InventoryStatus.editChoices("listed"))
    }

    @Test
    fun editingASoldItemDoesNotSendAStatus() {
        val sold = item("sold", available = 0, target = 1_100_000, channel = "mercadolibre")
        val json = InventoryUpdate.fromStatusForm(sold, "sold", 1_100_000, "facebook").toJson()
        assertFalse(json.has("status"))
        assertEquals("facebook", json.getString("listed_channel"))
        assertFalse(json.has("target_sale_cents"))
    }

    @Test
    fun emptiedFieldsAreSentAsExplicitNulls() {
        val listed = item("listed", available = 1, target = 1_100_000, channel = "mercadolibre")
        val json = InventoryUpdate.fromStatusForm(listed, "listed", null, "   ").toJson()
        assertTrue(json.has("target_sale_cents") && json.isNull("target_sale_cents"))
        assertTrue(json.has("listed_channel") && json.isNull("listed_channel"))
        assertFalse(json.has("status"))
        assertEquals(2, json.length())
        assertTrue(json.toString().contains("\"target_sale_cents\":null")) // what ApiClient sends
    }

    @Test
    fun unchangedFormSendsNothingAndEmptyFieldsStayUnset() {
        val listed = item("listed", available = 1, target = 1_100_000, channel = "mercadolibre")
        assertTrue(InventoryUpdate.fromStatusForm(listed, "listed", 1_100_000, "mercadolibre").isEmpty)
        val bare = item("in_stock", available = 1, target = null, channel = null)
        assertTrue(InventoryUpdate.fromStatusForm(bare, "in_stock", null, "").isEmpty)
    }

    @Test
    fun changedValuesAreSent() {
        val stock = item("in_stock", available = 1, target = null, channel = null)
        val json = InventoryUpdate.fromStatusForm(stock, "listed", 1_250_000, " mercadolibre ").toJson()
        assertEquals("listed", json.getString("status"))
        assertEquals(1_250_000L, json.getLong("target_sale_cents"))
        assertEquals("mercadolibre", json.getString("listed_channel"))
    }
}
