#!/usr/bin/env python3
"""
MongoDB Role Field Migration Script
==================================

This script migrates from dual role fields (is_user, is_admin) to a single field (is_user).

Migration Logic:
- If is_admin: true, set is_user: false (admin users)
- If is_admin: false, keep is_user as is (regular users)
- Remove is_admin field from all documents

Usage:
    python migrate_role_fields.py

⚠️  IMPORTANT: 
    - Backup your database before running this script
    - Test in development environment first
    - This script modifies the users collection only
"""

import asyncio
import sys
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)

async def migrate_role_fields():
    """Migrate role fields from is_admin to is_user only."""
    settings = get_settings()
    
    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    
    try:
        logger.info("🚀 Starting role field migration...")
        logger.info(f"📍 Database: {settings.DATABASE_NAME}")
        logger.info(f"📍 Collection: users")
        
        # Step 1: Find all documents with is_admin: true
        admin_users_cursor = db.users.find({"is_admin": True})
        admin_users = await admin_users_cursor.to_list(length=None)
        
        logger.info(f"📊 Found {len(admin_users)} admin users to migrate")
        
        if admin_users:
            # Step 2: Update admin users to set is_user: false
            for admin_user in admin_users:
                user_id = str(admin_user.get("_id"))
                email = admin_user.get("email", "unknown")
                
                await db.users.update_one(
                    {"_id": admin_user["_id"]},
                    {"$set": {"is_user": False}}
                )
                
                logger.info(f"✅ Migrated admin user: {email} (ID: {user_id})")
        
        # Step 3: Count regular users for verification
        regular_users_count = await db.users.count_documents({"is_user": True})
        migrated_admins_count = await db.users.count_documents({"is_user": False})
        
        logger.info(f"📊 Regular users: {regular_users_count}")
        logger.info(f"📊 Migrated admins: {migrated_admins_count}")
        
        # Step 4: Remove is_admin field from all documents
        logger.info("🗑️  Removing is_admin field from all documents...")
        
        result = await db.users.update_many(
            {},  # Match all documents
            {"$unset": {"is_admin": ""}}
        )
        
        logger.info(f"✅ Removed is_admin field from {result.modified_count} documents")
        
        # Step 5: Verification
        logger.info("🔍 Verifying migration...")
        
        # Check that no documents have is_admin field
        remaining_is_admin = await db.users.count_documents({"is_admin": {"$exists": True}})
        
        if remaining_is_admin == 0:
            logger.info("✅ Verification passed: No is_admin fields remaining")
        else:
            logger.error(f"❌ Verification failed: {remaining_is_admin} documents still have is_admin field")
            return False
        
        # Final counts
        final_users = await db.users.count_documents({})
        final_regular_users = await db.users.count_documents({"is_user": True})
        final_admin_users = await db.users.count_documents({"is_user": False})
        
        logger.info("📋 Migration Summary:")
        logger.info(f"   Total users: {final_users}")
        logger.info(f"   Regular users (is_user: true): {final_regular_users}")
        logger.info(f"   Admin users (is_user: false): {final_admin_users}")
        logger.info(f"   Migration completed at: {datetime.utcnow()}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {str(e)}")
        return False
    
    finally:
        await client.close()

async def main():
    """Main function to run the migration."""
    print("=" * 60)
    print("🔧 MongoDB Role Field Migration")
    print("=" * 60)
    print()
    print("⚠️  WARNING: This will modify your database!")
    print("⚠️  Make sure you have a backup before proceeding.")
    print()
    
    # Ask for confirmation
    response = input("Do you want to continue? (yes/no): ").strip().lower()
    
    if response not in ["yes", "y"]:
        print("❌ Migration cancelled.")
        sys.exit(0)
    
    print()
    print("🚀 Starting migration...")
    
    success = await migrate_role_fields()
    
    if success:
        print()
        print("✅ Migration completed successfully!")
        print("🎉 You can now restart your application.")
    else:
        print()
        print("❌ Migration failed!")
        print("🔧 Please check the logs and fix any issues.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
