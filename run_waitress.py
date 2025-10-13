from waitress import serve
import logging
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

def initialize_app():
    """Initialize all services before starting server"""
    try:
        logger.info("="*60)
        logger.info("Starting application initialization...")
        logger.info("="*60)
        
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

        # Initialize XTS
        logger.info("Step 3/3: Initializing XTS authentication...")
        from xts_strategy_helper import initialize_xts_client
        initialize_xts_client()
        logger.info("✓ XTS authentication completed")
        
        logger.info("="*60)
        logger.info("✓ All services initialized successfully!")
        logger.info("="*60)
        return True
        
    except Exception as e:
        logger.error("="*60)
        logger.error(f"✗ Initialization failed: {e}")
        logger.error("="*60)
        raise

if __name__ == '__main__':
    try:
        # Initialize services first
        initialize_app()
        
        # Import app AFTER initialization
        from main import app
        
        # Get configuration from environment
        host = os.getenv('FLASK_HOST', '0.0.0.0')
        port = int(os.getenv('FLASK_PORT', 5002))
        threads = int(os.getenv('WAITRESS_THREADS', 4))
        
        # Print startup banner
        print("\n" + "="*60)
        print("🚀 Trading Bot Server Starting...")
        print("="*60)
        print(f"📍 Server Address: http://{host}:{port}")
        print(f"🔧 Worker Threads: {threads}")
        print(f"📊 Endpoints:")
        print(f"   • POST /sha/fyers - Fyers trading webhook")
        print(f"   • POST /sha/xts   - XTS trading webhook")
        print("="*60)
        print("✓ Server is ready to accept requests")
        print("✓ Press Ctrl+C to stop the server")
        print("="*60 + "\n")
        
        # Start Waitress server
        serve(
            app,
            host=host,
            port=port,
            threads=threads,
            url_scheme='http',
            channel_timeout=120,
            cleanup_interval=30,
            asyncore_use_poll=True
        )
        
    except KeyboardInterrupt:
        print("\n" + "="*60)
        print("🛑 Server stopped by user (Ctrl+C)")
        print("="*60)
        logger.info("Server stopped by user")
        sys.exit(0)
        
    except Exception as e:
        print("\n" + "="*60)
        print(f"❌ Failed to start server: {e}")
        print("="*60)
        logger.critical(f"Failed to start server: {e}", exc_info=True)
        sys.exit(1)