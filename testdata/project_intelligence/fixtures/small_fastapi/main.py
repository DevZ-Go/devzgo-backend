from fastapi import FastAPI

app = FastAPI(title="Campus Notes")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/notes")
def list_notes():
    return [{"id": 1, "text": "Review chapter 3"}]


@app.post("/notes")
def create_note(payload: dict):
    return {"id": 2, "text": payload.get("text", "")}
