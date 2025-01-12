import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
from typing import List
from rcon.discord.discordbase import DiscordBase
import rcon.rcon as rcon
from lib.config import config

logger = logging.getLogger(__name__)

class CopyNicknameView(discord.ui.View):
    def __init__(self, nickname: str):
        super().__init__()
        self.nickname = nickname

    @discord.ui.button(label="Copy Nickname", style=discord.ButtonStyle.primary)
    async def copy_nickname(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.nickname}```", ephemeral=True)

class Registration(commands.Cog, DiscordBase):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.shutdown_event = asyncio.Event()
        self.in_Loop = False
        self.loop_started = False
        self.webhook_url = config.get("rcon", 0, "registration", 0, "webhook")
        self.update_nickname = config.get("rcon", 0, "registration", 0, "update_nickname")
        self.nickname_format = config.get("rcon", 0, "registration", 0, "nickname_format")
        self.clan_position = config.get("rcon", 0, "registration", 0, "clan_position")

    async def background_task(self):
        while not self.shutdown_event.is_set():
            await asyncio.sleep(5)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.loop_started:
            self.loop_started = True
            self.bot.loop.create_task(self.background_task())
            logger.info("Registration background task started")

    @app_commands.command(name="register", description="Register your T17 account")
    @app_commands.describe(
        t17_name="Your T17 name",
        clan_tag="Your clan tag (required for clan format)",
        t17_number="Your T17 number (required for t17 format)",
        vote_reminders="Receive in-game vote reminders",
        nickname_format="Format of your nickname (simple, clan, or t17)"
    )
    @app_commands.choices(nickname_format=[
        app_commands.Choice(name="Simple (T17 name only)", value="simple"),
        app_commands.Choice(name="With Clan Tag", value="clan"),
        app_commands.Choice(name="With T17 Number", value="t17")
    ])
    async def register(self, interaction: discord.Interaction, t17_name: str, 
                      nickname_format: str,
                      clan_tag: str = None, 
                      t17_number: str = None,
                      vote_reminders: bool = True):
        try:
            logger.info(f"Registration request from {interaction.user.name} ({interaction.user.id}) for T17: {t17_name}")
            await interaction.response.defer(ephemeral=True)
            
            # Validate required fields based on format
            if nickname_format == "clan" and not clan_tag:
                await interaction.followup.send("Clan tag is required for clan format", ephemeral=True)
                return
                
            if nickname_format == "t17" and not t17_number:
                await interaction.followup.send("T17 number is required for t17 format", ephemeral=True)
                return

            success = self.update_Voter_Registration(
                discord_user=interaction.user.name,
                discord_user_id=interaction.user.id,
                discord_nick=interaction.user.display_name,
                player_id=t17_name,
                vote_reminders=vote_reminders
            )
            
            if not success:
                await interaction.followup.send("Registration failed. Please try again later.", ephemeral=True)
                return

            if self.update_nickname:
                new_nickname = self.format_nickname(t17_name, clan_tag, t17_number)
                try:
                    await interaction.user.edit(nick=new_nickname)
                    await interaction.followup.send(f"Registration successful! Nickname updated to: {new_nickname}", ephemeral=True)
                except discord.Forbidden:
                    view = CopyNicknameView(new_nickname)
                    await interaction.followup.send(
                        f"Registration successful! I don't have permission to change your nickname.\n"
                        f"Please set your nickname to: `{new_nickname}`", 
                        view=view,
                        ephemeral=True
                    )
            else:
                await interaction.followup.send("Registration successful!", ephemeral=True)

            if self.webhook_url:
                webhook = discord.SyncWebhook.from_url(self.webhook_url)
                webhook.send(f"New registration: {interaction.user.mention} as {t17_name}")

        except Exception as e:
            logger.error(f"Registration error: {e}")
            await interaction.followup.send("An error occurred. Please try again later.", ephemeral=True)

    @register.autocomplete("t17_name")
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            if len(current) < 3:
                return []
            
            players = await self.query_Player_Database(current.replace(" ", "%"))
            return [app_commands.Choice(name=p, value=p) for p in (players or [])]
            
        except Exception as e:
            logger.error(f"Autocomplete error: {e}")
            return []

    async def query_Player_Database(self, query: str) -> List[str]:
        try:
            if len(query) > 1:       
                payload = {"page_size": 25, "page": 1, "player_name": query}
                result = await rcon.get_Player_History(payload)
                players = result.get_Players_Name()
                return players[:25] if players and len(players) else None
            return None
            
        except Exception as e:
            logger.error(f"Database query error: {e}")
            return None

    def format_nickname(self, display_name: str, clan_tag: str = None, t17_number: str = None) -> str:
        try:
            if self.nickname_format == "simple":
                return display_name[:32]
            
            if self.nickname_format == "t17" and t17_number:
                return f"{display_name[:27]}#{t17_number}"[:32]
            
            if self.nickname_format == "clan" and clan_tag:
                tag = f"[{clan_tag[:4]}]"
                return (f"{tag} {display_name}" if self.clan_position == "prefix" else f"{display_name} {tag}")[:32]
            
            return display_name[:32]
            
        except Exception as e:
            logger.error(f"Nickname format error: {e}")
            return display_name[:32] 