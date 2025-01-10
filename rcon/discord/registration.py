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

logger = logging.getLogger(__name__)

class Registration(commands.Cog, DiscordBase):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = config.get("rcon", 0, "registration", 0)
        self.webhook_url = config.get("rcon", 0, "registration", 0, "webhook")
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

            # Update nickname if enabled
            if self.config["t17_discord_user_name"]:
                try:
                    # Get the player name from the autocomplete result
                    multi_array = await self.query_Player_Database(t17_name)
                    if multi_array and len(multi_array) > 0:
                        display_name = multi_array[0][1]  # Use the actual player name
                        if clan_tag:
                            formatted_name = f"{display_name[:25]} [{clan_tag[:4]}]"
                        else:
                            formatted_name = display_name[:32]
                        
                        # Check if the current nickname matches
                        current_nick = interaction.user.display_name
                        logger.info(f"Current nickname: {current_nick}, Desired nickname: {formatted_name}")
                        
                        if current_nick == formatted_name:
                            await interaction.response.send_message(
                                f"Registration successful!\n"
                                f"Your Discord nickname already matches your T17 name.\n"
                                f"Vote Reminders: {vote_reminders.value if vote_reminders else 'No'}",
                                ephemeral=True
                            )
                        else:
                            await interaction.user.edit(nick=formatted_name)
                            logger.info("Nickname updated successfully")
                            
                            await interaction.response.send_message(
                                f"Registration successful!\n"
                                f"Discord nickname updated to match T17 name: {formatted_name}\n"
                                f"Vote Reminders: {vote_reminders.value if vote_reminders else 'No'}",
                                ephemeral=True
                            )
                        
                        # Send webhook if configured
                        if self.webhook_url:
                            try:
                                webhook = discord.Webhook.from_url(
                                    self.webhook_url,
                                    session=aiohttp.ClientSession()
                                )
                                await webhook.send(
                                    f"New Registration:\n"
                                    f"User: {interaction.user.mention} ({interaction.user.id})\n"
                                    f"T17 Name: {display_name}\n"
                                    f"Clan Tag: {clan_tag if clan_tag else 'None'}\n"
                                    f"Vote Reminders: {vote_reminders.value if vote_reminders else 'No'}"
                                )
                                await webhook.session.close()
                            except Exception as e:
                                logger.error(f"Webhook error: {e}")
                    else:
                        await interaction.response.send_message(
                            "Failed to retrieve player name. Registration saved but nickname not updated.",
                            ephemeral=True
                        )
                except discord.Forbidden:
                    await interaction.response.send_message(
                        "Unable to update your Discord nickname. Please contact a Discord admin to grant the bot necessary permissions.",
                        ephemeral=True
                    )
                    logger.warning(f"Bot lacks permission to change nickname for user {interaction.user.id}")
                except Exception as e:
                    logger.error(f"Failed to update nickname: {e}")
                    await interaction.response.send_message(
                        "Failed to update nickname. Your registration is still saved.",
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    f"Registration successful!\n"
                    f"Vote Reminders: {vote_reminders.value if vote_reminders else 'No'}",
                    ephemeral=True
                )

        except Exception as e:
            logger.error(f"Registration error: {e}")
            await interaction.response.send_message(
                "Failed to complete registration. Please try again later.",
                ephemeral=True
            )

    @register.autocomplete("t17_name")
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            if len(current) < 5:  # Minimum 5 characters
                return []
            
            logger.info(f"Search query: {current}")
            multi_array = await self.query_Player_Database(current.replace(" ", "%"))
            
            if not multi_array:
                return []
            
            return [
                app_commands.Choice(
                    name=f"Last seen: {datetime.fromtimestamp(player[3]/1000).strftime('%Y-%m-%d')} - {player[1]}"[:100],
                    value=player[0]
                )
                for player in multi_array[:25]
            ]
            
        except Exception as e:
            logger.error(f"Unexpected error in autocomplete: {e}")
            return []

    async def query_Player_Database(self, query: str) -> List[tuple]:
        try:
            logger.info(f"Querying database for: {query}")
            
            # If it's a T17 ID (32 hex characters)
            if bool(re.fullmatch(r"[0-9a-fA-F]{32}", query)):
                # Use get_Player_History with the ID
                payload = {"page_size": 1, "page": 1, "player_id": query}
                result = await rcon.get_Player_History(payload)
                if result:
                    players = result.get_Players_Name()
                    if players and len(players) > 0:
                        logger.info(f"Found player by ID: {players[0][1]}")
                        return players
                    else:
                        logger.error(f"No player found for ID: {query}")
                        return None
                else:
                    logger.error("No result from get_Player_History")
                    return None
            else:
                # Search by name
                payload = {"page_size": 25, "page": 1, "player_name": query}
                result = await rcon.get_Player_History(payload)
                if result:
                    players = result.get_Players_Name()
                    logger.info(f"Search results: {players}")
                    return players
                else:
                    logger.error("No results from name search")
                    return None
            
        except Exception as e:
            logger.error(f"Error querying player database: {e}")
            return None

    async def send_registration_webhook(self, user, t17_name: str, clan_tag: str = None, vote_reminders: str = None):
        """Send webhook notification about new registration"""
        if not self.webhook_url:
            return

        webhook = discord.Webhook.from_url(
            self.webhook_url,
            session=aiohttp.ClientSession()
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
        finally:
            await webhook.session.close()

async def setup(bot):
    await bot.add_cog(Registration(bot)) 