from __future__ import annotations
from pathlib import Path
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/octet-stream,text/plain,*/*",
    "Referer": "https://transparencia.macae.rj.gov.br/",
}

LICITACOES_URL = "https://transparencia.macae.rj.gov.br/default/contratacoes/gerarcsvlicitacoes?draw=1&start=0&length=-1&check-adm%5B%5D=direta&check-adm%5B%5D=indireta&idunidadegestoradireta=1&idunidadegestoraindireta=2"
CONTRATOS_URL = "https://transparencia.macae.rj.gov.br/default/contratacoes/gerarcsvcontratos?draw=1&start=0&length=-1&check-adm%5B%5D=direta&check-adm%5B%5D=indireta&idunidadegestoradireta=1&idunidadegestoraindireta=2"


def _valid_csv(text: str) -> bool:
    return bool(text and any(";" in line for line in text.splitlines()[:5]))


def _download(url: str, dest: Path, session: requests.Session) -> None:
    response = session.get(url, timeout=120)
    response.raise_for_status()
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        text = response.content.decode("latin1")
    if not _valid_csv(text):
        raise ValueError(f"Conteúdo inválido para {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8-sig")


def update_macae_portal(out_dir: str | Path = "data/raw/macae") -> dict:
    out = Path(out_dir)
    with requests.Session() as session:
        session.headers.update(HEADERS)
        lic = out / "licitacoes.csv"
        con = out / "contratos.csv"
        _download(LICITACOES_URL, lic, session)
        _download(CONTRATOS_URL, con, session)
    return {"files": {"licitacoes": str(lic), "contratos": str(con)}}
