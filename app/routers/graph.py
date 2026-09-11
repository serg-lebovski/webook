from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.services import graph_service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/library-graph", response_class=HTMLResponse)
def library_graph_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse("library_graph.html", {"request": request, "user": user})


@router.get("/library-graph/data.json")
def library_graph_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return JSONResponse(graph_service.build_graph(db, user.id))
