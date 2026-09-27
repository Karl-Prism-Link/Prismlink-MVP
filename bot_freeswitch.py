"""Top-level module wrapper for apps.voice.bot_freeswitch."""

from apps.voice.bot_freeswitch import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
