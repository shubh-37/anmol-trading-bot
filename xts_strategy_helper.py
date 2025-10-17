import pandas as pd
from datetime import datetime
import json
import os
import requests
import logging
from functools import lru_cache
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get configuration from environment
token_telegram = os.getenv('TELEGRAM_TOKEN')
chat_id_telegram = os.getenv('TELEGRAM_CHAT_ID')
xts_user_id = os.getenv('XTS_USER_ID')
xts_api_key = os.getenv('XTS_INTERACTIVE_API_KEY')
xts_api_secret = os.getenv('XTS_INTERACTIVE_API_SECRET')
xts_api_source = os.getenv('XTS_API_SOURCE', 'WEBAPI')
xts_api_root = os.getenv('XTS_API_ROOT', 'https://api.xts.com')

# Validate required environment variables
if not all([token_telegram, chat_id_telegram, xts_user_id, xts_api_key, xts_api_secret]):
    raise ValueError("Missing required XTS environment variables. Check .env file.")

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading.log", mode='a')
    ]
)

# Global variable to store XTS client session
xts_token = None
xts_user_id_token = None

def send_telegram_message(message):
    """Send message to Telegram with proper error handling"""
    try:
        # Input validation
        if not message:
            logger.warning("Empty message, skipping Telegram send")
            return False

        # Ensure the message is a string and limit length
        if isinstance(message, bytes):
            message = message.decode('utf-8')
        elif not isinstance(message, str):
            message = str(message)

        # Telegram message limit is 4096 characters
        if len(message) > 4096:
            message = message[:4093] + "..."

        # Construct the API endpoint with proper encoding
        url = f'https://api.telegram.org/bot{token_telegram}/sendMessage'
        data = {
            'chat_id': chat_id_telegram,
            'text': message,
            'parse_mode': 'HTML'
        }

        # Make the request with timeout
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()

        logger.debug("Telegram message sent successfully")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in send_telegram_message: {e}")
        return False


def initialize_xts_client():
    """Initialize XTS client with authentication"""
    global xts_token, xts_user_id_token

    try:
        # Try to load existing token
        if os.path.exists("./xts_store_token.json"):
            with open("./xts_store_token.json", "r") as token_file:
                token_data = json.load(token_file)
                xts_token = token_data.get("token")
                xts_user_id_token = token_data.get("userID")

                # Verify token is still valid by making a test request
                test_url = f"{xts_api_root}/interactive/user/profile"
                headers = {"Authorization": xts_token}
                test_response = requests.get(test_url, headers=headers, timeout=10)

                if test_response.status_code == 200:
                    logger.info("XTS client initialized with existing token")
                    return True
                else:
                    logger.warning("Existing token invalid, logging in again")

        # Login to get new token
        login_url = f"{xts_api_root}/interactive/user/session"
        payload = {
            "appKey": xts_api_key,
            "secretKey": xts_api_secret,
            "source": xts_api_source
        }

        logger.info("Logging into XTS...")
        response = requests.post(login_url, json=payload, timeout=10)
        response.raise_for_status()

        result = response.json()

        if result.get("type") == "success":
            xts_token = result["result"]["token"]
            xts_user_id_token = result["result"]["userID"]

            # Save token to file
            token_data = {
                "token": xts_token,
                "userID": xts_user_id_token,
                "timestamp": datetime.now().isoformat()
            }

            with open("./xts_store_token.json", "w") as token_file:
                json.dump(token_data, token_file, indent=4)

            # Set secure file permissions
            os.chmod("./xts_store_token.json", 0o600)

            logger.info("XTS client initialized successfully")
            return True
        else:
            error_msg = result.get("description", "Unknown error")
            raise Exception(f"XTS login failed: {error_msg}")

    except requests.exceptions.RequestException as e:
        logger.error(f"XTS login request failed: {e}")
        raise Exception(f"Failed to connect to XTS API: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize XTS client: {e}")
        raise


@lru_cache(maxsize=100)
def get_future_name(symbol, exchange):
    """Get future symbol name with caching for performance - uses local CSV"""
    if not symbol:
        logger.error("Symbol is required")
        return None, None

    try:
        # Exchange configuration
        exchange_config = {
            "NSE": {"filename": "NSE_FO.csv", "exchange_no": 11},
            "MCX": {"filename": "MCX_COM.csv", "exchange_no": 30},
            "BSE": {"filename": "BSE_FO.csv", "exchange_no": 14}
        }

        if exchange not in exchange_config:
            logger.error(f"Unsupported exchange: {exchange}")
            return None, None

        config = exchange_config[exchange]
        local_filename = config["filename"]
        exchange_no = config["exchange_no"]

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
        df = df[(df["exch no"] == exchange_no) & (df["symbol main name"] == symbol)]

        if df.empty:
            logger.warning(f"No data found for symbol: {symbol} on exchange: {exchange}")
            return None, None

        # Extract and process dates
        filter_date = df["sym des"].str.extract(r'(\d{2} [A-Za-z]{3} \d{2})', expand=False)
        df.loc[:, "date"] = pd.to_datetime(filter_date, format="%y %b %d").dt.strftime('%Y-%m-%d')

        # Filter by current date
        current_date = datetime.now().strftime('%Y-%m-%d')
        df = df[df['date'] >= current_date]

        if df.empty:
            logger.warning(f"No valid future contracts found for symbol: {symbol}")
            return None, None

        # Get the nearest expiry contract
        first_row = df.iloc[0]
        symbol_name = first_row['symbol name']
        lot_size = first_row["lot size"]

        logger.debug(f"Found future symbol: {symbol_name}, lot size: {lot_size}")
        return symbol_name, lot_size

    except Exception as e:
        logger.error(f"Error in get_future_name: {e}")
        return None, None


