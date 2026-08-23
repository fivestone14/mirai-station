import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/* Every secret this app holds arrives here and nowhere else.
 *
 * local.properties is untracked (and refused by the repo's scrub twice over),
 * so the credential exists on the building machine and inside the built APK —
 * never in git, never in a diff, never in a screenshot of the source.
 *
 * A missing value FAILS THE BUILD by name. The tempting alternative — default
 * to "" and let it through — ships an APK that installs, opens, and shows an
 * auth failure the user then debugs on a phone. Silence is the expensive
 * option; this is the cheap one. */
fun secret(key: String): String {
    val f = rootProject.file("local.properties")
    if (!f.exists()) throw GradleException(
        "mirai-mobile: local.properties is missing.\n" +
        "  cp local.properties.example local.properties   and fill it in."
    )
    val props = Properties().apply { f.inputStream().use { load(it) } }
    val v = props.getProperty(key)?.trim()
    if (v.isNullOrEmpty()) throw GradleException(
        "mirai-mobile: $key is not set in local.properties.\n" +
        "  See local.properties.example for what it wants."
    )
    return v
}

/* Release signing is configured only when a key exists. A release build with no
 * key produces an APK Android refuses to install, which is a confusing way to
 * discover you have not made one — so this says the quiet part with the exact
 * command. Debug builds need none of this and work from a clean checkout. */
val keystoreFile = rootProject.file("keystore.properties")
val hasKeystore = keystoreFile.exists()
val keystoreProps = Properties().apply {
    if (hasKeystore) keystoreFile.inputStream().use { load(it) }
}

android {
    namespace = "com.mirai.mobile"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mirai.mobile"
        minSdk = 26            // Android 8 — the first with per-app install permission
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        buildConfigField("String", "STATION_URL", "\"${secret("STATION_URL").trimEnd('/')}\"")
        buildConfigField("String", "STATION_USER", "\"${secret("STATION_USER")}\"")
        buildConfigField("String", "STATION_PASS", "\"${secret("STATION_PASS")}\"")
        buildConfigField("String", "PAYLOAD_USER", "\"${secret("STATION_PAYLOAD_USER")}\"")
    }

    signingConfigs {
        if (hasKeystore) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false          // a WebView shell has nothing to shrink
            if (hasKeystore) signingConfig = signingConfigs.getByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"   // installs alongside a release build
        }
    }

    buildFeatures {
        buildConfig = true
        viewBinding = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

/* A release build with no key is a build whose output cannot be installed.
 * Fail at configuration time with the command that fixes it. */
gradle.taskGraph.whenReady {
    if (!hasKeystore && allTasks.any { it.name.contains("Release", ignoreCase = true) }) {
        throw GradleException(
            "mirai-mobile: no keystore.properties, so a release build would be unsigned " +
            "and Android would refuse to install it.\n\n" +
            "  Make one key, once:\n" +
            "    keytool -genkeypair -v -keystore mirai-release.jks -alias mirai \\\n" +
            "            -keyalg RSA -keysize 4096 -validity 10000\n\n" +
            "  Then write keystore.properties (untracked) next to build.gradle.kts:\n" +
            "    storeFile=mirai-release.jks\n" +
            "    storePassword=...\n" +
            "    keyAlias=mirai\n" +
            "    keyPassword=...\n\n" +
            "  Keep the .jks and its passwords in the password manager. Android will not\n" +
            "  update an app signed with a different key — losing it means uninstalling.\n\n" +
            "  To get going without one: gradle assembleDebug"
        )
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
