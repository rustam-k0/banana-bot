import asyncio
import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

async def main():
    try:
        # Create a dummy image part
        with open("icon.png", "wb") as f:
            f.write(b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xdb\x00C\x01\t\t\t\x0c\x0b\x0c\x18\r\r\x182!\x1c!22222222222222222222222222222222222222222222222222\xff\xc0\x00\x11\x08\x00\x08\x00\x08\x03\x01"\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x14\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x14\x11\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xfd\xfc\xe2\x8a\x00\x00\x00')
        img_bytes = open("icon.png", "rb").read()
        
        print("Testing gemini-3-pro-image-preview with image + text...")
        res = await client.aio.models.generate_content(
            model='gemini-3-pro-image-preview',
            contents=['Change this image to black and white', genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")],
            config=genai.types.GenerateContentConfig(response_modalities=["IMAGE"])
        )
        for part in res.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                print("Pro Edit Success:", len(part.inline_data.data))
    except Exception as e:
        print("Pro Edit Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
