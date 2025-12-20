import cv2
import numpy as np
import time
import threading
import queue
import os
import re
from PIL import Image, ImageDraw, ImageFont
import sounddevice as sd
import webrtcvad
import asyncio
import edge_tts
import io
import soundfile as sf

# OCR input
from yt_ocr import start_ocr
# Skills
from begging_skills import usr_live_comment, boss_live_comment

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

language_code = "hi"  # "en"

DUPLICATE_COOLDOWN = 10
MAX_TTS_BACKLOG = 40
MAX_NO_SPEAK_SECONDS = 60  # Trigger boss message after 60s silence

# ========== VAD CONFIG ==========
vad = webrtcvad.Vad(3)
AI_RESPONSE_WAIT = 5
SILENCE_REQUIRED = 1.0
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
last_speak_time = time.time()  # Initialize globally

# ============================================================
# CLEAN USER + TEXT
# ============================================================

def clean(user, text):
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
    try:
        audio = sd.rec(VAD_FRAME, samplerate=VAD_SAMPLE_RATE, channels=1, dtype='int16')
        sd.wait()
    except Exception as e:
        print("⚠ Audio error:", e)
        return False
    raw = audio.tobytes()
    return vad.is_speech(raw, VAD_SAMPLE_RATE)

def wait_for_ai_response():
    print("🤖 Waiting for AI response window…")
    end_time = time.time() + AI_RESPONSE_WAIT
    while time.time() < end_time:
        if vad_is_speaking():
            print("🎤 AI is speaking… pausing TTS queue")
            break
        time.sleep(0.05)
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
        print("⏺ No AI response detected, continuing…")

# ============================================================
# RENDER VTUBER FRAME
# ============================================================

def render(user, text):
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
    for l in wrap(user, f_user):
        w = draw.textbbox((0, 0), l, font=f_user)[2]
        draw.text(((W - w) // 2, y), l, font=f_user, fill=USER_COLOR)
        y += 45
    y += 30
    for l in wrap(text, f_comment):
        w = draw.textbbox((0, 0), l, font=f_comment)[2]
        draw.text(((W - w) // 2, y), l, font=f_comment, fill=TEXT_COLOR)
        y += 85
    return cv2.resize(cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR), DISPLAY_SIZE)

# ============================================================
# TTS ENGINE
# ============================================================

async def speak_edge_async(text: str, voice: str):
    if not text or not text.strip():
        return
    communicate = edge_tts.Communicate(text=text, voice=voice)
    audio_bytes = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes += chunk["data"]
    audio_buffer = io.BytesIO(audio_bytes)
    data, samplerate = sf.read(audio_buffer, dtype="float32")
    sd.play(data, samplerate)
    sd.wait()

def speak_edge(text: str, language="en"):
    HINDI_FEMALE = "hi-IN-SwaraNeural"
    us_female = "en-US-AvaMultilingualNeural"
    voice = HINDI_FEMALE if language == "hi" else us_female
    asyncio.run(speak_edge_async(text, voice))

def speak(user="", text=""):
    global last_speak_time
    global language_code
    RED = "\033[91m"
    GREEN = "\033[92m"
    RESET = "\033[0m"
    
    # Update time immediately so we don't double trigger
    last_speak_time = time.time()
    
    

    try:
        # If user is empty, it's a boss message, don't format as user comment
        if user == "":
             comment_read = text
        else:
             comment_read = usr_live_comment(user, text)
        print(f"{RED}🔊 TTS:{RESET} {GREEN}{comment_read}{RESET}")     
        speak_edge(comment_read, language_code)
        wait_for_ai_response()
        
        # Update time again after speaking finishes
        last_speak_time = time.time()

    except Exception as e:
        print("⚠ TTS Error:", e)

# ============================================================
# DUPLICATE FILTER & OCR
# ============================================================

def is_duplicate(user, text):
    sig = f"{user.lower()}::{text.lower()}"
    now = time.time()
    if sig in spoken_cache and now - spoken_cache[sig] < DUPLICATE_COOLDOWN:
        return True
    spoken_cache[sig] = now
    return False

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
# TTS QUEUE THREAD (UPDATED FOR BOSS MODE)
# ============================================================

def tts_worker():
    global current_frame, running, last_speak_time

    while running:
        # 1. Handle Backlog
        if msg_queue.qsize() > MAX_TTS_BACKLOG:
            print(f"⚠ Backlog huge ({msg_queue.qsize()}) — skipping to latest")
            last_msg = None
            while not msg_queue.empty():
                last_msg = msg_queue.get()
            if last_msg:
                user, text = last_msg
                current_frame = render(user, text)
                speak(user, text)
            continue

        try:
            # 2. Try to get a message (wait max 1 second)
            user, text = msg_queue.get(timeout=1)
            
            # If we get here, we have a message
            current_frame = render(user, text)
            speak(user, text)

        except queue.Empty:
            # 3. NO MESSAGE RECEIVED - CHECK IDLE TIME
            now = time.time()
            if now - last_speak_time > MAX_NO_SPEAK_SECONDS:
                print("💤 Silence detected... Triggering Boss Mode!")
                
                try:
                    boss_msg = boss_live_comment() # Get entertainment text
                    
                    # Update screen so viewers see something happening
                    current_frame = render("🌟 HOST 🌟", boss_msg) 
                    
                    # Speak it (this will update last_speak_time inside speak function)
                    speak("", boss_msg)
                    
                except Exception as e:
                    print("⚠ Boss Mode Error:", e)
                    last_speak_time = time.time() # Reset timer even if error to prevent loop
            
            continue

# ============================================================
# MAIN
# ============================================================

def main():
    global running, current_frame, last_speak_time

    last_speak_time = time.time() # Start the timer
    current_frame = render("VTuber Ready", "Waiting for messages...")

    threading.Thread(target=tts_worker, daemon=True).start()
    threading.Thread(target=ocr_worker, daemon=True).start()

    cv2.namedWindow("VTuber", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("VTuber", *DISPLAY_SIZE)

    while True:
        try:
            cv2.imshow("VTuber", current_frame)
        except cv2.error as e:
            time.sleep(0.05)
            continue

        key = cv2.waitKey(1) & 0xFF
        if key == 27: # ESC
            running = False
            break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
