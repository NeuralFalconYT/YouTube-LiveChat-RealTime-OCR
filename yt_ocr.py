# yt_ocr.py - High-quality OCR (no blur, no distortions)
# This script:
#  - Captures a screen region (YouTube chat)
#  - Runs OCR to get (username, comment) pairs
#  - Applies 5 layers of anti-duplicate / stability logic
#  - Sends only truly new comments to a callback

import time
import re
import hashlib
import keyboard
import mss
import numpy as np
import cv2
import ctypes
from difflib import SequenceMatcher
from overlay_select import ScreenSelector
from local_ocr import ocr  # <-- your OCR that returns list[(username, comment)]

# ============================================================
# MAKE PROCESS DPI AWARE (for sharp capture on Windows)
# ============================================================
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================

SIMILARITY_THRESHOLD = 0.86   # for frame-to-frame text similarity
HASH_SCALE = 0.20             # smaller = faster hash, still good enough
CAPTURE_SLEEP = 0.03          # ~33 FPS capture loop

DUP_TIME_WINDOW = 2 * 60      # Layer 4: 2 minutes history dedupe (same user+comment)
FREEZE_FRAMES   = 4           # Layer 5: if same signature for 4 frames → chat "frozen"

# ============================================================
# STATE (global)
# ============================================================

last_frame_norm = []          # normalized lines from previous frame (for detect_new)
last_img_hash   = None        # last frame's image hash (for duplicate frame skip)

recent_msg_log  = {}          # Layer 4: (user,text) -> last_seen_timestamp
sig_history     = []          # Layer 5: list of recent frame signatures

_RE_KEEP = re.compile(r"[a-z0-9@ ]+")

# ============================================================
# UTILS: Normalization & similarity
# ============================================================

def normalize_line(username, comment):
    """
    Normalize (username, comment) into a compact string
    used for frame-to-frame difference detection.
    """
    t = f"{username} {comment}".lower()
    t = re.sub(r"\s+", " ", t)                # collapse spaces
    t = _RE_KEEP.sub(lambda m: m.group(0), t) # keep only a-z0-9@ and space
    return t.replace(" ", "")                 # remove spaces entirely


def similar(a, b):
    """
    Compare two normalized strings and return True
    if they are similar enough.
    """
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


# ============================================================
# LAYER 3: Detect new messages between frames
# ============================================================

def detect_new(prev_norm, curr_norm, curr_raw):
    """
    Given:
      - prev_norm: list of normalized strings from previous frame
      - curr_norm: list of normalized strings from current frame
      - curr_raw:  list of (user, text) tuples from current frame
    Return:
      - subset of curr_raw considered NEW messages
    """
    if not curr_norm:
        return []
    if not prev_norm:
        # first frame: treat all as new
        return curr_raw[:]

    new = []
    i = len(curr_norm) - 1
    j = len(prev_norm) - 1

    # Walk from bottom to top and stop once we hit a "matching" line
    while i >= 0:
        if j >= 0 and similar(curr_norm[i], prev_norm[j]):
            break
        new.append(curr_raw[i])
        i -= 1

    return new[::-1]  # reverse to restore original top-down order


# ============================================================
# LAYER 4: Time-based history dedupe
# ============================================================

def normalize_for_history(username, text):
    """
    Normalize for history comparison:
    - lowercase
    - strip spaces at ends
    - keep simple text (no special chars)
    """
    u = username.strip().lower()
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9 @]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return u, t


def should_emit_message(username, text, now=None, window=DUP_TIME_WINDOW):
    """
    Returns True if (username, text) is NOT a duplicate within the time window.
    Otherwise returns False.
    """
    global recent_msg_log

    if now is None:
        now = time.time()

    u_norm, t_norm = normalize_for_history(username, text)
    key = (u_norm, t_norm)

    last_seen = recent_msg_log.get(key)
    if last_seen is not None and (now - last_seen) < window:
        # Seen recently → treat as duplicate
        return False

    # Update log with this message
    recent_msg_log[key] = now

    # Optional cleanup: if too big, drop very old entries
    if len(recent_msg_log) > 2000:
        cutoff = now - window
        recent_msg_log = {k: ts for k, ts in recent_msg_log.items() if ts >= cutoff}

    return True


# ============================================================
# LAYER 5: Chat-freeze detection via frame signature
# ============================================================

def build_signature(comments):
    """
    Build a "signature" for the current frame's comments.
    This is used to detect whether the chat content is frozen
    (same lines showing over and over).
    """
    sig = []
    for u, t in comments:
        # simple normalization
        u2 = u.strip().lower()
        t2 = t.strip().lower()
        t2 = re.sub(r"[^a-z0-9 @]+", "", t2)
        t2 = re.sub(r"\s+", " ", t2)
        sig.append(f"{u2}:{t2}")
    return sig


def update_and_check_frozen(signature):
    """
    Update signature history and return True if chat is considered "frozen".
    Chat is frozen if the same signature repeats for FREEZE_FRAMES times.
    """
    global sig_history

    sig_history.append(signature)
    if len(sig_history) > FREEZE_FRAMES:
        sig_history.pop(0)

    # not enough history yet
    if len(sig_history) < FREEZE_FRAMES:
        return False

    # frozen if all last FREEZE_FRAMES signatures are identical
    first = sig_history[0]
    return all(s == first for s in sig_history)


