"""Optional local enhancement; originals and their transcripts remain authoritative."""

import asyncio
import io
import wave
from contextlib import suppress


class EnhancementError(Exception):
    pass


async def enhance_audio(data: bytes) -> bytes:
    """Mild FFT reduction and leveling with exact sample count and delay compensation.

    At 16 kHz afftdn advances 200 samples over a 600-sample window: its output
    lags by 400 samples. Feed a zero tail before removing that delay, including
    for recordings ending in speech. No model, files, network or shell is used.
    """
    with wave.open(io.BytesIO(data), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise EnhancementError("Unsupported enhancement format")
        params = stream.getparams()
        pcm = stream.readframes(stream.getnframes())
    filters = (
        "afftdn=nr=6:nf=-55:tn=0,"
        f"atrim=start_sample=400:end_sample={len(pcm) // 2 + 400},"
        "dynaudnorm=f=250:g=5:p=0.9:m=4"
    )
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-filter_threads", "1",
        "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
        "-af", filters, "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def feed():
        process.stdin.write(pcm + b"\0" * 800)
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

    async def read():
        result = bytearray()
        while part := await process.stdout.read(min(65536, len(pcm) + 1 - len(result))):
            result.extend(part)
            if len(result) > len(pcm):
                raise EnhancementError("Enhancement changed sample count")
        return bytes(result)

    tasks = [asyncio.create_task(feed()), asyncio.create_task(read())]
    try:
        async with asyncio.timeout(5):
            _, enhanced = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode or len(enhanced) != len(pcm):
            raise EnhancementError("Enhancement failed or changed sample count")
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams(params)
        stream.writeframes(enhanced)
    return output.getvalue()
