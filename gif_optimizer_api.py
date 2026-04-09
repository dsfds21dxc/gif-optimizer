#!/usr/bin/env python3
"""GIF Optimizer API v4 — Creavite-style: 10fps + ffmpeg palettegen."""

import os
import subprocess
import tempfile
import uuid
import json
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "GIF Optimizer API", "version": "4.0"})

@app.route("/health", methods=["GET"])
def health():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return jsonify({"status": "ok", "ffmpeg": r.stdout.split("\n")[0]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/optimize", methods=["POST"])
def optimize_gif():
    if "gif" not in request.files:
        return jsonify({"error": "No gif file"}), 400

    gif_file = request.files["gif"]
    target_fps = int(request.args.get("fps", 10))
    colors = min(256, max(2, int(request.args.get("colors", 256))))
    lossy = int(request.args.get("lossy", 80))

    work_dir = tempfile.mkdtemp()
    uid = str(uuid.uuid4())[:8]
    input_path = os.path.join(work_dir, f"{uid}_in.gif")
    palette_path = os.path.join(work_dir, f"{uid}_pal.png")
    output_path = os.path.join(work_dir, f"{uid}_out.gif")

    try:
        gif_file.save(input_path)
        input_size = os.path.getsize(input_path)

        # Pass 1: fps reduction + palette generation (diff mode for animations)
        p1_cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"fps={target_fps},palettegen=max_colors={colors}:stats_mode=diff",
            palette_path
        ]
        r1 = subprocess.run(p1_cmd, capture_output=True, text=True, timeout=60)
        if r1.returncode != 0:
            return jsonify({"error": "Palette failed", "stderr": r1.stderr[-300:]}), 500

        # Pass 2: apply palette with bayer dithering + rectangle diff
        p2_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", palette_path,
            "-lavfi", f"[0:v]fps={target_fps}[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-loop", "0",
            output_path
        ]
        r2 = subprocess.run(p2_cmd, capture_output=True, text=True, timeout=120)
        if r2.returncode != 0:
            return jsonify({"error": "Optimize failed", "stderr": r2.stderr[-300:]}), 500

        output_size = os.path.getsize(output_path)

        # Gifsicle pass for extra compression
        if lossy > 0:
            try:
                lossy_path = os.path.join(work_dir, f"{uid}_final.gif")
                lr = subprocess.run(
                    ["gifsicle", "-O3", f"--lossy={lossy}", output_path, "-o", lossy_path],
                    capture_output=True, text=True, timeout=60
                )
                if lr.returncode == 0:
                    ls = os.path.getsize(lossy_path)
                    if ls < output_size:
                        output_path = lossy_path
                        output_size = ls
            except FileNotFoundError:
                pass

        resp = send_file(output_path, mimetype="image/gif",
                        as_attachment=True, download_name="optimized.gif")
        resp.headers["X-Original-Size"] = str(input_size)
        resp.headers["X-Optimized-Size"] = str(output_size)
        resp.headers["X-Output-FPS"] = str(target_fps)
        resp.headers["X-Savings"] = f"{max(0, 100-round(output_size/input_size*100))}%"
        resp.headers["Access-Control-Expose-Headers"] = "X-Original-Size, X-Optimized-Size, X-Savings, X-Output-FPS"
        return resp

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        import glob
        for fp in glob.glob(os.path.join(work_dir, "*")):
            try: os.remove(fp)
            except: pass
        try: os.rmdir(work_dir)
        except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
