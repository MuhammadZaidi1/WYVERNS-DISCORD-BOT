import discord
from discord.ext import commands

from storage import get_connection

SOB_EMOJI = "😭"


class Sobs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = get_connection()
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS sob_messages (
                guild_id    INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                author_id   INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                count       INTEGER NOT NULL,
                content     TEXT,
                PRIMARY KEY (guild_id, message_id)
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sob_author ON sob_messages (guild_id, author_id)"
        )
        # Holds the most recent reset's data per member, so it can be undone
        # with ,revert. Only the latest reset per person is recoverable —
        # a second reset before reverting overwrites this backup.
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS sob_backup (
                guild_id    INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                author_id   INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                count       INTEGER NOT NULL,
                content     TEXT,
                PRIMARY KEY (guild_id, message_id)
            )
            """
        )
        self.db.commit()

    # -- helpers -----------------------------------------------------

    def _adjust_count(self, guild_id, message_id, author_id, channel_id, delta, content=""):
        cur = self.db.cursor()
        cur.execute(
            "SELECT count FROM sob_messages WHERE guild_id=? AND message_id=?",
            (guild_id, message_id),
        )
        row = cur.fetchone()

        if row is None:
            new_count = max(0, delta)
            if new_count == 0:
                return
            cur.execute(
                "INSERT INTO sob_messages (guild_id, message_id, author_id, channel_id, count, content) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, message_id, author_id, channel_id, new_count, content),
            )
        else:
            new_count = max(0, row[0] + delta)
            if new_count == 0:
                cur.execute(
                    "DELETE FROM sob_messages WHERE guild_id=? AND message_id=?",
                    (guild_id, message_id),
                )
            else:
                cur.execute(
                    "UPDATE sob_messages SET count=? WHERE guild_id=? AND message_id=?",
                    (new_count, guild_id, message_id),
                )

        self.db.commit()

    def _member_total(self, guild_id, author_id):
        cur = self.db.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(count), 0) FROM sob_messages WHERE guild_id=? AND author_id=?",
            (guild_id, author_id),
        )
        return cur.fetchone()[0]

    def _leaderboard(self, guild_id, limit=10):
        cur = self.db.cursor()
        cur.execute(
            "SELECT author_id, SUM(count) AS total FROM sob_messages "
            "WHERE guild_id=? GROUP BY author_id HAVING total > 0 "
            "ORDER BY total DESC LIMIT ?",
            (guild_id, limit),
        )
        return cur.fetchall()

    def _top_messages(self, guild_id, author_id, limit=10):
        cur = self.db.cursor()
        cur.execute(
            "SELECT message_id, channel_id, count, content FROM sob_messages "
            "WHERE guild_id=? AND author_id=? ORDER BY count DESC LIMIT ?",
            (guild_id, author_id, limit),
        )
        return cur.fetchall()

    def _reset_member(self, guild_id, member_id):
        cur = self.db.cursor()
        cur.execute(
            "SELECT message_id, channel_id, count, content FROM sob_messages "
            "WHERE guild_id=? AND author_id=?",
            (guild_id, member_id),
        )
        rows = cur.fetchall()

        if not rows:
            return False  # nothing to reset

        # Clear any older backup for this person first — only one backup
        # (the most recent reset) is ever kept per member.
        self.db.execute(
            "DELETE FROM sob_backup WHERE guild_id=? AND author_id=?",
            (guild_id, member_id),
        )
        for message_id, channel_id, count, content in rows:
            self.db.execute(
                "INSERT INTO sob_backup (guild_id, message_id, author_id, channel_id, count, content) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, message_id, member_id, channel_id, count, content),
            )
        self.db.execute(
            "DELETE FROM sob_messages WHERE guild_id=? AND author_id=?",
            (guild_id, member_id),
        )
        self.db.commit()
        return True

    def _revert_member(self, guild_id, member_id):
        cur = self.db.cursor()
        cur.execute(
            "SELECT message_id, channel_id, count, content FROM sob_backup "
            "WHERE guild_id=? AND author_id=?",
            (guild_id, member_id),
        )
        rows = cur.fetchall()

        if not rows:
            return False  # nothing to revert

        for message_id, channel_id, count, content in rows:
            self.db.execute(
                "INSERT OR REPLACE INTO sob_messages "
                "(guild_id, message_id, author_id, channel_id, count, content) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, message_id, member_id, channel_id, count, content),
            )
        self.db.execute(
            "DELETE FROM sob_backup WHERE guild_id=? AND author_id=?",
            (guild_id, member_id),
        )
        self.db.commit()
        return True

    async def _scan_guild(self, guild, status_callback=None):
        def _clear():
            self.db.execute("DELETE FROM sob_messages WHERE guild_id=?", (guild.id,))
            self.db.commit()

        await self.bot.loop.run_in_executor(None, _clear)

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

                        def _insert(mid=message.id, aid=message.author.id, cid=channel.id,
                                    c=count, content=message.content[:100]):
                            self.db.execute(
                                "INSERT OR REPLACE INTO sob_messages "
                                "(guild_id, message_id, author_id, channel_id, count, content) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (guild.id, mid, aid, cid, c, content),
                            )
                            self.db.commit()

                        await self.bot.loop.run_in_executor(None, _insert)
            except (discord.Forbidden, discord.HTTPException):
                continue

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
            cur = self.db.cursor()
            cur.execute(
                "SELECT author_id, channel_id FROM sob_messages WHERE guild_id=? AND message_id=?",
                (payload.guild_id, payload.message_id),
            )
            row = cur.fetchone()
            if row:
                self._adjust_count(payload.guild_id, payload.message_id, row[0], row[1], -1)
            return
        if message.author.bot:
            return
        self._adjust_count(payload.guild_id, payload.message_id, message.author.id, payload.channel_id, -1)

    # -- commands --------------------------------------------------------

    @commands.command(name="sobs")
    async def sobs(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        total = self._member_total(ctx.guild.id, member.id)

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
        entries = self._leaderboard(ctx.guild.id)

        if not entries:
            await ctx.send("😭 Nobody has received any sob reactions yet! Try `,sob scan` if this seems wrong.")
            return

        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for position, (member_id, total) in enumerate(entries, start=1):
            member = ctx.guild.get_member(member_id)
            name = member.mention if member else f"<@{member_id}>"
            rank = medals[position - 1] if position <= 3 else f"`#{position}`"
            description += f"{rank} {name} — **😭 {total:,}**\n"

        embed = discord.Embed(title="😭 Sob Leaderboard", description=description, color=discord.Color.blurple())
        embed.set_footer(text=f"Top {len(entries)} members • Highest → Lowest")
        await ctx.send(embed=embed)

    @sob.command(name="msg")
    async def sob_msg(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        rows = self._top_messages(ctx.guild.id, member.id)

        if not rows:
            await ctx.send(f"😭 **{member.display_name}** has no individually tracked sob messages yet.")
            return

        lines = []
        for message_id, channel_id, count, content in rows:
            jump_url = f"https://discord.com/channels/{ctx.guild.id}/{channel_id}/{message_id}"
            snippet = (content or "").replace("\n", " ").strip() or "*(no text — embed/attachment)*"
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            lines.append(f"**😭 {count}** — [{snippet}]({jump_url})")

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

    @commands.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def reset_sobs(self, ctx, member: discord.Member):
        """Resets a member's sob count to 0. Their prior data is saved and
        can be restored once with ,revert — but only the most recent
        reset is recoverable."""
        did_reset = self._reset_member(ctx.guild.id, member.id)
        if not did_reset:
            await ctx.send(f"**{member.display_name}** already has no sob count to reset.")
            return
        await ctx.send(
            f"😭 Reset **{member.display_name}**'s sob count to 0. "
            f"Use `,revert @{member.display_name}` to undo this."
        )

    @reset_sobs.error
    async def reset_sobs_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need administrator permissions to reset someone's count.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("Couldn't find that member. Usage: `,reset @user`")
        else:
            raise error

    @commands.command(name="revert")
    @commands.has_permissions(administrator=True)
    async def revert_sobs(self, ctx, member: discord.Member):
        """Restores a member's sob count from their most recent ,reset."""
        did_revert = self._revert_member(ctx.guild.id, member.id)
        if not did_revert:
            await ctx.send(f"There's no recent reset to revert for **{member.display_name}**.")
            return
        total = self._member_total(ctx.guild.id, member.id)
        await ctx.send(f"😭 Restored **{member.display_name}**'s sob count to {total:,}.")

    @revert_sobs.error
    async def revert_sobs_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need administrator permissions to revert someone's count.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("Couldn't find that member. Usage: `,revert @user`")
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Sobs(bot))