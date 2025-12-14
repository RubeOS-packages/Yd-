import sys
import os
import asyncio
import threading
import json
import argparse
import shutil
from typing import Callable

# --- Dependency Check ---
try:
    import yt_dlp
    from textual.app import App, ComposeResult
    from textual.containers import Container, Vertical, Horizontal
    from textual.widgets import Header, Footer, Input, Button, Static, ProgressBar, RadioSet, RadioButton, Log
    from textual.worker import Worker
    from fastapi import FastAPI, WebSocket, Request, Form
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    import uvicorn
except ImportError as e:
    print(f"Missing dependency: {e.name}")
    print("Please install: pip install yt-dlp textual fastapi uvicorn jinja2 websockets pyinstaller")
    sys.exit(1)

# ==========================================
# 1. EMBEDDED WEB ASSETS (HTML/CSS/JS)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniDownloader Web</title>
    <style>
        :root { --primary: #00ff88; --bg: #1a1a1a; --surface: #2d2d2d; --text: #eee; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin: 0; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
        .container { width: 90%; max-width: 600px; margin-top: 50px; }
        .card { background: var(--surface); padding: 2rem; border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); border: 1px solid #333; }
        h1 { margin-top: 0; color: var(--primary); text-align: center; text-transform: uppercase; letter-spacing: 2px; }
        input[type="text"] { width: 100%; padding: 12px; background: #111; border: 1px solid #444; color: white; border-radius: 6px; box-sizing: border-box; margin-bottom: 1rem; }
        input:focus { outline: 2px solid var(--primary); border-color: transparent; }
        .options { display: flex; gap: 1rem; margin-bottom: 1rem; justify-content: center; }
        button { width: 100%; padding: 12px; background: var(--primary); color: #000; border: none; font-weight: bold; border-radius: 6px; cursor: pointer; transition: transform 0.1s; }
        button:active { transform: scale(0.98); }
        #progress-area { margin-top: 20px; display: none; }
        .progress-bar { width: 100%; height: 10px; background: #444; border-radius: 5px; overflow: hidden; margin-top: 5px; }
        .fill { height: 100%; background: var(--primary); width: 0%; transition: width 0.2s; }
        .status-text { font-size: 0.9rem; color: #aaa; margin-top: 5px; text-align: center; }
        .log-box { background: #000; font-family: monospace; padding: 10px; margin-top: 20px; border-radius: 6px; height: 150px; overflow-y: auto; font-size: 0.8rem; border: 1px solid #333; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>Omni Downloader</h1>
            <input type="text" id="url" placeholder="Paste YouTube/Video URL here...">
            <div class="options">
                <label><input type="radio" name="fmt" value="video" checked> Video (MP4)</label>
                <label><input type="radio" name="fmt" value="audio"> Audio (MP3)</label>
            </div>
            <button onclick="startDownload()">Download Now</button>
            
            <div id="progress-area">
                <div class="progress-bar"><div class="fill" id="fill"></div></div>
                <div class="status-text" id="status">Initializing...</div>
            </div>
        </div>
        <div class="log-box" id="logs">
            <div>System Ready...</div>
        </div>
    </div>

    <script>
        let ws;
        function log(msg) {
            const box = document.getElementById('logs');
            box.innerHTML += `<div>> ${msg}</div>`;
            box.scrollTop = box.scrollHeight;
        }

        function startDownload() {
            const url = document.getElementById('url').value;
            const fmt = document.querySelector('input[name="fmt"]:checked').value;
            if(!url) return alert("Please enter a URL");

            document.getElementById('progress-area').style.display = 'block';
            
            // Connect WebSocket
            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            ws = new WebSocket(`${protocol}://${window.location.host}/ws`);

            ws.onopen = () => {
                log("Connected to server...");
                ws.send(JSON.stringify({url: url, format: fmt}));
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if(data.type === 'progress') {
                    document.getElementById('fill').style.width = data.percent + '%';
                    document.getElementById('status').innerText = data.status;
                } else if (data.type === 'log') {
                    log(data.msg);
                } else if (data.type === 'done') {
                    document.getElementById('status').innerText = "Download Complete!";
                    document.getElementById('fill').style.width = "100%";
                    ws.close();
                } else if (data.type === 'error') {
                    log("ERROR: " + data.msg);
                    ws.close();
                }
            };
        }
    </script>
</body>
</html>
"""

# ==========================================
# 2. CORE DOWNLOADER ENGINE
# ==========================================
class DownloaderEngine:
    def __init__(self):
        self.download_path = os.path.join(os.getcwd(), "downloads")
        if not os.path.exists(self.download_path):
            os.makedirs(self.download_path)

    def download(self, url, fmt, hook_callback):
        ydl_opts = {
            'outtmpl': f'{self.download_path}/%(title)s.%(ext)s',
            'progress_hooks': [hook_callback],
            'quiet': True,
            'nocolor': True
        }
        
        if fmt == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
            })
        else:
            ydl_opts.update({'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'})

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return True, "Finished"
        except Exception as e:
            return False, str(e)

# ==========================================
# 3. TEXTUAL TERMINAL UI (TUI)
# ==========================================
class OmniTUI(App):
    CSS = """
    Screen { align: center middle; background: $surface; }
    Container { width: 60; height: auto; border: tall $primary; background: $surface-lightEN; padding: 1; }
    .title { text-align: center; text-style: bold; color: $secondary; margin-bottom: 1; }
    Input { margin-bottom: 1; }
    RadioSet { margin-bottom: 1; layout: horizontal; justify-content: center; }
    Button { width: 100%; margin-bottom: 1; variant: primary; }
    ProgressBar { margin-bottom: 1; }
    Log { height: 10; border: solid $accent; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("OMNI DOWNLOADER", classes="title"),
            Input(placeholder="Paste URL...", id="url"),
            RadioSet(
                RadioButton("Video", value=True, id="vid"),
                RadioButton("Audio", id="aud"),
            ),
            Button("START DOWNLOAD", id="start"),
            ProgressBar(id="bar", total=100, show_eta=True),
            Log(id="log")
        )
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "start":
            url = self.query_one("#url").value
            is_audio = self.query_one("#aud").value
            fmt = "audio" if is_audio else "video"
            
            if not url:
                self.query_one("#log").write_line("Error: No URL provided.")
                return

            self.query_one("#log").write_line(f"Starting {fmt} download...")
            self.query_one("#start").disabled = True
            
            # Run download in a worker thread to keep UI responsive
            self.run_worker(self.download_task(url, fmt), thread=True)

    def download_task(self, url, fmt):
        engine = DownloaderEngine()
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%','')
                    self.call_from_thread(self.update_bar, float(p))
                except: pass
            elif d['status'] == 'finished':
                self.call_from_thread(self.query_one("#log").write_line, "Download complete. Processing...")

        success, msg = engine.download(url, fmt, progress_hook)
        self.call_from_thread(self.finish_ui, success, msg)

    def update_bar(self, val):
        self.query_one("#bar").update(progress=val)

    def finish_ui(self, success, msg):
        log = self.query_one("#log")
        if success:
            log.write_line("SUCCESS: Saved to /downloads")
        else:
            log.write_line(f"ERROR: {msg}")
        self.query_one("#start").disabled = False
        self.query_one("#bar").update(progress=0)

# ==========================================
# 4. FASTAPI WEB SERVER (WebSocket Support)
# ==========================================
web_app = FastAPI()
engine = DownloaderEngine()

@web_app.get("/")
async def get():
    return HTMLResponse(HTML_TEMPLATE)

@web_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        req = json.loads(data)
        url = req['url']
        fmt = req['format']

        def web_hook(d):
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%','')
                    asyncio.run_coroutine_threadsafe(
                        websocket.send_json({"type": "progress", "percent": float(p), "status": "Downloading..."}),
                        loop
                    )
                except: pass
            elif d['status'] == 'finished':
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"type": "log", "msg": "Processing file..."}),
                    loop
                )

        loop = asyncio.get_event_loop()
        # Run blocking download in executor
        await loop.run_in_executor(None, lambda: engine.download(url, fmt, web_hook))
        
        await websocket.send_json({"type": "done"})
    except Exception as e:
        await websocket.send_json({"type": "error", "msg": str(e)})
    finally:
        await websocket.close()

# ==========================================
# 5. BUILD SYSTEM (PyInstaller)
# ==========================================
def build_app():
    print("📦 Building Executable...")
    
    # Check if pyinstaller is installed
    if shutil.which("pyinstaller") is None:
        print("Error: PyInstaller not found. Run: pip install pyinstaller")
        return

    # Create spec arguments
    # Note: We don't need --add-data for HTML because it's embedded in the string above!
    args = [
        "omni.py",
        "--onefile",
        "--name=OmniDownloader",
        "--clean",
        "--hidden-import=yt_dlp",
        "--hidden-import=textual",
        "--hidden-import=uvicorn",
        "--hidden-import=fastapi",
    ]
    
    import PyInstaller.__main__
    PyInstaller.__main__.run(args)
    print("\n✅ Build Complete! Check the 'dist' folder.")

# ==========================================
# 6. MAIN ENTRY POINT
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", action="store_true", help="Start Web Server Mode")
    parser.add_argument("--build", action="store_true", help="Compile to Executable")
    args = parser.parse_args()

    if args.build:
        build_app()
    elif args.web:
        print("🌐 Server running at http://127.0.0.1:8000")
        print("Press Ctrl+C to stop")
        uvicorn.run(web_app, host="0.0.0.0", port=8000, log_level="error")
    else:
        app = OmniTUI()
        app.run()

if __name__ == "__main__":
    main()
