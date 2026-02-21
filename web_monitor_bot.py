"""
🤖 Bot de Telegram - Monitor de Webs (Silencioso)
==================================================
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
#  ⚙️  CONFIGURACIÓN
# ─────────────────────────────────────────────
BOT_TOKEN         = os.environ["BOT_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
INTERVAL          = 3 * 60    # segundos entre comprobaciones
TIMEOUT           = 60_000    # ms por web
PORT              = int(os.environ.get("PORT", 10000))
TZ                = ZoneInfo("Europe/Madrid")
RECOVERY_CONFIRMS = 1         # checks OK para confirmar recuperación

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
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Estado interno del monitor ──────────────
# Cuándo cayó cada web
down_since:    dict[str, datetime] = {}
# Webs de las que ya se envió alerta (anti-spam)
alerted:       set[str]           = set()
# Contador de checks OK consecutivos por web
ok_streak:     dict[str, int]     = {}
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  SERVIDOR HTTP (Render necesita un puerto)
# ─────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("🌐 HTTP en puerto %d", PORT)
    server.serve_forever()


# ─────────────────────────────────────────────
#  COMPROBACIÓN DE WEBS (secuencial)
# ─────────────────────────────────────────────

async def check_website(url: str, browser) -> dict:
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
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for url in WEBSITES:
            logger.info("  → %s", url)
            results.append(await check_website(url, browser))
        await browser.close()
    return results


# ─────────────────────────────────────────────
#  UTILIDADES DE TIEMPO
# ─────────────────────────────────────────────

def now_tz() -> datetime:
    return datetime.now(TZ)

def now_str() -> str:
    return now_tz().strftime("%d/%m/%Y %H:%M:%S")

def duration_str(since: datetime) -> str:
    """Formatea la duración de una caída de forma legible."""
    delta = now_tz() - since.astimezone(TZ)
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ─────────────────────────────────────────────
#  CONSTRUCCIÓN DE MENSAJES
# ─────────────────────────────────────────────

def build_alert(new_failures: list) -> str:
    lines = [
        f"🚨 *ALERTA — {now_str()}*",
        f"❌ {len(new_failures)} web(s) caída(s):",
        "─────────────────────────",
    ]
    for r in new_failures:
        code_str = f"`{r['code']}`" if r["code"] else "`---`"
        since = down_since.get(r["url"])
        since_str = f"\n   Caída desde: {since.astimezone(TZ).strftime('%H:%M:%S')}" if since else ""
        lines.append(
            f"❌ *{r['url']}*\n"
            f"   Error: {code_str} — {r['description']}"
            f"{since_str}"
        )
    return "\n".join(lines)


def build_recovery(url: str, since: datetime) -> str:
    dur = duration_str(since)
    return (
        f"✅ *RECUPERADA — {now_str()}*\n"
        f"🌐 {url}\n"
        f"⏱ Tiempo caída: *{dur}*"
    )


def build_full_report(results: list) -> str:
    ok     = sum(1 for r in results if r["status"] == "ok")
    failed = len(results) - ok
    lines  = [
        f"📡 *Estado de Webs* — {now_str()}",
        f"🌐 Total: {len(results)}  ✅ UP: {ok}  ❌ DOWN: {failed}",
        "─────────────────────────",
    ]
    for r in results:
        if r["status"] == "ok":
            time_str = f"{r['time_ms']} ms"
            lines.append(f"✅ *{r['url']}*\n   `{r['code']}` — {r['description']} — {time_str}")
        else:
            emoji    = "⏱️" if r["status"] == "timeout" else "❌"
            code_str = f"`{r['code']}`" if r["code"] else "`---`"
            since    = down_since.get(r["url"])
            since_str = f" (caída desde {since.astimezone(TZ).strftime('%H:%M:%S')}, {duration_str(since)})" if since else ""
            lines.append(f"{emoji} *{r['url']}*\n   {code_str} — {r['description']}{since_str}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  LOOP DE MONITORIZACIÓN SILENCIOSO
# ─────────────────────────────────────────────

async def monitor_loop(bot: Bot):
    while True:
        logger.info("🔍 Comprobación automática...")
        results = await run_checks()
        now     = now_tz()

        for r in results:
            url    = r["url"]
            is_ok  = r["status"] == "ok"

            if not is_ok:
                # ── Web caída ──────────────────────────────
                ok_streak.pop(url, None)          # resetea racha OK

                if url not in down_since:
                    down_since[url] = now         # primera vez que cae

                if url not in alerted:
                    # Primera alerta para este incidente
                    msg = build_alert([r])
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                    alerted.add(url)
                    logger.info("🚨 Alerta enviada: %s", url)
                else:
                    logger.info("🔇 Ya alertado, sin spam: %s", url)

            else:
                # ── Web OK ─────────────────────────────────
                if url in alerted:
                    # Estaba caída → contar checks OK consecutivos
                    ok_streak[url] = ok_streak.get(url, 0) + 1
                    logger.info("🔄 %s OK streak: %d/%d", url, ok_streak[url], RECOVERY_CONFIRMS)

                    if ok_streak[url] >= RECOVERY_CONFIRMS:
                        # Recuperación confirmada
                        since = down_since.pop(url, now)
                        alerted.discard(url)
                        ok_streak.pop(url, None)
                        msg = build_recovery(url, since)
                        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                        logger.info("✅ Recuperación confirmada: %s", url)
                else:
                    # Nunca estuvo caída en esta sesión
                    ok_streak.pop(url, None)

        await asyncio.sleep(INTERVAL)


# ─────────────────────────────────────────────
#  COMANDOS DEL BOT
# ─────────────────────────────────────────────

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Bot Monitor de Webs activo*\n\n"
        "Monitorización silenciosa:\n"
        "  🔇 Sin incidencias → sin mensajes\n"
        "  🚨 Caída detectada → alerta inmediata (una sola vez)\n"
        "  ✅ Recuperación confirmada → aviso con duración de la caída\n\n"
        "Comandos:\n"
        "  /check — Estado completo de todas las webs\n"
        "  /list  — Lista de webs monitorizadas\n\n"
        f"⏰ Comprobación cada *{INTERVAL // 60} min* · "
        f"Recuperación confirmada tras *{RECOVERY_CONFIRMS} checks OK*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Comprobando todas las webs, espera un momento...")
    results = await run_checks()
    now = now_tz()
    for r in results:
        if r["status"] != "ok" and r["url"] not in down_since:
            down_since[r["url"]] = now
    await update.message.reply_text(build_full_report(results), parse_mode="Markdown")


async def cmd_list(update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 *Webs monitorizadas:*\n"]
    for i, url in enumerate(WEBSITES, 1):
        lines.append(f"  {i}. {url}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────

async def post_init(app):
    asyncio.create_task(monitor_loop(app.bot))


def main():
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

    print("🚀 Bot iniciado.")
    print(f"⏰ Comprobación cada {INTERVAL // 60} min · Recuperación tras {RECOVERY_CONFIRMS} checks OK.")
    app.run_polling()


if __name__ == "__main__":
    main()
