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
        if not any("isn't responding" in t for t in visible_text(ns)):
            break
        wait = find(ns, "Wait")
        if not wait:
            break
        tap(wait)
        log("dismissed a system 'not responding' dialog")
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

    # Player: show controls, check position, seek via the slider and the +10s button.
    for step in range(3):
        adb("shell", "input", "tap", "900", "500")
        time.sleep(1)
        ns = shot(f"controls-{step}")
        slider = find(ns, "Playback position")
        if slider:
            tap(slider, fx=0.5)
            time.sleep(8)
            shot("after-seek-50pct")
            adb("shell", "input", "tap", "900", "500")
            time.sleep(1)
            ns = shot("controls-after-seek")
            fwd = find(ns, "Seek forward 10 seconds")
            if fwd:
                tap(fwd)
                time.sleep(1)
                shot("after-forward-10s")
            back = find(ns, "Seek backward 10 seconds")
            if back:
                time.sleep(4)
                ns = shot("before-back")
                back = find(ns, "Seek backward 10 seconds") or back
                tap(back)
                time.sleep(1)
                shot("after-back-10s")
            break
        time.sleep(3)
    time.sleep(20)
    adb("shell", "input", "tap", "900", "500")
    time.sleep(1)
    shot("final")


try:
    main()
finally:
    with open(f"{OUT}/logcat.txt", "wb") as f:
        f.write(adb("logcat", "-d", "-v", "time", timeout=300).stdout)
    log("done")
