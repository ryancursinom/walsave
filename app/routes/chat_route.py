from fastapi import APIRouter
from app.schemas import ChatRequest, ChatResponse
from app.graph import executar_fluxo

router = APIRouter(tags=["chat"])

@router.post("/chat")
def conversar(requisicao: ChatRequest) -> ChatResponse:
    
    resposta = executar_fluxo(
        requisicao.pergunta,
        requisicao.session_id,
        requisicao.user_id
    )

    return ChatResponse(
        resposta=resposta[0],
        agentes_chamados=resposta[1],
    )