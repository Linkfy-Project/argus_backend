from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import math
import re

import pandas as pd
from sqlalchemy.orm import Session

from app.models.work import PublicWork
from app.services.work_service import recompute_work
from app.utils.parsing import first_present


def clean_value(value):
    """
    Converte valores problemáticos do Pandas para None antes de salvar no banco.
    Resolve NaN, NaT, strings vazias e infinitos.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None

    if isinstance(value, str):
        value = value.strip()

        if value == "":
            return None

        if value.lower() in {"nan", "nat", "none", "null", "na", "n/a"}:
            return None

        return value

    return value


def clean_str(value) -> str | None:
    value = clean_value(value)

    if value is None:
        return None

    text = str(value).strip()

    if text == "":
        return None

    # Evita coisas como "26.0" quando vier de campo categórico/id.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    return text


def clean_float(value) -> float | None:
    value = clean_value(value)

    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()

        if text == "":
            return None

        text = (
            text.replace("R$", "")
            .replace("\xa0", "")
            .replace(" ", "")
            .strip()
        )

        # Formato brasileiro: 1.234.567,89
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        # Formato brasileiro simples: 1234,89
        elif "," in text:
            text = text.replace(",", ".")

        # Mantém apenas número, sinal e ponto decimal.
        text = re.sub(r"[^0-9.\-]", "", text)

        if text in {"", ".", "-", "-."}:
            return None

        value = text

    try:
        number = float(value)

        if math.isnan(number) or math.isinf(number):
            return None

        return number
    except Exception:
        return None


def clean_date(value) -> date | None:
    value = clean_value(value)

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if text == "":
        return None

    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

        if pd.isna(parsed):
            return None

        return parsed.date()
    except Exception:
        return None


def read_csv_flexible(path: Path) -> pd.DataFrame:
    """
    Lê CSV tentando detectar separador.
    Usa dtype=str e keep_default_na=False para evitar NaN/NaT entrando no fluxo.
    """

    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:5000]

    sep = ";" if sample.count(";") >= sample.count(",") else ","

    try:
        df = pd.read_csv(
            path,
            sep=sep,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
            low_memory=False,
        )
    except UnicodeDecodeError:
        df = pd.read_csv(
            path,
            sep=sep,
            encoding="latin1",
            dtype=str,
            keep_default_na=False,
            low_memory=False,
        )

    df.columns = [str(col).strip() for col in df.columns]

    return df


def build_external_id(
    source: str | None,
    contract_number: str | None,
    bidding_number: str | None,
    contractor_document: str | None,
    object_description: str | None,
) -> str | None:
    """
    Cria uma chave razoável para evitar duplicação.
    """

    parts = [
        clean_str(source),
        clean_str(contract_number),
        clean_str(bidding_number),
        clean_str(contractor_document),
    ]

    parts = [part for part in parts if part]

    if parts:
        return "|".join(parts)[:120]

    if object_description:
        return f"{source or 'csv'}|{object_description[:80]}"[:120]

    return None


def row_to_payload(row: dict, default_municipio: str = "Macae") -> dict:
    """
    Converte uma linha de CSV em um payload limpo para PublicWork.
    """

    obj = first_present(
        row,
        [
            "Objeto",
            "objeto",
            "dsobjeto",
            "DescricaoObjeto",
            "DescriçãoObjeto",
            "descricao",
            "Descrição",
            "Descricao",
        ],
    )

    contractor = first_present(
        row,
        [
            "Contratado",
            "contratado",
            "nmempresa",
            "Empresa",
            "empresa",
            "NomeContratado",
        ],
    )

    doc = first_present(
        row,
        [
            "CNPJCPFContratado",
            "cnpjcpfcontratado",
            "cnpj",
            "CNPJ",
            "cpf_cnpj",
            "CNPJ/CPF",
            "DocumentoContratado",
        ],
    )

    municipio = (
        first_present(
            row,
            [
                "municipio",
                "Município",
                "Municipio",
                "Ente",
                "ente",
                "nm_municipio",
                "nome_municipio",
            ],
        )
        or default_municipio
    )

    contract_number = first_present(
        row,
        [
            "Contrato",
            "nrcontrato",
            "NumeroContrato",
            "NúmeroContrato",
            "Nº Contrato",
            "Numero",
            "Número",
        ],
    )

    bidding_number = first_present(
        row,
        [
            "N° Licitação",
            "Nº Licitação",
            "nrlicitacao",
            "NumeroLicitacao",
            "NúmeroLicitação",
            "modalidaenumero",
        ],
    )

    source = first_present(row, ["fonte", "source", "Fonte"]) or "csv_import"

    contract_type = first_present(
        row,
        [
            "TipoContrato",
            "tipo_contrato",
            "idtipocontrato",
            "Tipo Contrato",
            "idtipolicitacao",
        ],
    )

    managing_unit = first_present(
        row,
        [
            "UnidadeGestora",
            "Unidade Gestora",
            "idunidadegestora",
            "idunidadegestoradireta",
            "idunidadegestoraindireta",
        ],
    )

    requesting_agency = first_present(
        row,
        [
            "Órgão Solicitante",
            "OrgaoSolicitante",
            "ÓrgãoSolicitante",
            "idorgaosolicitante",
            "idorgaosolicitanteindireta",
        ],
    )

    object_description = clean_str(obj)

    source_clean = clean_str(source) or "csv_import"
    contract_number_clean = clean_str(contract_number)
    bidding_number_clean = clean_str(bidding_number)
    contractor_document_clean = clean_str(doc)

    external_id = build_external_id(
        source=source_clean,
        contract_number=contract_number_clean,
        bidding_number=bidding_number_clean,
        contractor_document=contractor_document_clean,
        object_description=object_description,
    )

    payload = {
        "external_id": external_id,
        "source": source_clean,
        "municipio": clean_str(municipio) or default_municipio,
        "object_description": object_description or "",
        "contractor_name": clean_str(contractor),
        "contractor_document": contractor_document_clean,
        "contract_type": clean_str(contract_type),
        "contract_number": contract_number_clean,
        "bidding_number": bidding_number_clean,
        "managing_unit": clean_str(managing_unit),
        "requesting_agency": clean_str(requesting_agency),
        "contract_value": clean_float(
            first_present(
                row,
                [
                    "ValorContrato",
                    "Valor Contrato",
                    "Valor",
                    "nrvalor",
                    "valor_original",
                    "valor",
                ],
            )
        ),
        "committed_value": clean_float(
            first_present(row, ["ValorEmpenhado", "Valor Empenhado", "valor_empenhado"])
        ),
        "settled_value": clean_float(
            first_present(row, ["ValorLiquidado", "Valor Liquidado", "valor_liquidado"])
        ),
        "paid_value": clean_float(
            first_present(row, ["ValorPago", "Valor Pago", "valor_pago"])
        ),
        "additive_value": clean_float(
            first_present(row, ["Aditivo", "ValorAditivo", "Valor Aditivo", "valor_aditivo"])
        ),
        "area_m2": clean_float(
            first_present(row, ["area_m2", "Área", "Area", "area", "metragem"])
        ),
        "signed_at": clean_date(
            first_present(
                row,
                [
                    "DataAssinaturaContrato",
                    "Data Assinatura Contrato",
                    "DataAssinatura",
                    "dtlicitacao",
                    "data_assinatura",
                    "Data",
                ],
            )
        ),
        "due_at": clean_date(
            first_present(
                row,
                [
                    "DataVencimentoContrato",
                    "Data Vencimento Contrato",
                    "DataVencimento",
                    "data_vencimento",
                    "Vigência",
                    "Vigencia",
                ],
            )
        ),
        "finished_at": clean_date(
            first_present(
                row,
                [
                    "DataConclusao",
                    "DataConclusão",
                    "data_conclusao",
                    "finished_at",
                ],
            )
        ),
        "status": clean_str(
            first_present(row, ["status", "Status", "Situacao", "Situação"])
        ),
        "address": clean_str(
            first_present(row, ["Endereco", "Endereço", "address", "logradouro"])
        ),
        "neighborhood": clean_str(
            first_present(row, ["Bairro", "bairro", "neighborhood"])
        ),
        "latitude": clean_float(first_present(row, ["latitude", "lat", "Latitude"])),
        "longitude": clean_float(first_present(row, ["longitude", "lon", "lng", "Longitude"])),
        "idh": clean_float(first_present(row, ["idh", "IDH"])),
    }

    return payload


def find_existing_work(db: Session, payload: dict) -> PublicWork | None:
    """
    Busca obra existente para evitar duplicação em importações periódicas.
    """

    external_id = payload.get("external_id")
    source = payload.get("source")

    if external_id and source:
        existing = (
            db.query(PublicWork)
            .filter(PublicWork.external_id == external_id)
            .filter(PublicWork.source == source)
            .first()
        )

        if existing:
            return existing

    contract_number = payload.get("contract_number")
    contractor_document = payload.get("contractor_document")

    if contract_number and contractor_document:
        return (
            db.query(PublicWork)
            .filter(PublicWork.contract_number == contract_number)
            .filter(PublicWork.contractor_document == contractor_document)
            .first()
        )

    return None


def upsert_work(db: Session, payload: dict) -> tuple[PublicWork, bool]:
    """
    Cria ou atualiza uma obra.
    Retorna: (obra, created=True/False)
    """

    existing = find_existing_work(db, payload)

    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)

        return existing, False

    work = PublicWork(**payload)
    db.add(work)

    return work, True


def import_csv(
    db: Session,
    path: str | Path,
    default_municipio: str = "Macae",
    recompute: bool = True,
) -> dict:
    """
    Importa CSV para o banco com limpeza de NaN/NaT e proteção contra duplicidade.
    """

    p = Path(path)

    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {p}")

    df = read_csv_flexible(p)

    created_ids: list[int] = []
    updated_ids: list[int] = []
    errors: list[dict] = []

    for index, record in df.iterrows():
        row_number = int(index) + 2

        try:
            payload = row_to_payload(record.to_dict(), default_municipio=default_municipio)

            # Ignora linhas sem objeto e sem número de contrato/licitação.
            if not payload.get("object_description") and not payload.get("contract_number") and not payload.get("bidding_number"):
                errors.append(
                    {
                        "row": row_number,
                        "status": "skipped",
                        "reason": "Linha sem objeto, contrato ou licitação.",
                    }
                )
                continue

            work, created = upsert_work(db, payload)

            db.commit()
            db.refresh(work)

            if created:
                created_ids.append(work.id)
            else:
                updated_ids.append(work.id)

            if recompute:
                recompute_work(db, work.id)

        except Exception as exc:
            db.rollback()

            errors.append(
                {
                    "row": row_number,
                    "status": "error",
                    "error": str(exc),
                }
            )

    return {
        "path": str(p),
        "created": len(created_ids),
        "updated": len(updated_ids),
        "errors": len([error for error in errors if error.get("status") == "error"]),
        "skipped": len([error for error in errors if error.get("status") == "skipped"]),
        "created_ids_preview": created_ids[:20],
        "updated_ids_preview": updated_ids[:20],
        "errors_preview": errors[:20],
        "preview_limit": 20,
    }