def getting_strike(symbol, option_type, strike, exchnge, date):
    """Get option strike details from local CSV files"""
    print(symbol, option_type, strike, date)
    if symbol is not None:
        main_ss = symbol
        if exchnge == "NSE":
            local_filename = "NSE_FO.csv"
            EXCHNGE_NO = 11
        elif exchnge == "MCX":
            local_filename = "MCX_COM.csv"
        elif exchnge == "BSE":
            local_filename = "BSE_FO.csv"
            if symbol == "BSX":
                symbol = "SENSEX"
            elif symbol == "BKX":
                symbol = "BANKEX"
            else:
                print("symbol not define in code for bse kindly define  ")
                return None, None, None, None, None

        opt_type = option_type

        column_names = [
            "num", "sym des", "exch no", "lot size", "tick size", "blank",
            "timing", "date", "Time", "symbol name",
            "ID 1", "id 2", "token no", "symbol main name", "ISIN", "strike", "option type", "pass", "none", "0", "0.0"
        ]

        df = pd.read_csv(local_filename, header=None, names=column_names)

        print(type(strike))
        strike = int(strike)
        filtered_df = df[(df["symbol main name"] == symbol.upper()) & (df["strike"] == strike) & (df["option type"] == opt_type)]

        if filtered_df.empty:
            print("No data found for the specified conditions.")
            return None, None, None, None, None

        # Extract the desired columns and create a copy of the DataFrame
        result_df = filtered_df[["symbol name", "lot size", "sym des", "symbol main name"]].copy()

        # Extract the date using regular expressions on the entire "sym des" column
        filter_date = filtered_df["sym des"].str.extract(r'(\d{2} [A-Za-z]{3} \d{2})', expand=False)

        # Add the extracted date as a new column "date" to the result_df DataFrame using .loc
        result_df.loc[:, "date"] = pd.to_datetime(filter_date, format="%y %b %d").dt.strftime('%Y-%m-%d')
        filter_date_converted = pd.to_datetime(date, format='%y-%m-%d').strftime('%Y-%m-%d')

        # Filter the DataFrame by the converted date
        result_df = result_df[result_df['date'] == filter_date_converted]

        # Check if the filtered DataFrame is empty after filtering by date
        if result_df.empty:
            print(f"Date '{date}' not found.")
            return None, None, None, None, None

        print(result_df)
        symbols = result_df['symbol name'].tolist()
        main_symbols = result_df['symbol main name'].tolist()
        symbol_lot = result_df['lot size'].tolist()
        exiry_date = result_df['date'].tolist()

        first_symbol = symbols[0]
        first_main_symbol = main_symbols[0]
        first_symbol_lot = symbol_lot[0]
        first_expiry_date = exiry_date[0]

        return first_symbol, first_main_symbol, first_symbol_lot, first_expiry_date, main_ss

    else:
        return None, None, None, None, None


def cancel_orders_for_all():
    """Cancel all pending orders using XTS API"""
    global xts_token
    # Need to add exchangeSegment and instrumentId in the request body
    try:
        url = f"{xts_api_root}/interactive/orders/cancelall"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        request_body = {
            "clientID": "*****"
        }

        response = requests.post(url, json=request_body, headers=headers, timeout=10)
        response.raise_for_status()

        result = response.json()
        print(result)

        if result.get("type") == "success":
            logger.info("All orders cancelled successfully")
            send_telegram_message("✅ All orders cancelled successfully")
        else:
            logger.warning(f"Cancel all orders response: {result}")
            send_telegram_message(f"⚠️ Cancel all orders: {result.get('description', 'Unknown error')}")

        return result

    except Exception as e:
        logger.error(f"Failed to cancel all orders: {e}")
        send_telegram_message(f"❌ Failed to cancel all orders: {str(e)}")
        return None


def cancel_single_order(symbol):
    """Cancel orders for a specific symbol using XTS API"""
    global xts_token

    headers = {
        "Authorization": xts_token,
        "Content-Type": "application/json"
    }

    # First API call: Get all orders
    try:
        get_orders_url = f"{xts_api_root}/interactive/orders/dealerorderbook?clientID=*****"
        
        response = requests.get(get_orders_url, headers=headers, timeout=10)
        response.raise_for_status()

        orders_data = response.json()
        print(orders_data)

        if orders_data.get("type") != "success":
            logger.warning(f"Failed to get orders: {orders_data}")
            return None

    except Exception as e:
        logger.error(f"Failed to get orders for {symbol}: {e}")
        send_telegram_message(f"❌ Failed to get orders for {symbol}: {str(e)}")
        return None

    # Filter orders for the symbol that are pending/open
    orders = orders_data.get("result", [])
    symbol_orders = [order for order in orders if order.get("TradingSymbol") == symbol and order.get("OrderStatus") in ["Pending", "Open"]]

    if not symbol_orders:
        print(f"symbol {symbol} has no pending orders")
        send_telegram_message(f"symbol {symbol} has no pending orders")
        return None

    # Second API call: Cancel orders
    try:
        cancel_url = f"{xts_api_root}/interactive/orders/cancel"
        for order in symbol_orders:
            order_id = order.get("AppOrderID")

            cancel_response = requests.delete(cancel_url, json=order_id, headers=headers, timeout=10)
            print(cancel_response.json())

        send_telegram_message(f"✅ Cancelled {len(symbol_orders)} order(s) for {symbol}")
        return True

    except Exception as e:
        logger.error(f"Failed to cancel orders for {symbol}: {e}")
        send_telegram_message(f"❌ Failed to cancel orders for {symbol}: {str(e)}")
        return None


