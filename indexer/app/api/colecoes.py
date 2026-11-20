"""Domínios (os pacotes em dominios/<id>/) e as coleções do usuário: cada coleção é de um domínio,
e os documentos dela são lidos e analisados com as regras dele."""
from fastapi import APIRouter, HTTPException, status

from app import pacotes
from app.api.deps import SessionDep
from app.infra import repo
from app.infra.modelos import Collection
from app.infra.seguranca import CurrentUser
from app.schemas import CollectionIn, CollectionOut, DomainOut

router = APIRouter(prefix="/api", tags=["Coleções"])


def domain_view(pacote: pacotes.Pacote) -> DomainOut:
    return DomainOut(
        id=pacote.id, nome=pacote.nome, descricao=pacote.descricao.strip(), padrao=pacote.id == pacotes.PADRAO,
        entidade=pacote.entidade.model_dump(), recorte=pacote.recorte.model_dump(),
        metadados=[{"nome": m.nome, "rotulo": m.rotulo, "tipo": m.tipo, "papel": m.papel} for m in pacote.metadados],
        textos=pacote.textos.model_dump())


@router.get("/dominios", response_model=list[DomainOut], summary="Os domínios disponíveis")
async def list_domains(user: CurrentUser) -> list[DomainOut]:
    """Um por pacote em `dominios/`: o que a interface mostra para escolher e rotular."""
    return [domain_view(pacote) for pacote in pacotes.todos().values()]


@router.get("/colecoes", response_model=list[CollectionOut], summary="As coleções do usuário")
async def list_collections(user: CurrentUser, session: SessionDep) -> list[CollectionOut]:
    return [CollectionOut(id=c.id, nome=c.nome, dominio=c.dominio, created_at=c.created_at, documentos=total)
            for c, total in await repo.list_collections(session, user.id)]


@router.post("/colecoes", response_model=CollectionOut, status_code=status.HTTP_201_CREATED,
             summary="Cria uma coleção de um domínio")
async def create_collection(body: CollectionIn, user: CurrentUser, session: SessionDep) -> CollectionOut:
    if body.dominio not in pacotes.todos():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Domínio desconhecido: {body.dominio!r}. Existem: {', '.join(pacotes.todos())}.")
    collection = Collection(user_id=user.id, nome=body.nome.strip(), dominio=body.dominio)
    session.add(collection)
    await session.commit()
    return CollectionOut(id=collection.id, nome=collection.nome, dominio=collection.dominio,
                         created_at=collection.created_at, documentos=0)
