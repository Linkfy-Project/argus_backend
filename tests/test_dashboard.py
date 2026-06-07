"""
Testes básicos para o Dashboard Executivo do ARGUS.

Testa os endpoints de dashboard e health check para garantir
que retornam status 200 e a estrutura esperada mesmo sem dados.
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    """Testa o endpoint de health check."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "service" in data
    assert "version" in data
    assert "db_status" in data
    assert "timestamp" in data


def test_dashboard_summary_returns_200():
    """Testa que /dashboard/summary retorna 200 com estrutura correta."""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    data = response.json()

    # Verifica campos obrigatórios
    assert "municipio" in data
    assert "ultima_atualizacao" in data
    assert "obras_monitoradas" in data
    assert "valor_total_contratado" in data
    assert "valor_total_pago" in data
    assert "valor_potencial_em_risco" in data
    assert "obras_criticas" in data
    assert "obras_alto_risco" in data
    assert "obras_em_atencao" in data
    assert "obras_eficientes" in data
    assert "obras_atrasadas" in data
    assert "obras_sem_geolocalizacao" in data
    assert "contratos_com_aditivos_altos" in data
    assert "alertas_criticos" in data
    assert "alertas_totais" in data
    assert "fornecedores_monitorados" in data
    assert "bairros_monitorados" in data
    assert "score_medio" in data
    assert "data_quality_score" in data


def test_dashboard_summary_with_municipio_param():
    """Testa que /dashboard/summary aceita parâmetro municipio."""
    response = client.get("/api/v1/dashboard/summary?municipio=Macae")
    assert response.status_code == 200
    data = response.json()
    assert "municipio" in data


def test_dashboard_summary_with_accent_normalization():
    """Testa normalização de acento: 'Macae' deve encontrar 'Macaé'."""
    response_sem_acento = client.get("/api/v1/dashboard/summary?municipio=Macae")
    response_com_acento = client.get("/api/v1/dashboard/summary?municipio=Macaé")
    assert response_sem_acento.status_code == 200
    assert response_com_acento.status_code == 200
    # Ambos devem retornar o mesmo município canônico
    assert response_sem_acento.json()["municipio"] == response_com_acento.json()["municipio"]


def test_dashboard_summary_no_data_returns_zeros():
    """Testa que sem dados o endpoint retorna zeros, não erro 500."""
    response = client.get("/api/v1/dashboard/summary?municipio=MunicipioInexistente123")
    assert response.status_code == 200
    data = response.json()
    assert data["obras_monitoradas"] == 0
    assert data["valor_total_contratado"] == 0.0
    assert data["obras_criticas"] == 0
    assert data["score_medio"] == 0.0


def test_dashboard_priority_queue_returns_200():
    """Testa que /dashboard/priority-queue retorna 200 com lista."""
    response = client.get("/api/v1/dashboard/priority-queue")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_dashboard_priority_queue_with_limit():
    """Testa que /dashboard/priority-queue aceita parâmetro limit."""
    response = client.get("/api/v1/dashboard/priority-queue?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 5


def test_dashboard_priority_queue_item_structure():
    """Testa a estrutura de cada item da fila priorizada."""
    response = client.get("/api/v1/dashboard/priority-queue?limit=1")
    assert response.status_code == 200
    data = response.json()
    if len(data) > 0:
        item = data[0]
        assert "prioridade" in item
        assert "obra_id" in item
        assert "obra" in item
        assert "bairro" in item
        assert "secretaria" in item
        assert "fornecedor" in item
        assert "score_argus" in item
        assert "classificacao_risco" in item
        assert "valor_contratado" in item
        assert "valor_em_risco_estimado" in item
        assert "dias_atraso" in item
        assert "alertas_ativos" in item
        assert "motivo_principal" in item
        assert "acao_sugerida" in item


def test_dashboard_risk_distribution_returns_200():
    """Testa que /dashboard/risk-distribution retorna 200 com 5 faixas."""
    response = client.get("/api/v1/dashboard/risk-distribution")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5  # Eficiente, Atenção, Alto risco, Crítico, Sem score


def test_dashboard_risk_distribution_labels():
    """Testa que as faixas de risco têm os labels corretos."""
    response = client.get("/api/v1/dashboard/risk-distribution")
    assert response.status_code == 200
    data = response.json()
    labels = [item["label"] for item in data]
    assert "Eficiente" in labels
    assert "Atenção" in labels
    assert "Alto risco" in labels
    assert "Crítico" in labels
    assert "Sem score" in labels


def test_dashboard_top_neighborhoods_risk_returns_200():
    """Testa que /dashboard/top-neighborhoods-risk retorna 200 com lista."""
    response = client.get("/api/v1/dashboard/top-neighborhoods-risk")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_dashboard_top_neighborhoods_risk_item_structure():
    """Testa a estrutura de cada item do ranking de bairros."""
    response = client.get("/api/v1/dashboard/top-neighborhoods-risk?limit=1")
    assert response.status_code == 200
    data = response.json()
    if len(data) > 0:
        item = data[0]
        assert "bairro" in item
        assert "obras" in item
        assert "score_medio" in item
        assert "obras_criticas" in item
        assert "obras_atrasadas" in item
        assert "valor_total" in item
        assert "alertas" in item
        assert "classificacao" in item
        assert "recomendacao" in item


def test_dashboard_top_suppliers_risk_returns_200():
    """Testa que /dashboard/top-suppliers-risk retorna 200 com lista."""
    response = client.get("/api/v1/dashboard/top-suppliers-risk")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_dashboard_top_suppliers_risk_item_structure():
    """Testa a estrutura de cada item do ranking de fornecedores."""
    response = client.get("/api/v1/dashboard/top-suppliers-risk?limit=1")
    assert response.status_code == 200
    data = response.json()
    if len(data) > 0:
        item = data[0]
        assert "fornecedor" in item
        assert "cnpj" in item
        assert "contratos" in item
        assert "valor_total" in item
        assert "score_medio" in item
        assert "obras_criticas" in item
        assert "alertas" in item
        assert "aditivo_medio_percentual" in item
        assert "classificacao" in item
        assert "recomendacao" in item


def test_dashboard_no_500_on_empty_municipio():
    """Testa que nenhum endpoint de dashboard retorna 500 para município inexistente."""
    endpoints = [
        "/api/v1/dashboard/summary?municipio=XyzInexistente",
        "/api/v1/dashboard/priority-queue?municipio=XyzInexistente",
        "/api/v1/dashboard/risk-distribution?municipio=XyzInexistente",
        "/api/v1/dashboard/top-neighborhoods-risk?municipio=XyzInexistente",
        "/api/v1/dashboard/top-suppliers-risk?municipio=XyzInexistente",
    ]
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, f"Endpoint {endpoint} retornou {response.status_code}"


def test_existing_endpoints_not_broken():
    """Testa que endpoints existentes não foram quebrados."""
    # Health
    response = client.get("/health")
    assert response.status_code == 200

    # Analytics summary
    response = client.get("/api/v1/analytics/summary")
    assert response.status_code == 200

    # Works list
    response = client.get("/api/v1/works")
    assert response.status_code == 200
