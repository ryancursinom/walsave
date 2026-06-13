# Imports
import os
import operator
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver # -> Substitui a função que tínhamos criado para histórico com sessões, porque faz isso automaticamente
from langchain_core.messages import RemoveMessage
from typing import Annotated
from langgraph.graph import StateGraph, MessagesState, END
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
class Estado(MessagesState):
    agentes_chamados: Annotated[list[str], operator.add]
    rota: str


# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado) -> dict:
    saida = router_app.invoke({"messages": list(estado["messages"])})
    texto = saida["messages"][-1].text

    if "ROUTE=" not in texto:
        return {
            "agentes_chamados": ["roteador"],
            "rota":             "fim",
            "messages":         [{"role": "assistant", "content": texto}],
        }

    rota = "fim"
    for linha in texto.splitlines():
        if linha.startswith("ROUTE="):
            rota = linha.split("=", 1)[1].strip()
            break

    return {
        "agentes_chamados": ["roteador", rota],
        "rota":             rota,
        # Histórico limpo: o especialista vai ler só a conversa real
    }

def no_orquestrador(estado: Estado) -> dict:
    # Pega a última resposta do especialista (última AIMessage com conteúdo)
    ultima_especialista = ""
    for mensagem in reversed(estado["messages"]):
        if mensagem.type == "ai" and mensagem.content:
            ultima_especialista = mensagem.content
            break
    
    saida = orquestrador_app.invoke({
        "messages": {"role": "human", "content": ultima_especialista}
    })

    return {
        "agentes_chamados": ["orquestrador"],
        "messages":        [{"role": "assistant", "content": saida["messages"][-1].text}]
    }


# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    return estado["rota"] if estado["rota"] in ("financeiro", "agenda", "faq") else "fim"


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro", financeiro_app)
grafo.add_node("faq", faq_app)
grafo.add_node("orquestrador", no_orquestrador)

grafo.set_entry_point("roteador")

grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro","orquestrador")
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
        "messages": [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota": "",
    }

    estado_final = fluxo_agentes.invoke(
        estado_inicial,
        config={"configurable": {"thread_id": session_id}},
    )

    print(f"[debug] agentes chamados: {estado_final['agentes_chamados']}")
    return estado_final["messages"][-1].text

# PROCESSO DE PERGUNTAS E RESPOSTAS ATÉ QUE O USUÁRIO ENCERRE O CHAT
while True:
    user_input = input("👥 ")
    if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
       print("Encerrando a conversa.")
       break
    try:
       resposta = executar_fluxo(
           pergunta_usuario=user_input,
           session_id="Não importa agora"
       )

       print(resposta)
    except Exception as e:
       print("Erro ao consumir a API:", e)