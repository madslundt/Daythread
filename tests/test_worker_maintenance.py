import asyncio
from contextlib import suppress
from threading import Event, get_ident
from types import SimpleNamespace

from omi_local.profiles import selected_profile
from omi_local.worker import maintenance


def test_retention_io_uses_thread_and_preserves_profile_context():
    async def run():
        loop = asyncio.get_running_loop()
        observed, done = [], asyncio.Event()
        event_loop_thread = get_ident()

        def retention(audio_days, transcript_days):
            observed.append((get_ident(), selected_profile.get(), audio_days, transcript_days))
            loop.call_soon_threadsafe(done.set)

        memory = SimpleNamespace(
            store=SimpleNamespace(apply_retention=retention),
            settings=SimpleNamespace(audio_retention_days=7, transcript_retention_days=30),
        )
        token = selected_profile.set("private-profile")
        task = asyncio.create_task(maintenance(memory))
        selected_profile.reset(token)
        try:
            await asyncio.wait_for(done.wait(), timeout=1)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        thread, profile, audio_days, transcript_days = observed[0]
        assert thread != event_loop_thread
        assert (profile, audio_days, transcript_days) == ("private-profile", 7, 30)

    asyncio.run(run())


def test_shutdown_waits_for_inflight_retention_storage_work():
    async def run():
        entered, release, finished = Event(), Event(), Event()

        def retention(*args):
            entered.set()
            assert release.wait(3)
            finished.set()

        memory = SimpleNamespace(
            store=SimpleNamespace(apply_retention=retention),
            settings=SimpleNamespace(audio_retention_days=7, transcript_retention_days=30),
        )
        task = asyncio.create_task(maintenance(memory))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task
        assert finished.is_set()

    asyncio.run(run())
