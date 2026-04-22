from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.database import get_session
from app.services.widget_service import WidgetService
from app.widgets.catalog import catalog_for_api, CATALOG_VERSION

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


class WidgetActionRequest(BaseModel):
    action_id: str
    payload: dict = {}


@router.get("/catalog")
def get_catalog(response: Response):
    """Return the widget catalog as machine-readable JSON.

    Response shape: {"version": "<hash>", "widgets": {<widget_type>: {...}}}.
    Catalog is the single source of truth — emits no-cache headers plus an
    ETag so the browser can revalidate cheaply and never show a stale catalog
    after a backend change.
    """
    response.headers["Cache-Control"] = "no-cache"
    response.headers["ETag"] = f'"{CATALOG_VERSION}"'
    return catalog_for_api()


@router.get("/{instance_id}")
def get_widget(instance_id: str, session: Session = Depends(get_session)):
    """Get a widget instance by ID."""
    ws = WidgetService(session)
    instance = ws.get_instance(instance_id)
    if not instance:
        raise HTTPException(404, "Widget not found")
    return WidgetService.instance_to_dict(instance)


@router.post("/{instance_id}/action")
def widget_action(
    instance_id: str,
    req: WidgetActionRequest,
    session: Session = Depends(get_session),
):
    """Execute a simple action on a widget (dismiss, load_more).

    For graph-resuming actions (confirm, cancel), use the chat message endpoint
    with type="resume" instead.
    """
    ws = WidgetService(session)
    instance = ws.get_instance(instance_id)
    if not instance:
        raise HTTPException(404, "Widget not found")

    # Check concurrent action — don't execute on already-completed widgets
    if instance.status in ("completed", "dismissed", "expired"):
        return WidgetService.instance_to_dict(instance)

    # Route to action handler
    from app.widgets.actions import handle_action
    updated = handle_action(ws, instance, req.action_id, req.payload)
    if not updated:
        raise HTTPException(400, f"Unknown action: {req.action_id}")

    return WidgetService.instance_to_dict(updated)
