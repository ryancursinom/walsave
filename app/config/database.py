import psycopg2
from contextvars import ContextVar
from pymongo import MongoClient
from qdrant_client import QdrantClient, models
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.config.config import MONGODB_URI, SQL_URL, QDRANT_URL, QDRANT_API_KEY, GEMINI_API_KEY

_mongo = MongoClient(MONGODB_URI)
db = _mongo["assessor"]
col_sessoes = db["sessoes"]
col_perfis = db["perfis"]

col_sessoes.create_index("session_id")
col_sessoes.create_index("iniciada_em")
col_perfis.create_index("user_id", unique=True)

qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

COLLECTION_MEMORIA = "memoria_resumos"
COLLECTION_FAQ = "faq_chunks"
COLLECTION_PERFIL_RESTRICOES = "perfil_restricoes"
EMBEDDING_DIM = 768
CURRENT_USER_ID: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def _garantir_indice_tenant(nome: str) -> None:
    info = qdrant.get_collection(nome)
    if "user_id" in (getattr(info, "payload_schema", {}) or {}):
        return

    qdrant.create_payload_index(
        collection_name=nome,
        field_name="user_id",
        field_schema=models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD,
            is_tenant=True,
        ),
    )


def garantir_colecoes_qdrant() -> None:
    """Cria collections e índices necessários quando ainda não existem."""
    for nome in (COLLECTION_MEMORIA, COLLECTION_FAQ, COLLECTION_PERFIL_RESTRICOES):
        if not qdrant.collection_exists(nome):
            qdrant.create_collection(
                collection_name=nome,
                vectors_config=models.VectorParams(
                    size=EMBEDDING_DIM,
                    distance=models.Distance.COSINE,
                ),
            )

    # Só o perfil usa user_id como tenant; manter as collections existentes intactas.
    _garantir_indice_tenant(COLLECTION_PERFIL_RESTRICOES)


_embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-2-preview",
    google_api_key=GEMINI_API_KEY,
)


def gerar_embedding(texto: str) -> list[float]:
    return _embeddings.embed_query(texto, output_dimensionality=EMBEDDING_DIM)


def gerar_embeddings_batch(textos: list[str]) -> list[list[float]]:
    return _embeddings.embed_documents(textos, output_dimensionality=EMBEDDING_DIM)


def get_conn():
    if not CURRENT_USER_ID.get():
        raise PermissionError("operação financeira recusada: user_id ausente")
    return psycopg2.connect(SQL_URL)
