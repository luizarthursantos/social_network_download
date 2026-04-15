import os
import uuid
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, after_this_request
import yt_dlp

app = Flask(__name__)

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Clean up files older than 10 minutes
def cleanup_old_files():
    while True:
        time.sleep(60)
        cutoff = time.time() - 600
        for f in DOWNLOAD_DIR.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)

threading.Thread(target=cleanup_old_files, daemon=True).start()


def build_ydl_opts(output_path: str, session_id: str | None) -> dict:
    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    if session_id:
        # Write a minimal Netscape cookie file for Instagram auth
        cookie_file = str(DOWNLOAD_DIR / f"{uuid.uuid4().hex}.txt")
        with open(cookie_file, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(
                ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\t"
                + session_id.strip()
                + "\n"
            )
        opts["cookiefile"] = cookie_file
        opts["_cookie_file_to_delete"] = cookie_file  # custom key for cleanup
    return opts


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    session_id = (data.get("session_id") or "").strip() or None

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if "instagram.com" not in url:
        return jsonify({"error": "Only Instagram URLs are supported"}), 400

    file_id = uuid.uuid4().hex
    output_template = str(DOWNLOAD_DIR / f"{file_id}.%(ext)s")

    opts = build_ydl_opts(output_template, session_id)
    cookie_file = opts.pop("_cookie_file_to_delete", None)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Resolve the actual filename written to disk
            filename = ydl.prepare_filename(info)
            # yt-dlp may merge into .mp4 even when template says .webm
            if not Path(filename).exists():
                # Try mp4 fallback
                filename = str(DOWNLOAD_DIR / f"{file_id}.mp4")
            if not Path(filename).exists():
                # Glob for any file with the id prefix
                matches = list(DOWNLOAD_DIR.glob(f"{file_id}.*"))
                if not matches:
                    return jsonify({"error": "Download failed: output file not found"}), 500
                filename = str(matches[0])
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "login" in msg.lower() or "authentication" in msg.lower() or "requires" in msg.lower():
            return jsonify({"error": "Instagram requires authentication. Please provide your session ID."}), 403
        return jsonify({"error": f"Download error: {msg}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
    finally:
        if cookie_file and Path(cookie_file).exists():
            Path(cookie_file).unlink(missing_ok=True)

    ext = Path(filename).suffix.lstrip(".")
    download_name = f"instagram_story.{ext}"

    @after_this_request
    def remove_file(response):
        try:
            Path(filename).unlink(missing_ok=True)
        except Exception:
            pass
        return response

    return send_file(
        filename,
        as_attachment=True,
        download_name=download_name,
        mimetype="video/mp4",
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
