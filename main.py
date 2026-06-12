# Imports
import os
import operator
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver # -> Substitui a função que tínhamos criado para histórico com sessões, porque faz isso automaticamente
from langchain_core.messages import RemoveMessage
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, END
from prompts.prompts import PROMPTS
from tools.pg_tools import TOOLS
from tools.faq_tools import faq_retriever


# Carregando variáveis de ambiente
load_dotenv()

# Instância modelo do Gemini (gemini-2.5-flash)
llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    google_api_key=os.getenv("GEMINI_API_KEY")
)

llm_groq = ChatGroq(
   model="openai/gpt-oss-120b",
   temperature=0.7,
   api_key=os.getenv("GROQ_API_KEY")
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])

llm_rapido = ChatGroq(
   model="llama-3.3-70b-versatile",
   temperature=0.0,
   api_key=os.getenv("GROQ_API_KEY")
)

# =================================================================================
# CRIAÇÃO DO AGENTE DE IA, PASSANDO PARÂMETROS DE CONFIG DE FORMA BEM MAIS SIMPLES
# =================================================================================
router_memory = MemorySaver()

router_app = create_agent(
   model=llm_rapido,
   system_prompt=PROMPTS["router"],
   checkpointer=router_memory
)

financeiro_app = create_agent(
   model=llm_especialista,
   system_prompt=PROMPTS["financial"],
   tools=TOOLS
)

agenda_app = create_agent(
   model=llm_especialista,
   system_prompt=PROMPTS["schedule"]
)

faq_app = create_agent(
    model=llm_rapido,
    system_prompt=PROMPTS["faq"],
    tools=[faq_retriever]
)

orquestrador_app = create_agent(
   model=llm_rapido,
   system_prompt=PROMPTS["orchestrator"]
)

# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(TypedDict):
    input:              str                                  # sobrescrito a cada etapa
    session_id:         str                                  # ID da sessão
    agentes_chamados:   Annotated[list[str], operator.add]  # acumula entre nós
    saida_especialista: str                                  # JSON do especialista ativo
    resposta_final:     str                                  # resposta para o usuário


# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado) -> dict:
    saida = router_app.invoke(
        {"messages": [{"role": "human", "content": estado["input"]}]},
        config={"configurable": {"thread_id": estado["session_id"]}},
    )
    texto = saida["messages"][-1].text

    # Resposta direta (saudação, fora de escopo): já escreve no campo final
    if not texto.strip().startswith("ROUTE="):
        return {
            "agentes_chamados": ["roteador"],
            "resposta_final":   texto,
        }

    # Encaminhamento: sobrescreve input com o protocolo para o especialista
    return {
        "input":            texto,
        "agentes_chamados": ["roteador"],
    }


def no_financeiro(estado: Estado) -> dict:
    saida = financeiro_app.invoke(
        {"messages": [{"role": "human", "content": estado["input"]}]},
        config={"configurable": {"thread_id": {estado['session_id']}}},
    )
    return {
        "saida_especialista": saida["messages"][-1].text,
        "agentes_chamados":   ["financeiro"],
    }


def no_agenda(estado: Estado) -> dict:
    saida = agenda_app.invoke(
        {"messages": [{"role": "human", "content": estado["input"]}]},
        config={"configurable": {"thread_id": {estado['session_id']}}},
    )
    return {
        "saida_especialista": saida["messages"][-1].text,
        "agentes_chamados":   ["agenda"],
    }


def no_faq(estado: Estado) -> dict:
    saida = faq_app.invoke(
        {"messages": [{"role": "human", "content": estado["input"]}]},
        config={"configurable": {"thread_id": {estado['session_id']}}},
    )
    return {
        "saida_especialista": saida["messages"][-1].text,
        "resposta_final":     saida["messages"][-1].text,  # bypassa o orquestrador
        "agentes_chamados":   ["faq"],
    }


def no_orquestrador(estado: Estado) -> dict:
    saida = orquestrador_app.invoke(
        {"messages": [{"role": "human", "content": estado["saida_especialista"]}]},
        config={"configurable": {"thread_id": {estado['session_id']}}},
    )
    return {
        "resposta_final":   saida["messages"][-1].text,
        "agentes_chamados": ["orquestrador"],
    }


# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    """Lê o protocolo do roteador e devolve o nome do próximo nó."""
    texto = estado["input"].strip()

    if not texto.startswith("ROUTE="):
        return "fim"   # resposta direta já foi escrita no nó do roteador

    rota = texto.split("\n", 1)[0].split("=", 1)[1].strip()
    return rota if rota in ("financeiro", "agenda", "faq") else "fim"


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro",   no_financeiro)
grafo.add_node("agenda",       no_agenda)
grafo.add_node("faq",          no_faq)
grafo.add_node("orquestrador", no_orquestrador)

grafo.set_entry_point("roteador")

grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "agenda":     "agenda",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro",   "orquestrador")
grafo.add_edge("agenda",       "orquestrador")
grafo.add_edge("orquestrador", END)
grafo.add_edge("faq",          END)   # FAQ bypassa o orquestrador

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo(pergunta_usuario: str, session_id: str) -> str:
    estado_inicial = {
        "input":              pergunta_usuario,
        "session_id":         session_id,
        "agentes_chamados":   [],
        "saida_especialista": "",
        "resposta_final":     "",
    }

    estado_final = fluxo_agentes.invoke(
        estado_inicial,
        config={"configurable": {"thread_id": session_id}},
    )

    print(f"[debug] agentes chamados: {estado_final['agentes_chamados']}")
    return estado_final["resposta_final"]

# PROCESSO DE PERGUNTAS E RESPOSTAS ATÉ QUE O USUÁRIO ENCERRE O CHAT
while True:
    user_input = input("👥 ")
    if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
       print("Encerrando a conversa.")
       break
    try:
       resposta = executar_fluxo(
           input=user_input,
           session_id="Não importa agora"
       )

       print(resposta)
    except Exception as e:
       print("Erro ao consumir a API:", e)