"""
yt_ocr.py — extract live chat from yt live realtime 
"""


import os
import time
import re
import keyboard
import mss
import numpy as np
import cv2
import ctypes
import sqlite3
from difflib import SequenceMatcher
from datetime import datetime
from collections import Counter

from overlay_select import ScreenSelector
from local_ocr import ocr

# ============================================================
# DPI FIX (WINDOWS)
# ============================================================

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================

CAPTURE_SLEEP = 0.03
SIMILARITY_THRESHOLD = 0.86
FREEZE_FRAMES = 4

EDGE_DELTA_THRESHOLD = 120
ROI_START_RATIO = 0.70

# Spam control
SPAM_WINDOW = 15        # seconds
SPAM_MAX_COUNT = 3     # msgs per window

# SQL reread protection (seconds)
SQL_REREAD_BLOCK = 0.8

# ============================================================
# DEBUG
# ============================================================

def ts():
    return datetime.now().strftime("%d/%m/%Y %I:%M:%S.%f %p")[:-3]

def log(msg, display_log):
    if display_log:
        print(f"[{ts()}] {msg}")

# ============================================================
# SQLITE INIT
# ============================================================

def init_db(debug):
    if debug:
        if os.path.exists("live_chat.db"):
            os.remove("live_chat.db")
        db_path = "live_chat.db"
        print("[DB] live_chat.db RESET")
    else:
        db_path = ":memory:"
    
    db = sqlite3.connect(db_path, check_same_thread=False)
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE emitted (
            user TEXT,
            text TEXT,
            last_emit REAL
        )
    """)
    db.commit()
    return db, cur

_sql = None
_cur = None

# ============================================================
# NORMALIZATION
# ============================================================

def norm(s):
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s)

# ============================================================
# SQL — ONLY BLOCK OCR REREADS
# ============================================================

def sql_should_emit(username, text, now, display_log):
    u = norm(username)
    t = norm(text)

    row = _cur.execute(
        "SELECT last_emit FROM emitted WHERE user=? AND text=?",
        (u, t)
    ).fetchone()

    if row is None:
        log("🟥 SQL NEW", display_log)
        _cur.execute(
            "INSERT INTO emitted VALUES (?, ?, ?)",
            (u, t, now)
        )
        _sql.commit()
        return True

    last_emit = row[0]

    if now - last_emit < SQL_REREAD_BLOCK:
        log("🟥 SQL BLOCK → OCR reread", display_log)
        return False

    log("🟥 SQL PASS", display_log)
    _cur.execute(
        "UPDATE emitted SET last_emit=? WHERE user=? AND text=?",
        (now, u, t)
    )
    _sql.commit()
    return True

# ============================================================
# STATE
# ============================================================

last_frame_norm = []
sig_history = []
prev_edge_count = None

# spam memory: (user,text) -> timestamps
spam_memory = {}

# ============================================================
# HELPERS
# ============================================================

def normalize_line(u, t):
    return re.sub(r"[^a-z0-9@]", "", f"{u} {t}".lower())

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD

def detect_new(prev_norm, curr_norm, raw, display_log):
    if not prev_norm:
        log("🟩 FIRST FRAME → all new", display_log)
        return raw[:]

    new = []
    i = len(curr_norm) - 1
    j = len(prev_norm) - 1

    while i >= 0:
        if j >= 0 and similar(curr_norm[i], prev_norm[j]):
            break
        log(f"🟩 NEW → {curr_norm[i]}", display_log)
        new.append(raw[i])
        i -= 1

    return new[::-1]

def build_signature(comments):
    return [norm(u) + ":" + norm(t) for u, t in comments]

def check_frozen(sig, display_log):
    sig_history.append(sig)
    if len(sig_history) > FREEZE_FRAMES:
        sig_history.pop(0)

    if len(sig_history) == FREEZE_FRAMES and all(s == sig_history[0] for s in sig_history):
        log("🟧 FREEZE → identical OCR frames", display_log)
        return True

    return False

# ============================================================
# SPAM RATE LIMIT
# ============================================================

def spam_pass(username, text, now, display_log):
    key = (norm(username), norm(text))
    times = spam_memory.get(key, [])

    times = [x for x in times if now - x < SPAM_WINDOW]

    if len(times) >= SPAM_MAX_COUNT:
        log(f"🚫 SPAM BLOCK → @{username}: {text}", display_log)
        spam_memory[key] = times
        return False

    times.append(now)
    spam_memory[key] = times
    log(f"🟪 SPAM PASS ({len(times)}/{SPAM_MAX_COUNT}) → @{username}: {text}", display_log)
    return True

# ============================================================
# MAIN LOOP
# ============================================================

def start_ocr(callback, debug=False,display_log=False):
    global _sql, _cur, prev_edge_count, last_frame_norm

    _sql, _cur = init_db(debug)

    log("👉 Press F8 to select chat region", debug)
    selector = ScreenSelector()
    region = None

    while region is None:
        if keyboard.is_pressed("f8"):
            region = selector.select_area()
        time.sleep(0.1)

    left, top, right, bottom = map(int, region)
    log(f"📌 Region Selected: {left, top, right, bottom}", debug)

    with mss.mss() as sct:
        while True:
            grab = sct.grab({
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top
            })

            frame = np.array(grab)[:, :, :3]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            h, _ = gray.shape
            roi = gray[int(h * ROI_START_RATIO):]
            edges = cv2.Canny(roi, 50, 150)
            edge_count = np.count_nonzero(edges)

            delta = abs(edge_count - prev_edge_count) if prev_edge_count else 999
            prev_edge_count = edge_count

            if debug:
                cv2.imshow("CHAT", frame)
                cv2.imshow("EDGES", edges)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

            if delta <= EDGE_DELTA_THRESHOLD:
                # log(f"🟦 SKIP FRAME → delta={delta}", debug)
                time.sleep(CAPTURE_SLEEP)
                continue

            log(f"🟦 OCR TRIGGER → delta={delta}", display_log)

            comments = ocr(frame) or []
            log(f"🟨 OCR → {len(comments)} lines", display_log)

            if check_frozen(build_signature(comments), display_log):
                continue

            curr_norm = [normalize_line(u, t) for u, t in comments]
            new_msgs = detect_new(last_frame_norm, curr_norm, comments, display_log)
            last_frame_norm = curr_norm

            now = time.time()

            for username, text in new_msgs:
                log(f"🟩 PROCESS → @{username}: {text}", display_log)

                if not spam_pass(username, text, now, display_log):
                    continue

                if not sql_should_emit(username, text, now, display_log):
                    continue

                log(f"🟢 EMIT → @{username}: {text}", display_log)
                callback(username, text)

            time.sleep(CAPTURE_SLEEP)

    if debug:
        cv2.destroyAllWindows()

# ============================================================
# TEST
# ============================================================
# ============================================================
# SIMPLE TEST CALLBACK
# ============================================================





if __name__ == "__main__":
    def on_new_comment(user, text):
        print(f"NEW >> {user}: {text}")
    start_ocr(on_new_comment, debug=True,display_log=False)

