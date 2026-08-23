# mirai-mobile

An Android shell for the SNDK glance at `/m` on the view station.

It is a browser window with the station's password saved in it and no address
bar — about 2 MB, and it draws nothing itself. The screen lives on the Mac mini
and is fetched fresh each time the app opens, so **changing the glance is a file
edit on the mini, not a rebuild here**. The shell only changes when the shell
changes, which is rare.

    phone ──https──▶ Cloudflare tunnel ──▶ Caddy :8790 ──▶ station :8787 ──▶ /m

Same path the browser takes, so it works on cell and abroad without a VPN.

## What it holds

Three things, and nothing else: the credential, the window, and the pull-to-
refresh gesture. One permission — `INTERNET`. Backups and device-to-device
transfer are switched off, because a restored copy on another phone would carry
the station password with it.

## Building

The toolchain, once (about 1 GB — not the 8 GB Android Studio):

    brew install --cask temurin@17 android-commandlinetools
    brew install gradle
    sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"

There is no `gradlew` in the tree: the wrapper ships as a binary jar, and a
binary in a public repo is a thing nobody reads. Run `gradle wrapper` once if
you want one.

Your credentials, once:

    cp local.properties.example local.properties
    $EDITOR local.properties

Then:

    gradle assembleDebug        # works immediately, installs as "Mirai (debug)"
    gradle assembleRelease      # needs a signing key — see below

A missing value in `local.properties` fails the build by name. That is on
purpose: an empty default produces an APK that installs, opens, and shows an
auth error you then debug on a phone.

## A signing key

`assembleDebug` needs none and is the fastest way to a working app. A release
build wants one, and wants the same one forever — Android refuses to update an
app signed with a different key, so losing it means uninstall-and-reinstall.

    keytool -genkeypair -v -keystore mirai-release.jks -alias mirai \
            -keyalg RSA -keysize 4096 -validity 10000

Then `keystore.properties` next to `build.gradle.kts` (untracked):

    storeFile=mirai-release.jks
    storePassword=...
    keyAlias=mirai
    keyPassword=...

Put the `.jks` and its passwords in the password manager.

## Getting it onto the phone

Over the station, from anywhere:

    cp app/build/outputs/apk/release/app-release.apk \
       ../runtime/viewstation/static/m/mirai.apk

Then on the phone, open `<station>/m/mirai.apk` in Chrome and enter the station
password. Android blocks the install the first time: **Settings → Apps → Chrome
→ Install unknown apps → Allow**. That is per-app, so it trusts Chrome and
nothing else. Play Protect will warn that it does not recognise the developer —
expected for a self-signed app.

Or over a cable, at the desk:

    adb install app/build/outputs/apk/release/app-release.apk

No download, no Play Protect prompt, no unknown-sources permission.

> The APK on the station is the one file there that contains the station
> password. It is gitignored, and it is worth giving it an unguessable name
> rather than `mirai.apk` if the front door is ever opened wider.

## Nothing secret is in this directory

`local.properties` and `keystore.properties` are untracked, and the repo's
`.github/scripts/scrub.sh` refuses them in CI. `local.properties` is refused
twice over: Gradle writes `sdk.dir` into it, which is an absolute path under
the home directory, and the scrub has always rejected those for naming the
account.

Every URL comes from `local.properties`. There is no hostname in the source,
which is hygiene rather than protection — the station's certificate is
published in public transparency logs, so the name is discoverable regardless.
The password wall is what protects the station; this just keeps the
configuration in one place.
