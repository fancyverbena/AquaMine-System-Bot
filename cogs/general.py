from discord.ext import commands


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send("Pong!")


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))