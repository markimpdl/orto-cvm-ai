"""Small helper to build list/create/get/update/delete routers for simple tables."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from cvm.db import get_session

CHANNEL_STATUSES = ("active", "paused", "archived", "series")
CHANNEL_PRESENTERS = ("scenes", "overlay", "podcast", "editorial")


def _validate_data(model: type[SQLModel], data: dict, existing_language: str | None = None) -> None:
    if model.__name__ != "Channel":
        return
    if data.get("language") is not None and (lang := data["language"]) != existing_language:
        from cvm.channels import check_language

        try:
            check_language(lang)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    if "status" in data and data["status"] not in CHANNEL_STATUSES:
        raise HTTPException(422, "status: active | paused | archived | series")
    if "presenter" in data and data["presenter"] not in CHANNEL_PRESENTERS:
        raise HTTPException(422, "presenter: scenes | overlay | podcast | editorial")


def crud_router(model: type[SQLModel], prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[prefix.strip("/")])

    @router.get("")
    def list_items(session: Session = Depends(get_session)):
        return session.exec(select(model)).all()

    @router.post("")
    def create_item(data: dict, session: Session = Depends(get_session)):
        _validate_data(model, data)
        item = model.model_validate(data)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @router.get("/{item_id}")
    def get_item(item_id: int, session: Session = Depends(get_session)):
        item = session.get(model, item_id)
        if not item:
            raise HTTPException(404)
        return item

    @router.patch("/{item_id}")
    def update_item(item_id: int, data: dict, session: Session = Depends(get_session)):
        item = session.get(model, item_id)
        if not item:
            raise HTTPException(404)
        _validate_data(model, data, getattr(item, "language", None))
        for k, v in data.items():
            if k != "id" and k in model.model_fields:
                setattr(item, k, v)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @router.delete("/{item_id}")
    def delete_item(item_id: int, session: Session = Depends(get_session)):
        item = session.get(model, item_id)
        if not item:
            raise HTTPException(404)
        session.delete(item)
        session.commit()
        return {"deleted": item_id}

    return router
