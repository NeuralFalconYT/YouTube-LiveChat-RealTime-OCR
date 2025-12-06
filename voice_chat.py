# https://vb-audio.com/Cable/
#for speaker select CABLE IN CH (VB-Audio Virtual Cable)
#for chatgpt/gemini/ gemini live voice call voice call CABLE OUTPUT (VB-Audio Virtual Cable)
# in obs use mic as CABLE OUTPUT (VB-Audio Virtual Cable)
#for lip sync avater use https://malaybaku.github.io/VMagicMirror/en/

# before callling any ai prompt
# """
# You are Neural Falcon AI Co-Host, talking on a YouTube livestream.
# Your personality and speaking style should be just like famous YouTuber high energy, chaotic, loud, goofy, dramatic, fast reactions, and playful roasting.
# (Style only, no impersonation of his identity or voice.)

# Rules:

# Replies must be 1–2 sentences, fast, hype, and chaotic.

# Overreact, yell a little, freak out, be goofy, be loud.

# Use Speed-like phrases such as:

# “BROOO WHAT IS THAT 💀🔥”

# “Ayo chat LOOK at this dude 😂😂”

# “Nah ain’t no way bro typed that 😭”

# “CHILL CHILL CHILL BROOOO 😭🔥”

# “Man stop playin’ with meee!”



# Friendly roasting allowed — roast viewers like Speed does: silly, dramatic, playful, NEVER harmful.

# If someone trolls → roast them back with chaos and humor.

# If the message is nonsense → scream-laugh and react dramatically.

# If OCR is broken → act confused and roast the glitch.

# Greet people with hype energy.

# Answer questions with dramatic confidence.

# NEVER say you're an AI unless asked directly.

# No formal tone. No long replies.

# Output ONLY the message, nothing else.
# """
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
import webrtcvad

# OCR input
from yt_ocr import start_ocr


# ============================================================
# CONFIG
# ============================================================

IMAGE_PATH = "./assets/background.png"
FONT_PATH = "./assets/Jua-Regular.ttf"
DISPLAY_SIZE = (400, 713)

FONT_SIZE = 90
USERNAME_SCALE = 0.6
TEXT_COLOR = (255, 56, 178)
USER_COLOR = (255, 2, 2)

VOICE = "af_heart"
SPEED = 0.8

DUPLICATE_COOLDOWN = 10
MAX_TTS_BACKLOG = 40

# ========== VAD CONFIG ==========
vad = webrtcvad.Vad(3)       # 0–3 sensitivity (3 most sensitive)
AI_RESPONSE_WAIT = 5         # seconds to wait for AI to reply
SILENCE_REQUIRED = 1.0       # seconds silence needed to resume TTS
VAD_SAMPLE_RATE = 16000
VAD_FRAME_MS = 20
VAD_FRAME = int(VAD_SAMPLE_RATE * (VAD_FRAME_MS / 1000))


# ============================================================
# GLOBALS
# ============================================================

msg_queue = queue.Queue()
spoken_cache = {}
running = True
current_frame = None


# ============================================================
# CLEAN USER + TEXT
# ============================================================

