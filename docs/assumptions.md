# Assumptions

- Python 3.12 is the minimum runtime; newer compatible runtimes are acceptable in development.
- Services begin as modules in one distribution and may be split into processes without changing trust boundaries.
- All integrations remain fixture/offline until credentials and external acceptance are performed by the owner.
- Production writes and bounded autonomy remain disabled regardless of environment variables in general services.
