from flask import Flask, request, jsonify, abort
import os
import sys
import urllib.parse
import requests
import re
import logging
from fyres_strategy_helper import *
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

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("trading.log", mode="a")],
)

# Load configuration from environment
TOKEN_TELEGRAM = os.getenv("TELEGRAM_TOKEN")
TEST3_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5035))

# Validate required environment variables
if not all([TOKEN_TELEGRAM, TEST3_CHAT_ID]):
    raise ValueError("Missing required environment variables. Check .env file.")


def save_to_csv(parsed_data):
    """Save trading data to CSV with proper validation and error handling - NEW FORMAT"""
    try:
        # Input validation
        if not parsed_data or not isinstance(parsed_data, dict):
            raise ValueError("Invalid parsed_data provided")

        # NEW: Updated required fields for new format
        required_fields = [
            "exchange",
            "symbol",
            "buyfut",
            "action",
            "contracts",
            "position_size",
            "close_price",
            "order_type",
            "time_utc",
            "time_ist",
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
            return (
                str(value).replace("\n", " ").replace("\r", " ")[:100]
            )  # Limit length

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
            "pending",  # Default status
        ]

        # Define the CSV headers - NEW FORMAT
        headers = [
            "exchange",
            "symbol",
            "buyfut",
            "action",
            "contracts",
            "position_size",
            "close_price",
            "order_type",
            "time_utc",
            "time_ist",
            "source",
            "status",
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
            logger.info("Message does not contain required keywords in tag")
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

        logger.info(f"Successfully parsed JSON message: {result}")
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
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"vbscript:",
        r"onload=",
        r"onerror=",
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
            logger.info("Message does not contain required keywords")
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
            result["position_size"] = position_match.group(1)
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
        required_fields = ["exchange", "symbol", "position_size"]
        if not all(field in result for field in required_fields):
            logger.warning("Missing required fields in parsed message")
            return None

        logger.info(f"Successfully parsed message: {result}")
        return result

    except Exception as e:
        logger.error(f"Error parsing message: {e}")
        return None


def order_king_executer(result):

    if result:
        print(result)
        logging.info(f"result data: {result}")
        exchange = result["exchange"]
        main_symbol = result["symbol"]
        buyfut = int(result["buyfut"])

        position_size = int(result["position_size"])
        comment = result["comment"]
        open_price = float(result["open_price"])
        order_type = result["order_type"]
        print("Extracted Values:")
        print("Symbol:", main_symbol)
        print("Position Size:", position_size)
        print("Comment:", comment)
        print("Open Price:", open_price)
        print("exchnage :", exchange)

        logging.info(f"buyfut data: {buyfut},type: {type(buyfut)}")

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
        position_qty = first_symbol_lot * position_size

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
                    qty=position_qty,
                    limitPrice=open_price,
                    order_type=order_type,
                )

            elif comment == "Long Entry":
                print("long entry called")
                order_placement_buy_side(
                    symbol=first_symbol,
                    qty=position_qty,
                    limitPrice=open_price,
                    order_type=order_type,
                )
            elif (
                comment == "Exit fifty at two x"
                or comment == "long exit fifty at three x"
            ):
                print("half qty exit thing called ")
                exit_half_position(symbol=first_symbol, match_qty=position_qty)
            else:
                print("no condition satisfy ")
        else:
            send_telegram_message("first symbol is none ", chat_id=TEST3_CHAT_ID)

    else:
        print("Message ignored due to missing keywords.")
        send_telegram_message(
            "Message ignored due to missing keywords.", chat_id=TEST3_CHAT_ID
        )


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Hello, World!"}), 200


