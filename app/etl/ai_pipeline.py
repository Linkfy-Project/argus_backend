"""
Pipeline de classificação de descrições de obras públicas via OpenRouter.

Este módulo processa as descrições dos objetos de public_works usando um modelo
de IA via API do OpenRouter para:
1. Classificar se é uma obra (construção civil) ou não.
2. Extrair informações de endereço para geocodificação.

Os resultados são armazenados na tabela model_cache (nunca excluída) e o campo
description_hash é atualizado em public_works.

Uso:
    from app.etl.ai_pipeline import run_ai_pipeline
    stats = run_ai_pipeline(db)
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.work import ModelCache, PublicWork


def _load_system_prompt() -> str:
    """Carrega o prompt de sistema do arquivo system_prompt.txt na raiz do backend."""
    prompt_path = Path(__file__).resolve().parent.parent.parent / "system_prompt.txt"
    print(f"DEBUG: [AI PIPELINE] Carregando system prompt de: {prompt_path}")
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Arquivo system_prompt.txt não encontrado em: {prompt_path}"
        )
    return prompt_path.read_text(encoding="utf-8").strip()


def _compute_sha256(text_content: str) -> str:
    """Calcula o hash SHA-256 de uma string de texto."""
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


def backfill_description_hashes(db: Session) -> dict:
    """
    Preenche o campo description_hash em public_works para registros que ainda não têm.
    Esta função deve ser chamada SEMPRE, independentemente da API key do OpenRouter.
    O hash é calculado via SHA-256 do object_description.

    Args:
        db: Sessão do banco de dados.

    Returns:
        Dicionário com estatísticas do backfill.
    """
    print(f"DEBUG: [AI PIPELINE] ▶ Backfill de description_hash em public_works...")

    # Busca registros sem hash
    rows = db.execute(
        text(
            "SELECT id, object_description FROM public_works "
            "WHERE object_description IS NOT NULL "
            "AND object_description != '' "
            "AND (description_hash IS NULL OR description_hash = '')"
        )
    ).fetchall()

    total = len(rows)
    print(f"DEBUG: [AI PIPELINE]   Registros sem hash: {total}")

    if total == 0:
        print(f"DEBUG: [AI PIPELINE]   ✔ Todos os registros já possuem hash")
        return {"status": "ok", "updated": 0}

    updated = 0
    for row in rows:
        work_id = row[0]
        desc = row[1]
        desc_hash = _compute_sha256(desc)
        db.execute(
            text("UPDATE public_works SET description_hash = :hash WHERE id = :id"),
            {"hash": desc_hash, "id": work_id},
        )
        updated += 1

    db.commit()
    print(f"DEBUG: [AI PIPELINE]   ✔ Hashes atualizados: {updated}/{total}")
    return {"status": "ok", "updated": updated}


def _call_openrouter(
    system_prompt: str,
    user_message: str,
    model_id: str,
    provider: str,
    api_key: str,
    base_url: str,
    timeout: int,
) -> dict:
    """
    Faz a chamada à API do OpenRouter para classificar uma descrição.

    Args:
        system_prompt: Prompt de sistema carregado do arquivo.
        user_message: Descrição do objeto a ser classificada.
        model_id: ID do modelo no OpenRouter.
        provider: Slug do provedor preferido (ex: "Groq", "Together", etc).
        api_key: Chave de API do OpenRouter.
        base_url: URL base da API do OpenRouter.
        timeout: Timeout em segundos para a requisição.

    Returns:
        Dicionário com a resposta parseada do modelo.

    Raises:
        Exception: Se a chamada falhar ou a resposta não for JSON válido.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://argus-monitoramento.com.br",
        "X-Title": "ARGUS Pipeline de Obras",
    }

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        # Força o provedor específico (ex: Groq para alta velocidade)
        "provider": {
            "order": [provider],
            "allow_fallbacks": True,
        },
    }

    # Faz a requisição HTTP para o OpenRouter
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    data = response.json()

    # Extrai o conteúdo da resposta do modelo
    content = data["choices"][0]["message"]["content"].strip()

    # Tenta fazer parse do JSON retornado pelo modelo
    # Remove possíveis marcadores de código markdown
    if content.startswith("```"):
        # Remove ```json e ``` do início e fim
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(content)


