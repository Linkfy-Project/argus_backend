from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.jobs.data_sync_job import sync_public_data_job
from app.models.work import PublicWork

logger = get_logger(__name__)

TIMEZONE = "America/Sao_Paulo"
JOB_ID = "public_data_sync_every_15_days"

scheduler = BackgroundScheduler(timezone=TIMEZONE)


def _format_time_left(seconds: float) -> str:
    """
    Formata segundos em dias, horas, minutos e segundos.
    """

    seconds = max(int(seconds), 0)

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60
    seconds %= 60

    parts = []

    if days:
        parts.append(f"{days} dia(s)")
    if hours:
        parts.append(f"{hours} hora(s)")
    if minutes:
        parts.append(f"{minutes} minuto(s)")

    parts.append(f"{seconds} segundo(s)")

    return ", ".join(parts)


def get_next_sync_info() -> dict:
    """
    Retorna informações sobre a próxima execução automática.
    """

    job = scheduler.get_job(JOB_ID)

    if not job or not job.next_run_time:
        return {
            "scheduled": False,
            "message": "Nenhuma atualização automática agendada no momento.",
        }

    now = datetime.now(ZoneInfo(TIMEZONE))
    next_run_time = job.next_run_time

    seconds_left = (next_run_time - now).total_seconds()

    return {
        "scheduled": True,
        "job_id": JOB_ID,
        "timezone": TIMEZONE,
        "now": now.isoformat(),
        "next_run_time": next_run_time.isoformat(),
        "seconds_left": max(int(seconds_left), 0),
        "time_left": _format_time_left(seconds_left),
    }


def log_next_sync_info() -> None:
    """
    Mostra no terminal quanto tempo falta para a próxima atualização.
    """

    info = get_next_sync_info()

    if not info.get("scheduled"):
        print(f"[ARGUS SCHEDULER] {info['message']}")
        return

    print(
        "[ARGUS SCHEDULER] Próxima atualização automática em "
        f"{info['time_left']}."
    )
    print(
        "[ARGUS SCHEDULER] Data/hora prevista: "
        f"{info['next_run_time']} ({info['timezone']})."
    )


def _should_run_sync_immediately() -> bool:
    """
    Decide se o sync deve rodar imediatamente no startup.

    Regras:
    1. Se SYNC_ON_COLD_START=True (desenvolvimento) → sempre roda.
    2. Se o banco estiver vazio (0 obras) → roda para popular dados.
    3. Caso contrário → NÃO roda (adiado para 15 dias).
    """
    settings = get_settings()

    # Modo desenvolvimento: sempre roda
    if settings.SYNC_ON_COLD_START:
        logger.info("[ARGUS SCHEDULER] SYNC_ON_COLD_START=true — sync imediato (modo dev)")
        return True

    # Verifica se o banco tem dados
    db = SessionLocal()
    try:
        count = db.query(func.count(PublicWork.id)).scalar() or 0
        if count == 0:
            logger.info("[ARGUS SCHEDULER] Banco vazio — sync imediato para popular dados")
            return True
        else:
            logger.info(
                "[ARGUS SCHEDULER] Banco já tem %d obras — sync adiado para 15 dias",
                count,
            )
            return False
    except Exception as exc:
        logger.warning("[ARGUS SCHEDULER] Erro ao verificar banco: %s — sync imediato", exc)
        return True
    finally:
        db.close()


def start_scheduler() -> None:
    """
    Inicia o scheduler interno do ARGUS.

    Comportamento:
    - Se SYNC_ON_COLD_START=True (dev) ou banco vazio → roda imediatamente.
    - Senão → agenda próxima execução para daqui a 15 dias.
    - Depois disso, repete a cada 15 dias.
    """

    if scheduler.running:
        log_next_sync_info()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))

    if _should_run_sync_immediately():
        next_run_time = now
        print("[ARGUS SCHEDULER] Scheduler iniciado — sync será executado imediatamente.")
    else:
        next_run_time = now + timedelta(days=15)
        print(
            f"[ARGUS SCHEDULER] Scheduler iniciado — "
            f"próximo sync em 15 dias ({next_run_time.strftime('%d/%m/%Y %H:%M')})."
        )

    scheduler.add_job(
        sync_public_data_job,
        trigger="interval",
        days=15,
        next_run_time=next_run_time,
        id=JOB_ID,
        name="Atualização quinzenal dos dados públicos do ARGUS",
        replace_existing=True,
        kwargs={
            "municipio": "Macae",
            "ano": None,
        },
    )

    scheduler.start()

    log_next_sync_info()


def stop_scheduler() -> None:
    """
    Encerra o scheduler com segurança.
    """

    if scheduler.running:
        scheduler.shutdown()

    print("[ARGUS SCHEDULER] Scheduler encerrado.")


def run_sync_now(municipio: str = "Macae", ano: int | None = None) -> dict:
    """
    Executa a sincronização manualmente pelo Swagger.
    """

    result = sync_public_data_job(municipio=municipio, ano=ano)

    log_next_sync_info()

    return result