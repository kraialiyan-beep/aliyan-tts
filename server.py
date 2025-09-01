import os
import uuid
import asyncio
import tempfile
from typing import List

from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pydub import AudioSegment
import edge_tts

app = FastAPI(title="Aliyan TTS")
app.mount("/", StaticFiles(directory="web", html=True), name="web")

# --- Helpers ---
MAX_CHARS = 1800  # safe per-request size for Edge TTS
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "en-US-AriaNeural")

# Split large text into smaller chunks
def split_text(text: str, limit: int = MAX_CHARS) -> List[str]:
    text = " ".join(text.split())  # normalize whitespace
    chunks, buf = [], []
    cur = 0
    for token in text.split(" "):
        if cur + len(token) + 1 > limit:
            chunks.append(" ".join(buf))
            buf, cur = [token], len(token) + 1
        else:
            buf.append(token)
            cur += len(token) + 1
    if buf:
        chunks.append(" ".join(buf))
    return chunks

async def synth_chunk(text: str, voice: str, rate: str, pitch: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(out_path)

async def synth_all(chunks: List[str], voice: str, rate: str, pitch: str, workdir: str) -> List[str]:
    tasks, paths = [], []
    for i, ch in enumerate(chunks):
        p = os.path.join(workdir, f"part_{i:04d}.mp3")
        paths.append(p)
        tasks.append(synth_chunk(ch, voice, rate, pitch, p))
    await asyncio.gather(*tasks)
    return paths

def merge_mp3(paths: List[str], per_gap_ms: int = 300) -> AudioSegment:
    audio = AudioSegment.silent(duration=0)
    gap = AudioSegment.silent(duration=per_gap_ms)
    for i, p in enumerate(paths):
        audio += AudioSegment.from_file(p)
        if i < len(paths) - 1:
            audio += gap
    return audio

@app.post("/api/synthesize")
async def synthesize(
    text: str = Form(...),
    voice: str = Form(DEFAULT_VOICE),
    rate: str = Form("+0%"),
    pitch: str = Form("+0Hz")
):
    if not text.strip():
        return JSONResponse({"error": "Empty text"}, status_code=400)

    jobid = uuid.uuid4().hex
    out_dir = os.path.join(tempfile.gettempdir(), f"aliyan_tts_{jobid}")
    os.makedirs(out_dir, exist_ok=True)

    chunks = split_text(text)
    mp3_parts = await synth_all(chunks, voice, rate, pitch, out_dir)
    final_audio = merge_mp3(mp3_parts, per_gap_ms=300)

    out_mp3 = os.path.join(out_dir, f"aliyan_{jobid}.mp3")
    final_audio.export(out_mp3, format="mp3", bitrate="128k")

    return {"download": f"/api/download/{jobid}", "seconds": len(final_audio) / 1000.0}

@app.get("/api/download/{jobid}")
def download(jobid: str):
    out_dir = os.path.join(tempfile.gettempdir(), f"aliyan_tts_{jobid}")
    for name in os.listdir(out_dir):
        if name.endswith(".mp3"):
            path = os.path.join(out_dir, name)
            return FileResponse(path, media_type="audio/mpeg", filename=name)
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/api/voices")
async def voices():
    # Short starter list (stable). Later we can add full 300+ voices
    return [
        "en-US-AriaNeural", "en-US-GuyNeural", "en-GB-LibbyNeural", "en-GB-RyanNeural",
        "hi-IN-SwaraNeural", "hi-IN-MadhurNeural",
        "ur-PK-UzmaNeural", "ur-PK-AsadNeural",
        "es-ES-ElviraNeural", "es-ES-AlvaroNeural",
    ]
