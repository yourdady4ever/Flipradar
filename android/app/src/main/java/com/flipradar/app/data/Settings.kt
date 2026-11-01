package com.flipradar.app.data

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * App settings stored in private SharedPreferences.
 *
 * The API token is encrypted with an AES-256/GCM key that lives in the Android Keystore (non-exportable);
 * only the ciphertext + IV are written to disk.  The token is never logged.  If the key becomes
 * unusable (e.g. data restored to another device) the token is dropped and the user re-enters it.
 */
class Settings(context: Context) {
    private val prefs: SharedPreferences = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    companion object {
        private const val PREFS = "flipradar_settings"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_TOKEN_CT = "token_ct"
        private const val KEY_TOKEN_IV = "token_iv"
        private const val KEY_POLL_MINUTES = "poll_interval_minutes"
        private const val KEY_LAST_SEEN_NOTIFICATION = "last_seen_notification_id"
        private const val KEY_NOTIF_PERMISSION_ASKED = "notification_permission_asked"

        private const val KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "flipradar_api_token_key"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS = 128

        /** Decrypted token cached in memory for this process (avoids a Keystore operation per request). */
        @Volatile
        private var cachedToken: Pair<String, String>? = null // ciphertext -> plaintext

        const val DEFAULT_POLL_MINUTES = 30
        /** 0 disables background polling; JobScheduler's minimum periodic interval is 15 minutes. */
        val POLL_CHOICES = listOf(0, 15, 30, 60, 180, 720)
    }

    var serverUrl: String?
        get() = prefs.getString(KEY_SERVER_URL, null)
        set(value) {
            prefs.edit().putString(KEY_SERVER_URL, value).apply()
        }

    var pollIntervalMinutes: Int
        get() = prefs.getInt(KEY_POLL_MINUTES, DEFAULT_POLL_MINUTES)
        set(value) {
            prefs.edit().putInt(KEY_POLL_MINUTES, value).apply()
        }

    var lastSeenNotificationId: Long
        get() = prefs.getLong(KEY_LAST_SEEN_NOTIFICATION, 0L)
        set(value) {
            prefs.edit().putLong(KEY_LAST_SEEN_NOTIFICATION, value).apply()
        }

    var notificationPermissionAsked: Boolean
        get() = prefs.getBoolean(KEY_NOTIF_PERMISSION_ASKED, false)
        set(value) {
            prefs.edit().putBoolean(KEY_NOTIF_PERMISSION_ASKED, value).apply()
        }

    val isConfigured: Boolean get() = !serverUrl.isNullOrBlank()

    val hasToken: Boolean get() = prefs.contains(KEY_TOKEN_CT)

    /** Decrypts the stored API token; null if none is stored or it can no longer be decrypted. */
    fun getToken(): String? {
        val ct = prefs.getString(KEY_TOKEN_CT, null) ?: return null
        val iv = prefs.getString(KEY_TOKEN_IV, null) ?: return null
        cachedToken?.let { (cachedCt, plain) -> if (cachedCt == ct) return plain }
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(GCM_TAG_BITS, Base64.decode(iv, Base64.NO_WRAP)))
            String(cipher.doFinal(Base64.decode(ct, Base64.NO_WRAP)), Charsets.UTF_8).also { cachedToken = ct to it }
        } catch (e: Exception) {
            // Key invalidated or data corrupted: forget the token rather than crash.
            cachedToken = null
            prefs.edit().remove(KEY_TOKEN_CT).remove(KEY_TOKEN_IV).apply()
            null
        }
    }

    /** Encrypts and stores [token]; null or empty removes it.  Returns false if encryption failed. */
    fun setToken(token: String?): Boolean {
        cachedToken = null
        if (token.isNullOrEmpty()) {
            prefs.edit().remove(KEY_TOKEN_CT).remove(KEY_TOKEN_IV).apply()
            return true
        }
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
            val ct = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
            prefs.edit()
                .putString(KEY_TOKEN_CT, Base64.encodeToString(ct, Base64.NO_WRAP))
                .putString(KEY_TOKEN_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .apply()
            true
        } catch (e: Exception) {
            false
        }
    }

    /** Returns a client for the configured server, or null when no server URL is set. */
    fun apiClient(): ApiClient? {
        val url = serverUrl?.takeIf { it.isNotBlank() } ?: return null
        return ApiClient(url, getToken())
    }

    private fun getOrCreateKey(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }
}
