# Imports
import operator
from langgraph.checkpoint.memory import MemorySaver # -> Substitui a função que tínhamos criado para histórico com sessões, porque faz isso automaticamente
from langchain_core.messages import RemoveMessage
from langchain_core.runnables import RunnableConfig
from typing import Annotated
from langgraph.graph import StateGraph, MessagesState, END
from app.guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada
from app.memory import iniciar_sessao, salvar_mensagem, encerrar_sessao
from app.config.agents import router_app, financeiro_app, faq_app, orquestrador_app, agenda_app
from app.juiz import _coleta, _julgar_jev, _publicar, FRASE_SEGURA
from app.config.database import CURRENT_USER_ID
from app.memory import salvar_mensagem
from app.observabilidade import abrir_turno, fechar_turno, status_do_estado

# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(MessagesState):
    agentes_chamados: Annotated[list[str], operator.add]
    rota: str
    mapa_pii: dict
    session_id: str
    user_id: str


# ==============================================================================
# NÓS
# ==============================================================================

def no_guardrail_entrada(estado: Estado, config: RunnableConfig) -> dict:
    human_message = list(estado["messages"])[-1]

    texto_anonimizado, mapa = anonimizar_entrada(human_message.text)

    resultado = guardrail_entrada(texto_anonimizado)
    
    salvar_mensagem(
        session_id=estado["session_id"],
        user_id=estado["user_id"],
        role="human",
        content=texto_anonimizado
    )

    if resultado["bloqueado"]:
        salvar_mensagem(
            session_id=estado["session_id"],
            user_id=estado["user_id"],
            role="assistant",
            content=resultado["mensagem"]
        )

        return {
            "messages": [{"role": "assistant", "content": resultado["mensagem"]}],
            "rota": "fim",
            "mapa_pii": {},
            "agentes_chamados": [f"guardrail_entrada: {resultado['motivo']}"],
        }
    
    return {
        "messages": [
            RemoveMessage(id=human_message.id),  # Remove a mensagem original do usuário
            {"role": "human", "content": texto_anonimizado}
        ],
        "mapa_pii": mapa,
        "agentes_chamados": ["guardrail_entrada"],
        "rota": "roteador",
    }

def no_roteador(estado: Estado,  config: RunnableConfig) -> dict:
    saida = router_app.invoke({"messages": list(estado["messages"])}, config=config)
    texto = saida["messages"][-1].text

    if "ROUTE=" not in texto:
        salvar_mensagem(
            session_id=estado["session_id"],
            user_id=estado["user_id"],
            role="assistant",
            content=texto
        )

        return {
            "agentes_chamados": ["roteador"],
            "rota":             "fim",
            "messages":         [{"role": "assistant", "content": texto}],
        }

    aliases = {"schedule": "agenda", "financial": "financeiro"}
    rota = "fim"
    for linha in texto.splitlines():
        if linha.startswith("ROUTE="):
            rota_bruta = linha.split("=", 1)[1].strip().lower()
            rota = aliases.get(rota_bruta, rota_bruta)
            break

    return {
        "agentes_chamados": ["roteador", rota],
        "rota":             rota,
        # Histórico limpo: o especialista vai ler só a conversa real
    }

def no_orquestrador(estado: Estado, config: RunnableConfig) -> dict:
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
        "messages":        [{"role": "assistant", "content": saida["messages"][-1].text}],
    }

def no_juiz(estado: dict) -> dict:
    pergunta, resposta, evidencia = _coleta(estado)

    if not resposta:
        return {"agentes_chamados": ["juiz:sem_resposta"]}
    try:
        return _julgar_jev(pergunta, resposta, evidencia)
    
    except Exception as exc:
        print(f"[juiz] erro {exc}")
        return _publicar(FRASE_SEGURA, "erro", resposta)

def no_guardrail_saida(estado: Estado, config: RunnableConfig) -> dict:
    ultima_resposta = list(estado["messages"])[-1] 

    resposta_final = guardrail_saida(ultima_resposta.text, estado["mapa_pii"])

    salvar_mensagem(
        session_id=estado["session_id"],
        user_id=estado["user_id"], 
        role="assistant",
        content=resposta_final["conteudo"]
    )

    return {
        "agentes_chamados": ["guardrail_saida"],
        "messages": [
            RemoveMessage(id=ultima_resposta.id),
            {"role": "assistant", "content": resposta_final["conteudo"]}
        ],
    }


# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    return estado["rota"] if estado["rota"] in ("financeiro", "agenda", "faq") else "fim"

def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    return estado["rota"]

# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro", financeiro_app)
grafo.add_node("agenda", agenda_app)
grafo.add_node("faq", faq_app)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("juiz", no_juiz)
grafo.add_node("guardrail_saida", no_guardrail_saida)

grafo.set_entry_point("guardrail_entrada")

grafo.add_conditional_edges(
    "guardrail_entrada",
    decidir_pos_guardrail_entrada,
    {
        "roteador": "roteador",
        "fim": END,
    }
)

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

grafo.add_edge("financeiro", "orquestrador")
grafo.add_edge("agenda", "orquestrador")
grafo.add_edge("orquestrador", "guardrail_saida")
grafo.add_edge("guardrail_saida", "juiz")
grafo.add_edge("faq", "juiz")
grafo.add_edge("juiz", END)

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo(pergunta_usuario: str, session_id_user: str, user_id: str) -> list:
    if not user_id or not user_id.strip():
        raise ValueError("user_id é obrigatório para executar o chat")
    iniciar_sessao(session_id=session_id_user, user_id=user_id)

    estado_inicial = {
        "messages": [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota": "",
        "mapa_pii": {},
        "session_id": session_id_user,
        "user_id": user_id
    }

    token = CURRENT_USER_ID.set(user_id)
    turno_id = abrir_turno()
    status = 'erro'
    rota = ''

    try:

        estado_final = fluxo_agentes.invoke(
            estado_inicial,
            config={"configurable": {"thread_id": session_id_user, "user_id": user_id}},
        )

    finally:
        fechar_turno(turno_id, session_id_user, rota, status)
        CURRENT_USER_ID.reset(token)
    
    return [
        estado_final["messages"][-1].text,
        estado_final["agentes_chamados"]
    ]

# # PROCESSO DE PERGUNTAS E RESPOSTAS ATÉ QUE O USUÁRIO ENCERRE O CHAT
# session_id = "12345"
# iniciar_sessao(session_id=session_id)

# try:
#     while True:
#         user_input = input("👥 ")
#         if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
#             print("Encerrando a conversa.")
#             raise SystemExit(0)
#         try:
#             resposta = executar_fluxo(
#                 pergunta_usuario=user_input,
#                 session_id=session_id
#             )

#             print(resposta)
#         except Exception as e:
#             print("Erro ao consumir a API:", e)

# except KeyboardInterrupt:
#     print("Sessão encerrada pelo usuário!")

# finally:
#     resumo = encerrar_sessao(session_id=session_id)
#     print("=== RESUMO DA SESSÃO ===\n")
#     print(resumo)
