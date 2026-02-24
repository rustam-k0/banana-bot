import asyncio
import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

async def main():
    try:
        res = await client.aio.models.generate_content(
            model='gemini-3-pro-image-preview',
            contents='Draw a cute cat',
            config=genai.types.GenerateContentConfig(response_modalities=["IMAGE"])
        )
        print("Response received.")
        for part in res.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                print("Found inline data!")
                if part.inline_data.data:
                    print(f"Data length: {len(part.inline_data.data)}")
            else:
                print("Part does not have inline_data.")
                print("Attributes:", dir(part))
    except Exception as e:
        print("Error:", e)
        
    try:
        res2 = await client.aio.models.generate_content(
            model='gemini-2.5-flash-image',
            contents='Draw a cute cat',
            config=genai.types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]) # as in my code
        )
        print("Flash image success")
        print(res2)
    except Exception as e:
        print("Flash image error:", e)

if __name__ == "__main__":
    asyncio.run(main())
