# bot.py - VTuber + OCR live chat reader (final production version)

import cv2
import numpy as np
import time
import threading
import queue
import os
import re
from PIL import Image, ImageDraw, ImageFont
from kokoro import KPipeline
import sounddevice as sd

# OCR input
from yt_ocr import start_ocr

# =====================================
# CONFIG
# =====================================

IMAGE_PATH = "./assets/background.png"
FONT_PATH = "./assets/Jua-Regular.ttf"
DISPLAY_SIZE = (400, 713)

FONT_SIZE = 90
USERNAME_SCALE = 0.6
TEXT_COLOR = (255, 56, 178)
USER_COLOR = (255, 2, 2)
language_code = 'a'
VOICE = "af_heart"
SPEED = 0.8

DUPLICATE_COOLDOWN = 10  # seconds
MAX_TTS_BACKLOG = 40     # NEW: If too many messages → jump to live

# =====================================
# GLOBALS
# =====================================

msg_queue = queue.Queue()   # queue for TTS messages
spoken_cache = {}           # duplicate filter cache

running = True              # for ending threads
current_frame = None        # latest frame to display


# =====================================
# CLEAN USER + TEXT
# =====================================

def clean(user, text):
    """
    Clean and normalize username + text.
    """
    if not re.search("[A-Za-z]", user):
        user = "Unknown"

    text = re.sub(r"[^a-zA-Z0-9 .,!?\'\";:\-\(\)\[\]_]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= 1:
        return None

    return user, text


# =====================================
# RENDER VTuber DISPLAY FRAME
# =====================================

def render(user, text):
    img = Image.open(IMAGE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    try:
        f_comment = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        f_user = ImageFont.truetype(FONT_PATH, int(FONT_SIZE * USERNAME_SCALE))
    except:
        f_comment = f_user = ImageFont.load_default()

    def wrap(t, font, width_limit=W-200):
        lines, cur = [], ""
        for w in t.split():
            test = (cur + " " + w).strip() if cur else w
            if draw.textbbox((0,0), test, font=font)[2] <= width_limit:
                cur = test
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    y = H // 2 - 300

    for l in wrap(user, f_user):
        w = draw.textbbox((0,0), l, font=f_user)[2]
        draw.text(((W-w)//2, y), l, font=f_user, fill=USER_COLOR)
        y += 45

    y += 30

    for l in wrap(text, f_comment):
        w = draw.textbbox((0,0), l, font=f_comment)[2]
        draw.text(((W-w)//2, y), l, font=f_comment, fill=TEXT_COLOR)
        y += 85

    return cv2.resize(cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR), DISPLAY_SIZE)


# =====================================
# TTS ENGINE
# =====================================

pipeline = KPipeline(lang_code=language_code)

def speak(text):
    RED = "\033[91m"
    GREEN = "\033[92m"
    RESET = "\033[0m"

    print(f"{RED}🔊 TTS:{RESET} {GREEN}{text}{RESET}")

    try:
        for _, (_, _, audio) in enumerate(pipeline(text, voice=VOICE, speed=SPEED)):
            sd.play(audio, samplerate=24000)
            sd.wait()
    except Exception as e:
        print("⚠ TTS Error:", e)


# =====================================
# DUPLICATE FILTER
# =====================================

def is_duplicate(user, text):
    sig = f"{user.lower()}::{text.lower()}"
    now = time.time()

    if sig in spoken_cache and now - spoken_cache[sig] < DUPLICATE_COOLDOWN:
        return True

    spoken_cache[sig] = now
    return False


# =====================================
# OCR WORKER THREAD
# =====================================

def ocr_worker():
    """
    Runs OCR module in a thread.
    Passes 'display_log=False' to avoid cluttering console with debug info.
    Passes 'debug=True' so we can see the CV2 window for OCR to check selection.
    """

    def callback(user, text):
        cleaned = clean(user, text)
        if not cleaned:
            return

        user, text = cleaned
        # print(time.strftime("%H:%M:%S"), f"📝 OCR: @{user}: {text}")
        
        if not is_duplicate(user, text):
            msg_queue.put((user, text))

    # IMPORTANT: We run start_ocr here.
    # Because start_ocr uses keyboard.is_pressed('f8'), it should work even in thread.
    # However, ensure the main CV2 window doesn't steal focus constantly.
    start_ocr(callback, debug=True, display_log=False)


# =====================================
# TTS + FRAME WORKER
# =====================================

def tts_worker():
    global current_frame, running

    while running:
        try:
            if msg_queue.qsize() > MAX_TTS_BACKLOG:
                print(f"⚠ Backlog too large ({msg_queue.qsize()}). Jumping to live...")
                last_msg = None
                while not msg_queue.empty():
                    last_msg = msg_queue.get()

                if last_msg:
                    user, text = last_msg
                    current_frame = render(user, text)
                    speak(text)
                continue

            user, text = msg_queue.get(timeout=1)

        except queue.Empty:
            continue

        current_frame = render(user, text)
        speak(text)


# =====================================
# MAIN UI LOOP
# =====================================

def main():
    global running, current_frame

    current_frame = render("VTuber Ready", "Waiting for chat...")

    # START OCR THREAD
    # We must ensure this runs in background so main loop can handle its own UI
    ocr_thread = threading.Thread(target=ocr_worker, daemon=True)
    ocr_thread.start()

    # START TTS THREAD
    tts_thread = threading.Thread(target=tts_worker, daemon=True)
    tts_thread.start()

    # Create VTuber Window
    cv2.namedWindow("VTuber", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("VTuber", *DISPLAY_SIZE)
    # Move window to top-left so it doesn't overlap with OCR debug window
    cv2.moveWindow("VTuber", 0, 0) 

    print("✅ Bot Started. Press F8 to select chat region. Press ESC to quit.")

    while running:
        if current_frame is not None:
            cv2.imshow("VTuber", current_frame)
        
        # We use a small waitKey to keep the UI responsive
        key = cv2.waitKey(100) & 0xFF

        if key == 27:  # ESC
            running = False
            print("🛑 Stopping Bot...")
            break

    cv2.destroyAllWindows()


# =====================================
# RUN
# =====================================

if __name__ == "__main__":
    main()
