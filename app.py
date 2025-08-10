from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "✅ Agente autónomo funcionando en Render"}

@app.get("/ping")
def ping():
    return {"message": "pong"}
