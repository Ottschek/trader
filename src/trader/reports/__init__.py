"""Telegram reports: /analise (company or coin report) and /news (market briefing).

Built by scripts/report.py in a GitHub job (started by the Cloudflare Worker), rendered as one
picture each (render.py), sent to Telegram and archived for the web app's Reports page.
Every AI sentence is checked against the data it was given (checks.py).
"""
