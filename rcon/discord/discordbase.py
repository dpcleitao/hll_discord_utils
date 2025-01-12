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
        
        # Handle migrations first
        self._ensure_version_table()
        self._handle_migrations()
        
        # Then ensure other tables
        self._ensure_tables()

    def _ensure_version_table(self):
        """Create and initialize version tracking table"""
        try:
            with self.conn:
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS db_version (
                        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                ''')
                
                # Insert initial version if table is empty
                self.cursor.execute('SELECT version FROM db_version ORDER BY version_id DESC LIMIT 1')
                if not self.cursor.fetchone():
                    self.cursor.execute(
                        'INSERT INTO db_version (version, updated_at) VALUES (?, ?)',
                        (1, int(time.time()))
                    )
        except sqlite3.Error as e:
            logger.error(f"Version table error: {e}")
            raise

    def _get_db_version(self) -> int:
        """Get current database version"""
        try:
            self.cursor.execute('SELECT version FROM db_version ORDER BY version_id DESC LIMIT 1')
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"Error getting DB version: {e}")
            return 0

    def _update_db_version(self, new_version: int):
        """Update database version"""
        try:
            with self.conn:
                self.cursor.execute(
                    'INSERT INTO db_version (version, updated_at) VALUES (?, ?)',
                    (new_version, int(time.time()))
                )
        except sqlite3.Error as e:
            logger.error(f"Error updating DB version: {e}")
            raise

    def _handle_migrations(self):
        """Handle all necessary database migrations"""
        current_version = self._get_db_version()
        logger.info(f"Current database version: {current_version}")

        try:
            if current_version < 2:
                logger.info("Applying migration to version 2...")
                with self.conn:
                    # Example migration: Add vote_reminders column
                    self.cursor.execute('''
                        ALTER TABLE voter_register 
                        ADD COLUMN votreg_vote_reminders BOOLEAN DEFAULT TRUE
                    ''')
                self._update_db_version(2)

            if current_version < 3:
                logger.info("Applying migration to version 3...")
                with self.conn:
                    # Example: Add last_updated column
                    self.cursor.execute('''
                        ALTER TABLE voter_register 
                        ADD COLUMN votreg_last_updated INTEGER DEFAULT ?
                    ''', (int(time.time()),))
                self._update_db_version(3)

            # Add more migrations as needed...

        except sqlite3.Error as e:
            logger.error(f"Migration error: {e}")
            raise

    def _ensure_tables(self):
        """Ensure all necessary tables exist"""
        try:
            with self.conn:
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
        except sqlite3.Error as e:
            logger.error(f"Table creation error: {e}")
            raise

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
