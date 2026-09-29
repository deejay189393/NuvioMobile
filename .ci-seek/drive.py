#!/usr/bin/env python3
"""Drives the app on an emulator: installs a test addon with one direct MP4 stream, starts
playback, then swipes horizontally across the player and records what the screen shows while
the finger is still down (screenshots, a screen recording) and where playback lands afterwards."""
import os, re, subprocess, sys, time
import xml.etree.ElementTree as ET

OUT = sys.argv[1]
APK = sys.argv[2]
VARIANT = sys.argv[3]
PKG = "com.nuviodebug.com"
ADDON = "raw.githubusercontent.com/deejay189393/nuviomobile/ci-addon/seek-addon/manifest.json"
META = "nuvio://meta?type=movie&id=seektest-bbb"
os.makedirs(OUT, exist_ok=True)
counter = [0]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(f"{OUT}/steps.log", "a") as f:
        f.write(line + "\n")


def adb(*args, timeout=180):
    return subprocess.run(["adb", *args], capture_output=True, timeout=timeout)


def dump():
    for _ in range(4):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        x = adb("exec-out", "cat", "/sdcard/ui.xml").stdout.decode("utf-8", "replace")
        if "<hierarchy" in x:
            return x
        time.sleep(2)
    return ""


def nodes(xml):
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for n in root.iter("node"):
        b = re.findall(r"\d+", n.get("bounds", ""))
        if len(b) != 4:
            continue
        out.append({
            "text": n.get("text", ""), "desc": n.get("content-desc", ""),
            "cls": n.get("class", ""), "clickable": n.get("clickable") == "true",
            "b": tuple(int(v) for v in b),
        })
    return out


def visible_text(ns):
    return [t for n in ns for t in (n["text"], n["desc"]) if t]


def shot(name):
    counter[0] += 1
    base = f"{OUT}/{counter[0]:02d}-{name}"
    with open(base + ".png", "wb") as f:
        f.write(adb("exec-out", "screencap", "-p").stdout)
    xml = dump()
    ns = nodes(xml)
    for _ in range(3):
        texts = visible_text(ns)
        if any("isn't responding" in t for t in texts):
            button, what = find(ns, "Wait"), "a system 'not responding' dialog"
        elif "Viewing full screen" in texts:
            button, what = find(ns, "Got it"), "the full-screen hint"
        else:
            break
        if not button:
            break
        tap(button)
        log(f"dismissed {what}")
        time.sleep(3)
        xml = dump()
        ns = nodes(xml)
    with open(base + ".xml", "w") as f:
        f.write(xml)
    log(f"shot {counter[0]:02d}-{name}: {visible_text(ns)[:60]}")
    return ns


def screenshot_only(name):
    counter[0] += 1
    path = f"{OUT}/{counter[0]:02d}-{name}.png"
    with open(path, "wb") as f:
        f.write(adb("exec-out", "screencap", "-p").stdout)
    log(f"screenshot {counter[0]:02d}-{name}")
    return path


def find(ns, pattern, exact=True):
    rx = re.compile(pattern if not exact else f"^(?:{pattern})$", re.I)
    for n in ns:
        for t in (n["text"], n["desc"]):
            if t and rx.search(t.strip()):
                return n
    return None


def tap(n, fx=0.5, fy=0.5):
    x1, y1, x2, y2 = n["b"]
    x, y = int(x1 + (x2 - x1) * fx), int(y1 + (y2 - y1) * fy)
    adb("shell", "input", "tap", str(x), str(y))
    log(f"tap {n['text'] or n['desc']!r} at {x},{y}")


def tap_first(ns, patterns):
    for p in patterns:
        n = find(ns, p)
        if n:
            tap(n)
            return p
    return None


def keyboard_shown():
    out = adb("shell", "dumpsys", "input_method").stdout.decode("utf-8", "replace")
    return "mInputShown=true" in out or "isInputViewShown=true" in out


def hide_keyboard():
    if keyboard_shown():
        adb("shell", "input", "keyevent", "4")
        time.sleep(1)
        log("keyboard hidden")


def text_nodes_between(ns, top, bottom):
    return [n for n in ns if n["clickable"] and not n["text"] and not n["desc"]
            and n["b"][1] >= top and n["b"][3] <= bottom]


def pass_profile_gate():
    for i in range(12):
        ns = shot(f"profile-{i}")
        title = find(ns, r"Who.s watching\?")
        if not title and not find(ns, "Create Profile") and not find(ns, "Add Profile"):
            return True
        if find(ns, "Create Profile"):
            if not find(ns, "Tester"):
                field = find(ns, "Profile name") or next((n for n in ns if "EditText" in n["cls"]), None)
                if field:
                    tap(field)
                    time.sleep(1)
                    adb("shell", "input", "text", "Tester")
                    time.sleep(1)
            hide_keyboard()
            ns = shot("profile-named")
            button = find(ns, "Create Profile")
            if button:
                tap(button)
            time.sleep(5)
            continue
        tester = find(ns, "Tester")
        done = find(ns, "Done")
        if tester and done:
            tap(done)
        elif tester:
            tap(tester)
        elif done and title:
            candidates = text_nodes_between(ns, title["b"][3], done["b"][1])
            log(f"add-profile candidates: {[c['b'] for c in candidates]}")
            if candidates:
                tap(candidates[0])
        elif find(ns, "Manage Profiles"):
            tap(find(ns, "Manage Profiles"))
        time.sleep(4)
    return False


