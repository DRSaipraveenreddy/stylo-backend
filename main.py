from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
import os
import re
import uuid
import json

load_dotenv()

app = FastAPI()

# CORS — allows frontend to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Credentials from .env
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Gemini
ai_client = genai.Client(api_key=GEMINI_API_KEY) 

# ── HEALTH CHECK ──────────────────────────────────────────────
@app.get("/hello")
def hello():
    return {"message": "Backend is working!"}

# ── UPLOAD IMAGE ──────────────────────────────────────────────
@app.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    category: str = Form(None),
    item_name: str = Form(None),
):
    try:
        contents = await file.read()
        clean_name = re.sub(r'[^a-zA-Z0-9.-]', '_', file.filename)
        file_path = f"{user_id}/{uuid.uuid4()}_{clean_name}"

        # Upload to Supabase Storage
        supabase.storage.from_("clothing-images").upload(
            path=file_path,
            file=contents,
            file_options={"content-type": file.content_type, "x-upsert": "true"}
        )

        # Get public URL
        public_url = supabase.storage.from_("clothing-images").get_public_url(file_path)

        # Save to clothing_items table
        item_data = {
            "user_id": user_id,
            "image_url": public_url,
            "filename": clean_name,
            "item_name": item_name or clean_name,
            "category": category or "General",
        }
        supabase.table("clothing_items").insert(item_data).execute()

        return {"status": "success", "image_url": public_url}

    except Exception as e:
        print(f"Upload error: {e}")
        return {"error": str(e)}


# ── SCAN OUTFIT PHOTO & AUTO-SORT INTO WARDROBE ───────────────
@app.post("/scan-outfit")
async def scan_outfit(
    file: UploadFile = File(...),
    user_id: str = Form(...),
):
    try:
        # Step 1 — Read image bytes directly (no base64 needed)
        contents = await file.read()
        mime_type = file.content_type or "image/jpeg"

        # Step 2 — Upload original outfit photo to Supabase Storage
        clean_name = re.sub(r'[^a-zA-Z0-9.-]', '_', file.filename)
        file_path = f"{user_id}/outfits/{uuid.uuid4()}_{clean_name}"

        supabase.storage.from_("clothing-images").upload(
            path=file_path,
            file=contents,
            file_options={"content-type": mime_type, "x-upsert": "true"}
        )
        outfit_photo_url = supabase.storage.from_("clothing-images").get_public_url(file_path)

        # Step 3 — Send image to Gemini for analysis
        prompt = """You are a professional fashion stylist analyzing an outfit photo.

Identify every visible clothing item in this photo and categorize them.

Return ONLY a valid JSON object with no extra text, no markdown, no code blocks.
Use this exact format:
{
  "tops": [
    {"name": "White oversized t-shirt", "color": "white", "style": "casual"}
  ],
  "bottoms": [
    {"name": "Black slim fit jeans", "color": "black", "style": "casual"}
  ],
  "footwear": [
    {"name": "White Nike Air Force 1", "color": "white", "style": "sneakers"}
  ],
  "accessories": [
    {"name": "Silver chain necklace", "color": "silver", "style": "minimal"}
  ]
}

Rules:
- If a category has no items, return an empty array []
- Be specific about color, fit, and style for each item
- Only include items clearly visible in the photo
- Return JSON only, absolutely no extra text"""

        # ✅ FIXED — correct format for google-genai Python SDK
        ai_response = ai_client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[
                types.Part.from_bytes(
                    data=contents,
                    mime_type=mime_type
                ),
                prompt
            ]
        )

        # Step 4 — Parse Gemini response
        raw_text = ai_response.text.strip()

        # Clean markdown if Gemini adds it
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        detected_items = json.loads(raw_text)

        # Step 5 — Save each detected item to Supabase wardrobe
        saved_items = []
        category_map = {
            "tops": "Tops",
            "bottoms": "Bottoms",
            "footwear": "Footwear",
            "accessories": "Accessories"
        }

        for category_key, category_label in category_map.items():
            items_in_category = detected_items.get(category_key, [])
            for item in items_in_category:
                item_name = item.get("name", "Unknown item")
                color = item.get("color", "")
                style = item.get("style", "")

                # Full description as item name
                full_name = f"{item_name}"
                if color and color.lower() not in item_name.lower():
                    full_name = f"{color.capitalize()} {item_name}"

                item_data = {
                    "user_id": user_id,
                    "image_url": outfit_photo_url,
                    "filename": clean_name,
                    "item_name": full_name,
                    "category": category_label,
                    "style_tag": style,
                }

                supabase.table("clothing_items").insert(item_data).execute()
                saved_items.append({
                    "category": category_label,
                    "item_name": full_name
                })

        # Step 6 — Return summary
        return {
            "status": "success",
            "message": f"{len(saved_items)} items detected and saved to your wardrobe!",
            "outfit_photo_url": outfit_photo_url,
            "detected_items": detected_items,
            "saved_items": saved_items
        }

    except json.JSONDecodeError:
        print(f"Gemini returned invalid JSON: {raw_text}")
        return {"error": "AI could not analyze the photo. Please try a clearer image."}

    except Exception as e:
        print(f"Scan outfit error: {e}")
        return {"error": str(e)}


