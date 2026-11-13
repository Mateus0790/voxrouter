"""Divisão de textos longos e concatenação de áudio."""

from __future__ import annotations

import io
import re
import wave

#: formatos cujos pedaços podem ser concatenados com segurança
CONCAT_FORMATS = frozenset({"mp3", "wav", "pcm", "aac"})
#: formatos que podem ser emendados durante o streaming (sem cabeçalho global)
STREAM_CONCAT_FORMATS = frozenset({"mp3", "pcm", "aac"})

_SENTENCE_RE = re.compile(r"(?<=[.!?…;])\s+|\n+")
_SOFT_RE = re.compile(r"(?<=,)\s+|\s+")


def _pack(tokens: list[str], max_chars: int) -> list[str]:
    out: list[str] = []
    cur = ""
    for tok in tokens:
        while len(tok) > max_chars:  # palavra gigante: corte seco
            if cur:
                out.append(cur)
                cur = ""
            out.append(tok[:max_chars])
            tok = tok[max_chars:]
        if not tok:
            continue
        if not cur:
            cur = tok
        elif len(cur) + 1 + len(tok) <= max_chars:
            cur += " " + tok
        else:
            out.append(cur)
            cur = tok
    if cur:
        out.append(cur)
    return out


def split_text(text: str, max_chars: int | None) -> list[str]:
    """Divide por frases (depois vírgulas/espaços) respeitando `max_chars`."""
    text = text.strip()
    if not max_chars or len(text) <= max_chars:
        return [text]
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s and s.strip()]
    tokens: list[str] = []
    for s in sentences:
        if len(s) <= max_chars:
            tokens.append(s)
        else:
            tokens.extend(_pack([t for t in _SOFT_RE.split(s) if t], max_chars))
    return _pack(tokens, max_chars)


def strip_id3(data: bytes) -> bytes:
    """Remove a tag ID3v2 do início de um MP3 (evita lixo entre pedaços)."""
    if len(data) >= 10 and data[:3] == b"ID3":
        b = data[6:10]
        size = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
        footer = 10 if data[5] & 0x10 else 0
        return data[10 + size + footer:]
    return data


def concat_wav(chunks: list[bytes]) -> bytes:
    params = None
    frames = bytearray()
    for c in chunks:
        with wave.open(io.BytesIO(c), "rb") as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise ValueError(f"pedaços WAV incompatíveis: {p} != {params}")
            frames.extend(w.readframes(w.getnframes()))
    assert params is not None
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(bytes(frames))
    return out.getvalue()


def concat_audio(chunks: list[bytes], fmt: str) -> bytes:
    if len(chunks) == 1:
        return chunks[0]
    if fmt == "mp3":
        return chunks[0] + b"".join(strip_id3(c) for c in chunks[1:])
    if fmt in ("pcm", "aac"):
        return b"".join(chunks)
    if fmt == "wav":
        return concat_wav(chunks)
    raise ValueError(f"concatenação não suportada para o formato '{fmt}' (use mp3, wav, pcm ou aac)")
