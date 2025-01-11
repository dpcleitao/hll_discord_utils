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
            await interaction.response.defer(ephemeral=True)
            logger.info(f"Starting registration for user: {interaction.user.name} ({interaction.user.id})")

            # Check if registration is enabled
            if not self.config.get("enabled", False):
                await interaction.followup.send(
                    "Registration is currently disabled.",
                    ephemeral=True
                )
                return

            # Make T17 number required if show_t17_number is true
            if self.config.get("show_t17_number", False) and not t17_number:
                await interaction.followup.send(
                    "T17 number is required when show_t17_number is enabled.",
                    ephemeral=True
                )
                return

            # Make clan tag required if clan_tag_required is true
            if self.config.get("clan_tag_required", False) and not clan_tag:
                await interaction.followup.send(
                    "Clan tag is required when clan_tag_required is enabled.",
                    ephemeral=True
                )
                return

            # verify that t17_name is a T17 ID
            if not re.fullmatch(r"[0-9a-fA-F]{32}", t17_name):
                await interaction.followup.send(
                    "Please select your name from the list and do not modify it.",
                    ephemeral=True
                )
                return

            # Database registration
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
                    logger.info(f"Creating new registration for user: {interaction.user.name}")
                    self.insert_Voter_Registration(
                        discord_user=interaction.user.name,
                        discord_user_id=interaction.user.id,
                        discord_nick=interaction.user.display_name,
                        player_id=t17_name,
                        register_cnt=0,
                        not_ingame_cnt=0
                    )
                else:
                    logger.info(f"Updated existing registration for user: {interaction.user.name}")
                self.conn.commit()
                
                success_message = "✅ Registration successful!\n\n"
                
                # Try to update nickname if enabled
                if self.config.get("t17_discord_user_name", False):
                    try:
                        multi_array = await self.query_Player_Database(t17_name)
                        if multi_array and len(multi_array) > 0:
                            formatted_name = self.format_nickname(
                                multi_array[0][1][0], clan_tag, t17_number, interaction.user
                            )
                            logger.info(f"Current nickname: {interaction.user.display_name}, Desired nickname: {formatted_name}")
                            try:
                                await interaction.user.edit(nick=formatted_name)
                                logger.info(f"Successfully updated nickname for {interaction.user.name} to: {formatted_name}")
                                success_message += f"Nickname updated to: {formatted_name}\n"
                            except discord.Forbidden:
                                logger.warning(f"Missing permissions to update nickname for {interaction.user.name}")
                                success_message += (
                                    f"⚠️ Could not automatically update your nickname.\n"
                                    f"Please manually set your nickname to:\n"
                                    f"```\n{formatted_name}\n```\n"
                                )
                    except Exception as e:
                        logger.error(f"Error updating nickname for {interaction.user.name}: {e}")
                        success_message += "⚠️ Could not update nickname due to an error.\n"

                # Try to assign the registered role
                if registered_role_id := self.config.get("registered_role_id"):
                    try:
                        role = interaction.guild.get_role(int(registered_role_id))
                        if role:
                            await interaction.user.add_roles(role)
                            logger.info(f"Successfully assigned registered role to: {interaction.user.name}")
                        else:
                            logger.error(f"Could not find registered role with ID: {registered_role_id}")
                    except Exception as e:
                        logger.error(f"Failed to assign role to {interaction.user.name}: {e}")

                # Try webhook notification
                if webhook_channel_id := self.config.get("webhook_channel_id"):
                    try:
                        channel = self.bot.get_channel(int(webhook_channel_id))
                        if channel:
                            embed = discord.Embed(
                                title="New Registration",
                                color=discord.Color.green(),
                                timestamp=datetime.now()
                            )
                            embed.add_field(name="User", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)
                            embed.add_field(name="T17 Name", value=t17_name, inline=True)
                            embed.add_field(name="Clan Tag", value=clan_tag if clan_tag else "None", inline=True)
                            embed.add_field(name="Vote Reminders", value=vote_reminders.value if vote_reminders else "No", inline=True)
                            
                            await channel.send(embed=embed)
                            logger.info(f"Sent registration webhook notification for: {interaction.user.name}")
                        else:
                            logger.error(f"Could not find webhook channel with ID: {webhook_channel_id}")
                    except Exception as e:
                        logger.error(f"Failed to send webhook for {interaction.user.name}: {e}")

                await interaction.followup.send(success_message, ephemeral=True)
                logger.info(f"Completed registration process for: {interaction.user.name}")

            except Exception as e:
                logger.error(f"Database registration error for {interaction.user.name}: {e}")
                await interaction.followup.send(
                    "Failed to complete registration. Please try again later.",
                    ephemeral=True
                )

        except Exception as e:
            logger.error(f"Registration command error for {interaction.user.name}: {e}")
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
        """Send webhook notification about new registration to specified channel"""
        try:
            if not (webhook_channel_id := self.config.get("webhook_channel_id")):
                logger.info("No webhook channel configured, skipping notification")
                return

            channel = self.bot.get_channel(int(webhook_channel_id))
            if not channel:
                logger.error(f"Could not find webhook channel with ID: {webhook_channel_id}")
                return

            embed = discord.Embed(
                title="New Registration",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            
            embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
            embed.add_field(name="T17 Name", value=t17_name, inline=True)
            embed.add_field(name="Clan Tag", value=clan_tag if clan_tag else "None", inline=True)
            embed.add_field(name="Vote Reminders", value=vote_reminders, inline=True)
            
            try:
                await channel.send(embed=embed)
                logger.info(f"Sent registration notification for user: {user.name}")
            except Exception as e:
                logger.error(f"Failed to send webhook message: {e}")
                
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

    def format_nickname(self, display_name: str, clan_tag: str = None, t17_number: str = None, user: discord.Member = None) -> str:
        """Format the nickname according to settings and permissions"""
        try:
            # Check if user has priority role for clan tag
            has_priority = False
            if user and self.config.get("clan_priority_roles"):
                has_priority = any(role.name in self.config.get("clan_priority_roles", []) 
                                 for role in user.roles)

            # Check if clan tag should be hidden
            show_clan_tag = True
            if clan_tag and clan_tag.upper() in self.config.get("clans", {}):
                if self.config["clans"][clan_tag.upper()].get("hide_tag", False):
                    show_clan_tag = False
                    logger.info(f"Hiding clan tag for user: {user.name}")

            name_format = self.config.get("name_format", "t17_first")
            logger.info(f"Using name format: {name_format}")

            if name_format == "clan_first" and clan_tag and show_clan_tag:
                if self.config.get("show_t17_number", False) and t17_number:
                    formatted_name = f"[{clan_tag[:4]}] {display_name[:20]}#{t17_number}"
                else:
                    formatted_name = f"[{clan_tag[:4]}] {display_name[:25]}"
            else:  # t17_first or default
                if self.config.get("show_t17_number", False) and t17_number:
                    if clan_tag and show_clan_tag:
                        formatted_name = f"{display_name[:20]}#{t17_number} [{clan_tag[:4]}]"
                    else:
                        formatted_name = f"{display_name[:27]}#{t17_number}"
                elif clan_tag and show_clan_tag:
                    formatted_name = f"{display_name[:25]} [{clan_tag[:4]}]"
                else:
                    formatted_name = display_name[:32]

            logger.info(f"Formatted nickname: {formatted_name}")
            return formatted_name

        except Exception as e:
            logger.error(f"Error formatting nickname: {e}")
            return display_name  # Return original name if formatting fails

async def setup(bot):
    logger.info("Setting up Registration cog")
    await bot.add_cog(Registration(bot))
    logger.info("Registration cog added")
    try:
        await bot.tree.sync()
        logger.info("Command tree synced successfully")
    except Exception as e:
        logger.error(f"Failed to sync command tree: {e}") 