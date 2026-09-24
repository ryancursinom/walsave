from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from qdrant_client import models

from app.config.database import (
    COLLECTION_PERFIL_RESTRICOES,
    col_perfis,
    gerar_embedding,
    garantir_colecoes_qdrant,
    qdrant,
)


def _user_id(config: RunnableConfig) -> str | None:
    return ((config or {}).get("configurable", {}) or {}).get("user_id")


@tool
def consultar_perfil(busca: str, config: RunnableConfig) -> str:
    """Consulta o perfil financeiro do usuário e as restrições relevantes.

    Use antes de recomendações personalizadas que dependam de renda, gastos
    fixos, horizonte, perfil de investidor ou preferências/restrições livres.
    O usuário é obtido exclusivamente do contexto da requisição; nunca informe
    user_id como argumento.
    """
    user_id = _user_id(config)
    if not user_id:
        return "Não foi possível identificar o usuário para consultar o perfil."

    perfil = col_perfis.find_one({"user_id": user_id}, {"_id": 0})
    if not perfil:
        return "Não há perfil cadastrado. Oriente o usuário a preencher a tela de perfil; não invente dados."

    garantir_colecoes_qdrant()
    vetor = gerar_embedding(busca or "restrições e preferências financeiras")
    resultado = qdrant.query_points(
        collection_name=COLLECTION_PERFIL_RESTRICOES,
        query=vetor,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id),
                )
            ]
        ),
        limit=5,
    )
    restricoes = [
        ponto.payload.get("restricao", "")
        for ponto in resultado.points
        if ponto.payload.get("restricao")
    ]

    linhas = [
        f"Renda mensal: R$ {perfil['renda_mensal']:.2f}",
        f"Gasto fixo mensal: R$ {perfil['gasto_fixo_mensal']:.2f}",
        f"Horizonte: {perfil['horizonte_meses']} meses",
        f"Perfil de investidor: {perfil['perfil_investidor']}",
        "Restrições relevantes:",
    ]
    linhas.extend(f"- {texto}" for texto in restricoes)
    return "\n".join(linhas)


TOOLS_PERFIL = [consultar_perfil]
