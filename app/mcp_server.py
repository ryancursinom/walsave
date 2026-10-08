# Imports
import sys
from pathlib import Path
from typing import Any, Optional
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

# O Cursor executa este arquivo pelo caminho absoluto; o pacote `app`
# só é encontrado se a raiz do projeto estiver no sys.path *antes* dos imports.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.config.database import CURRENT_SOURCE
from app.tools.financial_tools import (
    # Tools
    add_transaction as _add_transaction,
    saldo_diario as _daily_balance,
    search_transactions as _query_transactions,
    saldo_total as _total_balance,
    update_transaction as _update_transaction
)

# Este processo só existe para o Cursor; o chat FastAPI nunca importa este arquivo.
CURRENT_SOURCE.set("mcp")

# Define o servidor MCP
mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions="Ferramentas financeiras do Assessor.AI...",
)

# Define as anotações de ferramentas
LEITURA = ToolAnnotations(readOnlyHint=True)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

# MCP Tool para consultar saldo total
@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Saldo de todo o histórico: receitas menos despesas.",
    annotations=LEITURA,
)
def total_balance() -> dict[str, Any]:
    return _total_balance.invoke({})


# MCP Tool para consultar saldo do dia
@mcp.tool(
    name="daily_balance",
    title="Saldo diário",
    description="Saldo do dia informado (YYYY-MM-DD, America/Sao_Paulo): receitas menos despesas. Transferências não entram no cálculo.",
    annotations=LEITURA,
)
def daily_balance(
    date_local: str = Field(..., description="Data local no formato YYYY-MM-DD (America/Sao_Paulo)."),
) -> dict[str, Any]:
    return _daily_balance.invoke({"date_local": date_local})


# MCP Tool para consultar transações
@mcp.tool(
    name="query_transactions",
    title="Consultar transações",
    description="Lista transações com filtros opcionais (valor, tipo, categoria, descrição, pagamento, datas e texto original).",
    annotations=LEITURA,
)
def query_transactions(
    min_amount: Optional[float] = Field(
        default=None,
        description="Valor mínimo. Em busca por valor exato, use o mesmo valor em max_amount.",
    ),
    max_amount: Optional[float] = Field(
        default=None,
        description="Valor máximo. Em busca por valor exato, use o mesmo valor em min_amount.",
    ),
    type_name: Optional[str] = Field(
        default=None,
        description="Tipo: INCOME | EXPENSES | TRANSFER (aceita aliases em PT-BR).",
    ),
    category_name: Optional[str] = Field(
        default=None,
        description="Categoria: comida, besteira, estudo, transporte, saude, lazer, contas, investimento, presente, venda, eletronicos, outros.",
    ),
    description: Optional[str] = Field(default=None, description="Filtro parcial na descrição."),
    payment_method: Optional[str] = Field(default=None, description="Filtro da forma de pagamento."),
    min_date: Optional[str] = Field(
        default=None,
        description="Início do intervalo (YYYY-MM-DD). Em data única, use o mesmo valor em max_date.",
    ),
    max_date: Optional[str] = Field(
        default=None,
        description="Fim do intervalo (YYYY-MM-DD). Em data única, use o mesmo valor em min_date.",
    ),
    source_text: Optional[str] = Field(default=None, description="Filtro parcial no texto original do usuário."),
) -> dict[str, Any]:
    return _query_transactions.invoke({
        "min_amount": min_amount,
        "max_amount": max_amount,
        "type_name": type_name,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
        "min_date": min_date,
        "max_date": max_date,
        "source_text": source_text,
    })


