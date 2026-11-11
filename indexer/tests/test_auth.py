from datetime import UTC, datetime, timedelta

import jwt

from tests.conftest import register_and_login


async def test_register_login_and_me(ctx):
    user_id, headers = await register_and_login(ctx.client, "ana")
    me = await ctx.client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json() == {"user_id": user_id, "username": "ana", "created_at": me.json()["created_at"]}


async def test_register_rejects_duplicate_username(ctx):
    await register_and_login(ctx.client, "ana")
    again = await ctx.client.post("/auth/register", json={"username": "ana", "password": "outra-senha-1"})
    assert again.status_code == 409


async def test_register_validates_password_length(ctx):
    response = await ctx.client.post("/auth/register", json={"username": "ana", "password": "curta"})
    assert response.status_code == 422


async def test_login_rejects_wrong_password_and_unknown_user(ctx):
    await register_and_login(ctx.client, "ana")
    wrong = await ctx.client.post("/auth/login", data={"username": "ana", "password": "senha-errada-1"})
    unknown = await ctx.client.post("/auth/login", data={"username": "ninguem", "password": "senha-forte-123"})
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json()  # mesma resposta: não revela quais usuários existem


async def test_protected_routes_require_token(ctx):
    assert (await ctx.client.get("/api/documents")).status_code == 401
    bad = await ctx.client.get("/api/documents", headers={"Authorization": "Bearer nao-e-um-token"})
    assert bad.status_code == 401


async def test_expired_and_forged_tokens_are_rejected(ctx):
    user_id, _ = await register_and_login(ctx.client, "ana")
    past = datetime.now(UTC) - timedelta(minutes=5)
    expired = jwt.encode({"sub": user_id, "exp": past}, ctx.settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": user_id, "exp": datetime.now(UTC) + timedelta(minutes=5)},
                        "outro-segredo-qualquer-com-32-bytes!!", algorithm="HS256")
    for token in (expired, forged):
        response = await ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
