import pandas as pd
from datetime import datetime, timedelta
from fyerslogin import auto_login
from fyers_apiv3 import fyersModel
import json
import os
import re
import sys
import requests
import urllib.parse
import logging
from functools import lru_cache
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get configuration from environment
token_telegram = os.getenv('TELEGRAM_TOKEN')
chat_id_telegram = os.getenv('TELEGRAM_CHAT_ID')
client_id = os.getenv('FYERS_CLIENT_ID')

# Validate required environment variables
if not all([token_telegram, chat_id_telegram, client_id]):
    raise ValueError("Missing required environment variables. Check .env file.")

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


def extract_option_details(symbol):
    # Define the regex pattern to extract the components
    pattern = r'(?P<main_symbol>\w+)(?P<date>\d{2})(?P<month>\d{2})(?P<day>\d{2})(?P<option_type>[CP])(?P<strike>\d+)'
    
    match = re.match(pattern, symbol)
    
    if match:
        main_symbol = match.group('main_symbol')
        date = f"{match.group('date')}-{match.group('month')}-{match.group('day')}"
        option_type = match.group('option_type').lower()
        option_type_full = 'CE' if option_type == 'c' else 'PE'
        strike = match.group('strike')
        
        return {
            'main_symbol': main_symbol,
            'date': date,
            'option_type': option_type_full,
            'strike': strike
        }
    else:
        return None

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
            'parse_mode': 'HTML'  # Support basic formatting
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


# Removed dangerous stdout redirection - using proper logging instead


def initialize_fyers_client():
    """Initialize Fyers client with proper error handling"""
    try:
        with open("./store_token.json", "r") as access_token_file:
            store_tokenjson = json.load(access_token_file)
            access_token = store_tokenjson["access_token"]

        fyers = fyersModel.FyersModel(
            client_id=client_id,
            token=access_token,
            log_path=os.path.join(os.getcwd(), "logs")
        )

        # Test connection
        response = fyers.get_profile()
        if response.get('code') != 200:
            raise Exception(f"Fyers authentication failed: {response}")

        logger.info("Fyers client initialized successfully")
        return fyers

    except FileNotFoundError:
        logger.error("Token file not found. Please run authentication first.")
        raise Exception("Authentication required. Run fyerslogin.py first.")
    except Exception as e:
        logger.error(f"Failed to initialize Fyers client: {e}")
        raise

# Initialize global fyers client
fyers = initialize_fyers_client()

    
@lru_cache(maxsize=100)
def get_future_name(symbol, exchange):
    """Get future symbol name with caching for performance"""
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


# pp = get_future_name(symbol="GOLDM",exchnge="MCX")

# print(pp)
        
def get_sport_name(symbol,exchnge):
    try:
        if exchnge == "NSE":
            EXCHNGE_NO = 10
            local_filename = "NSE_CM.csv"
             
        elif exchnge == "MCX":
            local_filename = "MCX_COM.csv"
            EXCHNGE_NO = 30 
        elif exchnge == "BSE":
            local_filename = "BSE_CM.csv"
            EXCHNGE_NO = 10
        column_names = [
            "num", "sym des", "exch no", "lot size", "tick size", "blank",
            "timing", "date", "Time", "symbol name",
            "ID 1", "id 2", "token no", "symbol main name", "ISIN", "strike", "option type", "pass", "none", "0", "0.0"
        ]
        df = pd.read_csv(local_filename, header=None, names=column_names)
        print(df)
        df = df[(df["exch no"] == EXCHNGE_NO) & (df["symbol main name"] == symbol)]
        #print(df)
        first_row = df.iloc[0]
        print(first_row)
        symbol_name = first_row['symbol name']
        # lot_size = first_row["lot size"]
        return symbol_name
    except Exception as e:
        return None

import pandas as pd

def getting_strike(symbol, option_type, strike,exchnge,date):
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
                return               

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
    



# Removed unused parse_message_tv function - dead code cleanup    
    

def cancel_orders_for_all():
    response = fyers.orderbook()
    trading_data = response
    print(response)
    filtered_data = [order for order in trading_data.get('orderBook', []) if order.get('status') == 6]
    if not filtered_data :
        print("All positions are closed. nothing to cancle")
    #print(filtered_data)    
    else:
        filtered_ids = [order.get('id') for order in filtered_data]
        for order_id in filtered_ids:
            data = {"id": order_id}
            response = fyers.cancel_order(data=data)
            print(response) 



def cancel_single_order(symbol):
    response = fyers.orderbook()
    trading_data = response
    print(response)
    filtered_data = [order for order in trading_data.get('orderBook', []) if order.get('status') == 6 and order.get('symbol') == symbol]

    if not filtered_data :
        print(f"symbol {symbol} positions are closed. nothing to cancle")
        send_telegram_message(f"symbol {symbol} positions are closed. nothing to cancel")
    else:
        filtered_ids = [order.get('id') for order in filtered_data]
        for order_id in filtered_ids:
            data = {"id": order_id}
            response = fyers.cancel_order(data=data)
            print(response) 
            


