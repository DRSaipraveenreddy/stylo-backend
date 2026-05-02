from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
import os
import re
import uuid
import json
import io
import httpx

load_dotenv()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={'api_version': 'v1'}
)


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

        supabase.storage.from_("clothing-images").upload(
            path=file_path,
            file=contents,
            file_options={"content-type": file.content_type, "x-upsert": "true"}
        )

        public_url = supabase.storage.from_("clothing-images").get_public_url(file_path)

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


# ── HELPER: Crop item from image using bounding box ───────────
def crop_item_from_image(image_bytes: bytes, bbox: list, padding: int = 20) -> bytes:
    """
    Crops a clothing item from the full image using bounding box.
    bbox format: [x, y, width, height] as percentages (0-100)
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img_width, img_height = img.size

        x_pct, y_pct, w_pct, h_pct = bbox

        x1 = int((x_pct / 100) * img_width) - padding
        y1 = int((y_pct / 100) * img_height) - padding
        x2 = int(((x_pct + w_pct) / 100) * img_width) + padding
        y2 = int(((y_pct + h_pct) / 100) * img_height) + padding

        # ✅ If item takes more than 60% height (dress/jumpsuit), use full height
        if h_pct > 60:
            y1 = 0
            y2 = img_height

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(img_width, x2)
        y2 = min(img_height, y2)

        cropped = img.crop((x1, y1, x2, y2))
        output = io.BytesIO()
        cropped.save(output, format="PNG")
        return output.getvalue()

    except Exception as e:
        print(f"Crop error: {e}")
        return image_bytes


# ── HELPER: Find best matching image URL ──────────────────────
def find_image_url(item_name: str, image_map: dict) -> str | None:
    """
    Finds the best matching image URL for an item name.
    Tries exact match first, then partial match as fallback.
    """
    item_lower = item_name.lower().strip()

    # 1. Exact match
    for key, url in image_map.items():
        if key.lower().strip() == item_lower:
            return url

    # 2. One contains the other
    for key, url in image_map.items():
        key_lower = key.lower().strip()
        if item_lower in key_lower or key_lower in item_lower:
            return url

    # 3. Word overlap (at least 2 words match)
    item_words = set(item_lower.split())
    best_match_url = None
    best_match_count = 0
    for key, url in image_map.items():
        key_words = set(key.lower().split())
        overlap = len(item_words & key_words)
        if overlap >= 2 and overlap > best_match_count:
            best_match_count = overlap
            best_match_url = url

    return best_match_url


# ── HELPER: Build outfit collage from image URLs ──────────────
async def build_outfit_collage(image_urls: list, outfit_name: str) -> bytes | None:
    """
    Downloads wardrobe item images and stitches them into
    a clean outfit collage card using Pillow.
    """
    try:
        ITEM_SIZE = 280
        PADDING = 20
        HEADER_HEIGHT = 60

        images = []

        async with httpx.AsyncClient() as client:
            for url in image_urls:
                try:
                    resp = await client.get(url, timeout=10)
                    if resp.status_code == 200:
                        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                        img.thumbnail((ITEM_SIZE, ITEM_SIZE), Image.LANCZOS)
                        images.append(img)
                except Exception as e:
                    print(f"Image download error: {e}")

        if not images:
            print("No images downloaded for collage")
            return None

        num_items = len(images)
        cols = min(num_items, 3)
        rows = (num_items + cols - 1) // cols

        canvas_width = cols * (ITEM_SIZE + PADDING) + PADDING
        canvas_height = HEADER_HEIGHT + rows * (ITEM_SIZE + PADDING) + PADDING

        canvas = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # Header
        draw.rectangle([0, 0, canvas_width, HEADER_HEIGHT], fill=(0, 0, 0, 255))
        try:
            font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        except:
            font_header = ImageFont.load_default()

        draw.text((PADDING, 18), outfit_name, fill=(255, 255, 255), font=font_header)

        # Place items
        for i, img in enumerate(images):
            col = i % cols
            row = i // cols

            x = PADDING + col * (ITEM_SIZE + PADDING)
            y = HEADER_HEIGHT + PADDING + row * (ITEM_SIZE + PADDING)

            draw.rounded_rectangle(
                [x - 4, y - 4, x + ITEM_SIZE + 4, y + ITEM_SIZE + 4],
                radius=12,
                fill=(245, 245, 245, 255)
            )

            img_w, img_h = img.size
            offset_x = x + (ITEM_SIZE - img_w) // 2
            offset_y = y + (ITEM_SIZE - img_h) // 2
            canvas.paste(img, (offset_x, offset_y), img)

        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="JPEG", quality=90)
        return output.getvalue()

    except Exception as e:
        print(f"Collage build error: {e}")
        return None


# ── SCAN OUTFIT PHOTO & AUTO-SORT INTO WARDROBE ───────────────
@app.post("/scan-outfit")
async def scan_outfit(
    file: UploadFile = File(...),
    user_id: str = Form(...),
):
    try:
        contents = await file.read()
        mime_type = file.content_type or "image/jpeg"
        clean_name = re.sub(r'[^a-zA-Z0-9.-]', '_', file.filename)

        # ✅ Updated prompt — now includes dresses category
        prompt = """You are a professional fashion stylist analyzing an outfit photo.

Detect every visible clothing item and return their exact locations as bounding boxes.

