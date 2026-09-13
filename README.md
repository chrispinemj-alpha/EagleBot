Eagle Bot v0.1
Eagle Bot is an independent AI-assisted work and intelligence platform.
Product boundary
Telegram is one communication channel. The core product is Eagle Bot itself: web app, API, work memory, Coworkers, task state, governance, approvals, world-signal integration and audit trail.
Current capabilities
· Independent web app at / and /app
· JSON chat API at /api/chat
· Coworker routing at /api/coworkers
· Persistent conversations, tasks, approvals, orders, world events and audit logs
· Original Telegram commands and order workflow retained
· Telegram /web entry into the Eagle web app
· Provider-agnostic AI gateway
· Provider-agnostic real-world signal gateway
· Approval gate for consequential execution
· Admin approval API
· Health endpoint
Environment
Copy .env.example to your deployment secret store. Never commit real tokens or keys.
The original Telegram token that appeared in earlier source must be rotated before use.

Development

Bash
python -m pip install -r requirements.txt
python eagle_bot.py

Development URL: http://localhost:5000

Production

Use a production WSGI server (for example Gunicorn) behind HTTPS and a reverse proxy/load balancer. Move persistence from local SQLite to a managed PostgreSQL-compatible service before multi-instance production.
Architecture direction
The intended progression is:
Identity → Memory → Coworkers → World Signals → Tools → Governed Actions → Outcomes → Evaluation → Controlled Upgrades

No provider, model, messaging platform or individual integration should become Eagle's permanent identity boundary.
docs/
EAGLE_BOT_GO_TO_MARKET.md
EAGLE_BOT_ARCHITECTURE.md