# Fixed typo: cancel_single_order
def insideexit_order_symbol(symbol_id):
    data = {
        "id":symbol_id
    }
    
    response = fyers.exit_positions(data=data)
    #print(response)    

def exit_single_order(symbol):
    position = fyers.positions()
    print(position)
    

    if not position['netPositions']:
        print("No active positions.")
        return

    for order in position['netPositions']:
        if order['symbol'] == symbol and order['netQty']  != 0 :

            
            # Prepare data for the exit request
            data = {
                "id": order['id']
            }
            
            # Attempt to exit the position
            response = fyers.exit_positions(data=data)
            print(response)
            # send_telegram_message(message="hii script is working ")
            
            # Check if the exit was successful
            if response.get('code') == 200:
                print("Successfully closed position for symbol:", symbol)
                send_telegram_message(f"Successfully closed position for symbol: {symbol}")
            else:
                print("Failed to close position for symbol:", symbol)
                print("Response:", response)
                send_telegram_message(f"Failed to close position for symbol: {symbol} {response}")
            return
    
    print("open psotion  found for symbol:", symbol)

            
# exit_single_order("NSE:BANKNIFTY24N1352500CE")




def exit_all_order():
    data = {}
    
    response = fyers.exit_positions(data=data)
    print(response)  
    send_telegram_message(response)
def placing_market(fyers,symbol,qty,buy_sell,productType):
    
        #order_tag = f"st{strategy}si{signal}".replace(':', '').replace(',', '').replace('.', '')  # Remove any invalid characters
        data = {
            "symbol":symbol,
            "qty":abs(qty),
            "type":2,
            "side":buy_sell,
            "productType":productType,
            "limitPrice":0,
            "stopPrice":0,
            "validity":"DAY",
            "disclosedQty":0,
            "offlineOrder":False,
            "orderTag":"RASHALGOMRKT",
        } 
        response = fyers.place_order(data=data)
        print(response)
        send_telegram_message(response)



def exit_half_position(symbol,match_qty):
    position = fyers.positions()
    print(position)  
    if not position['netPositions']:
        print("No active positions do nothing in order half exit .")
        
    for order in position['netPositions']:
        if order['symbol'] == symbol :
            if order['netQty'] > match_qty:    
                if order['side'] == 1:
                    print(f"buy side half exit is working {order['netQty']} match qty  {match_qty}")
                    qty = order['netQty'] - match_qty
                    placing_market(fyers, symbol, qty, buy_sell=-1, productType=order['productType'])
                    
                    print(f"buy side half exit is working exit qty with {qty} ")
                elif order['side'] == -1:
                    print("Sell side position open. buy trade genrated exit sell trade ")
                    print(f"buy side half exit is working {order['netQty']} match qty  {match_qty}")
                    qty = order['netQty'] - match_qty
                    placing_market(fyers, symbol, qty, buy_sell=1, productType=order['productType'])
                    print(f"sell side half exit is working exit qty with {qty} ")

# exit_half_position(symbol="NSE:BANKNIFTY24N1352500CE",match_qty=30)


def placing_limit(fyers,symbol,qty,limitPrice,buy_sell,order_type):
    
    if order_type == "LMT":
        type = 1
        limitPrice = limitPrice
        
    else :
        type = 2
        limitPrice = 0    
        
    
    data = {
        "symbol":symbol,
        "qty":abs(qty),
        "type":type,
        "side":buy_sell,
        "productType":"MARGIN",
        "limitPrice":limitPrice,
        "stopPrice":0,
        "validity":"DAY",
        "disclosedQty":0,
        "offlineOrder":False,
        "orderTag":"tag1" 
    }
    print(data)
    response = fyers.place_order(data=data)
    print(response)
    #print(data)
    #response = data
    print(f"{order_type} order place {symbol}")
    send_telegram_message(f"{order_type} order place {symbol} {response}")

def place_market_order(fyers, symbol, qty, buy_sell):

    data = {
        "symbol":symbol,
        "qty":abs(qty),
        "type":1,
        "side":buy_sell,
        "productType":"MARGIN",
        "limitPrice":0,
        "stopPrice":0,
        "validity":"DAY",
        "disclosedQty":0,
        "offlineOrder":False,
        "orderTag":"tag1" 
    }
    print(data)
    response = data
    # response = fyers.place_order(data=data)
    # print(response)

    logger.info(f"Market order placed for {symbol}")
    send_telegram_message(f"Market order placed for {symbol}: {response}")