def exit_single_order(symbol, positions, exchange_instrument_id=None, product_type="NRML"):
    """
    Exit position for a specific symbol using XTS Square-off API
    
    Args:
        symbol: Trading symbol to exit
        positions: List of positions (already fetched)
        exchange_instrument_id: Optional instrument ID for precise matching
        product_type: Product type (NRML, MIS, CNC) - default NRML
    """
    global xts_token

    try:
        print(f"=== Starting exit for symbol: {symbol} ===")
        
        if not positions:
            print("No positions provided")
            send_telegram_message("ℹ️ No open positions found")
            return None

        # Find position for this symbol
        target_position = None
        exchange_instrument_id_str = str(exchange_instrument_id) if exchange_instrument_id else None
        
        for position in positions:
            position_instrument_id = str(position.get("ExchangeInstrumentId", ""))
            position_symbol = position.get("TradingSymbol", "")
            position_qty = int(position.get("Quantity", 0))
            
            # Match by instrument ID (preferred) or symbol name
            match_found = False
            if exchange_instrument_id_str and position_instrument_id == exchange_instrument_id_str:
                match_found = True
                print(f"✓ Matched by ExchangeInstrumentId: {position_instrument_id}")
            elif position_symbol == symbol:
                match_found = True
                print(f"✓ Matched by TradingSymbol: {symbol}")
            
            if match_found and position_qty != 0:
                target_position = position
                print(f"Found position: {position_symbol}, Qty: {position_qty}")
                break

        if not target_position:
            msg = f"No open position found for symbol: {symbol}"
            if exchange_instrument_id_str:
                msg += f" (ID: {exchange_instrument_id_str})"
            print(msg)
            send_telegram_message(f"ℹ️ {msg}")
            return None

        # Extract position details
        exchange_segment = target_position.get("ExchangeSegment")
        instrument_id = target_position.get("ExchangeInstrumentId")
        trading_symbol = target_position.get("TradingSymbol")
        position_qty = int(target_position.get("Quantity", 0))
        position_product_type = target_position.get("ProductType", product_type)
        
        # Calculate absolute quantity to square off
        squareoff_qty = abs(position_qty)
        
        print(f"Position details:")
        print(f"  Symbol: {trading_symbol}")
        print(f"  Exchange Segment: {exchange_segment}")
        print(f"  Instrument ID: {instrument_id}")
        print(f"  Current Quantity: {position_qty}")
        print(f"  Square-off Quantity: {squareoff_qty}")
        print(f"  Product Type: {position_product_type}")

        # Exit the position using square-off API
        squareoff_url = f"{xts_api_root}/interactive/portfolio/squareoff"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        payload = {
            "exchangeSegment": exchange_segment,
            "exchangeInstrumentID": int(instrument_id),
            "productType": position_product_type,
            "squareoffMode": "DayWise",
            "squareOffQtyValue": squareoff_qty,
            "clientID": "*****",
            "positionSquareOffQuantityType": "ExactQty"
        }
        
        print(f"Square-off request payload: {payload}")
        
        # Send PUT request
        squareoff_response = requests.put(
            squareoff_url, 
            json=payload, 
            headers=headers, 
            timeout=10
        )
        squareoff_result = squareoff_response.json()
        
        print(f"Square-off response: {squareoff_result}")

        if squareoff_result.get("type") == "success":
            success_msg = (
                f"✅ Successfully squared off position:\n"
                f"Symbol: {trading_symbol}\n"
                f"Quantity: {position_qty}\n"
                f"Squared off: {squareoff_qty} units"
            )
            print(success_msg)
            send_telegram_message(success_msg)
            
            # Log the order ID if available
            result_data = squareoff_result.get("result", {})
            if isinstance(result_data, dict):
                order_id = result_data.get("AppOrderID") or result_data.get("OrderID")
                if order_id:
                    print(f"Order ID: {order_id}")
        else:
            error_desc = squareoff_result.get("description", "Unknown error")
            error_msg = (
                f"❌ Failed to square off position:\n"
                f"Symbol: {trading_symbol}\n"
                f"Error: {error_desc}"
            )
            print(error_msg)
            send_telegram_message(error_msg)

        return squareoff_result

    except requests.exceptions.RequestException as e:
        error_msg = f"❌ Network error while exiting {symbol}: {str(e)}"
        logger.error(error_msg)
        send_telegram_message(error_msg)
        return None
    except Exception as e:
        error_msg = f"❌ Failed to exit position for {symbol}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        send_telegram_message(error_msg)
        return None

