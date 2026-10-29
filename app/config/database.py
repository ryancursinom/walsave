import inspect
import threading
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
CURRENT_SOURCE: ContextVar[str] = ContextVar("current_source", default="chat")


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


def _chamada_via_mcp() -> bool:
    if CURRENT_SOURCE.get() == "mcp":
        return True

    for frame in inspect.stack():
        arquivo = frame.filename.replace("\\", "/").lower()
        if arquivo.endswith("/mcp_server.py"):
            return True
        modulo = frame.frame.f_globals.get("__name__", "")
        if modulo == "app.mcp_server":
            return True

    return False


IDLE_TIMEOUT_TURNO = "120s"
ESPERA_CONEXAO_S = 60

class _ConexaoDoTurno:
    """O que a tool enxerga como conexão dentro de um turno."""

    def __init__(self, turno: "TurnoTransacional", savepoint: str):
        self._turno = turno
        self._savepoint = savepoint
        self._aberto = True
        self._executar(f"SAVEPOINT {savepoint}")

    def _executar(self, sql: str) -> None:
        cur = self._turno.conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def _desfazer(self) -> None:
        self._executar(f"ROLLBACK TO SAVEPOINT {self._savepoint}")
        self._executar(f"RELEASE SAVEPOINT {self._savepoint}")

    def cursor(self, *args, **kwargs):
        return self._turno.conn.cursor(*args, **kwargs)

    def commit(self) -> None:
        if not self._aberto:
            return
        self._executar(f"RELEASE SAVEPOINT {self._savepoint}")
        self._aberto = False
        self._turno.escritas += 1

    def rollback(self) -> None:
        if not self._aberto:
            return
        self._aberto = False
        self._turno.falhas += 1
        self._desfazer()

    def close(self) -> None:
        try:
            # Tool que sai sem commit: mesmo efeito do close() de uma conexão própria.
            if self._aberto:
                self._aberto = False
                self._desfazer()
        except Exception:
            self._turno.falhas += 1
        finally:
            if self._turno is not None:
                turno, self._turno = self._turno, None
                turno._lock.release()

    def __getattr__(self, nome):
        return getattr(self._turno.conn, nome)


class TurnoTransacional:
    """Uma conexão por turno, aberta só quando alguma tool pede o Postgres."""

    def __init__(self):
        self.conn = None
        self.escritas = 0
        self.falhas = 0
        self._seq = 0
        self._finalizado = False
        # Tools paralelas do mesmo turno usam a conexão uma de cada vez.
        self._lock = threading.RLock()

    def conexao(self) -> _ConexaoDoTurno:
        if self._finalizado:
            raise RuntimeError("transação do turno já foi finalizada")
        if not self._lock.acquire(timeout=ESPERA_CONEXAO_S):
            raise RuntimeError("conexão do turno ocupada por outra tool")
        try:
            if self.conn is None:
                self.conn = psycopg2.connect(SQL_URL)
                cur = self.conn.cursor()
                try:
                    # Se o processo travar com a transação aberta, o Postgres a encerra.
                    cur.execute(f"SET idle_in_transaction_session_timeout = '{IDLE_TIMEOUT_TURNO}'")
                finally:
                    cur.close()
            self._seq += 1
            return _ConexaoDoTurno(self, f"tool_{self._seq}")
        except Exception:
            self._lock.release()
            raise

    def finalizar(self, confirmar: bool) -> bool:
        """Confirma ou desfaz o turno inteiro. Devolve True se a escrita ficou."""
        if self._finalizado:
            return False
        self._finalizado = True
        if self.conn is None:
            return False
        try:
            if confirmar and self.falhas == 0:
                try:
                    self.conn.commit()
                    return True
                except Exception as exc:
                    print(f"[transacao] commit falhou: {exc}")
            self.conn.rollback()
            return False
        except Exception as exc:
            print(f"[transacao] rollback falhou: {exc}")
            return False
        finally:
            try:
                self.conn.close()
            except Exception:
                pass


CURRENT_TURNO: ContextVar[TurnoTransacional | None] = ContextVar("current_turno", default=None)


def get_conn():
    if not _chamada_via_mcp() and not CURRENT_USER_ID.get():
        raise PermissionError("operação financeira recusada: user_id ausente")

    turno = CURRENT_TURNO.get()
    if turno is not None:
        return turno.conexao()

    return psycopg2.connect(SQL_URL)