def order_placement_buy_side(symbol, qty, limitPrice, order_type):
    position = fyers.positions()  # Fetch positions from fyers
    print(position)
    limitPrice = float(limitPrice)  # Ensure limit price is a float
    cancel_single_order(symbol)  # Cancel any existing order for the symbol
    
    # Check if there are no active positions at all
    if not position['netPositions']:
        print("No active positions.")
        placing_limit(fyers, symbol, qty, limitPrice, buy_sell=1, order_type=order_type)
        return

    # Pre-check if the symbol exists in net positions
    if any(order['symbol'] == symbol for order in position['netPositions']):
        # Iterate through net positions to handle the specific symbol
        for order in position['netPositions']:
            if order['symbol'] == symbol:
                if int(order['netQty']) != 0:
                    print(order['symbol'])
                    if order['side'] == 1:
                        print("Buy side position open. Will not place any order in the buy side as position is already open.")
                        placing_limit(fyers, symbol, qty, limitPrice, buy_sell=1, order_type=order_type)
                        send_telegram_message("Buy side position open. Will not place any order in the buy side as position is already open.")
                    elif order['side'] == -1:
                        print("Sell side position open. Buy trade generated. Exit sell trade.")
                        exit_single_order(symbol)
                        placing_limit(fyers, symbol, qty, limitPrice, buy_sell=1, order_type=order_type)
                        send_telegram_message("Sell side position open. Sell trade generated. Exit sell trade.")
                    else:
                        print("No side detected.")
                else:
                    print("netQty == 0. Placing order in buy side.")
                    placing_limit(fyers, symbol, qty, limitPrice, buy_sell=1, order_type=order_type)
                break  # Exit loop after handling the matching symbol
    else:
        # If symbol not found, directly place the order
        print(f"No symbol found for {symbol}. Placing order in buy side.")
        placing_limit(fyers, symbol, qty, limitPrice, buy_sell=1, order_type=order_type)





def order_placement_sell_side(symbol,qty,limitPrice,order_type):
    position = fyers.positions()
    print(position)
    cancel_single_order(symbol)    
    if not position['netPositions']:
        print("No active positions.")
        placing_limit(fyers,symbol,abs(qty),limitPrice,buy_sell=-1,order_type=order_type)
        return
    
    
    if any(order['symbol'] == symbol for order in position['netPositions']):
        # If the symbol exists, process the net positions
        for order in position['netPositions']:
            if order['symbol'] == symbol:
                if order['netQty'] != 0:
                    if order['side'] == 1:
                        print("Buy side position open. Will not place any order in the buy side as position is already open.")
                        exit_single_order(symbol)  # Exit current order
                        placing_limit(fyers, symbol, abs(qty), limitPrice, buy_sell=-1, order_type=order_type)
                        send_telegram_message("Buy side position open. Will not place any order in the buy side as position is already open.")
                    elif order['side'] == -1:
                        print("Sell side position open. Sell trade generated. Exit sell trade.")
                        placing_limit(fyers, symbol, abs(qty), limitPrice, buy_sell=-1, order_type=order_type)
                        send_telegram_message("Sell side position open. Sell trade generated. Exit sell trade.")
                else:
                    print("netQty == 0. Placing order in sell side.")
                    placing_limit(fyers, symbol, qty, limitPrice, buy_sell=-1, order_type=order_type)
                break  # Exit the loop as we've handled the matching symbol
    else:
        # If the symbol is not found, directly place the order
        print(f"No symbol found for {symbol}. Placing order in sell side.")
        placing_limit(fyers, symbol, qty, limitPrice, buy_sell=-1, order_type=order_type)


def exit_only_sell_trades(symbol):
    position = fyers.positions()
    print(position)
    if not position['netPositions']:
        print("No active positions.")

    
    if any(order['symbol'] == symbol for order in position['netPositions']):
        # If the symbol exists, process the net positions
        for order in position['netPositions']:
            if order['symbol'] == symbol:
                if order['netQty'] != 0:
                    if order['side'] == -1:
                        print("Buy side position open. Will not place any order in the buy side as position is already open.")
                        exit_single_order(symbol)  # Exit current order    

def exit_only_buy_trades(symbol):
    position = fyers.positions()
    print(position)
    if not position['netPositions']:
        print("No active positions.")

    
    if any(order['symbol'] == symbol for order in position['netPositions']):
        # If the symbol exists, process the net positions
        for order in position['netPositions']:
            if order['symbol'] == symbol:
                if order['netQty'] != 0:
                    if order['side'] == 1:
                        print("Buy side position open. Will not place any order in the buy side as position is already open.")
                        exit_single_order(symbol)  # Exit current order    

     
                
# order_placement_buy_side(symbol="NSE:BANKNIFTY24N1352500CE",qty=15,limitPrice=1)   
# order_placement_buy_side(symbol="NSE:BANKNIFTY24N1352500CE",qty=15,limitPrice=1)   







