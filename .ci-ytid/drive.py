#!/usr/bin/env python3
"""Drives the app on an emulator: installs a test addon whose only stream is a ytId,
opens the item, selects the stream and records what happens (screenshots, UI dumps, logcat)."""
import os, re, subprocess, sys, time
import xml.etree.ElementTree as ET

OUT = sys.argv[1]
APK = sys.argv[2]
FLAVOR = sys.argv[3]
PKG = "com.nuviodebug.com"
ADDON = "raw.githubusercontent.com/deejay189393/nuviomobile/ci-addon/ytid-addon/manifest.json"
META = "nuvio://meta?type=movie&id=ytidtest-dQw4w9WgXcQ"
os.makedirs(OUT, exist_ok=True)
counter = [0]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(f"{OUT}/steps.log", "a") as f:
        f.write(line + "\n")


def adb(*args, timeout=180):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    return r


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


def main():
    log(f"flavor={FLAVOR} apk={APK}")
    log(adb("install", "-r", "-g", APK, timeout=600).stdout.decode().strip())
    adb("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed")
    adb("logcat", "-c")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(20)
    shot("launch")

    # First run: continue without an account, then create and pick a local profile.
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
    ns = shot("streams")
    stream = find(ns, r"ytId only, no url", exact=False) or find(ns, "YouTube")
    if not stream:
        time.sleep(10)
        ns = shot("streams-retry")
        stream = find(ns, r"ytId only, no url", exact=False) or find(ns, "YouTube")
    if not stream:
        log("RESULT: ytId stream not listed")
        return
    log("RESULT: ytId stream listed")
    tap(stream)
    for t in (1, 3, 6, 12, 25, 45):
        time.sleep(t if t < 3 else 3 if t < 12 else 10)
        shot(f"after-select-{t}s")
    time.sleep(15)

    if FLAVOR == "playstore":
        focus = adb("shell", "dumpsys", "window", "displays").stdout.decode("utf-8", "replace")
        m = re.findall(r"mCurrentFocus=.*", focus) or re.findall(r"mFocusedApp=.*", focus)
        log(f"focus after select: {m[:2]}")
        shot("playstore-final")
        return

    seek_checks()


DIMS = []


def screen_dims():
    if not DIMS:
        png = adb("exec-out", "screencap", "-p").stdout
        DIMS.extend([int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")])
    return DIMS


def ptap(fx, fy, label):
    w, h = screen_dims()
    x, y = int(w * fx), int(h * fy)
    adb("shell", "input", "tap", str(x), str(y))
    log(f"tap {label} at {x},{y} (screen {w}x{h})")


def read_position():
    """Position in seconds from the player's 'mm:ss / mm:ss' label (visible with the controls)."""
    for t in visible_text(nodes(dump())):
        m = re.match(r"^(\d+):(\d\d)(?::(\d\d))? / ", t.strip())
        if m:
            a, b, c = m.groups()
            return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    return None


LAST = {}


def seek_step(label, fx, fy, expected_at_tap, tolerance=3):
    """Shows the controls, taps (fx, fy) straight away, then reads the position.
    expected_at_tap(previous) gives where playback should be right after the tap, from the previous
    reading advanced to the tap time; None before the first reading."""
    time.sleep(5)  # let the controls auto-hide (3.5 s while playing)
    ptap(0.5, 0.2, "screen to show controls")
    time.sleep(0.6)
    ptap(fx, fy, label)
    tapped = time.time()
    time.sleep(0.8)
    started = time.time()
    after = read_position()
    read_at = time.time()
    log(f"position read took {read_at - started:.1f}s")
    shot(f"after-{label}")
    previous = None
    if LAST:
        previous = LAST["pos"] + (tapped - LAST["at"])
    expected = expected_at_tap(previous)
    LAST.update(pos=after, at=read_at) if after is not None else LAST.clear()
    if after is None or expected is None:
        log(f"RESULT seek {label}: position after={after}, expected={expected} -> FAIL")
        return after
    expected += read_at - tapped
    ok = abs(after - expected) <= tolerance
    log(f"RESULT seek {label}: now {after}s, expected ~{expected:.0f}s -> {'OK' if ok else 'FAIL'}")
    return after


def seek_checks():
    duration = 213.0
    track_start, track_end, track_y = 0.11, 0.89, 0.75

    def at(fraction):
        return track_start + (track_end - track_start) * fraction

    time.sleep(3)
    shot("playing")
    seek_step("bar-50pct", at(0.5), track_y, lambda prev: duration * 0.5)
    seek_step("forward-10s", 0.65, 0.489, lambda prev: None if prev is None else prev + 10)
    seek_step("back-10s", 0.35, 0.489, lambda prev: None if prev is None else prev - 10)
    seek_step("bar-90pct", at(0.9), track_y, lambda prev: duration * 0.9)
    p = seek_step("bar-5pct-backwards", at(0.05), track_y, lambda prev: duration * 0.05)
    t = time.time()
    time.sleep(15)
    ptap(0.5, 0.2, "screen to show controls")
    time.sleep(0.6)
    p2 = read_position()
    elapsed = time.time() - t
    shot("keeps-playing")
    if p is not None and p2 is not None:
        ok = abs((p2 - p) - elapsed) <= 3
        log(f"RESULT keeps playing after seeks: {p}s -> {p2}s in {elapsed:.0f}s -> {'OK' if ok else 'FAIL'}")
    else:
        log(f"RESULT keeps playing after seeks: could not read position ({p}, {p2}) -> FAIL")

try:
    main()
finally:
    with open(f"{OUT}/logcat.txt", "wb") as f:
        f.write(adb("logcat", "-d", "-v", "time", timeout=300).stdout)
    log("done")
