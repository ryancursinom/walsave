from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""

    user_id:    str = Field(
        default="usuario_teste",
        description="Identificador único do usuário. É o que permite a memória de longo prazo funcionar",
        examples=["usuario_teste"])
    
    session_id: str = Field(
        ...,
        description="Identificador da sessão do usuário. Pode ser um UUID ou outro identificador único.",
        examples=["2nnugu14bu343gb3"])
    
    pergunta:   str = Field(
        ..., 
        min_length=1,
        description="Pergunta ou mensagem do usuário para o assistente.", 
        examples=["gastei 50 reais no mercado"])


class ChatResponse(BaseModel):
    """O que a API devolve no POST /chat."""
    resposta:         str
    agentes_chamados: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """Ainda não é usado — é do Passo 6 da Etapa 3."""
    session_id: str
    resumo:     str | None = None