def exit_all_positions(product_type="NRML", square_off_mode="NetWise"):
    """
    Exit all open positions using XTS Square-off All API
    
    Args:
        product_type: Product type filter (currently not used with bulk API)
        square_off_mode: "DayWise" or "NetWise" (default: DayWise)
    """
    global xts_token

    try:
        print("=== Starting exit all positions (Bulk API) ===")
        
        # First, get current positions to show what will be closed
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=NetWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }

        response = requests.get(positions_url, headers=headers, timeout=10)
        response.raise_for_status()

        positions_data = response.json()

        if positions_data.get("type") != "success":
            logger.warning(f"Failed to get positions: {positions_data}")
            send_telegram_message("⚠️ Failed to get positions")
            return None

        positions = positions_data.get("result", {}).get("positionList", [])

        # Filter positions with non-zero quantity
        active_positions = [
            pos for pos in positions 
            if int(pos.get("Quantity", 0)) != 0
        ]

        if not active_positions:
            msg = "No open positions to exit"
            print(msg)
            send_telegram_message(f"ℹ️ {msg}")
            return None

        # Log positions that will be closed
        print(f"Found {len(active_positions)} positions to exit:")
        position_summary = []
        for pos in active_positions:
            trading_symbol = pos.get("TradingSymbol")
            quantity = pos.get("Quantity")
            pos_product = pos.get("ProductType")
            position_summary.append(f"  • {trading_symbol}: {quantity} ({pos_product})")
            print(f"  - {trading_symbol}: {quantity} units ({pos_product})")

        # Send notification about positions to be closed
        positions_list = "\n".join(position_summary[:10])  # Limit to first 10 for Telegram
        if len(active_positions) > 10:
            positions_list += f"\n  ... and {len(active_positions) - 10} more"
        
        send_telegram_message(
            f"🔄 Exiting {len(active_positions)} positions:\n{positions_list}"
        )

        # Use the bulk square-off API
        squareoff_all_url = f"{xts_api_root}/interactive/portfolio/squareoffall"
        
        payload = {
            "squareoffMode": square_off_mode,  # "DayWise" or "NetWise"
            "clientID": "*****"
        }
        
        print(f"Square-off all request payload: {payload}")
        
        # Send PUT request to square off all positions
        squareoff_response = requests.put(
            squareoff_all_url, 
            json=payload, 
            headers=headers, 
            timeout=10
        )
        
        squareoff_result = squareoff_response.json()
        print(f"Square-off all response: {squareoff_result}")

        if squareoff_result.get("type") == "success":
            success_msg = (
                f"✅ Successfully squared off ALL positions!\n"
                f"Total positions closed: {len(active_positions)}\n"
                f"Mode: {square_off_mode}"
            )
            print(success_msg)
            send_telegram_message(success_msg)
            
            # Log result details if available
            result_data = squareoff_result.get("result", {})
            if result_data:
                print(f"Square-off result details: {result_data}")
                
                # If the API returns order IDs or other details
                if isinstance(result_data, dict):
                    order_ids = result_data.get("orderIDs", [])
                    if order_ids:
                        print(f"Order IDs: {order_ids}")
                elif isinstance(result_data, list):
                    print(f"Closed {len(result_data)} positions")
            
            return squareoff_result
            
        else:
            error_desc = squareoff_result.get("description", "Unknown error")
            error_code = squareoff_result.get("code", "")
            error_msg = (
                f"❌ Failed to square off all positions:\n"
                f"Error: {error_desc}\n"
                f"Code: {error_code}"
            )
            print(error_msg)
            send_telegram_message(error_msg)
            
            return squareoff_result

    except requests.exceptions.RequestException as e:
        error_msg = f"❌ Network error during bulk exit: {str(e)}"
        logger.error(error_msg)
        send_telegram_message(error_msg)
        return None
    except Exception as e:
        error_msg = f"❌ Failed to exit all positions (bulk): {str(e)}"
        logger.error(error_msg, exc_info=True)
        send_telegram_message(error_msg)
        return None

def check_order_status(app_order_id):
    """Check order status using AppOrderID
    
    Args:
        app_order_id: The AppOrderID from order placement response
        
    Returns:
        dict: Order status details or None if error
    """
    global xts_token
    
    try:
        url = f"{xts_api_root}/interactive/orders"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        params = {
            "appOrderID": app_order_id
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()
    except Exception as e:
        logger.error(f"Failed to check order status: {e}")
        return None

def placing_market(symbol, qty, buy_sell, product_type, exchange_segment, exchange_instrument_id):
    """Place market order using XTS API and verify its status

    Args:
        symbol: Trading symbol
        qty: Order quantity
        buy_sell: "BUY" or "SELL"
        product_type: "MIS", "NRML", "CNC"
        exchange_segment: Exchange segment ID
        exchange_instrument_id: Exchange instrument ID
    """
    global xts_token

    try:
        url = f"{xts_api_root}/interactive/orders"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }

        payload = {
            "exchangeSegment": exchange_segment,
            "exchangeInstrumentID": exchange_instrument_id,
            "productType": product_type,
            "orderType": "MARKET",
            "orderSide": buy_sell,
            "timeInForce": "DAY",
            "disclosedQuantity": 0,
            "orderQuantity": abs(int(qty)),
            "limitPrice": 0,
            "stopPrice": 0,
            "orderUniqueIdentifier": f"XTS_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "clientID": "*****"
        }

        print(f"Placing market order: {payload}")
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        result = response.json()
        print(f"Order placement response: {result}")

        if result.get("type") == "success":
            app_order_id = result.get("result", {}).get("AppOrderID", "N/A")
            logger.info(f"Market order placed successfully for {symbol}, AppOrderID: {app_order_id}")
            
            # Wait a brief moment for order to process
            import time
            time.sleep(1)
            
            # Check order status
            order_status_response = check_order_status(app_order_id)
            
            if order_status_response and order_status_response.get("type") == "success":
                order_history = order_status_response.get("result", [])
                
                # Check if any of the order statuses is "Rejected"
                rejected_order = None
                latest_order = None
                
                for order in order_history:
                    if order.get("OrderStatus") == "Rejected":
                        rejected_order = order
                        break
                    # Keep track of the latest order status
                    latest_order = order
                
                if rejected_order:
                    # Order was rejected
                    reject_reason = rejected_order.get("CancelRejectReason", "Unknown reason")
                    logger.error(f"Market order REJECTED for {symbol}: {reject_reason}")
                    send_telegram_message(
                        f"❌ Market order REJECTED for {symbol}\n"
                        f"📊 {buy_sell} {qty} @ MARKET\n"
                        f"🔢 Order ID: {app_order_id}\n"
                        f"⚠️ Rejection Reason: {reject_reason}"
                    )
                else:
                    # Order was successful (not rejected)
                    final_status = latest_order.get("OrderStatus", "Unknown") if latest_order else "Unknown"
                    avg_price = latest_order.get("OrderAverageTradedPrice", "") if latest_order else ""
                    filled_qty = latest_order.get("CumulativeQuantity", 0) if latest_order else 0
                    
                    success_message = (
                        f"✅ Market order placed for {symbol}\n"
                        f"📊 {buy_sell} {qty} @ MARKET\n"
                        f"🔢 Order ID: {app_order_id}\n"
                        f"📈 Status: {final_status}"
                    )
                    
                    # Add price info if available and order is filled
                    if avg_price and avg_price != "" and final_status in ["Filled", "Traded"]:
                        success_message += f"\n💰 Avg Price: {avg_price}"
                        success_message += f"\n✔️ Filled Qty: {filled_qty}"
                    
                    logger.info(f"Market order successful for {symbol}, Status: {final_status}")
                    send_telegram_message(success_message)
            else:
                # Could not check order status, but placement was successful
                logger.warning(f"Could not verify order status for {symbol}, but placement successful")
                send_telegram_message(
                    f"⚠️ Market order placed for {symbol}\n"
                    f"📊 {buy_sell} {qty} @ MARKET\n"
                    f"🔢 Order ID: {app_order_id}\n"
                    f"⚠️ Status verification pending"
                )
        else:
            # Initial order placement failed
            error_desc = result.get('description', 'Unknown error')
            logger.error(f"Failed to place market order: {result}")
            send_telegram_message(
                f"❌ Failed to place market order for {symbol}\n"
                f"📊 {buy_sell} {qty} @ MARKET\n"
                f"Error: {error_desc}"
            )

        return result

    except Exception as e:
        logger.error(f"Failed to place market order: {e}")
        send_telegram_message(f"❌ Failed to place market order for {symbol}: {str(e)}")
        return None


