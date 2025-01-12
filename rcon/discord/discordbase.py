import sqlite3
import time
import logging

# get Logger for this modul
logger = logging.getLogger(__name__)

class DiscordBase:
    def __init__(self):
        self.msg_id = None
        self.conn = sqlite3.connect('hll_discord_helper.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Ensure tables exist
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure all necessary tables exist"""
        try:
            with self.conn:
                # Create voter registration table
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS voter_register (
                        votreg_seqno INTEGER PRIMARY KEY AUTOINCREMENT,
                        votreg_dis_user TEXT,
                        votreg_dis_user_id INTEGER,
                        votreg_dis_nick TEXT,
                        votreg_t17_id TEXT,
                        votereg_ask_reg_cnt INTEGER DEFAULT 0,
                        votereg_not_ingame_cnt INTEGER DEFAULT 0,
                        votreg_last_updated INTEGER,
                        votreg_vote_reminders BOOLEAN DEFAULT TRUE
                    )
                ''')
                
                # Create index for performance
                self.cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_voter_register_user_id 
                    ON voter_register(votreg_dis_user_id)
                ''')

                # Create message tracking table
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS message_ids (
                        msg_name TEXT PRIMARY KEY,
                        msg_id INTEGER
                    )
                ''')

        except sqlite3.Error as e:
            logger.error(f"Table creation error: {e}")
            raise

    def select_Message_Id(self, name):
        """Get message ID from database"""
        try:
            self.cursor.execute('SELECT msg_id FROM message_ids WHERE msg_name = ?', (name,))
            result = self.cursor.fetchone()
            return result[0] if result else None
        except sqlite3.Error as e:
            logger.error(f"Database error in select_Message_Id: {e}")
            return None

    # Registration Methods
    def update_Voter_Registration(self, discord_user, discord_user_id, discord_nick, player_id, vote_reminders=True):
        """Update or insert voter registration as a transaction"""
        try:
            with self.conn:
                self.cursor.execute('''
                    UPDATE voter_register 
                    SET votreg_dis_user = ?, 
                        votreg_dis_nick = ?, 
                        votreg_t17_id = ?, 
                        votereg_ask_reg_cnt = ?,
                        votereg_not_ingame_cnt = ?,
                        votreg_last_updated = ?,
                        votreg_vote_reminders = ?
                    WHERE votreg_dis_user_id = ?
                ''', (str(discord_user), str(discord_nick), str(player_id), 
                     0, 0, int(time.time()), vote_reminders, int(discord_user_id)))
                
                if self.cursor.rowcount == 0:
                    self.cursor.execute('''
                        INSERT INTO voter_register (
                            votreg_dis_user, votreg_dis_user_id, votreg_dis_nick, 
                            votreg_t17_id, votereg_ask_reg_cnt, votereg_not_ingame_cnt,
                            votreg_last_updated, votreg_vote_reminders
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (str(discord_user), int(discord_user_id), str(discord_nick), 
                          str(player_id), 0, 0, int(time.time()), vote_reminders))
                
                logger.info(f"Updated registration for user: {discord_user} ({discord_user_id})")
                return True

        except sqlite3.Error as e:
            logger.error(f"Database error in update_Voter_Registration: {e}")
            return False

    def get_User_Registration(self, discord_user_id: int) -> tuple:
        """Get user registration details"""
        try:
            self.cursor.execute('''
                SELECT 
                    votreg_dis_user, 
                    votreg_dis_nick, 
                    votreg_t17_id, 
                    votereg_ask_reg_cnt,
                    votreg_vote_reminders,
                    votreg_last_updated
                FROM voter_register 
                WHERE votreg_dis_user_id = ? 
                ORDER BY votreg_seqno DESC LIMIT 1
            ''', (discord_user_id,))
            return self.cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"Database error in get_User_Registration: {e}")
            return None

    def get_All_Registered_Users(self, with_vote_reminders: bool = None) -> list:
        """Get all registered users, optionally filtered by vote reminder preference"""
        try:
            query = '''
                SELECT 
                    votreg_dis_user_id, 
                    votreg_t17_id,
                    votreg_vote_reminders
                FROM voter_register 
                WHERE votreg_seqno IN (
                    SELECT MAX(votreg_seqno)
                    FROM voter_register
                    GROUP BY votreg_dis_user_id
                )
            '''
            
            if with_vote_reminders is not None:
                query += ' AND votreg_vote_reminders = ?'
                self.cursor.execute(query, (with_vote_reminders,))
            else:
                self.cursor.execute(query)
                
            return self.cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error in get_All_Registered_Users: {e}")
            return []

    def update_Vote_Reminder_Preference(self, discord_user_id: int, enabled: bool) -> bool:
        """Update user's vote reminder preference"""
        try:
            with self.conn:
                self.cursor.execute('''
                    UPDATE voter_register 
                    SET votreg_vote_reminders = ?,
                        votreg_last_updated = ?
                    WHERE votreg_dis_user_id = ? 
                    AND votreg_seqno = (
                        SELECT MAX(votreg_seqno) 
                        FROM voter_register 
                        WHERE votreg_dis_user_id = ?
                    )
                ''', (enabled, int(time.time()), discord_user_id, discord_user_id))
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error in update_Vote_Reminder_Preference: {e}")
            return False

    def increment_Ask_Registration_Count(self, discord_user_id: int) -> bool:
        """Increment the ask registration counter for a user"""
        try:
            with self.conn:
                self.cursor.execute('''
                    UPDATE voter_register 
                    SET votereg_ask_reg_cnt = votereg_ask_reg_cnt + 1,
                        votreg_last_updated = ?
                    WHERE votreg_dis_user_id = ? 
                    AND votreg_seqno = (
                        SELECT MAX(votreg_seqno) 
                        FROM voter_register 
                        WHERE votreg_dis_user_id = ?
                    )
                ''', (int(time.time()), discord_user_id, discord_user_id))
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error in increment_Ask_Registration_Count: {e}")
            return False