Return ONLY a valid JSON object. No markdown, no extra text.
Use this exact format:
{
  "tops": [
    {
      "name": "White oversized t-shirt",
      "color": "white",
      "style": "casual",
      "bbox": [10, 5, 80, 40]
    }
  ],
  "bottoms": [
    {
      "name": "Black slim fit jeans",
      "color": "black",
      "style": "casual",
      "bbox": [15, 45, 70, 50]
    }
  ],
  "dresses": [
    {
      "name": "Maroon halter neck mini dress",
      "color": "maroon",
      "style": "elegant",
      "bbox": [10, 5, 80, 90]
    }
  ],
  "footwear": [
    {
      "name": "White sneakers",
      "color": "white",
      "style": "casual",
      "bbox": [20, 85, 60, 12]
    }
  ],
  "accessories": [
    {
      "name": "Gold pendant necklace",
      "color": "gold",
      "style": "minimal",
      "bbox": [35, 10, 30, 15]
    }
  ]
}

bbox format: [x, y, width, height] as PERCENTAGES of image size (0 to 100).
x, y = top-left corner of the item
width, height = size of the item

Rules:
- If an item is a full one-piece garment (dress, jumpsuit, romper), put it in "dresses" NOT tops or bottoms
- If a category has no items, return empty array []
- Return accurate bounding boxes that tightly fit each clothing item
- Return JSON only, no extra text"""

        ai_response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(
                    data=contents,
                    mime_type=mime_type
                ),
                prompt
            ]
        )

        raw_text = ai_response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        detected_items = json.loads(raw_text)

        saved_items = []

        # ✅ Updated category map — now includes Dresses
        category_map = {
            "tops": "Tops",
            "bottoms": "Bottoms",
            "dresses": "Dresses",
            "footwear": "Footwear",
            "accessories": "Accessories"
        }

        for category_key, category_label in category_map.items():
            items_in_category = detected_items.get(category_key, [])
            for item in items_in_category:
                item_name = item.get("name", "Unknown item")
                color = item.get("color", "")
                style = item.get("style", "")
                bbox = item.get("bbox", None)

                full_name = item_name
                if color and color.lower() not in item_name.lower():
                    full_name = f"{color.capitalize()} {item_name}"

                # ✅ Smart crop logic per category
                if category_label == "Accessories":
                    # Accessories are too small — bbox always wrong, use full image
                    item_image_bytes = contents
                    image_ext = "jpg"
                    content_type = "image/jpeg"
                elif bbox and len(bbox) == 4:
                    # Crop using bbox for all other categories
                    item_image_bytes = crop_item_from_image(contents, bbox)
                    image_ext = "png"
                    content_type = "image/png"
                else:
                    # No bbox fallback
                    item_image_bytes = contents
                    image_ext = "jpg"
                    content_type = "image/jpeg"

                item_file_path = f"{user_id}/{category_label.lower()}/{uuid.uuid4()}.{image_ext}"
                supabase.storage.from_("clothing-images").upload(
                    path=item_file_path,
                    file=item_image_bytes,
                    file_options={"content-type": content_type, "x-upsert": "true"}
                )
                item_image_url = supabase.storage.from_("clothing-images").get_public_url(item_file_path)

                item_data = {
                    "user_id": user_id,
                    "image_url": item_image_url,
                    "filename": clean_name,
                    "item_name": full_name,
                    "category": category_label,
                    "style_tag": style,
                }
                supabase.table("clothing_items").insert(item_data).execute()
                saved_items.append({
                    "category": category_label,
                    "item_name": full_name,
                    "image_url": item_image_url
                })

        return {
            "status": "success",
            "message": f"{len(saved_items)} items detected, cropped and saved!",
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


# ── GENERATE OUTFITS WITH COLLAGE ─────────────────────────────
@app.post("/outfits")
async def generate_outfits(request: OutfitRequest):
    try:
        response = supabase.table("clothing_items").select("*").eq("user_id", request.user_id).execute()

        if not response.data or len(response.data) == 0:
            return {
                "status": "empty",
                "message": "Wardrobe is empty. Scan some clothes first!"
            }

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

Their wardrobe contains ONLY these items (use EXACT names as listed):
{wardrobe_str}

CRITICAL RULES:
1. Use ONLY items listed above — do NOT invent or add items not in the list
2. Use the EXACT item name as written above — do not rename, shorten or modify
3. Each outfit must have at least a top and bottom OR a dress
4. Suggest 5 outfit combinations

Return ONLY a valid JSON array with no extra text, no markdown, no code blocks.
Use this exact format:
[
  {{
    "outfit_name": "Casual Friday",
    "items": ["exact item name 1", "exact item name 2"],
    "styling_tip": "A short styling tip",
    "occasion": "Casual"
  }}
]"""

        ai_response = ai_client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt
        )

        raw_text = ai_response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        outfits = json.loads(raw_text)

        # Build collage for each outfit
        for outfit in outfits:
            outfit_items = outfit.get("items", [])
            outfit_name = outfit.get("outfit_name", "Outfit")

            item_image_urls = []
            for item_name in outfit_items:
                url = find_image_url(item_name, image_map)
                if url and url not in item_image_urls:
                    item_image_urls.append(url)

            print(f"Outfit: {outfit_name} | items: {outfit_items} | urls found: {len(item_image_urls)}")

            if item_image_urls:
                collage_bytes = await build_outfit_collage(item_image_urls, outfit_name)
                if collage_bytes:
                    collage_path = f"{request.user_id}/collages/{uuid.uuid4()}.jpg"
                    supabase.storage.from_("clothing-images").upload(
                        path=collage_path,
                        file=collage_bytes,
                        file_options={"content-type": "image/jpeg", "x-upsert": "true"}
                    )
                    collage_url = supabase.storage.from_("clothing-images").get_public_url(collage_path)
                    outfit["collage_url"] = collage_url
                else:
                    outfit["collage_url"] = None
            else:
                outfit["collage_url"] = None

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