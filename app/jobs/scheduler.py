from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.data_sync_job import sync_public_data_job

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


def start_scheduler() -> None:
    """
    Inicia o scheduler interno do ARGUS.

    Comportamento:
    - roda uma vez imediatamente quando a API iniciar;
    - depois roda novamente a cada 15 dias.
    """

    if scheduler.running:
        log_next_sync_info()
        return

    scheduler.add_job(
        sync_public_data_job,
        trigger="interval",
        days=15,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)),
        id=JOB_ID,
        name="Atualização quinzenal dos dados públicos do ARGUS",
        replace_existing=True,
        kwargs={
            "municipio": "Macae",
            "ano": None,
        },
    )

    scheduler.start()

    print("[ARGUS SCHEDULER] Scheduler iniciado.")
    print("[ARGUS SCHEDULER] Primeira atualização será executada imediatamente.")
    print("[ARGUS SCHEDULER] Depois disso, a atualização ocorrerá a cada 15 dias.")

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