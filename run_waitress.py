from waitress import serve
import logging
import os
import sys
import signal
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Global flag to track if server is shutting down
shutting_down = False


def send_telegram_message(message):
    """Send a message to Telegram via the Bot API."""
    try:
        import urllib.parse
        import requests

        TOKEN_TELEGRAM = os.getenv("TELEGRAM_TOKEN")
        TEST3_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

        if not TOKEN_TELEGRAM or not TEST3_CHAT_ID:
            logger.error("Telegram credentials not configured")
            return False

        formatted_message = urllib.parse.quote_plus(str(message))
        send_text = f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendMessage?chat_id={TEST3_CHAT_ID}&text={formatted_message}"

        response = requests.get(send_text, timeout=5)
        response.raise_for_status()
        logger.info("Telegram notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global shutting_down
    if shutting_down:
        return

    shutting_down = True
    logger.warning(f"Received signal {signum} - shutting down gracefully")
    send_telegram_message("🛑 Trading server is shutting down (Signal received)")
    sys.exit(0)


def register_signal_handlers():
    """Register signal handlers for graceful shutdown"""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def initialize_app():
    """Initialize all services before starting server"""
    try:
        logger.info("=" * 60)
        logger.info("Starting application initialization...")
        logger.info("=" * 60)

        # Update symbol data
        logger.info("Step 1/3: Updating NFO symbol data...")
        from nfolistupdate import nfo_update

        nfo_update()
        logger.info("✓ NFO symbol data updated successfully")

        # Initialize Fyers
        logger.info("Step 2/3: Initializing Fyers authentication...")
        from fyerslogin import auto_login

        auto_login()
        logger.info("✓ Fyers authentication completed")

        logger.info("=" * 60)
        logger.info("✓ All services initialized successfully!")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"✗ Initialization failed: {e}")
        logger.error("=" * 60)
        raise


if __name__ == "__main__":
    try:
        # Register signal handlers for graceful shutdown
        register_signal_handlers()

        # Initialize services first
        initialize_app()

        # Import app AFTER initialization
        from main import app

        # Get configuration from environment
        host = os.getenv("FLASK_HOST", "0.0.0.0")
        port = int(os.getenv("FLASK_PORT", 5035))
        threads = int(os.getenv("WAITRESS_THREADS", 4))

        # Print startup banner
        print("\n" + "=" * 60)
        print("🚀 Trading Bot Server Starting...")
        print("=" * 60)
        print(f"📍 Server Address: http://{host}:{port}")
        print(f"🔧 Worker Threads: {threads}")
        print(f"📊 Endpoints:")
        print(f"   • POST /fyers - Fyers trading webhook")
        print("=" * 60)
        print("✓ Server is ready to accept requests")
        print("✓ Press Ctrl+C to stop the server")
        print("=" * 60 + "\n")

        # Send startup notification
        startup_msg = (
            f"🚀 Trading server has started successfully!\n\n"
            f"📍 Server: http://{host}:{port}\n"
            f"🔧 Threads: {threads}\n"
            f"📊 Fyers Webhook: /sha/fyers\n"
        )
        send_telegram_message(startup_msg)

        # Start Waitress server
        serve(
            app,
            host=host,
            port=port,
            threads=threads,
            url_scheme="http",
            channel_timeout=120,
            cleanup_interval=30,
            asyncore_use_poll=True,
        )

    except KeyboardInterrupt:
        print("\n" + "=" * 60)
        print("🛑 Server stopped by user (Ctrl+C)")
        print("=" * 60)
        logger.info("Server stopped by user")
        if not shutting_down:
            send_telegram_message("🛑 Trading server stopped by user (Ctrl+C)")
        sys.exit(0)

    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ Failed to start server: {e}")
        print("=" * 60)
        logger.critical(f"Failed to start server: {e}", exc_info=True)
        send_telegram_message(f"❌ Trading server failed to start: {str(e)}")
        sys.exit(1)

    finally:
        # This will run if server exits for any reason
        if not shutting_down:
            send_telegram_message("🛑 Trading server has stopped unexpectedly")
