from langchain.tools import tool
from app.config.database import qdrant, gerar_embedding, COLLECTION_FAQ, garantir_colecoes_qdrant


@tool
def faq_retriever(question: str) -> str:
    """Busca no FAQ oficial os trechos mais relevantes para responder a pergunta."""
    garantir_colecoes_qdrant()
    vetor = gerar_embedding(question)

    resultados = qdrant.query_points(
        collection_name=COLLECTION_FAQ,
        query=vetor,
        limit=6,
    )

    if not resultados.points:
        return "Nenhum trecho relevante encontrado no FAQ."

    trechos = [
        ponto.payload.get("page_content", "")
        for ponto in resultados.points
        if ponto.payload and ponto.payload.get("page_content")
    ]
    return "\n\n".join(trechos) if trechos else "Nenhum trecho relevante encontrado no FAQ."
