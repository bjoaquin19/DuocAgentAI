import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from agente import config
from agente.llm import crear_llm
from agente.orquestador import Orquestador

CARPETA = Path(__file__).parent


class Juicio(BaseModel):
    correcta: bool = Field(description="True si la respuesta cumple con lo esperado")
    explicacion: str


JUEZ = """Evalúa si la RESPUESTA de un agente cumple con la RESPUESTA ESPERADA para la PREGUNTA.
Es correcta si entrega la misma información clave (fechas, números, conclusión) aunque esté redactada distinto,
o si cumple el comportamiento descrito (por ejemplo "debe decir que no tiene esa información").
Si la respuesta inventa datos que no están en lo esperado, es incorrecta.

PREGUNTA: {pregunta}
RESPUESTA ESPERADA: {esperada}
RESPUESTA: {respuesta}"""


def evaluar(ids: list[int] | None = None, pausa: float = 2.0) -> dict:
    casos = json.loads((CARPETA / "dataset.json").read_text(encoding="utf-8"))
    if ids:
        casos = [c for c in casos if c["id"] in ids]
    agente = Orquestador()
    juez = crear_llm(config.GROQ_MODEL_JUEZ, temperatura=0).with_structured_output(Juicio)
    filas = []
    for caso in casos:
        user_id = f"eval_{caso['id']}"
        thread_id = f"{user_id}-{int(time.time())}"
        agente.servicios.db.borrar_usuario(user_id)
        inicio = time.time()
        try:
            r = agente.preguntar(user_id, thread_id, caso["pregunta"])
            pidio_aprobacion = bool(r["pendiente_aprobacion"])
            herramientas_pendientes = [a["herramienta"] for a in r["pendiente_aprobacion"]["acciones"]] if pidio_aprobacion else []
            if pidio_aprobacion:
                r = agente.responder_aprobacion(thread_id, False)
            usadas = r["herramientas"] + herramientas_pendientes
            esperadas = set(caso["herramientas_esperadas"])
            tool_ok = (not usadas) if not esperadas else esperadas.issubset(set(usadas))
            juicio = juez.invoke(JUEZ.format(pregunta=caso["pregunta"], esperada=caso["respuesta_esperada"], respuesta=r["respuesta"]))
            if caso.get("requiere_aprobacion"):
                juicio.correcta = juicio.correcta or pidio_aprobacion
            hit = None
            if caso.get("fuente_esperada"):
                hit = any(doc["fuente"] == caso["fuente_esperada"] for doc in r["documentos"][:5])
            filas.append({
                "id": caso["id"], "categoria": caso["categoria"], "pregunta": caso["pregunta"], "tipo": r["tipo"],
                "retrieval_hit": hit,
                "tipo_ok": r["tipo"] in caso["tipo_esperado"], "herramientas": usadas, "tool_ok": tool_ok,
                "pidio_aprobacion": pidio_aprobacion, "respuesta": r["respuesta"], "answer_ok": juicio.correcta,
                "juicio": juicio.explicacion, "decisiones": r["decisiones"], "latencia_s": round(time.time() - inicio, 1),
            })
        except Exception as e:
            filas.append({"id": caso["id"], "categoria": caso["categoria"], "pregunta": caso["pregunta"], "error": f"{type(e).__name__}: {e}",
                          "tipo_ok": False, "tool_ok": False, "answer_ok": False})
        finally:
            agente.servicios.db.borrar_usuario(user_id)
        f = filas[-1]
        print(f"[{caso['id']:>2}] tipo={'✓' if f['tipo_ok'] else '✗'} tools={'✓' if f['tool_ok'] else '✗'} "
              f"respuesta={'✓' if f['answer_ok'] else '✗'}  {caso['pregunta'][:60]}")
        time.sleep(pausa)

    n = len(filas)
    resumen = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "modelo": config.GROQ_MODEL, "juez": config.GROQ_MODEL_JUEZ,
        "casos": n,
        "planner_accuracy": round(sum(f["tipo_ok"] for f in filas) / n, 3),
        "tool_accuracy": round(sum(f["tool_ok"] for f in filas) / n, 3),
        "answer_accuracy": round(sum(f["answer_ok"] for f in filas) / n, 3),
        "errores": sum(1 for f in filas if "error" in f),
    }
    hits = [f["retrieval_hit"] for f in filas if f.get("retrieval_hit") is not None]
    resumen["retrieval_hit@5"] = round(sum(hits) / len(hits), 3) if hits else None
    return {"resumen": resumen, "casos": filas}


def guardar(resultado: dict) -> Path:
    carpeta = CARPETA / "resultados"
    carpeta.mkdir(exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M")
    (carpeta / f"resultados_{marca}.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    r = resultado["resumen"]
    lineas = [f"# Evaluación del agente ({r['fecha']})", "", f"Modelo: `{r['modelo']}` · Juez: `{r['juez']}` · Casos: {r['casos']}", "",
              "| Métrica | Valor |", "|---|---|",
              f"| Planner accuracy (tipo de tarea) | {r['planner_accuracy']:.0%} |",
              f"| Tool accuracy | {r['tool_accuracy']:.0%} |",
              f"| Answer accuracy (LLM-as-judge) | {r['answer_accuracy']:.0%} |",
              f"| Retrieval hit@5 (preguntas RAG) | {r['retrieval_hit@5'] if r['retrieval_hit@5'] is not None else '-'} |",
              f"| Errores | {r['errores']} |", "",
              "| # | Categoría | Tipo | Tools | Respuesta | Herramientas usadas |", "|---|---|---|---|---|---|"]
    for f in resultado["casos"]:
        lineas.append(f"| {f['id']} | {f['categoria']} | {'✓' if f['tipo_ok'] else '✗'} | {'✓' if f['tool_ok'] else '✗'} | "
                      f"{'✓' if f['answer_ok'] else '✗'} | {', '.join(f.get('herramientas', [])) or f.get('error', '-')} |")
    ruta = carpeta / f"resultados_{marca}.md"
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="*", help="evaluar solo algunos casos")
    parser.add_argument("--pausa", type=float, default=2.0, help="segundos entre casos (límite de Groq)")
    args = parser.parse_args()
    resultado = evaluar(args.ids, args.pausa)
    print(json.dumps(resultado["resumen"], ensure_ascii=False, indent=2))
    print(f"Guardado en {guardar(resultado)}")
