from agente import config


def crear_llm(modelo: str | None = None, temperatura: float | None = None):
    from langchain_groq import ChatGroq

    if not config.GROQ_API_KEY:
        raise ValueError("Falta GROQ_API_KEY en el .env")
    return ChatGroq(
        model=modelo or config.GROQ_MODEL,
        temperature=config.TEMPERATURA if temperatura is None else temperatura,
        api_key=config.GROQ_API_KEY,
        max_retries=2,
    )
