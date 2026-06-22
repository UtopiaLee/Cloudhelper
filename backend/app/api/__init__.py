from fastapi import APIRouter

from app.api import accounts, audit, auth, budget, fleet, firewall, instances, schedules, shell, ssh_keys, system, tls

api_router = APIRouter()
api_router.include_router(system.router, tags=["system"])
api_router.include_router(tls.router, tags=["system"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(shell.router, tags=["shell"])
api_router.include_router(fleet.router, prefix="/fleet", tags=["fleet"])
api_router.include_router(ssh_keys.router, prefix="/ssh-keys", tags=["ssh-keys"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(instances.router, prefix="/accounts/{account_id}/instances", tags=["instances"])
api_router.include_router(firewall.router, prefix="/accounts/{account_id}/firewall", tags=["firewall"])
api_router.include_router(schedules.router, prefix="/accounts/{account_id}/schedules", tags=["schedules"])
api_router.include_router(budget.router, prefix="/accounts/{account_id}/budget", tags=["budget"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
