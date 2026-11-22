import discord
from discord.ext import commands

from utils.helpers import ok, err
from utils.store import store


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify:btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        c = store.get(interaction.guild.id).get("verify", {})
        role = interaction.guild.get_role(c.get("role", 0))
        if not role:
            return await interaction.response.send_message("Verification isn't set up properly. Tell an admin.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("You're already verified!", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="Verified via button")
            unv = interaction.guild.get_role(c.get("remove_role", 0))
            if unv and unv in interaction.user.roles:
                await interaction.user.remove_roles(unv, reason="Verified via button")
        except discord.HTTPException:
            return await interaction.response.send_message("I couldn't give you the role (my role must be above it).", ephemeral=True)
        await interaction.response.send_message(f"✅ You're verified and got **{role.name}**!", ephemeral=True)


class Verify(commands.Cog, name="Verify"):
    """Button verification panel."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(VerifyView())

    @commands.group(invoke_without_command=True, help="Setup: verify setup #channel @VerifiedRole  |  verify unverified @role")
    @commands.has_permissions(manage_guild=True)
    async def verify(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=ok(f"Use `{p}verify setup #channel @role` to post the panel.\n"
                                f"Optional: `{p}verify unverified @role` removes that role on verify."))

    @verify.command(name="setup", help="Post the verify panel. Usage: verify setup #channel @role")
    @commands.bot_has_permissions(manage_roles=True)
    async def v_setup(self, ctx, channel: discord.TextChannel, role: discord.Role):
        if role.is_default() or role.managed or role >= ctx.guild.me.top_role:
            return await ctx.send(embed=err("I can't assign that role (it must be below my top role)."))
        store.get(ctx.guild.id).setdefault("verify", {})["role"] = role.id
        store.save(ctx.guild.id)
        panel = discord.Embed(
            title="Verification",
            description=f"Click the button below to verify and gain access to **{ctx.guild.name}**.",
            color=0x57F287,
        )
        await channel.send(embed=panel, view=VerifyView())
        await ctx.send(embed=ok(f"Verify panel posted in {channel.mention}. Members get {role.mention}."))

    @verify.command(name="unverified", help="Role removed when a member verifies.")
    async def v_unverified(self, ctx, role: discord.Role):
        store.get(ctx.guild.id).setdefault("verify", {})["remove_role"] = role.id
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"{role.mention} will be removed on verification."))


async def setup(bot):
    await bot.add_cog(Verify(bot))
