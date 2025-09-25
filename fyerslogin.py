from datetime import datetime
from time import sleep
import time
import pyotp
import requests
from urllib.parse import parse_qs, urlparse
import warnings
import pandas as pd
from fyers_apiv3 import fyersModel
import json
import os
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context
from urllib3.poolmanager import PoolManager
import logging

pd.set_option('display.max_columns', None)
warnings.filterwarnings('ignore')

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import base64
def getEncodedString(string):
    string = str(string)
    base64_bytes = base64.b64encode(string.encode("ascii"))
    return base64_bytes.decode("ascii")

retries = 2

class SourceIpAdapter(HTTPAdapter):
    def __init__(self, source_ip, **kwargs):
        self.source_ip = source_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = create_urllib3_context()
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            source_address=(self.source_ip, 0),  # Bind to 147.93.152.218
            ssl_context=context,
            **pool_kwargs
        )

def auto_login():
    logger = logging.getLogger(__name__)
    logger.info("Starting Fyers authentication process")

    # Load credentials from environment variables
    redirect_uri = os.getenv('FYERS_REDIRECT_URI', 'https://www.google.com')
    client_id = os.getenv('FYERS_CLIENT_ID')
    secret_key = os.getenv('FYERS_SECRET_KEY')
    FY_ID = os.getenv('FYERS_FY_ID')
    TOTP_KEY = os.getenv('FYERS_TOTP_KEY')
    PIN = os.getenv('FYERS_PIN')

    # Validate required environment variables
    if not all([client_id, secret_key, FY_ID, TOTP_KEY, PIN]):
        raise ValueError("Missing required Fyers credentials in environment variables")

    grant_type = "authorization_code"
    response_type = "code"
    state = "sample"   
                   
    # Create a session with the custom adapter for 147.93.152.218
    session = requests.Session()
    # session.mount("https://", SourceIpAdapter(source_ip="147.93.152.218"))

    # Headers to mimic a browser and avoid Cloudflare detection
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "Origin": "https://api-t2.fyers.in",
        "Referer": "https://api-t2.fyers.in/",
        "Connection": "keep-alive"
    }

    # Step 1: Generate auth code URL
    appSession = fyersModel.SessionModel(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        state=state,
        secret_key=secret_key,
        grant_type=grant_type
    )
    generateTokenUrl = appSession.generate_authcode()
    print("Login 1: Generated auth URL:", generateTokenUrl)

    print("login 1")
    URL_SEND_LOGIN_OTP = "https://api-t2.fyers.in/vagator/v2/send_login_otp"
    try:
        res = session.post(
            url=URL_SEND_LOGIN_OTP,
            json={"fy_id": FY_ID, "app_id": "2"},
            headers=headers,
            timeout=10
        )
        res.raise_for_status()
        logger.info("Successfully sent login OTP")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error in send_login_otp: {e}")
        if 'res' in locals():
            logger.error(f"Response: {res.text}")
        raise Exception(f"Failed to send login OTP: {e}")

    print("login 2")
    print(res) 
    

    # Throttle to avoid rate limits
    if datetime.now().second % 30 > 27:
        print("Throttling for OTP timing...")
        time.sleep(5)

    # Step 3: Verify OTP
    URL_VERIFY_OTP = "https://api-t2.fyers.in/vagator/v2/verify_otp"
    try:
        res2 = session.post(
            url=URL_VERIFY_OTP,
            json={"request_key": res.json()["request_key"], "otp": pyotp.TOTP(TOTP_KEY).now()},
            headers=headers,
            timeout=10
        )
        res2.raise_for_status()
        logger.info("Successfully verified OTP")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error in verify_otp: {e}")
        if 'res2' in locals():
            logger.error(f"Response: {res2.text}")
        raise Exception(f"Failed to verify OTP: {e}")

    # Step 4: Verify PIN
    URL_VERIFY_OTP2 = "https://api-t2.fyers.in/vagator/v2/verify_pin"
    payload2 = {
        "request_key": res2.json()["request_key"],
        "identity_type": "pin",
        "identifier": PIN
    }
    try:
        res3 = session.post(
            url=URL_VERIFY_OTP2,
            json=payload2,
            headers=headers,
            timeout=10
        )
        res3.raise_for_status()
        logger.info("Successfully verified PIN")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error in verify_pin: {e}")
        if 'res3' in locals():
            logger.error(f"Response: {res3.text}")
        raise Exception(f"Failed to verify PIN: {e}")

    # Update session with access token
    session.headers.update({
        "Authorization": f"Bearer {res3.json()['data']['access_token']}"
    })


    TOKENURL = "https://api-t1.fyers.in/api/v3/token"
    payload3 = {
        "fyers_id": FY_ID,
        "app_id": client_id[:-4],
        "redirect_uri": redirect_uri,
        "appType": "100",
        "code_challenge": "",
        "state": "None",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True
    }

    try:
        res3 = session.post(
            url=TOKENURL,
            json=payload3,
            headers=headers,
            timeout=10
        )
        res3.raise_for_status()
        logger.info("Successfully obtained token")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error in token request: {e}")
        if 'res3' in locals():
            logger.error(f"Response: {res3.text}")
        raise Exception(f"Failed to obtain token: {e}") 
    
   # Step 6: Process auth code and generate token
    url = res3.json()['Url']
    parsed = urlparse(url)
    auth_code = parse_qs(parsed.query)['auth_code'][0]
    print("Auth code:", auth_code)

    session_model = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type=response_type,
        grant_type=grant_type
    )
    session_model.set_token(auth_code)

    try:
        response = session_model.generate_token()
        logger.info("Successfully generated access token")

        # Save token to file with secure permissions
        token_file = "store_token.json"
        with open(token_file, "w") as outfile:
            json.dump(response, outfile, indent=4)

        # Set secure file permissions (owner read/write only)
        os.chmod(token_file, 0o600)

        return response
    except Exception as e:
        logger.error(f"Error generating token: {e}")
        raise Exception(f"Failed to generate access token: {e}") 
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        token_response = auto_login()
        print("Authentication successful")
    except Exception as e:
        print(f"Authentication failed: {e}")
        exit(1)


