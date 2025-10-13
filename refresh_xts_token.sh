#!/bin/bash
#
# XTS Token Refresh Shell Wrapper
# This script is designed to be run as a cron job
# It handles virtual environment activation and logging
#
# Example crontab entry (runs daily at 9:00 AM):
# 0 9 * * * /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
#

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Change to script directory
cd "$SCRIPT_DIR" || {
    echo "ERROR: Failed to change to script directory: $SCRIPT_DIR"
    exit 1
}

# Log file for cron output
CRON_LOG="$SCRIPT_DIR/cron_output.log"

# Print start banner
echo "=================================================="
echo "XTS Token Refresh - $(date '+%Y-%m-%d %H:%M:%S')"
echo "Working Directory: $SCRIPT_DIR"
echo "=================================================="

# Check if .env file exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: .env file not found in $SCRIPT_DIR"
    echo "Please create .env file with required XTS credentials"
    exit 1
fi

# Activate virtual environment if it exists
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "Activating virtual environment..."
    source "$SCRIPT_DIR/venv/bin/activate" || {
        echo "ERROR: Failed to activate virtual environment"
        exit 1
    }
    echo "✓ Virtual environment activated"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "Activating virtual environment (.venv)..."
    source "$SCRIPT_DIR/.venv/bin/activate" || {
        echo "ERROR: Failed to activate virtual environment"
        exit 1
    }
    echo "✓ Virtual environment activated"
else
    echo "WARNING: No virtual environment found (venv or .venv)"
    echo "Using system Python..."
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found in PATH"
    exit 1
fi

# Print Python version
echo "Python version: $(python3 --version)"

# Check if required packages are installed
echo "Checking required packages..."
python3 -c "import requests, dotenv" 2>/dev/null || {
    echo "ERROR: Required Python packages not installed"
    echo "Please run: pip install -r requirements.txt"
    exit 1
}
echo "✓ Required packages found"

# Run the token refresh script
echo ""
echo "Running XTS token refresh script..."
echo "--------------------------------------------------"

python3 "$SCRIPT_DIR/xts_token_refresh.py"
EXIT_CODE=$?

echo "--------------------------------------------------"

# Check exit code
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ Token refresh completed successfully (exit code: $EXIT_CODE)"
else
    echo "✗ Token refresh failed (exit code: $EXIT_CODE)"
fi

echo "=================================================="
echo "Completed at: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="
echo ""

# Exit with the same code as the Python script
exit $EXIT_CODE
