import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import logging
import asyncio
import re
from rcon.discord.discordbase import DiscordBase
from lib.config import config
from typing import List

logger = logging.getLogger(__name__)

class Registration(commands.Cog, DiscordBase):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = config.get("rcon", 0, "registration", 0)
        self.in_Loop = False

    @app_commands.command(
        name="register", 
        description="Link your T17 account with Discord and set your preferences"
    )
    @app_commands.describe(
        t17_name="Type to search your T17 name or enter your T17 ID directly",
        clan_tag="Your clan tag (optional, for Discord display)",
        vote_reminders="Do you want in-game vote reminders?"
    )
    @app_commands.choices(vote_reminders=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no")
    ])
    async def register(
        self, 
        interaction: discord.Interaction, 
        t17_name: str, 
        clan_tag: str = None, 
        vote_reminders: app_commands.Choice[str] = None
    ):
        """Register or update T17 name"""
        # verify that t17_name is a T17 ID
        if bool(re.fullmatch(r"[0-9a-fA-F]{32}", t17_name)) == False:
            await interaction.response.send_message(
                '''Something went wrong, please select your name from the\n'''
                '''list and do not add or remove any characters,\n'''
                '''after the selection.''', 
                ephemeral=True
            )
            return

        vote_reminders_value = vote_reminders.value if vote_reminders else "no"

        # Store/update registration
        self.insert_Voter_Registration(
            discord_user=interaction.user.name,
            discord_user_id=interaction.user.id,
            discord_nick=interaction.user.display_name,
            player_id=t17_name,
            register_cnt=0,
            not_ingame_cnt=0
        )

        # Update nickname if enabled
        if self.config["t17_discord_user_name"]:
            try:
                formatted_name = t17_name
                if clan_tag:
                    formatted_name = f"{t17_name} [{clan_tag}]"
                await interaction.user.edit(nick=formatted_name)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Unable to update your Discord nickname. Please contact a Discord admin to grant the bot necessary permissions.",
                    ephemeral=True
                )
                logger.warning(f"Bot lacks permission to change nickname for user {interaction.user.id}")

        # Send webhook if configured
        if self.config.get("registration_webhook_url"):
            try:
                await self.send_registration_webhook(
                    interaction.user,
                    t17_name,
                    clan_tag,
                    vote_reminders.value
                )
            except Exception as e:
                logger.error(f"Failed to send webhook notification: {e}")
                # Continue execution - webhook failure shouldn't affect registration

        # Always send confirmation to user
        await interaction.response.send_message(
            f"Registration successful!\n"
            f"T17 Name: {t17_name}\n"
            f"Clan Tag: {clan_tag if clan_tag else 'None'}\n"
            f"Vote Reminders: {vote_reminders.value}",
            ephemeral=True
        )

    @register.autocomplete("t17_name")
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            while self.in_Loop:
                await asyncio.sleep(1)
            
            self.in_Loop = True
            
            if len(current) >= 2:
                logger.info(f"Search query: {current.replace(" ", "%")}")
                multi_array = await self.query_Player_Database(current.replace(" ", "%"))
        
                if multi_array is not None and len(multi_array) >= 1:
                    result = [
                        app_commands.Choice(name=f"Last: {datetime.fromtimestamp(player[3]/1000).strftime('%Y-%m-%d')} - {", ".join(player[1])}"[:100], value=player[0])
                        for player in multi_array
                    ]
                    self.in_Loop = False
                    return result
            
            self.in_Loop = False
            return []
            
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            self.in_Loop = False
            return []

async def setup(bot):
    await bot.add_cog(Registration(bot)) 