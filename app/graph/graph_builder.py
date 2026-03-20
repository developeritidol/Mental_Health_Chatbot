"""
LangGraph graph builder.
Compiles the Emotion → Guardrail → (LLM | Crisis) pipeline.
"""

from langgraph.graph import StateGraph, END
from app.graph.state import ChatState
from app.graph.nodes.emotion import emotion_node
from app.graph.nodes.guardrail import guardrail_node
from app.graph.nodes.llm import llm_node
from app.core.logger import get_logger

logger = get_logger(__name__)


def _route_after_guardrail(state: dict) -> str:
    """
    Conditional edge: decides whether to go to the LLM or skip to END.
    If is_crisis is True, the guardrail already populated a crisis_response,
    so we skip the LLM entirely for safety.
    """
    if state.get("is_crisis", False):
        logger.warning("Graph Router — Crisis detected, bypassing LLM → END")
        return "crisis_end"
    return "llm_node"


def build_graph() -> StateGraph:
    """
    Builds and compiles the LangGraph StateGraph.
    
    Flow:
        emotion_node → guardrail_node → [if crisis → END, else → llm_node → END]
    """
    logger.info("Building LangGraph pipeline...")

    graph = StateGraph(ChatState)

    # -- Add Nodes --
    graph.add_node("emotion_node", emotion_node)
    graph.add_node("guardrail_node", guardrail_node)
    graph.add_node("llm_node", llm_node)

    # -- Define Edges --
    graph.set_entry_point("emotion_node")
    graph.add_edge("emotion_node", "guardrail_node")

    # Conditional routing after guardrail
    graph.add_conditional_edges(
        "guardrail_node",
        _route_after_guardrail,
        {
            "llm_node": "llm_node",
            "crisis_end": END,
        },
    )
    graph.add_edge("llm_node", END)

    compiled = graph.compile()
    logger.info("LangGraph pipeline compiled successfully.")
    return compiled