def placing_limit(symbol, qty, limit_price, buy_sell, order_type, product_type, exchange_segment, exchange_instrument_id):
    """Place limit or market order using XTS API and verify its status

    Args:
        symbol: Trading symbol
        qty: Order quantity
        limit_price: Limit price (0 for market orders)
        buy_sell: "BUY" or "SELL"
        order_type: "LMT" for limit, "MKT" for market
        product_type: "MIS", "NRML", "CNC"
        exchange_segment: Exchange segment ID
        exchange_instrument_id: Exchange instrument ID
    """
    global xts_token

    try:
        url = f"{xts_api_root}/interactive/orders"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }

        # Determine order type
        if order_type == "LMT":
            xts_order_type = "LIMIT"
            price = float(limit_price)
        else:
            xts_order_type = "MARKET"
            price = 0

        payload = {
            "exchangeSegment": exchange_segment,
            "exchangeInstrumentID": exchange_instrument_id,
            "productType": product_type,
            "orderType": xts_order_type,
            "orderSide": buy_sell,
            "timeInForce": "DAY",
            "disclosedQuantity": 0,
            "orderQuantity": abs(int(qty)),
            "limitPrice": price,
            "stopPrice": 0,
            "orderUniqueIdentifier": f"XTS_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "clientID": "*****"
        }

        print(f"Placing {order_type} order: {payload}")
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        result = response.json()
        print(f"Order placement response: {result}")

        if result.get("type") == "success":
            app_order_id = result.get("result", {}).get("AppOrderID", "N/A")
            logger.info(f"{order_type} order placed successfully for {symbol}, AppOrderID: {app_order_id}")
            
            # Wait a brief moment for order to process
            import time
            time.sleep(1)
            
            # Check order status
            order_status_response = check_order_status(app_order_id)
            
            if order_status_response and order_status_response.get("type") == "success":
                order_history = order_status_response.get("result", [])
                
                # Check if any of the order statuses is "Rejected"
                rejected_order = None
                latest_order = None
                
                for order in order_history:
                    if order.get("OrderStatus") == "Rejected":
                        rejected_order = order
                        break
                    # Keep track of the latest order status
                    latest_order = order
                
                if rejected_order:
                    # Order was rejected
                    reject_reason = rejected_order.get("CancelRejectReason", "Unknown reason")
                    logger.error(f"{order_type} order REJECTED for {symbol}: {reject_reason}")
                    send_telegram_message(
                        f"❌ {order_type} order REJECTED for {symbol}\n"
                        f"📊 {buy_sell} {qty} @ {price if order_type == 'LMT' else 'MARKET'}\n"
                        f"🔢 Order ID: {app_order_id}\n"
                        f"⚠️ Rejection Reason: {reject_reason}"
                    )
                else:
                    # Order was successful (not rejected)
                    final_status = latest_order.get("OrderStatus", "Unknown") if latest_order else "Unknown"
                    avg_price = latest_order.get("OrderAverageTradedPrice", "") if latest_order else ""
                    filled_qty = latest_order.get("CumulativeQuantity", 0) if latest_order else 0
                    leaves_qty = latest_order.get("LeavesQuantity", qty) if latest_order else qty
                    
                    success_message = (
                        f"✅ {order_type} order placed for {symbol}\n"
                        f"📊 {buy_sell} {qty} @ {price if order_type == 'LMT' else 'MARKET'}\n"
                        f"🔢 Order ID: {app_order_id}\n"
                        f"📈 Status: {final_status}"
                    )
                    
                    # Add execution details based on order status
                    if final_status in ["Filled", "Traded"]:
                        if avg_price and avg_price != "":
                            success_message += f"\n💰 Avg Price: {avg_price}"
                        success_message += f"\n✔️ Filled Qty: {filled_qty}"
                    elif final_status in ["New", "PendingNew", "Open"]:
                        if order_type == "LMT":
                            success_message += f"\n⏳ Open Qty: {leaves_qty}"
                    
                    logger.info(f"{order_type} order successful for {symbol}, Status: {final_status}")
                    send_telegram_message(success_message)
            else:
                # Could not check order status, but placement was successful
                logger.warning(f"Could not verify order status for {symbol}, but placement successful")
                send_telegram_message(
                    f"⚠️ {order_type} order placed for {symbol}\n"
                    f"📊 {buy_sell} {qty} @ {price if order_type == 'LMT' else 'MARKET'}\n"
                    f"🔢 Order ID: {app_order_id}\n"
                    f"⚠️ Status verification pending"
                )
        else:
            # Initial order placement failed
            error_desc = result.get('description', 'Unknown error')
            logger.error(f"Failed to place {order_type} order: {result}")
            send_telegram_message(
                f"❌ Failed to place {order_type} order for {symbol}\n"
                f"📊 {buy_sell} {qty} @ {price if order_type == 'LMT' else 'MARKET'}\n"
                f"Error: {error_desc}"
            )

        return result

    except Exception as e:
        logger.error(f"Failed to place {order_type} order: {e}")
        send_telegram_message(f"❌ Failed to place {order_type} order for {symbol}: {str(e)}")
        return None

