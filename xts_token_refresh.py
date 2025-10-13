#!/usr/bin/env python3
"""
XTS Token Refresh Script
Refreshes XTS authentication token and saves to xts_store_token.json
Designed to be run as a cron job
"""

import json
import os
import sys
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
log_file = "xts_token_refresh.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Get configuration from environment
TOKEN_TELEGRAM = os.getenv('TELEGRAM_TOKEN')
CHAT_ID_TELEGRAM = os.getenv('TELEGRAM_CHAT_ID')
XTS_USER_ID = os.getenv('XTS_USER_ID')
XTS_API_KEY = os.getenv('XTS_INTERACTIVE_API_KEY')
XTS_API_SECRET = os.getenv('XTS_INTERACTIVE_API_SECRET')
XTS_API_SOURCE = os.getenv('XTS_API_SOURCE', 'WEBAPI')
XTS_API_ROOT = os.getenv('XTS_API_ROOT', 'https://api.xts.com')

def send_telegram_notification(message):
    """Send notification to Telegram"""
    try:
        if not TOKEN_TELEGRAM or not CHAT_ID_TELEGRAM:
            logger.warning("Telegram credentials not configured, skipping notification")
            return False

        url = f'https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendMessage'
        data = {
            'chat_id': CHAT_ID_TELEGRAM,
            'text': message,
            'parse_mode': 'HTML'
        }

        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        return True

    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False

def refresh_xts_token():
    """Refresh XTS authentication token"""
    try:
        # Validate required environment variables
        if not all([XTS_USER_ID, XTS_API_KEY, XTS_API_SECRET]):
            error_msg = "Missing required XTS environment variables (XTS_USER_ID, XTS_INTERACTIVE_API_KEY, XTS_INTERACTIVE_API_SECRET)"
            logger.error(error_msg)
            send_telegram_notification(f"❌ XTS Token Refresh Failed: {error_msg}")
            return False

        logger.info("="*60)
        logger.info("Starting XTS token refresh process...")
        logger.info(f"XTS API Root: {XTS_API_ROOT}")
        logger.info(f"XTS User ID: {XTS_USER_ID}")
        logger.info("="*60)

        # Login to XTS API
        login_url = f"{XTS_API_ROOT}/interactive/user/session"
        payload = {
            "appKey": XTS_API_KEY,
            "secretKey": XTS_API_SECRET,
            "source": XTS_API_SOURCE
        }

        logger.info("Sending login request to XTS API...")
        response = requests.post(login_url, json=payload, timeout=30)

        # Log response status
        logger.info(f"XTS API Response Status: {response.status_code}")

        response.raise_for_status()
        result = response.json()

        # Check if login was successful
        if result.get("type") != "success":
            error_desc = result.get("description", "Unknown error")
            error_msg = f"XTS login failed: {error_desc}"
            logger.error(error_msg)
            logger.error(f"Full response: {result}")
            send_telegram_notification(f"❌ XTS Token Refresh Failed: {error_msg}")
            return False

        # Extract token and user ID
        token = result["result"]["token"]
        user_id = result["result"]["userID"]

        # Save token to file
        token_data = {
            "token": token,
            "userID": user_id,
            "timestamp": datetime.now().isoformat(),
            "refreshed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        token_file = "./xts_store_token.json"
        with open(token_file, "w") as f:
            json.dump(token_data, f, indent=4)

        # Set secure file permissions (owner read/write only)
        os.chmod(token_file, 0o600)

        logger.info("="*60)
        logger.info("✓ XTS token refreshed successfully!")
        logger.info(f"✓ Token saved to: {token_file}")
        logger.info(f"✓ User ID: {user_id}")
        logger.info(f"✓ Timestamp: {token_data['refreshed_at']}")
        logger.info("="*60)

        # Send success notification
        success_msg = f"✅ XTS Token Refreshed Successfully\n\nTime: {token_data['refreshed_at']}\nUser: {user_id}"
        send_telegram_notification(success_msg)

        return True

    except requests.exceptions.Timeout:
        error_msg = "XTS API request timed out"
        logger.error(error_msg)
        send_telegram_notification(f"❌ XTS Token Refresh Failed: {error_msg}")
        return False

    except requests.exceptions.RequestException as e:
        error_msg = f"XTS API request failed: {e}"
        logger.error(error_msg)
        send_telegram_notification(f"❌ XTS Token Refresh Failed: {error_msg}")
        return False

    except Exception as e:
        error_msg = f"Unexpected error during token refresh: {e}"
        logger.error(error_msg, exc_info=True)
        send_telegram_notification(f"❌ XTS Token Refresh Failed: {error_msg}")
        return False

def main():
    """Main entry point for cron execution"""
    logger.info("\n" + "="*60)
    logger.info("XTS Token Refresh Cron Job Started")
    logger.info(f"Execution Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*60)

    success = refresh_xts_token()

    if success:
        logger.info("✓ Cron job completed successfully")
        sys.exit(0)  # Success exit code
    else:
        logger.error("✗ Cron job failed")
        sys.exit(1)  # Failure exit code

if __name__ == "__main__":
    main()