@app.route("/fyers", methods=["POST"])
def process_message():
    """Process webhook messages with comprehensive error handling and validation (JSON format)"""
    try:
        # Check if request is JSON
        if request.is_json:
            try:
                json_data = request.get_json()
                logger.info(f"Received JSON webhook data")
                logger.info(f"JSON content: {json_data}")
            except Exception as e:
                logger.error(f"Failed to parse JSON: {e}")
                return jsonify({"error": "Invalid JSON format"}), 400

            # Handle simple commands in JSON format
            if "command" in json_data:
                command = json_data["command"].lower()

                if command in ["hii", "hello"]:
                    response_msg = f"{command} - Fyers Trading script is operational"
                    send_telegram_message(response_msg, chat_id=TEST3_CHAT_ID)
                    return (
                        jsonify({"status": "ok", "message": "Health check processed"}),
                        200,
                    )

                elif command == "exit all":
                    logger.info("Exit all command received")
                    send_telegram_message(
                        "Executing exit all positions command", chat_id=TEST3_CHAT_ID
                    )
                    try:
                        exit_all_order()
                        send_telegram_message(
                            "✅ Exit all positions completed", chat_id=TEST3_CHAT_ID
                        )
                    except Exception as e:
                        logger.error(f"Failed to exit all positions: {e}")
                        send_telegram_message(
                            f"❌ Exit all positions failed: {str(e)}",
                            chat_id=TEST3_CHAT_ID,
                        )
                    return (
                        jsonify({"status": "ok", "message": "Exit all processed"}),
                        200,
                    )

                elif command == "cancel all":
                    logger.info("Cancel all command received")
                    send_telegram_message(
                        "Executing cancel all orders command", chat_id=TEST3_CHAT_ID
                    )
                    try:
                        cancel_orders_for_all()
                        send_telegram_message(
                            "✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID
                        )
                    except Exception as e:
                        logger.error(f"Failed to cancel all orders: {e}")
                        send_telegram_message(
                            f"❌ Cancel all orders failed: {str(e)}",
                            chat_id=TEST3_CHAT_ID,
                        )
                    return (
                        jsonify({"status": "ok", "message": "Cancel all processed"}),
                        200,
                    )

            # Send notification to Telegram
            notification_msg = f"📨 JSON Webhook received: {str(json_data)[:300]}..."
            send_telegram_message(notification_msg, chat_id=TEST3_CHAT_ID)

            # Parse JSON trading message
            parsed_data = parse_json_message(json_data)
            logger.info(f"Parsed data: {parsed_data}")

            if parsed_data:
                try:
                    # Send parsed data confirmation
                    confirmation_msg = f"📊 Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)

                    # Save to CSV
                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message(
                            "⚠️ Warning: Failed to save trade data to CSV",
                            chat_id=TEST3_CHAT_ID,
                        )
                    else:
                        logger.info("Trading data saved to CSV successfully")

                    # Execute trading logic
                    logger.info("Executing trading order")
                    order_king_executer(parsed_data)
                    send_telegram_message(
                        "✅ Trading order processed successfully", chat_id=TEST3_CHAT_ID
                    )

                except Exception as e:
                    error_msg = f"Error processing trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(
                        f"❌ Trading error: {str(e)}", chat_id=TEST3_CHAT_ID
                    )
                    return (
                        jsonify(
                            {"error": "Trading processing failed", "details": str(e)}
                        ),
                        500,
                    )
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": "Message processed but no trading action required",
                        }
                    ),
                    200,
                )

            return (
                jsonify(
                    {"status": "success", "message": "JSON Trading message processed"}
                ),
                200,
            )

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
                return (
                    jsonify({"status": "ok", "message": "Health check processed"}),
                    200,
                )

            elif message_lower == "exit all":
                logger.info("Exit all command received")
                send_telegram_message(
                    "Executing exit all positions command", chat_id=TEST3_CHAT_ID
                )
                try:
                    exit_all_order()
                    send_telegram_message(
                        "✅ Exit all positions completed", chat_id=TEST3_CHAT_ID
                    )
                except Exception as e:
                    logger.error(f"Failed to exit all positions: {e}")
                    send_telegram_message(
                        f"❌ Exit all positions failed: {str(e)}", chat_id=TEST3_CHAT_ID
                    )
                return jsonify({"status": "ok", "message": "Exit all processed"}), 200

            elif message_lower == "cancel all":
                logger.info("Cancel all command received")
                send_telegram_message(
                    "Executing cancel all orders command", chat_id=TEST3_CHAT_ID
                )
                try:
                    cancel_orders_for_all()
                    send_telegram_message(
                        "✅ Cancel all orders completed", chat_id=TEST3_CHAT_ID
                    )
                except Exception as e:
                    logger.error(f"Failed to cancel all orders: {e}")
                    send_telegram_message(
                        f"❌ Cancel all orders failed: {str(e)}", chat_id=TEST3_CHAT_ID
                    )
                return jsonify({"status": "ok", "message": "Cancel all processed"}), 200

            notification_msg = (
                text_data[:500] + "..." if len(text_data) > 500 else text_data
            )
            send_telegram_message(
                f"📨 Webhook received: {notification_msg}", chat_id=TEST3_CHAT_ID
            )

            parsed_data = parse_message(text_data)

            if parsed_data:
                try:
                    confirmation_msg = f"📊 Parsed data: {str(parsed_data)[:300]}..."
                    send_telegram_message(confirmation_msg, chat_id=TEST3_CHAT_ID)

                    logger.info("Saving trading data to CSV")
                    if not save_to_csv(parsed_data):
                        logger.error("Failed to save CSV data")
                        send_telegram_message(
                            "⚠️ Warning: Failed to save trade data to CSV",
                            chat_id=TEST3_CHAT_ID,
                        )

                    logger.info("Executing trading order")
                    order_king_executer(parsed_data)
                    send_telegram_message(
                        "✅ Trading order processed successfully", chat_id=TEST3_CHAT_ID
                    )

                except Exception as e:
                    error_msg = f"Error processing trading data: {str(e)}"
                    logger.error(error_msg)
                    send_telegram_message(
                        f"❌ Trading error: {str(e)}", chat_id=TEST3_CHAT_ID
                    )
                    return (
                        jsonify(
                            {"error": "Trading processing failed", "details": str(e)}
                        ),
                        500,
                    )
            else:
                logger.info("Message did not match trading pattern - no action taken")
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": "Message processed but no trading action required",
                        }
                    ),
                    200,
                )

            return (
                jsonify({"status": "success", "message": "Trading message processed"}),
                200,
            )

    except Exception as e:
        error_message = f"Unexpected error in webhook processing: {str(e)}"
        logger.error(error_message, exc_info=True)
        send_telegram_message(
            f"🚨 Critical error in webhook: {str(e)}", chat_id=TEST3_CHAT_ID
        )
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

        # Start the Flask application
        logger.info(f"Starting Flask application on {FLASK_HOST}:{FLASK_PORT}")
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,  # Never run debug in production
        )
    except Exception as e:
        logger.critical(f"Failed to start application: {e}")
        sys.exit(1)
