MAIN=main.py
MEMORY=512
VERSION=recommended
DISPLAY_NAME=Agent Playground
DESCRIPTION=Observable task environments for AI agents
AUTORESTART=true
SUBDOMAIN=agent-playground-demo
START=python -m uvicorn main:app --host 0.0.0.0 --port 80
