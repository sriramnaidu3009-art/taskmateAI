import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("ERROR: GEMINI_API_KEY is missing from your .env file!")
else:
    client = genai.Client(api_key=api_key)
    print("--- AVAILABLE MODELS FOR YOUR API KEY ---")
    try:
        for model in client.models.list():
            print(model.name)
    except Exception as e:
        print(f"Failed to list models: {e}")