def order_placement_buy_side(symbol, qty, limit_price, order_type, product_type, exchange_segment, exchange_instrument_id):
    """
    Place buy order on XTS platform with position management
    
    Args:
        symbol: Trading symbol
        qty: Order quantity
        limit_price: Limit price for the order
        order_type: "LMT" for limit, "MKT" for market
        product_type: "MIS", "NRML", "CNC"
        exchange_segment: Exchange segment ID
        exchange_instrument_id: Exchange instrument ID
    """
    global xts_token
    
    # Fetch positions from XTS
    try:
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=DayWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        response = requests.get(positions_url, headers=headers, timeout=10)
        positions_data = response.json()
        print(positions_data)
        
        # Handle "Data Not Available" case (no positions)
        if response.status_code == 400 and positions_data.get("code") == "e-portfolio-0005":
            logger.info("No positions available. Proceeding with order placement.")
            limit_price = float(limit_price)
            cancel_single_order(symbol)
            placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                         product_type=product_type, exchange_segment=exchange_segment,
                         exchange_instrument_id=exchange_instrument_id)
            return
        
        # Raise for other HTTP errors
        response.raise_for_status()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch positions: {e}")
        send_telegram_message(f"❌ Failed to fetch positions: {str(e)}")
        return None
    
    limit_price = float(limit_price)  # Ensure limit price is a float
    cancel_single_order(symbol)  # Cancel any existing order for the symbol
    
    # Check if there are no active positions at all
    if positions_data.get("type") != "success":
        logger.warning("Failed to get positions data")
        placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)
        return
    
    position_list = positions_data.get("result", {}).get("positionList", [])
    
    if not position_list:
        print("No active positions.")
        placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)
        return
    
    # Pre-check if the symbol exists in positions
    symbol_found = False
    for position in position_list:
        if position.get('TradingSymbol') == symbol or position.get('ExchangeInstrumentId') == exchange_instrument_id:
            symbol_found = True
            net_qty = int(position.get('Quantity', 0))
            
            if net_qty != 0:
                print(position.get('TradingSymbol'))
                
                if net_qty > 0:  # Buy side position open (positive quantity)
                    print("Buy side position open. Will not place any order in the buy side as position is already open.")
                    placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                                product_type=product_type, exchange_segment=exchange_segment,
                                exchange_instrument_id=exchange_instrument_id)
                    send_telegram_message("Buy side position open. Will not place any order in the buy side as position is already open.")
                
                elif net_qty < 0:  # Sell side position open (negative quantity)
                    print("Sell side position open. Buy trade generated. Exit sell trade.")
                    exit_single_order(symbol)
                    placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                                product_type=product_type, exchange_segment=exchange_segment,
                                exchange_instrument_id=exchange_instrument_id)
                    send_telegram_message("Sell side position open. Buy trade generated. Exit sell trade.")
                
                else:
                    print("No side detected.")
            else:
                print("netQty == 0. Placing order in buy side.")
                placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                            product_type=product_type, exchange_segment=exchange_segment,
                            exchange_instrument_id=exchange_instrument_id)
            break  # Exit loop after handling the matching symbol
    
    if not symbol_found:
        # If symbol not found, directly place the order
        print(f"No symbol found for {symbol}. Placing order in buy side.")
        placing_limit(symbol, qty, limit_price, buy_sell="BUY", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)

def order_placement_sell_side(symbol, qty, limit_price, order_type, product_type, exchange_segment, exchange_instrument_id):
    """
    Place sell order on XTS platform with position management
    
    Args:
        symbol: Trading symbol
        qty: Order quantity
        limit_price: Limit price for the order
        order_type: "LMT" for limit, "MKT" for market
        product_type: "MIS", "NRML", "CNC"
        exchange_segment: Exchange segment ID
        exchange_instrument_id: Exchange instrument ID
    """
    global xts_token
    
    # Fetch positions from XTS
    try:
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=DayWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        response = requests.get(positions_url, headers=headers, timeout=10)
        positions_data = response.json()
        print(positions_data)
        
        # Handle "Data Not Available" case (no positions)
        if response.status_code == 400 and positions_data.get("code") == "e-portfolio-0005":
            logger.info("No positions available. Proceeding with order placement.")
            limit_price = float(limit_price)
            cancel_single_order(symbol)
            placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                         product_type=product_type, exchange_segment=exchange_segment,
                         exchange_instrument_id=exchange_instrument_id)
            return
        
        # Raise for other HTTP errors
        response.raise_for_status()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch positions: {e}")
        send_telegram_message(f"❌ Failed to fetch positions: {str(e)}")
        return None
    
    limit_price = float(limit_price)  # Ensure limit price is a float
    cancel_single_order(symbol)  # Cancel any existing order for the symbol
    
    # Check if there are no active positions at all
    if positions_data.get("type") != "success":
        logger.warning("Failed to get positions data")
        placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)
        return
    
    position_list = positions_data.get("result", {}).get("positionList", [])
    
    if not position_list:
        print("No active positions.")
        placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)
        return
    
    # Pre-check if the symbol exists in positions
    symbol_found = False
    for position in position_list:
        if position.get('TradingSymbol') == symbol or position.get('ExchangeInstrumentId') == exchange_instrument_id:
            symbol_found = True
            net_qty = int(position.get('Quantity', 0))
            
            if net_qty != 0:
                print(position.get('TradingSymbol'))
                
                if net_qty < 0:  # Sell side position open (negative quantity)
                    print("Sell side position open. Will not place any order in the sell side as position is already open.")
                    placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                                product_type=product_type, exchange_segment=exchange_segment,
                                exchange_instrument_id=exchange_instrument_id)
                    send_telegram_message("Sell side position open. Will not place any order in the sell side as position is already open.")
                
                elif net_qty > 0:  # Buy side position open (positive quantity)
                    print("Buy side position open. Sell trade generated. Exit buy trade.")
                    exit_single_order(symbol)
                    placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                                product_type=product_type, exchange_segment=exchange_segment,
                                exchange_instrument_id=exchange_instrument_id)
                    send_telegram_message("Buy side position open. Sell trade generated. Exit buy trade.")
                
                else:
                    print("No side detected.")
            else:
                print("netQty == 0. Placing order in sell side.")
                placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                            product_type=product_type, exchange_segment=exchange_segment,
                            exchange_instrument_id=exchange_instrument_id)
            break  # Exit loop after handling the matching symbol
    
    if not symbol_found:
        # If symbol not found, directly place the order
        print(f"No symbol found for {symbol}. Placing order in sell side.")
        placing_limit(symbol, qty, limit_price, buy_sell="SELL", order_type=order_type,
                     product_type=product_type, exchange_segment=exchange_segment,
                     exchange_instrument_id=exchange_instrument_id)

