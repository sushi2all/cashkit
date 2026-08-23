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
