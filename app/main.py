from fastapi import FastAPI
from app.routes.chat_route import router as chat_router
from app.routes.sessions import router as sessions_router
from app.routes.profile_route import router as profile_router
from app.config.config import validar_config
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config.config import FRONTEND_DIR

app = FastAPI(
    title="WalSave - Assessor IA",
    description="Aplicativo de assessoria financeira e agenda com IA",
    version="0.1.0",
)

# Configurações de Segurança
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(profile_router)

# Conexão com o frontend
if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/", tags=["infra"])
    def raiz() -> dict:
        return {
            "mensagem": "API já iniciada. Frontend não encontrado. Acesse /docs para a documentação da API.",
        }
