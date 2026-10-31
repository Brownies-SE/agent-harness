from ..services.database import ChatError


def require_service(request, name):
    service = getattr(request.app.state, name, None)
    if service is None:
        raise ChatError("service_unconfigured")
    return service
