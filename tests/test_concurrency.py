import asyncio

from services.concurrency import assistant_turn


def test_ai_concurrency_limit():
    async def run() -> int:
        active = 0
        peak = 0

        async def task() -> None:
            nonlocal active, peak
            async with assistant_turn(2):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(task() for _ in range(6)))
        return peak

    assert asyncio.run(run()) <= 2
