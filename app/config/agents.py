from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from app.config.llms import llm_especialista, llm_rapido
from app.prompts.prompts import PROMPTS
from app.tools.financial_tools import TOOLS
from app.tools.mongodb_tools import TOOLS_MEMORIA
from app.tools.faq_tools import faq_retriever

router_memory = MemorySaver()

router_app = create_agent(
   model=llm_rapido,
   system_prompt=PROMPTS["router"],
   checkpointer=router_memory,
   tools=TOOLS_MEMORIA
)

financeiro_app = create_agent(
   model=llm_especialista,
   system_prompt=PROMPTS["financial"],
   tools=[
       *TOOLS,
       *TOOLS_MEMORIA
    ]
)

agenda_app = create_agent(
   model=llm_especialista,
   system_prompt=PROMPTS["schedule"]
)

faq_app = create_agent(
    model=llm_rapido,
    system_prompt=PROMPTS["faq"],
    tools=[
        faq_retriever,
        *TOOLS_MEMORIA
    ]
)

orquestrador_app = create_agent(
   model=llm_rapido,
   system_prompt=PROMPTS["orchestrator"]
)