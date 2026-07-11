import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

def main():
    """Performs the console-based authentication test."""
    creds = None
    if os.path.exists("token.json"):
        os.remove("token.json") # Ensure we always re-authenticate for this test
        print("Removed existing token.json for a fresh authentication test.")

    try:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_console()
        
        with open("token.json", "w") as token:
            token.write(creds.to_json())
            
        print("\nSuccessfully authenticated and created token.json!")

    except Exception as e:
        print(f"\nAn error occurred during authentication: {e}")

if __name__ == "__main__":
    main()

