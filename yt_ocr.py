# read youtbe live chat comment and pass it to llm for ai live stream
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

CAPTURE_SLEEP = 0.05
SIMILARITY_THRESHOLD = 0.85
FREEZE_FRAMES = 5

EDGE_DELTA_THRESHOLD = 120
ROI_START_RATIO = 0.70

# Spam control (Bursts)
SPAM_WINDOW = 15        # seconds
SPAM_MAX_COUNT = 3      # msgs per window

# Deduplication / Re-read control
DEDUPE_TIMEOUT = 45.0 

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
# SQL — DATABASE LOGGING
# ============================================================

def sql_should_emit(username, text, now, display_log):
    u = norm(username)
    t = norm(text)

    row = _cur.execute(
        "SELECT last_emit FROM emitted WHERE user=? AND text=?",
        (u, t)
    ).fetchone()

    if row is None:
        _cur.execute("INSERT INTO emitted VALUES (?, ?, ?)", (u, t, now))
        _sql.commit()
        return True

    last_emit = row[0]

    if now - last_emit < DEDUPE_TIMEOUT:
        return False

    _cur.execute("UPDATE emitted SET last_emit=? WHERE user=? AND text=?", (now, u, t))
    _sql.commit()
    return True

# ============================================================
# STATE
# ============================================================

last_frame_norm = []
sig_history = []
prev_edge_count = None
spam_memory = {}
recent_signatures = {}

# ============================================================
# HELPERS
# ============================================================

def normalize_line(u, t):
    return re.sub(r"[^a-z0-9@]", "", f"{u}{t}".lower())

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD

# ============================================================
# SPAM PASS CHECK
# ============================================================

def spam_pass(username, text, now, display_log):
    key = (norm(username), norm(text))
    times = spam_memory.get(key, [])
    
    # Filter only keep timestamps within the SPAM_WINDOW
    times = [x for x in times if now - x < SPAM_WINDOW]

    if len(times) >= SPAM_MAX_COUNT:
        log(f"🚫 SPAM BLOCK → @{username}: {text}", display_log)
        spam_memory[key] = times
        return False

    times.append(now)
    spam_memory[key] = times
    return True

# ============================================================
# CORE LOGIC: DETECT NEW MESSAGES
# ============================================================

def detect_new(prev_norm, curr_norm, raw, display_log):
    if not prev_norm:
        log("⚠️ BASELINE SET → Waiting for new chat...", display_log)
        return []

    last_prev_sig = prev_norm[-1]
    match_index = -1
    
    for i in range(len(curr_norm) - 1, -1, -1):
        if similar(curr_norm[i], last_prev_sig):
            context_match = True
            if i > 0 and len(prev_norm) > 1:
                if not similar(curr_norm[i-1], prev_norm[-2]):
                    context_match = False
            
            if context_match:
                match_index = i
                break
    
    if match_index != -1:
        return raw[match_index + 1:]

    if len(prev_norm) > 1:
        second_last = prev_norm[-2]
        for i in range(len(curr_norm) - 1, -1, -1):
            if similar(curr_norm[i], second_last):
                return raw[i + 2:]

    log("⚠️ SYNC LOST → Resetting baseline", display_log)
    return []

def build_signature(comments):
    return [norm(u) + ":" + norm(t) for u, t in comments]

def check_frozen(sig, display_log):
    sig_history.append(sig)
    if len(sig_history) > FREEZE_FRAMES:
        sig_history.pop(0)

    if len(sig_history) == FREEZE_FRAMES and all(s == sig_history[0] for s in sig_history):
        return True
    return False

# ============================================================
# MAIN LOOP
# ============================================================

def start_ocr(callback, debug=False, display_log=False):
    global _sql, _cur, prev_edge_count, last_frame_norm, recent_signatures, spam_memory

    _sql, _cur = init_db(debug)
    selector = ScreenSelector()
    
    # 1. Initial Selection
    log("👉 Press F8 to select chat region", True)
    region = None
    while region is None:
        if keyboard.is_pressed("f8"):
            region = selector.select_area()
        time.sleep(0.1)

    left, top, right, bottom = map(int, region)
    log(f"📌 Region Selected: {left, top, right, bottom}", True)

    with mss.mss() as sct:
        while True:
            # ============================================================
            # CHECK FOR RE-SELECTION (F8)
            # ============================================================
            if keyboard.is_pressed("f8"):
                log("🔄 F8 Pressed! Re-selecting region...", True)
                
                # Cleanup Debug Windows to clear screen
                if debug:
                    cv2.destroyAllWindows()
                
                # Wait for key release to prevent instant re-trigger
                while keyboard.is_pressed("f8"):
                    time.sleep(0.1)

                # Select New Area
                new_region = selector.select_area()
                
                if new_region:
                    region = new_region
                    left, top, right, bottom = map(int, region)
                    
                    # RESET ALL STATE (Like a fresh start)
                    last_frame_norm = []
                    recent_signatures.clear()
                    spam_memory.clear()
                    prev_edge_count = None
                    
                    log(f"📌 NEW Region Selected: {left, top, right, bottom}", True)
                    log("♻️  Memory Reset - Starting Fresh", True)
                else:
                    log("❌ Selection cancelled, continuing with old region.", True)
                
                # Small pause before resuming
                time.sleep(0.5)
                continue
            # ============================================================

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
                if cv2.waitKey(1) & 0xFF == 27:
                    break

            if delta <= EDGE_DELTA_THRESHOLD:
                time.sleep(CAPTURE_SLEEP)
                continue

            comments = ocr(frame) or []
            
            if check_frozen(build_signature(comments), display_log):
                continue

            curr_norm = [normalize_line(u, t) for u, t in comments]
            
            new_msgs = detect_new(last_frame_norm, curr_norm, comments, display_log)
            last_frame_norm = curr_norm
            now = time.time()

            if new_msgs:
                print(time.strftime("%H:%M:%S"), f"⚡ Detected {len(new_msgs)} new")

            for username, text in new_msgs:
                
                # --- DEDUPLICATION LOGIC ---
                sig = normalize_line(username, text)
                
                if sig in recent_signatures:
                    last_seen_time = recent_signatures[sig]
                    if now - last_seen_time < DEDUPE_TIMEOUT:
                        continue
                
                # 2. SPAM CHECK
                if not spam_pass(username, text, now, display_log):
                    continue

                # 3. SQL / LONG TERM CHECK
                if not sql_should_emit(username, text, now, display_log):
                    continue

                # EMIT
                recent_signatures[sig] = now
                log(f"🟢 EMIT → @{username}: {text}", display_log)
                callback(username, text)
            
            if len(recent_signatures) > 1000:
                recent_signatures = {k:v for k,v in recent_signatures.items() if now - v < DEDUPE_TIMEOUT}

            time.sleep(CAPTURE_SLEEP)

    if debug:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    def on_new_comment(user, text):
        print(f"NEW >> {user}: {text}")
    start_ocr(on_new_comment, debug=True, display_log=False)
