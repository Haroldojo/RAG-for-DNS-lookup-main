SYSTEM_PROMPT = """You are a technical support copilot. Write a concise response using only the
provided evidence. Do not infer or invent DNS records, propagation success, domain ownership, SSL
status, server status, configuration changes, or sources. Clearly separate observed facts from
recommendations. If evidence is insufficient or a tool failed, say the issue could not be diagnosed
confidently. Use exactly these headings: Diagnosis, Evidence observed, Recommended next steps,
Sources, Escalation status. You may cite only the supplied source labels such as [1]. Do not reveal
internal reasoning or chain-of-thought."""


def build_generation_prompt(
    query: str,
    intent: str,
    route_taken: str,
    retrieved_chunks: list[dict[str, object]],
    dns_result: dict[str, object] | None,
    ssl_result: dict[str, object] | None,
    http_result: dict[str, object] | None,
    citations: list[dict[str, str]],
    errors: list[str],
) -> str:
    sources = (
        "\n".join(
            f"[{index + 1}] {citation['source']} | {citation['topic']} | version {citation['version']}"
            for index, citation in enumerate(citations)
        )
        or "No retrieved sources."
    )
    chunks = (
        "\n\n".join(
            f"SOURCE [{index + 1}]\n{chunk['text']}" for index, chunk in enumerate(retrieved_chunks)
        )
        or "No retrieved document evidence."
    )
    return f"""User query: {query}
Intent: {intent}
Route: {route_taken}

Retrieved support evidence:
{chunks}

DNS tool output (may be null):
{dns_result}

SSL tool output (may be null):
{ssl_result}

HTTP tool output (may be null):
{http_result}

Available sources (application-controlled):
{sources}

Tool errors:
{errors or "None"}
"""
