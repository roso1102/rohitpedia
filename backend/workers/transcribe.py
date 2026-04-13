from __future__ import annotations

import os
import shutil
import subprocess


def _convert_to_wav_if_needed(file_path: str) -> tuple[str, str | None]:
    lower = file_path.lower()
    if not (lower.endswith(".oga") or lower.endswith(".ogg") or lower.endswith(".opus")):
        return file_path, None

    ffmpeg_bin = os.getenv("FFMPEG_BIN", "").strip() or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return file_path, "missing_ffmpeg_binary"

    wav_path = f"{os.path.splitext(file_path)[0]}.wav"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        file_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        wav_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return file_path, "ffmpeg_exec_error"

    if proc.returncode != 0:
        return file_path, "ffmpeg_convert_failed"
    return wav_path, None


def _normalize_transcript(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    timed = [line.split("] ", 1)[1].strip() for line in lines if "-->" in line and "] " in line]
    if timed:
        return " ".join(timed).strip()
    return text


def transcribe_audio(file_path: str) -> tuple[str | None, str]:
    whisper_bin = os.getenv("WHISPER_BIN", "").strip()
    whisper_model = os.getenv("WHISPER_MODEL_PATH", "").strip()

    binary = whisper_bin or shutil.which("whisper-cpp") or shutil.which("whisper")
    if not binary:
        return None, "missing_whisper_binary"
    if not whisper_model:
        return None, "missing_whisper_model_path"

    input_path, convert_error = _convert_to_wav_if_needed(file_path)
    if convert_error:
        return None, convert_error

    language = os.getenv("WHISPER_LANGUAGE", "en").strip() or "en"
    prompt = os.getenv("WHISPER_INITIAL_PROMPT", "").strip()
    beam_size = os.getenv("WHISPER_BEAM_SIZE", "8").strip() or "8"
    best_of = os.getenv("WHISPER_BEST_OF", "8").strip() or "8"

    cmd = [
        binary,
        "-m",
        whisper_model,
        "-f",
        input_path,
        "-l",
        language,
        "-bs",
        beam_size,
        "-bo",
        best_of,
        "-nt",
    ]
    if prompt:
        cmd.extend(["--prompt", prompt])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return None, "whisper_exec_error"

    if proc.returncode != 0:
        return None, "whisper_failed"

    transcript = _normalize_transcript(proc.stdout or "")
    if not transcript:
        return None, "empty_transcript"
    return transcript, "ok"
