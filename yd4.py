import sys
import os
import asyncio
import json
import argparse
import subprocess
from typing import Callable, Optional, Tuple
from pathlib import Path

# --- Dependency Check and Imports ---
try:
    import yt_dlp
except ImportError:
    print("FATAL ERROR: yt-dlp is not installed. Please run: pip install yt-dlp")
    sys.exit(1)

# Only import Textual/FastAPI components if they are available
TEXTUAL_OK = True
try:
    from textual.app import App, ComposeResult
    from textual.containers import Container
    from textual.widgets import Header, Footer, Input, Button, Static, ProgressBar, RadioSet, RadioButton, Log
    from textual.worker import Worker, WorkerState
except ImportError:
    TEXTUAL_OK = False

FASTAPI_OK = True
try:
    from fastapi import FastAPI, WebSocket, Request
    from fastapi.responses import HTMLResponse
    import uvicorn
except ImportError:
    FASTAPI_OK = False

# ==========================================
# PLATFORM-SPECIFIC DEFAULT PATHS
# ==========================================
def get_default_download_path() -> str:
    """Get platform-specific default download path."""
    system = sys.platform
    
    if system == "win32":
        # Windows: Create 'ytd' folder in user's home directory
        home = Path.home()
        download_path = home / "ytd"
    elif system == "darwin":
        # macOS: Create 'ytd' folder in user's home directory
        home = Path.home()
        download_path = home / "ytd"
    elif system.startswith("linux"):
        # Check if running on Android (Termux)
        if os.path.exists("/data/data/com.termux"):
            # Android/Termux: Use system's Download folder
            storage_path = Path("/storage/emulated/0")
            if storage_path.exists():
                download_path = storage_path / "Download"
            else:
                # Fallback to Termux home downloads
                download_path = Path.home() / "storage" / "downloads"
        else:
            # Regular Linux: Create 'ytd' folder in user's home directory
            home = Path.home()
            download_path = home / "ytd"
    else:
        # Fallback for unknown systems
        home = Path.home()
        download_path = home / "ytd"
    
    return str(download_path)

# ==========================================
# 1. CORE DOWNLOADER ENGINE (Enhanced)
# ==========================================
class DownloaderEngine:
    """Handles the core download logic using yt-dlp and manages file paths."""

    def download(self, url: str, fmt: str, output_path: str, hook_callback: Optional[Callable] = None) -> Tuple[bool, str]:
        # Set default path if none is provided
        if not output_path or not output_path.strip():
            output_path = get_default_download_path()
        
        # Normalize path
        output_path = os.path.abspath(output_path)
        
        # Ensure the directory exists
        try:
            os.makedirs(output_path, exist_ok=True)
        except Exception as e:
            return False, f"Failed to create directory: {str(e)}"
            
        ydl_opts = {
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'quiet': True,
            'nocolor': True,
            'noplaylist': True,
            'no_warnings': True,
        }

        if fmt == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({'format': 'bestvideo+bestaudio/best'})

        downloaded_file = None
        
        def internal_hook(d):
            nonlocal downloaded_file
            if d['status'] == 'finished':
                # Capture the downloaded filename
                downloaded_file = d.get('filename')
            
            if hook_callback:
                try:
                    hook_callback(d)
                except Exception:
                    pass

        ydl_opts['progress_hooks'] = [internal_hook]
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Get the final filename after all post-processing
                if info:
                    # For audio, the extension changes after post-processing
                    if fmt == 'audio':
                        base_filename = ydl.prepare_filename(info)
                        # Replace extension with mp3
                        final_file = os.path.splitext(base_filename)[0] + '.mp3'
                    else:
                        final_file = ydl.prepare_filename(info)
                    
                    # Verify file exists
                    if os.path.exists(final_file):
                        return True, final_file
                    elif downloaded_file and os.path.exists(downloaded_file):
                        return True, downloaded_file
                    else:
                        # Search for the file in the output directory
                        if info.get('title'):
                            title = info['title']
                            # Try to find file with this title
                            for file in os.listdir(output_path):
                                if title in file:
                                    found_path = os.path.join(output_path, file)
                                    return True, found_path
                        
                        return False, f"Download completed but file not found in {output_path}"
                else:
                    return False, "Failed to extract video information"

        except Exception as e:
            error_msg = str(e)
            if "call_from_thread" in error_msg or "ERROR:" in error_msg:
                error_msg = "Download failed. Please check the URL and try again."
            return False, error_msg

