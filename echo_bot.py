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
## For ai api reply
# from dotenv import load_dotenv
# load_dotenv()
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
    Prevents OCR garbage and weird unicode symbols.
    """

    # If no real characters in username → replace
    if not re.search("[A-Za-z]", user):
        user = "Unknown"

    # Remove unwanted characters from text
    text = re.sub(r"[^a-zA-Z0-9 .,!?\'\";:\-\(\)\[\]_]", " ", text)

    # Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= 1:
        return None

    return user, text


# =====================================
# RENDER VTuber DISPLAY FRAME
# =====================================

def render(user, text):
    """
    Creates the VTuber display frame showing the username + message
    centered on the background image.
    """

    img = Image.open(IMAGE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    # Load fonts
    try:
        f_comment = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        f_user = ImageFont.truetype(FONT_PATH, int(FONT_SIZE * USERNAME_SCALE))
    except:
        f_comment = f_user = ImageFont.load_default()

    # ----------------------------------------
    # Text wrapping helper
    # ----------------------------------------
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

    # ----- USERNAME -----
    for l in wrap(user, f_user):
        w = draw.textbbox((0,0), l, font=f_user)[2]
        draw.text(((W-w)//2, y), l, font=f_user, fill=USER_COLOR)
        y += 45

    y += 30  # space between username + comment

    # ----- COMMENT -----
    for l in wrap(text, f_comment):
        w = draw.textbbox((0,0), l, font=f_comment)[2]
        draw.text(((W-w)//2, y), l, font=f_comment, fill=TEXT_COLOR)
        y += 85

    # Convert to OpenCV BGR image
    return cv2.resize(cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR), DISPLAY_SIZE)


# =====================================
# TTS ENGINE
# =====================================

pipeline = KPipeline(lang_code='a')

def speak(text):
    """
    Uses Kokoro-TTS to speak the comment.
    Blocks until audio finished.
    """
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
# DUPLICATE FILTER (simple 10-sec cooldown)
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
    Whenever a new chat message is detected, callback() sends it to msg_queue.
    """

    def callback(user, text):
        cleaned = clean(user, text)
        if not cleaned:
            return

        user, text = cleaned

        # Prevent speaking same line twice within cooldown window
        if not is_duplicate(user, text):
            msg_queue.put((user, text))

    # Start OCR loop (this runs indefinitely)
    start_ocr(callback, debug=True)


# =====================================
# TTS + FRAME WORKER (NEW: JUMP TO LIVE IF BACKLOG TOO LARGE)
# =====================================

def tts_worker():
    """
    Reads messages from msg_queue and speaks them.
    NEW FEATURE:
        If queue backlog becomes too large (more than MAX_TTS_BACKLOG messages),
        we CLEAR the queue and only speak the newest message.
    This ensures TTS always stays LIVE and does NOT fall behind.
    """

    global current_frame, running

    while running:
        try:
            # -----------------------------------------------------
            # NEW FEATURE: Jump ahead to live comment
            # -----------------------------------------------------
            if msg_queue.qsize() > MAX_TTS_BACKLOG:
                print(f"⚠ Backlog too large ({msg_queue.qsize()}). Jumping to live messages...")

                last_msg = None

                # Empty queue but store last message
                while not msg_queue.empty():
                    last_msg = msg_queue.get()

                if last_msg:
                    user, text = last_msg
                    current_frame = render(user, text)
                    speak(text)

                continue  # restart loop

            # Normal mode: get next message
            user, text = msg_queue.get(timeout=1)

        except queue.Empty:
            continue

        # Update VTuber screen
        current_frame = render(user, text)

        # Speak the message
        speak(text)


# =====================================
# MAIN UI LOOP
# =====================================

def main():
    global running, current_frame

    # initial frame
    current_frame = render("VTuber Ready", "Waiting for messages...")

    # start threads
    threading.Thread(target=tts_worker, daemon=True).start()
    threading.Thread(target=ocr_worker, daemon=True).start()

    # UI window
    cv2.namedWindow("VTuber", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("VTuber", *DISPLAY_SIZE)

    while True:
        cv2.imshow("VTuber", current_frame)
        key = cv2.waitKey(30) & 0xFF

        if key == 27:  # ESC
            running = False
            break

    cv2.destroyAllWindows()


# =====================================
# RUN
# =====================================

if __name__ == "__main__":
    main()
