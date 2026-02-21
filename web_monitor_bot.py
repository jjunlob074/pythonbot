"""
🤖 Bot de Telegram - Monitor de Webs
======================================
Requisitos:
    pip install python-telegram-bot playwright
    playwright install chromium

Uso:
    1. Define las variables de entorno BOT_TOKEN y CHAT_ID
    2. Ejecuta: python web_monitor_bot.py
"""

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
#  ⚙️  CONFIGURACIÓN - VARIABLES DE ENTORNO
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]
INTERVAL  = 3 * 60  # Intervalo en segundos (3 minutos)
PORT      = int(os.environ.get("PORT", 10000))

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

TIMEOUT = 60_000  # ms
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


# ─────────────────────────────────────────────
#  SERVIDOR HTTP (necesario para Render)
# ─────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Silencia los logs del servidor HTTP


def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("🌐 Servidor HTTP escuchando en puerto %d", PORT)
    server.serve_forever()


# ─────────────────────────────────────────────
#  COMPROBACIÓN DE WEBS (secuencial)
# ─────────────────────────────────────────────

async def check_website(url: str, browser) -> dict:
    """Comprueba una URL. Abre un contexto, lo usa y lo cierra."""
    context = None
    try:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
        )
        page = await context.new_page()

        start = datetime.now()
        response = await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        elapsed = (datetime.now() - start).total_seconds()

        code = response.status if response else None

        if code is None:
            status, description = "error", "Sin respuesta"
        elif 200 <= code < 300:
            status, description = "ok", get_http_description(code)
        elif 300 <= code < 400:
            status, description = "warning", get_http_description(code)
        else:
            status, description = "error", get_http_description(code)

        return {"url": url, "status": status, "code": code,
                "time_ms": round(elapsed * 1000), "description": description}

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


async def run_checks() -> list:
    """
    Comprobaciones SECUENCIALES con un único navegador.
    Una web a la vez → mínimo consumo de RAM.
    """
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for url in WEBSITES:
            logger.info("  → Comprobando %s", url)
            result = await check_website(url, browser)
            results.append(result)
        await browser.close()
    return results


# ─────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────

def build_report(results: list) -> str:
    TZ = ZoneInfo("Europe/Madrid")
    now    = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
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


# ─────────────────────────────────────────────
#  LOOP DE MONITORIZACIÓN
# ─────────────────────────────────────────────

async def monitor_loop(bot: Bot):
    """Bucle infinito que comprueba las webs cada INTERVAL segundos."""
    while True:
        logger.info("🔍 Iniciando comprobación secuencial de webs...")
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
    await update.message.reply_text("🔍 Comprobando webs, espera un momento...")
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
    # Arranca el servidor HTTP en un hilo separado (requerido por Render)
    Thread(target=start_http_server, daemon=True).start()

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
