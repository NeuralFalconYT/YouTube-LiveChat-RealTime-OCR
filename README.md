
# YouTube LiveChat RealTime OCR

A real-time YouTube Live Chat capture tool powered by on-screen OCR.  
Designed for creators who want interactive livestreams where viewers can chat or talk with an AI using TTS or chatbot responses.

This project extracts YouTube Live Chat directly from the visible screen **no YouTube API required** and outputs clean, deduplicated messages ready for AI-driven applications such as VTuber TTS, character agents, or scripted automation.

---

## ✨ Features

- ✔ Real-time OCR-based chat extraction  
- ✔ Uses RapidOCR for text detection and recognition  
- ✔ Multi-line message reconstruction  
- ✔ Duplicate message filtering  
- ✔ Frame-to-frame difference detection  
- ✔ Chat-freeze detection (prevents old messages from repeating)  
- ✔ Callback-based system for custom integrations (TTS, AI chatbots, etc.)  
- ✔ Works without YouTube API or API keys  

---

## 📦 OCR Models Used

This project uses pre-trained OCR models from **RapidOCR**, which are licensed under the Apache License 2.0.

Model sources:

- RapidOCR GitHub:  
  https://github.com/RapidAI/RapidOCR  
- RapidOCR ModelScope (ONNX models):  
  https://www.modelscope.cn/models/RapidAI/RapidOCR/tree/master/onnx/PP-OCRv5  

Models included in this repository:

- `ch_PP-OCRv5_mobile_det.onnx` (text detection)  
- `en_PP-OCRv5_rec_mobile_infer.onnx` (English text recognition)  

These model files are **not created by this project** and are redistributed under the terms of the Apache-2.0 license.



## ⚙️ How It Works

1. User selects YouTube chat area using a screen selector  
2. Frames are captured in real-time  
3. OCR is applied using RapidOCR models  
4. Messages are cleaned, deduplicated, and structured  
5. Final messages are passed into callback functions (AI, TTS, logging, etc.)

This approach does **not bypass**, modify, or interact with YouTube’s internal systems.  
It simply performs OCR on what a human sees on the screen.

---

## 🚫 No YouTube API Used

This tool does **not** interact with YouTube APIs, private endpoints, cookies, tokens, or user data.  
Everything happens locally through on-screen OCR.

This method respects YouTube's platform rules because it works only with publicly visible on-screen content.

---

## 🧩 Example Integration

Example:

```python
from yt_ocr import start_ocr
def on_new_chat(user, text):
    print(f"{user}: {text}")  # or send to TTS / AI agent

start_ocr(on_new_chat)
````
###### Press F8 on Keyboard and select the YouTube Live Chat region <br>
![p](https://github.com/user-attachments/assets/005ad6d7-30e8-48cb-b79e-9d0d4ab88d5a)

---

## 📁 Project Structure

```
YouTube-LiveChat-RealTime-OCR/
│
├── models/                         # Pre-trained RapidOCR model files (Apache-2.0 licensed)
│   ├── ch_PP-OCRv5_mobile_det.onnx             # Text detection model 
│   ├── en_PP-OCRv5_rec_mobile_infer.onnx       # English text recognition model 
│   ├── ch_PP-OCRv5_mobile_det.onnx.bak         # Not used 
│   └── MODEL_SOURCE.md                        
│
├── local_ocr.py                   # Basic RapidOCR wrapper (extracts text from an image/screenshot)
├── yt_ocr.py                      # Live screen capture + dedupe filters + real-time chat extraction
│
├── echo_bot.py                    # Example bot: reads extracted comments using TTS or AI
├── blocked.txt                    # Words/phrases to filter out before processing comments
│
├── requirements.txt               # Python dependencies for OCR + example TTS bot
│
├── README.md                      
├── NOTICE                         
└── LICENSE                        

```



---
---

## 📝 License & Attribution

This repository includes **only the chat extraction logic**.  
All OCR model files are the property of **RapidAI** and its contributors.

### RapidOCR License  
This project uses RapidOCR, which is licensed under:  
**Apache License, Version 2.0**  
https://www.apache.org/licenses/LICENSE-2.0

A `NOTICE` file is included in this repository as required by the license.

---
## ❤️ Credits

OCR engine and model files provided by:

**RapidOCR Project**
[https://github.com/RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR)

This project simply builds an application layer on top.

---

## 🙌 Why This Project Exists

This tool was built while experimenting with YouTube livestreams where viewers could talk directly to an AI in real time.
Since YouTube provides no easy API for retrieving live chat without quota limits, this tool uses OCR to extract chat directly from the screen making it lightweight, API-free, and creator-friendly.

