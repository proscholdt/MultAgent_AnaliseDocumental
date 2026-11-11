"""Cadastro, login (JWT) e identificação do usuário corrente."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.models import User
from app.schemas import TokenOut, UserCreate, UserOut
from app.security import CurrentUser, create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Autenticação"])

# Hash usado quando o usuário não existe, para o login levar o mesmo tempo nos dois casos
# e não revelar quais nomes de usuário estão cadastrados.
_DUMMY_HASH = hash_password("senha-inexistente")


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED,
             summary="Cria um usuário")
async def register(body: UserCreate, session: SessionDep) -> User:
    user = User(username=body.username, password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Nome de usuário já cadastrado.") from None
    return user


@router.post("/login", response_model=TokenOut, summary="Troca usuário e senha por um token JWT")
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep) -> TokenOut:
    """No Swagger, use o botão **Authorize** e informe usuário e senha: ele chama esta rota
    e passa a enviar o token em todas as chamadas."""
    result = await session.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()
    valid = verify_password(form.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Usuário ou senha inválidos.",
                            headers={"WWW-Authenticate": "Bearer"})
    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in=expires_in, user_id=user.id)


@router.get("/me", response_model=UserOut, summary="Usuário do token")
async def me(user: CurrentUser) -> User:
    return user
