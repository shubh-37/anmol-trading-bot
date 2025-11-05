from flask import Flask, request, jsonify, abort
import os
import sys
import urllib.parse
import requests
import re
import logging
from fyres_strategy_helper import *
from xts_strategy_helper import *
from nfolistupdate import nfo_update
from waitress import serve
import csv
import datetime
from dotenv import load_dotenv
import hashlib
import hmac
import redis

# Load environment variables
load_dotenv()
# Flask app initialization
app = Flask(__name__)

redis_client = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True
)

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading.log", mode="a")
    ],
)

# Load configuration from environment
TOKEN_TELEGRAM = os.getenv('TELEGRAM_TOKEN')
TEST3_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5002))
POSITION_KEY_PREFIX = "xts_position:"

# Validate required environment variables
if not all([TOKEN_TELEGRAM, TEST3_CHAT_ID]):
    raise ValueError("Missing required environment variables. Check .env file.")

def get_position_from_redis(symbol):
    """Get current net position (in lots) for a symbol from Redis"""
    try:
        key = f"{POSITION_KEY_PREFIX}{symbol}"
        position = redis_client.get(key)
        if position is None:
            return 0
        return int(position)
    except Exception as e:
        logging.error(f"Error reading position from Redis for {symbol}: {e}")
        return 0

def set_position_in_redis(symbol, position_lots):
    """Store current net position (in lots) for a symbol in Redis"""
    try:
        key = f"{POSITION_KEY_PREFIX}{symbol}"
        redis_client.set(key, int(position_lots))
        logging.info(f"Stored position in Redis: {symbol} = {position_lots} lots")
    except Exception as e:
        logging.error(f"Error storing position in Redis for {symbol}: {e}")
        raise

def clear_position_in_redis(symbol):
    """Clear/delete position for a symbol from Redis"""
    try:
        key = f"{POSITION_KEY_PREFIX}{symbol}"
        redis_client.delete(key)
        logging.info(f"Cleared position in Redis for {symbol}")
    except Exception as e:
        logging.error(f"Error clearing position in Redis for {symbol}: {e}")

def get_all_positions_from_redis():
    """Get all positions from Redis (useful for debugging/monitoring)"""
    try:
        pattern = f"{POSITION_KEY_PREFIX}*"
        keys = redis_client.keys(pattern)
        positions = {}
        for key in keys:
            symbol = key.replace(POSITION_KEY_PREFIX, "")
            position = redis_client.get(key)
            positions[symbol] = int(position) if position else 0
        return positions
    except Exception as e:
        logging.error(f"Error reading all positions from Redis: {e}")
        return {}

