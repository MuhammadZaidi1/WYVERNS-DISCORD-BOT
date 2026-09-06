import discord
from discord.ext import commands

from storage import get_bucket, persist

SOB_EMOJI = "😭"


class Sobs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- helpers -----------------------------------------------------

    def _bucket(self, guild_id: int):
        return get_bucket("sobs", guild_id, default={"members": {}, "messages": {}})

    def _adjust_count(self, guild_id, message_id, author_id, channel_id, delta, content=""):
        bucket = self._bucket(guild_id)
        mid = str(message_id)
        aid = str(author_id)

        entry = bucket["messages"].get(mid)
        if entry is None:
            entry = {"author_id": author_id, "channel_id": channel_id, "count": 0, "content": content}
            bucket["messages"][mid] = entry

        entry["count"] = max(0, entry["count"] + delta)
        bucket["members"][aid] = max(0, bucket["members"].get(aid, 0) + delta)

        if entry["count"] == 0:
            del bucket["messages"][mid]

        persist()

    async def _scan_guild(self, guild, status_callback=None):
        bucket = self._bucket(guild.id)
        bucket["members"] = {}
        bucket["messages"] = {}

        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue

            if status_callback:
                await status_callback(f"Scanning #{channel.name}...")

            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    if message.author.bot:
                        continue
                    for reaction in message.reactions:
                        if str(reaction.emoji) != SOB_EMOJI:
                            continue
                        count = reaction.count
                        if count <= 0:
                            continue
                        mid = str(message.id)
                        bucket["messages"][mid] = {
                            "author_id": message.author.id,
                            "channel_id": channel.id,
                            "count": count,
                            "content": message.content[:100],
                        }
                        aid = str(message.author.id)
                        bucket["members"][aid] = bucket["members"].get(aid, 0) + count
            except (discord.Forbidden, discord.HTTPException):
                continue

        persist()

    # -- events --------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != SOB_EMOJI or payload.guild_id is None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if message.author.bot:
            return
        self._adjust_count(
            payload.guild_id, payload.message_id, message.author.id, payload.channel_id,
            +1, content=message.content[:100],
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != SOB_EMOJI or payload.guild_id is None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            bucket = self._bucket(payload.guild_id)
            entry = bucket["messages"].get(str(payload.message_id))
            if entry:
                self._adjust_count(payload.guild_id, payload.message_id, entry["author_id"], payload.channel_id, -1)
            return
        if message.author.bot:
            return
        self._adjust_count(payload.guild_id, payload.message_id, message.author.id, payload.channel_id, -1)

    # -- commands --------------------------------------------------------

    @commands.command(name="sobs")
    async def sobs(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        bucket = self._bucket(ctx.guild.id)
        total = bucket["members"].get(str(member.id), 0)

        embed = discord.Embed(
            title="😭 Sob Counter",
            description=f"**{member.mention}** has received\n\n# 😭 {total:,} sobs\n\non their messages.",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Run `,sob scan` (admin) if this looks out of date.")
        await ctx.send(embed=embed)

    @commands.group(name="sob", invoke_without_command=True)
    async def sob(self, ctx):
        await ctx.send(
            "Use:\n"
            "`,sobs [@user]` — total sob count\n"
            "`,sob leaderboard` — server leaderboard\n"
            "`,sob msg [@user]` — top individual messages by sob count\n"
            "`,sob scan` — (admin) rebuild counts from full channel history"
        )

    @sob.command(name="leaderboard")
    async def sob_leaderboard(self, ctx):
        bucket = self._bucket(ctx.guild.id)
        entries = [(mid, count) for mid, count in bucket["members"].items() if count > 0]
        entries.sort(key=lambda x: x[1], reverse=True)
        entries = entries[:25]

        if not entries:
            await ctx.send("😭 Nobody has received any sob reactions yet! Try `,sob scan` if this seems wrong.")
            return

        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for position, (member_id, total) in enumerate(entries, start=1):
            member = ctx.guild.get_member(int(member_id))
            name = member.mention if member else f"<@{member_id}>"
            rank = medals[position - 1] if position <= 3 else f"`#{position}`"
            description += f"{rank} {name} — **😭 {total:,}**\n"

        embed = discord.Embed(title="😭 Sob Leaderboard", description=description, color=discord.Color.blurple())
        embed.set_footer(text=f"Top {len(entries)} members • Highest → Lowest")
        await ctx.send(embed=embed)

    @sob.command(name="msg")
    async def sob_msg(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        bucket = self._bucket(ctx.guild.id)
        their_messages = [
            (mid, entry) for mid, entry in bucket["messages"].items()
            if entry["author_id"] == member.id and entry["count"] > 0
        ]
        their_messages.sort(key=lambda x: x[1]["count"], reverse=True)
        their_messages = their_messages[:10]

        if not their_messages:
            await ctx.send(f"😭 **{member.display_name}** has no individually tracked sob messages yet.")
            return

        lines = []
        for mid, entry in their_messages:
            jump_url = f"https://discord.com/channels/{ctx.guild.id}/{entry['channel_id']}/{mid}"
            snippet = entry["content"].replace("\n", " ").strip() or "*(no text — embed/attachment)*"
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            lines.append(f"**😭 {entry['count']}** — [{snippet}]({jump_url})")

        embed = discord.Embed(
            title=f"😭 Top Sob Messages — {member.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @sob.command(name="scan")
    @commands.has_permissions(administrator=True)
    async def sob_scan(self, ctx):
        status_msg = await ctx.send("😭 **Scanning full server history...** This can take a while on a large server.")

        async def status_callback(text):
            await status_msg.edit(content=f"😭 {text}")

        await self._scan_guild(ctx.guild, status_callback=status_callback)
        await status_msg.edit(content="✅ Scan complete. Counts are now up to date and will stay live from here on.")

    @sob_scan.error
    async def sob_scan_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need administrator permissions to run a full scan.")
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Sobs(bot))
