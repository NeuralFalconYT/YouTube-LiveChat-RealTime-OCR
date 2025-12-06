from rapidocr_onnxruntime import RapidOCR
import cv2
import re
import numpy as np
import unicodedata
import time


DETECTION_MODEL_PATH = "./models/ch_PP-OCRv5_mobile_det.onnx"
RECOGNITION_MODEL_PATH = "./models/en_PP-OCRv5_rec_mobile_infer.onnx"

ocr_engine = RapidOCR(
    use_cuda=True,
    det_model_path=DETECTION_MODEL_PATH,
    rec_model_path=RECOGNITION_MODEL_PATH
)

# =====================
# TYPE CHECKERS
# =====================

def is_cv_image(obj):
    return isinstance(obj, np.ndarray)

def is_image_path(obj):
    return isinstance(obj, str) and obj.lower().endswith(('.png', '.jpg', '.jpeg'))


# =====================
# OCR INTERFACE
# =====================

def ocr_interface(image):
    if is_image_path(image):
        image = cv2.imread(image)
    elif not is_cv_image(image):
        return []

    try:
        ocr_results, _ = ocr_engine(image)
    except Exception:
        return []

    if not ocr_results:
        return []

    extracted_lines = []
    for item in ocr_results:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue

        box, detected_text, confidence = item
        clean_text = str(detected_text).strip()

        if clean_text:
            extracted_lines.append(clean_text)

    return extracted_lines


# =====================
# LIVE BLOCKED.TXT SUPPORT
# =====================

_last_load_time = 0
_blocked_cache = []

def load_blocked_phrases():
    """Reload blocked.txt every 2 seconds without slowing OCR."""
    global _last_load_time, _blocked_cache
    now = time.time()

    if now - _last_load_time > 10:  # refresh every 10 sec
        try:
            with open("blocked.txt", "r", encoding="utf-8") as f:
                _blocked_cache = [
                    line.strip().lower()
                    for line in f
                    if line.strip()
                ]
        except FileNotFoundError:
            _blocked_cache = []

        _last_load_time = now

    return _blocked_cache


def detect_bad_comments(text):
    """Dynamic detection using blocked.txt"""
    blocked = load_blocked_phrases()
    lower = text.lower()
    return any(b in lower for b in blocked)


# =====================
# FILTER 2: XP BADGE REMOVAL
# =====================

def is_xp_badge(line):
    upper = line.upper().strip()

    if re.match(r"^\d+\s*XP$", upper):
        return True
    if re.match(r"^[A-Z]XP$", upper):
        return True

    return False


# =====================
# FIXING HASH TAG #1
# =====================

def fix_hash_tag(lines):
    if not any(re.match(r"^#\d+$", ln) for ln in lines):
        return lines

    result = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("@"):
            username = line
            i += 1

            if i < len(lines) and re.match(r"^#\d+$", lines[i]):
                i += 1

            parts = []
            while i < len(lines) and not lines[i].startswith("@"):
                parts.append(lines[i])
                i += 1

            comment = " ".join(parts).strip()
            result.append(f"{username} {comment}" if comment else username)

        else:
            i += 1

    return result


# =====================
# MERGE MULTI-LINE COMMENTS
# =====================

def fix_multiline(lines):
    result = []
    buffer = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("@"):
            if buffer:
                result.append(buffer.strip())
            buffer = line
        else:
            buffer += " " + line

    if buffer:
        result.append(buffer.strip())

    return result


# =====================
# REMOVE EMOJI
# =====================

def remove_emoji(text):
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Sk", "Cs")
    )


# =====================
# MAIN OCR PIPELINE
# =====================

def ocr(image):
    try:
        raw_lines = ocr_interface(image) or []

        clean = []

        for line in raw_lines:
            if detect_bad_comments(line):
                continue

            if len(line.strip()) <= 1 or not re.search(r"[A-Za-z0-9]", line):
                continue

            if is_xp_badge(line):
                continue

            line = remove_emoji(line)
            clean.append(line)

        clean = fix_hash_tag(clean)
        clean = fix_multiline(clean)

        chats = []
        for line in clean:
            if line.startswith("@"):
                parts = line.split()
                if len(parts) < 2:
                    continue
                user = parts[0]
                msg = " ".join(parts[1:])
                chats.append((user, msg))

        return chats

    except Exception as e:
        print("OCR Error:", e)
        return []


if __name__ == "__main__":
    img = "test.jpg"
    result = ocr(img)
    for user, msg in result:
        print(f"{user}: {msg}")
