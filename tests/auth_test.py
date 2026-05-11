# test_auth.py  (run from the project root)
import asyncio
import logging
from config.settings import load_sessions

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s"
)

async def main():
    sessions = load_sessions()

    for session in sessions:
        await session.authenticate()
        session.start_background_refresh()

    print("\nAll sessions authenticated. Watching background tasks...")
    print("Press Ctrl+C to stop.\n")

    while True:
        for session in sessions:
            print(session.status())
        await asyncio.sleep(10)

asyncio.run(main())