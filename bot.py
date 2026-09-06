import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()  # reads TOKEN from a local .env file

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix=",", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


async def load_cogs():
    """Auto-load every .py file in cogs/ as an extension.
    To add a new feature later: drop a new file in cogs/ with a
    `setup(bot)` function (see cogs/sobs.py as a template) — nothing
    else needs to change here."""
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith("_"):
            extension = f"cogs.{filename[:-3]}"
            try:
                await bot.load_extension(extension)
                print(f"Loaded extension: {extension}")
            except Exception as e:
                print(f"Failed to load extension {extension}: {e}")


async def main():
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set. Copy .env.example to .env and fill in your token.")
    asyncio.run(main())