def open_url(url):
    r = adb("shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", f"'{url}'", PKG)
    log(f"open {url}: {r.stdout.decode().strip()} {r.stderr.decode().strip()}")


DIMS = []


def screen_dims():
    if not DIMS:
        png = adb("exec-out", "screencap", "-p").stdout
        DIMS.extend([int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")])
    return DIMS


def device_now():
    return int(adb("shell", "date", "+%s%3N").stdout.decode().strip()) / 1000


DIAG = re.compile(r"^\s*(\d+\.\d+).*NuvioPlayerDiag.*?(state=(\w+)|isPlaying=(\w+)|firstFrame).*?positionMs=(\d+)")


def diag_events():
    out = adb("logcat", "-d", "-v", "epoch", "-s", "NuvioPlayerDiag").stdout.decode("utf-8", "replace")
    events = []
    for line in out.splitlines():
        m = DIAG.match(line)
        if m:
            t, _, state, playing, pos = m.groups()
            kind = state or (f"isPlaying={playing}" if playing else "firstFrame")
            events.append((float(t), kind, int(pos) / 1000))
    return events


def wait_for_playback(limit=120):
    start = time.time()
    while time.time() - start < limit:
        if any(kind == "isPlaying=true" for _, kind, _ in diag_events()):
            return True
        time.sleep(3)
    return False


def swipe_async(x1, y1, x2, y2, duration_ms):
    return subprocess.Popen(
        ["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def horizontal_seek(label, from_fx, to_fx):
    """Swipes across the middle of the playing video over 6 s and screenshots twice while the
    finger is still down and once after it lifts."""
    w, h = screen_dims()
    y = h // 2
    x1, x2 = int(w * from_fx), int(w * to_fx)
    time.sleep(3)
    swipe = swipe_async(x1, y, x2, y, 6000)
    log(f"swipe {label}: {x1},{y} -> {x2},{y} over 6 s (screen {w}x{h})")
    time.sleep(2.2)
    screenshot_only(f"{label}-finger-down-early")
    time.sleep(1.6)
    screenshot_only(f"{label}-finger-down-late")
    swipe.wait(timeout=30)
    screenshot_only(f"{label}-released")


def graded_seek(label, from_fx, to_fx, expected_offset_s):
    """Checks where a swipe seeks to. Playback is paused first, so the position at touch-down is
    exactly the paused position; after the finger lifts, the player logs where it seeked to."""
    w, h = screen_dims()
    y = h // 2
    time.sleep(3)
    before = device_now()
    adb("shell", "input", "keyevent", "KEYCODE_MEDIA_PAUSE")
    time.sleep(2.5)
    paused = next((e for e in diag_events() if e[0] >= before and e[1] == "isPlaying=false"), None)
    if paused is None:
        log(f"RESULT seek {label}: pause not logged -> UNKNOWN")
        adb("shell", "input", "keyevent", "KEYCODE_MEDIA_PLAY")
        return
    start = device_now()
    swipe_async(int(w * from_fx), y, int(w * to_fx), y, 1500).wait(timeout=30)
    time.sleep(4)
    moved = next((e for e in diag_events() if e[0] >= start and e[1] in ("firstFrame", "BUFFERING", "READY")), None)
    adb("shell", "input", "keyevent", "KEYCODE_MEDIA_PLAY")
    time.sleep(2)
    expected = paused[2] + expected_offset_s
    if moved is None:
        log(f"RESULT seek {label}: paused at {paused[2]:.1f}s, no seek logged after the swipe -> FAIL")
        return
    ok = abs(moved[2] - expected) <= 1.0
    log(f"RESULT seek {label}: paused at {paused[2]:.1f}s, seeked to {moved[2]:.1f}s ({moved[1]}), "
        f"expected {expected:.1f}s -> {'OK' if ok else 'FAIL'}")


def record(name, seconds, action):
    """Screen-records `action`: feedback that lasts under a second is gone before a screenshot
    of this emulator's 2400x1080 screen comes back, but a recording keeps every frame."""
    rec = subprocess.Popen(
        ["adb", "shell", "screenrecord", "--time-limit", str(seconds), "--size", "1200x540",
         "--bit-rate", "6000000", f"/sdcard/{name}.mp4"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    action()
    try:
        rec.wait(timeout=seconds + 30)
    except subprocess.TimeoutExpired:
        adb("shell", "pkill", "-INT", "screenrecord")
        rec.wait(timeout=30)
    time.sleep(1)
    adb("pull", f"/sdcard/{name}.mp4", f"{OUT}/{name}.mp4", timeout=120)
    size = os.path.getsize(f"{OUT}/{name}.mp4") if os.path.exists(f"{OUT}/{name}.mp4") else 0
    log(f"recorded {name}: {size} bytes")


def double_tap_forward():
    w, h = screen_dims()
    x, y = int(w * 0.85), h // 2

    def taps():
        before = device_now()
        # Two taps ~80 ms apart from one shell, so they land inside the double-tap window.
        subprocess.run(["adb", "shell", f"input tap {x} {y} & sleep 0.08; input tap {x} {y}; wait"], timeout=30)
        time.sleep(1.5)
        jump = next((e for e in diag_events() if e[0] >= before and e[1] in ("BUFFERING", "READY")), None)
        log(f"double tap at {x},{y}: first state change after it {jump}")

    time.sleep(4)
    record("double-tap", 6, taps)


def brightness_swipe():
    w, h = screen_dims()
    x = int(w * 0.12)

    def swipe():
        # Quick enough to count as a drag before the long-press (hold-to-speed) timeout.
        swipe_async(x, int(h * 0.72), x, int(h * 0.30), 1200).wait(timeout=30)

    time.sleep(4)
    record("brightness", 5, swipe)


def recorded_swipe():
    w, h = screen_dims()
    y = h // 2

    def swipes():
        swipe_async(int(w * 0.35), y, int(w * 0.60), y, 3500).wait(timeout=30)
        time.sleep(1.0)
        swipe_async(int(w * 0.60), y, int(w * 0.43), y, 3000).wait(timeout=30)

    time.sleep(4)
    record("swipe", 11, swipes)


def main():
    log(f"variant={VARIANT} apk={APK}")
    log(adb("install", "-r", "-g", APK, timeout=600).stdout.decode().strip())
    adb("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed")
    adb("shell", "settings", "put", "global", "hide_error_dialogs", "1")
    log("letting the emulator settle")
    time.sleep(45)
    adb("logcat", "-G", "16M")
    adb("logcat", "-c")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(20)
    shot("launch")

    for i in range(6):
        ns = shot(f"first-run-{i}")
        if tap_first(ns, ["Continue Without Account"]):
            time.sleep(5)
            break
        time.sleep(3)
    if not pass_profile_gate():
        log("RESULT: stuck at profile gate")
        return
    for i in range(4):
        ns = shot(f"after-gate-{i}")
        if not tap_first(ns, ["Not now", "No thanks", "Maybe later", "Skip", "Later", "Don't allow", "Got it"]):
            break
        time.sleep(3)

    open_url(f"stremio://{ADDON}")
    time.sleep(12)
    shot("addon-installed")
    open_url(META)
    time.sleep(12)
    ns = shot("details")
    for attempt in range(5):
        if tap_first(ns, ["Play", "Watch", "Watch now", "Streams", "Sources"]):
            break
        if tap_first(ns, ["View Details"]):
            time.sleep(10)
        else:
            adb("shell", "input", "swipe", "540", "1500", "540", "700", "400")
            time.sleep(3)
        ns = shot(f"details-{attempt}")
    time.sleep(10)
    playing = False
    for stream_name in ("HLS test stream", "MP4 test stream"):
        ns = shot(f"streams-{stream_name.split()[0].lower()}")
        stream = find(ns, stream_name, exact=False)
        if not stream:
            time.sleep(10)
            ns = shot("streams-retry")
            stream = find(ns, stream_name, exact=False)
        if not stream:
            log(f"{stream_name} not listed")
            continue
        adb("logcat", "-c")
        tap(stream)
        if wait_for_playback(90):
            log(f"RESULT: playing the {stream_name}")
            playing = True
            break
        screenshot_only("not-playing")
        log(f"{stream_name} did not start playing; going back to the stream list")
        adb("shell", "input", "keyevent", "4")
        time.sleep(8)
    if not playing:
        log("RESULT: playback did not start")
        return
    time.sleep(8)
    screenshot_only("playing")

    horizontal_seek("swipe-right", 0.40, 0.65)
    horizontal_seek("swipe-left", 0.65, 0.40)
    # A quarter of the width is 15 s on titles under 30 minutes.
    graded_seek("forward", 0.40, 0.65, 15)
    graded_seek("backward", 0.65, 0.40, -15)
    double_tap_forward()
    brightness_swipe()
    recorded_swipe()
    time.sleep(2)
    screenshot_only("final")


try:
    main()
finally:
    with open(f"{OUT}/logcat.txt", "wb") as f:
        f.write(adb("logcat", "-d", "-v", "time", timeout=300).stdout)
    log("done")
