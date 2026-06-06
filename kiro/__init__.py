# -*- coding: utf-8 -*-
"""
Kiro Gateway — Anthropic/OpenAI-compatible proxy backed by kiro-cli.

Modules:
    - database:       SQLite layer (accounts, clients, usage, settings)
    - kirocli_runner: drives the kiro-cli subprocess
    - account_pool:   round-robin over ksk_ accounts with failover
    - routes_proxy:   /v1/messages, /v1/chat/completions, /v1/models
    - routes_admin:   /admin panel + API
    - admin_ui:       admin panel HTML
"""

__version__ = "3.0.0"
