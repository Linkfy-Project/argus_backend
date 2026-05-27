"""
Script de validacao dos dados importados no banco ARGUS.

Uso:
    python scripts/validate_data.py

Retorna:
    Relatorio com contagens, estatisticas e verificacoes de integridade.
"""

import sqlite3
from pathlib import Path


def validate():
    db_path = Path("argus.db")
    if not db_path.exists():
        print(f"ERRO: Banco nao encontrado em {db_path.absolute()}")
        return

    print("=== VALIDACAO DOS DADOS NO SQLITE ===")
    print()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. Contagem total
    cur.execute("SELECT COUNT(*) FROM public_works")
    total = cur.fetchone()[0]
    print(f"[OK] Total de obras: {total}")

    # 2. Obras com score vs sem score
    cur.execute("""
        SELECT 
            COUNT(CASE WHEN efficiency_score IS NOT NULL THEN 1 END) as com_score,
            COUNT(CASE WHEN efficiency_score IS NULL THEN 1 END) as sem_score
        FROM public_works
    """)
    com, sem = cur.fetchone()
    print(f"[OK] Com efficiency_score: {com}")
    if sem > 0:
        print(f"[ERRO] Sem efficiency_score: {sem}")
    else:
        print(f"[OK] Sem efficiency_score: {sem}")

    # 3. Estatisticas dos scores
    cur.execute("""
        SELECT 
            ROUND(AVG(efficiency_score),2) as media,
            ROUND(MIN(efficiency_score),2) as min,
            ROUND(MAX(efficiency_score),2) as max
        FROM public_works 
        WHERE efficiency_score IS NOT NULL
    """)
    media, min_s, max_s = cur.fetchone()
    print(f"[INFO] Score medio: {media}")
    print(f"[INFO] Score minimo: {min_s}")
    print(f"[INFO] Score maximo: {max_s}")

    # 4. Riscos
    cur.execute("""
        SELECT 
            ROUND(AVG(risk_delay_probability),4),
            ROUND(AVG(risk_cost_probability),4),
            ROUND(AVG(risk_rework_probability),4)
        FROM public_works
        WHERE risk_delay_probability IS NOT NULL
    """)
    delay, cost, rework = cur.fetchone()
    print(f"[INFO] Risco medio de atraso: {delay}")
    print(f"[INFO] Risco medio de estouro de custo: {cost}")
    print(f"[INFO] Risco medio de retrabalho: {rework}")

    # 5. Obras sem objeto
    cur.execute("SELECT COUNT(*) FROM public_works WHERE object_description IS NULL OR object_description = ''")
    sem_objeto = cur.fetchone()[0]
    if sem_objeto > 0:
        print(f"[ALERTA] Obras sem object_description: {sem_objeto}")
    else:
        print(f"[OK] Obras sem object_description: {sem_objeto}")

    # 6. Obras sem contractor_document
    cur.execute("SELECT COUNT(*) FROM public_works WHERE contractor_document IS NULL OR contractor_document = ''")
    sem_doc = cur.fetchone()[0]
    print(f"[INFO] Obras sem contractor_document: {sem_doc}")

    # 7. Distribuicao por faixa de score
    cur.execute("""
        SELECT 
            CASE 
                WHEN efficiency_score >= 80 THEN '80-100 (Excelente)'
                WHEN efficiency_score >= 60 THEN '60-79 (Bom)'
                WHEN efficiency_score >= 40 THEN '40-59 (Regular)'
                WHEN efficiency_score >= 20 THEN '20-39 (Ruim)'
                WHEN efficiency_score >= 0 THEN '0-19 (Critico)'
                ELSE 'Sem score'
            END as faixa,
            COUNT(*) as qtd
        FROM public_works
        GROUP BY faixa
        ORDER BY faixa
    """)
    print()
    print("Distribuicao dos scores:")
    for faixa, qtd in cur.fetchall():
        print(f"  {faixa}: {qtd}")

    # 8. Alertas por severidade
    cur.execute("SELECT severity, COUNT(*) FROM alerts GROUP BY severity ORDER BY severity")
    print()
    print("Alertas por severidade:")
    for sev, qtd in cur.fetchall():
        print(f"  {sev}: {qtd}")

    # 9. Total de alerts
    cur.execute("SELECT COUNT(*) FROM alerts")
    total_alerts = cur.fetchone()[0]
    print(f"Total de alerts: {total_alerts}")

    # 10. Camadas geoespaciais
    cur.execute("SELECT layer_type, COUNT(*) FROM geo_layers GROUP BY layer_type")
    print()
    print("Camadas geoespaciais:")
    for layer, qtd in cur.fetchall():
        print(f"  {layer}: {qtd} registros")

    # 11. Top 5 maiores riscos de atraso
    cur.execute("""
        SELECT id, substr(object_description,1,50) as obj, 
               ROUND(efficiency_score,2) as score, 
               ROUND(risk_delay_probability,4) as risco
        FROM public_works 
        ORDER BY risk_delay_probability DESC 
        LIMIT 5
    """)
    print()
    print("Top 5 obras com maior risco de atraso:")
    for id_, obj, score, risco in cur.fetchall():
        print(f"  ID {id_}: score={score} | risco_atraso={risco} | {obj}...")

    # 12. Top 5 piores scores
    cur.execute("""
        SELECT id, substr(object_description,1,50) as obj, 
               ROUND(efficiency_score,2) as score
        FROM public_works 
        WHERE efficiency_score IS NOT NULL
        ORDER BY efficiency_score ASC 
        LIMIT 5
    """)
    print()
    print("Top 5 piores scores:")
    for id_, obj, score in cur.fetchall():
        print(f"  ID {id_}: score={score} | {obj}...")

    # 13. Obras por municipio
    cur.execute("SELECT municipio, COUNT(*) FROM public_works GROUP BY municipio ORDER BY COUNT(*) DESC LIMIT 5")
    print()
    print("Obras por municipio (top 5):")
    for mun, qtd in cur.fetchall():
        print(f"  {mun}: {qtd}")

    conn.close()
    print()
    print("=== VALIDACAO CONCLUIDA ===")


if __name__ == "__main__":
    validate()
