import asyncio
import os
from google import genai
from google.genai import types

async def main():
    client = genai.Client(http_options={"api_version": "v1alpha"})
    
    # 1x1 pixel valid JPEG bytes
    valid_jpg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xdb\x00C\x01\t\t\t\x0c\x0b\x0c\x18\r\r\x182!\x1c!22222222222222222222222222222222222222222222222222\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01"\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x05\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x15\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xff\xc4\x00\x14\x11\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xd2\x98\x10\x1c>\xff\xd9'

    for mode in ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"]:
        print(f"\nTesting {mode}")
        try:
            response = await client.aio.models.generate_content(
                model=mode,
                contents=[
                    types.Part.from_bytes(data=valid_jpg, mime_type="image/jpeg"),
                    "Give me a modified copy of this image where everything is red. Output only the generated image."
                ]
            )
            print("Success:", response)
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                has_inline = hasattr(part, 'inline_data') and part.inline_data is not None
                print(f"Has inline_data: {has_inline}")
                if has_inline:
                    print(f"Data length: {len(part.inline_data.data)}")
                    print("This model can output images when prompted!")
                else:
                    if hasattr(part, 'text') and part.text:
                        print(f"Text output: {part.text}")
        except Exception as e:
            print("Error:", e)
        
if __name__ == "__main__":
    asyncio.run(main())
