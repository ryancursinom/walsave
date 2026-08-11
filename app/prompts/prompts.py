from app.prompts.faq_prompt import FAQ_PROMPT
from app.prompts.router_prompt import COMPLETE_ROUTER_PROMPT
from app.prompts.financial_prompt import COMPLETE_FINANCIAL_PROMPT
from app.prompts.schedule_prompt import COMPLETE_SCHEDULE_PROMPT
from app.prompts.orchestrator_prompt import COMPLETE_ORCHESTRATOR_PROMPT

PROMPTS = {
    "faq": FAQ_PROMPT,
    "router": COMPLETE_ROUTER_PROMPT,
    "financial": COMPLETE_FINANCIAL_PROMPT,
    "schedule": COMPLETE_SCHEDULE_PROMPT,
    "orchestrator": COMPLETE_ORCHESTRATOR_PROMPT
}