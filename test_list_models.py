import asyncio
import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

async def main():
    try:
        models = await client.aio.models.list()
        for m in models:
            if 'image' in m.name or 'imagen' in m.name or 'gemini' in m.name:
                print(m.name)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
