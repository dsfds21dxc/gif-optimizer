#!/usr/bin/env python3
"""
GIF Optimizer API — uses ffmpeg palettegen/paletteuse for maximum quality compression.
Deploy on Railway, Render, or Fly.io (free tier).

Requirements: pip install flask flask-cors
System: ffmpeg must be installed (apt-get install ffmpeg)
"""

import os
import subprocess
import tempfile
import uuid
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allow requests from your banner maker

MAX_SIZE = 20 * 1024 * 1024  # 20MB max upload

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return jsonify({"status": "ok", "ffmpeg": result.stdout.split("\n")[0]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/optimize", methods=["POST"])
def optimize_gif():
    """
    Accepts a GIF file, optimizes it with ffmpeg palettegen/paletteuse.
    Returns the optimized GIF.

    Usage: POST /optimize with multipart form data, field name "gif"
    Optional query params:
      - colors: palette size (default 128, max 256)
      - dither: dithering method (default "sierra2_4a", options: none, bayer, sierra2_4a, floyd_steinberg)
      - lossy: enable lossy mode for extra compression (default false)
    """
    if "gif" not in request.files:
        return jsonify({"error": "No GIF file provided. Send as multipart with field 'gif'"}), 400

    gif_file = request.files["gif"]
    if gif_file.content_length and gif_file.content_length > MAX_SIZE:
        return jsonify({"error": f"File too large (max {MAX_SIZE // 1024 // 1024}MB)"}), 413

    colors = min(256, max(2, int(request.args.get("colors", 128))))
    dither = request.args.get("dither", "sierra2_4a")

    # Validate dither method
    valid_dithers = ["none", "bayer", "sierra2_4a", "floyd_steinberg", "heckbert"]
    if dither not in valid_dithers:
        dither = "sierra2_4a"

    work_dir = tempfile.mkdtemp()
    uid = str(uuid.uuid4())[:8]
    input_path = os.path.join(work_dir, f"{uid}_input.gif")
    palette_path = os.path.join(work_dir, f"{uid}_palette.png")
    output_path = os.path.join(work_dir, f"{uid}_output.gif")

    try:
        # Save uploaded GIF
        gif_file.save(input_path)
        input_size = os.path.getsize(input_path)

        # Pass 1: Generate optimal palette
        palette_cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"palettegen=max_colors={colors}:stats_mode=diff",
            palette_path
        ]
        result = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": "Palette generation failed", "detail": result.stderr[-500:]}), 500

        # Pass 2: Apply palette with dithering
        dither_opt = f"dither={dither}" if dither != "none" else "dither=none"
        if dither == "bayer":
            dither_opt = "dither=bayer:bayer_scale=3"

        optimize_cmd = [
            "ffmpeg", "-y", "-i", input_path, "-i", palette_path,
            "-lavfi", f"paletteuse={dither_opt}:diff_mode=rectangle",
            "-gifflags", "+transdiff",
            output_path
        ]
        result = subprocess.run(optimize_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return jsonify({"error": "Optimization failed", "detail": result.stderr[-500:]}), 500

        output_size = os.path.getsize(output_path)

        response = send_file(
            output_path,
            mimetype="image/gif",
            as_attachment=True,
            download_name="optimized.gif"
        )
        response.headers["X-Original-Size"] = str(input_size)
        response.headers["X-Optimized-Size"] = str(output_size)
        response.headers["X-Compression-Ratio"] = f"{output_size/input_size:.2%}"

        return response

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Processing timed out (max 120s)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Cleanup
        for f in [input_path, palette_path, output_path]:
            try: os.remove(f)
            except: pass
        try: os.rmdir(work_dir)
        except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
