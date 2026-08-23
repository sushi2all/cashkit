# Maestro iOS smoke — status and how to run it

`smoke.yaml` walks the same path as the web gate
(`e2e/web/gate.spec.ts`): auth → book → mutation turn → apply proposal →
forecast → trace → save, against the same harness.

## Status: written, reviewed, **not executed**

The session that wrote this flow (S3) could not run it, and does not claim it
passes. What was actually true on the machine, checked rather than assumed:

| Requirement | State on the S3 machine |
|---|---|
| Xcode.app | **Absent.** `xcode-select -p` → `/Library/Developer/CommandLineTools` |
| iOS Simulator / `simctl` | **Absent.** `xcrun simctl` → "not a developer tool or in PATH" |
| `maestro` binary | **Absent.** not on `PATH`, no `~/.maestro` |
| Java 17 (Maestro's runtime) | Present |

Command Line Tools do not include the Simulator, so there is no device to
drive; and a native iOS build (`expo run:ios`) needs Xcode as well. Installing
Xcode is a multi-gigabyte, machine-level change that a session subagent should
not make unilaterally, so the flow is left ready instead of half-run.

**Do not record this clause as green until someone runs it and says so.**

## Running it, once the toolchain exists

```bash
# 1. Xcode, once, from the App Store. Then:
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
xcodebuild -runFirstLaunch
xcrun simctl list devices available          # confirm a simulator exists

# 2. Maestro.
curl -fsSL https://get.maestro.mobile.dev | bash
maestro --version

# 3. The harness the flow talks to (same one the web E2E uses).
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run python apps/client/e2e/harness/server.py --port 8099

# 4. A native development build on the simulator. The scheme `cashkit://`
#    is registered by app.json; the deep link in the flow depends on it.
cd apps/client
EXPO_PUBLIC_API_URL=http://127.0.0.1:8099/api npx expo run:ios

# 5. The flow.
maestro test maestro/smoke.yaml
```

## What this flow can and cannot prove

The gate wording asks for "one dictated turn". A simulator has no voice: it can
borrow the host microphone, but nothing in an automated run can *speak* a
deterministic sentence into it, and iOS on-device recognition is not dependable
on a simulator at all.

So the flow asserts what a simulator can honestly show — that the dictation
control is present and that its state machine behaves in both directions,
either starting to listen or refusing in the SPEC §9 way and saying why — and
then completes the turn as text. **Dictation producing the right transcript is
a device check**, on hardware, by hand, on the TestFlight build (S6). Recorded
in `DECISIONS.md` as D-MLP-48 rather than left as an implied pass.

The adapter itself is not untested: `src/voice/dictation.native.ts` requires
on-device recognition and refuses rather than falling back to a cloud
recognizer, and the web adapter's fail-closed behaviour is covered by the web
E2E.

---

## S6's pass at it (2026-08-23) — Android, and where it stopped

S3 wrote this flow and could not run it: no Xcode, no Simulator, no Maestro.
Two of those three changed; the third did not, and the run still did not
happen. Here is exactly how far it got, so the next person starts from the
right place rather than repeating it.

| Requirement | State on 2026-08-23 |
|---|---|
| `maestro` | **Present** — 2.8.0, at `~/.maestro/bin/maestro` (not on `PATH`) |
| Flow syntax | **Valid** — `maestro check-syntax` passes on `smoke.yaml` and `steps/reset.yaml` |
| Xcode.app / `simctl` / iOS Simulator | **Still absent** |
| Java 17 | Present (Oracle 17.0.1, x86_64) |
| Android SDK | Present — `~/Library/Android/sdk`, platform `android-34`, build-tools 34/35 |
| Android AVD | Present — `Medium_Phone`, system image `android-35` |
| `adb` | Works |
| **An installable app** | **No.** See below |

**The Android lane is the right one and it is one step short.** The PROMPT
names an Android APK direct-install as the beta lane, `app.json` already
declares `io.cashkit.app` for both platforms — the same `appId` this flow
targets — and the flow uses no iOS-only command, so it should run against an
emulator unchanged.

What was done: `apps/client/eas.json` was written with three profiles
(`preview` is the direct-install APK), and **`npx expo prebuild --platform
android` succeeded** — the Expo config generates a valid native project.

What stopped it: `./gradlew :app:assembleRelease` reached 100% of dependency
resolution and then failed with **`No space left on device`**. The machine had
1.8 GiB free of 460 GiB. That is not a configuration failure and there is
nothing here to fix — a React Native release build needs several gigabytes of
Gradle and Kotlin artifacts before it compiles anything.

The generated `android/` directory was deleted afterwards: Expo's continuous
native generation means it is an output, not a source, and committing it would
give the repository a second, stale copy of `app.json`.

### To finish it, on a machine with room

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export JAVA_HOME=$(/usr/libexec/java_home -v 17)
export PATH="$PATH:$HOME/.maestro/bin:$ANDROID_HOME/platform-tools"

cd apps/client
npx expo prebuild --platform android --no-install
(cd android && ./gradlew :app:assembleRelease)
# → android/app/build/outputs/apk/release/app-release.apk

$ANDROID_HOME/emulator/emulator -avd Medium_Phone -no-snapshot &
adb wait-for-device
adb install -r android/app/build/outputs/apk/release/app-release.apk

# The harness the flow talks to, on the host. The emulator reaches the host at
# 10.0.2.2, so HARNESS must be overridden.
uv run python e2e/harness/server.py --port 8099   # from the repository root
maestro test maestro/smoke.yaml -e HARNESS=http://10.0.2.2:8099
```

Or, once an Expo account exists, skip the toolchain entirely:

```bash
npx eas-cli build --platform android --profile preview
```

### Two things that will still not be proved by that run

**Dictation producing a real transcript** (D-MLP-48). An emulator has no
voice. The flow asserts the control's state machine in both directions and
completes the turn as text; a correct transcript is a hardware check, by hand.

**The mobile export share sheet** (D-MLP-94). `src/exporting/download.native.ts`
is written and typechecked and has never run. It is not in this flow — S5's
export screen is not on the S3 gate path — so add a step for it when the flow
is first extended past S3's screens.
