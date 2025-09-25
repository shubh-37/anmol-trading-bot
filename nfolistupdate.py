import requests

def nfo_update():
        
    # List of URLs and corresponding local file paths
    files_to_download = {
        "https://public.fyers.in/sym_details/NSE_FO.csv": "NSE_FO.csv",
        "https://public.fyers.in/sym_details/NSE_CM.csv": "NSE_CM.csv",
        "https://public.fyers.in/sym_details/BSE_CM.csv": "BSE_CM.csv",
        "https://public.fyers.in/sym_details/MCX_COM.csv": "MCX_COM.csv",
        "https://public.fyers.in/sym_details/NSE_CD.csv": "NSE_CD.csv",
        "https://public.fyers.in/sym_details/BSE_FO.csv" : "BSE_FO.csv"
    }
    
    # Loop through each URL and file path
    for url, local_file_path in files_to_download.items():
        try:
            # Send an HTTP GET request to the URL
            response = requests.get(url)
    
            # Check if the request was successful
            if response.status_code == 200:
                # Save the file locally
                with open(local_file_path, "wb") as file:
                    file.write(response.content)
                print(f"File '{local_file_path}' downloaded successfully.")
            else:
                print(f"Failed to download {url}. Status code: {response.status_code}")
        except Exception as e:
            print(f"An error occurred while downloading {url}: {e}")
    