def exit_only_sell_trades(symbol, exchange_instrument_id=None):
    """
    Exit only sell side positions for a specific symbol
    
    Args:
        symbol: Trading symbol
        exchange_instrument_id: Optional exchange instrument ID for more precise matching
    """
    global xts_token
    print('Executing for symbol: ', symbol)
    print('Exchange instrument id: ', exchange_instrument_id)
    print('Exchange instrument id type: ', type(exchange_instrument_id))
    
    try:
        # Fetch positions from XTS (single API call)
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=DayWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        response = requests.get(positions_url, headers=headers, timeout=10)
        response.raise_for_status()
        positions_data = response.json()
        print(positions_data)
        
        if positions_data.get("type") != "success":
            logger.warning("Failed to get positions data")
            send_telegram_message("⚠️ Failed to get positions data")
            return None
        
        position_list = positions_data.get("result", {}).get("positionList", [])
        
        if not position_list:
            print("No active positions.")
            send_telegram_message("ℹ️ No active positions found")
            return None
            
        print('Position list: ', position_list)
        
        # Convert exchange_instrument_id to string for comparison
        exchange_instrument_id_str = str(exchange_instrument_id) if exchange_instrument_id else None
        
        # Check if symbol exists in positions
        for position in position_list:
            position_instrument_id = str(position.get('ExchangeInstrumentId', ''))
            
            print(f'Comparing: position[{position_instrument_id}] vs target[{exchange_instrument_id_str}]')
            
            # Match by ExchangeInstrumentId (preferred) or TradingSymbol (fallback)
            symbol_match = False
            if exchange_instrument_id_str and position_instrument_id == exchange_instrument_id_str:
                symbol_match = True
                print(f'✓ Matched by ExchangeInstrumentId: {position_instrument_id}')
            elif position.get('TradingSymbol') == symbol:
                symbol_match = True
                print(f'✓ Matched by TradingSymbol: {symbol}')
            
            print('Symbol match: ', symbol_match)
            
            if symbol_match:
                net_qty = int(position.get('Quantity', 0))
                trading_symbol = position.get('TradingSymbol', symbol)
                
                print(f'Net quantity: {net_qty}')
                
                if net_qty != 0:
                    if net_qty < 0:  # Sell side position (negative quantity)
                        print(f"Sell side position open for {trading_symbol}. Exiting sell trade.")
                        send_telegram_message(
                            f"🔄 Exiting SELL position:\n"
                            f"Symbol: {trading_symbol}\n"
                            f"Quantity: {net_qty}\n"
                            f"Instrument ID: {position_instrument_id}"
                        )
                        # Pass the already-fetched positions to avoid duplicate API call
                        exit_single_order(trading_symbol, position_list, exchange_instrument_id)
                        send_telegram_message(f"✅ Exited sell side position for {trading_symbol}")
                        return True
                    else:
                        print(f"Buy side position open for {trading_symbol}. Not a sell trade, skipping.")
                        send_telegram_message(
                            f"ℹ️ Buy side position found for {trading_symbol} (Qty: {net_qty}). Not exiting.",
                        )
                        return False
                else:
                    print(f"Position quantity is 0 for {trading_symbol}.")
                    send_telegram_message(f"ℹ️ Position quantity is 0 for {trading_symbol}")
                    return False
        
        print(f"No position found for symbol: {symbol} (ID: {exchange_instrument_id_str})")
        send_telegram_message(
            f"ℹ️ No matching position found:\n"
            f"Symbol: {symbol}\n"
            f"Instrument ID: {exchange_instrument_id_str}",
        )
        return False
        
    except Exception as e:
        logger.error(f"Failed to exit sell trades for {symbol}: {e}", exc_info=True)
        send_telegram_message(f"❌ Failed to exit sell trades for {symbol}: {str(e)}")
        return None

