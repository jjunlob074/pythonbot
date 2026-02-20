"""
🤖 Bot de Telegram - Monitor de Webs
======================================
Requisitos:
    pip install python-telegram-bot requests

Uso:
    1. Crea un bot en Telegram con @BotFather y obtén el TOKEN
    2. Obtén tu CHAT_ID iniciando conversación con @userinfobot
    3. Edita las variables BOT_TOKEN, CHAT_ID y WEBSITES
    4. Ejecuta: python web_monitor_bot.py
"""

import requests
import asyncio
import logging
from datetime import datetime
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ─────────────────────────────────────────────
#  ⚙️  CONFIGURACIÓN - EDITA ESTOS VALORES
# ─────────────────────────────────────────────
BOT_TOKEN = "TU_TOKEN_AQUI"          # Token de @BotFather
CHAT_ID   = "TU_CHAT_ID_AQUI"       # Tu chat ID (número)
INTERVAL  = 5 * 60                   # Intervalo en segundos (5 minutos)

WEBSITES = [
    "https://www.google.com",
    "https://www.github.com",
    "https://www.example.com",
    "https://httpstat.us/500",        # Ejemplo de web caída (error 500)
    "https://httpstat.us/404",        # Ejemplo de web con 404
]
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TIMEOUT = 10  # segundos máximos de espera por web

STATUS_EMOJIS = {
    "ok":      "✅",
    "warning": "⚠️",
    "error":   "❌",
    "timeout": "⏱️",
}

def check_website(url: str) -> dict:
    """Comprueba el estado de una web y devuelve un diccionario con el resultado."""
    try:
        start = datetime.now()
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        elapsed = (datetime.now() - start).total_seconds()

        status_code = response.status_code

        if 200 <= status_code < 300:
            status = "ok"
        elif 300 <= status_code < 400:
            status = "warning"
        else:
            status = "error"

        return {
            "url":         url,
            "status":      status,
            "code":        status_code,
            "time_ms":     round(elapsed * 1000),
            "description": get_http_description(status_code),
        }

    except requests.exceptions.Timeout:
        return {
            "url":         url,
            "status":      "timeout",
            "code":        None,
            "time_ms":     TIMEOUT * 1000,
            "description": "Tiempo de espera agotado",
        }
    except requests.exceptions.ConnectionError:
        return {
            "url":         url,
            "status":      "error",
            "code":        None,
            "time_ms":     None,
            "description": "Error de conexión (sin respuesta)",
        }
    except Exception as e:
        return {
            "url":         url,
            "status":      "error",
            "code":        None,
            "time_ms":     None,
            "description": f"Error inesperado: {str(e)}",
        }


def get_http_description(code: int) -> str:
    """Devuelve una descripción legible del código HTTP."""
    descriptions = {
        200: "OK",
        201: "Creado",
        204: "Sin contenido",
        301: "Movido permanentemente",
        302: "Redirección temporal",
        304: "No modificado",
        400: "Solicitud incorrecta",
        401: "No autorizado",
        403: "Prohibido",
        404: "No encontrado",
        408: "Tiempo de espera agotado",
        429: "Demasiadas solicitudes",
        500: "Error interno del servidor",
        502: "Bad Gateway",
        503: "Servicio no disponible",
        504: "Gateway Timeout",
    }
    return descriptions.get(code, f"Código HTTP {code}")


def build_report(results: list) -> str:
    """Construye el mensaje de reporte para Telegram."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    total  = len(results)
    ok     = sum(1 for r in results if r["status"] == "ok")
    failed = total - ok

    lines = [
        f"📡 *Monitor de Webs* — {now}",
        f"{'─' * 35}",
        f"🌐 Total webs: {total}  |  ✅ OK: {ok}  |  ❌ Caídas: {failed}",
        f"{'─' * 35}",
    ]

    for r in results:
        emoji = STATUS_EMOJIS.get(r["status"], "❓")
        code_str = f"`{r['code']}`" if r["code"] else "`---`"
        time_str = f"{r['time_ms']} ms" if r["time_ms"] else "N/A"

        lines.append(
            f"{emoji} *{r['url']}*\n"
            f"   Código: {code_str} — {r['description']}\n"
            f"   Tiempo de respuesta: {time_str}"
        )

    lines.append(f"{'─' * 35}")
    lines.append("_Próxima comprobación en 5 minutos_")

    return "\n".join(lines)


async def monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tarea periódica que comprueba las webs y envía el reporte."""
    logger.info("🔍 Comprobando webs...")
    results = [check_website(url) for url in WEBSITES]
    message = build_report(results)

    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode="Markdown",
    )
    logger.info("✅ Reporte enviado.")


# ─────────────────────────────────────────────
#  COMANDOS DEL BOT
# ─────────────────────────────────────────────

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /start — mensaje de bienvenida."""
    msg = (
        "👋 *Bot Monitor de Webs activo*\n\n"
        "Comandos disponibles:\n"
        "  /start  — Muestra este mensaje\n"
        "  /check  — Comprueba las webs ahora mismo\n"
        "  /list   — Muestra las webs monitorizadas\n\n"
        f"⏰ Comprobación automática cada *{INTERVAL // 60} minutos*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /check — comprobación inmediata."""
    await update.message.reply_text("🔍 Comprobando webs, espera un momento...")
    results = [check_website(url) for url in WEBSITES]
    message = build_report(results)
    await update.message.reply_text(message, parse_mode="Markdown")


async def cmd_list(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /list — lista de webs monitorizadas."""
    lines = ["📋 *Webs monitorizadas:*\n"]
    for i, url in enumerate(WEBSITES, 1):
        lines.append(f"  {i}. {url}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ARRANQUE DEL BOT
# ─────────────────────────────────────────────

def main() -> None:
    if BOT_TOKEN == "TU_TOKEN_AQUI":
        print("❌ ERROR: Debes configurar BOT_TOKEN y CHAT_ID antes de ejecutar.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registrar comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("list",  cmd_list))

    # Programar tarea periódica (cada INTERVAL segundos)
    job_queue = app.job_queue
    job_queue.run_repeating(monitor_job, interval=INTERVAL, first=10)

    print("🚀 Bot iniciado. Pulsa Ctrl+C para detenerlo.")
    print(f"⏰ Comprobando webs cada {INTERVAL // 60} minutos.")
    app.run_polling()


if __name__ == "__main__":
    main()