# ============================================================
# MAIN OCR LOOP (includes F8 re-select + all layers)
# ============================================================

def start_ocr(callback, debug=False):
    """
    Main loop:
    - Wait for F8 to select region
    - Continuously capture region
    - Run OCR
    - Apply dedupe layers
    - Call callback(user, text) for truly new messages
    """
    global last_img_hash, last_frame_norm, sig_history

    print("👉 Press F8 to select chat region...")

    selector = ScreenSelector()
    region = None

    # ---- INITIAL REGION SELECTION (first F8) ----
    while region is None:
        if keyboard.is_pressed("f8"):
            print("🎯 F8 pressed — select chat area now.")
            region = selector.select_area()
            break
        time.sleep(0.1)

    print("📌 Region Selected:", region)
    left, top, right, bottom = region

    # Debug window (keeps original resolution)
    if debug:
        cv2.namedWindow("CAPTURED", cv2.WINDOW_AUTOSIZE)

    with mss.mss() as sct:
        while True:
            try:
                # ======================================================
                # HOTKEY: F8 → RESELECT REGION ANYTIME
                # ======================================================
                if keyboard.is_pressed("f8"):
                    print("🎯 F8 pressed — re-select chat area.")
                    region = selector.select_area()
                    print("📌 New Region:", region)

                    left, top, right, bottom = region

                    # Reset per-region state
                    last_frame_norm = []
                    last_img_hash = None
                    sig_history = []  # reset chat-freeze history for new region

                    time.sleep(0.4)  # debounce, avoid double-trigger
                    continue

                # ======================================================
                # SCREEN CAPTURE
                # ======================================================
                w = right - left
                h = bottom - top

                raw = sct.grab({
                    "left": left,
                    "top": top,
                    "width": w,
                    "height": h
                })

                # Convert MSS image to BGR OpenCV image
                frame = np.array(raw)[:, :, :3]

                if debug:
                    cv2.imshow("CAPTURED", frame)
                    # Press 'q' to exit debug window
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                # ======================================================
                # LAYER 1: SKIP IDENTICAL FRAMES (HASH-BASED)
                # ======================================================
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small_hash = cv2.resize(gray, None, fx=HASH_SCALE, fy=HASH_SCALE)
                img_hash = hashlib.md5(small_hash.tobytes()).hexdigest()

                if img_hash == last_img_hash:
                    # exact same visual frame as last time → skip completely
                    time.sleep(CAPTURE_SLEEP)
                    continue

                last_img_hash = img_hash

                # ======================================================
                # OCR STEP: GET COMMENTS AS (username, text)
                # ======================================================
                comments = ocr(frame) or []  # must return list of (u, t)
                # e.g., [("@alex", "hello"), ("@neural", "hi bro"), ...]

                # ======================================================
                # LAYER 2: DE-DUPE SAME (USER, COMMENT) INSIDE THIS FRAME
                #          (different users with same text still allowed)
                # ======================================================
                seen_pairs = set()
                uniq = []
                for u, t in comments:
                    key = (u.strip().lower(), t.strip().lower())
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        uniq.append((u, t))
                comments = uniq

                # ======================================================
                # LAYER 5 (PART 1): CHAT-FREEZE DETECTION
                # Build signature over current comments and see if it is frozen
                # ======================================================
                signature = build_signature(comments)
                is_frozen = update_and_check_frozen(signature)

                if is_frozen:
                    # Chat content hasn't changed for FREEZE_FRAMES frames
                    # → treat as "frozen", do not emit anything
                    if debug:
                        print("[FREEZE] Chat frozen, skipping frame...")
                    time.sleep(CAPTURE_SLEEP)
                    continue

                # ======================================================
                # LAYER 3: FRAME-TO-FRAME NEW MESSAGE DETECTION
                # ======================================================
                curr_norm = [normalize_line(u, t) for (u, t) in comments]
                new_msgs = detect_new(last_frame_norm, curr_norm, comments)
                last_frame_norm = curr_norm

                # ======================================================
                # LAYER 4 + FINAL EMIT
                # ======================================================
                now = time.time()

                for username, text in new_msgs:
                    # Time-based history dedupe:
                    # Do not print if same user+comment seen in last DUP_TIME_WINDOW seconds
                    if not should_emit_message(username, text, now=now):
                        if debug:
                            print(f"[SKIP DUP] {username}: {text}")
                        continue

                    # If passed all layers → final new comment
                    if debug:
                        print(f"[OCR] {username}: {text}")
                    callback(username, text)

                time.sleep(CAPTURE_SLEEP)

            except KeyboardInterrupt:
                # Manual stop (Ctrl+C)
                break
            except Exception as e:
                if debug:
                    print("Error:", e)
                # Avoid tight error loop
                time.sleep(0.05)
                continue

    if debug:
        cv2.destroyWindow("CAPTURED")


# ============================================================
# SIMPLE TEST CALLBACK
# ============================================================

# def on_new_comment(user, text):
#     """
#     Basic example callback:
#     In real usage, you might send this to TTS, overlay, etc.
#     """
#     # print(f"NEW >> {user}: {text}")
#     pass


# if __name__ == "__main__":
#     # Run in debug mode:
#     # - shows the capture window
#     # - prints debug info (freeze, duplicate skips, etc.)
#     start_ocr(on_new_comment, debug=True)
