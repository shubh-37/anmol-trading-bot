# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based trading automation system that integrates with Fyers API for algorithmic trading. The application processes webhook messages from TradingView, parses trading signals, and executes orders through the Fyers trading platform.

## Key Dependencies

Install dependencies using:
```bash
pip install -r requirements.txt
```

Main dependencies:
- `fyers-apiv3`: Fyers trading API client
- `flask`: Web framework for webhook endpoints
- `pandas`: Data manipulation and analysis
- `pandas-ta`: Technical analysis indicators
- `pyotp`: TOTP authentication for Fyers login
- `requests`: HTTP client library
- `telethon`: Telegram client (if needed)
- `gunicorn`: WSGI server for production deployment

## Running the Application

### Development
```bash
python main.py
```
This starts the Flask development server on `0.0.0.0:5002`.

### Production
The application is configured for deployment with:
- `passenger_wsgi.py`: WSGI entry point for Passenger/Apache
- `gunicorn`: Alternative WSGI server (installed in requirements)

For gunicorn deployment:
```bash
gunicorn -w 4 -b 0.0.0.0:5002 main:app
```

## Architecture Overview

### Core Components

1. **main.py**: Main Flask application with webhook endpoint `/sha/test4`
   - Receives POST requests with trading signals
   - Parses messages using regex patterns
   - Executes trading logic through helper functions
   - Logs to both console and `stderr.log`

2. **fyerslogin.py**: Fyers API authentication module
   - Handles automated login with TOTP/PIN authentication
   - Generates and stores access tokens in `store_token.json`
   - Custom HTTP adapter for specific IP binding (if needed)

3. **fyres_strategy_helper.py**: Trading strategy implementation
   - Order placement functions (buy/sell side)
   - Position management (exit functions)
   - Option contract parsing and strike price calculations
   - Symbol mapping between TradingView and Fyers formats

4. **nfolistupdate.py**: Market data synchronization
   - Downloads updated symbol files from Fyers public URLs
   - Maintains local CSV files for different exchanges (NSE_FO, NSE_CM, BSE_CM, MCX_COM, etc.)

### Configuration

The application uses `config.ini` for sensitive configuration:
```ini
[telegram]
TOKEN_TELEGRAM = your_telegram_bot_token
TEST3_CHAT_ID = your_telegram_chat_id

[fyers]
redirect_uri = https://www.google.com
client_id = your_fyers_client_id
secret_key = your_fyers_secret_key
FY_ID = your_fyers_id
TOTP_KEY = your_totp_secret
PIN = your_trading_pin
```

### Data Flow

1. TradingView sends webhook POST to `/sha/test4`
2. Message parsed for trading signals containing "radhe" and "algo" keywords
3. Symbol mapping from TradingView format to Fyers format
4. Order execution through Fyers API
5. Trade data saved to daily CSV files in `data/` directory
6. Telegram notifications sent for confirmations

### Key Features

- **Message Processing**: Regex-based parsing of TradingView alert messages
- **Symbol Translation**: Converts TradingView symbols to Fyers exchange symbols
- **Position Management**: Handles entry/exit signals with quantity calculations
- **Error Handling**: Comprehensive logging and Telegram notifications
- **Data Persistence**: Daily CSV files for trade tracking

## Development Notes

- The application expects specific message formats from TradingView webhooks
- Authentication tokens are automatically refreshed through the login module
- All trading operations include safety checks and logging
- The system supports both futures and options trading
- Market data files are updated dynamically from Fyers public endpoints

## Security Considerations

- Never commit `config.ini` with real credentials
- The `store_token.json` file contains sensitive access tokens
- TOTP keys and PINs should be handled securely
- Consider IP whitelisting for webhook endpoints in production