def exit_only_buy_trades(symbol, exchange_instrument_id=None):
    """
    Exit only buy side positions for a specific symbol
    
    Args:
        symbol: Trading symbol
        exchange_instrument_id: Optional exchange instrument ID for more precise matching
    """
    global xts_token
    print('Executing for symbol: ', symbol)
    print('Exchange instrument id: ', exchange_instrument_id)
    print('Exchange instrument id type: ', type(exchange_instrument_id))
    
    try:
        # Fetch positions from XTS (single API call)
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=DayWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        response = requests.get(positions_url, headers=headers, timeout=10)
        response.raise_for_status()
        positions_data = response.json()
        print(positions_data)
        
        if positions_data.get("type") != "success":
            logger.warning("Failed to get positions data")
            send_telegram_message("⚠️ Failed to get positions data")
            return None
        
        position_list = positions_data.get("result", {}).get("positionList", [])
        
        if not position_list:
            print("No active positions.")
            send_telegram_message("ℹ️ No active positions found")
            return None
            
        print('Position list: ', position_list)
        
        # Convert exchange_instrument_id to string for comparison
        exchange_instrument_id_str = str(exchange_instrument_id) if exchange_instrument_id else None
        
        # Check if symbol exists in positions
        for position in position_list:
            position_instrument_id = str(position.get('ExchangeInstrumentId', ''))
            
            print(f'Comparing: position[{position_instrument_id}] vs target[{exchange_instrument_id_str}]')
            
            # Match by ExchangeInstrumentId (preferred) or TradingSymbol (fallback)
            symbol_match = False
            if exchange_instrument_id_str and position_instrument_id == exchange_instrument_id_str:
                symbol_match = True
                print(f'✓ Matched by ExchangeInstrumentId: {position_instrument_id}')
            elif position.get('TradingSymbol') == symbol:
                symbol_match = True
                print(f'✓ Matched by TradingSymbol: {symbol}')
            
            print('Symbol match: ', symbol_match)
            
            if symbol_match:
                net_qty = int(position.get('Quantity', 0))
                trading_symbol = position.get('TradingSymbol', symbol)
                
                print(f'Net quantity: {net_qty}')
                
                if net_qty != 0:
                    if net_qty > 0:  # Buy side position (positive quantity)
                        print(f"Buy side position open for {trading_symbol}. Exiting buy trade.")
                        send_telegram_message(
                            f"🔄 Exiting BUY position:\n"
                            f"Symbol: {trading_symbol}\n"
                            f"Quantity: {net_qty}\n"
                            f"Instrument ID: {position_instrument_id}"
                        )
                        # Pass the already-fetched positions to avoid duplicate API call
                        exit_single_order(trading_symbol, position_list, exchange_instrument_id)
                        send_telegram_message(f"✅ Exited buy side position for {trading_symbol}")
                        return True
                    else:
                        print(f"Sell side position open for {trading_symbol}. Not a buy trade, skipping.")
                        send_telegram_message(
                            f"ℹ️ Sell side position found for {trading_symbol} (Qty: {net_qty}). Not exiting.",
                        )
                        return False
                else:
                    print(f"Position quantity is 0 for {trading_symbol}.")
                    send_telegram_message(f"ℹ️ Position quantity is 0 for {trading_symbol}")
                    return False
        
        print(f"No position found for symbol: {symbol} (ID: {exchange_instrument_id_str})")
        send_telegram_message(
            f"ℹ️ No matching position found:\n"
            f"Symbol: {symbol}\n"
            f"Instrument ID: {exchange_instrument_id_str}",
        )
        return False
        
    except Exception as e:
        logger.error(f"Failed to exit buy trades for {symbol}: {e}", exc_info=True)
        send_telegram_message(f"❌ Failed to exit buy trades for {symbol}: {str(e)}")
        return None

def exit_half_position(symbol, match_qty, product_type, exchange_segment, exchange_instrument_id):
    """
    Exit half or partial position for a specific symbol
    
    Args:
        symbol: Trading symbol
        match_qty: The quantity to keep (will exit the difference)
        product_type: "MIS", "NRML", "CNC"
        exchange_segment: Exchange segment ID
        exchange_instrument_id: Exchange instrument ID
    """
    global xts_token
    
    try:
        # Fetch positions from XTS
        positions_url = f"{xts_api_root}/interactive/portfolio/dealerpositions?dayOrNet=DayWise&clientID=*****"
        headers = {
            "Authorization": xts_token,
            "Content-Type": "application/json"
        }
        
        response = requests.get(positions_url, headers=headers, timeout=10)
        response.raise_for_status()
        positions_data = response.json()
        print(positions_data)
        
        if positions_data.get("type") != "success":
            logger.warning("Failed to get positions data")
            return None
        
        position_list = positions_data.get("result", {}).get("positionList", [])
        
        if not position_list:
            print("No active positions. Do nothing in order half exit.")
            return None
        
        # Find position for this symbol
        for position in position_list:
            if position.get('TradingSymbol') == symbol or position.get('ExchangeInstrumentId') == exchange_instrument_id:
                net_qty = int(position.get('Quantity', 0))
                
                # Get product type from position if not provided
                position_product_type = product_type or position.get('ProductType', 'NRML')
                
                if abs(net_qty) > match_qty:
                    if net_qty > 0:  # Buy side position (positive quantity)
                        print(f"Buy side half exit is working. Current qty: {net_qty}, Match qty: {match_qty}")
                        qty = net_qty - match_qty
                        placing_market(
                            symbol=symbol,
                            qty=qty,
                            buy_sell="SELL",
                            product_type=position_product_type,
                            exchange_segment=exchange_segment,
                            exchange_instrument_id=exchange_instrument_id
                        )
                        print(f"Buy side half exit completed. Exited qty: {qty}")
                        send_telegram_message(f"✅ Buy side half exit for {symbol}: Sold {qty} qty")
                        return True
                    
                    elif net_qty < 0:  # Sell side position (negative quantity)
                        print(f"Sell side half exit is working. Current qty: {net_qty}, Match qty: {match_qty}")
                        qty = abs(net_qty) - match_qty
                        placing_market(
                            symbol=symbol,
                            qty=qty,
                            buy_sell="BUY",
                            product_type=position_product_type,
                            exchange_segment=exchange_segment,
                            exchange_instrument_id=exchange_instrument_id
                        )
                        print(f"Sell side half exit completed. Exited qty: {qty}")
                        send_telegram_message(f"✅ Sell side half exit for {symbol}: Bought {qty} qty")
                        return True
                else:
                    print(f"Current position qty ({abs(net_qty)}) is not greater than match_qty ({match_qty}). No half exit needed.")
                    return False
        
        print(f"No position found for symbol: {symbol}")
        return False
        
    except Exception as e:
        logger.error(f"Failed to exit half position for {symbol}: {e}")
        send_telegram_message(f"❌ Failed to exit half position for {symbol}: {str(e)}")
        return None
# Initialize XTS client on module load
try:
    initialize_xts_client()
except Exception as e:
    logger.error(f"Failed to initialize XTS client on module load: {e}")
    print(f"Warning: XTS client initialization failed: {e}")
