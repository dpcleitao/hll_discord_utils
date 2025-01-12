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

class Registration(commands.Cog, DiscordBase):
    def __init__(self, bot):
        super().__init__()  
        self.bot = bot
        self.shutdown_event = asyncio.Event()
        self.in_Loop = False
        self.loop_started = False
        # Only changed config access to match template structure
        server_config = config.get("rcon")[0]
        self.config = server_config.get("registration")[0]
        self.webhook_id = self.config.get("webhook_channel_id")

    async def background_task(self):
        while not self.shutdown_event.is_set():
            await asyncio.sleep(5)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.loop_started:
            self.loop_started = True
            self.bot.loop.create_task(self.background_task())
            logger.info("Registration background task started")

    async def query_Player_Database(self, query: str) -> List[str]:
        """Query the player database for matching names"""
        try:
            if len(query) > 1:       
                payload = {"page_size": 25, "page": 1, "player_name": query}
                result = await rcon.get_Player_History(payload)
                players = result.get_Players_Name()

                if players is not None and len(players):
                    return players[:25]
                else:
                    return None
            else:
                return None
            
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return None

    @app_commands.command(name="register", description="Register your T17 account")
    @app_commands.describe(
        t17_name="Your T17 name",
        clan_tag="Your clan tag (optional)",
        t17_number="Your T17 number (optional)",
        vote_reminders="Receive in-game vote reminders (default: True)"
    )
    async def register(self, interaction: discord.Interaction, t17_name: str, 
                      clan_tag: str = None, t17_number: str = None,
                      vote_reminders: bool = True):
        """Register a Discord user with their T17 account"""
        try:
            logger.info(f"Registration request from {interaction.user.name} ({interaction.user.id}) for T17: {t17_name}")
            await interaction.response.defer(ephemeral=True)
            
            success = self.update_Voter_Registration(
                discord_user=interaction.user.name,
                discord_user_id=interaction.user.id,
                discord_nick=interaction.user.display_name,
                player_id=t17_name,
                vote_reminders=vote_reminders  # Pass the vote_reminders preference
            )
            
            if not success:
                logger.error(f"Database update failed for user {interaction.user.name}")
                await interaction.followup.send("Failed to update registration database. Please try again later.", ephemeral=True)
                return

            # Only format nickname if configured
            if self.config.get("update_nickname", True):
                new_nickname = self.format_nickname(t17_name, clan_tag, t17_number)
                logger.info(f"Formatted nickname: {new_nickname}")

                try:
                    await interaction.user.edit(nick=new_nickname)
                    success_message = f"✅ Registration successful!\nNickname updated to: {new_nickname}"
                except discord.Forbidden:
                    logger.warning(f"Missing permissions to update nickname for {interaction.user.name}")
                    success_message = f"✅ Registration successful!\nPlease manually set your nickname to: {new_nickname}"
            else:
                success_message = "✅ Registration successful!"

            if self.webhook_id:
                try:
                    webhook = await self.bot.fetch_webhook(self.webhook_id)
                    await webhook.send(f"New registration: {interaction.user.mention} as {t17_name}")
                except Exception as e:
                    logger.error(f"Webhook notification failed: {e}")

            await interaction.followup.send(success_message, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Registration failed for {interaction.user.name}: {e}")
            await interaction.followup.send("An unexpected error occurred. Please try again later.", ephemeral=True)

    @register.autocomplete("t17_name")
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for T17 names"""
        try:
            if len(current) < 3:
                return []
            
            logger.info(f"Search query: {current}")
            players = await self.query_Player_Database(current.replace(" ", "%"))
            
            if not players:
                return []
            
            return [
                app_commands.Choice(name=player[0], value=player[0])
                for player in players
            ]
            
        except Exception as e:
            logger.error(f"Autocomplete error: {e}")
            return []

    def format_nickname(self, display_name: str, clan_tag: str = None, t17_number: str = None) -> str:
        """Format nickname according to configuration and Discord limits"""
        try:
            format_type = self.config.get("nickname_format", "simple")
            
            if format_type == "simple":
                return display_name[:32]
            
            if format_type == "t17":
                if t17_number:
                    return f"{display_name[:27]}#{t17_number}"[:32]
                return display_name[:32]
            
            if format_type == "clan":
                if not clan_tag:
                    return display_name[:32]
                    
                formatted_clan = f"[{clan_tag[:4]}]"
                position = self.config.get("clan_position", "suffix")
                
                if position == "prefix":
                    return f"{formatted_clan} {display_name}"[:32]
                return f"{display_name} {formatted_clan}"[:32]
            
            # Default to just the display name if no format matches
            return display_name[:32]

        except Exception as e:
            logger.error(f"Error formatting nickname: {e}")
            return display_name[:32]

async def setup(bot):
    await bot.add_cog(Registration(bot)) 