# MCP Tool para atualizar uma transação
@mcp.tool(
    name="update_transaction",
    title="Atualizar transação",
    description="Atualiza uma transação por id ou pela combinação match_text + date_local. Informe ao menos um campo a alterar.",
    annotations=ESCRITA,
)
def update_transaction(
    id: Optional[int] = Field(default=None, description="ID da transação. Se ausente, use match_text e date_local."),
    match_text: Optional[str] = Field(
        default=None,
        description="Texto para localizar a transação em source_text/description quando id não for informado.",
    ),
    date_local: Optional[str] = Field(
        default=None,
        description="Data local YYYY-MM-DD (America/Sao_Paulo), usada com match_text quando id ausente.",
    ),
    amount: Optional[float] = Field(default=None, description="Novo valor (sempre positivo)."),
    type_name: Optional[str] = Field(default=None, description="Novo tipo: INCOME | EXPENSES | TRANSFER."),
    category_name: Optional[str] = Field(default=None, description="Nova categoria (nome)."),
    description: Optional[str] = Field(default=None, description="Nova descrição."),
    payment_method: Optional[str] = Field(default=None, description="Novo meio de pagamento."),
    occurred_at: Optional[str] = Field(default=None, description="Novo timestamp ISO 8601."),
) -> dict[str, Any]:
    return _update_transaction.invoke({
        "id": id,
        "match_text": match_text,
        "date_local": date_local,
        "amount": amount,
        "type_name": type_name,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
        "occurred_at": occurred_at,
    })


# MCP Tool para registrar uma transação
@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description="Registra uma transação na tabela transactions.",
    annotations=ESCRITA,
)
def add_transaction(
    amount: float = Field(..., description="Valor da transação (sempre positivo)."),
    source_text: str = Field(..., description="Texto original do usuário."),
    occurred_at: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601; se ausente, usa NOW() no banco."
    ),
    type_name: Optional[str] = Field(default=None, description="""
                                     Nome do tipo: INCOME | EXPENSES | TRANSFER. Caso o usuário não informe o produto ou a situação em que ele realizou um gasto, entra como TRANSFER. Exemplo: 'Fiz um PIX para minha mãe de 8 reais', ele apenas realizou uma transferência para a mãe, não se sabe se foi um gasto ou não.
                                     """),
    category_name: Optional[str] = Field(default=None, description="""
                                         Nome da categoria que irá ser adicionada como categoria da transação (categoria não é tipo nesse caso).

                                        ## Possíveis categorias
                                        - comida → alimentação essencial (refeições básicas do dia a dia, mercado, marmita, etc.)
                                        - besteira → alimentação não essencial (doces, fast food, salgados, bebidas não necessárias, etc.)
                                        - estudo → gastos com educação (cursos, mensalidades, livros, materiais escolares/acadêmicos)
                                        - transporte → locomoção (uber, táxi, ônibus, metrô, combustível, passagens, manutenção básica de transporte)
                                        - saúde → despesas médicas (consultas, exames, remédios, tratamentos, emergências)
                                        - lazer → gastos não essenciais pessoais (roupas, rolês, viagens, entretenimento, hobbies)
                                        - contas → despesas fixas/essenciais (aluguel, luz, água, internet, condomínio, etc.)
                                        - investimento → aplicação de dinheiro ou aquisição de bens com objetivo de valorização (renda fixa/variável, imóveis, reformas, etc.)
                                        - presente → transações sem finalidade financeira direta para outra pessoa (dar ou receber presentes)
                                        - venda → entrada de dinheiro por venda de bens ou serviços
                                        - eletronicos → compra de eletrônicos (celular, fone, computador, videogame, etc.)
                                        - outros → qualquer transação que não se encaixa nas categorias acima
                                         """),
    description: Optional[str] = Field(default=None, description="Descrição (opcional)."),
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento (opcional)."),
) -> dict[str, Any]:
    return _add_transaction.invoke({
        "amount": amount,
        "source_text": source_text,
        "type_name": type_name,
        "category_name": category_name,
        "occurred_at": occurred_at,
        "description": description,
        "payment_method": payment_method,
    })

# Inicializados do MCP
if __name__ == "__main__":
    from app.config.config import SQL_URL

    if not SQL_URL:
        print("[assessor-financeiro] SQL_URL ausente no .env", file=sys.stderr)

    mcp.run(transport="stdio")