# ==========================================
# 2. UTILITY FUNCTIONS
# ==========================================
def open_file_in_os(file_path: str) -> bool:
    """Opens a file using the default OS application. Returns True if successful."""
    try:
        if sys.platform.startswith('darwin'):
            subprocess.call(('open', file_path))
        elif sys.platform.startswith('win32'):
            os.startfile(file_path)
        else:
            subprocess.call(('xdg-open', file_path))
        return True
    except Exception:
        return False

# ==========================================
# 3. TERMINAL USER INTERFACE (TUI) MODE
# ==========================================
def run_tui_app():
    if not TEXTUAL_OK:
        print("ERROR: Textual dependencies are missing. Run: pip install textual")
        sys.exit(1)
        
    class YDDL_TUI(App):
        CSS = """
        Screen {
            align: center middle;
            background: #1f1f1f;
        }
        
        Container {
            width: 80%;
            height: auto;
            border: solid #00AAFF;
            background: #2f2f2f;
            padding: 1;
        }
        
        .title {
            text-align: center;
            text-style: bold;
            color: #00AAFF;
            margin-bottom: 1;
        }
        
        Input {
            margin-bottom: 1;
            color: white;
            background: #3f3f3f;
        }
        
        RadioSet {
            margin-bottom: 1;
            layout: horizontal;
            align: center middle;
        }

        #start {
            width: 100%;
            margin-bottom: 1;
            background: #00AAFF;
            color: black;
            text-style: bold;
        }

        #open {
            margin-top: 1;
            background: #0088AA;
            color: white;
            width: 100%;
        }
        
        ProgressBar {
            margin-bottom: 1;
        }
        
        Log {
            height: 10;
            border: solid #777777;
            background: #111111;
            color: #CCCCCC;
        }
        """

        BINDINGS = [("o", "open_last_file", "Open Last File")]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.last_downloaded_file = None
            self.is_downloading = False

        def compose(self) -> ComposeResult:
            default_path = get_default_download_path()
            yield Header()
            yield Container(
                Static("YD Downloader (Terminal)", classes="title"),
                Input(placeholder=f"Output Folder (default: {default_path})", id="path"),
                Input(placeholder="Paste URL (Video/Playlist)...", id="url"),
                RadioSet(
                    RadioButton("Video (MP4)", value=True, id="vid"),
                    RadioButton("Audio (MP3)", id="aud"),
                ),
                Button("START DOWNLOAD", id="start"),
                ProgressBar(id="bar", total=100, show_eta=False),
                Log(id="log"),
                Button("OPEN LAST DOWNLOADED FILE", id="open", disabled=True),
            )
            yield Footer()

        async def on_button_pressed(self, event: Button.Pressed):
            if event.button.id == "start":
                if self.is_downloading:
                    return
                
                url = self.query_one("#url", Input).value.strip()
                path = self.query_one("#path", Input).value.strip()
                fmt = "audio" if self.query_one("#aud", RadioButton).value else "video"
                
                if not url:
                    self.query_one("#log", Log).write_line("Error: No URL provided.")
                    return

                output_dir = path if path else get_default_download_path()
                self.query_one("#log", Log).write_line(f"Starting {fmt} download...")
                self.query_one("#log", Log).write_line(f"Output directory: {output_dir}")
                self.query_one("#start", Button).disabled = True
                self.query_one("#open", Button).disabled = True
                self.is_downloading = True

                # Pass a lambda that calls download_task - this ensures it runs in worker thread
                self.run_worker(lambda: self.download_task(url, fmt, path), thread=True)
            
            elif event.button.id == "open":
                self.action_open_last_file()

        def download_task(self, url: str, fmt: str, path: str):
            """This runs in a worker thread."""
            engine = DownloaderEngine()

            def progress_hook(d):
                if d['status'] == 'downloading':
                    try:
                        percent_str = d.get('_percent_str', '0%').replace('%', '').strip()
                        percent = float(percent_str)
                        self.call_from_thread(self.update_progress, percent)
                    except (ValueError, TypeError):
                        pass
                elif d['status'] == 'finished':
                    self.call_from_thread(self.query_one("#log", Log).write_line, "Download complete. Processing...")

            success, result = engine.download(url, fmt, path, progress_hook)
            self.call_from_thread(self.finish_ui, success, result)

        def update_progress(self, percent: float):
            """Called from worker thread via call_from_thread."""
            try:
                self.query_one("#bar", ProgressBar).update(progress=percent)
            except Exception:
                pass

        def finish_ui(self, success: bool, result: str):
            """Called from worker thread via call_from_thread."""
            log = self.query_one("#log", Log)
            
            if success:
                self.last_downloaded_file = result
                log.write_line(f"SUCCESS: Saved to {self.last_downloaded_file}")
                self.query_one("#open", Button).disabled = False
            else:
                self.last_downloaded_file = None
                log.write_line(f"ERROR: {result}")
            
            self.query_one("#start", Button).disabled = False
            self.query_one("#bar", ProgressBar).update(progress=0)
            self.is_downloading = False

        def action_open_last_file(self):
            """Action bound to 'o' key and 'Open' button."""
            if self.last_downloaded_file and os.path.exists(self.last_downloaded_file):
                self.query_one("#log", Log).write_line(f"Opening: {self.last_downloaded_file}")
                if not open_file_in_os(self.last_downloaded_file):
                    self.query_one("#log", Log).write_line("Failed to open file automatically.")
            else:
                self.query_one("#log", Log).write_line("Error: No file available to open.")

    YDDL_TUI().run()

