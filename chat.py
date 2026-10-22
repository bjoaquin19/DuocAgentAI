import argparse
import uuid

from agente.orquestador import Orquestador


def mostrar(r: dict):
    print("\n  · " + "\n  · ".join(r["decisiones"]))
    if r["pendiente_aprobacion"]:
        return
    print(f"\nAgente: {r['respuesta']}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chat por consola con el agente")
    parser.add_argument("--usuario", default="estudiante_demo")
    parser.add_argument("--conversacion", default=None)
    args = parser.parse_args()
    thread_id = args.conversacion or f"{args.usuario}-{uuid.uuid4().hex[:8]}"
    agente = Orquestador()
    print(f"Usuario: {args.usuario} | conversación: {thread_id} (escribe 'salir' para terminar)\n")
    while True:
        texto = input("Tú: ").strip()
        if texto.lower() in ("salir", "exit", "quit"):
            break
        if not texto:
            continue
        r = agente.preguntar(args.usuario, thread_id, texto)
        mostrar(r)
        while r["pendiente_aprobacion"]:
            for accion in r["pendiente_aprobacion"]["acciones"]:
                print(f"\n  El agente quiere ejecutar {accion['herramienta']} con {accion['argumentos']}")
            aprobado = input("  ¿Autorizas? (s/n): ").strip().lower().startswith("s")
            r = agente.responder_aprobacion(thread_id, aprobado)
            mostrar(r)
