import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import logging
import asyncio
import re
from typing import List
from rcon.discord.discordbase import DiscordBase
import rcon.rcon as rcon
from lib.config import config
import aiohttp
import sqlite3

logger = logging.getLogger(__name__)

class RegistrationMetrics:
    def __init__(self):
        self.successful_registrations = 0
        self.failed_registrations = 0
        self.nickname_updates = 0
        self.nickname_failures = 0
        self.last_error = None
        self.last_registration_time = None
        
    def log_registration_attempt(self, success: bool):
        if success:
            self.successful_registrations += 1
        else:
            self.failed_registrations += 1
        self.last_registration_time = datetime.now()

class Registration(commands.Cog, DiscordBase):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        logger.info("Initializing Registration cog")
        self.webhook_url = config.get("rcon", 0, "registration", 0, "webhook")
        self.config = config.get("rcon", 0, "registration", 0)
        logger.info(f"Registration config loaded: {self.config}")
        
        # Initialize database connection from DiscordBase
        self.conn = sqlite3.connect('hll_discord_helper.db')
        self.cursor = self.conn.cursor()
        logger.info("Database connection established")

    @app_commands.command(
        name="register", 
        description="Link your T17 account with Discord and set your preferences"
    )
    @app_commands.describe(
        t17_name="Type to search your T17 name or enter your T17 ID directly",
        clan_tag="Your clan tag (required if clan_tag_required is true)",
        t17_number="Your T17 number (required if show_t17_number is true)",
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
        t17_number: str = None,
        vote_reminders: app_commands.Choice[str] = None
    ):
        """Register or update T17 name"""
        try:
            # Initial response to prevent timeout
            await interaction.response.defer(ephemeral=True)

            # Check if registration is enabled
            if not self.config.get("enabled", False):
                await interaction.response.send_message(
                    "Registration is currently disabled.",
                    ephemeral=True
                )
                return

            # Make T17 number required if show_t17_number is true
            if self.config.get("show_t17_number", False) and not t17_number:
                await interaction.response.send_message(
                    "T17 number is required when show_t17_number is enabled.",
                    ephemeral=True
                )
                return

            # Make clan tag required if clan_tag_required is true
            if self.config.get("clan_tag_required", False) and not clan_tag:
                await interaction.response.send_message(
                    "Clan tag is required when clan_tag_required is enabled.",
                    ephemeral=True
                )
                return

            # verify that t17_name is a T17 ID
            if bool(re.fullmatch(r"[0-9a-fA-F]{32}", t17_name)) == False:
                await interaction.response.send_message(
                    '''Something went wrong, please select your name from the\n'''
                    '''list and do not add or remove any characters,\n'''
                    '''after the selection.''', 
                    ephemeral=True
                )
                return

            # Validate t17_number if provided
            if t17_number:
                # Remove # if user included it
                t17_number = t17_number.lstrip('#')
                
                # Check if it's exactly 4 digits
                if not re.fullmatch(r'\d{4}', t17_number):
                    await interaction.response.send_message(
                        "T17 number must be exactly 4 digits (e.g., 1234).",
                        ephemeral=True
                    )
                    return
            
            # Check if t17_number is required by config
            if self.config.get("t17_number_required", False) and not t17_number:
                await interaction.response.send_message(
                    "T17 number is required. Please provide your 4-digit T17 number.",
                    ephemeral=True
                )
                return

            # Database registration first - this is the most important part
            try:
                # Try to update existing registration first
                self.cursor.execute('''
                    UPDATE voter_register 
                    SET votreg_dis_user = ?, votreg_dis_nick = ?, votreg_t17_id = ?, 
                        votereg_ask_reg_cnt = ?, votereg_not_ingame_cnt = ?
                    WHERE votreg_dis_user_id = ?
                ''', (str(interaction.user.name), str(interaction.user.display_name), 
                      str(t17_name), 0, 0, int(interaction.user.id)))
                
                if self.cursor.rowcount == 0:
                    # If no rows were updated, insert new registration
                    self.insert_Voter_Registration(
                        discord_user=interaction.user.name,
                        discord_user_id=interaction.user.id,
                        discord_nick=interaction.user.display_name,
                        player_id=t17_name,
                        register_cnt=0,
                        not_ingame_cnt=0
                    )
                self.conn.commit()
                
                # Registration successful - now try optional features
                success_message = "✅ Registration successful!\n\n"
                
                # Try to update nickname if enabled
                if self.config["t17_discord_user_name"]:
                    try:
                        multi_array = await self.query_Player_Database(t17_name)
                        if multi_array and len(multi_array) > 0:
                            formatted_name = self.format_nickname(
                                multi_array[0][1][0], clan_tag, t17_number, interaction.user
                            )
                            try:
                                await interaction.user.edit(nick=formatted_name)
                                success_message += f"Nickname updated to: {formatted_name}\n"
                            except discord.Forbidden:
                                success_message += (
                                    f"⚠️ Could not automatically update your nickname.\n"
                                    f"Please manually set your nickname to:\n"
                                    f"```\n{formatted_name}\n```\n"
                                )
                
                # Try webhook notification - don't let it affect registration
                try:
                    if self.webhook_url:
                        await self.send_registration_webhook(
                            interaction.user, t17_name, clan_tag,
                            vote_reminders.value if vote_reminders else 'No'
                        )
                except Exception as e:
                    logger.error(f"Webhook notification failed: {e}")
                    # Don't let webhook failure affect the user experience
                
                # Send final success message
                await interaction.followup.send(
                    success_message,
                    ephemeral=True
                )
                
            except Exception as e:
                logger.error(f"Database registration error: {e}")
                await interaction.followup.send(
                    "Failed to complete registration. Please try again later.",
                    ephemeral=True
                )
                
        except Exception as e:
            logger.error(f"Registration command error: {e}")
            try:
                await interaction.followup.send(
                    "An error occurred. Please try again later.",
                    ephemeral=True
                )
            except:
                pass

    @register.autocomplete("t17_name")
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            logger.info(f"Autocomplete triggered with input: {current}")
            
            if len(current) < 3:  # Changed from 5 to 3 characters
                logger.info("Input too short, returning empty list")
                return []
            
            logger.info("Querying player database...")
            multi_array = await self.query_Player_Database(current.replace(" ", "%"))
            
            if not multi_array:
                logger.info("No results found from database")
                return []
            
            logger.info(f"Found {len(multi_array)} results")
            
            choices = [
                app_commands.Choice(
                    name=f"Last seen: {datetime.fromtimestamp(player[3]/1000).strftime('%Y-%m-%d')} - {player[1]}"[:100],
                    value=player[0]
                )
                for player in multi_array[:25]
            ]
            
            logger.info(f"Returning {len(choices)} choices")
            return choices
            
        except Exception as e:
            logger.error(f"Autocomplete error: {e}", exc_info=True)
            return []

    async def query_Player_Database(self, query: str) -> List[tuple]:
        try:
            logger.info(f"Database query started for: {query}")
            
            # If it's a T17 ID (32 hex characters)
            if bool(re.fullmatch(r"[0-9a-fA-F]{32}", query)):
                logger.info("Query is a T17 ID")
                payload = {
                    "page_size": 1,
                    "page": 1,
                    "player_id": query,
                    "steam_id_64": None,
                    "name_contains": None
                }
            else:
                logger.info("Query is a player name")
                payload = {
                    "page_size": 25,
                    "page": 1,
                    "player_name": query
                }
            
            logger.info(f"Sending RCON request with payload: {payload}")
            result = await rcon.get_Player_History(payload)
            
            if result:
                players = result.get_Players_Name()
                logger.info(f"Got {len(players) if players else 0} results")
                return players
            else:
                logger.error("No result from get_Player_History")
                return None
            
        except Exception as e:
            logger.error(f"Database query error: {e}", exc_info=True)
            return None

    async def send_registration_webhook(self, user, t17_name: str, clan_tag: str = None, vote_reminders: str = None):
        """Send webhook notification about new registration"""
        if not self.webhook_url:
            return

        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(
                self.webhook_url,
                session=session
            )
            
            try:
                await webhook.send(
                    f"New Registration:\n"
                    f"User: {user.mention} ({user.id})\n"
                    f"T17 Name: {t17_name}\n"
                    f"Clan Tag: {clan_tag if clan_tag else 'None'}\n"
                    f"Vote Reminders: {vote_reminders}"
                )
            except Exception as e:
                logger.error(f"Webhook error: {e}")

    @discord.ui.button(custom_id="copy_nickname")
    async def copy_nickname_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Get the formatted name from the original message content
        message_content = interaction.message.content
        # Extract the name from between the code block
        formatted_name = message_content.split('```')[1].strip()
        await interaction.response.send_message(f"Here's your nickname to copy: {formatted_name}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Track nickname changes"""
        if before.nick != after.nick and self.webhook_url:  # Only track nickname changes
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(
                    self.webhook_url,
                    session=session
                )
                
                try:
                    await webhook.send(
                        f"Nickname Update:\n"
                        f"User: {after.mention} ({after.id})\n"
                        f"Old Nickname: {before.nick or before.name}\n"
                        f"New Nickname: {after.nick or after.name}"
                    )
                except Exception as e:
                    logger.error(f"Webhook error: {e}")

    async def validate_inputs(self, t17_name: str, clan_tag: str = None, t17_number: str = None) -> tuple[bool, str]:
        """Validate all input parameters before processing"""
        if not re.fullmatch(r"[0-9a-fA-F]{32}", t17_name):
            return False, "Invalid T17 ID format"
            
        if clan_tag and (len(clan_tag) > 4 or not clan_tag.isalnum()):
            return False, "Clan tag must be 1-4 alphanumeric characters"
            
        if t17_number:
            t17_number = t17_number.lstrip('#')
            if not re.fullmatch(r'\d{4}', t17_number):
                return False, "T17 number must be exactly 4 digits"
                
        return True, ""

    async def ensure_database_connection(self):
        """Ensure database connection is active and recover if needed"""
        try:
            self.cursor.execute("SELECT 1")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            logger.warning("Database connection lost, attempting reconnection")
            await self.initialize_database_connection()

    async def update_registration(self, user_data: dict) -> bool:
        """Handle registration database operations with proper transaction management"""
        try:
            self.conn.execute("BEGIN TRANSACTION")
            
            # Update existing registration
            self.cursor.execute('''
                UPDATE voter_register 
                SET votreg_dis_user = ?, votreg_dis_nick = ?, votreg_t17_id = ?
                WHERE votreg_dis_user_id = ?
            ''', (user_data['name'], user_data['nick'], user_data['t17_id'], user_data['id']))
            
            if self.cursor.rowcount == 0:
                # Insert new registration
                self.cursor.execute('''
                    INSERT INTO voter_register (
                        votreg_dis_user, votreg_dis_user_id, votreg_dis_nick, 
                        votreg_t17_id, votereg_ask_reg_cnt, votereg_not_ingame_cnt
                    ) VALUES (?, ?, ?, ?, 0, 0)
                ''', (user_data['name'], user_data['id'], user_data['nick'], user_data['t17_id']))
            
            self.conn.commit()
            return True
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Database operation failed: {e}")
            return False

    def validate_configuration(self) -> tuple[bool, str]:
        """Validate all required configuration settings"""
        required_settings = [
            ("t17_discord_user_name", bool),
            ("show_t17_number", bool),
            ("t17_number_required", bool),
            ("clan_priority_roles", list)
        ]
        
        for setting, expected_type in required_settings:
            value = self.config.get(setting)
            if value is None:
                return False, f"Missing required setting: {setting}"
            if not isinstance(value, expected_type):
                return False, f"Invalid type for {setting}: expected {expected_type}"
                
        return True, ""

async def setup(bot):
    logger.info("Setting up Registration cog")
    await bot.add_cog(Registration(bot))
    logger.info("Registration cog added")
    try:
        await bot.tree.sync()
        logger.info("Command tree synced successfully")
    except Exception as e:
        logger.error(f"Failed to sync command tree: {e}") 