#!/usr/bin/env python3
"""
GIF Optimizer API v2 — uses ffmpeg 2-pass palettegen/paletteuse.
Deploy on Railway with Docker.
"""

import os
import subprocess
import tempfile
import uuid
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

MAX_SIZE = 20 * 1024 * 1024

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "GIF Optimizer API",
        "version": "2.0",
        "endpoints": {
            "/health": "GET - check ffmpeg status",
            "/optimize": "POST - optimize a GIF (multipart, field: gif)"
        }
    })

@app.route("/health", methods=["GET"])
def health():
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return jsonify({"status": "ok", "ffmpeg": result.stdout.split("\n")[0]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/optimize", methods=["POST"])
def optimize_gif():
    if "gif" not in request.files:
        return jsonify({"error": "No GIF file. Send multipart with field 'gif'"}), 400

    gif_file = request.files["gif"]
    colors = min(256, max(2, int(request.args.get("colors", 128))))
    dither = request.args.get("dither", "sierra2_4a")
    lossy = request.args.get("lossy", "0")

    valid_dithers = ["none", "bayer", "sierra2_4a", "floyd_steinberg", "heckbert"]
    if dither not in valid_dithers:
        dither = "sierra2_4a"

    work_dir = tempfile.mkdtemp()
    uid = str(uuid.uuid4())[:8]
    input_path = os.path.join(work_dir, f"{uid}_in.gif")
    palette_path = os.path.join(work_dir, f"{uid}_pal.png")
    output_path = os.path.join(work_dir, f"{uid}_out.gif")

    try:
        gif_file.save(input_path)
        input_size = os.path.getsize(input_path)

        # Get input info (fps, dimensions)
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
            capture_output=True, text=True, timeout=10
        )

        # Pass 1: Generate optimal palette
        # stats_mode=diff: only count pixels that change between frames
        # This gives much better palette usage for animations
        palette_cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"palettegen=max_colors={colors}:stats_mode=diff:reserve_transparent=on",
            palette_path
        ]
        r1 = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=60)
        if r1.returncode != 0:
            # Fallback: try without reserve_transparent
            palette_cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", f"palettegen=max_colors={colors}:stats_mode=diff",
                palette_path
            ]
            r1 = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=60)
            if r1.returncode != 0:
                return jsonify({"error": "Palette generation failed", "detail": r1.stderr[-300:]}), 500

        # Pass 2: Apply palette with optimal settings
        if dither == "bayer":
            dither_str = "dither=bayer:bayer_scale=3"
        elif dither == "none":
            dither_str = "dither=none"
        else:
            dither_str = f"dither={dither}"

        # diff_mode=rectangle: only re-encode changed rectangular region per frame
        # This is the KEY optimization that reduces file size dramatically
        optimize_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", palette_path,
            "-lavfi", f"[0:v][1:v]paletteuse={dither_str}:diff_mode=rectangle",
            "-loop", "0",
            output_path
        ]
        r2 = subprocess.run(optimize_cmd, capture_output=True, text=True, timeout=120)
        if r2.returncode != 0:
            return jsonify({"error": "Optimization failed", "detail": r2.stderr[-300:]}), 500

        output_size = os.path.getsize(output_path)

        # If lossy mode requested, try gifsicle too (if available)
        if lossy != "0":
            try:
                lossy_path = os.path.join(work_dir, f"{uid}_lossy.gif")
                lossy_val = min(200, max(30, int(lossy)))
                lr = subprocess.run(
                    ["gifsicle", "-O3", f"--lossy={lossy_val}", output_path, "-o", lossy_path],
                    capture_output=True, text=True, timeout=60
                )
                if lr.returncode == 0 and os.path.getsize(lossy_path) < output_size:
                    output_path = lossy_path
                    output_size = os.path.getsize(lossy_path)
            except FileNotFoundError:
                pass  # gifsicle not installed, skip

        response = send_file(
            output_path,
            mimetype="image/gif",
            as_attachment=True,
            download_name="optimized.gif"
        )
        response.headers["X-Original-Size"] = str(input_size)
        response.headers["X-Optimized-Size"] = str(output_size)
        response.headers["X-Savings"] = f"{max(0, 100 - round(output_size/input_size*100))}%"
        response.headers["Access-Control-Expose-Headers"] = "X-Original-Size, X-Optimized-Size, X-Savings"

        return response

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Processing timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for f_path in [input_path, palette_path, output_path]:
            try: os.remove(f_path)
            except: pass
        try: 
            # Clean up any lossy file too
            import glob
            for f_path in glob.glob(os.path.join(work_dir, "*")):
                try: os.remove(f_path)
                except: pass
            os.rmdir(work_dir)
        except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
