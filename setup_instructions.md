# Setup Instructions

## Prerequisites
- Python 3.8 or higher
- pip package manager

## Installation Steps

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Environment Configuration
1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` file with your credentials:
   ```bash
   nano .env
   ```

3. Fill in all required values:
   - `FYERS_CLIENT_ID`: Your Fyers API client ID
   - `FYERS_SECRET_KEY`: Your Fyers API secret key
   - `FYERS_FY_ID`: Your Fyers user ID
   - `FYERS_TOTP_KEY`: Your TOTP secret key
   - `FYERS_PIN`: Your trading PIN
   - `TELEGRAM_TOKEN`: Your Telegram bot token
   - `TELEGRAM_CHAT_ID`: Your Telegram chat ID

### 3. Initial Authentication
Run the login script to generate access tokens:
```bash
python fyerslogin.py
```

This will create a `store_token.json` file with your access tokens.

### 4. Update Symbol Data
The application will automatically update symbol data on startup, but you can also run it manually:
```bash
python -c "from nfolistupdate import nfo_update; nfo_update()"
```

### 5. Start the Application

#### Development Mode
```bash
python main.py
```

#### Production Mode with Gunicorn
```bash
gunicorn -w 4 -b 0.0.0.0:5002 main:app
```

## Security Notes

1. **Never commit sensitive files to version control:**
   - `.env` file
   - `config.ini` file (if it exists)
   - `store_token.json` file

2. **File Permissions:**
   The application automatically sets secure permissions on sensitive files:
   - `store_token.json`: 600 (owner read/write only)
   - CSV files: 640 (owner read/write, group read)

3. **Environment Variables:**
   All credentials are now loaded from environment variables instead of hardcoded values.

## Troubleshooting

### Authentication Issues
- Ensure your TOTP key is correct
- Check that your Fyers credentials are valid
- Verify the PIN is correct

### Symbol Data Issues
- Make sure you have internet connectivity to download symbol files
- Check that the CSV files are being created in the project directory

### Trading Issues
- Verify your Fyers account has trading permissions
- Check that you have sufficient balance for trades
- Ensure market hours for trading

## Log Files
- `trading.log`: Application logs
- Check logs for detailed error information

## Health Check
Test the application with a simple webhook:
```bash
curl -X POST http://localhost:5002/sha/test4 -d "hello"
```

You should receive a Telegram notification if everything is working correctly.