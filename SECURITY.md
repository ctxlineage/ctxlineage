# Security Policy

## Supported versions

Pre-1.0: only the latest released version receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Email **me@masukai.dev** with details
(affected version, reproduction, impact). You will get an acknowledgement
within 48 hours and a fix or mitigation plan within 7 days for confirmed
issues.

## Notes for report users

Generated reports embed **full prompt and response text** by design. Treat
`ctxlineage-report.html` and `.ctxlineage/events.jsonl` like logs containing
sensitive data: redact before sharing (`ctxlineage report --redact` lands in
v0.1.0) and keep them out of version control (the default directory is
gitignored by `ctxlineage init` documentation guidance).
