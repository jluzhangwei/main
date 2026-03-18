FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLDP_HOST=0.0.0.0 \
    LLDP_PORT=18080

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY lldp.html ./lldp.html
COPY lldp-manual.html ./lldp-manual.html
COPY lldp_sql_service.py ./lldp_sql_service.py
COPY extract_lldp_neighbor_addresses.py ./extract_lldp_neighbor_addresses.py
COPY start_lldp_service.sh ./start_lldp_service.sh
COPY shared ./shared
COPY .env.mysql.example ./.env.mysql.example

RUN mkdir -p /app/tmp_csv /app/state_snapshots && chmod +x /app/start_lldp_service.sh

EXPOSE 18080

CMD ["python", "-m", "uvicorn", "lldp_sql_service:app", "--host", "0.0.0.0", "--port", "18080"]
