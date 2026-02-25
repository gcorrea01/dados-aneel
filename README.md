# Monitor de Tarifa Residencial (ANEEL x IPCA)

Este projeto coleta tarifas residenciais (`B1`, modalidade `Convencional`) por distribuidora na API aberta da ANEEL e compara os últimos 5 anos com o IPCA (IBGE SIDRA).

## O que ele gera

- `output/historico/<distribuidora>.csv`: série histórica com TE, TUSD, tarifa total e reajuste entre vigências.
- `output/resumo_comparativo.csv`: resumo por distribuidora com:
  - reajuste acumulado de 5 anos da tarifa;
  - CAGR da tarifa (5 anos);
  - IPCA acumulado no mesmo intervalo;
  - CAGR do IPCA;
  - diferença tarifa vs IPCA (pontos percentuais).
- `output/snapshots_mensais.csv`: histórico mensal das execuções para acompanhar novas homologações.

## Requisitos

- `python3` (testado em 3.10+)
- Acesso de rede às APIs:
  - `https://dadosabertos.aneel.gov.br`
  - `https://apisidra.ibge.gov.br`

## Configuração de distribuidoras

Edite `distribuidoras.txt` com uma sigla/nome da distribuidora por linha:

```txt
CEEE-D
CPFL PAULISTA
ENEL SP
```

## Execução

```bash
python3 tarifa_monitor.py
```

Ou passando a lista diretamente:

```bash
python3 tarifa_monitor.py --distribuidoras "CEEE-D,CPFL PAULISTA,ENEL SP"
```

## UI (gráfico Energia x IPCA)

1. Instalar dependência:

```bash
python3 -m pip install -r requirements.txt
```

2. Subir a interface:

```bash
streamlit run app.py
```

Na UI você consegue:
- selecionar a concessionária;
- escolher janela de 5 ou 10 anos;
- ver card com tarifa atual mais recente;
- comparar o reajuste acumulado de energia contra IPCA no gráfico;
- forçar atualização imediata clicando em `Atualizar agora`.

## Atualização mensal (cron)

Exemplo para rodar todo dia 2 às 07:00:

```cron
0 7 2 * * cd /Users/gcorrea/Desktop/dados-aneel && /usr/bin/python3 tarifa_monitor.py >> cron.log 2>&1
```

Assim, a base é atualizada mensalmente e os novos valores homologados entram automaticamente no `snapshots_mensais.csv`.
