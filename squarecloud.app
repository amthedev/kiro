MAIN=main.py
MEMORY=512
VERSION=recommended
DISPLAY_NAME=Kiro Gateway
DESCRIPTION=Proxy API para contas Kiro com painel admin
SUBDOMAIN=kiro-gateway
START=uvicorn main:app --host 0.0.0.0 --port 80
AUTORESTART=true
