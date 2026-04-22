from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.widget_instance import WidgetInstance


class WidgetService:
    def __init__(self, db_session: Session):
        self.session = db_session

    def create_instance(
        self,
        session_id: str,
        widget_data: dict,
        created_by: str = "",
    ) -> WidgetInstance:
        """Create a new widget instance in DB."""
        instance = WidgetInstance(
            session_id=session_id,
            widget_type=widget_data.get("widget", ""),
            status="pending",
            title=widget_data.get("title", ""),
            data=widget_data.get("data", {}),
            extra_data=widget_data.get("metadata", {}),
            created_by=created_by,
        )
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def get_instance(self, instance_id: str) -> WidgetInstance | None:
        return self.session.get(WidgetInstance, instance_id)

    def update_status(self, instance_id: str, status: str, **kwargs) -> WidgetInstance | None:
        """Update widget status and optional fields."""
        instance = self.session.get(WidgetInstance, instance_id)
        if not instance:
            return None
        instance.status = status
        instance.updated_at = datetime.now(timezone.utc)
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def update_data(self, instance_id: str, data: dict) -> WidgetInstance | None:
        """Update widget data (for mutations like pagination)."""
        instance = self.session.get(WidgetInstance, instance_id)
        if not instance:
            return None
        instance.data = data
        instance.updated_at = datetime.now(timezone.utc)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def batch_fetch(self, instance_ids: list[str]) -> dict[str, WidgetInstance]:
        """Fetch multiple instances in one query. Returns {id: instance}."""
        if not instance_ids:
            return {}
        instances = self.session.exec(
            select(WidgetInstance).where(WidgetInstance.id.in_(instance_ids))
        ).all()
        return {i.id: i for i in instances}

    def delete_for_session(self, session_id: str):
        """Delete all widget instances for a session."""
        instances = self.session.exec(
            select(WidgetInstance).where(WidgetInstance.session_id == session_id)
        ).all()
        for i in instances:
            self.session.delete(i)
        self.session.commit()

    @staticmethod
    def instance_to_dict(instance: WidgetInstance) -> dict:
        """Convert instance to frontend-friendly dict."""
        return {
            "instance_id": instance.id,
            "widget": instance.widget_type,
            "status": instance.status,
            "title": instance.title,
            "data": instance.data,
            "metadata": instance.extra_data,
            "created_by": instance.created_by,
            "created_at": instance.created_at.isoformat(),
            "updated_at": instance.updated_at.isoformat(),
        }