def save_to_csv(parsed_data):
    """Save trading data to CSV with proper validation and error handling - NEW FORMAT"""
    try:
        # Input validation
        if not parsed_data or not isinstance(parsed_data, dict):
            raise ValueError("Invalid parsed_data provided")

        # NEW: Updated required fields for new format
        required_fields = [
            "exchange", "symbol", "buyfut", "action",
            "contracts", "position_size", "close_price", "order_type",
            "time_utc", "time_ist"
        ]

        for field in required_fields:
            if field not in parsed_data:
                raise ValueError(f"Missing required field: {field}")

        # Ensure the 'data' directory exists with proper permissions
        folder_name = "data"
        os.makedirs(folder_name, mode=0o755, exist_ok=True)

        # Define the filename with today's date
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        file_name = os.path.join(folder_name, f"{date_str}.csv")

        # Sanitize data for CSV
        def sanitize_value(value):
            if value is None:
                return ""
            return str(value).replace('\n', ' ').replace('\r', ' ')[:100]  # Limit length

        # Extract and sanitize values - NEW FORMAT
        row = [
            sanitize_value(parsed_data["exchange"]),
            sanitize_value(parsed_data["symbol"]),
            sanitize_value(parsed_data["buyfut"]),
            sanitize_value(parsed_data["action"]),
            sanitize_value(parsed_data["contracts"]),
            sanitize_value(parsed_data["position_size"]),
            sanitize_value(parsed_data["close_price"]),
            sanitize_value(parsed_data["order_type"]),
            sanitize_value(parsed_data["time_utc"]),
            sanitize_value(parsed_data["time_ist"]),
            sanitize_value(parsed_data.get("source", "")),
            "pending"  # Default status
        ]

        # Define the CSV headers - NEW FORMAT
        headers = [
            "exchange", "symbol", "buyfut", "action",
            "contracts", "position_size", "close_price", "order_type",
            "time_utc", "time_ist", "source", "status"
        ]

        # Write the row to the CSV file
        write_header = not os.path.exists(file_name)
        with open(file_name, mode="a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if write_header:
                writer.writerow(headers)
            writer.writerow(row)

        # Set secure file permissions
        os.chmod(file_name, 0o640)

        logger.info(f"Data saved to {file_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to save CSV data: {e}")
        return False


def send_telegram_message(message, chat_id):
    """Send a message to Telegram via the Bot API."""
    if isinstance(message, bytes):  # If it's bytes, decode it
        message = message.decode("utf-8")
    elif not isinstance(message, str):  # If it's some other type, convert it
        message = str(message)

    formatted_message = urllib.parse.quote_plus(message)  # Ensure this gets a string
    send_text = f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendMessage?chat_id={chat_id}&text={formatted_message}"

    try:
        response = requests.get(send_text)
        response.raise_for_status()  # Raise an exception for HTTP errors
        logging.info("Message sent successfully")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message. Error: {str(e)}")
        return False


def validate_json_payload(data):
    """Validate JSON payload structure"""
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object")

    # Check for required top-level keys
    required_keys = ["strategy", "symbol", "price", "meta"]
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

    # Validate strategy object - NEW: removed 'comment', kept action, contracts, position_size
    strategy_required = ["action", "contracts", "position_size"]
    for key in strategy_required:
        if key not in data["strategy"]:
            raise ValueError(f"Missing required strategy field: {key}")

    # Validate symbol object
    symbol_required = ["exchange", "ticker"]
    for key in symbol_required:
        if key not in data["symbol"]:
            raise ValueError(f"Missing required symbol field: {key}")

    # Validate price object
    price_required = ["close"]
    for key in price_required:
        if key not in data["price"]:
            raise ValueError(f"Missing required price field: {key}")

    # Validate meta object
    if "tag" not in data["meta"]:
        raise ValueError("Missing required meta field: tag")

    return True


def parse_json_message(json_data):
    """Parse JSON trading message with proper validation and error handling - NEW FORMAT"""
    try:
        # Validate the payload structure
        validate_json_payload(json_data)

        # Check if the tag contains "radhe algo"
        tag = json_data["meta"].get("tag", "").lower()
        if "radhe" not in tag or "algo" not in tag:
            logger.debug("Message does not contain required keywords in tag")
            return None

        result = {}

        # Extract exchange and symbol
        result["exchange"] = json_data["symbol"]["exchange"]
        symbol_raw = json_data["symbol"]["ticker"]

        # Process the symbol to determine if it's futures
        # Check if symbol ends with ! or if it's explicitly marked
        if symbol_raw.endswith("!"):
            result["symbol"] = re.sub(r"[\d!]+$", "", symbol_raw)
            result["buyfut"] = 1
        else:
            # For options/other instruments
            result["symbol"] = symbol_raw
            result["buyfut"] = 0

        # NEW: Extract action (buy/sell), contracts, and position_size
        result["action"] = json_data["strategy"]["action"].strip().lower()

        try:
            result["contracts"] = int(json_data["strategy"]["contracts"])
            result["position_size"] = int(json_data["strategy"]["position_size"])
        except (ValueError, TypeError):
            logger.error("Invalid contracts or position_size format")
            return None

        # Extract close price (this is what we'll use for order placement)
        try:
            result["close_price"] = float(json_data["price"]["close"])
        except (ValueError, TypeError):
            logger.error("Invalid close price format")
            return None

        # Extract order type (default to MKT if not specified)
        result["order_type"] = json_data["meta"].get("order_type", "MKT").upper()

        # Handle time fields - use current time if not provided
        current_utc = datetime.datetime.utcnow()
        result["time_utc"] = current_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        ist_time = current_utc + datetime.timedelta(hours=5, minutes=30)
        result["time_ist"] = ist_time.strftime("%Y-%m-%dT%H:%M:%S")

        # Extract source for tracking
        result["source"] = json_data["meta"].get("source", "")

        logger.debug(f"Successfully parsed JSON message: {result}")
        return result

    except ValueError as e:
        logger.error(f"Validation error in JSON message: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing JSON message: {e}")
        return None


def validate_input_message(message):
    """Validate and sanitize input message"""
    if not message:
        raise ValueError("Empty message")

    if not isinstance(message, str):
        message = str(message)

    # Length validation
    if len(message) > 10000:
        raise ValueError("Message too long")

    # Basic sanitization - remove potential script tags and other dangerous content
    dangerous_patterns = [
        r'<script[^>]*>.*?</script>',
        r'javascript:',
        r'vbscript:',
        r'onload=',
        r'onerror='
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, message, re.IGNORECASE):
            raise ValueError("Message contains potentially dangerous content")

    return message


def parse_message(message):
    """Parse trading message with proper validation and error handling (Legacy text format)"""
    try:
        # Validate input
        message = validate_input_message(message)

        # Check if both required keywords are in the message
        if "radhe" not in message.lower() or "algo" not in message.lower():
            logger.debug("Message does not contain required keywords")
            return None

        # Extract data with better error handling
        result = {}

        # Extract exchange and symbol
        filled_on_match = re.search(r"filled on (\S+):(\S+)", message)
        if filled_on_match:
            result["exchange"] = filled_on_match.group(1)
            symbol_raw = filled_on_match.group(2)

            # Process the symbol
            if symbol_raw.endswith("!"):
                result["symbol"] = re.sub(r"[\d!]+$", "", symbol_raw)
                result["buyfut"] = 1
            else:
                result["symbol"] = re.sub(r"[!.]+$", "", symbol_raw)
                result["buyfut"] = 0
        else:
            logger.warning("Could not extract exchange/symbol from message")
            return None

        # Extract position
        position_match = re.search(r"New strategy position is ([\-\d]+)", message)
        if position_match:
            result["new_strategy_position"] = position_match.group(1)
        else:
            logger.warning("Could not extract position from message")
            return None

        # Extract comment
        comment_match = re.search(r"comment\s*=\s*([^\n]+)", message, re.IGNORECASE)
        if comment_match:
            result["comment"] = comment_match.group(1).strip()[:100]  # Limit length

        # Extract open price
        open_price_match = re.search(r"open\s*:\s*([\d.]+)", message)
        if open_price_match:
            try:
                result["open_price"] = float(open_price_match.group(1))
            except ValueError:
                logger.warning("Invalid open price format")

        # Extract order type
        order_type_match = re.search(r"order_type\s*:\s*(\S+)", message, re.IGNORECASE)
        if order_type_match:
            result["order_type"] = order_type_match.group(1)[:20]  # Limit length

        # Extract time
        time_match = re.search(r"time\s*:\s*([\d\-T:Z]+)", message)
        if time_match:
            time_utc = time_match.group(1)
            try:
                utc_time = datetime.datetime.strptime(time_utc, "%Y-%m-%dT%H:%M:%SZ")
                ist_time = utc_time + datetime.timedelta(hours=5, minutes=30)
                result["time_utc"] = time_utc
                result["time_ist"] = ist_time.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                logger.warning("Invalid time format")

        # Extract interval
        interval_match = re.search(r"interval\s*:\s*(\S+)", message)
        if interval_match:
            result["interval"] = interval_match.group(1)[:20]  # Limit length

        # Ensure we have minimum required fields
        required_fields = ["exchange", "symbol", "new_strategy_position"]
        if not all(field in result for field in required_fields):
            logger.warning("Missing required fields in parsed message")
            return None

        logger.debug(f"Successfully parsed message: {result}")
        return result

    except Exception as e:
        logger.error(f"Error parsing message: {e}")
        return None


def order_king_executer(result):

    if result:
        print(result)
        logging.debug(f"result data: {result}")
        exchange = result["exchange"]
        main_symbol = result["symbol"]
        buyfut = int(result["buyfut"])

        new_strategy_position = int(result["new_strategy_position"])
        comment = result["comment"]
        open_price = float(result["open_price"])
        order_type = result["order_type"]
        print("Extracted Values:")
        print("Symbol:", main_symbol)
        print("New Strategy Position:", new_strategy_position)
        print("Comment:", comment)
        print("Open Price:", open_price)
        print("exchnage :", exchange)

        logging.debug(f"buyfut data: {buyfut},type: {type(buyfut)}")

        if buyfut == 1:
            print(f"Symbol: {main_symbol} -> use future chart for this")
            first_symbol, first_symbol_lot = get_future_name(
                symbol=main_symbol, exchnge=exchange
            )
        else:
            ext_value = extract_option_details(main_symbol)
            if ext_value:
                main_symbol = ext_value["main_symbol"]
                date = ext_value["date"]
                option_type = ext_value["option_type"]
                strike = ext_value["strike"]
                (
                    first_symbol,
                    first_main_symbol,
                    first_symbol_lot,
                    first_expiry_date,
                    main_ss,
                ) = getting_strike(
                    symbol=main_symbol,
                    option_type=option_type,
                    strike=strike,
                    exchnge=exchange,
                    date=date,
                )
            else:
                print("tradingview symbol not found")
        print(first_symbol, first_symbol_lot)
        # first_symbol, first_main_symbol, first_symbol_lot, first_expiry_date, main_ss = getting_strike(symbol=main_symbol, option_type=option_type, strike=strike, date=date)
        first_symbol = str(first_symbol)
        first_symbol_lot = int(first_symbol_lot)
        new_strategy_position = first_symbol_lot * new_strategy_position

        if first_symbol is not None:

            if comment == "exit all ":
                print("exit single order called ")
                exit_single_order(first_symbol)
            elif (
                comment == "Remaining Short Exit"
                or comment == "Stop Loss Short"
                or comment == "Short SL"
                or comment == "Short TP"
                or comment == "Short BE"
                or comment == "Short Exit"
                or comment == "Close entry(s) order Short Entry"
            ):
                exit_only_sell_trades(symbol=first_symbol)
            elif (
                comment == "Stop Loss Long Exit"
                or comment == "Remaining Long Exit"
                or comment == "Long SL"
                or comment == "Long TP"
                or comment == "Long BE"
                or comment == "Long Exit"
                or comment == "Close entry(s) order Long Entry"
            ):
                exit_only_buy_trades(symbol=first_symbol)

            elif comment == "Short Entry":
                print("short entry called ")
                order_placement_sell_side(
                    symbol=first_symbol,
                    qty=new_strategy_position,
                    limitPrice=open_price,
                    order_type=order_type,
                )

            elif comment == "Long Entry":
                print("long entry called")
                order_placement_buy_side(
                    symbol=first_symbol,
                    qty=new_strategy_position,
                    limitPrice=open_price,
                    order_type=order_type,
                )
            elif (
                comment == "Exit fifty at two x"
                or comment == "long exit fifty at three x"
            ):
                print("half qty exit thing called ")
                exit_half_position(symbol=first_symbol, match_qty=new_strategy_position)
            else:
                print("no condition satisfy ")
        else:
            send_telegram_message("first symbol is none ", chat_id=TEST3_CHAT_ID)

    else:
        print("Message ignored due to missing keywords.")
        send_telegram_message("Message ignored due to missing keywords.", chat_id=TEST3_CHAT_ID)


def get_instrument_details(symbol, exchange):
    """
    Get exchange segment and instrument ID from CSV files for XTS
    
    Args:
        symbol: Trading symbol (e.g., 'NIFTY27MAR2532000CE')
        exchange: Exchange name ('NSE', 'MCX', 'BSE')
    
    Returns:
        tuple: (exchange_segment, exchange_instrument_id) or (None, None)
    """
    try:
        # Exchange configuration with proper XTS segment names
        exchange_config = {
            "NSE": {
                "filename": "NSE_FO.csv", 
                "exchange_segment": "NSEFO"  # NSE Futures & Options
            },
            "NSE_CM": {
                "filename": "NSE_CM.csv", 
                "exchange_segment": "NSECM"  # NSE Capital Market (Cash)
            },
            "MCX": {
                "filename": "MCX_COM.csv", 
                "exchange_segment": "MCXFO"  # MCX Futures & Options
            },
            "BSE": {
                "filename": "BSE_FO.csv", 
                "exchange_segment": "BSEFO"  # BSE Futures & Options
            },
            "BSE_CM": {
                "filename": "BSE_CM.csv", 
                "exchange_segment": "BSECM"  # BSE Capital Market (Cash)
            }
        }
        
        if exchange not in exchange_config:
            logger.error(f"Unsupported exchange: {exchange}")
            return None, None
        
        config = exchange_config[exchange]
        local_filename = config["filename"]
        exchange_segment = config["exchange_segment"]
        
        # Check if file exists
        if not os.path.exists(local_filename):
            logger.error(f"Symbol data file not found: {local_filename}")
            return None, None
        
        column_names = [
            "num", "sym des", "exch no", "lot size", "tick size", "blank",
            "timing", "date", "Time", "symbol name",
            "ID 1", "id 2", "token no", "symbol main name", "ISIN",
            "strike", "option type", "pass", "none", "0", "0.0"
        ]
        
        df = pd.read_csv(local_filename, header=None, names=column_names)
        
        # Find the row matching the symbol
        matched_row = df[df["symbol name"] == symbol]
        
        if matched_row.empty:
            logger.warning(f"No instrument found for symbol: {symbol}")
            return None, None
        
        # Get the token number (exchange instrument ID)
        exchange_instrument_id = int(matched_row.iloc[0]["token no"])
        
        logger.debug(f"Found instrument details - Segment: {exchange_segment}, ID: {exchange_instrument_id}")
        return exchange_segment, exchange_instrument_id
        
    except Exception as e:
        logger.error(f"Error getting instrument details: {e}")
        return None, None

def order_king_executer_xts(result, product_type="NRML"):
    """
    Execute trading orders for XTS platform based on webhook data - Redis-backed version
    Positions are now stored in Redis for persistence across restarts
    """
    if not result:
        print("Message ignored due to missing keywords.")
        send_telegram_message("⚠️ Message ignored due to missing keywords.", chat_id=TEST3_CHAT_ID)
        return

    print(result)
    logging.debug(f"result data: {result}")

    # ---- Extract fields (expecting flat parsed payload) ----
    exchange = result.get("exchange") or result.get("symbol", {}).get("exchange")
    main_symbol = result.get("symbol") or result.get("symbol", {}).get("ticker")
    buyfut = int(result.get("buyfut", 0))
    contracts = int(result.get("contracts", 0))
    position_size = int(result.get("position_size", 0))
    
    # Fallback to 'contracts' if position_size not provided
    if "position_size" not in result and contracts != 0:
        position_size = contracts
    
    close_price = float(result.get("close_price") or result.get("price", {}).get("close", 0.0))
    order_type = result.get("order_type") or (result.get("meta") or {}).get("order_type", "MKT")

    print("=== Extracted Values ===")
    print(f"Symbol: {main_symbol}")
    print(f"Contracts (raw): {contracts}")
    print(f"Position Size (signed lots): {position_size}")
    print(f"Close Price: {close_price}")
    print(f"Order Type: {order_type}")
    print(f"Exchange: {exchange}")

    # ---- Resolve tradable symbol & lot size ----
    if buyfut == 1:
        first_symbol, first_symbol_lot = get_future_name(symbol=main_symbol, exchange=exchange)
    else:
        ext_value = extract_option_details(main_symbol)
        if ext_value:
            main_symbol = ext_value["main_symbol"]
            date = ext_value["date"]
            option_type = ext_value["option_type"]
            strike = ext_value["strike"]
            (
                first_symbol,
                first_main_symbol,
                first_symbol_lot,
                first_expiry_date,
                main_ss,
            ) = getting_strike(
                symbol=main_symbol,
                option_type=option_type,
                strike=strike,
                exchnge=exchange,
                date=date,
            )
        else:
            error_msg = "❌ TradingView symbol not found"
            print(error_msg)
            send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
            return

    if first_symbol is None:
        error_msg = "❌ First symbol is None - cannot proceed"
        print(error_msg)
        send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
        return

    first_symbol = str(first_symbol)
    first_symbol_lot = int(first_symbol_lot)

    incoming_qty_units = abs(position_size) * first_symbol_lot

    print(f"Trading Symbol: {first_symbol}")
    print(f"Lot Size: {first_symbol_lot}")
    print(f"Incoming trade (lots): {position_size}, units: {incoming_qty_units}")

    # ---- Instrument details for XTS ----
    exchange_segment, exchange_instrument_id = get_instrument_details(first_symbol, exchange)
    if exchange_segment is None or exchange_instrument_id is None:
        error_msg = f"❌ Could not get instrument details for {first_symbol}"
        print(error_msg)
        send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
        return

    print(f"XTS Details - Segment: {exchange_segment}, Instrument ID: {exchange_instrument_id}, Product: {product_type}")

    # ---- Get current net position from Redis ----
    current_net_lots = get_position_from_redis(first_symbol)
    
    # Optionally sync with live XTS position
    try:
        if "get_xts_position" in globals():
            live_units = get_xts_position(first_symbol)
            if live_units is not None:
                live_lots = int(live_units // first_symbol_lot) if first_symbol_lot else 0
                current_net_lots = live_lots
                set_position_in_redis(first_symbol, current_net_lots)
    except Exception as _e:
        logging.debug(f"get_xts_position failed: {_e}")

    print(f"Current net (lots) for {first_symbol}: {current_net_lots}")

    # ---- Core logic per your requested rules ----
    try:
        # 1) STOPLOSS / position_size == 0: close existing net positions if any
        if position_size == 0:
            print("Received stoploss (position_size == 0) signal")
            if current_net_lots == 0:
                msg = f"⚠️ SKIPPING stoploss for {first_symbol} — no net position exists"
                print(msg)
                send_telegram_message(msg, chat_id=TEST3_CHAT_ID)
                return
            else:
                close_side = "BUY" if current_net_lots < 0 else "SELL"
                qty_to_close = abs(current_net_lots) * first_symbol_lot
                print(f"Closing existing net for {first_symbol}: side={close_side}, qty={qty_to_close}")
                send_telegram_message(
                    f"📛 Closing net position for {first_symbol}\n"
                    f"Side: {close_side}\nQty: {qty_to_close}\nPrice: {close_price}\nType: {order_type}",
                    chat_id=TEST3_CHAT_ID,
                )

                place_market_order(
                    symbol=first_symbol,
                    qty=qty_to_close,
                    limit_price=close_price,
                    order_type=order_type,
                    buy_sell=close_side,
                    product_type=product_type,
                    exchange_segment=exchange_segment,
                    exchange_instrument_id=exchange_instrument_id,
                )

                # Clear position in Redis
                set_position_in_redis(first_symbol, 0)
                print(f"Net for {first_symbol} set to 0 after closing.")
                return

        # 2) Non-zero position_size: treat as signed trade lots
        if position_size == current_net_lots:
            msg = f"⚖️ Ignored: incoming position_size ({position_size}) equals current net ({current_net_lots}) for {first_symbol}"
            print(msg)
            send_telegram_message(msg, chat_id=TEST3_CHAT_ID)
            return

        trade_side = "BUY" if position_size > 0 else "SELL"
        trade_qty_units = abs(position_size) * first_symbol_lot

        print(f"Executing trade for {first_symbol}: side={trade_side}, units={trade_qty_units}")
        send_telegram_message(
            f"🚀 Executing trade for {first_symbol}\n"
            f"Side: {trade_side}\n"
            f"Lots: {position_size}\n"
            f"Units: {trade_qty_units}\n"
            f"Price: {close_price}\n"
            f"Order Type: {order_type}",
            chat_id=TEST3_CHAT_ID,
        )

        place_market_order(
            symbol=first_symbol,
            qty=trade_qty_units,
            limit_price=close_price,
            order_type=order_type,
            buy_sell=trade_side,
            product_type=product_type,
            exchange_segment=exchange_segment,
            exchange_instrument_id=exchange_instrument_id,
        )

        # Update position in Redis
        new_net = current_net_lots + position_size
        set_position_in_redis(first_symbol, new_net)
        print(f"Updated net for {first_symbol}: {current_net_lots} -> {new_net}")
        return

    except Exception as e:
        error_msg = f"❌ Error executing order: {str(e)}"
        logging.error(error_msg, exc_info=True)
        send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
        raise

# def order_king_executer_xts(result, product_type="NRML"):
#     """
#     Execute trading orders for XTS platform based on webhook data - Simplified logic
#     Implemented per user's specified rules:
#       - position_size == 0 -> stoploss/close: only act if there is an existing net position; otherwise skip
#       - position_size != 0 -> treat as signed trade lots (positive buy, negative sell)
#       - identical repeated signals that wouldn't change net are ignored
#     """
#     if not result:
#         print("Message ignored due to missing keywords.")
#         send_telegram_message("⚠️ Message ignored due to missing keywords.", chat_id=TEST3_CHAT_ID)
#         return

#     print(result)
#     logging.debug(f"result data: {result}")

#     # ---- Extract fields (expecting flat parsed payload) ----
#     exchange = result.get("exchange") or result.get("symbol", {}).get("exchange")
#     main_symbol = result.get("symbol") or result.get("symbol", {}).get("ticker")
#     buyfut = int(result.get("buyfut", 0))
#     # Note: action may be present but we use position_size to determine signed trade
#     # action_text = result.get("action", "").lower()
#     contracts = int(result.get("contracts", 0))
#     position_size = int(result.get("position_size", 0))  # signed lots from webhook
#     # Fallback to 'contracts' if position_size not provided
#     if "position_size" not in result and contracts != 0:
#         position_size = contracts
#     # price fields
#     close_price = float(result.get("close_price") or result.get("price", {}).get("close", 0.0))
#     order_type = result.get("order_type") or (result.get("meta") or {}).get("order_type", "MKT")

#     print("=== Extracted Values ===")
#     print(f"Symbol: {main_symbol}")
#     print(f"Contracts (raw): {contracts}")
#     print(f"Position Size (signed lots): {position_size}")
#     print(f"Close Price: {close_price}")
#     print(f"Order Type: {order_type}")
#     print(f"Exchange: {exchange}")

#     # ---- Resolve tradable symbol & lot size ----
#     if buyfut == 1:
#         first_symbol, first_symbol_lot = get_future_name(symbol=main_symbol, exchange=exchange)
#     else:
#         ext_value = extract_option_details(main_symbol)
#         if ext_value:
#             main_symbol = ext_value["main_symbol"]
#             date = ext_value["date"]
#             option_type = ext_value["option_type"]
#             strike = ext_value["strike"]
#             (
#                 first_symbol,
#                 first_main_symbol,
#                 first_symbol_lot,
#                 first_expiry_date,
#                 main_ss,
#             ) = getting_strike(
#                 symbol=main_symbol,
#                 option_type=option_type,
#                 strike=strike,
#                 exchnge=exchange,
#                 date=date,
#             )
#         else:
#             error_msg = "❌ TradingView symbol not found"
#             print(error_msg)
#             send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#             return

#     if first_symbol is None:
#         error_msg = "❌ First symbol is None - cannot proceed"
#         print(error_msg)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         return

#     first_symbol = str(first_symbol)
#     first_symbol_lot = int(first_symbol_lot)

#     # compute qty for incoming trade (lots -> units)
#     incoming_qty_units = abs(position_size) * first_symbol_lot

#     print(f"Trading Symbol: {first_symbol}")
#     print(f"Lot Size: {first_symbol_lot}")
#     print(f"Incoming trade (lots): {position_size}, units: {incoming_qty_units}")

#     # ---- Instrument details for XTS ----
#     exchange_segment, exchange_instrument_id = get_instrument_details(first_symbol, exchange)
#     if exchange_segment is None or exchange_instrument_id is None:
#         error_msg = f"❌ Could not get instrument details for {first_symbol}"
#         print(error_msg)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         return

#     print(f"XTS Details - Segment: {exchange_segment}, Instrument ID: {exchange_instrument_id}, Product: {product_type}")

#     # ---- Maintain local net positions by symbol (lots) ----
#     # NOTE: Persist this dict in production (file/DB) to survive restarts.
#     global current_net_positions
#     try:
#         current_net_positions
#     except NameError:
#         current_net_positions = {}

#     # If available, try to get live XTS position (signed units) and convert to lots for truth
#     current_net_lots = current_net_positions.get(first_symbol, 0)  # signed lots
#     try:
#         # optional helper: if you have get_xts_position returning signed units, use it to correct local state
#         if "get_xts_position" in globals():
#             live_units = get_xts_position(first_symbol)  # expect signed units (positive long, negative short)
#             if live_units is not None:
#                 # convert units -> lots (integer division)
#                 live_lots = int(live_units // first_symbol_lot) if first_symbol_lot else 0
#                 current_net_lots = live_lots
#                 current_net_positions[first_symbol] = current_net_lots
#     except Exception as _e:
#         # ignore failures from live query and fall back to local state
#         logging.debug(f"get_xts_position failed: {_e}")

#     print(f"Current net (lots) for {first_symbol}: {current_net_lots}")

#     # ---- Core logic per your requested rules ----
#     try:
#         # 1) STOPLOSS / position_size == 0: close existing net positions if any, else ignore
#         if position_size == 0:
#             print("Received stoploss (position_size == 0) signal")
#             if current_net_lots == 0:
#                 msg = f"⚠️ SKIPPING stoploss for {first_symbol} — no net position exists"
#                 print(msg)
#                 send_telegram_message(msg, chat_id=TEST3_CHAT_ID)
#                 return
#             else:
#                 # Close entire net position by placing opposite-side order
#                 close_side = "BUY" if current_net_lots < 0 else "SELL"
#                 qty_to_close = abs(current_net_lots) * first_symbol_lot
#                 print(f"Closing existing net for {first_symbol}: side={close_side}, qty={qty_to_close}")
#                 send_telegram_message(
#                     f"📛 Closing net position for {first_symbol}\n"
#                     f"Side: {close_side}\nQty: {qty_to_close}\nPrice: {close_price}\nType: {order_type}",
#                     chat_id=TEST3_CHAT_ID,
#                 )

#                 place_market_order(
#                     symbol=first_symbol,
#                     qty=qty_to_close,
#                     limit_price=close_price,
#                     order_type=order_type,
#                     buy_sell=close_side,
#                     product_type=product_type,
#                     exchange_segment=exchange_segment,
#                     exchange_instrument_id=exchange_instrument_id,
#                 )

#                 # update local net to zero
#                 current_net_positions[first_symbol] = 0
#                 print(f"Net for {first_symbol} set to 0 after closing.")
#                 return

#         # 2) Non-zero position_size: treat as signed trade lots to execute
#         # If incoming instruction equals current net exactly — ignore (duplicate)
#         if position_size == current_net_lots:
#             msg = f"⚖️ Ignored: incoming position_size ({position_size}) equals current net ({current_net_lots}) for {first_symbol}"
#             print(msg)
#             send_telegram_message(msg, chat_id=TEST3_CHAT_ID)
#             return

#         # Otherwise execute the incoming trade exactly as specified (signed lots)
#         trade_side = "BUY" if position_size > 0 else "SELL"
#         trade_qty_units = abs(position_size) * first_symbol_lot

#         print(f"Executing trade for {first_symbol}: side={trade_side}, units={trade_qty_units}")
#         send_telegram_message(
#             f"🚀 Executing trade for {first_symbol}\n"
#             f"Side: {trade_side}\n"
#             f"Lots: {position_size}\n"
#             f"Units: {trade_qty_units}\n"
#             f"Price: {close_price}\n"
#             f"Order Type: {order_type}",
#             chat_id=TEST3_CHAT_ID,
#         )

#         place_market_order(
#             symbol=first_symbol,
#             qty=trade_qty_units,
#             limit_price=close_price,
#             order_type=order_type,
#             buy_sell=trade_side,
#             product_type=product_type,
#             exchange_segment=exchange_segment,
#             exchange_instrument_id=exchange_instrument_id,
#         )

#         # Update local net position by adding the signed lots (position_size)
#         new_net = current_net_lots + position_size
#         current_net_positions[first_symbol] = new_net
#         print(f"Updated net for {first_symbol}: {current_net_lots} -> {new_net}")
#         return

#     except Exception as e:
#         error_msg = f"❌ Error executing order: {str(e)}"
#         logger.error(error_msg, exc_info=True)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         raise

        
# def order_king_executer_xts(result, product_type="NRML"):
#     """
#     Execute trading orders for XTS platform based on webhook data - NEW SIMPLIFIED LOGIC

#     Args:
#         result: Parsed webhook data dictionary with action, contracts, position_size
#         product_type: Product type for orders - "MIS", "NRML", or "CNC" (default: "NRML")
#     """
#     if not result:
#         print("Message ignored due to missing keywords.")
#         send_telegram_message("⚠️ Message ignored due to missing keywords.", chat_id=TEST3_CHAT_ID)
#         return

#     print(result)
#     logging.debug(f"result data: {result}")

#     # Extract new webhook fields
#     exchange = result["exchange"]
#     main_symbol = result["symbol"]
#     buyfut = int(result["buyfut"])
#     action = result["action"]  # 'buy' or 'sell'
#     contracts = int(result["contracts"])
#     position_size = int(result["position_size"])
#     close_price = float(result["close_price"])
#     order_type = result["order_type"]

#     print("=== Extracted Values ===")
#     print(f"Symbol: {main_symbol}")
#     print(f"Action: {action}")
#     print(f"Contracts: {contracts}")
#     print(f"Position Size: {position_size}")
#     print(f"Close Price: {close_price}")
#     print(f"Order Type: {order_type}")
#     print(f"Exchange: {exchange}")

#     # Get symbol and lot size from CSV
#     if buyfut == 1:
#         print(f"Symbol: {main_symbol} -> use future chart for this")
#         first_symbol, first_symbol_lot = get_future_name(
#             symbol=main_symbol, exchange=exchange
#         )
#     else:
#         ext_value = extract_option_details(main_symbol)
#         if ext_value:
#             main_symbol = ext_value["main_symbol"]
#             date = ext_value["date"]
#             option_type = ext_value["option_type"]
#             strike = ext_value["strike"]
#             (
#                 first_symbol,
#                 first_main_symbol,
#                 first_symbol_lot,
#                 first_expiry_date,
#                 main_ss,
#             ) = getting_strike(
#                 symbol=main_symbol,
#                 option_type=option_type,
#                 strike=strike,
#                 exchnge=exchange,
#                 date=date,
#             )
#         else:
#             error_msg = "❌ TradingView symbol not found"
#             print(error_msg)
#             send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#             return

#     if first_symbol is None:
#         error_msg = "❌ First symbol is None - cannot proceed"
#         print(error_msg)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         return

#     first_symbol = str(first_symbol)
#     first_symbol_lot = int(first_symbol_lot)

#     # Calculate quantity based on contracts * lot_size
#     quantity = contracts * first_symbol_lot

#     print(f"Trading Symbol: {first_symbol}")
#     print(f"Lot Size: {first_symbol_lot}")
#     print(f"Calculated Quantity: {quantity}")

#     # Get exchange segment and instrument ID for XTS
#     exchange_segment, exchange_instrument_id = get_instrument_details(first_symbol, exchange)

#     if exchange_segment is None or exchange_instrument_id is None:
#         error_msg = f"❌ Could not get instrument details for {first_symbol}"
#         print(error_msg)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         return

#     print(f"XTS Details - Segment: {exchange_segment}, Instrument ID: {exchange_instrument_id}, Product: {product_type}")

#     # Execute trading logic based on action
#     try:
#         if action == "buy":
#             # BUY action - place order directly
#             print(f"📈 BUY action detected - placing BUY order")
#             send_telegram_message(
#                 f"📈 Executing BUY order\n"
#                 f"Symbol: {first_symbol}\n"
#                 f"Quantity: {quantity}\n"
#                 f"Price: {close_price}\n"
#                 f"Type: {order_type}",
#                 chat_id=TEST3_CHAT_ID
#             )

#             place_market_order(
#                 symbol=first_symbol,
#                 qty=quantity,
#                 limit_price=close_price,
#                 order_type=order_type,
#                 buy_sell="BUY",
#                 product_type=product_type,
#                 exchange_segment=exchange_segment,
#                 exchange_instrument_id=exchange_instrument_id
#             )

#         elif action == "sell":
#             # SELL action - check if position_size == 0 (stoploss)
#             if position_size == 0:
#                 # This is a stoploss - check if position exists first
#                 print(f"📉 SELL action with position_size=0 (STOPLOSS) - checking positions first")

#                 # Check positions to see if we have anything to sell
#                 position_exists = check_position_exists(
#                     symbol=first_symbol,
#                     exchange_instrument_id=exchange_instrument_id
#                 )

#                 if not position_exists:
#                     # No position exists - skip the order
#                     skip_msg = (
#                         f"⚠️ SKIPPING SELL order (Stoploss)\n"
#                         f"Reason: No position exists for {first_symbol}\n"
#                         f"This means the BUY never went through"
#                     )
#                     print(skip_msg)
#                     send_telegram_message(skip_msg, chat_id=TEST3_CHAT_ID)
#                     logger.warning(f"Skipped SELL order for {first_symbol} - no position exists")
#                     return
#                 else:
#                     # Position exists - place the SELL order
#                     print(f"✓ Position exists for {first_symbol} - placing SELL order")
#                     send_telegram_message(
#                         f"📉 Executing SELL order (Stoploss)\n"
#                         f"Symbol: {first_symbol}\n"
#                         f"Quantity: {quantity}\n"
#                         f"Price: {close_price}\n"
#                         f"Type: {order_type}",
#                         chat_id=TEST3_CHAT_ID
#                     )

#                     place_market_order(
#                         symbol=first_symbol,
#                         qty=quantity,
#                         limit_price=close_price,
#                         order_type=order_type,
#                         buy_sell="SELL",
#                         product_type=product_type,
#                         exchange_segment=exchange_segment,
#                         exchange_instrument_id=exchange_instrument_id
#                     )
#             else:
#                 # Normal SELL (position_size != 0) - place order directly
#                 print(f"📉 SELL action (normal exit) - placing SELL order")
#                 send_telegram_message(
#                     f"📉 Executing SELL order\n"
#                     f"Symbol: {first_symbol}\n"
#                     f"Quantity: {quantity}\n"
#                     f"Price: {close_price}\n"
#                     f"Type: {order_type}",
#                     chat_id=TEST3_CHAT_ID
#                 )

#                 place_market_order(
#                     symbol=first_symbol,
#                     qty=quantity,
#                     limit_price=close_price,
#                     order_type=order_type,
#                     buy_sell="SELL",
#                     product_type=product_type,
#                     exchange_segment=exchange_segment,
#                     exchange_instrument_id=exchange_instrument_id
#                 )
#         else:
#             error_msg = f"⚠️ Unknown action received: {action}"
#             print(error_msg)
#             send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)

#     except Exception as e:
#         error_msg = f"❌ Error executing order: {str(e)}"
#         logger.error(error_msg, exc_info=True)
#         send_telegram_message(error_msg, chat_id=TEST3_CHAT_ID)
#         raise  # Re-raise to trigger the outer exception handler


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Hello, World!"}), 200


@app.route("/sha/fyers", methods=["POST"])
def process_message():
    """Process webhook messages with comprehensive error handling and validation (JSON format)"""
    try:
        # Check if request is JSON
        if request.is_json:
            try:
                json_data = request.get_json()
                logger.info(f"Received JSON webhook data")
                logger.debug(f"JSON content: {json_data}")
            except Exception as e:
                logger.error(f"Failed to parse JSON: {e}")
                return jsonify({"error": "Invalid JSON format"}), 400
            
            # Handle simple commands in JSON format
            if "command" in json_data:
                command = json_data["command"].lower()
                
                if command in ["hii", "hello"]:
                    response_msg = f"{command} - Fyers Trading script is operational"
                    send_telegram_message(response_msg, chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Health check processed"}), 200
                
                elif command == "exit all":
                    logger.info("Exit all command received")
                    send_telegram_message("Executing exit all positions command", chat_id=TEST3_CHAT_ID)
                    try:
                        exit_all_order()
                        send_telegram_message("✅ Exit all positions completed", chat_id=TEST3_CHAT_ID)
                    except Exception as e:
                        logger.error(f"Failed to exit all positions: {e}")
                        send_telegram_message(f"❌ Exit all positions failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Exit all processed"}), 200
                
                elif command == "cancel all":
                    logger.info("Cancel all command received")
                    send_telegram_message("Executing cancel all orders command", chat_id=TEST3_CHAT_ID)
                    try:
                        cancel_orders_for_all()
                        send_telegram_message("✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID)
                    except Exception as e:
                        logger.error(f"Failed to cancel all orders: {e}")
                        send_telegram_message(f"❌ Cancel all orders failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Cancel all processed"}), 200
            
            # Send notification to Telegram
            notification_msg = f"📨 JSON Webhook received: {str(json_data)[:300]}..."
            send_telegram_message(notification_msg, chat_id=TEST3_CHAT_ID)
            
            # Parse JSON trading message
            parsed_data = parse_json_message(json_data)
            logger.debug(f"Parsed data: {parsed_data}")
            
            if parsed_data:
                try:
                    # Send parsed data confirmation
                    confirmation_msg = f"📊 Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)
                    
                    # Save to CSV
                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message("⚠️ Warning: Failed to save trade data to CSV", chat_id=TEST3_CHAT_ID)
                    else:
                        logger.info("Trading data saved to CSV successfully")
                    
                    # Execute trading logic
                    logger.info("Executing trading order")
                    order_king_executer(parsed_data)
                    send_telegram_message("✅ Trading order processed successfully", chat_id=TEST3_CHAT_ID)
                    
                except Exception as e:
                    error_msg = f"Error processing trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(f"❌ Trading error: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"error": "Trading processing failed", "details": str(e)}), 500
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return jsonify({"status": "ok", "message": "Message processed but no trading action required"}), 200
            
            return jsonify({"status": "success", "message": "JSON Trading message processed"}), 200
        
        else:
            # Fallback to legacy text format
            if not request.data:
                logger.warning("Empty request received")
                return jsonify({"error": "Empty request"}), 400
            
            try:
                text_data = request.data.decode("utf-8")
            except UnicodeDecodeError:
                logger.error("Invalid UTF-8 encoding in request")
                return jsonify({"error": "Invalid encoding"}), 400
            
            if len(text_data) > 10000:
                logger.warning("Request data too large")
                return jsonify({"error": "Message too large"}), 400
            
            logger.info(f"Received legacy text webhook data (length: {len(text_data)})")
            
            message_lower = text_data.lower()
            
            if message_lower in ["hii", "hello"]:
                response_msg = f"{message_lower} - Trading script is operational"
                send_telegram_message(response_msg, chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Health check processed"}), 200
            
            elif message_lower == "exit all":
                logger.info("Exit all command received")
                send_telegram_message("Executing exit all positions command", chat_id=TEST3_CHAT_ID)
                try:
                    exit_all_order()
                    send_telegram_message("✅ Exit all positions completed", chat_id=TEST3_CHAT_ID)
                except Exception as e:
                    logger.error(f"Failed to exit all positions: {e}")
                    send_telegram_message(f"❌ Exit all positions failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Exit all processed"}), 200
            
            elif message_lower == "cancel all":
                logger.info("Cancel all command received")
                send_telegram_message("Executing cancel all orders command", chat_id=TEST3_CHAT_ID)
                try:
                    cancel_orders_for_all()
                    send_telegram_message("✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID)
                except Exception as e:
                    logger.error(f"Failed to cancel all orders: {e}")
                    send_telegram_message(f"❌ Cancel all orders failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Cancel all processed"}), 200
            
            notification_msg = text_data[:500] + "..." if len(text_data) > 500 else text_data
            send_telegram_message(f"📨 Webhook received: {notification_msg}", chat_id=TEST3_CHAT_ID)
            
            parsed_data = parse_message(text_data)
            
            if parsed_data:
                try:
                    confirmation_msg = f"📊 Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)
                    
                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message("⚠️ Warning: Failed to save trade data to CSV", chat_id=TEST3_CHAT_ID)
                    
                    logger.info("Executing trading order")
                    order_king_executer(parsed_data)
                    send_telegram_message("✅ Trading order processed successfully", chat_id=TEST3_CHAT_ID)
                    
                except Exception as e:
                    error_msg = f"Error processing trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(f"❌ Trading error: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"error": "Trading processing failed", "details": str(e)}), 500
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return jsonify({"status": "ok", "message": "Message processed but no trading action required"}), 200
            
            return jsonify({"status": "success", "message": "Trading message processed"}), 200
    
    except Exception as e:
        error_message = f"Unexpected error in webhook processing: {str(e)}"
        logger.error(error_message, exc_info=True)
        send_telegram_message(f"🚨 Critical error in webhook: {str(e)}", chat_id=TEST3_CHAT_ID)
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/sha/xts", methods=["POST"])
def process_message_xts():
    """Process webhook messages for XTS with comprehensive error handling and validation (JSON format)"""
    try:
        # Check if request is JSON
        if request.is_json:
            try:
                json_data = request.get_json()
                logger.info(f"[XTS] Received JSON webhook data")
                logger.debug(f"JSON content: {json_data}")
            except Exception as e:
                logger.error(f"Failed to parse JSON: {e}")
                return jsonify({"error": "Invalid JSON format"}), 400
            
            # Handle simple commands in JSON format
            if "command" in json_data:
                command = json_data["command"].lower()
                
                if command in ["hii", "hello"]:
                    response_msg = f"{command} - XTS Trading script is operational"
                    send_telegram_message(response_msg, chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Health check processed"}), 200
                
                elif command == "exit all":
                    logger.info("[XTS] Exit all command received")
                    send_telegram_message("Executing exit all positions command (XTS)", chat_id=TEST3_CHAT_ID)
                    try:
                        exit_all_positions()
                        send_telegram_message("✅ Exit all positions completed", chat_id=TEST3_CHAT_ID)
                    except Exception as e:
                        logger.error(f"Failed to exit all positions: {e}")
                        send_telegram_message(f"❌ Exit all positions failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Exit all processed"}), 200
                
                elif command == "cancel all":
                    logger.info("[XTS] Cancel all command received")
                    send_telegram_message("Executing cancel all orders command (XTS)", chat_id=TEST3_CHAT_ID)
                    try:
                        cancel_orders_for_all()
                        send_telegram_message("✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID)
                    except Exception as e:
                        logger.error(f"Failed to cancel all orders: {e}")
                        send_telegram_message(f"❌ Cancel all orders failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"status": "ok", "message": "Cancel all processed"}), 200
            
            # Send notification to Telegram
            notification_msg = f"📨 [XTS] JSON Webhook received: {str(json_data)[:300]}..."
            send_telegram_message(notification_msg, chat_id=TEST3_CHAT_ID)
            
            # Parse JSON trading message
            parsed_data = parse_json_message(json_data)
            logger.debug(f"Parsed data: {parsed_data}")
            
            if parsed_data:
                try:
                    # Send parsed data confirmation
                    confirmation_msg = f"📊 [XTS] Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)
                    
                    # Save to CSV
                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message("⚠️ Warning: Failed to save trade data to CSV", chat_id=TEST3_CHAT_ID)
                    else:
                        logger.info("Trading data saved to CSV successfully")
                    
                    # Execute trading logic for XTS
                    logger.info("Executing XTS trading order")
                    order_king_executer_xts(parsed_data, product_type="NRML")
                    send_telegram_message("✅ XTS Trading order processed successfully", chat_id=TEST3_CHAT_ID)
                    
                except Exception as e:
                    error_msg = f"Error processing XTS trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(f"❌ XTS Trading error: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"error": "Trading processing failed", "details": str(e)}), 500
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return jsonify({"status": "ok", "message": "Message processed but no trading action required"}), 200
            
            return jsonify({"status": "success", "message": "XTS JSON Trading message processed"}), 200
        
        else:
            # Fallback to legacy text format
            if not request.data:
                logger.warning("Empty request received")
                return jsonify({"error": "Empty request"}), 400
            
            try:
                text_data = request.data.decode("utf-8")
            except UnicodeDecodeError:
                logger.error("Invalid UTF-8 encoding in request")
                return jsonify({"error": "Invalid encoding"}), 400
            
            if len(text_data) > 10000:
                logger.warning("Request data too large")
                return jsonify({"error": "Message too large"}), 400
            
            logger.info(f"[XTS] Received legacy text webhook data (length: {len(text_data)})")
            
            message_lower = text_data.lower()
            
            if message_lower in ["hii", "hello"]:
                response_msg = f"{message_lower} - XTS Trading script is operational"
                send_telegram_message(response_msg, chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Health check processed"}), 200
            
            elif message_lower == "exit all":
                logger.info("[XTS] Exit all command received")
                send_telegram_message("Executing exit all positions command (XTS)", chat_id=TEST3_CHAT_ID)
                try:
                    exit_all_positions()
                    send_telegram_message("✅ Exit all positions completed", chat_id=TEST3_CHAT_ID)
                except Exception as e:
                    logger.error(f"Failed to exit all positions: {e}")
                    send_telegram_message(f"❌ Exit all positions failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Exit all processed"}), 200
            
            elif message_lower == "cancel all":
                logger.info("[XTS] Cancel all command received")
                send_telegram_message("Executing cancel all orders command (XTS)", chat_id=TEST3_CHAT_ID)
                try:
                    cancel_orders_for_all()
                    send_telegram_message("✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID)
                except Exception as e:
                    logger.error(f"Failed to cancel all orders: {e}")
                    send_telegram_message(f"❌ Cancel all orders failed: {str(e)}", chat_id=TEST3_CHAT_ID)
                return jsonify({"status": "ok", "message": "Cancel all processed"}), 200
            
            notification_msg = text_data[:500] + "..." if len(text_data) > 500 else text_data
            send_telegram_message(f"📨 [XTS] Webhook received: {notification_msg}", chat_id=TEST3_CHAT_ID)
            
            parsed_data = parse_message(text_data)
            
            if parsed_data:
                try:
                    confirmation_msg = f"📊 [XTS] Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)
                    
                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message("⚠️ Warning: Failed to save trade data to CSV", chat_id=TEST3_CHAT_ID)
                    
                    logger.info("Executing XTS trading order")
                    order_king_executer_xts(parsed_data, product_type="NRML")
                    send_telegram_message("✅ XTS Trading order processed successfully", chat_id=TEST3_CHAT_ID)
                    
                except Exception as e:
                    error_msg = f"Error processing XTS trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(f"❌ XTS Trading error: {str(e)}", chat_id=TEST3_CHAT_ID)
                    return jsonify({"error": "Trading processing failed", "details": str(e)}), 500
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return jsonify({"status": "ok", "message": "Message processed but no trading action required"}), 200
            
            return jsonify({"status": "success", "message": "XTS Trading message processed"}), 200
    
    except Exception as e:
        error_message = f"Unexpected error in XTS webhook processing: {str(e)}"
        logger.error(error_message, exc_info=True)
        send_telegram_message(f"🚨 Critical error in XTS webhook: {str(e)}", chat_id=TEST3_CHAT_ID)
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    try:
        # Update symbol data on startup
        logger.info("Updating symbol data on startup...")
        nfo_update()
        logger.info("Symbol data updated successfully")

        # Initialize Fyers login
        logger.info("Initializing Fyers authentication...")
        from fyerslogin import auto_login
        auto_login()
        logger.info("Fyers authentication completed")

        # Initialize XTS login
        logger.info("Initializing XTS authentication...")
        from xts_strategy_helper import initialize_xts_client
        initialize_xts_client()
        logger.info("XTS authentication completed")

        # Start the Flask application
        logger.info(f"Starting Flask application on {FLASK_HOST}:{FLASK_PORT}")
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False  # Never run debug in production
        )
    except Exception as e:
        logger.critical(f"Failed to start application: {e}")
        sys.exit(1)