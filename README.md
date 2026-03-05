# 🚀 DigitalOcean GPU Resource Monitor

A serverless, AI-driven infrastructure tool that audits DigitalOcean GPU Droplets. It bridges the DigitalOcean API, NVIDIA's DCGM Exporter, and a Large Language Model to provide real-time, conversational insights into your AI compute resources and hardware health.

## ✨ Features
* **Dual-Path Telemetry:** Scrapes ground-truth NVIDIA GPU metrics (Temperature, VRAM, Power, Utilization) via port 9400. Gracefully falls back to Host System proxy metrics (CPU, RAM, Load) if blocked.
* **Automated Resource Categorization:** Categorizes servers into 4 strict tiers: `Idle`, `Optimized`, `Over provisioned`, or `Under provisioned`.
* **Explainability:** Generates a hard-data reasoning string so the AI agent can prove *why* it recommends shutting down or resizing a server.
* **GenAI Ready:** Includes strict OpenAPI 3.0 schemas designed for DigitalOcean's GenAI Platform.

## 🛠️ Deployment Guide
1. Deploy `main.py` as a DigitalOcean Serverless Function. Replace the placeholder token with a read-only DO API token.
2. Set up a GenAI Agent using the files in the `agent_setup/` directory for instructions and schemas.
3. Ask your agent: *"Give me a health and utilization summary of my GPU fleet."*
