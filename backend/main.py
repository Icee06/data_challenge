from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="CQA Hackathon Backend API")

# Enable CORS for Vanilla JS frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for cached dataset
dialogues_db = {}

ORGANIZER_API_URL = "https://api.hackathon-endpoint.com"  # Replace with actual organizer URL

@app.get("/api/fetch-dataset/{group_id}")
async def fetch_dataset(group_id: str):
    """Fetch raw dataset from organizers and store locally in memory."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ORGANIZER_API_URL}/dataset/{group_id}")
            response.raise_for_status()
            raw_data = response.json()

        # Parse and index dialogues by ID
        global dialogues_db
        dialogues_db = {item["id"]: item for item in raw_data.get("dialogues", [])}
        return {"status": "success", "count": len(dialogues_db)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dialogues")
async def list_dialogues(language: str = None, type: str = None):
    """List all dialogues with optional language (RU/KZ/EN) and type (chat/call) filtering."""
    results = list(dialogues_db.values())
    if language:
        results = [d for d in results if d.get("language") == language]
    if type:
        results = [d for d in results if d.get("type") == type]
    return {"dialogues": results}

@app.get("/api/dialogues/{dialogue_id}")
async def get_dialogue(dialogue_id: str):
    """Get single dialogue details and transcript."""
    if dialogue_id not in dialogues_db:
        raise HTTPException(status_code=404, detail="Dialogue not found")
    return dialogues_db[dialogue_id]