"""Persistência do perfil financeiro estruturado e das restrições semânticas."""
from datetime import datetime, timezone
from uuid import UUID, uuid5

from qdrant_client import models

from app.config.database import (
    COLLECTION_PERFIL_RESTRICOES,
    col_perfis,
    garantir_colecoes_qdrant,
    gerar_embeddings_batch,
    qdrant,
)
from app.schemas import ProfileRequest, ProfileResponse

# Namespace estável: o mesmo usuário e índice geram sempre o mesmo ponto.
_RESTRICAO_NAMESPACE = UUID("8bb3c1fd-cb8e-4f36-8b3d-5cf3b31d6ad1")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _id_restricao(user_id: str, indice: int) -> str:
    return str(uuid5(_RESTRICAO_NAMESPACE, f"{user_id}:{indice}"))


def create(request: ProfileRequest) -> ProfileResponse:
    """Substitui o perfil inteiro no MongoDB e as restrições no Qdrant."""
    user_id = request.user_id
    agora = _agora()

    garantir_colecoes_qdrant()
    vetores = gerar_embeddings_batch(request.restricoes)

    # A lista inteira é substituída: nenhum ponto antigo desse tenant sobrevive.
    qdrant.delete(
        collection_name=COLLECTION_PERFIL_RESTRICOES,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id),
                    )
                ]
            )
        ),
    )

    qdrant.upsert(
        collection_name=COLLECTION_PERFIL_RESTRICOES,
        points=[
            models.PointStruct(
                id=_id_restricao(user_id, indice),
                vector=vetor,
                payload={
                    "user_id": user_id,
                    "restricao": restricao,
                    "indice": indice,
                },
            )
            for indice, (restricao, vetor) in enumerate(zip(request.restricoes, vetores))
        ],
    )

    documento = {
        "user_id": user_id,
        "renda_mensal": request.renda_mensal,
        "gasto_fixo_mensal": request.gasto_fixo_mensal,
        "horizonte_meses": request.horizonte_meses,
        "perfil_investidor": request.perfil_investidor,
        "restricoes": request.restricoes,
        "atualizado_em": agora,
    }
    col_perfis.update_one(
        {"user_id": user_id},
        {"$set": documento, "$setOnInsert": {"criado_em": agora}},
        upsert=True,
    )

    return ProfileResponse(
        user_id=user_id,
        renda_mensal=request.renda_mensal,
        gasto_fixo_mensal=request.gasto_fixo_mensal,
        horizonte_meses=request.horizonte_meses,
        perfil_investidor=request.perfil_investidor,
        restricoes=request.restricoes,
        atualizado_em=agora,
    )
