from fastapi import FastAPI

app = FastAPI(
    title="WalSave - Assessor IA",
    description="Aplicativo de assessoria financeira e agenda com IA",
    version="0.1.0",
)

@app.get("/health")
def health() -> dict:
    """Endpoint de verificação de saúde da aplicação."""
    return {"status": "ok"}