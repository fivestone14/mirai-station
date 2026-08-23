package com.mirai.mobile

import android.annotation.SuppressLint
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.WindowManager
import android.webkit.HttpAuthHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * The whole app.
 *
 * It owns three things — the credential, the window, and the gesture — and not
 * one line of what is on screen. That lives on the mini, which is the point: a
 * change to the glance is a file edit there, not a rebuild, a re-sign, a
 * re-download and a re-install here.
 *
 * ON THE CREDENTIAL. WebView.loadUrl(url, headers) attaches headers to the
 * FIRST request only — not to glance.js, not to any fetch() the page makes for
 * /api/sndk/payload. A shell built that way loads the page and then watches
 * every call inside it come back 401, which looks like a broken station rather
 * than a broken shell. onReceivedHttpAuthRequest is the right hook: WebView
 * raises it for every request that draws a challenge, subresources and XHR
 * included, and remembers the answer for the rest of the session.
 *
 * The handler answers for ONE host and cancels for every other, so a redirect
 * cannot walk the password off the station and onto somebody else's server.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout

    private val stationHost: String? = Uri.parse(BuildConfig.STATION_URL).host

    private val glanceUrl: String =
        BuildConfig.STATION_URL + "/m?user=" + Uri.encode(BuildConfig.PAYLOAD_USER)

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // A price you are glancing at must not time the screen out mid-glance.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.statusBarColor = GROUND
        window.navigationBarColor = GROUND

        web = WebView(this).apply {
            setBackgroundColor(GROUND)          // no white flash before the page paints
            settings.apply {
                javaScriptEnabled = true        // the glance is drawn in JS
                domStorageEnabled = true
                cacheMode = WebSettings.LOAD_DEFAULT
                allowFileAccess = false         // nothing local to read, so nothing local is reachable
                allowContentAccess = false
                setSupportZoom(false)           // a fixed layout; pinch only breaks it
                builtInZoomControls = false
                displayZoomControls = false
                mediaPlaybackRequiresUserGesture = true
            }
            webViewClient = StationClient()
            isVerticalScrollBarEnabled = false
            overScrollMode = WebView.OVER_SCROLL_NEVER
        }

        refresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors(JADE)
            setProgressBackgroundColorSchemeColor(SURFACE)
            addView(web)
            setOnRefreshListener { web.reload() }
        }

        setContentView(refresh)
        if (savedInstanceState == null) web.loadUrl(glanceUrl)
    }

    /** Coming back to the app is a request for a current reading, not the one
     *  that was on screen when it was put down. */
    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        refresh.removeAllViews()
        web.destroy()
        super.onDestroy()
    }

    private inner class StationClient : WebViewClient() {

        override fun onReceivedHttpAuthRequest(
            view: WebView?, handler: HttpAuthHandler?, host: String?, realm: String?
        ) {
            if (handler == null) return
            if (host != null && stationHost != null && host.equals(stationHost, true)) {
                handler.proceed(BuildConfig.STATION_USER, BuildConfig.STATION_PASS)
            } else {
                // Never answer a challenge from a host we did not mean to talk to.
                handler.cancel()
            }
        }

        /** Keep the shell on the station. Anything else is not this app's job
         *  and must not inherit its credential. */
        override fun shouldOverrideUrlLoading(
            view: WebView?, request: WebResourceRequest?
        ): Boolean {
            val host = request?.url?.host ?: return true
            return !(stationHost != null && host.equals(stationHost, true))
        }

        override fun onReceivedError(
            view: WebView?, request: WebResourceRequest?, error: WebResourceError?
        ) {
            // Only the main document earns the error page — a failed subresource
            // must not blank a screen that is otherwise readable.
            if (request?.isForMainFrame != true) return
            view?.loadDataWithBaseURL(null, OFFLINE_HTML, "text/html", "utf-8", null)
        }

        override fun onPageFinished(view: WebView?, url: String?) {
            refresh.isRefreshing = false
        }
    }

    private companion object {
        const val GROUND = 0xFF0A0E14.toInt()      // the station's own background
        const val SURFACE = 0xFF121924.toInt()
        const val JADE = 0xFF43C59E.toInt()

        /** Deliberately plain: it says which of the two things is wrong, because
         *  from a phone those are the only two worth telling apart. */
        val OFFLINE_HTML = """
            <html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              html,body{margin:0;height:100%;background:#0a0e14;color:#8494a0;
                font:15px/1.5 -apple-system,system-ui,sans-serif;
                display:flex;align-items:center;justify-content:center;text-align:center}
              div{padding:0 28px;max-width:22em}
              b{color:#dbe4e0;font-weight:600;display:block;margin-bottom:8px}
              span{color:#5b6a76;font-size:13px;display:block;margin-top:14px}
            </style></head><body><div>
              <b>Can't reach the station</b>
              Either this phone has no connection, or the Mac mini is not answering.
              <span>Pull down to try again.</span>
            </div></body></html>
        """.trimIndent()
    }
}
