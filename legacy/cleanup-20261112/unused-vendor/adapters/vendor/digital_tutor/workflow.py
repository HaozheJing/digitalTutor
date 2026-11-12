"""Explicit experimental workflow controls; no change to default deployments."""
def enabled(app):
    return bool(getattr(app,'workflow_enabled',False) and app.context_mode=='daily_native')