# ==========================================
# 4. WEB SERVER INTERFACE MODE
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YD Downloader Web</title>
    <style>
        :root { --main: #00AAFF; --bg: #222; --card: #333; --text: #eee; }
        * { box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
            background: var(--bg); 
            color: var(--text); 
            display: flex; 
            justify-content: center; 
            align-items: center; 
            min-height: 100vh; 
            margin: 0; 
            padding: 20px;
        }
        .card { 
            background: var(--card); 
            padding: 2rem; 
            border-radius: 10px; 
            width: 100%; 
            max-width: 450px; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.5); 
        }
        h2 { color: var(--main); text-align: center; margin-top: 0; }
        .info { 
            text-align: center; 
            color: #888; 
            font-size: 0.85em; 
            margin-bottom: 15px; 
        }
        input[type="text"] { 
            width: 100%; 
            padding: 12px; 
            margin: 10px 0 20px 0; 
            border: 1px solid #555; 
            background: #444; 
            color: white; 
            border-radius: 5px; 
            font-size: 14px;
        }
        .options { 
            display: flex; 
            justify-content: space-around; 
            margin-bottom: 20px; 
        }
        .options label { cursor: pointer; }
        button { 
            width: 100%; 
            padding: 14px; 
            background: var(--main); 
            color: #000; 
            border: none; 
            font-weight: bold; 
            border-radius: 5px; 
            cursor: pointer; 
            font-size: 16px;
            transition: background 0.2s;
        }
        button:hover { background: #0099DD; }
        button:disabled { background: #555; cursor: not-allowed; }
        #progress-area { margin-top: 20px; display: none; }
        .progress-bar { 
            height: 12px; 
            background: #555; 
            border-radius: 6px; 
            overflow: hidden; 
        }
        .fill { 
            height: 100%; 
            background: var(--main); 
            width: 0%; 
            transition: width 0.3s ease; 
        }
        #status { 
            text-align: center; 
            margin-top: 12px; 
            font-size: 0.9em; 
            color: #aaa;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>YD Downloader</h2>
        <div class="info" id="pathInfo">Files saved to: Loading...</div>
        <input type="text" id="url" placeholder="Paste Video URL" required>
        <div class="options">
            <label><input type="radio" name="fmt" value="video" checked> Video (MP4)</label>
            <label><input type="radio" name="fmt" value="audio"> Audio (MP3)</label>
        </div>
        <button id="downloadBtn" onclick="startDownload()">Start Download</button>

        <div id="progress-area">
            <div class="progress-bar"><div class="fill" id="fill"></div></div>
            <div id="status">Ready</div>
        </div>
    </div>

    <script>
        // Get default path from server
        fetch('/info')
            .then(r => r.json())
            .then(data => {
                document.getElementById('pathInfo').innerText = 'Files saved to: ' + data.default_path;
            })
            .catch(() => {
                document.getElementById('pathInfo').innerText = 'Files saved to default location';
            });

        let ws;
        function startDownload() {
            const url = document.getElementById('url').value.trim();
            const fmt = document.querySelector('input[name="fmt"]:checked').value;
            if(!url) {
                alert("Please enter a URL");
                return;
            }

            document.getElementById('progress-area').style.display = 'block';
            document.getElementById('status').innerText = "Connecting...";
            document.getElementById('downloadBtn').disabled = true;
            document.getElementById('fill').style.width = '0%';

            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            ws = new WebSocket(`${protocol}://${window.location.host}/ws`);

            ws.onopen = () => {
                ws.send(JSON.stringify({url: url, format: fmt}));
                document.getElementById('status').innerText = 'Download started...';
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if(data.type === 'progress') {
                    document.getElementById('fill').style.width = data.percent + '%';
                    document.getElementById('status').innerText = `${data.status} | ${Math.round(data.percent)}%`;
                } else if (data.type === 'done') {
                    document.getElementById('status').innerText = "Download COMPLETE! File saved to: " + data.path;
                    document.getElementById('fill').style.width = "100%";
                    document.getElementById('downloadBtn').disabled = false;
                } else if (data.type === 'error') {
                    document.getElementById('status').innerText = "ERROR: " + data.msg;
                    document.getElementById('downloadBtn').disabled = false;
                }
            };
            
            ws.onerror = () => {
                document.getElementById('status').innerText = "Connection error occurred.";
                document.getElementById('downloadBtn').disabled = false;
            };
            
            ws.onclose = () => {
                console.log("WebSocket closed.");
                document.getElementById('downloadBtn').disabled = false;
            };
        }
    </script>
</body>
</html>
"""
def run_web_app():
    if not FASTAPI_OK:
        print("ERROR: FastAPI dependencies are missing. Run: pip install fastapi uvicorn")
        sys.exit(1)

    app = FastAPI()
    engine = DownloaderEngine()

    @app.get("/", response_class=HTMLResponse)
    async def get():
        return HTML_TEMPLATE

    @app.get("/info")
    async def get_info():
        return {"default_path": get_default_download_path()}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            data = await websocket.receive_text()
            req = json.loads(data)
            url = req.get('url', '').strip()
            fmt = req.get('format', 'video')
            
            if not url:
                await websocket.send_json({"type": "error", "msg": "No URL provided"})
                return
            
            path = get_default_download_path()
            loop = asyncio.get_event_loop()

            def web_hook(d):
                if d['status'] == 'downloading':
                    try:
                        percent_str = d.get('_percent_str', '0%').replace('%', '').strip()
                        percent = float(percent_str)
                        asyncio.run_coroutine_threadsafe(
                            websocket.send_json({"type": "progress", "percent": percent, "status": "Downloading..."}),
                            loop
                        )
                    except (ValueError, TypeError):
                        pass
                elif d['status'] == 'finished':
                    asyncio.run_coroutine_threadsafe(
                        websocket.send_json({"type": "progress", "percent": 100, "status": "Processing..."}),
                        loop
                    )

            success, result = await loop.run_in_executor(
                None, lambda: engine.download(url, fmt, path, web_hook)
            )

            if success:
                await websocket.send_json({"type": "done", "path": result})
            else:
                await websocket.send_json({"type": "error", "msg": result})

        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "msg": "Invalid request format"})
        except Exception as e:
            await websocket.send_json({"type": "error", "msg": str(e)})
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    print(f"📁 Default download location: {get_default_download_path()}")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")

# ==========================================
# 5. MAIN ENTRY POINT
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="YD Downloader: Terminal and Web App")
    parser.add_argument("--web", action="store_true", help="Launch Local Web Server Interface")
    args = parser.parse_args()

    print(f"📁 Default download location: {get_default_download_path()}")
    
    if args.web:
        print("🌐 Launching Web Server Mode at http://127.0.0.1:8000")
        print("Press Ctrl+C to stop the server.")
        run_web_app()
    else:
        print("💻 Launching Terminal UI Mode...")
        run_tui_app()

if __name__ == "__main__":
    main()