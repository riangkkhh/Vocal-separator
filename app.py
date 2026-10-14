"""
Vocal / Instrumental separator - backend on Modal.

One file holds everything:
  * Separate  - GPU worker (BS-Roformer + MDX23C ensemble)
  * web       - HTTP API (upload / status / download / quota)
  * cleanup   - hourly job that deletes old files

Deployed automatically by GitHub Actions (see .github/workflows/deploy-backend.yml).
"""
import modal

# ----------------------------- settings --------------------------------------
APP_NAME = "vocal-separator"

MONTHLY_LIMIT = 300          # global cap of songs per month (all users together)
COOLDOWN_DAYS = 7            # one song per user per week (by hashed IP)
MAX_UPLOAD_MB = 40
MAX_DURATION_SEC = 8 * 60    # 8 minutes
RETENTION_SEC = 2 * 3600     # delete files after 2 hours
IP_SALT = "change-me-to-any-random-text"

MODEL_A = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"   # best vocal separation
MODEL_B = "MDX23C-8KFFT-InstVoc_HQ.ckpt"               # second opinion for ensemble
WEIGHT_A, WEIGHT_B = 0.6, 0.4

ALLOWED_EXT = {"mp3", "wav", "flac", "m4a", "ogg", "aac", "opus", "wma", "mp4"}

# ----------------------------- shared objects --------------------------------
app = modal.App(APP_NAME)
files_vol = modal.Volume.from_name("vocal-sep-files", create_if_missing=True)
state = modal.Dict.from_name("vocal-sep-state", create_if_missing=True)

web_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("fastapi[standard]", "python-multipart")
)


def _download_models():
    """Runs once at image build time, so models are baked into the image."""
    from audio_separator.separator import Separator

    for name in (MODEL_A, MODEL_B):
        s = Separator(model_file_dir="/models")
        s.load_model(model_filename=name)


gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install("audio-separator[gpu]", "numpy", "soundfile")
    .run_function(_download_models)
)


# ----------------------------- helpers ---------------------------------------
def _month_key():
    import time

    return "quota:" + time.strftime("%Y-%m", time.gmtime())


def _set_job(job_id, **fields):
    key = "job:" + job_id
    try:
        job = state[key]
    except KeyError:
        job = {}
    job.update(fields)
    state[key] = job


def _refund(job_id):
    """Failed job -> give back the monthly slot and the user's weekly slot."""
    try:
        job = state["job:" + job_id]
    except KeyError:
        return
    qk = _month_key()
    try:
        state[qk] = max(0, state[qk] - 1)
    except KeyError:
        pass
    try:
        state.pop(job.get("ip_key", ""))
    except KeyError:
        pass


# ----------------------------- GPU worker ------------------------------------
@app.cls(
    image=gpu_image,
    gpu="T4",
    volumes={"/data": files_vol},
    timeout=900,
    max_containers=2,      # never more than 2 GPUs at once = cost ceiling
    scaledown_window=20,
)
class Separate:
    @modal.enter()
    def load(self):
        import os
        from audio_separator.separator import Separator

        self.seps, self.dirs = {}, {}
        for key, name in (("a", MODEL_A), ("b", MODEL_B)):
            out = f"/tmp/out_{key}"
            os.makedirs(out, exist_ok=True)
            sep = Separator(model_file_dir="/models", output_dir=out, output_format="WAV")
            sep.load_model(model_filename=name)
            self.seps[key], self.dirs[key] = sep, out

    @modal.method()
    def run(self, job_id: str):
        import glob
        import os
        import subprocess

        import numpy as np
        import soundfile as sf

        job_dir = f"/data/jobs/{job_id}"
        wav_in = f"/tmp/{job_id}.wav"
        try:
            files_vol.reload()
            _set_job(job_id, status="processing", step="מכין את הקובץ")
            src = glob.glob(f"{job_dir}/input.*")[0]
            subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-vn", "-ac", "2", "-ar", "44100", wav_in],
                check=True, capture_output=True,
            )

            results = {}
            for key, label in (("a", "הפרדה ראשית (מודל 1)"), ("b", "הפרדה משלימה (מודל 2)")):
                try:
                    _set_job(job_id, step=label)
                    for f in glob.glob(self.dirs[key] + "/*"):
                        os.remove(f)
                    self.seps[key].separate(wav_in)
                    stems = {}
                    for f in glob.glob(self.dirs[key] + "/*.wav"):
                        low = os.path.basename(f).lower()
                        name = "vocals" if "(vocals)" in low else "instrumental" if "(instrumental)" in low else None
                        if name:
                            data, sr = sf.read(f, dtype="float32", always_2d=True)
                            if sr == 44100:
                                stems[name] = data
                    if "vocals" in stems and "instrumental" in stems:
                        results[key] = stems
                except Exception as e:  # if one model fails, the other still works
                    print(f"model {key} failed:", e)
            if not results:
                raise RuntimeError("ההפרדה נכשלה")

            _set_job(job_id, step="מחבר ומנקה את התוצאה")
            weights = {"a": WEIGHT_A, "b": WEIGHT_B}
            total_w = sum(weights[k] for k in results)
            for stem in ("vocals", "instrumental"):
                n = min(len(results[k][stem]) for k in results)
                mix = sum(results[k][stem][:n] * (weights[k] / total_w) for k in results)
                peak = float(np.abs(mix).max())
                if peak > 0.98:                       # avoid clipping
                    mix = mix * (0.98 / peak)
                wav_out = f"{job_dir}/{stem}.wav"
                sf.write(wav_out, mix, 44100, subtype="PCM_24")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", wav_out, "-b:a", "320k", f"{job_dir}/{stem}.mp3"],
                    check=True, capture_output=True,
                )

            for f in glob.glob(f"{job_dir}/input.*"):
                os.remove(f)
            files_vol.commit()
            _set_job(job_id, status="done", step="מוכן")
        except Exception as e:
            print("job failed:", e)
            _refund(job_id)
            _set_job(job_id, status="error", step="", error="אירעה שגיאה בעיבוד. נסו שוב מאוחר יותר.")
        finally:
            if os.path.exists(wav_in):
                os.remove(wav_in)


