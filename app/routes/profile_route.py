from fastapi import APIRouter
from app.schemas import ProfileRequest, ProfileResponse
from app.profile import create

router = APIRouter(tags=["perfil"])


@router.post("/perfil", response_model=ProfileResponse)
def conversar(requisicao: ProfileRequest) -> ProfileResponse:
    return create(requisicao)
