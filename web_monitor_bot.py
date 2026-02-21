"""
🤖 Bot de Telegram - Monitor de Webs
======================================
Requisitos:
    pip install python-telegram-bot playwright
    playwright install chromium

Uso:
    1. Crea un bot en Telegram con @BotFather y obtén el TOKEN
    2. Obtén tu CHAT_ID iniciando conversación con @userinfobot
    3. Define las variables de entorno BOT_TOKEN y CHAT_ID:
           export BOT_TOKEN="tu_token_aqui"
           export CHAT_ID="tu_chat_id_aqui"
    4. Ejecuta: python web_monitor_bot.py
"""

import asyncio
import logging
import os
from datetime import datetime
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
#  ⚙️  CONFIGURACIÓN - VARIABLES DE ENTORNO
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]
INTERVAL  = 5 * 60  # Intervalo en segundos (5 minutos)

WEBSITES = [
    "https://www.redeia.com/es",
    "https://www.ree.es/es",
    "https://www.elewit.ventures/es",
    "https://www.reintel.es/es",
    "https://www.redinter.company/es",
    "https://www.redinter.pe/es",
    "https://www.redinter.cl/es",
    "https://bosquemarino.redeia.com/es",
    "https://www.planificacionelectrica.es/",
    "https://www.sistemaelectrico-ree.es/es",
]

TIMEOUT = 15_000  # ms (Playwright usa milisegundos)
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

STATUS_EMOJIS = {
    "ok":      "✅",
    "warning": "⚠️",
    "error":   "❌",
    "timeout": "⏱️",
}


async def check_website(url: str, browser) -> dict:
    """
    Comprueba una URL usando Playwright (Chromium headless).
    Esto evita el bloqueo por JA3 fingerprinting de Incapsula/Imperva.
    """
    context = None
    try:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            extra_http_headers={
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
        )
        page = await context.new_page()

        start = datetime.now()
        response = await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        elapsed = (datetime.now() - start).total_seconds()

        code = response.status if response else None

        if code is None:
            status = "error"
            description = "Sin respuesta"
        elif 200 <= code < 300:
            status = "ok"
            description = get_http_description(code)
        elif 300 <= code < 400:
            status = "warning"
            description = get_http_description(code)
        else:
            status = "error"
            description = get_http_description(code)

        return {
            "url": url,
            "status": status,
            "code": code,
            "time_ms": round(elapsed * 1000),
            "description": description,
        }

    except Exception as e:
        err = str(e)
        if "timeout" in err.lower():
            return {"url": url, "status": "timeout", "code": None,
                    "time_ms": TIMEOUT, "description": "Tiempo de espera agotado"}
        return {"url": url, "status": "error", "code": None,
                "time_ms": None, "description": err[:80]}
    finally:
        if context:
            await context.close()


def get_http_description(code: int) -> str:
    descriptions = {
        200: "OK", 201: "Creado", 204: "Sin contenido",
        301: "Movido permanentemente", 302: "Redirección temporal",
        400: "Solicitud incorrecta", 401: "No autorizado", 403: "Prohibido",
        404: "No encontrado", 408: "Timeout", 429: "Demasiadas solicitudes",
        500: "Error interno del servidor", 502: "Bad Gateway",
        503: "Servicio no disponible", 504: "Gateway Timeout",
    }
    return descriptions.get(code, f"Código HTTP {code}")


def build_report(results: list) -> str:
    now    = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ok     = sum(1 for r in results if r["status"] == "ok")
    failed = len(results) - ok

    lines = [
        f"📡 *Monitor de Webs* — {now}",
        f"🌐 Total: {len(results)}  ✅ OK: {ok}  ❌ Caídas: {failed}",
        "─────────────────────────",
    ]

    for r in results:
        emoji    = STATUS_EMOJIS.get(r["status"], "❓")
        code_str = f"`{r['code']}`" if r["code"] else "`---`"
        time_str = f"{r['time_ms']} ms" if r["time_ms"] else "N/A"
        lines.append(
            f"{emoji} *{r['url']}*\n"
            f"   Código: {code_str} — {r['description']}\n"
            f"   Tiempo: {time_str}"
        )

    lines.append("─────────────────────────")
    lines.append("_Próxima comprobación en 5 minutos_")
    return "\n".join(lines)


async def run_checks() -> list:
    """Lanza todas las comprobaciones en paralelo con un único navegador."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        tasks = [check_website(url, browser) for url in WEBSITES]
        results = await asyncio.gather(*tasks)
        await browser.close()
    return list(results)


# ─────────────────────────────────────────────
#  LOOP DE MONITORIZACIÓN
# ─────────────────────────────────────────────

async def monitor_loop(bot: Bot):
    """Bucle infinito que comprueba las webs cada INTERVAL segundos."""
    while True:
        logger.info("🔍 Comprobando webs con Playwright...")
        results = await run_checks()
        message = build_report(results)
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
        logger.info("✅ Reporte enviado. Esperando %d minutos...", INTERVAL // 60)
        await asyncio.sleep(INTERVAL)


# ─────────────────────────────────────────────
#  COMANDOS DEL BOT
# ─────────────────────────────────────────────

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Bot Monitor de Webs activo*\n\n"
        "Comandos disponibles:\n"
        "  /start — Muestra este mensaje\n"
        "  /check — Comprueba las webs ahora mismo\n"
        "  /list  — Muestra las webs monitorizadas\n\n"
        f"⏰ Comprobación automática cada *{INTERVAL // 60} minutos*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Comprobando webs con Playwright, espera un momento...")
    results = await run_checks()
    await update.message.reply_text(build_report(results), parse_mode="Markdown")


async def cmd_list(update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 *Webs monitorizadas:*\n"]
    for i, url in enumerate(WEBSITES, 1):
        lines.append(f"  {i}. {url}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────

async def post_init(app):
    """Lanza el monitor en segundo plano justo después de iniciar la app."""
    asyncio.create_task(monitor_loop(app.bot))


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("list",  cmd_list))

    print("🚀 Bot iniciado. Pulsa Ctrl+C para detenerlo.")
    print(f"⏰ Comprobando webs cada {INTERVAL // 60} minutos.")
    app.run_polling()


if __name__ == "__main__":
    main()