# ----------------------------- web API ---------------------------------------
@app.function(
    image=web_image,
    volumes={"/data": files_vol},
    max_containers=3,
    timeout=300,
)
@modal.asgi_app()
def web():
    import hashlib
    import os
    import re
    import subprocess
    import time
    import uuid

    from fastapi import FastAPI, File, HTTPException, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse

    api = FastAPI()
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @api.get("/api/quota")
    def quota():
        used = state.get(_month_key(), 0)
        return {"used": used, "limit": MONTHLY_LIMIT, "remaining": max(0, MONTHLY_LIMIT - used)}

    @api.post("/api/upload")
    async def upload(request: Request, file: UploadFile = File(...)):
        now = time.time()
        ip_key = "ip:" + hashlib.sha256((IP_SALT + client_ip(request)).encode()).hexdigest()[:24]

        last = state.get(ip_key, 0)
        if now - last < COOLDOWN_DAYS * 86400:
            days = int((COOLDOWN_DAYS * 86400 - (now - last)) // 86400) + 1
            raise HTTPException(429, f"אפשר להעלות שיר אחד בשבוע. נסו שוב בעוד כ-{days} ימים.")

        qk = _month_key()
        if state.get(qk, 0) >= MONTHLY_LIMIT:
            raise HTTPException(503, "המכסה החודשית של האתר הסתיימה. נסו שוב בתחילת החודש הבא.")

        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, "סוג קובץ לא נתמך. אפשר MP3, WAV, FLAC, M4A, OGG.")

        job_id = uuid.uuid4().hex
        job_dir = f"/data/jobs/{job_id}"
        os.makedirs(job_dir, exist_ok=True)
        path = f"{job_dir}/input.{ext}"

        size = 0
        with open(path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    out.close()
                    os.remove(path)
                    raise HTTPException(413, f"הקובץ גדול מדי (מקסימום {MAX_UPLOAD_MB}MB).")
                out.write(chunk)

        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True, check=True,
            )
            duration = float(probe.stdout.strip())
        except Exception:
            os.remove(path)
            raise HTTPException(400, "לא הצלחנו לקרוא את קובץ האודיו.")
        if duration > MAX_DURATION_SEC:
            os.remove(path)
            raise HTTPException(400, f"השיר ארוך מדי (מקסימום {MAX_DURATION_SEC // 60} דקות).")

        state[ip_key] = now
        state[qk] = state.get(qk, 0) + 1
        state["job:" + job_id] = {
            "status": "queued", "step": "בתור", "created": now, "ip_key": ip_key,
        }
        files_vol.commit()
        Separate().run.spawn(job_id)
        return {"job_id": job_id}

    @api.get("/api/status/{job_id}")
    def status(job_id: str):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(404)
        try:
            job = state["job:" + job_id]
        except KeyError:
            raise HTTPException(404, "המשימה לא נמצאה או שפג תוקפה.")
        return {k: job.get(k) for k in ("status", "step", "error")}

    @api.get("/api/download/{job_id}/{stem}")
    def download(job_id: str, stem: str, fmt: str = "mp3", dl: int = 0):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id) or stem not in ("vocals", "instrumental") or fmt not in ("mp3", "wav"):
            raise HTTPException(404)
        files_vol.reload()
        path = f"/data/jobs/{job_id}/{stem}.{fmt}"
        if not os.path.exists(path):
            raise HTTPException(404, "הקובץ לא נמצא או שפג תוקפו.")
        names = {"vocals": "vocals", "instrumental": "playback"}
        media = "audio/mpeg" if fmt == "mp3" else "audio/wav"
        return FileResponse(path, media_type=media, filename=f"{names[stem]}.{fmt}" if dl else None)

    return api


# ----------------------------- cleanup ---------------------------------------
@app.function(
    image=web_image,
    volumes={"/data": files_vol},
    schedule=modal.Period(hours=1),
)
def cleanup():
    import os
    import shutil
    import time

    files_vol.reload()
    root = "/data/jobs"
    now = time.time()
    if os.path.isdir(root):
        for d in os.listdir(root):
            p = os.path.join(root, d)
            try:
                created = state["job:" + d]["created"]
            except KeyError:
                created = os.path.getmtime(p)
            if now - created > RETENTION_SEC:
                shutil.rmtree(p, ignore_errors=True)
                try:
                    state.pop("job:" + d)
                except KeyError:
                    pass
        files_vol.commit()

    # forget old IP records (older than the cooldown)
    try:
        for k in list(state.keys()):
            if k.startswith("ip:") and now - state[k] > COOLDOWN_DAYS * 86400:
                state.pop(k)
    except Exception as e:
        print("ip cleanup skipped:", e)
