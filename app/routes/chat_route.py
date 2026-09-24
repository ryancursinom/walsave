from fastapi import APIRouter, HTTPException
from app.schemas import ChatRequest, ChatResponse
from app.graph import executar_fluxo

router = APIRouter(tags=["chat"])


@router.post("/chat")
def conversar(requisicao: ChatRequest) -> ChatResponse:
    if not requisicao.user_id or not requisicao.user_id.strip():
        raise HTTPException(
            status_code=400,
            detail="user_id é obrigatório para enviar mensagens",
        )

    resposta = executar_fluxo(
        requisicao.pergunta,
        requisicao.session_id,
        requisicao.user_id,
    )

    return ChatResponse(
        resposta=resposta[0],
        agentes_chamados=resposta[1],
    )
