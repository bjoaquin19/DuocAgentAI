# DuocAgentAI
Agente de Duoc orientado a alumnos para dudas sobre reglamento, institución, fechas o prácticas.

Agente de IA que orienta a estudiantes nuevos de Duoc UC sobre el reglamento académico, el calendario 2026 y las prácticas profesionales. Proyecto de la asignatura Ingeniería de Soluciones con IA.

El agente esta hecho con LangGraph y usa modelo de Groq, específicamente gpt-oss-20b. Busca la información en los documentos oficiales guardados en MongoDB Atlas con búsqueda vectorial. Antes de responder
arma un plan, usa herramientas para consultar fechas o calcular plazos (en diversos casos hipoteticos, gran parte guardados en el repositorio, específicamente en la carpeta eval\resultados). Además implementamos
que este te recuerde, recordando tus datos entre conversaciones y pide autorización para hacer un plan.

Se puede usar desde una app en Streamlit, por consola o mediante una API conecrtada a n8n.

Alumnos: Joaquin Herrera y Roberto Bustos
