package com.flipradar.app.data

import com.flipradar.app.util.ServerUrl

/**
 * The saved API token belongs to the server it was saved for and is never sent to another one, e.g.
 * after a typo in the server URL.  (Settings.changeServer drops it when the saved server changes.)
 */
object TokenBinding {
    /**
     * Token to send to [url]: the one typed in the form, otherwise the saved token, but only when [url]
     * is the server it was saved for ([savedUrl]).  [savedToken] (a Keystore decryption) is only called
     * when its result may be used.
     */
    fun tokenFor(url: String, typed: String?, savedUrl: String?, savedToken: () -> String?): String? {
        typed?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        return if (ServerUrl.sameServer(savedUrl, url)) savedToken() else null
    }
}
