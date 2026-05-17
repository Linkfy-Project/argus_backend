from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
import pandas as pd
import requests
from app.core.config import get_settings

PALAVRAS_OBRAS = (
    r"obra|obras|reforma|reformas|constru[cç][aã]o|construir|engenharia|"
    r"pavimenta[cç][aã]o|drenagem|infraestrutura|urbaniza[cç][aã]o|"
    r"manuten[cç][aã]o predial|ponte|escola|creche|posto de sa[úu]de|"
    r"hospital|unidade b[aá]sica|pra[çc]a|quadra|cal[çc]amento|asfalto"
)

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/csv,*/*"}


def normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str).str.strip()
    return df


def _registros(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def _signature(df: pd.DataFrame) -> str:
    return hashlib.md5(df.astype(str).fillna("").to_csv(index=False).encode("utf-8")).hexdigest()


def get_json(endpoint: str, params: dict) -> list | dict:
    settings = get_settings()
    url = f"{settings.TCE_BASE_URL}/{endpoint}"
    response = requests.get(url, params=params, headers=HEADERS, timeout=120)
    response.raise_for_status()
    text = response.content.decode("utf-8-sig", errors="replace").strip()
    if text.startswith("<"):
        raise ValueError(f"A API retornou HTML para {url}")
    return json.loads(text)


def fetch_paginated(endpoint: str, municipio: str, ano: int | None = None, limite: int = 1000, max_paginas: int = 200) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    seen_pages: set[str] = set()
    inicio = 0
    for _ in range(max_paginas):
        params = {"inicio": inicio, "limite": limite, "jsonfull": "true", "municipio": municipio}
        if ano:
            params["ano"] = ano
        data = get_json(endpoint, params)
        records = _registros(data)
        if not records:
            break
        part = normalizar_colunas(pd.DataFrame(records))
        sig = _signature(part)
        if sig in seen_pages:
            break
        seen_pages.add(sig)
        frames.append(part)
        if len(part) < limite:
            break
        inicio += limite
        time.sleep(0.2)
    return normalizar_colunas(pd.concat(frames, ignore_index=True).drop_duplicates()) if frames else pd.DataFrame()


def fetch_single(endpoint: str, ano: int | None = None) -> pd.DataFrame:
    params = {"jsonfull": "true"}
    if ano:
        params["ano"] = ano
    return normalizar_colunas(pd.DataFrame(_registros(get_json(endpoint, params))))


def filter_works(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [c for c in df.columns if df[c].dtype == "object"]
    mask = pd.Series(False, index=df.index)
    for col in cols:
        mask = mask | df[col].astype(str).str.contains(PALAVRAS_OBRAS, case=False, regex=True, na=False)
    return df[mask].copy()


def extract_tcerj(municipio: str = "Macae", ano: int | None = None, out_dir: str | Path = "data/raw/tcerj") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lic = fetch_paginated("licitacoes", municipio=municipio, ano=ano)
    contratos = fetch_paginated("contratos_municipio", municipio=municipio, ano=ano)
    paralisadas = fetch_single("obras_paralisadas", ano=ano)
    if not paralisadas.empty and "municipio" in [c.lower() for c in paralisadas.columns]:
        col = next(c for c in paralisadas.columns if c.lower() == "municipio")
        paralisadas = paralisadas[paralisadas[col].astype(str).str.lower().str.contains("macae|macaé", regex=True, na=False)]

    lic_obras = filter_works(lic); lic_obras["fonte"] = "licitacoes"
    contratos_obras = filter_works(contratos); contratos_obras["fonte"] = "contratos_municipio"
    paralisadas["fonte"] = "obras_paralisadas"
    consolidado = pd.concat([lic_obras, contratos_obras, paralisadas], ignore_index=True, sort=False).drop_duplicates()

    files = {
        "licitacoes_raw": out / "licitacoes_raw.csv",
        "contratos_raw": out / "contratos_raw.csv",
        "obras_paralisadas_raw": out / "obras_paralisadas_raw.csv",
        "obras_consolidado": out / "obras_consolidado.csv",
    }
    lic.to_csv(files["licitacoes_raw"], index=False, encoding="utf-8-sig")
    contratos.to_csv(files["contratos_raw"], index=False, encoding="utf-8-sig")
    paralisadas.to_csv(files["obras_paralisadas_raw"], index=False, encoding="utf-8-sig")
    consolidado.to_csv(files["obras_consolidado"], index=False, encoding="utf-8-sig")
    return {"rows": len(consolidado), "files": {k: str(v) for k, v in files.items()}}