# ── GET WARDROBE ──────────────────────────────────────────────
@app.get("/wardrobe/{user_id}")
def get_wardrobe(user_id: str):
    try:
        response = supabase.table("clothing_items").select("*").eq("user_id", user_id).execute()
        return {"items": response.data}
    except Exception as e:
        print(f"Wardrobe error: {e}")
        return {"error": str(e)}


# ── GET WARDROBE BY CATEGORY ──────────────────────────────────
@app.get("/wardrobe/{user_id}/category/{category}")
def get_wardrobe_by_category(user_id: str, category: str):
    try:
        response = (
            supabase.table("clothing_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("category", category)
            .execute()
        )
        return {"category": category, "items": response.data}
    except Exception as e:
        print(f"Wardrobe category error: {e}")
        return {"error": str(e)}


# ── OUTFITS REQUEST BODY ──────────────────────────────────────
class OutfitRequest(BaseModel):
    user_id: str
    preferences: Optional[dict] = None


# ── GENERATE OUTFITS ──────────────────────────────────────────
@app.post("/outfits")
async def generate_outfits(request: OutfitRequest):
    try:
        # Step 1 — fetch wardrobe from Supabase
        response = supabase.table("clothing_items").select("*").eq("user_id", request.user_id).execute()

        if not response.data or len(response.data) == 0:
            return {
                "status": "empty",
                "message": "Wardrobe is empty. Scan some clothes first!"
            }

        # Step 2 — build items list
        items = response.data
        item_descriptions = [
            f"{item.get('item_name') or item.get('filename', 'item')} ({item.get('category', 'General')})"
            for item in items
        ]
        image_map = {
            item.get('item_name') or item.get('filename', ''): item.get('image_url', '')
            for item in items
        }
        wardrobe_str = "\n".join(item_descriptions)

        # Step 3 — build prompt with preferences
        prefs = request.preferences or {}
        styles = prefs.get('styles', [])
        colors = prefs.get('colors', [])
        occasions = prefs.get('occasions', [])
        body_type = prefs.get('bodyType', [])

        prompt = f"""You are a professional fashion stylist.

The user's style profile:
- Preferred styles: {', '.join(styles) if styles else 'not specified'}
- Favorite colors: {', '.join(colors) if colors else 'not specified'}
- Body type: {', '.join(body_type) if body_type else 'not specified'}
- Occasions they dress for: {', '.join(occasions) if occasions else 'not specified'}

Their wardrobe contains:
{wardrobe_str}

Suggest 5 outfit combinations using ONLY items from their wardrobe.
Return ONLY a valid JSON array with no extra text, no markdown, no code blocks.
Use this exact format:
[
  {{
    "outfit_name": "Casual Friday",
    "items": ["item1", "item2", "item3"],
    "styling_tip": "A short styling tip",
    "occasion": "Casual"
  }}
]"""

        # Step 4 — call Gemini
        ai_response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )

        raw_text = ai_response.text.strip()

        # Step 5 — clean response
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        outfits = json.loads(raw_text)

        return {"status": "success", "outfits": outfits, "image_map": image_map}

    except json.JSONDecodeError:
        print(f"Gemini returned invalid JSON: {raw_text}")
        return {"error": "AI returned an invalid response. Please try again."}

    except Exception as e:
        print(f"Outfit generation error: {e}")
        return {"error": str(e)}


# ── DELETE WARDROBE ITEM ──────────────────────────────────────
@app.delete("/wardrobe/{user_id}/{item_id}")
async def delete_item(user_id: str, item_id: str):
    try:
        supabase.table("clothing_items").delete().eq("id", item_id).eq("user_id", user_id).execute()
        return {"status": "success", "message": "Item deleted"}
    except Exception as e:
        print(f"Delete error: {e}")
        return {"error": str(e)}


# ── RUN SERVER ────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
