import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
from typing import List, Optional
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
        self.webhook_url = config.get("rcon", 0, "registration", 0, "webhook_channel_id")
        self.update_nickname = config.get("rcon", 0, "registration", 0, "update_nickname")
        self.nickname_formats = config.get("rcon", 0, "registration", 0, "nickname_formats", default=["simple"])
        self.clan_position = config.get("rcon", 0, "registration", 0, "clan_position", default="suffix")
        self.hidden_clan_tags = config.get("rcon", 0, "registration", 0, "hidden_clan_tags", default=[])
        self.role_id = config.get("rcon", 0, "registration", 0, "registered_role_id")

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
        vote_reminders="Receive in-game vote reminders"
    )
    async def register(
        self,
        interaction: discord.Interaction,
        t17_name: str,
        vote_reminders: bool = True,
        clan_tag: Optional[str] = None,
        t17_number: Optional[str] = None,
        show_clan: Optional[bool] = False,
        show_t17: Optional[bool] = False
    ):
        try:
            logger.info(f"Registration request from {interaction.user.name} ({interaction.user.id}) for T17: {t17_name}")
            await interaction.response.defer(ephemeral=True)
            
            # Validate required fields based on format
            if show_clan and not clan_tag:
                await interaction.followup.send("Clan tag is required if you want to show it in your nickname", ephemeral=True)
                return
                
            if show_t17 and not t17_number:
                await interaction.followup.send("T17 number is required if you want to show it in your nickname", ephemeral=True)
                return

            # Validate format options against config
            if show_clan and "clan" not in self.nickname_formats:
                await interaction.followup.send("Clan tag display is not enabled on this server", ephemeral=True)
                return

            if show_t17 and "t17" not in self.nickname_formats:
                await interaction.followup.send("T17 number display is not enabled on this server", ephemeral=True)
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

            registration_message = "Registration successful!"

            # Add role if configured
            if self.role_id:
                try:
                    role = interaction.guild.get_role(int(self.role_id))
                    if role:
                        await interaction.user.add_roles(role)
                        logger.info(f"Added registration role to user: {interaction.user.name}")
                except Exception as e:
                    logger.warning(f"Failed to add role to user: {interaction.user.name} - {e}")

            # Try to update nickname if enabled
            if self.update_nickname:
                try:
                    new_nickname = self.format_nickname(
                        display_name=t17_name, 
                        clan_tag=clan_tag, 
                        t17_number=t17_number, 
                        show_clan=show_clan,
                        show_t17=show_t17
                    )
                    await interaction.user.edit(nick=new_nickname)
                    registration_message += f"\nNickname updated to: `{new_nickname}`"
                except discord.Forbidden:
                    view = CopyNicknameView(new_nickname)
                    registration_message += f"\nI don't have permission to change your nickname.\nPlease set your nickname to: `{new_nickname}`"
                    await interaction.followup.send(registration_message, view=view, ephemeral=True)
                    try:
                        if self.webhook_url:
                            webhook = discord.SyncWebhook.from_url(self.webhook_url)
                            webhook.send(f"New registration: {interaction.user.mention} as {t17_name}")
                    except Exception as e:
                        logger.warning(f"Failed to send webhook: {e}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to update nickname for user: {interaction.user.name} - {e}")

            await interaction.followup.send(registration_message, ephemeral=True)

            # Try to send webhook notification
            try:
                if self.webhook_url:
                    webhook = discord.SyncWebhook.from_url(self.webhook_url)
                    webhook.send(f"New registration: {interaction.user.mention} as {t17_name}")
            except Exception as e:
                logger.warning(f"Failed to send webhook: {e}")

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

    def format_nickname(self, display_name: str, clan_tag: str = None, t17_number: str = None, show_clan: bool = False, show_t17: bool = False) -> str:
        try:
            result = display_name

            if show_clan and clan_tag and "clan" in self.nickname_formats and clan_tag.upper() not in [tag.upper() for tag in self.hidden_clan_tags]:
                tag = f"[{clan_tag[:4]}]"
                result = f"{tag} {result}" if self.clan_position == "prefix" else f"{result} {tag}"

            if show_t17 and t17_number and "t17" in self.nickname_formats:
                result = f"{result}#{t17_number}"

            return result[:32]
            
        except Exception as e:
            logger.error(f"Nickname format error: {e}")
            return display_name[:32] 