package com.mirai.mobile

import android.annotation.SuppressLint
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.HapticFeedbackConstants
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.HttpAuthHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
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
 *
 * ON THE GESTURE (2026-09-09). SwipeRefreshLayout decides whether to steal a
 * downward drag by asking the WEBVIEW whether it can scroll up. That is the
 * right question only when the page scrolls the document. The reading thread
 * does not: it scrolls an inner element and pins the document at
 * overflow:hidden, so the WebView answered "cannot scroll up" at every position
 * in a 4300px feed and every drag became a reload that threw the reader back to
 * the top. The glance never showed it, because the glance never scrolls.
 *
 * The page is the only thing that knows, so the page is asked. It reports its
 * own scroll position through `MiraiShell.atTop()` and the refresh gesture is
 * armed from that, falling back to the WebView's own answer when a page says
 * nothing — which is exactly the old behaviour for any page that scrolls
 * normally. A page that never calls in is a page that scrolls the document.
 *
 * ON BACK. Without a handler, Android's back button finished the activity from
 * wherever you were, so leaving the thread meant leaving the app and coming
 * back to a cold start. Back now walks the WebView's own history first.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout

    /** What the page last said about its own scroll position, or null when this
     *  page has never spoken — a page that scrolls the document, in other words,
     *  for which the WebView's own answer was always correct. Reset on every
     *  navigation so a silent page cannot inherit a talkative one's answer. */
    @Volatile private var pageAtTop: Boolean? = null

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
            // Our own page, our own origin, one boolean in one direction. The
            // usual objection to addJavascriptInterface is that it hands a
            // reflective bridge to whatever HTML happens to load; here nothing
            // but the station can load at all (shouldOverrideUrlLoading), and
            // the surface is a single method that takes a Boolean and returns
            // nothing. minSdk is 26, so the pre-17 reflection hole does not
            // exist in any build this app runs on.
            addJavascriptInterface(ShellBridge(), "MiraiShell")
        }

        refresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors(JADE)
            setProgressBackgroundColorSchemeColor(SURFACE)
            addView(web)
            setOnRefreshListener { web.reload() }
            // "can the child still scroll up?" — answered by the page when the
            // page has an opinion, and by the WebView when it does not.
            setOnChildScrollUpCallback { _, _ ->
                pageAtTop?.let { !it } ?: web.canScrollVertically(-1)
            }
        }

        // Back leaves the app only when there is nothing left to go back to.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) {
                    web.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

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

    /** What the page may tell, and ask of, the shell. Two methods, no reflection
     *  surface worth the name, and nothing but the station can load here. */
    private inner class ShellBridge {
        /** Called by the page whenever its scroll position crosses the top.
         *  WebView delivers this on a binder thread, hence @Volatile above. */
        @JavascriptInterface
        fun atTop(v: Boolean) { pageAtTop = v }

        /**
         * One haptic tick, on a confirmed tap.
         *
         * NOT navigator.vibrate(). A duration-only API cannot make a tick: the
         * pulse-to-buzz perceptual boundary is around 28-30ms, while an LRA
         * needs 20-60ms just to spin up and an ERM 50-100 — the crisp region
         * sits underneath the actuator's own rise time, so the best the web API
         * can produce is a soft thud. performHapticFeedback drives a tuned
         * waveform with a braking signal, which is what makes it feel like a
         * click rather than a buzz.
         *
         * Worth carrying for a screen read outdoors: haptics is the only channel
         * here that a finger cannot cover and sunlight cannot wash out.
         *
         * No VIBRATE permission — this routes through the View's own haptic
         * feedback, which respects the system's touch-feedback setting. A phone
         * with haptics turned off stays silent, correctly.
         */
        @JavascriptInterface
        fun tick() {
            // No flags: the one-argument form honours the system's touch-feedback
            // setting, which is the behaviour we want. FLAG_IGNORE_GLOBAL_SETTING
            // exists to override a user who has turned haptics off, and a glance
            // screen has no business doing that.
            web.post { web.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY) }
        }
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

        override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
            // A new page has said nothing yet, and must not inherit the last
            // one's answer: navigating from the thread (which reports) to the
            // glance (which does not) would otherwise leave the gesture armed
            // from a stale boolean.
            pageAtTop = null
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