def clean(user, text):
    """Normalize OCR text and ensure it is safe to speak."""

    if not re.search("[A-Za-z]", user):
        user = "Unknown"

    text = re.sub(r"[^a-zA-Z0-9 .,!?\'\";:\-\(\)\[\]_]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= 1:
        return None

    return user, text


# ============================================================
# VAD HELPERS
# ============================================================

def vad_is_speaking():
    """Record a tiny audio frame and detect if speech exists."""
    try:
        audio = sd.rec(VAD_FRAME, samplerate=VAD_SAMPLE_RATE, channels=1, dtype='int16')
        sd.wait()
    except Exception as e:
        print("⚠ Audio error:", e)
        return False

    raw = audio.tobytes()
    return vad.is_speech(raw, VAD_SAMPLE_RATE)


def wait_for_ai_response():
    """Pause TTS until AI finishes talking."""

    print("🤖 Waiting for AI response window…")
    end_time = time.time() + AI_RESPONSE_WAIT

    # Phase 1: Give AI time to begin speaking
    while time.time() < end_time:
        if vad_is_speaking():
            print("🎤 AI is speaking… pausing TTS queue")
            break
        time.sleep(0.05)

    # Phase 2: AI is talking — wait for silence
    if vad_is_speaking():
        print("⏳ Waiting for AI to finish…")
        silence_start = None

        while True:
            if vad_is_speaking():
                silence_start = None
            else:
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= SILENCE_REQUIRED:
                    print("🔇 AI finished speaking. Resuming queue.")
                    return

            time.sleep(0.05)
    else:
        print("⏺ No AI response detected, continuing…" )


# ============================================================
# RENDER VTUBER FRAME
# ============================================================

def render(user, text):
    """Create centered VTuber display frame."""

    img = Image.open(IMAGE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    try:
        f_comment = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        f_user = ImageFont.truetype(FONT_PATH, int(FONT_SIZE * USERNAME_SCALE))
    except:
        f_comment = f_user = ImageFont.load_default()

    def wrap(t, font, width_limit=W - 200):
        lines, cur = [], ""
        for w in t.split():
            test = (cur + " " + w).strip() if cur else w
            if draw.textbbox((0, 0), test, font=font)[2] <= width_limit:
                cur = test
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    y = H // 2 - 300

    # Username
    for l in wrap(user, f_user):
        w = draw.textbbox((0, 0), l, font=f_user)[2]
        draw.text(((W - w) // 2, y), l, font=f_user, fill=USER_COLOR)
        y += 45

    y += 30

    # Comment
    for l in wrap(text, f_comment):
        w = draw.textbbox((0, 0), l, font=f_comment)[2]
        draw.text(((W - w) // 2, y), l, font=f_comment, fill=TEXT_COLOR)
        y += 85

    return cv2.resize(cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR), DISPLAY_SIZE)


# ============================================================
# TTS ENGINE
# ============================================================

pipeline = KPipeline(lang_code='a')

def speak(text):
    RED = "\033[91m"
    GREEN = "\033[92m"
    RESET = "\033[0m"

    print(f"{RED}🔊 TTS:{RESET} {GREEN}{text}{RESET}")

    try:
        for _, (_, _, audio) in enumerate(pipeline(text, voice=VOICE, speed=SPEED)):
            sd.play(audio, samplerate=24000)
            sd.wait()

        wait_for_ai_response()

    except Exception as e:
        print("⚠ TTS Error:", e)


# ============================================================
# DUPLICATE FILTER
# ============================================================

def is_duplicate(user, text):
    sig = f"{user.lower()}::{text.lower()}"
    now = time.time()

    if sig in spoken_cache and now - spoken_cache[sig] < DUPLICATE_COOLDOWN:
        return True

    spoken_cache[sig] = now
    return False


# ============================================================
# OCR THREAD
# ============================================================

def ocr_worker():
    def callback(user, text):
        cleaned = clean(user, text)
        if not cleaned:
            return

        user, text = cleaned
        if not is_duplicate(user, text):
            msg_queue.put((user, text))

    start_ocr(callback, debug=True)


# ============================================================
# TTS QUEUE THREAD
# ============================================================

def tts_worker():
    global current_frame, running

    while running:

        # Skip large backlog
        if msg_queue.qsize() > MAX_TTS_BACKLOG:
            print(f"⚠ Backlog huge ({msg_queue.qsize()}) — skipping to latest message")

            last_msg = None
            while not msg_queue.empty():
                last_msg = msg_queue.get()

            if last_msg:
                user, text = last_msg
                current_frame = render(user, text)
                speak(text)
            continue

        try:
            user, text = msg_queue.get(timeout=1)
        except queue.Empty:
            continue

        current_frame = render(user, text)
        speak(text)


# ============================================================
# MAIN UI LOOP (CRASH-PROOF)
# ============================================================

def main():
    global running, current_frame

    current_frame = render("VTuber Ready", "Waiting for messages...")

    threading.Thread(target=tts_worker, daemon=True).start()
    threading.Thread(target=ocr_worker, daemon=True).start()

    cv2.namedWindow("VTuber", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("VTuber", *DISPLAY_SIZE)

    while True:
        try:
            cv2.imshow("VTuber", current_frame)
        except cv2.error as e:
            print("⚠ OpenCV display error (auto-recover):", e)
            time.sleep(0.05)
            continue

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            running = False
            break

    cv2.destroyAllWindows()


# ============================================================

if __name__ == "__main__":
    main()