def _process_single_description(
    system_prompt: str,
    description: str,
    description_hash: str,
    model_id: str,
    provider: str,
    api_key: str,
    base_url: str,
    timeout: int,
) -> dict:
    """
    Processa uma única descrição: chama o modelo e retorna o resultado.

    Args:
        system_prompt: Prompt de sistema.
        description: Descrição do objeto.
        description_hash: Hash SHA-256 da descrição.
        model_id: ID do modelo.
        provider: Slug do provedor preferido no OpenRouter (ex: "Groq").
        api_key: Chave de API.
        base_url: URL base da API.
        timeout: Timeout em segundos.

    Returns:
        Dicionário com hash, status, resultado ou erro.
    """
    try:
        # Monta a mensagem do usuário com a descrição
        user_message = f"Classifique a seguinte descrição de objeto público:\n\n{description}"

        # Chama o modelo via OpenRouter
        result = _call_openrouter(
            system_prompt=system_prompt,
            user_message=user_message,
            model_id=model_id,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

        return {
            "description_hash": description_hash,
            "description": description,
            "status": "ok",
            "result": result,
            "raw_response": json.dumps(result, ensure_ascii=False),
        }

    except json.JSONDecodeError as exc:
        # Modelo retornou algo que não é JSON válido
        return {
            "description_hash": description_hash,
            "description": description,
            "status": "error",
            "error": f"Resposta não é JSON válido: {exc}",
            "raw_response": None,
        }
    except httpx.HTTPStatusError as exc:
        # Erro HTTP da API
        return {
            "description_hash": description_hash,
            "description": description,
            "status": "error",
            "error": f"Erro HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            "raw_response": None,
        }
    except Exception as exc:
        # Qualquer outro erro
        return {
            "description_hash": description_hash,
            "description": description,
            "status": "error",
            "error": str(exc),
            "raw_response": None,
        }


def run_ai_pipeline(db: Session) -> dict:
    """
    Executa a pipeline de IA para classificar todas as descrições de public_works.

    Fluxo:
    1. Carrega o system_prompt.txt.
    2. Busca todas as descrições distintas de public_works que ainda não estão em model_cache.
    3. Para cada descrição, chama o modelo via OpenRouter (com processamento paralelo).
    4. Armazena os resultados em model_cache.
    5. Atualiza description_hash em public_works.

    Args:
        db: Sessão do banco de dados.

    Returns:
        Dicionário com estatísticas do processamento.
    """
    settings = get_settings()
    started_at = datetime.now()

    print(f"DEBUG: [AI PIPELINE] ===============================================")
    print(f"DEBUG: [AI PIPELINE] INICIANDO PIPELINE DE IA")
    print(f"DEBUG: [AI PIPELINE]   Modelo:     {settings.OPENROUTER_MODEL_ID}")
    print(f"DEBUG: [AI PIPELINE]   Provider:   {settings.OPENROUTER_PROVIDER}")
    print(f"DEBUG: [AI PIPELINE]   Workers:    {settings.AI_PIPELINE_MAX_WORKERS}")
    print(f"DEBUG: [AI PIPELINE]   Timeout:    {settings.AI_PIPELINE_TIMEOUT}s")
    print(f"DEBUG: [AI PIPELINE]   Início:     {started_at.isoformat()}")
    print(f"DEBUG: [AI PIPELINE] ===============================================")

    # Verifica se a chave de API está configurada
    if not settings.OPENROUTER_API_KEY:
        print(f"DEBUG: [AI PIPELINE] ⚠ OPENROUTER_API_KEY não configurada. Pulando pipeline.")
        return {
            "status": "skipped",
            "reason": "OPENROUTER_API_KEY não configurada",
            "started_at": started_at.isoformat(),
        }

    # Carrega o system prompt
    try:
        system_prompt = _load_system_prompt()
        print(f"DEBUG: [AI PIPELINE] ✔ System prompt carregado ({len(system_prompt)} caracteres)")
    except FileNotFoundError as exc:
        print(f"DEBUG: [AI PIPELINE] ✘ Erro ao carregar system prompt: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "started_at": started_at.isoformat(),
        }

    # Busca descrições distintas de public_works que ainda não foram processadas
    # Primeiro, busca todos os registros que não têm description_hash
    print(f"DEBUG: [AI PIPELINE] ▶ Buscando descrições não processadas...")

    # Busca descrições distintas que não estão no model_cache
    query = text("""
        SELECT DISTINCT pw.object_description
        FROM public_works pw
        WHERE pw.object_description IS NOT NULL
          AND pw.object_description != ''
          AND NOT EXISTS (
              SELECT 1 FROM model_cache mc
              WHERE mc.description_hash = pw.description_hash
          )
    """)

    rows = db.execute(query).fetchall()
    descriptions = [row[0] for row in rows]

    # Aplica o limite configurado (útil para testes)
    # AI_PIPELINE_LIMIT=0 significa sem limite (processa tudo)
    if settings.AI_PIPELINE_LIMIT > 0:
        descriptions = descriptions[: settings.AI_PIPELINE_LIMIT]
        print(
            f"DEBUG: [AI PIPELINE]   ⚠ LIMITE ATIVO: processando apenas "
            f"{settings.AI_PIPELINE_LIMIT} descrições (AI_PIPELINE_LIMIT)"
        )

    total_to_process = len(descriptions)
    print(f"DEBUG: [AI PIPELINE]   Total de descrições para processar: {total_to_process}")

    if total_to_process == 0:
        # Verifica quantas já estão no cache
        cached_count = db.execute(text("SELECT COUNT(*) FROM model_cache")).scalar()
        print(f"DEBUG: [AI PIPELINE] ✔ Todas as descrições já estão no cache ({cached_count} entradas)")
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds()
        return {
            "status": "ok",
            "total_descriptions": 0,
            "cached": cached_count,
            "processed": 0,
            "errors": 0,
            "duration_seconds": duration,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }

    # Prepara a lista de tarefas com hash e descrição
    # (os hashes já foram preenchidos pelo backfill_description_hashes)
    tasks = []
    for desc in descriptions:
        desc_hash = _compute_sha256(desc)
        tasks.append((desc, desc_hash))

    # Processa as descrições com ThreadPoolExecutor para paralelismo
    processed_count = 0
    error_count = 0
    cached_count = 0
    results_to_save = []

    print(f"DEBUG: [AI PIPELINE] ▶ Processando {total_to_process} descrições com {settings.AI_PIPELINE_MAX_WORKERS} workers...")
    print(f"DEBUG: [AI PIPELINE] ─────────────────────────────────────────────")

    with ThreadPoolExecutor(max_workers=settings.AI_PIPELINE_MAX_WORKERS) as executor:
        # Submete todas as tarefas
        future_to_task = {}
        for desc, desc_hash in tasks:
            future = executor.submit(
                _process_single_description,
                system_prompt=system_prompt,
                description=desc,
                description_hash=desc_hash,
                model_id=settings.OPENROUTER_MODEL_ID,
                provider=settings.OPENROUTER_PROVIDER,
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
                timeout=settings.AI_PIPELINE_TIMEOUT,
            )
            future_to_task[future] = (desc, desc_hash)

        # Coleta os resultados conforme ficam prontos
        for i, future in enumerate(as_completed(future_to_task), 1):
            desc, desc_hash = future_to_task[future]
            try:
                result = future.result()
                results_to_save.append(result)

                if result["status"] == "ok":
                    processed_count += 1
                    is_obra = result["result"].get("is_obra", None)
                    print(
                        f"DEBUG: [AI PIPELINE]   [{i}/{total_to_process}] ✔ hash={desc_hash[:12]}... | "
                        f"is_obra={is_obra} | desc={desc[:60]}..."
                    )
                else:
                    error_count += 1
                    print(
                        f"DEBUG: [AI PIPELINE]   [{i}/{total_to_process}] ✘ hash={desc_hash[:12]}... | "
                        f"Erro: {result.get('error', 'desconhecido')[:80]}"
                    )
            except Exception as exc:
                error_count += 1
                print(
                    f"DEBUG: [AI PIPELINE]   [{i}/{total_to_process}] ✘ hash={desc_hash[:12]}... | "
                    f"Exceção: {str(exc)[:80]}"
                )
                results_to_save.append({
                    "description_hash": desc_hash,
                    "description": desc,
                    "status": "error",
                    "error": str(exc),
                    "raw_response": None,
                })

            # Log de progresso a cada 10 itens
            if i % 10 == 0:
                elapsed = (datetime.now() - started_at).total_seconds()
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total_to_process - i) / rate if rate > 0 else 0
                print(
                    f"DEBUG: [AI PIPELINE]   ── Progresso: {i}/{total_to_process} "
                    f"({i * 100 // total_to_process}%) | "
                    f"Sucesso: {processed_count} | Erros: {error_count} | "
                    f"Velocidade: {rate:.1f}/s | ETA: {eta:.0f}s"
                )

    # Salva todos os resultados no model_cache
    print(f"DEBUG: [AI PIPELINE] ─────────────────────────────────────────────")
    print(f"DEBUG: [AI PIPELINE] ▶ Salvando {len(results_to_save)} resultados no model_cache...")

    saved_count = 0
    for result_data in results_to_save:
        try:
            # Prepara os dados para inserção — colunas individuais
            is_obra_value = 0
            local_value = None
            cidade_value = None
            estado_value = None
            extracao_value = None  # endereco_geocoding

            if result_data["status"] == "ok" and result_data.get("result"):
                res = result_data["result"]
                is_obra_value = 1 if res.get("is_obra", False) else 0
                end = res.get("extracao_endereco", {})
                local_value = end.get("local") or None
                cidade_value = end.get("cidade") or None
                estado_value = end.get("estado") or None
                extracao_value = end.get("endereco_geocoding") or None

            # Verifica se já existe no cache (pode ter sido inserido por outra execução)
            existing = db.execute(
                text("SELECT 1 FROM model_cache WHERE description_hash = :hash"),
                {"hash": result_data["description_hash"]},
            ).fetchone()

            if existing:
                # Atualiza registro existente
                db.execute(
                    text(
                        "UPDATE model_cache SET "
                        "is_obra = :is_obra, "
                        "local = :local, "
                        "cidade = :cidade, "
                        "estado = :estado, "
                        "extracao_endereco = :extracao, "
                        "model_id = :model_id, "
                        "raw_response = :raw, "
                        "status = :status, "
                        "error_message = :error, "
                        "updated_at = :now "
                        "WHERE description_hash = :hash"
                    ),
                    {
                        "is_obra": is_obra_value,
                        "local": local_value,
                        "cidade": cidade_value,
                        "estado": estado_value,
                        "extracao": extracao_value,
                        "model_id": settings.OPENROUTER_MODEL_ID,
                        "raw": result_data.get("raw_response"),
                        "status": result_data["status"],
                        "error": result_data.get("error"),
                        "now": datetime.utcnow(),
                        "hash": result_data["description_hash"],
                    },
                )
            else:
                # Insere novo registro
                db.execute(
                    text(
                        "INSERT INTO model_cache "
                        "(description_hash, object_description, is_obra, local, cidade, estado, "
                        "extracao_endereco, model_id, raw_response, status, error_message, "
                        "created_at, updated_at) "
                        "VALUES (:hash, :desc, :is_obra, :local, :cidade, :estado, "
                        ":extracao, :model_id, :raw, :status, :error, :now, :now)"
                    ),
                    {
                        "hash": result_data["description_hash"],
                        "desc": result_data.get("description", ""),
                        "is_obra": is_obra_value,
                        "local": local_value,
                        "cidade": cidade_value,
                        "estado": estado_value,
                        "extracao": extracao_value,
                        "model_id": settings.OPENROUTER_MODEL_ID,
                        "raw": result_data.get("raw_response"),
                        "status": result_data["status"],
                        "error": result_data.get("error"),
                        "now": datetime.utcnow(),
                    },
                )

            saved_count += 1

        except Exception as exc:
            print(
                f"DEBUG: [AI PIPELINE]   ✘ Erro ao salvar cache para hash "
                f"{result_data['description_hash'][:12]}...: {exc}"
            )

    db.commit()
    print(f"DEBUG: [AI PIPELINE]   ✔ Resultados salvos no model_cache: {saved_count}")

    # Estatísticas finais
    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds()

    stats = {
        "status": "ok",
        "total_descriptions": total_to_process,
        "processed": processed_count,
        "errors": error_count,
        "cached": saved_count,
        "duration_seconds": duration,
        "model_id": settings.OPENROUTER_MODEL_ID,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }

    print(f"DEBUG: [AI PIPELINE] ===============================================")
    print(f"DEBUG: [AI PIPELINE] PIPELINE DE IA CONCLUÍDA")
    print(f"DEBUG: [AI PIPELINE]   Status:         {stats['status']}")
    print(f"DEBUG: [AI PIPELINE]   Total:          {stats['total_descriptions']}")
    print(f"DEBUG: [AI PIPELINE]   Processados OK: {stats['processed']}")
    print(f"DEBUG: [AI PIPELINE]   Erros:          {stats['errors']}")
    print(f"DEBUG: [AI PIPELINE]   Salvos em cache: {stats['cached']}")
    print(f"DEBUG: [AI PIPELINE]   Duração:        {duration:.2f}s")
    if processed_count > 0:
        print(f"DEBUG: [AI PIPELINE]   Velocidade média: {processed_count / duration:.1f} desc/s")
    print(f"DEBUG: [AI PIPELINE] ===============================================")

    return stats
