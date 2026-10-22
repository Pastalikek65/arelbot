import json
import os
import asyncio
from src.database import Database, User, ChatSession, Message
from sqlalchemy.orm import Session
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JSON_FILE = "chat_sessions.json"

def migrate():
    if not os.path.exists(JSON_FILE):
        logger.warning(f"No {JSON_FILE} found. Skipping migration.")
        return

    logger.info("Starting migration from JSON to Database...")
    
    # Initialize DB (will create sqlite file if not exists)
    db = Database()
    session = db.get_session()
    
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for chat_id_str, chat_data in data.items():
            chat_id = int(chat_id_str)
            logger.info(f"Migrating chat {chat_id}...")
            
            # 1. Create or Get ChatSession
            chat_session = session.query(ChatSession).filter_by(id=chat_id).first()
            if not chat_session:
                chat_session = ChatSession(id=chat_id, type="unknown", settings=json.dumps(chat_data.get("settings", {})))
                session.add(chat_session)
            
            # 2. Migrate User Profiles
            profiles = chat_data.get("profiles", {})
            for user_id_str, profile_data in profiles.items():
                user_id = int(user_id_str)
                user = session.query(User).filter_by(id=user_id).first()
                if not user:
                    user = User(
                        id=user_id,
                        username=profile_data.get("name"), # We stored name here
                        first_name=profile_data.get("name"),
                        message_count=profile_data.get("count", 0),
                        avg_message_length=profile_data.get("avg_len", 0),
                        uses_emojis=profile_data.get("emoji_user", False),
                        communication_style=profile_data.get("style", "normal")
                    )
                    session.add(user)
                else:
                    # Update existing user stats if needed
                     user.message_count += profile_data.get("count", 0)
            
            # 3. Migrate Messages
            history = chat_data.get("history", [])
            for msg in history:
                role = msg.get("role")
                parts = msg.get("parts", [])
                
                for part in parts:
                    content = ""
                    func_name = None
                    func_args = None
                    
                    if isinstance(part, str):
                        content = part
                    elif isinstance(part, dict): # Function call/response object from history
                        # This is a simplification; JSON history format might vary slightly
                        # If complex object, dump to string
                        content = json.dumps(part)
                    
                    # Try to extract user from content if it's "[User]: msg" format
                    user_id = None
                    if role == "user" and content.startswith("[") and "]: " in content:
                        # Extract username, simple heuristic
                        pass 

                    new_msg = Message(
                        chat_id=chat_id,
                        user_id=user_id, # Can be null if we can't map strictly
                        role=role,
                        content=content
                    )
                    session.add(new_msg)
        
        session.commit()
        logger.info("Migration completed successfully! 🎉")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Migration failed: {e}", exc_info=True)
    finally:
        session.close()

if __name__ == "__main__":
    migrate()
