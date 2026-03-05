# VIA

<div align="center">
  <img src="img/logo.png" width="50%">
</div>

<br>

<div align="center">
  <img src="img/demo.gif" width="50%">
</div>

**VIA (Voice Input Assistant)** is a real-time transcription desktop application designed to bridge the gap between your voice and your computer. It provides high-accuracy, real-time speech transcription and acts as your intelligent typing assistant across any application you use.

Supports macOS, Windows, and Linux (X11 only; Wayland is not supported).

## Features & Capabilities

- **Instant Voice Input:** Easily trigger transcription via push-to-talk (hold) or tap-to-talk modes.
- **AI Post-Processing:** Optionally refine, format, or improve your transcribed text automatically using Gemini before it even hits your screen.
- **Auto-Paste Integration:** Your transcribed and processed text is immediately inserted into whatever app or window you are actively working in.
- **Floating Transcript Interface:** View your live transcription through a non-intrusive floating overlay.
- **History & Editing Control:** Access a chat-like history menu of your prior voice notes, giving you full control to copy, edit, or re-insert transcripts as needed.

## Prerequisites

- **Python 3.10+**
- **Google Cloud account** with a billing-enabled project
- A **Google Cloud API key**

## API Key Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Select or create a project with billing enabled
3. Create an API key (**APIs & Services → Credentials → Create Credentials → API key**)
4. Restrict the API key to required APIs (**APIs & Services → Library**):
   - **Cloud Speech-to-Text API**
   - **Generative Language API** *(for intelligent post-processing)*
5. Launch the app, click the 🎙 icon in the menu bar → **Show / Hide Settings**, and paste the key into the **Google Cloud API Key** field

## Installation

Installing on Linux:
```bash
# Clone the repository
git clone https://github.com/yuhao-he/voice-input-assistant.git
cd voice-input-assistant

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Installing on Mac:

Before running the application on macOS, you must grant appropriate permissions:
1. Go to **System Settings -> Privacy & Security -> Privacy** (or **System Preferences -> Security & Privacy -> Privacy**).
2. Go to **Accessibility** and enable it for your "host" application (e.g., Terminal, iTerm2, or VS Code).
3. Do the same under **Input Monitoring** to allow the application to listen for global hotkeys.

```bash
# Clone the repository
git clone https://github.com/yuhao-he/voice-input-assistant.git
cd voice-input-assistant

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Installing on Windows:
```bash
# Clone the repo
git clone https://github.com/yuhao-he/voice-input-assistant.git
cd voice_input

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
pip install "PyQt6==6.6.1" "PyQt6-Qt6==6.6.2"
```

## Usage

```bash
source venv/bin/activate # Windows: venv\Scripts\activate
python main.py
```

Press **`Ctrl` + `Shift` + `Alt` + `Q`** anywhere to show or hide the settings menu.

You can also access the settings menu via the microphone icon in your system tray or menu bar.
