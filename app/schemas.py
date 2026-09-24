from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Annotated, Literal

Restricao = Annotated[
    str,
    Field(
        min_length=1,
        description="Uma restrição ou preferência do usuário em linguagem livre.",
    ),
]


class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""

    user_id:    str = Field(
        ...,
        min_length=1,
        description="Identificador único do usuário. É o que permite a memória de longo prazo funcionar",
        examples=["usuario-123"])
    
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


class ProfileRequest(BaseModel):
    """O que o navegador envia no POST do perfil financeiro."""

    user_id: str = Field(
        ...,
        min_length=1,
        description="Identificador estável do usuário, o mesmo utilizado no chat.",
        examples=["usuario_teste"],
    )

    renda_mensal: float = Field(
        ...,
        gt=0,
        description="Renda mensal do usuário. Deve ser maior que zero.",
        examples=[6000],
    )

    gasto_fixo_mensal: float = Field(
        ...,
        ge=0,
        description=(
            "Total de gastos fixos mensais. Deve ser maior ou igual a zero "
            "e menor que a renda mensal."
        ),
        examples=[3800],
    )

    horizonte_meses: int = Field(
        ...,
        ge=1,
        le=120,
        description="Horizonte de planejamento em meses.",
        examples=[18],
    )

    perfil_investidor: Literal[
        "conservador",
        "moderado",
        "arrojado",
    ] = Field(
        ...,
        description="Perfil de risco do investidor.",
        examples=["conservador"],
    )

    restricoes: list[Restricao] = Field(
        ...,
        min_length=1,
        max_length=5,
        description=(
            "Lista de 1 a 5 restrições independentes em linguagem livre, "
            "utilizadas posteriormente em busca semântica."
        ),
        examples=[
            [
                "não quero nada muito arriscado, prefiro dormir tranquilo",
                "preciso deixar uma reserva para consertar o carro",
                "quero trocar de apartamento no meio do ano que vem",
            ]
        ],
    )

    @model_validator(mode="after")
    def validar_gastos_menores_que_renda(self):
        if self.gasto_fixo_mensal >= self.renda_mensal:
            raise ValueError(
                "gasto_fixo_mensal deve ser menor que renda_mensal"
            )

        return self

    @field_validator("restricoes")
    @classmethod
    def validar_restricoes_nao_vazias(cls, valores):
        valores_limpos = [valor.strip() for valor in valores]
        if any(not valor for valor in valores_limpos):
            raise ValueError("cada restrição deve conter texto não vazio")
        return valores_limpos


class ProfileResponse(BaseModel):
    """Perfil devolvido após o salvamento confirmado."""

    user_id: str
    renda_mensal: float
    gasto_fixo_mensal: float
    horizonte_meses: int
    perfil_investidor: Literal["conservador", "moderado", "arrojado"]
    restricoes: list[Restricao]
    atualizado_em: datetime | None = None


class SessionResponse(BaseModel):
    """Ainda não é usado — é do Passo 6 da Etapa 3."""
    session_id: str
    resumo:     str | None = None
